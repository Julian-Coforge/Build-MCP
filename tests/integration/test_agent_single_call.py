"""The agent's single tool call, end to end: fake Claude, real in-process MCP server.

Covers brief step 5a: connect to the MCP server as a client and make one tool call for a
hardcoded request. FakeMessages stands in for the paid API, so this runs free in CI.
"""

import io
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, ListToolsResult

from it_requests import config
from it_requests.agent.llm import LLMClient
from it_requests.agent.prompts import SYSTEM_PROMPT_SINGLE_CALL
from it_requests.agent.single_call import run_single_tool_call
from it_requests.observability import JsonFormatter
from it_requests.tracing import make_tracer

pytestmark = pytest.mark.anyio

REQUEST_ID = "REQ-0501"
REQUEST_TEXT = "Hi, I'm E1002. I'd like to request a monitor for my desk, please."


class SpyClient:
    """Wraps the real MCP client and records the meta sent with each tool call."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.tool_calls: list[dict[str, Any]] = []

    async def list_tools(self) -> ListToolsResult:
        return await self._client.list_tools()

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any]
    ) -> CallToolResult:
        self.tool_calls.append({"name": name, "arguments": arguments, "meta": meta})
        return await self._client.call_tool(name, arguments, meta=meta)  # type: ignore[arg-type]


class Run:
    """Everything one test needs: the paths written, the logs, and a helper to run once."""

    def __init__(self, tmp_path: Path, fake_messages: Any, log_stream: io.StringIO) -> None:
        self.ledger_path = tmp_path / "logs" / "cost_ledger.jsonl"
        self.trace_path = tmp_path / "traces" / f"{REQUEST_ID}.jsonl"
        self.fake_messages = fake_messages
        self.log_stream = log_stream

    async def once(
        self, server: MCPServer, logger: logging.Logger
    ) -> tuple[dict[str, Any], SpyClient]:
        tracer, provider = make_tracer(self.trace_path)
        ticks = iter([1.0, 1.5])
        llm = LLMClient(
            create=self.fake_messages.create,
            model=config.DEV_MODEL,
            env="dev",
            ledger_path=self.ledger_path,
            tracer=tracer,
            logger=logger,
            clock=lambda: next(ticks),
        )
        try:
            async with Client(server) as client:
                spy = SpyClient(client)
                result = await run_single_tool_call(
                    REQUEST_TEXT,
                    request_id=REQUEST_ID,
                    llm=llm,
                    mcp_client=spy,
                    tracer=tracer,
                    logger=logger,
                )
        finally:
            provider.shutdown()  # flush the trace file, as the live script does
        return result, spy

    def spans(self) -> list[dict[str, Any]]:
        lines = self.trace_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def ledger(self) -> list[dict[str, Any]]:
        lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def logs(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log_stream.getvalue().splitlines()]


@pytest.fixture
def agent_logger(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.agent.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)  # DEBUG on, to prove the request text stays at DEBUG
    logger.propagate = False
    yield logger, stream
    logger.handlers = []


@pytest.fixture
def run(
    tmp_path: Path, fake_messages: Any, agent_logger: tuple[logging.Logger, io.StringIO]
) -> Run:
    return Run(tmp_path, fake_messages, agent_logger[1])


def _by_name(spans: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(s for s in spans if s["name"] == name)


# --- Eligibility check: the server's real verdict comes back --------------------------------


async def test_one_tool_call_returns_server_verdict(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    run.fake_messages.queue(
        make_message(
            tool_name="check_request_eligibility",
            tool_input={"employee_id": "E1002", "item": "monitor"},
        )
    )

    result, spy = await run.once(server, agent_logger[0])

    assert result["request_id"] == REQUEST_ID
    assert result["tool"] == "check_request_eligibility"
    assert result["arguments"] == {"employee_id": "E1002", "item": "monitor"}
    assert result["result_status"] == "ok"
    # Decided by the real server's tested Python, not by the model (CLAUDE.md principle).
    assert result["result"]["status"] == "ELIGIBLE"
    assert result["result"]["rule_id"] == "R-NEW"
    assert len(spy.tool_calls) == 1


async def test_model_gets_server_tools_system_prompt_and_request(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    run.fake_messages.queue(make_message(tool_name="get_employee_info"))

    await run.once(server, agent_logger[0])

    assert len(run.fake_messages.calls) == 1  # one model call only; the loop is M6
    sent = run.fake_messages.calls[0]
    assert sorted(t["name"] for t in sent["tools"]) == [
        "check_request_eligibility",
        "flag_for_human_review",
        "get_employee_info",
        "get_policy_limits",
    ]
    assert sent["system"] == SYSTEM_PROMPT_SINGLE_CALL
    assert sent["messages"] == [{"role": "user", "content": REQUEST_TEXT}]


async def test_ledger_has_one_line_matching_the_run(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    run.fake_messages.queue(
        make_message(
            tool_name="check_request_eligibility",
            tool_input={"employee_id": "E1002", "item": "monitor"},
        )
    )

    result, _ = await run.once(server, agent_logger[0])

    (line,) = run.ledger()
    assert line["request_id"] == REQUEST_ID
    assert line["run_id"] == result["run_id"]
    assert line["stage"] == "single_call"
    assert result["cost_usd"] == line["cost_usd"]


async def test_trace_links_run_llm_call_and_tool(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    run.fake_messages.queue(
        make_message(
            tool_name="check_request_eligibility",
            tool_input={"employee_id": "E1002", "item": "monitor"},
        )
    )

    result, spy = await run.once(server, agent_logger[0])

    spans = run.spans()
    root = _by_name(spans, "agent.run")
    llm_span = _by_name(spans, "llm.call")
    tool_span = _by_name(spans, "tool.check_request_eligibility")
    assert root["parent_span_id"] is None
    assert llm_span["parent_span_id"] == root["span_id"]
    assert tool_span["parent_span_id"] == root["span_id"]
    assert {s["trace_id"] for s in spans} == {root["trace_id"]}
    assert root["attributes"]["request_id"] == REQUEST_ID
    assert root["attributes"]["run_id"] == result["run_id"]
    assert root["attributes"]["model"] == config.DEV_MODEL
    assert tool_span["attributes"]["tool"] == "check_request_eligibility"
    assert tool_span["attributes"]["result_status"] == "ok"
    # The MCP call carries the tool span's W3C traceparent, so a server span can later be
    # joined under it (PLAN §5).
    meta = spy.tool_calls[0]["meta"]
    assert meta["request_id"] == REQUEST_ID
    assert meta["traceparent"] == f"00-{tool_span['trace_id']}-{tool_span['span_id']}-01"


async def test_run_start_and_end_logged_with_cost(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    run.fake_messages.queue(
        make_message(
            tool_name="check_request_eligibility",
            tool_input={"employee_id": "E1002", "item": "monitor"},
            input_tokens=2000,
            output_tokens=150,
        )
    )

    await run.once(server, agent_logger[0])

    logs = run.logs()
    events = [r["event"] for r in logs]
    assert events[0] == "run.start"
    assert events[-1] == "run.end"
    end = logs[-1]
    assert end["cost_usd"] == run.ledger()[0]["cost_usd"]
    assert (end["input_tokens"], end["output_tokens"]) == (2000, 150)
    # The request text is logged at DEBUG only, never at INFO or above (PLAN §5).
    with_text = [r for r in logs if REQUEST_TEXT in json.dumps(r)]
    assert with_text
    assert all(r["level"] == "DEBUG" for r in with_text)


# --- Lookup: the employee's name never leaves the tool result ----------------------------------


async def test_employee_name_is_never_traced_logged_or_ledgered(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> None:
    employees = json.loads(config.EMPLOYEES_FILE.read_text(encoding="utf-8"))["employees"]
    name = next(e["name"] for e in employees if e["employee_id"] == "E1002")
    run.fake_messages.queue(
        make_message(tool_name="get_employee_info", tool_input={"employee_id": "E1002"})
    )

    result, _ = await run.once(server, agent_logger[0])

    assert result["result"]["name"] == name  # the agent does receive it...
    for text in (
        run.trace_path.read_text(encoding="utf-8"),
        run.ledger_path.read_text(encoding="utf-8"),
        run.log_stream.getvalue(),
    ):
        assert name not in text  # ...but it is never written anywhere
        assert name.split()[0] not in text
    tool_span = _by_name(run.spans(), "tool.get_employee_info")
    assert tool_span["attributes"]["data_version"] == "employees-v1"


# --- No tool call: nothing is sent to the server ------------------------------------------------


async def test_reply_without_tool_use_makes_no_tool_call(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
    server_log: io.StringIO,
) -> None:
    run.fake_messages.queue(
        make_message(tool_name=None, text="I can't help with that.", stop_reason="end_turn")
    )

    result, spy = await run.once(server, agent_logger[0])

    assert result["error"] == "NO_TOOL_CALL"
    assert result["request_id"] == REQUEST_ID
    assert spy.tool_calls == []
    assert "tool.call" not in server_log.getvalue()
    assert not any(s["name"].startswith("tool.") for s in run.spans())

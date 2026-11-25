"""The agent's single tool call, end to end: fake Claude, real in-process MCP server.

Covers brief step 5a: connect to the MCP server as a client and make one tool call for a
hardcoded request. FakeMessages stands in for the paid API, so this runs free in CI.
"""

import io
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
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


ELIGIBILITY_INPUT = {"employee_id": "E1002", "item": "monitor"}
SERVER_TOOL_NAMES = [
    "check_request_eligibility",
    "flag_for_human_review",
    "get_employee_info",
    "get_policy_limits",
]


@pytest.fixture
def eligibility_run(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> Callable[[], Awaitable[tuple[dict[str, Any], SpyClient]]]:
    """The model asks for one eligibility check (E1002 monitor); the real server answers."""

    async def _go() -> tuple[dict[str, Any], SpyClient]:
        run.fake_messages.queue(
            make_message(
                tool_name="check_request_eligibility",
                tool_input=ELIGIBILITY_INPUT,
                input_tokens=2000,
                output_tokens=150,
            )
        )
        return await run.once(server, agent_logger[0])

    return _go


@pytest.fixture
def lookup_run(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> Callable[[], Awaitable[tuple[dict[str, Any], SpyClient]]]:
    """The model asks for E1002's employee record, which holds their name."""

    async def _go() -> tuple[dict[str, Any], SpyClient]:
        run.fake_messages.queue(
            make_message(tool_name="get_employee_info", tool_input={"employee_id": "E1002"})
        )
        return await run.once(server, agent_logger[0])

    return _go


@pytest.fixture
def no_tool_run(
    server: MCPServer,
    run: Run,
    agent_logger: tuple[logging.Logger, io.StringIO],
    make_message: Callable[..., Message],
) -> Callable[[], Awaitable[tuple[dict[str, Any], SpyClient]]]:
    """The model replies in text and asks for no tool."""

    async def _go() -> tuple[dict[str, Any], SpyClient]:
        run.fake_messages.queue(
            make_message(tool_name=None, text="I can't help with that.", stop_reason="end_turn")
        )
        return await run.once(server, agent_logger[0])

    return _go


RunOnce = Callable[[], Awaitable[tuple[dict[str, Any], SpyClient]]]
E1002_NAME = next(
    e["name"]
    for e in json.loads(config.EMPLOYEES_FILE.read_text(encoding="utf-8"))["employees"]
    if e["employee_id"] == "E1002"
)


# ---------- eligibility check: the server's real verdict comes back ----------


async def test_single_call_returns_the_call_it_made(eligibility_run: RunOnce) -> None:
    result, _ = await eligibility_run()

    assert {k: result[k] for k in ("request_id", "tool", "arguments", "result_status")} == {
        "request_id": REQUEST_ID,
        "tool": "check_request_eligibility",
        "arguments": ELIGIBILITY_INPUT,
        "result_status": "ok",
    }


async def test_single_call_returns_the_server_verdict(eligibility_run: RunOnce) -> None:
    # Decided by the real server's tested Python, not by the model (CLAUDE.md principle).
    result, _ = await eligibility_run()

    assert (result["result"]["status"], result["result"]["rule_id"]) == ("ELIGIBLE", "R-NEW")


async def test_single_call_makes_exactly_one_tool_call(eligibility_run: RunOnce) -> None:
    _, spy = await eligibility_run()

    assert len(spy.tool_calls) == 1


async def test_single_call_makes_exactly_one_model_call(eligibility_run: RunOnce, run: Run) -> None:
    # One model call only; the loop is M6.
    await eligibility_run()

    assert len(run.fake_messages.calls) == 1


async def test_single_call_sends_the_server_tools(eligibility_run: RunOnce, run: Run) -> None:
    await eligibility_run()

    assert sorted(t["name"] for t in run.fake_messages.calls[0]["tools"]) == SERVER_TOOL_NAMES


async def test_single_call_sends_system_prompt_and_request(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()
    sent = run.fake_messages.calls[0]

    assert (sent["system"], sent["messages"]) == (
        SYSTEM_PROMPT_SINGLE_CALL,
        [{"role": "user", "content": REQUEST_TEXT}],
    )


async def test_single_call_ledger_has_one_line_for_the_run(
    eligibility_run: RunOnce, run: Run
) -> None:
    result, _ = await eligibility_run()
    (line,) = run.ledger()

    assert (line["request_id"], line["run_id"], line["stage"]) == (
        REQUEST_ID,
        result["run_id"],
        "single_call",
    )


async def test_single_call_reported_cost_equals_ledger_cost(
    eligibility_run: RunOnce, run: Run
) -> None:
    result, _ = await eligibility_run()

    assert result["cost_usd"] == run.ledger()[0]["cost_usd"]


# ---------- trace ----------


async def test_single_call_root_span_has_no_parent(eligibility_run: RunOnce, run: Run) -> None:
    await eligibility_run()

    assert _by_name(run.spans(), "agent.run")["parent_span_id"] is None


@pytest.mark.parametrize(
    "child", ["llm.call", "tool.check_request_eligibility"], ids=["llm-call", "tool-call"]
)
async def test_single_call_child_span_hangs_off_the_root(
    eligibility_run: RunOnce, run: Run, child: str
) -> None:
    await eligibility_run()
    spans = run.spans()

    assert _by_name(spans, child)["parent_span_id"] == _by_name(spans, "agent.run")["span_id"]


async def test_single_call_every_span_shares_one_trace_id(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()

    assert len({s["trace_id"] for s in run.spans()}) == 1


async def test_single_call_root_span_names_request_run_and_model(
    eligibility_run: RunOnce, run: Run
) -> None:
    result, _ = await eligibility_run()
    attributes = _by_name(run.spans(), "agent.run")["attributes"]

    assert (attributes["request_id"], attributes["run_id"], attributes["model"]) == (
        REQUEST_ID,
        result["run_id"],
        config.DEV_MODEL,
    )


async def test_single_call_tool_span_names_tool_and_status(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()
    attributes = _by_name(run.spans(), "tool.check_request_eligibility")["attributes"]

    assert (attributes["tool"], attributes["result_status"]) == ("check_request_eligibility", "ok")


async def test_single_call_meta_carries_request_id(eligibility_run: RunOnce) -> None:
    _, spy = await eligibility_run()

    assert spy.tool_calls[0]["meta"]["request_id"] == REQUEST_ID


async def test_single_call_meta_carries_the_tool_span_traceparent(
    eligibility_run: RunOnce, run: Run
) -> None:
    # The MCP call carries the tool span's W3C traceparent, so a server span can later be
    # joined under it (PLAN §5).
    _, spy = await eligibility_run()
    tool_span = _by_name(run.spans(), "tool.check_request_eligibility")

    assert spy.tool_calls[0]["meta"]["traceparent"] == (
        f"00-{tool_span['trace_id']}-{tool_span['span_id']}-01"
    )


# ---------- run logs ----------


async def test_single_call_logs_run_start_first_and_run_end_last(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()
    events = [r["event"] for r in run.logs()]

    assert (events[0], events[-1]) == ("run.start", "run.end")


async def test_single_call_run_end_has_ledger_cost_and_tokens(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()
    end = run.logs()[-1]

    assert (end["cost_usd"], end["input_tokens"], end["output_tokens"]) == (
        run.ledger()[0]["cost_usd"],
        2000,
        150,
    )


async def test_single_call_request_text_is_logged_at_debug(
    eligibility_run: RunOnce, run: Run
) -> None:
    await eligibility_run()

    with_text = [r for r in run.logs() if REQUEST_TEXT in json.dumps(r)]

    assert len(with_text) >= 1


async def test_single_call_request_text_is_never_logged_above_debug(
    eligibility_run: RunOnce, run: Run
) -> None:
    # The request text is logged at DEBUG only, never at INFO or above (PLAN §5).
    await eligibility_run()

    with_text = [r for r in run.logs() if REQUEST_TEXT in json.dumps(r)]

    assert {r["level"] for r in with_text} == {"DEBUG"}


# ---------- lookup: the employee's name never leaves the tool result ----------


async def test_single_call_lookup_gives_the_agent_the_name(lookup_run: RunOnce) -> None:
    result, _ = await lookup_run()

    assert result["result"]["name"] == E1002_NAME


OUTPUTS = ["trace", "ledger", "log"]


def _written(run: Run, output: str) -> str:
    paths = {"trace": run.trace_path, "ledger": run.ledger_path}
    return (
        paths[output].read_text(encoding="utf-8") if output in paths else run.log_stream.getvalue()
    )


@pytest.mark.parametrize("output", OUTPUTS, ids=str)
@pytest.mark.parametrize(
    "secret", [E1002_NAME, E1002_NAME.split()[0]], ids=["full-name", "first-name"]
)
async def test_single_call_name_is_never_written(
    lookup_run: RunOnce, run: Run, output: str, secret: str
) -> None:
    await lookup_run()

    assert secret not in _written(run, output)


async def test_single_call_lookup_span_names_the_data_version(
    lookup_run: RunOnce, run: Run
) -> None:
    await lookup_run()

    tool_span = _by_name(run.spans(), "tool.get_employee_info")

    assert tool_span["attributes"]["data_version"] == "employees-v1"


# ---------- no tool call: nothing is sent to the server ----------


async def test_single_call_text_reply_returns_no_tool_call_error(no_tool_run: RunOnce) -> None:
    result, _ = await no_tool_run()

    assert (result["error"], result["request_id"]) == ("NO_TOOL_CALL", REQUEST_ID)


async def test_single_call_text_reply_sends_nothing_to_the_server(
    no_tool_run: RunOnce, server_log: io.StringIO
) -> None:
    _, spy = await no_tool_run()

    assert (spy.tool_calls, "tool.call" in server_log.getvalue()) == ([], False)


async def test_single_call_text_reply_writes_no_tool_span(no_tool_run: RunOnce, run: Run) -> None:
    await no_tool_run()

    assert [s["name"] for s in run.spans() if s["name"].startswith("tool.")] == []

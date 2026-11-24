"""The ReAct loop end to end: scripted fake Claude, real in-process MCP server (M6).

The model's turns are scripted with FakeMessages; every tool result comes from the real
server and the real policy code. Nothing calls the paid API, so this runs free in CI.
"""

import io
import itertools
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
from it_requests.agent.loop import AgentRequest, AgentResult, run_agent
from it_requests.agent.prompts import SYSTEM_PROMPT_REACT
from it_requests.observability import JsonFormatter
from it_requests.tracing import make_tracer

pytestmark = pytest.mark.anyio

REQUEST_ID = "REQ-0001"
T1_TEXT = (
    "Hi, I'm Marcus (E1002). I'd like a second monitor for my desk, I'm juggling a lot of windows."
)
T2_TEXT = "This is Priya, E1001. My laptop feels slow lately, can I get a new one?"
T3_TEXT = (
    "Dana Okafor, E1003. My work phone was stolen from my car last night and I need a "
    "replacement ASAP."
)

Use = tuple[str, dict[str, object]]


def use(name: str, thought: str = "Next step.", **arguments: object) -> Use:
    """One scripted tool_use: the model always sends `thought` with the arguments."""
    return name, {"thought": thought, **arguments}


def final(
    decision: str,
    *,
    rule_id: str | None = None,
    review_id: str | None = None,
    reason_code: str | None = None,
    reply: str = "",
    thought: str = "The decision is made.",
) -> Use:
    return "final_answer", {
        "thought": thought,
        "decision": decision,
        "rule_id": rule_id,
        "review_id": review_id,
        "reason_code": reason_code,
        "draft_reply": reply,
    }


APPROVE_T1 = final(
    "approve",
    rule_id="R-NEW",
    reply="Approved: your role allows up to 2 monitors and you have 1 (R-NEW). IT will "
    "arrange fulfilment.",
)


class SpyClient:
    """Wraps the real MCP client and records every tool call the agent sends."""

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


class Harness:
    """Scripts the model's turns, runs the loop once, and reads back what it wrote."""

    def __init__(
        self,
        tmp_path: Path,
        fake_messages: Any,
        make_message: Callable[..., Message],
        server: MCPServer,
        logger: logging.Logger,
        log_stream: io.StringIO,
        review_queue_path: Path,
    ) -> None:
        self.ledger_path = tmp_path / "logs" / "cost_ledger.jsonl"
        self.trace_path = tmp_path / "traces" / f"{REQUEST_ID}.jsonl"
        self.fake = fake_messages
        self.make_message = make_message
        self.server = server
        self.logger = logger
        self.log_stream = log_stream
        self.queue_path = review_queue_path
        self.spy: SpyClient | None = None
        self.sent: list[Message] = []

    def script(self, *turns: list[Use] | Message) -> None:
        """Queue one model response per turn: a list of tool uses, or a ready Message."""
        for turn in turns:
            message = turn if isinstance(turn, Message) else self.make_message(tool_uses=turn)
            self.sent.append(message)
            self.fake.queue(message)

    async def run(self, text: str = T1_TEXT, *, max_model_calls: int = 12) -> AgentResult:
        tracer, provider = make_tracer(self.trace_path)
        ticks = itertools.count(0.0, 0.25)
        llm = LLMClient(
            create=self.fake.create,
            model=config.DEV_MODEL,
            env="dev",
            ledger_path=self.ledger_path,
            tracer=tracer,
            logger=self.logger,
            clock=lambda: next(ticks),
        )
        try:
            async with Client(self.server) as client:
                self.spy = SpyClient(client)
                return await run_agent(
                    AgentRequest(request_id=REQUEST_ID, request_text=text),
                    llm=llm,
                    mcp_client=self.spy,
                    tracer=tracer,
                    logger=self.logger,
                    max_model_calls=max_model_calls,
                )
        finally:
            provider.shutdown()  # flush the trace file

    # --- What the run wrote ---

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self.fake.calls)

    def tool_calls(self, name: str | None = None) -> list[dict[str, Any]]:
        assert self.spy is not None
        return [c for c in self.spy.tool_calls if name is None or c["name"] == name]

    def tickets(self) -> list[dict[str, Any]]:
        if not self.queue_path.exists():
            return []
        tickets: list[dict[str, Any]] = json.loads(self.queue_path.read_text())["tickets"]
        return tickets

    def ledger(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.ledger_path.read_text().splitlines()]

    def spans(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.trace_path.read_text().splitlines()]

    def logs(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log_stream.getvalue().splitlines()]

    def last_user_message(self, call_index: int) -> dict[str, Any]:
        message: dict[str, Any] = self.calls[call_index]["messages"][-1]
        assert message["role"] == "user"
        return message


@pytest.fixture
def agent_logger(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.loop.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)  # DEBUG on, to prove private text stays at DEBUG
    logger.propagate = False
    yield logger, stream
    logger.handlers = []


@pytest.fixture
def h(
    tmp_path: Path,
    fake_messages: Any,
    make_message: Callable[..., Message],
    server: MCPServer,
    agent_logger: tuple[logging.Logger, io.StringIO],
    review_queue_path: Path,
) -> Harness:
    logger, stream = agent_logger
    return Harness(tmp_path, fake_messages, make_message, server, logger, stream, review_queue_path)


def _content(block: Any) -> Any:
    return block["content"]


# --- The four primary paths (REQUIREMENTS §9.1) --------------------------------------------


async def test_t1_approve(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    assert result.final.decision == "approve"
    assert result.final.rule_id == "R-NEW"
    assert result.fallback is False
    assert result.guard_events == ()
    assert [s.tool for s in result.steps] == ["get_employee_info", "check_request_eligibility"]
    # The verdict came from the real server, not the script.
    assert result.steps[1].observation["status"] == "ELIGIBLE"
    assert len(h.calls) == 3
    ledger = h.ledger()
    assert [line["stage"] for line in ledger] == ["react_step_1", "react_step_2", "react_step_3"]
    assert {line["run_id"] for line in ledger} == {result.run_id}
    assert result.cost_usd == pytest.approx(sum(line["cost_usd"] for line in ledger))
    assert result.input_tokens == sum(line["input_tokens"] for line in ledger)


async def test_server_never_sees_thought_and_gets_request_id(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    for call in h.tool_calls():
        assert "thought" not in call["arguments"]
        assert call["meta"]["request_id"] == REQUEST_ID
    assert result.steps[0].arguments == {"employee_id": "E1002"}
    assert result.steps[0].thought == "Next step."


async def test_first_request_uses_react_prompt_tools_and_request_id(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    await h.run()

    first = h.calls[0]
    assert first["system"][0]["text"] == SYSTEM_PROMPT_REACT
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    names = [t["name"] for t in first["tools"]]
    assert sorted(names) == [
        "check_request_eligibility",
        "final_answer",
        "flag_for_human_review",
        "get_employee_info",
        "get_policy_limits",
    ]
    assert names[-1] == "final_answer"
    for tool in first["tools"]:
        assert tool["input_schema"]["required"][0] == "thought"
    assert first["messages"] == [
        {"role": "user", "content": f"Request ID: {REQUEST_ID}\n\n{T1_TEXT}"}
    ]


async def test_t2_deny(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1001")],
        [use("check_request_eligibility", employee_id="E1001", item="laptop")],
        [use("get_policy_limits", role="standard")],
        [
            final(
                "deny",
                rule_id="R-REFRESH",
                reply="Your laptop is not due for refresh until 2028-03-10 (R-REFRESH). "
                "The IT helpdesk can help with performance.",
            )
        ],
    )

    result = await h.run(T2_TEXT)

    assert result.final.decision == "deny"
    assert result.final.rule_id == "R-REFRESH"
    assert result.final.draft_reply is not None and "R-REFRESH" in result.final.draft_reply
    assert result.guard_events == ()
    assert h.tickets() == []


async def test_t3_escalate_e6_writes_one_ticket(h: Harness) -> None:
    h.script(
        [
            use(
                "flag_for_human_review",
                employee_id="E1003",
                request=T3_TEXT,
                reason="E6: Employee reports company phone stolen.",
            )
        ],
        [
            final(
                "escalate",
                review_id="REV-0001",
                reason_code="E6",
                reply="Sent for human review, reference REV-0001. A security report may be needed.",
            )
        ],
    )

    result = await h.run(T3_TEXT)

    assert result.final.decision == "escalate"
    assert (result.final.review_id, result.final.reason_code) == ("REV-0001", "E6")
    assert result.fallback is False
    (ticket,) = h.tickets()
    assert (ticket["employee_id"], ticket["reason_code"]) == ("E1003", "E6")


async def test_tool_error_is_returned_as_data_not_is_error(h: Harness) -> None:
    # E1: the server's error dict is an observation the model reasons about (rule 6).
    h.script(
        [use("get_employee_info", employee_id="E9999")],
        [
            use(
                "flag_for_human_review",
                employee_id="E9999",
                request="I need a new headset.",
                reason="E1: Employee ID not found.",
            )
        ],
        [final("escalate", review_id="REV-0001", reason_code="E1", reply="Ref REV-0001.")],
    )

    result = await h.run("E9999: I need a new headset.")

    (block,) = h.last_user_message(1)["content"]
    assert block["type"] == "tool_result"
    assert not block.get("is_error", False)
    assert json.loads(_content(block))["error"] == "EMPLOYEE_NOT_FOUND"
    assert result.final.reason_code == "E1"


# --- Guards ----------------------------------------------------------------------------------


async def test_c1_rejects_approve_without_eligibility_then_model_corrects(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [APPROVE_T1],  # too early: no eligibility check yet
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    assert result.final.decision == "approve"
    assert result.fallback is False
    (event,) = result.guard_events
    assert event.tool == "final_answer"
    assert "C1" in event.codes
    assert event.after_step == 1
    # The rejection goes back to the model as an is_error tool_result naming the constraint.
    (block,) = h.last_user_message(2)["content"]
    assert block["is_error"] is True
    assert "C1: " in _content(block)
    blocked = [r for r in h.logs() if r["event"] == "guard.blocked"]
    assert len(blocked) == 1 and blocked[0]["level"] == "WARNING"


async def test_malformed_final_answer_is_rejected_then_corrected(h: Harness) -> None:
    bad = final("approve", rule_id="R-NEW", reply="x")
    bad[1]["decision"] = "maybe"
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [bad],
        [APPROVE_T1],
    )

    result = await h.run()

    assert result.final.decision == "approve"
    (event,) = result.guard_events
    assert event.codes == ("INVALID_FINAL_ANSWER",)
    (block,) = h.last_user_message(3)["content"]
    assert block["is_error"] is True


async def test_two_rejected_final_answers_fall_back_to_e11(h: Harness) -> None:
    h.script([APPROVE_T1], [APPROVE_T1])

    result = await h.run()

    assert result.fallback is True
    assert len(h.calls) == 2  # no model call after the fallback
    assert result.final.decision == "escalate"
    assert (result.final.reason_code, result.final.review_id) == ("E11", "REV-0001")
    assert result.final.draft_reply is not None and "REV-0001" in result.final.draft_reply
    (ticket,) = h.tickets()
    assert ticket["reason_code"] == "E11"
    assert ticket["employee_id"] == "E1002"  # read from the request text
    assert result.steps[-1].tool == "flag_for_human_review"
    fallback_logs = [r for r in h.logs() if r["event"] == "agent.fallback"]
    assert len(fallback_logs) == 1 and fallback_logs[0]["level"] == "WARNING"


async def test_fallback_reuses_an_existing_review_ticket(h: Harness) -> None:
    flag = use(
        "flag_for_human_review",
        employee_id="E1003",
        request=T3_TEXT,
        reason="E6: Employee reports company phone stolen.",
    )
    wrong = final("escalate", review_id="REV-0042", reason_code="E6", reply="Ref REV-0042.")
    h.script([flag], [wrong], [wrong])

    result = await h.run(T3_TEXT)

    assert result.fallback is True
    assert len(h.tickets()) == 1  # C7: the code never flags a second time
    assert result.final.review_id == "REV-0001"
    # The ticket says E6; the answer keeps the ticket's code so the two agree.
    assert result.final.reason_code == "E6"
    assert len(h.tool_calls("flag_for_human_review")) == 1


async def test_step_cap_blocks_ninth_call_then_falls_back(h: Harness) -> None:
    lookups = [[use("get_employee_info", employee_id="E1002")] for _ in range(9)]
    # After the 9th is blocked, the model still doesn't escalate: one more lookup.
    h.script(*lookups, [use("get_employee_info", employee_id="E1002")])

    result = await h.run()

    assert len(h.tool_calls("get_employee_info")) == 8  # the 9th never reached the server
    (event,) = result.guard_events
    assert event.codes == ("C6_STEP_LIMIT",)
    (block,) = h.last_user_message(9)["content"]
    assert block["is_error"] is True and "C6_STEP_LIMIT" in _content(block)
    assert result.fallback is True
    assert len(h.calls) == 10
    assert result.final.reason_code == "E11"
    (ticket,) = h.tickets()
    assert ticket["reason_code"] == "E11"


async def test_step_cap_then_escalation_is_not_a_fallback(h: Harness) -> None:
    lookups = [[use("get_employee_info", employee_id="E1002")] for _ in range(9)]
    h.script(
        *lookups,
        [
            use(
                "flag_for_human_review",
                employee_id="E1002",
                request=T1_TEXT,
                reason="E11: Step limit reached.",
            )
        ],
        [final("escalate", review_id="REV-0001", reason_code="E11", reply="Ref REV-0001.")],
    )

    result = await h.run()

    assert result.fallback is False
    assert result.final.reason_code == "E11"
    assert len(h.tickets()) == 1


async def test_second_flag_is_blocked_by_c7(h: Harness) -> None:
    h.script(
        [
            use(
                "flag_for_human_review",
                employee_id="E1003",
                request=T3_TEXT,
                reason="E6: Employee reports company phone stolen.",
            )
        ],
        [
            use(
                "flag_for_human_review",
                employee_id="E1003",
                request="Also my headset.",
                reason="E5: Second item.",
            )
        ],
        [final("escalate", review_id="REV-0001", reason_code="E6", reply="Ref REV-0001.")],
    )

    result = await h.run(T3_TEXT)

    assert result.final.decision == "escalate"
    (event,) = result.guard_events
    assert event.codes == ("C7_ALREADY_FLAGGED",)
    assert len(h.tool_calls("flag_for_human_review")) == 1
    assert len(h.tickets()) == 1


async def test_max_model_calls_falls_back(h: Harness) -> None:
    h.script(*[[use("get_employee_info", employee_id="E1002")] for _ in range(3)])

    result = await h.run(max_model_calls=3)

    assert result.fallback is True
    assert len(h.calls) == 3
    assert result.final.reason_code == "E11"
    assert len(h.tickets()) == 1


# --- Message handling ----------------------------------------------------------------------


async def test_two_tool_uses_in_one_turn_both_run_one_reply(h: Harness) -> None:
    h.script(
        [
            use("get_employee_info", employee_id="E1002"),
            use("check_request_eligibility", employee_id="E1002", item="monitor"),
        ],
        [APPROVE_T1],
    )

    result = await h.run()

    assert [s.tool for s in result.steps] == ["get_employee_info", "check_request_eligibility"]
    blocks = h.last_user_message(1)["content"]
    assert [b["tool_use_id"] for b in blocks] == ["toolu_01", "toolu_02"]
    assert json.loads(_content(blocks[1]))["status"] == "ELIGIBLE"
    assert result.final.decision == "approve"


async def test_assistant_turn_is_sent_back_unchanged(
    h: Harness, make_message: Callable[..., Message]
) -> None:
    first = make_message(
        thinking="The employee wants a monitor.",
        tool_uses=[use("get_employee_info", employee_id="E1002")],
    )
    h.script(
        first,
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    await h.run()

    # Thinking blocks must go back exactly as received (signature included).
    assistant = h.calls[1]["messages"][1]
    assert assistant == {"role": "assistant", "content": first.content}
    assert assistant["content"][0].type == "thinking"
    # Each call gets a snapshot: later turns don't change what an earlier call was sent.
    assert [len(c["messages"]) for c in h.calls] == [1, 3, 5]


async def test_turn_without_tool_use_asks_for_final_answer(
    h: Harness, make_message: Callable[..., Message]
) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        make_message(tool_name=None, text="Let me think.", stop_reason="end_turn"),
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    assert result.final.decision == "approve"
    assert len(h.calls) == 4  # the nudge counts as a model call
    assert h.last_user_message(2)["content"] == "Call final_answer to finish."


# --- Trace and logs --------------------------------------------------------------------------


async def test_trace_has_run_llm_and_tool_spans(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    spans = h.spans()
    (root,) = [s for s in spans if s["name"] == "agent.run"]
    assert root["parent_span_id"] is None
    children = [s for s in spans if s["parent_span_id"] == root["span_id"]]
    assert sorted(s["name"] for s in children) == [
        "llm.call",
        "llm.call",
        "llm.call",
        "tool.check_request_eligibility",
        "tool.get_employee_info",
    ]
    attributes = root["attributes"]
    assert attributes["request_id"] == REQUEST_ID
    assert attributes["run_id"] == result.run_id
    assert attributes["decision"] == "approve"
    assert attributes["rule_id"] == "R-NEW"
    assert attributes["total_cost_usd"] == pytest.approx(result.cost_usd)
    assert attributes["total_tokens"] == result.input_tokens + result.output_tokens
    tool_span = next(s for s in children if s["name"] == "tool.check_request_eligibility")
    assert tool_span["attributes"]["result_status"] == "ok"
    assert json.loads(tool_span["attributes"]["arguments"]) == {
        "employee_id": "E1002",
        "item": "monitor",
    }


async def test_logs_have_step_events_and_run_end(h: Harness) -> None:
    h.script(
        [use("get_employee_info", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [APPROVE_T1],
    )

    result = await h.run()

    logs = h.logs()
    events = [r["event"] for r in logs]
    assert events[0] == "run.start"
    assert events[-1] == "run.end"
    for event in ("react.thought", "react.action", "react.observation"):
        assert events.count(event) == 2
    observations = [r for r in logs if r["event"] == "react.observation"]
    assert [r["result_status"] for r in observations] == ["ok", "ok"]
    end = logs[-1]
    assert (end["decision"], end["rule_id"], end["review_id"]) == ("approve", "R-NEW", None)
    assert end["cost_usd"] == pytest.approx(result.cost_usd)
    assert end["fallback"] is False


async def test_private_text_stays_out_of_trace_and_info_logs(h: Harness) -> None:
    # The name appears in the request, in a thought, in the reply and in the tool result.
    h.script(
        [use("get_employee_info", thought="Marcus wants a monitor.", employee_id="E1002")],
        [use("check_request_eligibility", employee_id="E1002", item="monitor")],
        [final("approve", rule_id="R-NEW", reply="Hi Marcus, approved under R-NEW.")],
    )

    result = await h.run()

    assert result.steps[0].observation["name"].startswith("Marcus")
    assert "Marcus" not in h.trace_path.read_text()
    above_debug = [r for r in h.logs() if r["level"] != "DEBUG"]
    assert "Marcus" not in json.dumps(above_debug)
    # Each private item is still available at DEBUG, for local troubleshooting.
    debug_text = json.dumps([r for r in h.logs() if r["level"] == "DEBUG"])
    assert T1_TEXT in debug_text and "Hi Marcus" in debug_text

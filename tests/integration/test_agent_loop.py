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


# ---------- scripted scenarios ----------
# Each scenario scripts the model's turns, runs the loop once and returns the result. Tests
# then check one behaviour each, so a failure names exactly what broke.

T1_PATH = (
    [use("get_employee_info", employee_id="E1002")],
    [use("check_request_eligibility", employee_id="E1002", item="monitor")],
    [APPROVE_T1],
)
T3_FLAG = use(
    "flag_for_human_review",
    employee_id="E1003",
    request=T3_TEXT,
    reason="E6: Employee reports company phone stolen.",
)
LOOKUP = [use("get_employee_info", employee_id="E1002")]
CHECK = [use("check_request_eligibility", employee_id="E1002", item="monitor")]
SERVER_TOOLS_AND_FINAL = [
    "check_request_eligibility",
    "final_answer",
    "flag_for_human_review",
    "get_employee_info",
    "get_policy_limits",
]


async def _t1(h: Harness) -> AgentResult:
    h.script(*T1_PATH)
    return await h.run()


async def _t2(h: Harness) -> AgentResult:
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
    return await h.run(T2_TEXT)


async def _t3(h: Harness) -> AgentResult:
    h.script(
        [T3_FLAG],
        [
            final(
                "escalate",
                review_id="REV-0001",
                reason_code="E6",
                reply="Sent for human review, reference REV-0001. A security report may be needed.",
            )
        ],
    )
    return await h.run(T3_TEXT)


async def _unknown_employee(h: Harness) -> AgentResult:
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
    return await h.run("E9999: I need a new headset.")


async def _early_approve(h: Harness) -> AgentResult:
    h.script(LOOKUP, [APPROVE_T1], CHECK, [APPROVE_T1])  # 2nd turn: no eligibility check yet
    return await h.run()


async def _malformed_final(h: Harness) -> AgentResult:
    bad = final("approve", rule_id="R-NEW", reply="x")
    bad[1]["decision"] = "maybe"
    h.script(LOOKUP, CHECK, [bad], [APPROVE_T1])
    return await h.run()


async def _two_rejected(h: Harness) -> AgentResult:
    h.script([APPROVE_T1], [APPROVE_T1])
    return await h.run()


async def _fallback_after_flag(h: Harness) -> AgentResult:
    wrong = final("escalate", review_id="REV-0042", reason_code="E6", reply="Ref REV-0042.")
    h.script([T3_FLAG], [wrong], [wrong])
    return await h.run(T3_TEXT)


async def _step_cap(h: Harness) -> AgentResult:
    # Nine lookups; after the 9th is blocked, the model still doesn't escalate: one more.
    h.script(*[LOOKUP] * 9, LOOKUP)
    return await h.run()


async def _step_cap_then_escalate(h: Harness) -> AgentResult:
    h.script(
        *[LOOKUP] * 9,
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
    return await h.run()


async def _second_flag(h: Harness) -> AgentResult:
    h.script(
        [T3_FLAG],
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
    return await h.run(T3_TEXT)


async def _max_model_calls(h: Harness) -> AgentResult:
    h.script(*[LOOKUP] * 3)
    return await h.run(max_model_calls=3)


async def _two_uses_in_one_turn(h: Harness) -> AgentResult:
    h.script([*LOOKUP, *CHECK], [APPROVE_T1])
    return await h.run()


async def _with_thinking(h: Harness) -> Message:
    first = h.make_message(thinking="The employee wants a monitor.", tool_uses=LOOKUP)
    h.script(first, CHECK, [APPROVE_T1])
    await h.run()
    return first


async def _text_turn(h: Harness) -> AgentResult:
    h.script(
        LOOKUP,
        h.make_message(tool_name=None, text="Let me think.", stop_reason="end_turn"),
        CHECK,
        [APPROVE_T1],
    )
    return await h.run()


async def _private_text(h: Harness) -> AgentResult:
    # The name appears in the request, in a thought, in the reply and in the tool result.
    h.script(
        [use("get_employee_info", thought="Marcus wants a monitor.", employee_id="E1002")],
        CHECK,
        [final("approve", rule_id="R-NEW", reply="Hi Marcus, approved under R-NEW.")],
    )
    return await h.run()


def _decision(result: AgentResult) -> tuple[Any, ...]:
    final_answer = result.final
    return (
        final_answer.decision,
        final_answer.rule_id,
        final_answer.review_id,
        final_answer.reason_code,
        result.fallback,
    )


def _events(h: Harness, event: str) -> list[dict[str, Any]]:
    return [r for r in h.logs() if r["event"] == event]


# ---------- T1 approve (REQUIREMENTS §9.1) ----------


async def test_loop_t1_approves_under_r_new_without_fallback(h: Harness) -> None:
    result = await _t1(h)

    assert _decision(result) == ("approve", "R-NEW", None, None, False)


async def test_loop_t1_trips_no_guard(h: Harness) -> None:
    result = await _t1(h)

    assert result.guard_events == ()


async def test_loop_t1_records_lookup_then_check(h: Harness) -> None:
    result = await _t1(h)

    assert [s.tool for s in result.steps] == ["get_employee_info", "check_request_eligibility"]


async def test_loop_t1_verdict_comes_from_the_real_server(h: Harness) -> None:
    # The script never says ELIGIBLE: the server's policy code does.
    result = await _t1(h)

    assert result.steps[1].observation["status"] == "ELIGIBLE"


async def test_loop_t1_makes_three_model_calls(h: Harness) -> None:
    await _t1(h)

    assert len(h.calls) == 3


async def test_loop_t1_ledger_has_one_stage_per_model_call(h: Harness) -> None:
    await _t1(h)

    assert [line["stage"] for line in h.ledger()] == [
        "react_step_1",
        "react_step_2",
        "react_step_3",
    ]


async def test_loop_t1_ledger_lines_share_the_run_id(h: Harness) -> None:
    result = await _t1(h)

    assert {line["run_id"] for line in h.ledger()} == {result.run_id}


async def test_loop_t1_cost_is_the_ledger_sum(h: Harness) -> None:
    result = await _t1(h)

    assert result.cost_usd == pytest.approx(sum(line["cost_usd"] for line in h.ledger()))


async def test_loop_t1_input_tokens_are_the_ledger_sum(h: Harness) -> None:
    result = await _t1(h)

    assert result.input_tokens == sum(line["input_tokens"] for line in h.ledger())


async def test_loop_server_never_sees_thought(h: Harness) -> None:
    await _t1(h)

    assert ["thought" in call["arguments"] for call in h.tool_calls()] == [False, False]


async def test_loop_every_tool_call_carries_the_request_id(h: Harness) -> None:
    await _t1(h)

    assert [call["meta"]["request_id"] for call in h.tool_calls()] == [REQUEST_ID] * 2


async def test_loop_step_keeps_thought_apart_from_arguments(h: Harness) -> None:
    result = await _t1(h)

    assert (result.steps[0].thought, result.steps[0].arguments) == (
        "Next step.",
        {"employee_id": "E1002"},
    )


async def test_loop_first_request_uses_the_cached_react_prompt(h: Harness) -> None:
    await _t1(h)

    assert h.calls[0]["system"][0] == {
        "type": "text",
        "text": SYSTEM_PROMPT_REACT,
        "cache_control": {"type": "ephemeral"},
    }


async def test_loop_first_request_offers_server_tools_and_final_answer(h: Harness) -> None:
    await _t1(h)

    assert sorted(t["name"] for t in h.calls[0]["tools"]) == SERVER_TOOLS_AND_FINAL


async def test_loop_final_answer_is_the_last_tool_offered(h: Harness) -> None:
    await _t1(h)

    assert h.calls[0]["tools"][-1]["name"] == "final_answer"


async def test_loop_every_offered_tool_requires_thought_first(h: Harness) -> None:
    await _t1(h)

    first_required = [t["input_schema"]["required"][0] for t in h.calls[0]["tools"]]

    assert set(first_required) == {"thought"}


async def test_loop_first_message_is_request_id_then_request_text(h: Harness) -> None:
    await _t1(h)

    assert h.calls[0]["messages"] == [
        {"role": "user", "content": f"Request ID: {REQUEST_ID}\n\n{T1_TEXT}"}
    ]


# ---------- T2 deny and T3 escalate ----------


async def test_loop_t2_denies_under_r_refresh(h: Harness) -> None:
    result = await _t2(h)

    assert _decision(result) == ("deny", "R-REFRESH", None, None, False)


async def test_loop_t2_reply_cites_the_rule(h: Harness) -> None:
    result = await _t2(h)

    assert "R-REFRESH" in (result.final.draft_reply or "")


async def test_loop_t2_trips_no_guard(h: Harness) -> None:
    result = await _t2(h)

    assert result.guard_events == ()


async def test_loop_t2_writes_no_ticket(h: Harness) -> None:
    await _t2(h)

    assert h.tickets() == []


async def test_loop_t3_escalates_e6_with_the_ticket_id(h: Harness) -> None:
    result = await _t3(h)

    assert _decision(result) == ("escalate", None, "REV-0001", "E6", False)


async def test_loop_t3_writes_one_e6_ticket_for_the_employee(h: Harness) -> None:
    await _t3(h)

    assert [(t["employee_id"], t["reason_code"]) for t in h.tickets()] == [("E1003", "E6")]


# ---------- tool errors are data ----------


async def test_loop_tool_error_goes_back_as_a_normal_tool_result(h: Harness) -> None:
    await _unknown_employee(h)

    (block,) = h.last_user_message(1)["content"]

    assert (block["type"], block.get("is_error", False)) == ("tool_result", False)


async def test_loop_tool_error_result_carries_the_error_code(h: Harness) -> None:
    await _unknown_employee(h)

    (block,) = h.last_user_message(1)["content"]

    assert json.loads(_content(block))["error"] == "EMPLOYEE_NOT_FOUND"


async def test_loop_unknown_employee_escalates_e1(h: Harness) -> None:
    result = await _unknown_employee(h)

    assert result.final.reason_code == "E1"


# ---------- guards: C1 and a malformed final answer ----------


async def test_loop_c1_rejection_is_corrected_by_the_model(h: Harness) -> None:
    result = await _early_approve(h)

    assert _decision(result) == ("approve", "R-NEW", None, None, False)


async def test_loop_c1_rejection_records_one_guard_event(h: Harness) -> None:
    result = await _early_approve(h)

    (event,) = result.guard_events

    assert (event.tool, "C1" in event.codes, event.after_step) == ("final_answer", True, 1)


async def test_loop_c1_rejection_is_an_is_error_result(h: Harness) -> None:
    await _early_approve(h)

    (block,) = h.last_user_message(2)["content"]

    assert block["is_error"] is True


async def test_loop_c1_rejection_names_the_constraint(h: Harness) -> None:
    await _early_approve(h)

    (block,) = h.last_user_message(2)["content"]

    assert "C1: " in _content(block)


async def test_loop_c1_rejection_logs_one_warning(h: Harness) -> None:
    await _early_approve(h)

    assert [r["level"] for r in _events(h, "guard.blocked")] == ["WARNING"]


async def test_loop_malformed_final_answer_is_corrected(h: Harness) -> None:
    result = await _malformed_final(h)

    assert result.final.decision == "approve"


async def test_loop_malformed_final_answer_records_invalid_final_answer(h: Harness) -> None:
    result = await _malformed_final(h)

    assert [e.codes for e in result.guard_events] == [("INVALID_FINAL_ANSWER",)]


async def test_loop_malformed_final_answer_is_an_is_error_result(h: Harness) -> None:
    await _malformed_final(h)

    (block,) = h.last_user_message(3)["content"]

    assert block["is_error"] is True


# ---------- fallback after two rejections ----------


async def test_loop_two_rejections_fall_back_to_e11_escalation(h: Harness) -> None:
    result = await _two_rejected(h)

    assert _decision(result) == ("escalate", None, "REV-0001", "E11", True)


async def test_loop_fallback_makes_no_further_model_call(h: Harness) -> None:
    await _two_rejected(h)

    assert len(h.calls) == 2


async def test_loop_fallback_reply_quotes_the_review_id(h: Harness) -> None:
    result = await _two_rejected(h)

    assert "REV-0001" in (result.final.draft_reply or "")


async def test_loop_fallback_files_e11_ticket_with_id_from_request_text(h: Harness) -> None:
    await _two_rejected(h)

    assert [(t["reason_code"], t["employee_id"]) for t in h.tickets()] == [("E11", "E1002")]


async def test_loop_fallback_flag_is_the_last_step(h: Harness) -> None:
    result = await _two_rejected(h)

    assert result.steps[-1].tool == "flag_for_human_review"


async def test_loop_fallback_logs_one_warning(h: Harness) -> None:
    await _two_rejected(h)

    assert [r["level"] for r in _events(h, "agent.fallback")] == ["WARNING"]


async def test_loop_fallback_with_no_employee_id_files_unknown(h: Harness) -> None:
    # No E-number anywhere in the request: the ticket still gets written, as UNKNOWN.
    h.script([APPROVE_T1], [APPROVE_T1])

    await h.run("Hi, can I get a new monitor?")

    assert [t["employee_id"] for t in h.tickets()] == ["UNKNOWN"]


async def test_loop_fallback_reuses_an_existing_ticket(h: Harness) -> None:
    # C7: the code never flags a second time, so the earlier ticket is the answer.
    result = await _fallback_after_flag(h)

    assert _decision(result) == ("escalate", None, "REV-0001", "E6", True)


async def test_loop_fallback_after_flag_writes_no_second_ticket(h: Harness) -> None:
    await _fallback_after_flag(h)

    assert (len(h.tickets()), len(h.tool_calls("flag_for_human_review"))) == (1, 1)


# ---------- C6 step cap, C7 and the model-call cap ----------


async def test_loop_step_cap_keeps_the_ninth_call_from_the_server(h: Harness) -> None:
    await _step_cap(h)

    assert len(h.tool_calls("get_employee_info")) == 8


async def test_loop_step_cap_records_one_c6_guard_event(h: Harness) -> None:
    result = await _step_cap(h)

    assert [e.codes for e in result.guard_events] == [("C6_STEP_LIMIT",)]


async def test_loop_step_cap_tells_the_model_with_an_error_result(h: Harness) -> None:
    await _step_cap(h)

    (block,) = h.last_user_message(9)["content"]

    assert (block["is_error"], "C6_STEP_LIMIT" in _content(block)) == (True, True)


async def test_loop_step_cap_ignored_falls_back_to_e11(h: Harness) -> None:
    result = await _step_cap(h)

    assert (result.fallback, result.final.reason_code, len(h.calls)) == (True, "E11", 10)


async def test_loop_step_cap_fallback_files_one_e11_ticket(h: Harness) -> None:
    await _step_cap(h)

    assert [t["reason_code"] for t in h.tickets()] == ["E11"]


async def test_loop_step_cap_then_escalation_is_not_a_fallback(h: Harness) -> None:
    result = await _step_cap_then_escalate(h)

    assert (result.fallback, result.final.reason_code, len(h.tickets())) == (False, "E11", 1)


async def test_loop_second_flag_is_blocked_by_c7(h: Harness) -> None:
    result = await _second_flag(h)

    assert [e.codes for e in result.guard_events] == [("C7_ALREADY_FLAGGED",)]


async def test_loop_second_flag_never_reaches_the_server(h: Harness) -> None:
    await _second_flag(h)

    assert (len(h.tool_calls("flag_for_human_review")), len(h.tickets())) == (1, 1)


async def test_loop_second_flag_run_still_escalates(h: Harness) -> None:
    result = await _second_flag(h)

    assert result.final.decision == "escalate"


async def test_loop_max_model_calls_falls_back_to_e11(h: Harness) -> None:
    result = await _max_model_calls(h)

    assert (result.fallback, result.final.reason_code) == (True, "E11")


async def test_loop_max_model_calls_stops_at_the_cap(h: Harness) -> None:
    await _max_model_calls(h)

    assert (len(h.calls), len(h.tickets())) == (3, 1)


# ---------- message handling ----------


async def test_loop_two_tool_uses_in_one_turn_both_run(h: Harness) -> None:
    result = await _two_uses_in_one_turn(h)

    assert [s.tool for s in result.steps] == ["get_employee_info", "check_request_eligibility"]


async def test_loop_two_tool_uses_get_one_reply_with_both_results(h: Harness) -> None:
    await _two_uses_in_one_turn(h)

    blocks = h.last_user_message(1)["content"]

    assert [b["tool_use_id"] for b in blocks] == ["toolu_01", "toolu_02"]


async def test_loop_two_tool_uses_results_keep_their_order(h: Harness) -> None:
    await _two_uses_in_one_turn(h)

    blocks = h.last_user_message(1)["content"]

    assert json.loads(_content(blocks[1]))["status"] == "ELIGIBLE"


async def test_loop_two_tool_uses_run_still_approves(h: Harness) -> None:
    result = await _two_uses_in_one_turn(h)

    assert result.final.decision == "approve"


async def test_loop_assistant_turn_is_sent_back_unchanged(h: Harness) -> None:
    # Thinking blocks must go back exactly as received (signature included).
    first = await _with_thinking(h)

    assert h.calls[1]["messages"][1] == {"role": "assistant", "content": first.content}


async def test_loop_assistant_turn_keeps_its_thinking_block_first(h: Harness) -> None:
    await _with_thinking(h)

    assert h.calls[1]["messages"][1]["content"][0].type == "thinking"


async def test_loop_each_model_call_gets_a_message_snapshot(h: Harness) -> None:
    # Later turns don't change what an earlier call was sent.
    await _with_thinking(h)

    assert [len(c["messages"]) for c in h.calls] == [1, 3, 5]


async def test_loop_text_turn_is_nudged_to_call_final_answer(h: Harness) -> None:
    await _text_turn(h)

    assert h.last_user_message(2)["content"] == "Call final_answer to finish."


async def test_loop_text_turn_nudge_counts_as_a_model_call(h: Harness) -> None:
    await _text_turn(h)

    assert len(h.calls) == 4


async def test_loop_text_turn_run_still_approves(h: Harness) -> None:
    result = await _text_turn(h)

    assert result.final.decision == "approve"


# ---------- trace ----------


def _root(h: Harness) -> dict[str, Any]:
    (root,) = [s for s in h.spans() if s["name"] == "agent.run"]
    return root


def _children(h: Harness) -> list[dict[str, Any]]:
    root_id = _root(h)["span_id"]
    return [s for s in h.spans() if s["parent_span_id"] == root_id]


async def test_loop_trace_root_span_has_no_parent(h: Harness) -> None:
    await _t1(h)

    assert _root(h)["parent_span_id"] is None


async def test_loop_trace_root_has_llm_and_tool_children(h: Harness) -> None:
    await _t1(h)

    assert sorted(s["name"] for s in _children(h)) == [
        "llm.call",
        "llm.call",
        "llm.call",
        "tool.check_request_eligibility",
        "tool.get_employee_info",
    ]


async def test_loop_trace_root_names_request_run_and_decision(h: Harness) -> None:
    result = await _t1(h)
    attributes = _root(h)["attributes"]

    assert {k: attributes[k] for k in ("request_id", "run_id", "decision", "rule_id")} == {
        "request_id": REQUEST_ID,
        "run_id": result.run_id,
        "decision": "approve",
        "rule_id": "R-NEW",
    }


async def test_loop_trace_root_total_cost_matches_the_result(h: Harness) -> None:
    result = await _t1(h)

    assert _root(h)["attributes"]["total_cost_usd"] == pytest.approx(result.cost_usd)


async def test_loop_trace_root_total_tokens_match_the_result(h: Harness) -> None:
    result = await _t1(h)

    assert _root(h)["attributes"]["total_tokens"] == result.input_tokens + result.output_tokens


async def test_loop_trace_tool_span_records_status_and_arguments(h: Harness) -> None:
    await _t1(h)
    tool_span = next(s for s in _children(h) if s["name"] == "tool.check_request_eligibility")
    attributes = tool_span["attributes"]

    assert (attributes["result_status"], json.loads(attributes["arguments"])) == (
        "ok",
        {"employee_id": "E1002", "item": "monitor"},
    )


# ---------- logs ----------


async def test_loop_logs_start_with_run_start_and_end_with_run_end(h: Harness) -> None:
    await _t1(h)
    events = [r["event"] for r in h.logs()]

    assert (events[0], events[-1]) == ("run.start", "run.end")


@pytest.mark.parametrize("event", ["react.thought", "react.action", "react.observation"], ids=str)
async def test_loop_logs_one_react_event_per_step(h: Harness, event: str) -> None:
    await _t1(h)

    assert len(_events(h, event)) == 2


async def test_loop_observation_logs_carry_result_status(h: Harness) -> None:
    await _t1(h)

    assert [r["result_status"] for r in _events(h, "react.observation")] == ["ok", "ok"]


async def test_loop_run_end_log_has_the_decision(h: Harness) -> None:
    await _t1(h)
    end = h.logs()[-1]

    assert (end["decision"], end["rule_id"], end["review_id"], end["fallback"]) == (
        "approve",
        "R-NEW",
        None,
        False,
    )


async def test_loop_run_end_log_cost_matches_the_result(h: Harness) -> None:
    result = await _t1(h)

    assert h.logs()[-1]["cost_usd"] == pytest.approx(result.cost_usd)


# ---------- private text ----------


async def test_loop_tool_result_does_hold_the_name(h: Harness) -> None:
    # Guards the premise: the name really is in the data the agent sees.
    result = await _private_text(h)

    assert result.steps[0].observation["name"].startswith("Marcus")


async def test_loop_name_never_reaches_the_trace(h: Harness) -> None:
    await _private_text(h)

    assert "Marcus" not in h.trace_path.read_text()


async def test_loop_name_never_reaches_info_or_higher_logs(h: Harness) -> None:
    await _private_text(h)

    above_debug = [r for r in h.logs() if r["level"] != "DEBUG"]

    assert "Marcus" not in json.dumps(above_debug)


@pytest.mark.parametrize("text", [T1_TEXT, "Hi Marcus"], ids=["request", "reply"])
async def test_loop_private_text_is_kept_at_debug(h: Harness, text: str) -> None:
    # Each private item is still available at DEBUG, for local troubleshooting.
    await _private_text(h)

    debug_text = json.dumps([r for r in h.logs() if r["level"] == "DEBUG"])

    assert text in debug_text

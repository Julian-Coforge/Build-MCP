"""Reflection inside the agent run, and the labelled fault-injection demo (REQUIREMENTS §11).

Scripted FakeMessages, real in-process MCP server, as in test_agent_loop.py. Every run with
a reflector queues one extra model response: the reflection_verdict turn. The Harness is
copied from test_agent_loop.py (test folders are not packages, and its helpers are plain
functions), extended with the reflector and inject_fault options.
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

from it_requests import config
from it_requests.agent.llm import LLMClient
from it_requests.agent.loop import FAULT_SENTENCE, AgentRequest, AgentResult, run_agent
from it_requests.agent.reflection import Reflector
from it_requests.agent.trace_format import format_trace
from it_requests.observability import JsonFormatter
from it_requests.tracing import make_tracer

pytestmark = pytest.mark.anyio

REQUEST_ID = "REQ-0001"
T1_TEXT = (
    "Hi, I'm Marcus (E1002). I'd like a second monitor for my desk, I'm juggling a lot of windows."
)
T2_TEXT = "This is Priya, E1001. My laptop feels slow lately, can I get a new one?"
FAULT_DATE = "2026-11-28"  # in no data file and no tool result: the fault breaks C4 and C5

Use = tuple[str, dict[str, object]]


def use(name: str, **arguments: object) -> Use:
    return name, {"thought": "Next step.", **arguments}


def final(decision: str, *, rule_id: str | None = None, reply: str = "") -> Use:
    return "final_answer", {
        "thought": "The decision is made.",
        "decision": decision,
        "rule_id": rule_id,
        "review_id": None,
        "reason_code": None,
        "draft_reply": reply,
    }


ALL_YES = {f"q{n}": True for n in range(1, 7)}


def verdict(status: str = "CONFIRMED", reply: str | None = None, change: str | None = None) -> Use:
    return "reflection_verdict", {
        "thought": "Checked the draft against the checklist.",
        "answers": ALL_YES if status == "CONFIRMED" else ALL_YES | {"q3": False},
        "verdict": status,
        "revised_reply": reply,
        "what_changed": change,
    }


T1_REPLY = (
    "Approved: your role allows up to 2 monitors and you have 1 (R-NEW). IT will arrange "
    "fulfilment."
)
T1_PATH = (
    [use("get_employee_info", employee_id="E1002")],
    [use("check_request_eligibility", employee_id="E1002", item="monitor")],
    [final("approve", rule_id="R-NEW", reply=T1_REPLY)],
)
T2_REPLY = (
    "Your laptop is not due for refresh until 2028-03-10 (R-REFRESH). The IT helpdesk can "
    "help with performance."
)
T2_PATH = (
    [use("get_employee_info", employee_id="E1001")],
    [use("check_request_eligibility", employee_id="E1001", item="laptop")],
    [use("get_policy_limits", role="standard")],
    [final("deny", rule_id="R-REFRESH", reply=T2_REPLY)],
)
# The model's clean rewrite of the fault run: the delivery promise is gone.
CLEAN_REWRITE = "Approved under R-NEW: you hold 1 of 2 monitors. IT will arrange fulfilment."
# Drops the rule ID, so the guards (C2) reject it and the draft stands.
RULELESS_REWRITE = "Your laptop is not due for refresh until 2028-03-10."
FAULT_LABEL = "[FAULT INJECTED: demo run, a delivery promise was added to the draft]"


class _ExplodingMCP:
    """An MCP client that must never be used: the argument check comes first."""

    async def list_tools(self) -> Any:
        raise AssertionError("run_agent must refuse inject_fault before touching MCP")

    async def call_tool(self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any]) -> Any:
        raise AssertionError("run_agent must refuse inject_fault before touching MCP")


class Harness:
    """Scripts the model's turns, runs the agent once, and reads back what it wrote."""

    def __init__(
        self,
        tmp_path: Path,
        fake_messages: Any,
        make_message: Callable[..., Message],
        server: MCPServer,
        logger: logging.Logger,
        log_stream: io.StringIO,
    ) -> None:
        self.ledger_path = tmp_path / "logs" / "cost_ledger.jsonl"
        self.trace_path = tmp_path / "traces" / f"{REQUEST_ID}.jsonl"
        self.fake = fake_messages
        self.make_message = make_message
        self.server = server
        self.logger = logger
        self.log_stream = log_stream

    def script(self, *turns: list[Use]) -> None:
        for turn in turns:
            self.fake.queue(self.make_message(tool_uses=turn))

    async def run(
        self, text: str = T1_TEXT, *, reflect: bool = True, inject_fault: bool = False
    ) -> AgentResult:
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
        reflector = Reflector(llm=llm, tracer=tracer, logger=self.logger) if reflect else None
        try:
            async with Client(self.server) as client:
                return await run_agent(
                    AgentRequest(request_id=REQUEST_ID, request_text=text),
                    llm=llm,
                    mcp_client=client,  # type: ignore[arg-type]
                    tracer=tracer,
                    logger=self.logger,
                    reflector=reflector,
                    inject_fault=inject_fault,
                )
        finally:
            provider.shutdown()  # flush the trace file

    async def run_unconnected(self, *, inject_fault: bool) -> AgentResult:
        # No `async with Client`: its task group would wrap an early error in an
        # ExceptionGroup, hiding the plain ValueError the caller actually gets.
        tracer, provider = make_tracer(self.trace_path)
        llm = LLMClient(
            create=self.fake.create,
            model=config.DEV_MODEL,
            env="dev",
            ledger_path=self.ledger_path,
            tracer=tracer,
            logger=self.logger,
            clock=lambda: 0.0,
        )
        try:
            return await run_agent(
                AgentRequest(request_id=REQUEST_ID, request_text=T1_TEXT),
                llm=llm,
                mcp_client=_ExplodingMCP(),
                tracer=tracer,
                logger=self.logger,
                reflector=None,
                inject_fault=inject_fault,
            )
        finally:
            provider.shutdown()

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self.fake.calls)

    def ledger_stages(self) -> list[str]:
        lines = self.ledger_path.read_text().splitlines()
        return [json.loads(line)["stage"] for line in lines]

    def ledger_total(self) -> float:
        lines = self.ledger_path.read_text().splitlines()
        return float(sum(json.loads(line)["cost_usd"] for line in lines))

    def spans(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.trace_path.read_text().splitlines()]

    def span(self, name: str) -> dict[str, Any]:
        return next(s for s in self.spans() if s["name"] == name)

    def child_names(self, parent: str) -> list[str]:
        parent_id = self.span(parent)["span_id"]
        return sorted(s["name"] for s in self.spans() if s["parent_span_id"] == parent_id)

    def records(self, event: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in self.log_stream.getvalue().splitlines()]
        return [r for r in lines if r["event"] == event]

    def reflection_request(self) -> str:
        # The reflection call is always the last model call of a run.
        content: str = self.calls[-1]["messages"][0]["content"]
        return content


@pytest.fixture
def agent_logger(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.reflection.loop.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
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
) -> Harness:
    logger, stream = agent_logger
    return Harness(tmp_path, fake_messages, make_message, server, logger, stream)


# ---------- scripted scenarios ----------


async def _t1_confirmed(h: Harness) -> AgentResult:
    h.script(*T1_PATH, [verdict()])
    return await h.run()


async def _fault_model_confirms(h: Harness) -> AgentResult:
    # The model misses the injected promise and says CONFIRMED: only the code catches it.
    h.script(*T1_PATH, [verdict()])
    return await h.run(inject_fault=True)


async def _fault_model_revises(h: Harness) -> AgentResult:
    h.script(*T1_PATH, [verdict("REVISED", CLEAN_REWRITE, "Removed the delivery promise.")])
    return await h.run(inject_fault=True)


async def _t2_ruleless_revision(h: Harness) -> AgentResult:
    h.script(*T2_PATH, [verdict("REVISED", RULELESS_REWRITE, "Shortened the reply.")])
    return await h.run(T2_TEXT)


async def _fallback(h: Harness) -> AgentResult:
    # Two rejected final answers: the code escalates. No verdict is queued, so a
    # reflection call would fail the run (FakeMessages raises when nothing is queued).
    approve_too_early = [final("approve", rule_id="R-NEW", reply=T1_REPLY)]
    h.script(approve_too_early, approve_too_early)
    return await h.run()


async def _t1_without_reflector(h: Harness) -> AgentResult:
    h.script(*T1_PATH)
    return await h.run(reflect=False)


# ---------- T1 with a reflector: CONFIRMED ----------


async def test_reflection_t1_confirmed_keeps_the_approval(h: Harness) -> None:
    result = await _t1_confirmed(h)

    assert result.final.decision == "approve"


async def test_reflection_t1_confirmed_reports_confirmed(h: Harness) -> None:
    result = await _t1_confirmed(h)

    assert result.reflection is not None and result.reflection.status == "CONFIRMED"


async def test_reflection_t1_confirmed_makes_four_model_calls(h: Harness) -> None:
    await _t1_confirmed(h)

    assert len(h.calls) == 4


async def test_reflection_t1_confirmed_ledger_ends_with_reflection(h: Harness) -> None:
    await _t1_confirmed(h)

    assert h.ledger_stages() == ["react_step_1", "react_step_2", "react_step_3", "reflection"]


async def test_reflection_t1_confirmed_sends_the_reply_unchanged(h: Harness) -> None:
    result = await _t1_confirmed(h)

    assert result.final.draft_reply == T1_REPLY


async def test_reflection_t1_confirmed_is_not_marked_fault_injected(h: Harness) -> None:
    result = await _t1_confirmed(h)

    assert result.fault_injected is False


async def test_reflection_run_cost_includes_the_reflection_call(h: Harness) -> None:
    # Run totals must match the ledger, reflection line included (PLAN §4).
    result = await _t1_confirmed(h)

    assert result.cost_usd == pytest.approx(h.ledger_total())


async def test_reflection_root_span_total_includes_the_reflection_call(h: Harness) -> None:
    await _t1_confirmed(h)

    assert h.span("agent.run")["attributes"]["total_cost_usd"] == pytest.approx(h.ledger_total())


async def test_reflection_run_end_logs_the_reflection_status(h: Harness) -> None:
    await _t1_confirmed(h)

    assert [r["reflection"] for r in h.records("run.end")] == ["CONFIRMED"]


async def test_reflection_root_span_records_the_status(h: Harness) -> None:
    await _t1_confirmed(h)

    assert h.span("agent.run")["attributes"]["reflection"] == "CONFIRMED"


async def test_reflection_span_sits_under_agent_run(h: Harness) -> None:
    await _t1_confirmed(h)

    assert "reflection" in h.child_names("agent.run")


async def test_reflection_span_has_an_llm_call_child(h: Harness) -> None:
    await _t1_confirmed(h)

    assert h.child_names("reflection") == ["llm.call"]


# ---------- fault injection: the model says CONFIRMED, the code catches it ----------


async def test_fault_model_confirms_result_is_revised(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert result.reflection is not None and result.reflection.status == "REVISED"


async def test_fault_model_confirms_final_reply_drops_the_date(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert FAULT_DATE not in (result.final.draft_reply or "")


async def test_fault_model_confirms_final_reply_is_the_original(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert result.final.draft_reply == T1_REPLY


async def test_fault_model_confirms_changes_name_the_date(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert result.reflection is not None and result.reflection.changes == (
        f"removed unsupported date {FAULT_DATE}",
    )


async def test_fault_draft_before_reflection_holds_the_fault(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert result.reflection is not None and result.reflection.draft_reply == (
        f"{T1_REPLY} {FAULT_SENTENCE}"
    )


async def test_fault_reflection_model_sees_the_faulty_draft(h: Harness) -> None:
    await _fault_model_confirms(h)

    assert FAULT_SENTENCE in h.reflection_request()


async def test_fault_run_is_marked_fault_injected(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert result.fault_injected is True


async def test_fault_injection_logs_one_warning(h: Harness) -> None:
    await _fault_model_confirms(h)

    assert [r["level"] for r in h.records("reflection.fault_injected")] == ["WARNING"]


async def test_fault_root_span_is_marked_fault_injected(h: Harness) -> None:
    await _fault_model_confirms(h)

    assert h.span("agent.run")["attributes"]["fault_injected"] is True


async def test_fault_trace_starts_with_the_fault_label(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert format_trace(result).splitlines()[0] == FAULT_LABEL


async def test_fault_trace_shows_the_revision(h: Harness) -> None:
    result = await _fault_model_confirms(h)

    assert "REFLECTION: REVISED (" in format_trace(result)


# ---------- fault injection: the model revises ----------


async def test_fault_model_revises_result_is_revised(h: Harness) -> None:
    result = await _fault_model_revises(h)

    assert result.reflection is not None and result.reflection.status == "REVISED"


async def test_fault_model_revises_sends_the_clean_rewrite(h: Harness) -> None:
    result = await _fault_model_revises(h)

    assert result.final.draft_reply == CLEAN_REWRITE


async def test_fault_model_revises_keeps_the_approval(h: Harness) -> None:
    result = await _fault_model_revises(h)

    assert result.final.decision == "approve"


# ---------- T2: a revision that breaks the guards is discarded ----------


async def test_t2_ruleless_revision_reports_confirmed(h: Harness) -> None:
    result = await _t2_ruleless_revision(h)

    assert result.reflection is not None and result.reflection.status == "CONFIRMED"


async def test_t2_ruleless_revision_keeps_the_cited_reply(h: Harness) -> None:
    result = await _t2_ruleless_revision(h)

    assert result.final.draft_reply == T2_REPLY


# ---------- fallback: reflection is skipped ----------


async def test_fallback_makes_no_reflection_call(h: Harness) -> None:
    await _fallback(h)

    assert len(h.calls) == 2


async def test_fallback_reflection_is_skipped(h: Harness) -> None:
    result = await _fallback(h)

    assert result.reflection is not None and result.reflection.status == "SKIPPED"


async def test_fallback_run_end_logs_skipped(h: Harness) -> None:
    await _fallback(h)

    assert [r["reflection"] for r in h.records("run.end")] == ["SKIPPED"]


# ---------- no reflector: the M6 behaviour ----------


async def test_no_reflector_makes_three_model_calls_for_t1(h: Harness) -> None:
    await _t1_without_reflector(h)

    assert len(h.calls) == 3


async def test_no_reflector_result_has_no_reflection(h: Harness) -> None:
    result = await _t1_without_reflector(h)

    assert result.reflection is None


async def test_no_reflector_run_end_logs_no_reflection(h: Harness) -> None:
    await _t1_without_reflector(h)

    assert [r["reflection"] for r in h.records("run.end")] == [None]


async def test_inject_fault_without_reflector_raises(h: Harness) -> None:
    with pytest.raises(ValueError, match="reflector"):
        await h.run_unconnected(inject_fault=True)

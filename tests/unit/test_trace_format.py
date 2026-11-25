"""Tests for format_trace: the printed Thought, Action, Observation trace (REQUIREMENTS §12.2).

Built on hand-made AgentResults, so the format is pinned independently of the loop.
"""

from it_requests.agent.guards import FinalAnswer
from it_requests.agent.loop import AgentResult, GuardEvent, Step
from it_requests.agent.trace_format import format_trace

EMPLOYEE = {"employee_id": "E1002", "role": "engineer", "status": "active"}
VERDICT = {"employee_id": "E1002", "item": "monitor", "status": "ELIGIBLE", "rule_id": "R-NEW"}


def _result(
    *,
    steps: tuple[Step, ...],
    final: FinalAnswer,
    guard_events: tuple[GuardEvent, ...] = (),
    fallback: bool = False,
    final_thought: str = "",
) -> AgentResult:
    return AgentResult(
        request_id="REQ-0001",
        run_id="run-1",
        steps=steps,
        final=final,
        guard_events=guard_events,
        fallback=fallback,
        cost_usd=0.001,
        input_tokens=3000,
        output_tokens=300,
        final_thought=final_thought,
    )


LOOKUP = Step(
    thought="Need the employee's role first.",
    tool="get_employee_info",
    arguments={"employee_id": "E1002"},
    observation=EMPLOYEE,
)
CHECK = Step(
    thought="Now check eligibility for a monitor.",
    tool="check_request_eligibility",
    arguments={"employee_id": "E1002", "item": "monitor"},
    observation=VERDICT,
)
APPROVE = FinalAnswer(
    decision="approve", rule_id="R-NEW", draft_reply="Approved: you may have a second monitor."
)


def test_format_trace_approve_renders_every_line() -> None:
    result = _result(steps=(LOOKUP, CHECK), final=APPROVE, final_thought="Eligible under R-NEW.")

    assert format_trace(result) == "\n".join(
        [
            "Thought: Need the employee's role first.",
            'Action: get_employee_info({"employee_id": "E1002"})',
            'Observation: {"employee_id": "E1002", "role": "engineer", "status": "active"}',
            "Thought: Now check eligibility for a monitor.",
            'Action: check_request_eligibility({"employee_id": "E1002", "item": "monitor"})',
            'Observation: {"employee_id": "E1002", "item": "monitor", "status": "ELIGIBLE", '
            '"rule_id": "R-NEW"}',
            "Thought: Eligible under R-NEW.",
            "Final Answer: APPROVE (rule R-NEW)",
            "Reply: Approved: you may have a second monitor.",
        ]
    )


def test_format_trace_deny_shows_rule() -> None:
    deny = FinalAnswer(decision="deny", rule_id="R-REFRESH", draft_reply="Denied (R-REFRESH).")

    text = format_trace(_result(steps=(LOOKUP,), final=deny))

    assert "Final Answer: DENY (rule R-REFRESH)" in text


def test_format_trace_escalate_shows_code_and_review_id() -> None:
    escalate = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="Sent for review, reference REV-0001.",
    )

    text = format_trace(_result(steps=(LOOKUP,), final=escalate))

    assert "Final Answer: ESCALATE (E6, review REV-0001)" in text


def _guarded_lines() -> list[str]:
    guard = GuardEvent(after_step=1, tool="final_answer", codes=("C1", "REQUIRED_CALLS"))
    text = format_trace(_result(steps=(LOOKUP, CHECK), final=APPROVE, guard_events=(guard,)))
    return text.splitlines()


GUARD_LINE = "GUARD: blocked final_answer (C1, REQUIRED_CALLS)"


def test_format_trace_guard_line_follows_its_step_observation() -> None:
    lines = _guarded_lines()

    previous = lines[lines.index(GUARD_LINE) - 1]

    assert previous.startswith('Observation: {"employee_id": "E1002", "role"')


def test_format_trace_guard_line_comes_before_the_next_thought() -> None:
    lines = _guarded_lines()

    following = lines[lines.index(GUARD_LINE) + 1]

    assert following == "Thought: Now check eligibility for a monitor."


def test_format_trace_guard_before_any_step_is_the_first_line() -> None:
    guard = GuardEvent(after_step=0, tool="final_answer", codes=("C1",))

    lines = format_trace(_result(steps=(LOOKUP,), final=APPROVE, guard_events=(guard,)))

    assert lines.splitlines()[0] == "GUARD: blocked final_answer (C1)"


def _fallback_text() -> str:
    flag_step = Step(
        thought="Fallback: final answer rejected twice.",
        tool="flag_for_human_review",
        arguments={"employee_id": "E1002", "request": "x", "reason": "E11: rejected twice."},
        observation={"review_id": "REV-0001", "status": "pending", "reason_code": "E11"},
    )
    final = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E11",
        draft_reply="Sent for review, reference REV-0001.",
    )
    return format_trace(_result(steps=(flag_step,), final=final, fallback=True))


def test_format_trace_fallback_is_labelled() -> None:
    assert "Final Answer (fallback): ESCALATE (E11, review REV-0001)" in _fallback_text()


def test_format_trace_fallback_has_no_plain_final_answer_line() -> None:
    assert "Final Answer: " not in _fallback_text()


def _bare_lines() -> list[str]:
    step = Step(thought="", tool="get_employee_info", arguments={}, observation={})
    final = FinalAnswer(decision="approve", rule_id="R-NEW")
    return format_trace(_result(steps=(step,), final=final)).splitlines()


def test_format_trace_missing_thought_is_shown_as_none() -> None:
    assert _bare_lines()[0] == "Thought: (none)"


def test_format_trace_missing_reply_is_shown_as_none() -> None:
    assert _bare_lines()[-1] == "Reply: (none)"


def test_format_trace_no_final_thought_adds_no_thought_line() -> None:
    # No final thought: the Final Answer follows the last Observation directly.
    assert _bare_lines()[-3].startswith("Observation:")

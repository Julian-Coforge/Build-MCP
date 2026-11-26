"""Tests for format_trace: the printed Thought, Action, Observation trace (REQUIREMENTS §12.2).

Built on hand-made AgentResults, so the format is pinned independently of the loop.
"""

from it_requests.agent.guards import FinalAnswer
from it_requests.agent.loop import AgentResult, GuardEvent, Step
from it_requests.agent.reflection import ReflectionResult
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
    reflection: ReflectionResult | None = None,
    fault_injected: bool = False,
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
        reflection=reflection,
        fault_injected=fault_injected,
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


# ---------- reflection lines ----------

FAULTY_DRAFT = "Approved: you may have a second monitor. Delivered by 2026-11-28."
FAULT_LABEL = "[FAULT INJECTED: demo run, a delivery promise was added to the draft]"


def _reflection(
    status: str, *, draft: str | None, changes: tuple[str, ...] = ()
) -> ReflectionResult:
    return ReflectionResult(
        status=status,  # type: ignore[arg-type]
        changes=changes,
        final=APPROVE,
        draft_reply=draft,
        answers=None,
    )


CONFIRMED = _reflection("CONFIRMED", draft=APPROVE.draft_reply)
REVISED = _reflection(
    "REVISED",
    draft=FAULTY_DRAFT,
    changes=("Removed the delivery promise.", "removed unsupported date 2026-11-28"),
)
SKIPPED = _reflection("SKIPPED", draft=APPROVE.draft_reply)


def test_format_trace_confirmed_reflection_sits_between_answer_and_reply() -> None:
    lines = format_trace(_result(steps=(LOOKUP,), final=APPROVE, reflection=CONFIRMED))

    assert lines.splitlines()[-3:] == [
        "Final Answer: APPROVE (rule R-NEW)",
        "REFLECTION: CONFIRMED",
        "Reply: Approved: you may have a second monitor.",
    ]


def test_format_trace_revised_reflection_shows_draft_changes_and_final_reply() -> None:
    lines = format_trace(_result(steps=(LOOKUP,), final=APPROVE, reflection=REVISED))

    assert lines.splitlines()[-4:] == [
        "Final Answer: APPROVE (rule R-NEW)",
        f"Draft (before reflection): {FAULTY_DRAFT}",
        "REFLECTION: REVISED (Removed the delivery promise.; removed unsupported date 2026-11-28)",
        "Reply: Approved: you may have a second monitor.",
    ]


def test_format_trace_skipped_reflection_names_the_fallback() -> None:
    text = format_trace(_result(steps=(LOOKUP,), final=APPROVE, reflection=SKIPPED))

    assert "REFLECTION: SKIPPED (fallback reply)" in text.splitlines()


def test_format_trace_no_reflection_adds_no_reflection_line() -> None:
    text = format_trace(_result(steps=(LOOKUP,), final=APPROVE))

    assert "REFLECTION" not in text


def test_format_trace_fault_run_starts_with_the_label() -> None:
    result = _result(steps=(LOOKUP,), final=APPROVE, reflection=REVISED, fault_injected=True)

    assert format_trace(result).splitlines()[0] == FAULT_LABEL


def test_format_trace_normal_run_has_no_fault_label() -> None:
    text = format_trace(_result(steps=(LOOKUP,), final=APPROVE, reflection=CONFIRMED))

    assert "FAULT INJECTED" not in text

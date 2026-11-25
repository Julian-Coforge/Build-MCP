"""Tests for the evaluation helpers: loading cases, grading runs, summarising (PLAN §8).

Grading is pure, so it is tested on hand-built AgentResults; no model is called.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from it_requests import config
from it_requests.agent.guards import FinalAnswer
from it_requests.agent.loop import AgentResult, Step
from it_requests.evals import (
    CASES_FILE,
    Case,
    CaseFileError,
    Grade,
    estimate_usd,
    format_summary,
    grade,
    load_cases,
    resolve_model,
    summarise,
)

REQUIRED_FIELDS = {
    "id",
    "request_id",
    "text",
    "expected_decision",
    "expected_code",
    "required_tools",
}


# ---------- load_cases on the real file ----------


def test_cases_file_lives_in_evals_folder() -> None:
    assert CASES_FILE == config.REPO_ROOT / "evals" / "cases.json"


REAL_CASES = {c.id: c for c in load_cases(CASES_FILE)}
RAW_CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def test_load_cases_real_file_has_t1_to_t12_in_order() -> None:
    assert list(REAL_CASES) == [f"T{n}" for n in range(1, 13)]


def test_load_cases_real_file_numbers_request_ids_in_order() -> None:
    assert [c.request_id for c in REAL_CASES.values()] == [f"REQ-{n:04d}" for n in range(1, 13)]


@pytest.mark.parametrize("raw", RAW_CASES, ids=[c["id"] for c in RAW_CASES])
def test_load_cases_real_case_has_every_field(raw: dict[str, Any]) -> None:
    assert set(raw) == REQUIRED_FIELDS


# T1-T4 are the graded demo: the text must match REQUIREMENTS §9.1 exactly.
PRIMARY_TEXT = {
    "T1": (
        "Hi, I'm Marcus (E1002). I'd like a second monitor for my desk, I'm juggling a lot of "
        "windows."
    ),
    "T2": "This is Priya, E1001. My laptop feels slow lately, can I get a new one?",
    "T3": (
        "Dana Okafor, E1003. My work phone was stolen from my car last night and I need a "
        "replacement ASAP."
    ),
    "T4": (
        "I'm Alex, E1005. My doctor recommended a standing desk and an ergonomic keyboard for "
        "my back pain."
    ),
}


@pytest.mark.parametrize(("case_id", "text"), PRIMARY_TEXT.items(), ids=list(PRIMARY_TEXT))
def test_load_cases_primary_four_text_is_word_for_word(case_id: str, text: str) -> None:
    assert REAL_CASES[case_id].text == text


@pytest.mark.parametrize(
    ("case_id", "decision", "code"),
    [
        ("T1", "approve", "R-NEW"),
        ("T2", "deny", "R-REFRESH"),
        ("T3", "escalate", "E6"),
        ("T4", "escalate", "E9"),
        ("T5", "approve", "R-REFRESH-OK"),
        ("T6", "deny", "R-ROLE"),
        ("T7", "escalate", "E1"),
        ("T8", "escalate", "E2"),
        ("T9", "escalate", "E7"),
        ("T10", "escalate", "E8"),
        ("T11", "escalate", "E5"),
        ("T12", "escalate", "E4"),
    ],
    ids=[f"T{n}" for n in range(1, 13)],
)
def test_load_cases_expected_outcomes_match_requirements_section_9(
    case_id: str, decision: str, code: str
) -> None:
    case = REAL_CASES[case_id]

    assert (case.expected_decision, case.expected_code) == (decision, code)


LOOKUPS = ("get_employee_info", "check_request_eligibility")


@pytest.mark.parametrize(
    ("case_id", "tools"),
    [
        ("T1", LOOKUPS),
        ("T2", (*LOOKUPS, "get_policy_limits")),
        ("T9", (*LOOKUPS, "flag_for_human_review")),  # row 3, E7
        ("T3", ("flag_for_human_review",)),  # row 1
    ],
    ids=["T1-approve", "T2-deny", "T9-e7", "T3-escalate"],
)
def test_load_cases_required_tools_follow_the_decision_table(
    case_id: str, tools: tuple[str, ...]
) -> None:
    assert REAL_CASES[case_id].required_tools == tools


# ---------- load_cases failures ----------


def _write(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"version": "test", "cases": cases}), encoding="utf-8")
    return path


def _case(**overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": "T1",
        "request_id": "REQ-0001",
        "text": "E1002 wants a monitor.",
        "expected_decision": "approve",
        "expected_code": "R-NEW",
        "required_tools": ["get_employee_info", "check_request_eligibility"],
    }
    return case | overrides


@pytest.mark.parametrize("missing", sorted(REQUIRED_FIELDS), ids=str)
def test_load_cases_missing_field_raises_naming_it(tmp_path: Path, missing: str) -> None:
    case = _case()
    del case[missing]

    with pytest.raises(CaseFileError, match=missing):
        load_cases(_write(tmp_path, [case]))


def test_load_cases_duplicate_id_raises(tmp_path: Path) -> None:
    cases = [_case(), _case(request_id="REQ-0002")]

    with pytest.raises(CaseFileError, match="duplicate.*T1"):
        load_cases(_write(tmp_path, cases))


@pytest.mark.parametrize(
    "bad",
    [
        {"expected_decision": "maybe"},
        {"required_tools": ["final_answer"]},
        {"surprise": "field"},
    ],
    ids=["unknown-decision", "final-answer-as-tool", "extra-field"],
)
def test_load_cases_invalid_case_raises(tmp_path: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(CaseFileError):
        load_cases(_write(tmp_path, [_case(**bad)]))


def test_load_cases_unreadable_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CaseFileError):
        load_cases(path)


# ---------- grade ----------

EMPLOYEE = {"employee_id": "E1002", "role": "engineer", "status": "active"}
ELIGIBLE = {"status": "ELIGIBLE", "rule_id": "R-NEW"}
NOT_ELIGIBLE = {"status": "NOT_ELIGIBLE", "rule_id": "R-REFRESH"}
LIMITS = {"role": "standard", "limits": {}}
FLAG = {"review_id": "REV-0001", "status": "pending", "reason_code": "E6"}


def _step(tool: str, observation: dict[str, Any]) -> Step:
    return Step(thought="t", tool=tool, arguments={}, observation=observation)


def _result(final: FinalAnswer, steps: list[Step], *, fallback: bool = False) -> AgentResult:
    return AgentResult(
        request_id="REQ-0001",
        run_id="run-1",
        steps=tuple(steps),
        final=final,
        guard_events=(),
        fallback=fallback,
        cost_usd=0.01,
        input_tokens=1000,
        output_tokens=100,
    )


def _make_case(decision: str, code: str, tools: tuple[str, ...]) -> Case:
    return Case(
        id="T1",
        request_id="REQ-0001",
        text="x",
        expected_decision=decision,  # type: ignore[arg-type]
        expected_code=code,
        required_tools=tools,  # type: ignore[arg-type]
    )


APPROVE_CASE = _make_case("approve", "R-NEW", ("get_employee_info", "check_request_eligibility"))
DENY_CASE = _make_case(
    "deny", "R-REFRESH", ("get_employee_info", "check_request_eligibility", "get_policy_limits")
)
ESCALATE_CASE = _make_case("escalate", "E6", ("flag_for_human_review",))

APPROVE_STEPS = [_step("get_employee_info", EMPLOYEE), _step("check_request_eligibility", ELIGIBLE)]
DENY_STEPS = [
    _step("get_employee_info", EMPLOYEE),
    _step("check_request_eligibility", NOT_ELIGIBLE),
    _step("get_policy_limits", LIMITS),
]
ESCALATE_STEPS = [_step("flag_for_human_review", FLAG)]


def _approve(rule_id: str = "R-NEW", reply: str = "Approved.") -> FinalAnswer:
    return FinalAnswer(decision="approve", rule_id=rule_id, draft_reply=reply)


def _deny(rule_id: str = "R-REFRESH", reply: str = "Denied under R-REFRESH.") -> FinalAnswer:
    return FinalAnswer(decision="deny", rule_id=rule_id, draft_reply=reply)


def _escalate(
    code: str = "E6", review_id: str = "REV-0001", reply: str = "Reference REV-0001."
) -> FinalAnswer:
    return FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id=review_id,
        reason_code=code,
        draft_reply=reply,
    )


def _flags(g: Grade) -> tuple[bool, bool, bool, bool, bool]:
    return g.decision_ok, g.code_ok, g.tools_ok, g.reply_ok, g.passed


def test_grade_matching_approve_passes_every_check() -> None:
    g = grade(APPROVE_CASE, _result(_approve(), APPROVE_STEPS))

    assert _flags(g) == (True, True, True, True, True)


def test_grade_keeps_the_case_id() -> None:
    assert grade(APPROVE_CASE, _result(_approve(), APPROVE_STEPS)).case_id == "T1"


def test_grade_matching_deny_passes() -> None:
    assert grade(DENY_CASE, _result(_deny(), DENY_STEPS)).passed


def test_grade_matching_escalate_passes() -> None:
    assert grade(ESCALATE_CASE, _result(_escalate(), ESCALATE_STEPS)).passed


def test_grade_wrong_decision_fails_decision() -> None:
    g = grade(APPROVE_CASE, _result(_deny(rule_id="R-NEW", reply="R-NEW"), APPROVE_STEPS))

    assert _flags(g) == (False, True, True, True, False)


def test_grade_wrong_rule_fails_code() -> None:
    g = grade(APPROVE_CASE, _result(_approve(rule_id="R-REFRESH-OK"), APPROVE_STEPS))

    assert _flags(g) == (True, False, True, True, False)


def test_grade_escalate_code_comes_from_reason_code() -> None:
    g = grade(ESCALATE_CASE, _result(_escalate(code="E11"), ESCALATE_STEPS))

    assert _flags(g) == (True, False, True, True, False)


def test_grade_missing_required_tool_fails_tools() -> None:
    g = grade(DENY_CASE, _result(_deny(), DENY_STEPS[:2]))

    assert _flags(g) == (True, True, False, True, False)


def test_grade_errored_tool_call_does_not_count_as_required_call() -> None:
    steps = [_step("get_employee_info", {"error": "EMPLOYEE_NOT_FOUND"}), APPROVE_STEPS[1]]

    assert not grade(APPROVE_CASE, _result(_approve(), steps)).tools_ok


def test_grade_deny_without_rule_in_reply_fails_reply() -> None:
    g = grade(DENY_CASE, _result(_deny(reply="Not due yet."), DENY_STEPS))

    assert _flags(g) == (True, True, True, False, False)


def test_grade_deny_citing_only_a_longer_rule_fails_reply() -> None:
    # "R-REFRESH" inside "R-REFRESH-OK" is not a citation of R-REFRESH.
    g = grade(DENY_CASE, _result(_deny(reply="See R-REFRESH-OK."), DENY_STEPS))

    assert not g.reply_ok


def test_grade_escalate_without_review_id_in_reply_fails_reply() -> None:
    g = grade(ESCALATE_CASE, _result(_escalate(reply="A reviewer will follow up."), ESCALATE_STEPS))

    assert _flags(g) == (True, True, True, False, False)


def test_grade_escalate_with_no_review_id_fails_reply() -> None:
    final = FinalAnswer(decision="escalate", rule_id=None, reason_code="E6", draft_reply="x")

    assert not grade(ESCALATE_CASE, _result(final, ESCALATE_STEPS)).reply_ok


def test_grade_approve_reply_has_no_citation_requirement() -> None:
    assert grade(APPROVE_CASE, _result(_approve(reply="Approved."), APPROVE_STEPS)).reply_ok


E11_CASE = _make_case("escalate", "E11", ("flag_for_human_review",))


def _fallback_grade() -> Grade:
    return grade(E11_CASE, _result(_escalate(code="E11"), ESCALATE_STEPS, fallback=True))


def test_grade_fallback_matching_every_check_still_fails() -> None:
    # Even when every check happens to match: a fallback means the model didn't finish.
    assert _flags(_fallback_grade()) == (True, True, True, True, False)


def test_grade_fallback_is_marked_as_fallback() -> None:
    assert _fallback_grade().fallback is True


# ---------- summarise and format_summary ----------


def _grade(case_id: str, passed: bool) -> Grade:
    return Grade(
        case_id=case_id,
        decision_ok=passed,
        code_ok=passed,
        tools_ok=passed,
        reply_ok=passed,
        fallback=False,
        passed=passed,
    )


THREE_ROUNDS = [
    _grade("T1", True),
    _grade("T2", True),
    _grade("T1", True),
    _grade("T2", False),
    _grade("T1", True),
    _grade("T2", True),
]


def test_summarise_counts_passes_per_case_in_order() -> None:
    assert summarise(THREE_ROUNDS).per_case == {"T1": "3/3", "T2": "2/3"}


def test_summarise_counts_overall_passes_and_total() -> None:
    summary = summarise(THREE_ROUNDS)

    assert (summary.passed, summary.total) == (5, 6)


def test_summarise_pass_rate_is_passed_over_total() -> None:
    assert summarise(THREE_ROUNDS).pass_rate == pytest.approx(5 / 6)


def test_summarise_no_grades_gives_zeros() -> None:
    summary = summarise([])

    assert (summary.per_case, summary.passed, summary.total, summary.pass_rate) == ({}, 0, 0, 0.0)


def test_format_summary_lists_header_then_one_row_per_case() -> None:
    text = format_summary(summarise([_grade("T1", True), _grade("T2", False)]))

    assert [line.split() for line in text.splitlines()[:3]] == [
        ["Case", "Passed"],
        ["T1", "1/1"],
        ["T2", "0/1"],
    ]


def test_format_summary_ends_with_overall_line() -> None:
    text = format_summary(summarise([_grade("T1", True), _grade("T2", False)]))

    assert text.splitlines()[-1] == "Overall: 1/2 (50%)"


# ---------- resolve_model and estimate_usd ----------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("haiku", config.DEV_MODEL),
        ("sonnet", config.EVAL_MODEL),
        ("Sonnet", config.EVAL_MODEL),
        (config.DEV_MODEL, config.DEV_MODEL),
        (config.EVAL_MODEL, config.EVAL_MODEL),
    ],
    ids=["haiku", "sonnet", "capitalised", "dev-model-id", "eval-model-id"],
)
def test_resolve_model_maps_alias_or_id_to_model_id(given: str, expected: str) -> None:
    assert resolve_model(given) == expected


@pytest.mark.parametrize("given", ["opus", "claude-opus-5-5", "gpt-4"], ids=str)
def test_resolve_model_rejects_others(given: str) -> None:
    # Opus is never used for the agent (CLAUDE.md budget rule).
    with pytest.raises(ValueError, match="model"):
        resolve_model(given)


# PLAN §4: about $0.085 per Sonnet run and $0.004 per Haiku run, margin included.
@pytest.mark.parametrize(
    ("model", "expected"),
    [(config.EVAL_MODEL, 1.02), (config.DEV_MODEL, 0.048)],
    ids=["sonnet", "haiku"],
)
def test_estimate_usd_uses_plan_per_run_figures(model: str, expected: float) -> None:
    assert estimate_usd(model, runs=12) == pytest.approx(expected)

"""Tests for the deterministic half of reflection (REQUIREMENTS §6.1 C4, §11; PLAN §2).

Observations come from the real pure tool functions, as in test_guards.py, so the date
search runs over the exact JSON the server returns.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from it_requests import config
from it_requests.agent.guards import FinalAnswer, Observation
from it_requests.agent.reflection import (
    CODE_FINDINGS,
    REFLECTION_TOOL,
    ReflectionVerdict,
    code_findings,
    parse_verdict,
    remove_sentences_with,
    unsupported_dates,
)
from it_requests.data import load_employees, load_policy
from it_requests.eligibility import check_request_eligibility
from it_requests.employees import get_employee_info
from it_requests.reviews import flag_for_human_review

DIRECTORY = load_employees(config.EMPLOYEES_FILE)
POLICY = load_policy(config.POLICY_FILE)
FIXED_NOW = datetime(2026, 11, 22, 9, 0, 0)


def _emp(employee_id: str) -> Observation:
    result = get_employee_info(employee_id, directory=DIRECTORY)
    return Observation("get_employee_info", {"employee_id": employee_id}, result)


def _elig(employee_id: str, item: str) -> Observation:
    result = check_request_eligibility(employee_id, item, directory=DIRECTORY, policy=POLICY)
    return Observation(
        "check_request_eligibility", {"employee_id": employee_id, "item": item}, result
    )


def _flag(queue_path: Path) -> Observation:
    arguments = {"employee_id": "E1003", "request": "Phone stolen.", "reason": "E6: Stolen."}
    result = flag_for_human_review(
        "E1003", "Phone stolen.", "E6: Stolen.", queue_path=queue_path, now=FIXED_NOW
    )
    return Observation("flag_for_human_review", arguments, result)


# E1001 laptop: NOT_ELIGIBLE under R-REFRESH, next_eligible_date 2028-03-10 (§8). The
# employee record also holds 2025-03-10 (the laptop's issue date) and 2021-06-14 (hire date).
DENY_OBSERVATIONS = [_emp("E1001"), _elig("E1001", "laptop")]
# E1001 mobile_phone: NOT_ELIGIBLE under R-ROLE, so next_eligible_date is null.
ROLE_DENY_OBSERVATIONS = [_emp("E1001"), _elig("E1001", "mobile_phone")]
CLEAN_DENY_REPLY = "Your laptop is due for refresh on 2028-03-10 (rule R-REFRESH)."
INVENTED_DATE = "2027-01-01"  # in no tool result for E1001


def _deny(reply: str, rule_id: str = "R-REFRESH") -> FinalAnswer:
    return FinalAnswer(decision="deny", rule_id=rule_id, draft_reply=reply)


def _escalate(reply: str, review_id: str | None = "REV-0001") -> FinalAnswer:
    return FinalAnswer(
        decision="escalate", rule_id=None, review_id=review_id, reason_code="E6", draft_reply=reply
    )


@pytest.fixture
def flag_observations(tmp_path: Path) -> list[Observation]:
    # A fresh queue per test, so the review ID is REV-0001.
    return [_flag(tmp_path / "queue.json")]


# ---------- CODE_FINDINGS ----------


def test_code_findings_constant_lists_codes_in_fixed_order() -> None:
    assert CODE_FINDINGS == (
        "C4_UNSUPPORTED_DATE",
        "DENY_MISSING_NEXT_DATE",
        "ESCALATE_MISSING_REVIEW_ID",
    )


# ---------- code_findings: clean drafts ----------


def test_code_findings_clean_deny_returns_empty() -> None:
    assert code_findings(_deny(CLEAN_DENY_REPLY), DENY_OBSERVATIONS) == []


def test_code_findings_clean_escalation_returns_empty(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("Sent for review, reference REV-0001.")

    assert code_findings(answer, flag_observations) == []


def test_code_findings_approve_without_dates_returns_empty() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW", draft_reply="Approved (R-NEW).")

    assert code_findings(answer, [_emp("E1002"), _elig("E1002", "monitor")]) == []


# ---------- code_findings: C4_UNSUPPORTED_DATE ----------


def test_code_findings_invented_date_is_flagged() -> None:
    reply = f"{CLEAN_DENY_REPLY} A new one will arrive by {INVENTED_DATE}."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == ["C4_UNSUPPORTED_DATE"]


def test_code_findings_date_from_nested_observation_is_not_flagged() -> None:
    # 2025-03-10 is the laptop's issued_date, nested in the employee's equipment list.
    reply = f"{CLEAN_DENY_REPLY} Your laptop was issued on 2025-03-10."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == []


def test_code_findings_date_only_in_a_failed_observation_is_flagged() -> None:
    # Hand-built: an error result is not data, so a date inside it supports nothing.
    failed = Observation("check_request_eligibility", {}, {"error": "X", "as_of": "2026-11-22"})
    reply = f"{CLEAN_DENY_REPLY} Checked on 2026-11-22."

    assert code_findings(_deny(reply), [*DENY_OBSERVATIONS, failed]) == ["C4_UNSUPPORTED_DATE"]


def test_code_findings_no_reply_returns_empty_for_c4() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW", draft_reply=None)

    assert code_findings(answer, [_emp("E1002"), _elig("E1002", "monitor")]) == []


# ---------- code_findings: DENY_MISSING_NEXT_DATE ----------


def test_code_findings_deny_without_next_eligible_date_is_flagged() -> None:
    reply = "Your laptop is not due for refresh yet (rule R-REFRESH)."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == ["DENY_MISSING_NEXT_DATE"]


def test_code_findings_deny_with_null_next_date_is_not_flagged() -> None:
    reply = "Your role is not entitled to a phone (rule R-ROLE)."

    assert code_findings(_deny(reply, "R-ROLE"), ROLE_DENY_OBSERVATIONS) == []


def test_code_findings_deny_uses_the_latest_eligibility_check() -> None:
    # The newest check (mobile_phone, R-ROLE) has no next date, so none is required.
    observations = [*DENY_OBSERVATIONS, _elig("E1001", "mobile_phone")]
    reply = "Your role is not entitled to a phone (rule R-ROLE)."

    assert code_findings(_deny(reply, "R-ROLE"), observations) == []


# ---------- code_findings: ESCALATE_MISSING_REVIEW_ID ----------


def test_code_findings_escalation_without_review_id_in_reply_is_flagged(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("A reviewer will follow up.")

    assert code_findings(answer, flag_observations) == ["ESCALATE_MISSING_REVIEW_ID"]


def test_code_findings_escalation_with_null_review_id_is_flagged(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("A reviewer will follow up.", review_id=None)

    assert code_findings(answer, flag_observations) == ["ESCALATE_MISSING_REVIEW_ID"]


# ---------- code_findings: several at once ----------


def test_code_findings_several_findings_come_in_fixed_order() -> None:
    reply = f"Not due yet (rule R-REFRESH). A new one will arrive by {INVENTED_DATE}."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == [
        "C4_UNSUPPORTED_DATE",
        "DENY_MISSING_NEXT_DATE",
    ]


# ---------- unsupported_dates ----------


def test_unsupported_dates_reports_each_once_in_order() -> None:
    reply = "Ships 2027-02-02, arrives 2027-01-01, confirmed 2027-02-02."

    assert unsupported_dates(reply, DENY_OBSERVATIONS) == ["2027-02-02", "2027-01-01"]


def test_unsupported_dates_supported_date_is_omitted() -> None:
    reply = "Due 2028-03-10, ships 2027-01-01."

    assert unsupported_dates(reply, DENY_OBSERVATIONS) == ["2027-01-01"]


def test_unsupported_dates_empty_reply_returns_empty() -> None:
    assert unsupported_dates("", DENY_OBSERVATIONS) == []


def test_unsupported_dates_no_observations_flags_every_date() -> None:
    assert unsupported_dates("Due 2028-03-10.", []) == ["2028-03-10"]


# ---------- remove_sentences_with ----------


def test_remove_sentences_with_drops_the_sentence_holding_the_token() -> None:
    reply = "Denied (R-REFRESH). It ships by 2027-01-01. Ask the helpdesk."

    assert remove_sentences_with(reply, ["2027-01-01"]) == "Denied (R-REFRESH). Ask the helpdesk."


def test_remove_sentences_with_drops_the_last_sentence_cleanly() -> None:
    assert remove_sentences_with("Denied. Ships 2027-01-01.", ["2027-01-01"]) == "Denied."


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Approved! Ships 2027-01-01! Thanks.", "Approved! Thanks."),
        ("Approved? Ships 2027-01-01? Thanks.", "Approved? Thanks."),
        ("Line one\nShips 2027-01-01\nLine three", "Line one\nLine three"),
    ],
    ids=["exclamation", "question", "newline"],
)
def test_remove_sentences_with_splits_on_every_boundary(reply: str, expected: str) -> None:
    assert remove_sentences_with(reply, ["2027-01-01"]) == expected


def test_remove_sentences_with_keeps_decimal_points_inside_a_sentence() -> None:
    # Only ". " ends a sentence, so "$1.50" stays whole.
    reply = "The fee is $1.50 today. Ships 2027-01-01."

    assert remove_sentences_with(reply, ["2027-01-01"]) == "The fee is $1.50 today."


def test_remove_sentences_with_several_tokens_drops_each_sentence() -> None:
    reply = "A. Ships 2027-01-01. B. Arrives 2027-02-02. C."

    assert remove_sentences_with(reply, ["2027-01-01", "2027-02-02"]) == "A. B. C."


def test_remove_sentences_with_absent_token_returns_reply_stripped() -> None:
    assert remove_sentences_with("  Denied. Ask the helpdesk.  ", ["2027-01-01"]) == (
        "Denied. Ask the helpdesk."
    )


def test_remove_sentences_with_no_tokens_returns_reply_stripped() -> None:
    assert remove_sentences_with(" Denied. ", []) == "Denied."


def test_remove_sentences_with_every_sentence_removed_returns_empty() -> None:
    assert remove_sentences_with("Ships 2027-01-01.", ["2027-01-01"]) == ""


# ---------- REFLECTION_TOOL ----------

VERDICT_PROPERTIES = ["thought", "answers", "verdict", "revised_reply", "what_changed"]
QUESTIONS = [f"q{n}" for n in range(1, 7)]
SCHEMA = REFLECTION_TOOL["input_schema"]
# The six §11 answers: each a required boolean, nothing else allowed.
ANSWERS_SCHEMA = {
    "type": "object",
    "properties": {q: {"type": "boolean"} for q in QUESTIONS},
    "required": QUESTIONS,
    "additionalProperties": False,
}


def test_reflection_tool_is_named_reflection_verdict() -> None:
    assert REFLECTION_TOOL["name"] == "reflection_verdict"


def test_reflection_tool_is_strict() -> None:
    assert REFLECTION_TOOL["strict"] is True


def test_reflection_tool_forbids_additional_properties() -> None:
    assert SCHEMA["additionalProperties"] is False


def test_reflection_tool_requires_every_property_in_order() -> None:
    assert SCHEMA["required"] == VERDICT_PROPERTIES


def test_reflection_tool_lists_properties_in_order() -> None:
    assert list(SCHEMA["properties"]) == VERDICT_PROPERTIES


def test_reflection_tool_answers_are_six_required_booleans() -> None:
    assert SCHEMA["properties"]["answers"] == ANSWERS_SCHEMA


def test_reflection_tool_verdict_is_confirmed_or_revised() -> None:
    assert SCHEMA["properties"]["verdict"] == {
        "type": "string",
        "enum": ["CONFIRMED", "REVISED"],
    }


@pytest.mark.parametrize("field", ["revised_reply", "what_changed"], ids=str)
def test_reflection_tool_optional_text_is_string_or_null(field: str) -> None:
    assert SCHEMA["properties"][field] == {"anyOf": [{"type": "string"}, {"type": "null"}]}


# ---------- parse_verdict ----------

ALL_YES = {q: True for q in QUESTIONS}
CONFIRMED_INPUT: dict[str, Any] = {
    "thought": "Every answer is yes.",
    "answers": ALL_YES,
    "verdict": "CONFIRMED",
    "revised_reply": None,
    "what_changed": None,
}
REVISED_INPUT: dict[str, Any] = {
    "thought": "The draft promised a delivery date.",
    "answers": ALL_YES | {"q3": False},
    "verdict": "REVISED",
    "revised_reply": "Approved (R-NEW). IT will arrange fulfilment.",
    "what_changed": "Removed the promised delivery date.",
}


def test_parse_verdict_confirmed_builds_the_verdict() -> None:
    assert parse_verdict(CONFIRMED_INPUT) == ReflectionVerdict(
        thought="Every answer is yes.",
        answers=ALL_YES,
        verdict="CONFIRMED",
        revised_reply=None,
        what_changed=None,
    )


def test_parse_verdict_revised_keeps_the_revised_reply() -> None:
    assert parse_verdict(REVISED_INPUT).revised_reply == (
        "Approved (R-NEW). IT will arrange fulfilment."
    )


def test_parse_verdict_revised_keeps_the_answers() -> None:
    assert parse_verdict(REVISED_INPUT).answers == ALL_YES | {"q3": False}


def test_parse_verdict_does_not_mutate_input() -> None:
    before = {**REVISED_INPUT, "answers": dict(REVISED_INPUT["answers"])}

    parse_verdict(REVISED_INPUT)

    assert REVISED_INPUT == before


@pytest.mark.parametrize(
    "bad",
    [
        CONFIRMED_INPUT | {"verdict": "MAYBE"},
        {k: v for k, v in CONFIRMED_INPUT.items() if k != "verdict"},
        CONFIRMED_INPUT | {"thought": 7},
        CONFIRMED_INPUT | {"answers": "all yes"},
        CONFIRMED_INPUT | {"answers": {q: True for q in QUESTIONS[:5]}},
        CONFIRMED_INPUT | {"answers": ALL_YES | {"q7": True}},
        CONFIRMED_INPUT | {"answers": ALL_YES | {"q1": "yes"}},
        REVISED_INPUT | {"revised_reply": None},
        REVISED_INPUT | {"revised_reply": ""},
        REVISED_INPUT | {"revised_reply": "   "},
        REVISED_INPUT | {"revised_reply": 3},
        REVISED_INPUT | {"what_changed": 3},
        CONFIRMED_INPUT | {"revised_reply": "A different reply."},
    ],
    ids=[
        "unknown-verdict",
        "missing-verdict",
        "thought-not-string",
        "answers-not-object",
        "answers-missing-q6",
        "answers-extra-q7",
        "answer-not-boolean",
        "revised-with-null-reply",
        "revised-with-empty-reply",
        "revised-with-blank-reply",
        "revised-reply-not-string",
        "what-changed-not-string",
        "confirmed-with-revised-reply",
    ],
)
def test_parse_verdict_malformed_input_raises(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_verdict(bad)

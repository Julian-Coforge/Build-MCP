"""Tests for the system prompts: ReAct (REQUIREMENTS §5, §6, §6.1, §10) and reflection (§11)."""

import re

import pytest

from it_requests.agent.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT_REACT,
    SYSTEM_PROMPT_REFLECTION,
    SYSTEM_PROMPT_SINGLE_CALL,
)

TRIGGERS = {
    "E1": "EMPLOYEE_NOT_FOUND",
    "E2": "EMPLOYEE_NOT_ACTIVE",
    "E3": "NON_CATALOG_ITEM",
    "E4": "ITEM_UNCLEAR",
    "E5": "MULTIPLE_ITEMS",
    "E6": "LOST_OR_STOLEN",
    "E7": "DAMAGE_OUTSIDE_CYCLE",
    "E8": "NON_STANDARD_SPEC",
    "E9": "ACCOMMODATION",
    "E10": "IDENTITY_MISMATCH",
    "E11": "TOOL_FAILURE_OR_CONFLICT",
}


def test_prompt_version_is_m7_for_reflection() -> None:
    assert PROMPT_VERSION == "m7-v1"


def test_single_call_prompt_is_kept_for_m5() -> None:
    assert "exactly ONE" in SYSTEM_PROMPT_SINGLE_CALL


@pytest.mark.parametrize("code", sorted(TRIGGERS), ids=str)
def test_react_prompt_names_every_escalation_code(code: str) -> None:
    # Whole-token match: "E1" must be found on its own, not only inside "E10" or "E11".
    assert re.search(rf"\b{code}\b", SYSTEM_PROMPT_REACT)


@pytest.mark.parametrize("trigger", sorted(TRIGGERS.values()), ids=str)
def test_react_prompt_names_every_escalation_trigger(trigger: str) -> None:
    assert trigger in SYSTEM_PROMPT_REACT


@pytest.mark.parametrize("constraint", [f"C{n}" for n in range(1, 8)], ids=str)
def test_react_prompt_names_every_constraint(constraint: str) -> None:
    assert re.search(rf"\b{constraint}\b", SYSTEM_PROMPT_REACT)


@pytest.mark.parametrize(
    "name",
    ["final_answer", "flag_for_human_review", "check_request_eligibility", "thought"],
    ids=str,
)
def test_react_prompt_names_the_tools_the_method_depends_on(name: str) -> None:
    assert name in SYSTEM_PROMPT_REACT


def test_react_prompt_says_first_matching_row_wins() -> None:
    assert "first matching row" in SYSTEM_PROMPT_REACT.lower()


def test_react_prompt_shows_reason_starting_with_code() -> None:
    assert '"E6: ' in SYSTEM_PROMPT_REACT  # reason must start with the code


@pytest.mark.parametrize(
    "phrase",
    ["performance complaint", "eligible", "damage"],
    ids=["performance", "eligible", "damage"],
)
def test_react_prompt_includes_the_two_non_triggers(phrase: str) -> None:
    assert phrase in SYSTEM_PROMPT_REACT.lower()


@pytest.mark.parametrize(
    "phrase", ["replacement", "asset_id", "next_eligible_date", "review_id"], ids=str
)
def test_react_prompt_includes_reply_rules(phrase: str) -> None:
    assert phrase in SYSTEM_PROMPT_REACT.lower()


# Identical across runs, so it can be cached: no employee or review IDs, request IDs,
# dates or names from the mock data.
@pytest.mark.parametrize(
    "pattern",
    [r"\bE[0-9]{4}\b", r"\bREV-[0-9]+", r"\bREQ-[0-9]+", r"[0-9]{4}-[0-9]{2}-[0-9]{2}"],
    ids=["employee-id", "review-id", "request-id", "date"],
)
def test_react_prompt_has_no_per_request_ids_or_dates(pattern: str) -> None:
    assert re.search(pattern, SYSTEM_PROMPT_REACT) is None


@pytest.mark.parametrize("name", ["Priya", "Marcus", "Dana", "Sam", "Alex", "Jordan"], ids=str)
def test_react_prompt_has_no_employee_names(name: str) -> None:
    assert name not in SYSTEM_PROMPT_REACT


# ---------- reflection prompt ----------

# REQUIREMENTS §11, word for word: the model answers exactly the written checklist.
SECTION_11_QUESTIONS = [
    "Does the decision match the first matching row of the decision table (Section 6)?",
    "Does every date, count, and entitlement in the draft appear in a tool observation from "
    "this run? (C4)",
    "Does the draft avoid promises of delivery dates, specific models, or review outcomes? (C5)",
    "For a denial, is the rule ID cited, and is `next_eligible_date` included when it exists?",
    "For an escalation, was `flag_for_human_review` actually called, and is its `review_id` in "
    "the draft?",
    "Does the draft answer the employee's actual request, including every item they mentioned?",
]


@pytest.mark.parametrize("question", SECTION_11_QUESTIONS, ids=[f"q{n}" for n in range(1, 7)])
def test_reflection_prompt_holds_each_section_11_question(question: str) -> None:
    assert question in SYSTEM_PROMPT_REFLECTION


@pytest.mark.parametrize("constraint", ["C4", "C5"], ids=str)
def test_reflection_prompt_states_the_reply_constraints(constraint: str) -> None:
    assert re.search(rf"\b{constraint}\b", SYSTEM_PROMPT_REFLECTION)


def test_reflection_prompt_names_the_verdict_tool() -> None:
    assert "call reflection_verdict exactly once" in SYSTEM_PROMPT_REFLECTION


def test_reflection_prompt_forbids_changing_the_decision() -> None:
    assert (
        "never change the decision, rule ID, review ID or reason code" in SYSTEM_PROMPT_REFLECTION
    )


def test_reflection_prompt_limits_edits_to_the_reply_text() -> None:
    assert "you may only rewrite the reply text" in SYSTEM_PROMPT_REFLECTION.lower()


def test_reflection_prompt_requires_code_findings_fixed() -> None:
    assert "any code findings you are given must be fixed" in SYSTEM_PROMPT_REFLECTION.lower()


@pytest.mark.parametrize(
    "pattern",
    [r"\bE[0-9]{4}\b", r"\bREV-[0-9]+", r"\bREQ-[0-9]+", r"[0-9]{4}-[0-9]{2}-[0-9]{2}"],
    ids=["employee-id", "review-id", "request-id", "date"],
)
def test_reflection_prompt_has_no_per_request_ids_or_dates(pattern: str) -> None:
    assert re.search(pattern, SYSTEM_PROMPT_REFLECTION) is None


@pytest.mark.parametrize("name", ["Priya", "Marcus", "Dana", "Sam", "Alex", "Jordan"], ids=str)
def test_reflection_prompt_has_no_employee_names(name: str) -> None:
    assert name not in SYSTEM_PROMPT_REFLECTION

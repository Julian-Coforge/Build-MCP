"""Tests for the ReAct system prompt (REQUIREMENTS §5, §6, §6.1, §10; PLAN §2)."""

import re

import pytest

from it_requests.agent.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT_REACT,
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


def test_prompt_version_is_m6() -> None:
    assert PROMPT_VERSION == "m6-v1"


def test_single_call_prompt_is_kept_for_m5() -> None:
    assert "exactly ONE" in SYSTEM_PROMPT_SINGLE_CALL


@pytest.mark.parametrize(("code", "trigger"), sorted(TRIGGERS.items()))
def test_every_escalation_code_and_trigger_is_present(code: str, trigger: str) -> None:
    # Whole-token match: "E1" must be found on its own, not only inside "E10" or "E11".
    assert re.search(rf"\b{code}\b", SYSTEM_PROMPT_REACT)
    assert trigger in SYSTEM_PROMPT_REACT


@pytest.mark.parametrize("constraint", [f"C{n}" for n in range(1, 8)])
def test_every_constraint_is_present(constraint: str) -> None:
    assert re.search(rf"\b{constraint}\b", SYSTEM_PROMPT_REACT)


def test_names_the_tools_the_method_depends_on() -> None:
    for name in ("final_answer", "flag_for_human_review", "check_request_eligibility"):
        assert name in SYSTEM_PROMPT_REACT
    assert "thought" in SYSTEM_PROMPT_REACT


def test_includes_decision_table_order_and_reason_format() -> None:
    assert "first matching row" in SYSTEM_PROMPT_REACT.lower()
    assert '"E6: ' in SYSTEM_PROMPT_REACT  # reason must start with the code


def test_includes_the_two_non_triggers() -> None:
    lowered = SYSTEM_PROMPT_REACT.lower()
    assert "performance complaint" in lowered
    assert "eligible" in lowered and "damage" in lowered


def test_includes_reply_rules() -> None:
    lowered = SYSTEM_PROMPT_REACT.lower()
    for phrase in ("replacement", "asset_id", "next_eligible_date", "review_id"):
        assert phrase in lowered


def test_has_no_per_request_content() -> None:
    # Identical across runs, so it can be cached: no employee or review IDs, request IDs,
    # dates or names from the mock data.
    assert not re.search(r"\bE[0-9]{4}\b", SYSTEM_PROMPT_REACT)
    assert not re.search(r"\bREV-[0-9]+", SYSTEM_PROMPT_REACT)
    assert not re.search(r"\bREQ-[0-9]+", SYSTEM_PROMPT_REACT)
    assert not re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", SYSTEM_PROMPT_REACT)
    for name in ("Priya", "Marcus", "Dana", "Sam", "Alex", "Jordan"):
        assert name not in SYSTEM_PROMPT_REACT

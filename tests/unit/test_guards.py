"""Tests for the agent's constraint guards (REQUIREMENTS §5, §6, §6.1 C1-C3, C6, C7; PLAN §2).

Every observation is built by calling the real pure tool functions, so the guards are tested
against the exact result shapes the server returns, never hand-typed copies.
"""

import dataclasses
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from it_requests import config
from it_requests.agent.guards import (
    INVESTIGATION_TOOLS,
    MAX_INVESTIGATION_CALLS,
    FinalAnswer,
    Observation,
    check_final_answer,
    check_tool_call,
    violation_message,
)
from it_requests.data import EmployeeDirectory, PolicyTable, load_employees, load_policy
from it_requests.eligibility import check_request_eligibility
from it_requests.employees import get_employee_info
from it_requests.policy import get_policy_limits
from it_requests.reviews import flag_for_human_review

FIXED_NOW = datetime(2026, 11, 22, 9, 0, 0)

Flag = Callable[..., Observation]


@pytest.fixture(scope="module")
def directory() -> EmployeeDirectory:
    return load_employees(config.EMPLOYEES_FILE)


@pytest.fixture(scope="module")
def policy() -> PolicyTable:
    return load_policy(config.POLICY_FILE)


@pytest.fixture(scope="module")
def emp(directory: EmployeeDirectory) -> Callable[[str], Observation]:
    def _emp(employee_id: str) -> Observation:
        result = get_employee_info(employee_id, directory=directory)
        return Observation("get_employee_info", {"employee_id": employee_id}, result)

    return _emp


@pytest.fixture(scope="module")
def limits(policy: PolicyTable) -> Callable[[str], Observation]:
    def _limits(role: str) -> Observation:
        return Observation(
            "get_policy_limits", {"role": role}, get_policy_limits(role, policy=policy)
        )

    return _limits


@pytest.fixture(scope="module")
def elig(directory: EmployeeDirectory, policy: PolicyTable) -> Callable[[str, str], Observation]:
    def _elig(employee_id: str, item: str) -> Observation:
        result = check_request_eligibility(employee_id, item, directory=directory, policy=policy)
        return Observation(
            "check_request_eligibility", {"employee_id": employee_id, "item": item}, result
        )

    return _elig


@pytest.fixture
def flag(tmp_path: Path) -> Flag:
    # A fresh queue per test (never data/review_queue.json), so review IDs start at REV-0001.
    def _flag(employee_id: str, request: str, reason: str) -> Observation:
        arguments = {"employee_id": employee_id, "request": request, "reason": reason}
        result = flag_for_human_review(
            employee_id, request, reason, queue_path=tmp_path / "queue.json", now=FIXED_NOW
        )
        return Observation("flag_for_human_review", arguments, result)

    return _flag


# ---------- Data types and constants ----------


def test_investigation_tools_are_the_three_read_only_tools() -> None:
    assert INVESTIGATION_TOOLS == frozenset(
        {"get_employee_info", "get_policy_limits", "check_request_eligibility"}
    )


def test_max_investigation_calls_is_eight_per_c6() -> None:
    assert MAX_INVESTIGATION_CALLS == 8


def test_observation_is_frozen(emp: Callable[[str], Observation]) -> None:
    observation = emp("E1002")

    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.tool = "x"  # type: ignore[misc]


def test_final_answer_is_frozen() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW")

    with pytest.raises(dataclasses.FrozenInstanceError):
        answer.decision = "deny"  # type: ignore[misc]


def test_final_answer_optional_fields_default_to_none() -> None:
    answer = FinalAnswer(decision="escalate", rule_id=None)

    assert (answer.review_id, answer.reason_code, answer.draft_reply) == (None, None, None)


# ---------- check_tool_call: C6 step limit ----------


def test_check_tool_call_first_call_is_allowed() -> None:
    assert check_tool_call("get_employee_info", []) is None


def test_check_tool_call_eighth_investigation_call_is_allowed(
    emp: Callable[[str], Observation],
) -> None:
    seven = [emp("E1002")] * 7

    assert check_tool_call("check_request_eligibility", seven) is None


@pytest.mark.parametrize("tool", sorted(INVESTIGATION_TOOLS), ids=str)
def test_check_tool_call_ninth_investigation_call_is_blocked(
    tool: str, emp: Callable[[str], Observation]
) -> None:
    eight = [emp("E1002")] * 8

    assert check_tool_call(tool, eight) == "C6_STEP_LIMIT"


def test_emp_unknown_id_observation_is_an_error(emp: Callable[[str], Observation]) -> None:
    # Guards the next test's premise: E9999 really is a failed lookup.
    assert emp("E9999").result["error"] == "EMPLOYEE_NOT_FOUND"


def test_check_tool_call_failed_investigation_calls_still_count(
    emp: Callable[[str], Observation],
) -> None:
    # C6 caps calls, not successes: a model looping on a bad ID must still hit the limit.
    eight_errors = [emp("E9999")] * 8

    assert check_tool_call("get_employee_info", eight_errors) == "C6_STEP_LIMIT"


def test_check_tool_call_flag_does_not_count_towards_the_limit(
    emp: Callable[[str], Observation], flag: Flag
) -> None:
    observations = [emp("E1002")] * 7 + [flag("E1002", "phone stolen", "E6: Phone stolen.")]

    assert check_tool_call("get_employee_info", observations) is None


def test_check_tool_call_flag_is_allowed_after_the_step_limit(
    emp: Callable[[str], Observation],
) -> None:
    # C6: reaching the limit triggers E11, and the agent must still be able to make that call.
    eight = [emp("E1002")] * 8

    assert check_tool_call("flag_for_human_review", eight) is None


# ---------- check_tool_call: C7 one flag per request ----------


def test_check_tool_call_first_flag_is_allowed(emp: Callable[[str], Observation]) -> None:
    assert check_tool_call("flag_for_human_review", [emp("E1002")]) is None


def test_flag_fixture_first_flag_creates_rev_0001(flag: Flag) -> None:
    # Guards the premise of the C3/C7 tests below: a fresh queue per test.
    assert flag("E1002", "phone stolen", "E6: Phone stolen.").result["review_id"] == "REV-0001"


def test_check_tool_call_second_flag_is_blocked(flag: Flag) -> None:
    first = flag("E1002", "phone stolen", "E6: Phone stolen.")

    assert check_tool_call("flag_for_human_review", [first]) == "C7_ALREADY_FLAGGED"


def test_flag_fixture_bad_reason_is_rejected(flag: Flag) -> None:
    assert flag("E1002", "phone stolen", "stolen phone").result["error"] == "INVALID_REASON"


def test_check_tool_call_flag_retry_is_allowed_after_a_rejected_flag(flag: Flag) -> None:
    # A rejected call wrote no ticket (§7.4), so nothing has been flagged yet.
    rejected = flag("E1002", "phone stolen", "stolen phone")

    assert check_tool_call("flag_for_human_review", [rejected]) is None


# ---------- check_final_answer: approve (C1 + required calls) ----------


def test_approve_passes_with_eligible_observation(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    observations = [emp("E1002"), elig("E1002", "monitor")]
    answer = FinalAnswer(decision="approve", rule_id="R-NEW", draft_reply="Approved under R-NEW.")

    assert check_final_answer(answer, observations) == []


def test_elig_fixture_e1001_laptop_is_not_eligible(
    elig: Callable[[str, str], Observation],
) -> None:
    assert elig("E1001", "laptop").result["status"] == "NOT_ELIGIBLE"


def test_approve_with_not_eligible_observation_fails_c1(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    observations = [emp("E1001"), elig("E1001", "laptop")]
    answer = FinalAnswer(decision="approve", rule_id="R-REFRESH")

    assert check_final_answer(answer, observations) == ["C1"]


def test_approve_with_needs_review_observation_fails_c1(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    observations = [emp("E1006"), elig("E1006", "monitor")]
    answer = FinalAnswer(decision="approve", rule_id="R-STATUS")

    assert check_final_answer(answer, observations) == ["C1"]


def test_approve_with_wrong_rule_id_fails_c1(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    observations = [emp("E1002"), elig("E1002", "monitor")]  # R-NEW
    answer = FinalAnswer(decision="approve", rule_id="R-REFRESH-OK")

    assert check_final_answer(answer, observations) == ["C1"]


def test_approve_uses_the_latest_eligibility_check(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    # An older ELIGIBLE verdict (for another item) can't back an approval after a newer
    # NOT_ELIGIBLE one: the latest check is what the decision must rest on.
    observations = [emp("E1002"), elig("E1002", "monitor"), elig("E1002", "docking_station")]
    answer = FinalAnswer(decision="approve", rule_id="R-NEW")

    assert check_final_answer(answer, observations) == ["C1"]


def test_approve_without_employee_lookup_fails_required_calls(
    elig: Callable[[str, str], Observation],
) -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW")

    assert check_final_answer(answer, [elig("E1002", "monitor")]) == ["REQUIRED_CALLS"]


def test_failed_employee_lookup_does_not_satisfy_required_calls(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    # An error result is not data about the employee, so it can't count as the lookup.
    observations = [emp("abc"), elig("E1002", "monitor")]
    answer = FinalAnswer(decision="approve", rule_id="R-NEW")

    assert check_final_answer(answer, observations) == ["REQUIRED_CALLS"]


def test_approve_with_no_observations_fails_c1_and_required_calls() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW")

    assert check_final_answer(answer, []) == ["C1", "REQUIRED_CALLS"]


# ---------- check_final_answer: deny (C2 + required calls) ----------


@pytest.fixture
def deny_observations(
    emp: Callable[[str], Observation],
    elig: Callable[[str, str], Observation],
    limits: Callable[[str], Observation],
) -> list[Observation]:
    # E1001 laptop: NOT_ELIGIBLE under R-REFRESH (§8).
    return [emp("E1001"), elig("E1001", "laptop"), limits("standard")]


def test_deny_passes_with_rule_id_cited(deny_observations: list[Observation]) -> None:
    answer = FinalAnswer(
        decision="deny",
        rule_id="R-REFRESH",
        draft_reply="Your laptop is not due for refresh until 2028-03-10 (rule R-REFRESH).",
    )

    assert check_final_answer(answer, deny_observations) == []


def test_deny_without_rule_id_in_reply_fails_c2(deny_observations: list[Observation]) -> None:
    answer = FinalAnswer(
        decision="deny", rule_id="R-REFRESH", draft_reply="Not due until 2028-03-10."
    )

    assert check_final_answer(answer, deny_observations) == ["C2"]


def test_deny_with_no_reply_fails_c2(deny_observations: list[Observation]) -> None:
    answer = FinalAnswer(decision="deny", rule_id="R-REFRESH")

    assert check_final_answer(answer, deny_observations) == ["C2"]


def test_deny_citing_only_a_longer_rule_id_fails_c2(
    deny_observations: list[Observation],
) -> None:
    # "R-REFRESH" is a substring of "R-REFRESH-OK"; citing the wrong rule is not citing it.
    answer = FinalAnswer(
        decision="deny", rule_id="R-REFRESH", draft_reply="Checked against R-REFRESH-OK."
    )

    assert check_final_answer(answer, deny_observations) == ["C2"]


def test_deny_with_wrong_rule_id_fails_c2(deny_observations: list[Observation]) -> None:
    answer = FinalAnswer(decision="deny", rule_id="R-ROLE", draft_reply="Denied (R-ROLE).")

    assert check_final_answer(answer, deny_observations) == ["C2"]


def test_deny_with_eligible_observation_fails_c2(
    emp: Callable[[str], Observation],
    elig: Callable[[str, str], Observation],
    limits: Callable[[str], Observation],
) -> None:
    observations = [emp("E1002"), elig("E1002", "monitor"), limits("engineer")]
    answer = FinalAnswer(decision="deny", rule_id="R-NEW", draft_reply="Denied (R-NEW).")

    assert check_final_answer(answer, observations) == ["C2"]


def test_deny_without_policy_limits_fails_required_calls(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation]
) -> None:
    observations = [emp("E1001"), elig("E1001", "laptop")]
    answer = FinalAnswer(decision="deny", rule_id="R-REFRESH", draft_reply="Denied (R-REFRESH).")

    assert check_final_answer(answer, observations) == ["REQUIRED_CALLS"]


# ---------- check_final_answer: escalate (C3, E7 required calls, C7) ----------


def test_escalate_passes_with_flag_review_id_quoted(flag: Flag) -> None:
    observations = [flag("E1002", "My phone was stolen", "E6: Phone reported stolen.")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="A reviewer will follow up. Your reference is REV-0001.",
    )

    assert check_final_answer(answer, observations) == []


def test_escalate_without_flag_fails_c3(emp: Callable[[str], Observation]) -> None:
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="Reference REV-0001.",
    )

    assert check_final_answer(answer, [emp("E1002")]) == ["C3"]


def test_escalate_after_rejected_flag_fails_c3(flag: Flag) -> None:
    observations = [flag("E1002", "My phone was stolen", "phone stolen")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="Reference REV-0001.",
    )

    assert check_final_answer(answer, observations) == ["C3"]


def test_escalate_with_wrong_review_id_fails_c3(flag: Flag) -> None:
    observations = [flag("E1002", "My phone was stolen", "E6: Phone reported stolen.")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0042",
        reason_code="E6",
        draft_reply="Reference REV-0042.",
    )

    assert check_final_answer(answer, observations) == ["C3"]


def test_escalate_without_review_id_in_reply_fails_c3(flag: Flag) -> None:
    observations = [flag("E1002", "My phone was stolen", "E6: Phone reported stolen.")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="A reviewer will follow up.",
    )

    assert check_final_answer(answer, observations) == ["C3"]


@pytest.mark.parametrize(
    "reason_code",
    [None, "E0", "E12", "e6", "E6: stolen", ""],
    ids=["none", "e0", "e12", "lower", "with-text", "empty"],
)
def test_escalate_with_invalid_reason_code_fails_c3(reason_code: str | None, flag: Flag) -> None:
    observations = [flag("E1002", "My phone was stolen", "E6: Phone reported stolen.")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code=reason_code,
        draft_reply="Reference REV-0001.",
    )

    assert check_final_answer(answer, observations) == ["C3"]


def _e7_answer() -> FinalAnswer:
    return FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E7",
        draft_reply="IT will verify the damage. Reference REV-0001.",
    )


E7_REASON = "E7: Laptop reported broken; not due for refresh."


def test_e7_passes_with_lookup_and_check_before_flag(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation], flag: Flag
) -> None:
    observations = [
        emp("E1001"),
        elig("E1001", "laptop"),
        flag("E1001", "My laptop screen is cracked", E7_REASON),
    ]
    assert check_final_answer(_e7_answer(), observations) == []


def test_e7_without_eligibility_check_fails_required_calls(
    emp: Callable[[str], Observation], flag: Flag
) -> None:
    observations = [emp("E1001"), flag("E1001", "My laptop screen is cracked", E7_REASON)]
    assert check_final_answer(_e7_answer(), observations) == ["REQUIRED_CALLS"]


def test_e7_with_eligibility_check_after_flag_fails_required_calls(
    emp: Callable[[str], Observation], elig: Callable[[str, str], Observation], flag: Flag
) -> None:
    # Decision table row 3: the checks come BEFORE the flag, so the flag rests on them.
    observations = [
        emp("E1001"),
        flag("E1001", "My laptop screen is cracked", E7_REASON),
        elig("E1001", "laptop"),
    ]
    assert check_final_answer(_e7_answer(), observations) == ["REQUIRED_CALLS"]


def test_non_e7_escalation_needs_no_investigation(flag: Flag) -> None:
    # Row 1 needs only the flag: E1 has no valid employee to look up at all.
    observations = [flag("E9999", "Need a laptop", "E1: Employee ID not found.")]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E1",
        draft_reply="Reference REV-0001.",
    )

    assert check_final_answer(answer, observations) == []


def test_flag_fixture_second_request_creates_rev_0002(flag: Flag) -> None:
    flag("E1002", "My phone was stolen", "E6: Phone reported stolen.")

    assert flag("E1002", "Also my headset", "E5: Second item.").result["review_id"] == "REV-0002"


def test_two_successful_flags_fail_c7(flag: Flag) -> None:
    observations = [
        flag("E1002", "My phone was stolen", "E6: Phone reported stolen."),
        flag("E1002", "Also my headset", "E5: Second item."),
    ]
    answer = FinalAnswer(
        decision="escalate",
        rule_id=None,
        review_id="REV-0001",
        reason_code="E6",
        draft_reply="Reference REV-0001.",
    )

    assert check_final_answer(answer, observations) == ["C7"]


# ---------- Several violations at once ----------


def test_several_violations_are_all_returned_in_fixed_order(
    emp: Callable[[str], Observation],
    elig: Callable[[str, str], Observation],
    flag: Flag,
) -> None:
    # Deny on an ELIGIBLE verdict, no policy lookup, no rule cited, and two flags.
    observations = [
        emp("E1002"),
        elig("E1002", "monitor"),
        flag("E1002", "request one", "E6: x."),
        flag("E1002", "request two", "E6: y."),
    ]
    answer = FinalAnswer(decision="deny", rule_id="R-NEW", draft_reply="No.")

    assert check_final_answer(answer, observations) == ["C2", "REQUIRED_CALLS", "C7"]


# ---------- violation_message ----------

ALL_CODES = ["C1", "C2", "C3", "REQUIRED_CALLS", "C7", "C6_STEP_LIMIT", "C7_ALREADY_FLAGGED"]


@pytest.mark.parametrize("code", ALL_CODES, ids=str)
def test_violation_message_starts_with_the_code(code: str) -> None:
    assert violation_message([code]).startswith(f"{code}: ")


@pytest.mark.parametrize("code", ALL_CODES, ids=str)
def test_violation_message_is_one_full_sentence(code: str) -> None:
    message = violation_message([code])

    assert (message.endswith("."), message.count(f"{code}: ")) == (True, 1)


@pytest.mark.parametrize("word", ["check_request_eligibility", "ELIGIBLE"], ids=str)
def test_violation_message_c1_names_the_required_tool_and_verdict(word: str) -> None:
    assert word in violation_message(["C1"])


def test_violation_message_several_codes_keep_their_order() -> None:
    message = violation_message(["C2", "REQUIRED_CALLS", "C7"])

    assert message.index("C2: ") < message.index("REQUIRED_CALLS: ") < message.index("C7: ")


def test_violation_message_no_codes_is_empty() -> None:
    assert violation_message([]) == ""

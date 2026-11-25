"""Tests for check_request_eligibility and add_months (REQUIREMENTS §4.1-4.3, §7.3, §8, §12.1)."""

from datetime import date
from typing import Any

import pytest

from it_requests import config
from it_requests.data import (
    CATALOG_ITEMS,
    EmployeeDirectory,
    PolicyTable,
    load_employees,
    load_policy,
)
from it_requests.eligibility import add_months, check_request_eligibility


@pytest.fixture(scope="module")
def directory() -> EmployeeDirectory:
    return load_employees(config.EMPLOYEES_FILE)


@pytest.fixture(scope="module")
def policy() -> PolicyTable:
    return load_policy(config.POLICY_FILE)


@pytest.fixture(scope="module")
def check(directory: EmployeeDirectory, policy: PolicyTable) -> Any:
    # Binds the real data once, so each test reads as check(employee_id, item, ...).
    def _check(employee_id: str, item: str, as_of: date = config.REFERENCE_DATE) -> Any:
        return check_request_eligibility(
            employee_id, item, directory=directory, policy=policy, as_of=as_of
        )

    return _check


def _pick(result: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """The fields of result named in expected, so one assert compares a group of fields."""
    return {key: result[key] for key in expected}


# ---------- A. every row of REQUIREMENTS §8 ----------

SECTION_8 = [
    # (employee, item, status, rule_id)
    ("E1001", "laptop", "NOT_ELIGIBLE", "R-REFRESH"),
    ("E1001", "keyboard_mouse", "ELIGIBLE", "R-REFRESH-OK"),
    ("E1001", "mobile_phone", "NOT_ELIGIBLE", "R-ROLE"),
    ("E1002", "monitor", "ELIGIBLE", "R-NEW"),
    ("E1002", "docking_station", "NOT_ELIGIBLE", "R-LIMIT"),
    ("E1002", "laptop", "ELIGIBLE", "R-REFRESH-OK"),
    ("E1003", "mobile_phone", "NOT_ELIGIBLE", "R-REFRESH"),
    ("E1003", "monitor", "ELIGIBLE", "R-REFRESH-OK"),
    ("E1004", "laptop", "NOT_ELIGIBLE", "R-ROLE"),
    ("E1005", "laptop", "ELIGIBLE", "R-REFRESH-OK"),
    ("E1006", "monitor", "NEEDS_REVIEW", "R-STATUS"),
]
SECTION_8_ERRORS = [
    # (employee, item, error code)
    ("E9999", "laptop", "EMPLOYEE_NOT_FOUND"),
    ("E1001", "standing_desk", "UNKNOWN_ITEM"),
]


@pytest.mark.parametrize(
    ("employee_id", "item", "status", "rule_id"),
    SECTION_8,
    ids=[f"{e}-{i}-{r}" for e, i, _, r in SECTION_8],
)
def test_check_eligibility_requirements_row_returns_status_and_rule(
    check: Any, employee_id: str, item: str, status: str, rule_id: str
) -> None:
    result = check(employee_id, item)

    assert (result["status"], result["rule_id"]) == (status, rule_id)


@pytest.mark.parametrize(
    ("employee_id", "item", "code"),
    SECTION_8_ERRORS,
    ids=[f"{e}-{i}-{c}" for e, i, c in SECTION_8_ERRORS],
)
def test_check_eligibility_requirements_error_row_returns_error_code(
    check: Any, employee_id: str, item: str, code: str
) -> None:
    assert check(employee_id, item)["error"] == code


# ---------- B. exact values for the demo cases ----------


def test_check_eligibility_e1001_laptop_reports_refresh_not_due(check: Any) -> None:
    expected = {
        "units_held": 1,
        "max": 1,
        "oldest_issued_date": "2025-03-10",
        "next_eligible_date": "2028-03-10",
        "replaces_asset_id": None,
        "as_of": "2026-11-22",
    }

    result = check("E1001", "laptop")

    assert _pick(result, expected) == expected


def test_check_eligibility_e1002_monitor_reports_new_unit(check: Any) -> None:
    # R-NEW adds a unit; nothing is returned, so there is no asset to replace.
    expected = {"units_held": 1, "max": 2, "next_eligible_date": None, "replaces_asset_id": None}

    result = check("E1002", "monitor")

    assert _pick(result, expected) == expected


def test_check_eligibility_e1003_monitor_holds_two_of_two(check: Any) -> None:
    result = check("E1003", "monitor")

    assert (result["units_held"], result["max"]) == (2, 2)


def test_check_eligibility_e1003_monitor_breaks_tie_by_asset_id(check: Any) -> None:
    # Both monitors were issued 2023-04-11; the lower asset_id is "oldest", every time.
    result = check("E1003", "monitor")

    assert (result["replaces_asset_id"], result["refresh_date"]) == ("MN-1031", "2026-04-11")


def test_check_eligibility_e1005_laptop_names_unit_to_return(check: Any) -> None:
    assert check("E1005", "laptop")["replaces_asset_id"] == "LT-1650"


def test_check_eligibility_e1003_phone_reports_next_eligible_date(check: Any) -> None:
    assert check("E1003", "mobile_phone")["next_eligible_date"] == "2027-01-20"


def test_check_eligibility_data_versions_name_both_files(check: Any) -> None:
    assert check("E1001", "laptop")["data_versions"] == {
        "employees": "employees-v1",
        "policy": "policy-v1",
    }


# ---------- C. boundaries around the refresh date ----------
# E1001 keyboard_mouse: issued 2021-06-14, 24-month refresh, so refresh date is 2023-06-14.


def test_check_eligibility_on_the_refresh_date_is_eligible(check: Any) -> None:
    result = check("E1001", "keyboard_mouse", as_of=date(2023, 6, 14))

    assert (result["status"], result["rule_id"]) == ("ELIGIBLE", "R-REFRESH-OK")


def test_check_eligibility_one_day_before_refresh_is_not_eligible(check: Any) -> None:
    result = check("E1001", "keyboard_mouse", as_of=date(2023, 6, 13))

    assert (result["status"], result["rule_id"]) == ("NOT_ELIGIBLE", "R-REFRESH")


def test_check_eligibility_one_day_before_refresh_names_the_refresh_date(check: Any) -> None:
    result = check("E1001", "keyboard_mouse", as_of=date(2023, 6, 13))

    assert result["next_eligible_date"] == "2023-06-14"


# ---------- D. add_months ----------


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),  # leap-year clamp (REQUIREMENTS §4.2)
        (date(2023, 1, 31), 1, date(2023, 2, 28)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2025, 3, 10), 36, date(2028, 3, 10)),
        (date(2021, 6, 14), 24, date(2023, 6, 14)),
        (date(2024, 11, 30), 3, date(2025, 2, 28)),  # crosses a year end
        (date(2026, 11, 22), 0, date(2026, 11, 22)),
    ],
    ids=[
        "leap-year-clamp",
        "non-leap-clamp",
        "leap-day-plus-year",
        "e1001-laptop",
        "e1001-keyboard",
        "crosses-year-end",
        "zero-months",
    ],
)
def test_add_months_clamps_to_the_last_day_of_short_months(
    start: date, months: int, expected: date
) -> None:
    assert add_months(start, months) == expected


# ---------- E. order of the algorithm: the first matching step wins ----------


def test_check_eligibility_status_is_checked_before_role(check: Any) -> None:
    # E1006 is on leave and, as an engineer, not entitled to a phone. Status comes first.
    result = check("E1006", "mobile_phone")

    assert (result["status"], result["rule_id"]) == ("NEEDS_REVIEW", "R-STATUS")


def test_check_eligibility_unknown_employee_is_checked_before_unknown_item(check: Any) -> None:
    assert check("E9999", "standing_desk")["error"] == "EMPLOYEE_NOT_FOUND"


def test_check_eligibility_bad_id_format_is_checked_first(check: Any) -> None:
    assert check("abc", "laptop") == {"error": "INVALID_EMPLOYEE_ID", "employee_id": "abc"}


# ---------- F. input normalisation ----------


def test_check_eligibility_messy_inputs_give_the_clean_verdict(check: Any) -> None:
    assert check(" e1002 ", " Monitor ") == check("E1002", "monitor")


def test_check_eligibility_unknown_item_echoes_raw_item(check: Any) -> None:
    assert check("E1001", " Standing_Desk ") == {"error": "UNKNOWN_ITEM", "item": " Standing_Desk "}


# ---------- G. robustness: every employee x every catalog item ----------

STATUSES = {"ELIGIBLE", "NOT_ELIGIBLE", "NEEDS_REVIEW"}
RULE_IDS = {"R-ROLE", "R-NEW", "R-REFRESH-OK", "R-REFRESH", "R-LIMIT", "R-STATUS"}
ALL_PAIRS = [
    (employee.employee_id, item)
    for employee in load_employees(config.EMPLOYEES_FILE).employees
    for item in CATALOG_ITEMS
]
ALL_PAIR_IDS = [f"{e}-{i}" for e, i in ALL_PAIRS]


@pytest.mark.parametrize(("employee_id", "item"), ALL_PAIRS, ids=ALL_PAIR_IDS)
def test_check_eligibility_any_pair_returns_a_known_status(
    check: Any, employee_id: str, item: str
) -> None:
    assert check(employee_id, item)["status"] in STATUSES


@pytest.mark.parametrize(("employee_id", "item"), ALL_PAIRS, ids=ALL_PAIR_IDS)
def test_check_eligibility_any_pair_returns_a_known_rule(
    check: Any, employee_id: str, item: str
) -> None:
    assert check(employee_id, item)["rule_id"] in RULE_IDS


@pytest.mark.parametrize(("employee_id", "item"), ALL_PAIRS, ids=ALL_PAIR_IDS)
def test_check_eligibility_eligible_verdict_has_no_next_eligible_date(
    check: Any, employee_id: str, item: str
) -> None:
    result = check(employee_id, item)

    # Only ELIGIBLE verdicts are constrained; any other status passes trivially.
    assert result["status"] != "ELIGIBLE" or result["next_eligible_date"] is None


# ---------- H. shape: one fixed key set for every verdict ----------

EXPECTED_KEYS = {
    "employee_id",
    "item",
    "status",
    "rule_id",
    "role",
    "employment_status",
    "units_held",
    "max",
    "refresh_months",
    "oldest_issued_date",
    "refresh_date",
    "next_eligible_date",
    "replaces_asset_id",
    "as_of",
    "data_versions",
}


@pytest.mark.parametrize(
    ("employee_id", "item"),
    [(e, i) for e, i, _, _ in SECTION_8],
    ids=[f"{e}-{i}" for e, i, _, _ in SECTION_8],
)
def test_check_eligibility_every_verdict_has_the_same_keys(
    check: Any, employee_id: str, item: str
) -> None:
    assert set(check(employee_id, item)) == EXPECTED_KEYS


def test_check_eligibility_status_verdict_stops_before_policy(check: Any) -> None:
    # R-STATUS returns before the policy is read: holdings are known, limits are not.
    expected = {"units_held": 1, "max": None, "refresh_months": None, "refresh_date": None}

    result = check("E1006", "monitor")

    assert _pick(result, expected) == expected

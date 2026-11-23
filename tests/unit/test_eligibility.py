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


# --- A. Every row of REQUIREMENTS §8 ---------------------------------------------------

SECTION_8 = [
    # (employee, item, status, rule_id or error code)
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
    ("E9999", "laptop", "error", "EMPLOYEE_NOT_FOUND"),
    ("E1001", "standing_desk", "error", "UNKNOWN_ITEM"),
]


@pytest.mark.parametrize(
    ("employee_id", "item", "status", "code"),
    SECTION_8,
    ids=[f"{e}-{i}-{c}" for e, i, _, c in SECTION_8],
)
def test_requirements_section_8(
    check: Any, employee_id: str, item: str, status: str, code: str
) -> None:
    result = check(employee_id, item)

    if status == "error":
        assert result["error"] == code
    else:
        assert result["status"] == status
        assert result["rule_id"] == code


# --- B. Exact values for the demo cases --------------------------------------------------


def test_e1001_laptop_refresh_not_due(check: Any) -> None:
    result = check("E1001", "laptop")

    assert result["units_held"] == 1
    assert result["max"] == 1
    assert result["oldest_issued_date"] == "2025-03-10"
    assert result["next_eligible_date"] == "2028-03-10"
    assert result["replaces_asset_id"] is None
    assert result["as_of"] == "2026-11-22"


def test_e1002_monitor_new_unit(check: Any) -> None:
    result = check("E1002", "monitor")

    assert result["units_held"] == 1
    assert result["max"] == 2
    assert result["next_eligible_date"] is None
    # R-NEW adds a unit; nothing is returned, so there is no asset to replace.
    assert result["replaces_asset_id"] is None


def test_e1003_monitor_replacement_breaks_tie_by_asset_id(check: Any) -> None:
    result = check("E1003", "monitor")

    assert result["units_held"] == 2
    assert result["max"] == 2
    # Both monitors were issued 2023-04-11; the lower asset_id is "oldest", every time.
    assert result["replaces_asset_id"] == "MN-1031"
    assert result["refresh_date"] == "2026-04-11"


def test_e1005_laptop_names_unit_to_return(check: Any) -> None:
    assert check("E1005", "laptop")["replaces_asset_id"] == "LT-1650"


def test_e1003_phone_next_eligible_date(check: Any) -> None:
    assert check("E1003", "mobile_phone")["next_eligible_date"] == "2027-01-20"


def test_data_versions_name_both_files(check: Any) -> None:
    assert check("E1001", "laptop")["data_versions"] == {
        "employees": "employees-v1",
        "policy": "policy-v1",
    }


# --- C. Boundaries around the refresh date ------------------------------------------------
# E1001 keyboard_mouse: issued 2021-06-14, 24-month refresh, so refresh date is 2023-06-14.


def test_eligible_on_the_refresh_date(check: Any) -> None:
    result = check("E1001", "keyboard_mouse", as_of=date(2023, 6, 14))

    assert result["status"] == "ELIGIBLE"
    assert result["rule_id"] == "R-REFRESH-OK"


def test_not_eligible_one_day_before(check: Any) -> None:
    result = check("E1001", "keyboard_mouse", as_of=date(2023, 6, 13))

    assert result["status"] == "NOT_ELIGIBLE"
    assert result["rule_id"] == "R-REFRESH"
    assert result["next_eligible_date"] == "2023-06-14"


# --- D. add_months --------------------------------------------------------------------------


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
)
def test_add_months(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


# --- E. Order of the algorithm: the first matching step wins -------------------------------


def test_status_checked_before_role(check: Any) -> None:
    # E1006 is on leave and, as an engineer, not entitled to a phone. Status comes first.
    result = check("E1006", "mobile_phone")

    assert result["status"] == "NEEDS_REVIEW"
    assert result["rule_id"] == "R-STATUS"


def test_unknown_employee_checked_before_unknown_item(check: Any) -> None:
    assert check("E9999", "standing_desk")["error"] == "EMPLOYEE_NOT_FOUND"


def test_bad_id_format_checked_first(check: Any) -> None:
    assert check("abc", "laptop") == {"error": "INVALID_EMPLOYEE_ID", "employee_id": "abc"}


# --- F. Input normalisation ------------------------------------------------------------------


def test_messy_inputs_normalise(check: Any) -> None:
    assert check(" e1002 ", " Monitor ") == check("E1002", "monitor")


def test_unknown_item_echoes_raw_item(check: Any) -> None:
    assert check("E1001", " Standing_Desk ") == {"error": "UNKNOWN_ITEM", "item": " Standing_Desk "}


# --- G. Robustness: every employee x every catalog item ---------------------------------------

STATUSES = {"ELIGIBLE", "NOT_ELIGIBLE", "NEEDS_REVIEW"}
RULE_IDS = {"R-ROLE", "R-NEW", "R-REFRESH-OK", "R-REFRESH", "R-LIMIT", "R-STATUS"}


def test_every_employee_and_item_gives_a_valid_verdict(
    check: Any, directory: EmployeeDirectory
) -> None:
    for employee in directory.employees:
        for item in CATALOG_ITEMS:
            result = check(employee.employee_id, item)
            context = f"{employee.employee_id} {item}: {result}"
            assert result["status"] in STATUSES, context
            assert result["rule_id"] in RULE_IDS, context
            if result["status"] == "ELIGIBLE":
                assert result["next_eligible_date"] is None, context


# --- H. Shape: one fixed key set for every verdict ----------------------------------------------

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
    [(e, i) for e, i, status, _ in SECTION_8 if status != "error"],
)
def test_every_verdict_has_the_same_keys(check: Any, employee_id: str, item: str) -> None:
    assert set(check(employee_id, item)) == EXPECTED_KEYS


def test_status_verdict_stops_before_policy(check: Any) -> None:
    # R-STATUS returns before the policy is read: holdings are known, limits are not.
    result = check("E1006", "monitor")

    assert result["units_held"] == 1
    assert result["max"] is None
    assert result["refresh_months"] is None
    assert result["refresh_date"] is None

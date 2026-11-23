"""Tests for get_employee_info and its helpers (REQUIREMENTS §2.1, §7.1, §12.1)."""

from datetime import date

import pytest

from it_requests import config
from it_requests.data import EmployeeDirectory, load_employees
from it_requests.employees import get_employee_info, normalize_employee_id, tenure_months


@pytest.fixture(scope="module")
def directory() -> EmployeeDirectory:
    # Module scope: the data is frozen, so one load can be shared safely by every test.
    return load_employees(config.EMPLOYEES_FILE)


# --- normalize_employee_id ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E1001", "E1001"),
        ("e1001", "E1001"),
        (" e1001 ", "E1001"),
        ("\tE9999\n", "E9999"),
        ("abc", None),
        ("", None),
        ("   ", None),
        ("E12", None),
        ("E12345", None),
        ("1001", None),
        ("E 1001", None),
        ("EE1001", None),
    ],
)
def test_normalize_employee_id(raw: str, expected: str | None) -> None:
    assert normalize_employee_id(raw) == expected


# --- tenure_months -------------------------------------------------------------


@pytest.mark.parametrize(
    ("hire", "as_of", "expected"),
    [
        # Same day of month: the month is complete, so it counts.
        (date(2021, 6, 14), date(2026, 11, 14), 65),
        # One day short: the 65th month is not yet complete.
        (date(2021, 6, 14), date(2026, 11, 13), 64),
        (date(2021, 6, 14), date(2021, 6, 14), 0),
        (date(2021, 6, 14), date(2026, 11, 22), 65),
    ],
)
def test_tenure_months(hire: date, as_of: date, expected: int) -> None:
    assert tenure_months(hire, as_of) == expected


# --- get_employee_info: success ------------------------------------------------


def test_e1001_returns_full_record(directory: EmployeeDirectory) -> None:
    result = get_employee_info("E1001", directory=directory)

    assert result["employee_id"] == "E1001"
    assert result["name"] == "Priya Shah"
    assert result["role"] == "standard"
    assert result["hire_date"] == "2021-06-14"
    assert result["tenure_months"] == 65
    assert result["status"] == "active"
    assert len(result["equipment"]) == 3
    assert {"item": "laptop", "asset_id": "LT-2291", "issued_date": "2025-03-10"} in result[
        "equipment"
    ]
    assert result["data_version"] == "employees-v1"
    assert "error" not in result


def test_equipment_sorted_by_issued_date_then_asset_id(directory: EmployeeDirectory) -> None:
    result = get_employee_info("E1001", directory=directory)

    # The two 2021-06-14 items come first, tie-broken by asset_id; the 2025 laptop is last.
    assert [e["asset_id"] for e in result["equipment"]] == ["KM-1001", "MN-1001", "LT-2291"]


def test_on_leave_employee_is_returned_not_judged(directory: EmployeeDirectory) -> None:
    # The lookup reports status; deciding what on_leave means is the eligibility tool's job.
    result = get_employee_info("E1006", directory=directory)

    assert result["status"] == "on_leave"
    assert "error" not in result


@pytest.mark.parametrize("raw", [" e1001 ", "e1001"])
def test_messy_input_normalises_to_e1001(directory: EmployeeDirectory, raw: str) -> None:
    result = get_employee_info(raw, directory=directory)

    assert result["employee_id"] == "E1001"
    assert result["name"] == "Priya Shah"


def test_as_of_is_respected(directory: EmployeeDirectory) -> None:
    result = get_employee_info("E1001", directory=directory, as_of=date(2022, 6, 14))

    assert result["tenure_months"] == 12


# --- get_employee_info: errors returned as data ---------------------------------


def test_unknown_employee_returns_not_found(directory: EmployeeDirectory) -> None:
    assert get_employee_info("E9999", directory=directory) == {
        "error": "EMPLOYEE_NOT_FOUND",
        "employee_id": "E9999",
    }


def test_not_found_echoes_normalised_id(directory: EmployeeDirectory) -> None:
    # The format was valid, so the normalised ID is the useful thing to report back.
    assert get_employee_info(" e9999 ", directory=directory) == {
        "error": "EMPLOYEE_NOT_FOUND",
        "employee_id": "E9999",
    }


@pytest.mark.parametrize("raw", ["abc", "", "E12", "E12345", "1001"])
def test_bad_format_returns_invalid_id_with_raw_input(
    directory: EmployeeDirectory, raw: str
) -> None:
    # The raw input is echoed unchanged so the model can see exactly what it sent.
    assert get_employee_info(raw, directory=directory) == {
        "error": "INVALID_EMPLOYEE_ID",
        "employee_id": raw,
    }

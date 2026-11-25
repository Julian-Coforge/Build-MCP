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


# ---------- normalize_employee_id ----------


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
    ids=[
        "upper",
        "lower",
        "padded",
        "tab-newline",
        "letters",
        "empty",
        "blank",
        "too-short",
        "too-long",
        "no-prefix",
        "inner-space",
        "double-prefix",
    ],
)
def test_normalize_employee_id_trims_uppercases_or_returns_none(
    raw: str, expected: str | None
) -> None:
    assert normalize_employee_id(raw) == expected


# ---------- tenure_months ----------


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
    ids=["same-day-of-month", "one-day-short", "hire-day", "reference-date"],
)
def test_tenure_months_counts_only_complete_months(hire: date, as_of: date, expected: int) -> None:
    assert tenure_months(hire, as_of) == expected


# ---------- get_employee_info: success ----------


E1001_RECORD = {
    "employee_id": "E1001",
    "name": "Priya Shah",
    "role": "standard",
    "hire_date": "2021-06-14",
    "tenure_months": 65,
    "status": "active",
    "equipment": [
        {"item": "keyboard_mouse", "asset_id": "KM-1001", "issued_date": "2021-06-14"},
        {"item": "monitor", "asset_id": "MN-1001", "issued_date": "2021-06-14"},
        {"item": "laptop", "asset_id": "LT-2291", "issued_date": "2025-03-10"},
    ],
    "data_version": "employees-v1",
}


def test_get_employee_info_known_id_returns_whole_record(directory: EmployeeDirectory) -> None:
    # The whole value, so a wrong field, an extra item or an "error" key all fail here.
    assert get_employee_info("E1001", directory=directory) == E1001_RECORD


def test_get_employee_info_sorts_equipment_by_issued_date_then_asset_id(
    directory: EmployeeDirectory,
) -> None:
    result = get_employee_info("E1001", directory=directory)

    # The two 2021-06-14 items come first, tie-broken by asset_id; the 2025 laptop is last.
    assert [e["asset_id"] for e in result["equipment"]] == ["KM-1001", "MN-1001", "LT-2291"]


def test_get_employee_info_on_leave_employee_reports_status(directory: EmployeeDirectory) -> None:
    # The lookup reports status; deciding what on_leave means is the eligibility tool's job.
    assert get_employee_info("E1006", directory=directory)["status"] == "on_leave"


def test_get_employee_info_on_leave_employee_is_not_an_error(directory: EmployeeDirectory) -> None:
    assert "error" not in get_employee_info("E1006", directory=directory)


@pytest.mark.parametrize("raw", [" e1001 ", "e1001"], ids=["padded", "lower"])
def test_get_employee_info_messy_input_returns_e1001_record(
    directory: EmployeeDirectory, raw: str
) -> None:
    assert get_employee_info(raw, directory=directory) == E1001_RECORD


def test_get_employee_info_as_of_sets_tenure(directory: EmployeeDirectory) -> None:
    result = get_employee_info("E1001", directory=directory, as_of=date(2022, 6, 14))

    assert result["tenure_months"] == 12


# ---------- get_employee_info: errors returned as data ----------


def test_get_employee_info_unknown_id_returns_not_found(directory: EmployeeDirectory) -> None:
    assert get_employee_info("E9999", directory=directory) == {
        "error": "EMPLOYEE_NOT_FOUND",
        "employee_id": "E9999",
    }


def test_get_employee_info_not_found_echoes_normalised_id(directory: EmployeeDirectory) -> None:
    # The format was valid, so the normalised ID is the useful thing to report back.
    assert get_employee_info(" e9999 ", directory=directory) == {
        "error": "EMPLOYEE_NOT_FOUND",
        "employee_id": "E9999",
    }


@pytest.mark.parametrize(
    "raw",
    ["abc", "", "E12", "E12345", "1001"],
    ids=["letters", "empty", "too-short", "too-long", "no-prefix"],
)
def test_get_employee_info_bad_format_returns_invalid_id_with_raw_input(
    directory: EmployeeDirectory, raw: str
) -> None:
    # The raw input is echoed unchanged so the model can see exactly what it sent.
    assert get_employee_info(raw, directory=directory) == {
        "error": "INVALID_EMPLOYEE_ID",
        "employee_id": raw,
    }

"""Tests for the mock employee data file and its validating loader (REQUIREMENTS §2.2, §3, §8)."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from it_requests import config
from it_requests.data import (
    CATALOG_ITEMS,
    ROLES,
    DataValidationError,
    load_employees,
)

# ---------- the real data file ----------


def test_load_employees_real_file_has_six_employees() -> None:
    assert len(load_employees(config.EMPLOYEES_FILE).employees) == 6


def test_load_employees_real_file_has_data_version_employees_v1() -> None:
    assert load_employees(config.EMPLOYEES_FILE).data_version == "employees-v1"


def test_directory_get_known_id_returns_every_equipment_item() -> None:
    # Dana holds two monitors; both must survive loading as separate items. A missing
    # employee would fail here too (None has no .equipment).
    employee = load_employees(config.EMPLOYEES_FILE).get("E1003")

    assert len(employee.equipment) == 4  # type: ignore[union-attr]


def test_directory_get_unknown_id_returns_none() -> None:
    assert load_employees(config.EMPLOYEES_FILE).get("E9999") is None


# ---------- invalid data fails loudly ----------
# Each case starts from one small valid file and breaks exactly one thing,
# so a failure points at the single rule that was not enforced.


def _valid_directory() -> dict[str, Any]:
    return {
        "data_version": "test-v1",
        "effective_date": "2026-11-22",
        "employees": [
            {
                "employee_id": "E1001",
                "name": "Test One",
                "role": "standard",
                "hire_date": "2021-06-14",
                "status": "active",
                "equipment": [
                    {"item": "laptop", "asset_id": "LT-0001", "issued_date": "2025-03-10"},
                ],
            },
            {
                "employee_id": "E1002",
                "name": "Test Two",
                "role": "engineer",
                "hire_date": "2022-01-10",
                "status": "active",
                "equipment": [
                    {"item": "monitor", "asset_id": "MN-0002", "issued_date": "2022-01-10"},
                ],
            },
        ],
    }


def _write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _duplicate_employee_id(d: dict[str, Any]) -> None:
    d["employees"][1]["employee_id"] = "E1001"


def _duplicate_asset_id(d: dict[str, Any]) -> None:
    d["employees"][1]["equipment"][0]["asset_id"] = "LT-0001"


def _bad_employee_id_format(d: dict[str, Any]) -> None:
    d["employees"][0]["employee_id"] = "1001"


def _non_ascii_digit_employee_id(d: dict[str, Any]) -> None:
    # Arabic-Indic digits: regex \d accepts them, but they are not valid E#### IDs and
    # would never match the ASCII ID the agent looks up.
    d["employees"][0]["employee_id"] = "E١٠٠١"


def _unknown_role(d: dict[str, Any]) -> None:
    d["employees"][0]["role"] = "intern"


def _unknown_status(d: dict[str, Any]) -> None:
    d["employees"][0]["status"] = "retired"


def _unknown_item(d: dict[str, Any]) -> None:
    d["employees"][0]["equipment"][0]["item"] = "standing_desk"


def _impossible_date(d: dict[str, Any]) -> None:
    d["employees"][0]["hire_date"] = "2026-13-01"


def _issued_before_hire(d: dict[str, Any]) -> None:
    d["employees"][0]["equipment"][0]["issued_date"] = "2021-06-13"


def _hire_after_effective_date(d: dict[str, Any]) -> None:
    d["employees"][0]["hire_date"] = "2026-11-23"


def _issued_after_effective_date(d: dict[str, Any]) -> None:
    # Hire date stays valid, so only the equipment-date check can catch this.
    d["employees"][0]["equipment"][0]["issued_date"] = "2026-11-23"


def _unknown_extra_field(d: dict[str, Any]) -> None:
    d["employees"][0]["salary"] = 100000


def _missing_employees_key(d: dict[str, Any]) -> None:
    del d["employees"]


@pytest.mark.parametrize(
    "break_data",
    [
        _duplicate_employee_id,
        _duplicate_asset_id,
        _bad_employee_id_format,
        _non_ascii_digit_employee_id,
        _unknown_role,
        _unknown_status,
        _unknown_item,
        _impossible_date,
        _issued_before_hire,
        _hire_after_effective_date,
        _issued_after_effective_date,
        _unknown_extra_field,
        _missing_employees_key,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_load_employees_invalid_data_raises(
    tmp_path: Path, break_data: Callable[[dict[str, Any]], None]
) -> None:
    data = copy.deepcopy(_valid_directory())
    break_data(data)
    path = _write_json(tmp_path / "employees.json", data)

    with pytest.raises(DataValidationError):
        load_employees(path)


def test_load_employees_valid_baseline_loads(tmp_path: Path) -> None:
    # Guards the parametrized test above: if the baseline itself were invalid,
    # every "invalid" case would pass for the wrong reason.
    path = _write_json(tmp_path / "employees.json", _valid_directory())

    assert len(load_employees(path).employees) == 2


def test_load_employees_missing_file_raises_naming_the_path(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"

    with pytest.raises(DataValidationError, match="does_not_exist.json"):
        load_employees(path)


def test_load_employees_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "employees.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(DataValidationError):
        load_employees(path)


# ---------- enum tuples ----------


def test_catalog_items_are_exactly_the_six_requirements_items() -> None:
    # REQUIREMENTS §3: only these six items can be approved automatically. Sorted lists
    # catch a missing, an extra and a duplicated item in one comparison.
    assert sorted(CATALOG_ITEMS) == sorted(
        ["laptop", "monitor", "docking_station", "headset", "keyboard_mouse", "mobile_phone"]
    )


def test_roles_are_exactly_the_four_requirements_roles() -> None:
    # REQUIREMENTS §2.2 / §4.1: exactly four roles.
    assert sorted(ROLES) == sorted(["standard", "engineer", "manager", "contractor"])

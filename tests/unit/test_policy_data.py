"""Tests for the role-entitlement policy file and its loader (REQUIREMENTS §4.1, §4.2, §7.2)."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from it_requests import config
from it_requests.data import DataValidationError, load_policy

# ---------- the real policy file ----------


def test_load_policy_real_file_has_data_version_policy_v1() -> None:
    assert load_policy(config.POLICY_FILE).data_version == "policy-v1"


def test_load_policy_real_file_has_four_roles() -> None:
    assert len(load_policy(config.POLICY_FILE).roles) == 4


def test_load_policy_real_file_has_six_items_per_role() -> None:
    roles = load_policy(config.POLICY_FILE).roles

    assert {role: len(items) for role, items in roles.items()} == {
        "standard": 6,
        "engineer": 6,
        "manager": 6,
        "contractor": 6,
    }


SPOT_CHECKS = [
    ("engineer", "monitor", 2, 36),
    ("standard", "docking_station", 1, "none"),  # entitled, no refresh cycle (R-LIMIT)
    ("contractor", "laptop", 0, None),  # not entitled (R-ROLE)
    ("manager", "mobile_phone", 1, 24),
]


def _spot_id(case: tuple[str, str, int, object]) -> str:
    return f"{case[0]}-{case[1]}"


@pytest.mark.parametrize("case", SPOT_CHECKS, ids=_spot_id)
def test_limits_for_returns_requirements_max(case: tuple[Any, Any, int, object]) -> None:
    role, item, max_units, _ = case

    assert load_policy(config.POLICY_FILE).limits_for(role)[item].max == max_units


@pytest.mark.parametrize("case", SPOT_CHECKS, ids=_spot_id)
def test_limits_for_returns_requirements_refresh_months(case: tuple[Any, Any, int, object]) -> None:
    role, item, _, refresh = case

    assert load_policy(config.POLICY_FILE).limits_for(role)[item].refresh_months == refresh


# ---------- invalid policy fails loudly ----------
# Each case deep-copies the real file and breaks exactly one thing, so a failure points
# at the single rule that was not enforced.


def _real_policy() -> dict[str, Any]:
    # Read at test time, not import time, so a missing file fails one test, not collection.
    data: dict[str, Any] = json.loads(config.POLICY_FILE.read_text(encoding="utf-8"))
    return data


def _missing_role(p: dict[str, Any]) -> None:
    del p["roles"]["contractor"]


def _role_missing_item(p: dict[str, Any]) -> None:
    del p["roles"]["standard"]["laptop"]


def _unknown_role(p: dict[str, Any]) -> None:
    p["roles"]["intern"] = copy.deepcopy(p["roles"]["standard"])


def _unknown_item(p: dict[str, Any]) -> None:
    p["roles"]["standard"]["standing_desk"] = {"max": 1, "refresh_months": 36}


def _negative_max(p: dict[str, Any]) -> None:
    p["roles"]["standard"]["laptop"]["max"] = -1


def _max_zero_with_refresh(p: dict[str, Any]) -> None:
    # Not entitled, yet a refresh interval: contradictory under the v1.1 meaning.
    p["roles"]["standard"]["mobile_phone"]["refresh_months"] = 24


def _entitled_with_null_refresh(p: dict[str, Any]) -> None:
    # null means "not entitled"; an entitled item must say 24/36 or "none".
    p["roles"]["standard"]["laptop"]["refresh_months"] = None


def _zero_refresh(p: dict[str, Any]) -> None:
    p["roles"]["standard"]["laptop"]["refresh_months"] = 0


def _refresh_never(p: dict[str, Any]) -> None:
    # Only the exact string "none" is allowed; near-misses must not slip through.
    p["roles"]["standard"]["docking_station"]["refresh_months"] = "never"


def _extra_field(p: dict[str, Any]) -> None:
    p["roles"]["standard"]["laptop"]["notes"] = "unexpected"


@pytest.mark.parametrize(
    "break_policy",
    [
        _missing_role,
        _role_missing_item,
        _unknown_role,
        _unknown_item,
        _negative_max,
        _max_zero_with_refresh,
        _entitled_with_null_refresh,
        _zero_refresh,
        _refresh_never,
        _extra_field,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_load_policy_invalid_policy_raises(
    tmp_path: Path, break_policy: Callable[[dict[str, Any]], None]
) -> None:
    data = _real_policy()
    break_policy(data)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DataValidationError):
        load_policy(path)


@pytest.mark.parametrize(
    ("break_policy", "named"),
    [(_missing_role, "contractor"), (_role_missing_item, "laptop")],
    ids=["missing-role", "missing-item"],
)
def test_load_policy_incomplete_policy_error_names_what_is_missing(
    tmp_path: Path, break_policy: Callable[[dict[str, Any]], None], named: str
) -> None:
    data = _real_policy()
    break_policy(data)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DataValidationError, match=named):
        load_policy(path)


def test_load_policy_missing_file_raises_naming_the_path(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="no_policy.json"):
        load_policy(tmp_path / "no_policy.json")


def test_load_policy_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(DataValidationError):
        load_policy(path)

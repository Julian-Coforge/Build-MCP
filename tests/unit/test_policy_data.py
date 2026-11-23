"""Tests for the role-entitlement policy file and its loader (REQUIREMENTS §4.1, §4.2, §7.2)."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from it_requests import config
from it_requests.data import DataValidationError, load_policy

# --- The real policy file ---------------------------------------------------------


def test_real_policy_file_loads_complete() -> None:
    policy = load_policy(config.POLICY_FILE)

    assert policy.data_version == "policy-v1"
    assert len(policy.roles) == 4
    assert all(len(items) == 6 for items in policy.roles.values())


@pytest.mark.parametrize(
    ("role", "item", "max_units", "refresh"),
    [
        ("engineer", "monitor", 2, 36),
        ("standard", "docking_station", 1, "none"),  # entitled, no refresh cycle (R-LIMIT)
        ("contractor", "laptop", 0, None),  # not entitled (R-ROLE)
        ("manager", "mobile_phone", 1, 24),
    ],
)
def test_limits_for_spot_checks(role: Any, item: Any, max_units: int, refresh: object) -> None:
    limit = load_policy(config.POLICY_FILE).limits_for(role)[item]

    assert limit.max == max_units
    assert limit.refresh_months == refresh


# --- Invalid policy fails loudly ----------------------------------------------------
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
def test_invalid_policy_raises(
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
)
def test_completeness_error_names_what_is_missing(
    tmp_path: Path, break_policy: Callable[[dict[str, Any]], None], named: str
) -> None:
    data = _real_policy()
    break_policy(data)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DataValidationError, match=named):
        load_policy(path)


def test_missing_file_raises_with_path(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="no_policy.json"):
        load_policy(tmp_path / "no_policy.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(DataValidationError):
        load_policy(path)

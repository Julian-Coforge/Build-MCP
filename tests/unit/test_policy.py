"""Tests for get_policy_limits and normalize_role (REQUIREMENTS §4.1, §7.2, §12.1)."""

import json

import pytest

from it_requests import config
from it_requests.data import CATALOG_ITEMS, PolicyTable, load_policy
from it_requests.policy import get_policy_limits, normalize_role


@pytest.fixture(scope="module")
def policy() -> PolicyTable:
    # Module scope: the table is frozen, so one load is safely shared by every test.
    return load_policy(config.POLICY_FILE)


# ---------- normalize_role ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("standard", "standard"),
        ("Manager", "manager"),
        (" ENGINEER ", "engineer"),
        ("Contractor", "contractor"),
        ("\tmanager\n", "manager"),
        ("intern", None),
        ("", None),
        ("   ", None),
        ("admin", None),
        ("managers", None),
        ("man ager", None),
    ],
    ids=[
        "lower",
        "title-case",
        "padded-upper",
        "contractor",
        "tab-newline",
        "unknown",
        "empty",
        "blank",
        "admin",
        "plural",
        "split-word",
    ],
)
def test_normalize_role_trims_lowercases_or_returns_none(raw: str, expected: str | None) -> None:
    assert normalize_role(raw) == expected


# ---------- get_policy_limits: success ----------


def test_get_policy_limits_standard_returns_whole_record(policy: PolicyTable) -> None:
    # The whole value: role, every item with exact limits, data_version, and no error key.
    assert get_policy_limits("standard", policy=policy) == {
        "role": "standard",
        "limits": {
            "laptop": {"max": 1, "refresh_months": 36},
            "monitor": {"max": 1, "refresh_months": 36},
            "docking_station": {"max": 1, "refresh_months": "none"},
            "headset": {"max": 1, "refresh_months": 24},
            "keyboard_mouse": {"max": 1, "refresh_months": 24},
            "mobile_phone": {"max": 0, "refresh_months": None},
        },
        "data_version": "policy-v1",
    }


def test_get_policy_limits_lists_items_in_catalog_order(policy: PolicyTable) -> None:
    # Same order as CATALOG_ITEMS on every call, so output is deterministic.
    assert list(get_policy_limits("standard", policy=policy)["limits"]) == list(CATALOG_ITEMS)


# max per item in CATALOG_ITEMS order (laptop, monitor, docking_station, headset,
# keyboard_mouse, mobile_phone), copied from the REQUIREMENTS §4.1 table.
EXPECTED_MAX = {
    "standard": [1, 1, 1, 1, 1, 0],
    "engineer": [1, 2, 1, 1, 1, 0],
    "manager": [1, 2, 1, 1, 1, 1],
    "contractor": [0, 0, 0, 1, 1, 0],
}


@pytest.mark.parametrize("role", list(EXPECTED_MAX), ids=str)
def test_get_policy_limits_role_max_matches_requirements_table(
    policy: PolicyTable, role: str
) -> None:
    # A list over all six items also catches a missing or extra item (length differs).
    limits = get_policy_limits(role, policy=policy)["limits"]

    assert [limits[item]["max"] for item in limits] == EXPECTED_MAX[role]


@pytest.mark.parametrize("item", ["laptop", "monitor", "docking_station"], ids=str)
def test_get_policy_limits_contractor_desk_hardware_is_not_entitled(
    policy: PolicyTable, item: str
) -> None:
    # null, not "none": "not entitled" (R-ROLE) must stay distinct from
    # "entitled, no refresh cycle" (R-LIMIT), so the model never infers it from max.
    limits = get_policy_limits("contractor", policy=policy)["limits"]

    assert limits[item] == {"max": 0, "refresh_months": None}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Manager", "manager"), (" ENGINEER ", "engineer"), ("Contractor", "contractor")],
    ids=["title-case", "padded-upper", "contractor"],
)
def test_get_policy_limits_mixed_case_role_returns_normalised_role(
    policy: PolicyTable, raw: str, expected: str
) -> None:
    assert get_policy_limits(raw, policy=policy)["role"] == expected


@pytest.mark.parametrize("raw", ["Manager", " ENGINEER ", "Contractor"], ids=str.strip)
def test_get_policy_limits_mixed_case_role_returns_limits(policy: PolicyTable, raw: str) -> None:
    assert "limits" in get_policy_limits(raw, policy=policy)


def test_get_policy_limits_json_writes_not_entitled_as_null(policy: PolicyTable) -> None:
    # The MCP layer sends this as JSON: it must serialise, and None must become null.
    text = json.dumps(get_policy_limits("standard", policy=policy))

    assert '"mobile_phone": {"max": 0, "refresh_months": null}' in text


def test_get_policy_limits_json_keeps_none_refresh_as_string(policy: PolicyTable) -> None:
    text = json.dumps(get_policy_limits("standard", policy=policy))

    assert '"docking_station": {"max": 1, "refresh_months": "none"}' in text


# ---------- get_policy_limits: errors returned as data ----------


@pytest.mark.parametrize("raw", ["intern", "", "admin"], ids=["intern", "empty", "admin"])
def test_get_policy_limits_unknown_role_returns_error_with_raw_input(
    policy: PolicyTable, raw: str
) -> None:
    # Raw input echoed unchanged, so the model sees exactly what it sent.
    assert get_policy_limits(raw, policy=policy) == {"error": "UNKNOWN_ROLE", "role": raw}

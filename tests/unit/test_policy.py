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


# --- normalize_role -----------------------------------------------------------------


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
)
def test_normalize_role(raw: str, expected: str | None) -> None:
    assert normalize_role(raw) == expected


# --- get_policy_limits: success ---------------------------------------------------------


def test_standard_returns_all_items_with_exact_values(policy: PolicyTable) -> None:
    result = get_policy_limits("standard", policy=policy)

    assert result["role"] == "standard"
    assert result["data_version"] == "policy-v1"
    # Same order as CATALOG_ITEMS on every call, so output is deterministic.
    assert list(result["limits"]) == list(CATALOG_ITEMS)
    assert result["limits"]["laptop"] == {"max": 1, "refresh_months": 36}
    assert result["limits"]["docking_station"] == {"max": 1, "refresh_months": "none"}
    assert result["limits"]["mobile_phone"] == {"max": 0, "refresh_months": None}
    assert "error" not in result


# max per item in CATALOG_ITEMS order (laptop, monitor, docking_station, headset,
# keyboard_mouse, mobile_phone), copied from the REQUIREMENTS §4.1 table.
EXPECTED_MAX = {
    "standard": [1, 1, 1, 1, 1, 0],
    "engineer": [1, 2, 1, 1, 1, 0],
    "manager": [1, 2, 1, 1, 1, 1],
    "contractor": [0, 0, 0, 1, 1, 0],
}


@pytest.mark.parametrize("role", list(EXPECTED_MAX))
def test_every_role_matches_requirements_table(policy: PolicyTable, role: str) -> None:
    limits = get_policy_limits(role, policy=policy)["limits"]

    assert len(limits) == 6
    assert [limits[item]["max"] for item in CATALOG_ITEMS] == EXPECTED_MAX[role]


def test_contractor_not_entitled_to_desk_hardware(policy: PolicyTable) -> None:
    limits = get_policy_limits("contractor", policy=policy)["limits"]

    for item in ("laptop", "monitor", "docking_station"):
        # null, not "none": "not entitled" (R-ROLE) must stay distinct from
        # "entitled, no refresh cycle" (R-LIMIT), so the model never infers it from max.
        assert limits[item] == {"max": 0, "refresh_months": None}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Manager", "manager"), (" ENGINEER ", "engineer"), ("Contractor", "contractor")],
)
def test_role_lookup_is_case_insensitive(policy: PolicyTable, raw: str, expected: str) -> None:
    result = get_policy_limits(raw, policy=policy)

    assert result["role"] == expected
    assert "limits" in result


def test_result_is_json_serialisable_with_null(policy: PolicyTable) -> None:
    # The MCP layer sends this as JSON: it must serialise, and None must become null.
    text = json.dumps(get_policy_limits("standard", policy=policy))

    assert '"mobile_phone": {"max": 0, "refresh_months": null}' in text
    assert '"docking_station": {"max": 1, "refresh_months": "none"}' in text


# --- get_policy_limits: errors returned as data -------------------------------------------


@pytest.mark.parametrize("raw", ["intern", "", "admin"])
def test_unknown_role_returns_error_with_raw_input(policy: PolicyTable, raw: str) -> None:
    # Raw input echoed unchanged, so the model sees exactly what it sent.
    assert get_policy_limits(raw, policy=policy) == {"error": "UNKNOWN_ROLE", "role": raw}

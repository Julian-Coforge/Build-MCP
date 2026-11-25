"""get_policy_limits called through the MCP protocol, in-process (no network)."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from mcp.types import CallToolResult, Tool

pytestmark = pytest.mark.anyio

CallTool = Callable[..., Awaitable[CallToolResult]]
ListTools = Callable[[], Awaitable[dict[str, Tool]]]
Records = Callable[[], list[dict[str, Any]]]

# REQUIREMENTS §7.2 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Return the equipment a role is entitled to, the maximum units per item, and the refresh "
    "interval in months. Use this to explain a decision or a denial."
)


async def test_policy_tool_description_matches_requirements(listed_tools: ListTools) -> None:
    tools = await listed_tools()

    assert tools["get_policy_limits"].description == EXPECTED_DESCRIPTION


async def test_policy_tool_engineer_call_is_not_an_error(call_tool: CallTool) -> None:
    result = await call_tool("get_policy_limits", {"role": "engineer"})

    assert result.is_error is False


async def test_policy_tool_engineer_gets_two_monitors(call_tool: CallTool) -> None:
    result = await call_tool("get_policy_limits", {"role": "engineer"})

    assert result.structured_content["limits"]["monitor"] == {"max": 2, "refresh_months": 36}


async def test_policy_tool_result_names_the_data_version(call_tool: CallTool) -> None:
    result = await call_tool("get_policy_limits", {"role": "engineer"})

    assert result.structured_content["data_version"] == "policy-v1"


async def test_policy_tool_mixed_case_role_returns_normalised_role(call_tool: CallTool) -> None:
    result = await call_tool("get_policy_limits", {"role": "Manager"})

    assert result.structured_content["role"] == "manager"


async def test_policy_tool_unknown_role_is_data_not_protocol_error(call_tool: CallTool) -> None:
    result = await call_tool("get_policy_limits", {"role": "intern"})

    # is_error stays False: an unknown role is an answer the model reasons about,
    # not a failure of the tool (REQUIREMENTS §7).
    assert (result.is_error, result.structured_content) == (
        False,
        {"error": "UNKNOWN_ROLE", "role": "intern"},
    )


# The two "no refresh" meanings must arrive distinct after JSON round-tripping:
# "none" = entitled, no refresh cycle (R-LIMIT); null = not entitled (R-ROLE).
@pytest.mark.parametrize(
    ("item", "refresh"),
    [("docking_station", "none"), ("mobile_phone", None)],
    ids=["none-string-survives", "null-survives"],
)
async def test_policy_tool_refresh_none_and_null_survive_the_protocol(
    call_tool: CallTool, item: str, refresh: str | None
) -> None:
    result = await call_tool("get_policy_limits", {"role": "standard"})

    assert result.structured_content["limits"][item]["refresh_months"] == refresh


async def test_policy_tool_call_logs_one_event(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    await call_tool("get_policy_limits", {"role": "engineer"})

    assert len(tool_call_records()) == 1


async def test_policy_tool_log_line_has_role_status_and_version(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    expected = {
        "tool": "get_policy_limits",
        "role": "engineer",
        "status": "ok",
        "data_version": "policy-v1",
    }

    await call_tool("get_policy_limits", {"role": "engineer"})
    (record,) = tool_call_records()

    assert {key: record[key] for key in expected} == expected

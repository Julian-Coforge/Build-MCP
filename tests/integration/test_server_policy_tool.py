"""get_policy_limits called through the MCP protocol, in-process (no network)."""

import io
import json

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio

# REQUIREMENTS §7.2 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Return the equipment a role is entitled to, the maximum units per item, and the refresh "
    "interval in months. Use this to explain a decision or a denial."
)


async def test_description_matches_requirements(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.list_tools()

    tool = next(t for t in result.tools if t.name == "get_policy_limits")
    assert tool.description == EXPECTED_DESCRIPTION


async def test_engineer_limits(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_policy_limits", {"role": "engineer"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["limits"]["monitor"] == {"max": 2, "refresh_months": 36}
    assert result.structured_content["data_version"] == "policy-v1"


async def test_mixed_case_role_works_through_protocol(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_policy_limits", {"role": "Manager"})

    assert result.structured_content is not None
    assert result.structured_content["role"] == "manager"


async def test_unknown_role_is_data_not_protocol_error(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_policy_limits", {"role": "intern"})

    # is_error stays False: an unknown role is an answer the model reasons about,
    # not a failure of the tool (REQUIREMENTS §7).
    assert result.is_error is False
    assert result.structured_content == {"error": "UNKNOWN_ROLE", "role": "intern"}


async def test_none_and_null_survive_the_protocol(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_policy_limits", {"role": "standard"})

    assert result.structured_content is not None
    limits = result.structured_content["limits"]
    # The two "no refresh" meanings must arrive distinct after JSON round-tripping:
    # "none" = entitled, no refresh cycle (R-LIMIT); null = not entitled (R-ROLE).
    assert limits["docking_station"]["refresh_months"] == "none"
    assert limits["mobile_phone"]["refresh_months"] is None


async def test_tool_call_logs_one_event(server: MCPServer, server_log: io.StringIO) -> None:
    async with Client(server) as client:
        await client.call_tool("get_policy_limits", {"role": "engineer"})

    records = [json.loads(line) for line in server_log.getvalue().splitlines()]
    tool_calls = [r for r in records if r["event"] == "tool.call"]
    assert len(tool_calls) == 1
    record = tool_calls[0]
    assert record["tool"] == "get_policy_limits"
    assert record["role"] == "engineer"
    assert record["status"] == "ok"
    assert record["data_version"] == "policy-v1"

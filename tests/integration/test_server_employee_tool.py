"""get_employee_info called through the MCP protocol, in-process (no network)."""

import io
import json
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio

# REQUIREMENTS §7.1 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Look up an employee's role, hire date, tenure, employment status, and the equipment "
    "currently issued to them. Use this first for any request about a specific employee."
)


async def test_lists_exactly_the_registered_tools(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.list_tools()

    # Sorted, so the test doesn't depend on registration order.
    assert sorted(t.name for t in result.tools) == [
        "check_request_eligibility",
        "get_employee_info",
        "get_policy_limits",
    ]
    tool = next(t for t in result.tools if t.name == "get_employee_info")
    assert tool.description == EXPECTED_DESCRIPTION


async def test_known_employee_returns_structured_record(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["employee_id"] == "E1001"
    assert result.structured_content["role"] == "standard"
    assert result.structured_content["tenure_months"] == 65


async def test_unknown_employee_is_data_not_protocol_error(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E9999"})

    # is_error stays False: "not found" is a normal answer the model reasons about
    # (ask again, or escalate under E1), not a failure of the tool itself (REQUIREMENTS §7).
    assert result.is_error is False
    assert result.structured_content == {"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"}


async def test_messy_id_normalises_through_protocol(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": " e1001 "})

    assert result.structured_content is not None
    assert result.structured_content["employee_id"] == "E1001"


def _tool_calls(stream: io.StringIO) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [r for r in records if r["event"] == "tool.call"]


async def test_tool_call_logs_one_event_without_name(
    server: MCPServer, server_log: io.StringIO
) -> None:
    async with Client(server) as client:
        await client.call_tool("get_employee_info", {"employee_id": "E1001"})

    tool_calls = _tool_calls(server_log)
    assert len(tool_calls) == 1
    record = tool_calls[0]
    assert record["tool"] == "get_employee_info"
    assert record["employee_id"] == "E1001"
    assert record["resolved_employee_id"] == "E1001"
    assert record["status"] == "ok"
    assert record["data_version"] == "employees-v1"
    assert isinstance(record["duration_ms"], int | float)
    # Employee names are never logged (CLAUDE.md, PLAN §5): check the raw text, not just keys.
    assert "Priya" not in server_log.getvalue()


@pytest.mark.parametrize(
    ("sent", "resolved", "status"),
    [
        # Raw input kept exactly as sent; resolved shows who it matched.
        (" e1001 ", "E1001", "ok"),
        # Any error resolves to nobody: null, not a guess.
        ("E9999", None, "EMPLOYEE_NOT_FOUND"),
        ("abc", None, "INVALID_EMPLOYEE_ID"),
    ],
)
async def test_log_shows_raw_and_resolved_employee_id(
    server: MCPServer, server_log: io.StringIO, sent: str, resolved: str | None, status: str
) -> None:
    async with Client(server) as client:
        await client.call_tool("get_employee_info", {"employee_id": sent})

    record = _tool_calls(server_log)[0]
    assert record["employee_id"] == sent
    assert record["resolved_employee_id"] == resolved
    assert record["status"] == status

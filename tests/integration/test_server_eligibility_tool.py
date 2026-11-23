"""check_request_eligibility called through the MCP protocol, in-process (no network)."""

import io
import json
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio

# REQUIREMENTS §7.3 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Decide whether one unit of a catalog item is within policy for this employee. Returns "
    "ELIGIBLE, NOT_ELIGIBLE, or NEEDS_REVIEW, with the rule applied. This is the only source "
    "of truth for eligibility."
)


async def _check(server: MCPServer, employee_id: str, item: str) -> Any:
    async with Client(server) as client:
        return await client.call_tool(
            "check_request_eligibility", {"employee_id": employee_id, "item": item}
        )


async def test_description_matches_requirements(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.list_tools()

    tool = next(t for t in result.tools if t.name == "check_request_eligibility")
    assert tool.description == EXPECTED_DESCRIPTION


async def test_t1_new_monitor_is_eligible(server: MCPServer) -> None:
    result = await _check(server, "E1002", "monitor")

    assert result.is_error is False
    assert result.structured_content["status"] == "ELIGIBLE"
    assert result.structured_content["rule_id"] == "R-NEW"


async def test_t2_laptop_refresh_not_due(server: MCPServer) -> None:
    result = await _check(server, "E1001", "laptop")

    assert result.structured_content["status"] == "NOT_ELIGIBLE"
    assert result.structured_content["rule_id"] == "R-REFRESH"
    assert result.structured_content["next_eligible_date"] == "2028-03-10"


async def test_on_leave_needs_review(server: MCPServer) -> None:
    result = await _check(server, "E1006", "monitor")

    assert result.structured_content["status"] == "NEEDS_REVIEW"
    assert result.structured_content["rule_id"] == "R-STATUS"


async def test_unknown_item_is_data_not_protocol_error(server: MCPServer) -> None:
    result = await _check(server, "E1001", "standing_desk")

    # is_error stays False: the model must see UNKNOWN_ITEM and escalate (E3),
    # not treat it as a broken tool (REQUIREMENTS §7).
    assert result.is_error is False
    assert result.structured_content == {"error": "UNKNOWN_ITEM", "item": "standing_desk"}


def _tool_calls(stream: io.StringIO) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [r for r in records if r["event"] == "tool.call"]


@pytest.mark.parametrize(
    ("employee_id", "item", "resolved", "status", "rule_id"),
    [
        # status is the verdict itself, not "ok", so logs show decisions at a glance.
        (" e1002 ", "monitor", "E1002", "ELIGIBLE", "R-NEW"),
        ("E1001", "laptop", "E1001", "NOT_ELIGIBLE", "R-REFRESH"),
        # Errors: status is the error code, and there is no rule or resolved employee.
        ("E1001", "standing_desk", None, "UNKNOWN_ITEM", None),
        ("E9999", "laptop", None, "EMPLOYEE_NOT_FOUND", None),
    ],
)
async def test_tool_call_log_line(
    server: MCPServer,
    server_log: io.StringIO,
    employee_id: str,
    item: str,
    resolved: str | None,
    status: str,
    rule_id: str | None,
) -> None:
    await _check(server, employee_id, item)

    tool_calls = _tool_calls(server_log)
    assert len(tool_calls) == 1
    record = tool_calls[0]
    assert record["tool"] == "check_request_eligibility"
    assert record["employee_id"] == employee_id  # raw, exactly as sent
    assert record["resolved_employee_id"] == resolved
    assert record["item"] == item
    assert record["status"] == status
    assert record["rule_id"] == rule_id
    # Both files decide the verdict, so the log names both (matches data_versions).
    assert record["data_version"] == {"employees": "employees-v1", "policy": "policy-v1"}


async def test_log_never_contains_employee_name(server: MCPServer, server_log: io.StringIO) -> None:
    await _check(server, "E1001", "laptop")

    # Check the raw text, not just keys: the name must not appear anywhere (CLAUDE.md).
    assert "Priya" not in server_log.getvalue()
    assert "Shah" not in server_log.getvalue()

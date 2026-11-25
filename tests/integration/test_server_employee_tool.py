"""get_employee_info called through the MCP protocol, in-process (no network)."""

import io
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from mcp.types import CallToolResult, Tool

pytestmark = pytest.mark.anyio

CallTool = Callable[..., Awaitable[CallToolResult]]
ListTools = Callable[[], Awaitable[dict[str, Tool]]]
Records = Callable[[], list[dict[str, Any]]]

# REQUIREMENTS §7.1 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Look up an employee's role, hire date, tenure, employment status, and the equipment "
    "currently issued to them. Use this first for any request about a specific employee."
)


async def test_server_lists_exactly_the_four_registered_tools(listed_tools: ListTools) -> None:
    tools = await listed_tools()

    # Sorted, so the test doesn't depend on registration order.
    assert sorted(tools) == [
        "check_request_eligibility",
        "flag_for_human_review",
        "get_employee_info",
        "get_policy_limits",
    ]


async def test_employee_tool_description_matches_requirements(listed_tools: ListTools) -> None:
    tools = await listed_tools()

    assert tools["get_employee_info"].description == EXPECTED_DESCRIPTION


async def test_employee_tool_known_employee_is_not_an_error(call_tool: CallTool) -> None:
    result = await call_tool("get_employee_info", {"employee_id": "E1001"})

    assert result.is_error is False


async def test_employee_tool_known_employee_returns_structured_record(
    call_tool: CallTool,
) -> None:
    expected = {"employee_id": "E1001", "role": "standard", "tenure_months": 65}

    result = await call_tool("get_employee_info", {"employee_id": "E1001"})
    content = result.structured_content

    assert {key: content[key] for key in expected} == expected


async def test_employee_tool_unknown_employee_is_data_not_protocol_error(
    call_tool: CallTool,
) -> None:
    result = await call_tool("get_employee_info", {"employee_id": "E9999"})

    # is_error stays False: "not found" is a normal answer the model reasons about
    # (ask again, or escalate under E1), not a failure of the tool itself (REQUIREMENTS §7).
    assert (result.is_error, result.structured_content) == (
        False,
        {"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"},
    )


async def test_employee_tool_messy_id_normalises_through_protocol(call_tool: CallTool) -> None:
    result = await call_tool("get_employee_info", {"employee_id": " e1001 "})

    assert result.structured_content["employee_id"] == "E1001"


async def test_employee_tool_call_logs_one_event(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    await call_tool("get_employee_info", {"employee_id": "E1001"})

    assert len(tool_call_records()) == 1


async def test_employee_tool_log_line_has_ids_status_and_version(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    expected = {
        "tool": "get_employee_info",
        "employee_id": "E1001",
        "resolved_employee_id": "E1001",
        "status": "ok",
        "data_version": "employees-v1",
    }

    await call_tool("get_employee_info", {"employee_id": "E1001"})
    (record,) = tool_call_records()

    assert {key: record[key] for key in expected} == expected


async def test_employee_tool_log_line_records_duration(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    await call_tool("get_employee_info", {"employee_id": "E1001"})
    (record,) = tool_call_records()

    assert isinstance(record["duration_ms"], int | float)


async def test_employee_tool_log_never_contains_employee_name(
    call_tool: CallTool, server_log: io.StringIO
) -> None:
    # Employee names are never logged (CLAUDE.md, PLAN §5): check the raw text, not just keys.
    await call_tool("get_employee_info", {"employee_id": "E1001"})

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
    ids=["messy-id", "not-found", "bad-format"],
)
async def test_employee_tool_log_shows_raw_and_resolved_employee_id(
    call_tool: CallTool,
    tool_call_records: Records,
    sent: str,
    resolved: str | None,
    status: str,
) -> None:
    await call_tool("get_employee_info", {"employee_id": sent})
    record = tool_call_records()[0]

    assert (record["employee_id"], record["resolved_employee_id"], record["status"]) == (
        sent,
        resolved,
        status,
    )

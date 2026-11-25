"""check_request_eligibility called through the MCP protocol, in-process (no network)."""

import io
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from mcp.types import CallToolResult, Tool

pytestmark = pytest.mark.anyio

CallTool = Callable[..., Awaitable[CallToolResult]]
ListTools = Callable[[], Awaitable[dict[str, Tool]]]
Records = Callable[[], list[dict[str, Any]]]

# REQUIREMENTS §7.3 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Decide whether one unit of a catalog item is within policy for this employee. Returns "
    "ELIGIBLE, NOT_ELIGIBLE, or NEEDS_REVIEW, with the rule applied. This is the only source "
    "of truth for eligibility."
)


async def _check(call_tool: CallTool, employee_id: str, item: str) -> CallToolResult:
    return await call_tool("check_request_eligibility", {"employee_id": employee_id, "item": item})


def _verdict(result: CallToolResult) -> tuple[Any, Any]:
    content = result.structured_content
    return content["status"], content["rule_id"]


async def test_eligibility_tool_description_matches_requirements(
    listed_tools: ListTools,
) -> None:
    tools = await listed_tools()

    assert tools["check_request_eligibility"].description == EXPECTED_DESCRIPTION


async def test_eligibility_tool_verdict_is_not_an_error(call_tool: CallTool) -> None:
    result = await _check(call_tool, "E1002", "monitor")

    assert result.is_error is False


@pytest.mark.parametrize(
    ("employee_id", "item", "verdict"),
    [
        ("E1002", "monitor", ("ELIGIBLE", "R-NEW")),  # T1
        ("E1001", "laptop", ("NOT_ELIGIBLE", "R-REFRESH")),  # T2
        ("E1006", "monitor", ("NEEDS_REVIEW", "R-STATUS")),  # on leave
    ],
    ids=["t1-new-monitor", "t2-refresh-not-due", "on-leave"],
)
async def test_eligibility_tool_returns_status_and_rule(
    call_tool: CallTool, employee_id: str, item: str, verdict: tuple[str, str]
) -> None:
    result = await _check(call_tool, employee_id, item)

    assert _verdict(result) == verdict


async def test_eligibility_tool_t2_names_next_eligible_date(call_tool: CallTool) -> None:
    result = await _check(call_tool, "E1001", "laptop")

    assert result.structured_content["next_eligible_date"] == "2028-03-10"


async def test_eligibility_tool_unknown_item_is_data_not_protocol_error(
    call_tool: CallTool,
) -> None:
    result = await _check(call_tool, "E1001", "standing_desk")

    # is_error stays False: the model must see UNKNOWN_ITEM and escalate (E3),
    # not treat it as a broken tool (REQUIREMENTS §7).
    assert (result.is_error, result.structured_content) == (
        False,
        {"error": "UNKNOWN_ITEM", "item": "standing_desk"},
    )


LOG_CASES = [
    # status is the verdict itself, not "ok", so logs show decisions at a glance.
    (" e1002 ", "monitor", "E1002", "ELIGIBLE", "R-NEW"),
    ("E1001", "laptop", "E1001", "NOT_ELIGIBLE", "R-REFRESH"),
    # Errors: status is the error code, and there is no rule or resolved employee.
    ("E1001", "standing_desk", None, "UNKNOWN_ITEM", None),
    ("E9999", "laptop", None, "EMPLOYEE_NOT_FOUND", None),
]
LOG_IDS = ["eligible-messy-id", "not-eligible", "unknown-item", "not-found"]


@pytest.mark.parametrize(
    ("employee_id", "item", "resolved", "status", "rule_id"), LOG_CASES, ids=LOG_IDS
)
async def test_eligibility_tool_log_line_records_the_call(
    call_tool: CallTool,
    tool_call_records: Records,
    employee_id: str,
    item: str,
    resolved: str | None,
    status: str,
    rule_id: str | None,
) -> None:
    expected = {
        "tool": "check_request_eligibility",
        "employee_id": employee_id,  # raw, exactly as sent
        "resolved_employee_id": resolved,
        "item": item,
        "status": status,
        "rule_id": rule_id,
        # Both files decide the verdict, so the log names both (matches data_versions).
        "data_version": {"employees": "employees-v1", "policy": "policy-v1"},
    }

    await _check(call_tool, employee_id, item)
    (record,) = tool_call_records()

    assert {key: record[key] for key in expected} == expected


@pytest.mark.parametrize("name_part", ["Priya", "Shah"], ids=["first-name", "surname"])
async def test_eligibility_tool_log_never_contains_employee_name(
    call_tool: CallTool, server_log: io.StringIO, name_part: str
) -> None:
    # Check the raw text, not just keys: the name must not appear anywhere (CLAUDE.md).
    await _check(call_tool, "E1001", "laptop")

    assert name_part not in server_log.getvalue()

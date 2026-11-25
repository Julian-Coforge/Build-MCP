"""flag_for_human_review called through the MCP protocol, in-process (no network)."""

import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, Tool

pytestmark = pytest.mark.anyio

CallTool = Callable[..., Awaitable[CallToolResult]]
ListTools = Callable[[], Awaitable[dict[str, Tool]]]
Records = Callable[[], list[dict[str, Any]]]

# REQUIREMENTS §7.4 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Escalate a request to a human reviewer. Use this when any escalation trigger applies or "
    "the decision is not clear-cut. Do not use it for requests that are clearly approved or "
    "denied."
)

T3_REQUEST = "My work phone was stolen from my car last night..."
T3_REASON = "E6: Employee reports company phone stolen."


async def _flag(call_tool: CallTool, reason: str = T3_REASON) -> CallToolResult:
    return await call_tool(
        "flag_for_human_review",
        {"employee_id": "E1003", "request": T3_REQUEST, "reason": reason},
    )


def _tickets(queue_path: Path) -> list[dict[str, Any]]:
    tickets: list[dict[str, Any]] = json.loads(queue_path.read_text(encoding="utf-8"))["tickets"]
    return tickets


async def test_review_tool_description_matches_requirements(listed_tools: ListTools) -> None:
    tools = await listed_tools()

    assert tools["flag_for_human_review"].description == EXPECTED_DESCRIPTION


async def test_review_tool_t3_flag_returns_pending_ticket(call_tool: CallTool) -> None:
    result = await _flag(call_tool)

    assert (result.is_error, result.structured_content) == (
        False,
        {"review_id": "REV-0001", "status": "pending", "reason_code": "E6", "duplicate": False},
    )


async def test_review_tool_t3_flag_writes_one_ticket(
    call_tool: CallTool, review_queue_path: Path
) -> None:
    await _flag(call_tool)

    assert len(_tickets(review_queue_path)) == 1


async def test_review_tool_ticket_uses_the_injected_clock(
    call_tool: CallTool, review_queue_path: Path
) -> None:
    # The injected clock reaches the ticket, so the server never needs the real time in tests.
    await _flag(call_tool)

    assert _tickets(review_queue_path)[0]["created_at"] == "2026-11-22T09:00:00+00:00"


async def test_review_tool_repeat_flag_returns_the_same_ticket(call_tool: CallTool) -> None:
    await _flag(call_tool)

    again = await _flag(call_tool)

    assert (again.structured_content["review_id"], again.structured_content["duplicate"]) == (
        "REV-0001",
        True,
    )


async def test_review_tool_repeat_flag_writes_no_new_ticket(
    call_tool: CallTool, review_queue_path: Path
) -> None:
    await _flag(call_tool)

    await _flag(call_tool)

    assert len(_tickets(review_queue_path)) == 1


async def test_review_tool_invalid_reason_is_data_not_protocol_error(
    call_tool: CallTool,
) -> None:
    result = await _flag(call_tool, reason="phone stolen")

    # is_error stays False: the model must read INVALID_REASON and retry with a code.
    assert (result.is_error, result.structured_content["error"]) == (False, "INVALID_REASON")


async def test_review_tool_invalid_reason_writes_nothing(
    call_tool: CallTool, review_queue_path: Path
) -> None:
    await _flag(call_tool, reason="phone stolen")

    assert not review_queue_path.exists()


async def test_review_tool_log_line_has_ticket_fields(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    expected = {
        "tool": "flag_for_human_review",
        "employee_id": "E1003",
        "reason_code": "E6",
        "review_id": "REV-0001",
        "duplicate": False,
        "status": "ok",
    }

    await _flag(call_tool)
    (record,) = tool_call_records()

    assert {key: record[key] for key in expected} == expected


async def test_review_tool_error_log_line_has_error_status_and_no_ticket(
    call_tool: CallTool, tool_call_records: Records
) -> None:
    await _flag(call_tool, reason="phone stolen")
    record = tool_call_records()[0]

    assert (record["status"], record["review_id"]) == ("INVALID_REASON", None)


@pytest.mark.parametrize("reason", [T3_REASON, "phone stolen"], ids=["success", "rejected"])
async def test_review_tool_log_never_contains_request_text(
    call_tool: CallTool, server_log: io.StringIO, reason: str
) -> None:
    # PLAN §5: raw request text is never logged at INFO. Check the raw text, not just keys.
    await _flag(call_tool, reason=reason)

    assert "stolen from my car" not in server_log.getvalue()

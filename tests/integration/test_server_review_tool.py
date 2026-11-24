"""flag_for_human_review called through the MCP protocol, in-process (no network)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio

# REQUIREMENTS §7.4 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Escalate a request to a human reviewer. Use this when any escalation trigger applies or "
    "the decision is not clear-cut. Do not use it for requests that are clearly approved or "
    "denied."
)

T3_REQUEST = "My work phone was stolen from my car last night..."
T3_REASON = "E6: Employee reports company phone stolen."


async def _flag(server: MCPServer, employee_id: str, request: str, reason: str) -> Any:
    async with Client(server) as client:
        return await client.call_tool(
            "flag_for_human_review",
            {"employee_id": employee_id, "request": request, "reason": reason},
        )


def _tickets(queue_path: Path) -> list[dict[str, Any]]:
    tickets: list[dict[str, Any]] = json.loads(queue_path.read_text(encoding="utf-8"))["tickets"]
    return tickets


async def test_description_matches_requirements(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.list_tools()

    tool = next(t for t in result.tools if t.name == "flag_for_human_review")
    assert tool.description == EXPECTED_DESCRIPTION


async def test_t3_flag_creates_ticket(server: MCPServer, review_queue_path: Path) -> None:
    result = await _flag(server, "E1003", T3_REQUEST, T3_REASON)

    assert result.is_error is False
    assert result.structured_content["review_id"] == "REV-0001"
    assert result.structured_content["status"] == "pending"
    assert result.structured_content["reason_code"] == "E6"
    tickets = _tickets(review_queue_path)
    assert len(tickets) == 1
    # The injected clock reaches the ticket, so the server never needs the real time in tests.
    assert tickets[0]["created_at"] == "2026-11-22T09:00:00+00:00"


async def test_repeat_flag_is_idempotent(server: MCPServer, review_queue_path: Path) -> None:
    await _flag(server, "E1003", T3_REQUEST, T3_REASON)

    again = await _flag(server, "E1003", T3_REQUEST, T3_REASON)

    assert again.structured_content["review_id"] == "REV-0001"
    assert again.structured_content["duplicate"] is True
    assert len(_tickets(review_queue_path)) == 1


async def test_invalid_reason_is_data_and_writes_nothing(
    server: MCPServer, review_queue_path: Path
) -> None:
    result = await _flag(server, "E1003", T3_REQUEST, "phone stolen")

    # is_error stays False: the model must read INVALID_REASON and retry with a code.
    assert result.is_error is False
    assert result.structured_content["error"] == "INVALID_REASON"
    assert not review_queue_path.exists()


def _tool_calls(stream: io.StringIO) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [r for r in records if r["event"] == "tool.call"]


async def test_log_line_has_ticket_fields_but_no_request_text(
    server: MCPServer, server_log: io.StringIO
) -> None:
    await _flag(server, "E1003", T3_REQUEST, T3_REASON)

    tool_calls = _tool_calls(server_log)
    assert len(tool_calls) == 1
    record = tool_calls[0]
    assert record["tool"] == "flag_for_human_review"
    assert record["employee_id"] == "E1003"
    assert record["reason_code"] == "E6"
    assert record["review_id"] == "REV-0001"
    assert record["duplicate"] is False
    assert record["status"] == "ok"
    # PLAN §5: raw request text is never logged at INFO. Check the raw text, not just keys.
    assert "stolen from my car" not in server_log.getvalue()


async def test_error_log_line_has_error_status(server: MCPServer, server_log: io.StringIO) -> None:
    await _flag(server, "E1003", T3_REQUEST, "phone stolen")

    record = _tool_calls(server_log)[0]
    assert record["status"] == "INVALID_REASON"
    assert record["review_id"] is None
    assert "stolen from my car" not in server_log.getvalue()

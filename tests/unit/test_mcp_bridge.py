"""Tests for the MCP-to-Claude bridge: tool definitions and tool calls (PLAN §2, §5)."""

from typing import Any

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from it_requests.agent.mcp_bridge import (
    call_tool,
    span_safe_arguments,
    to_claude_tools,
    traceparent,
)

SCHEMA = {
    "type": "object",
    "properties": {"employee_id": {"type": "string"}},
    "required": ["employee_id"],
}


# ---------- to_claude_tools ----------


def test_to_claude_tools_maps_name_description_and_schema() -> None:
    listed = ListToolsResult(
        tools=[
            Tool(name="get_employee_info", description="Look up an employee.", input_schema=SCHEMA),
            Tool(name="get_policy_limits", description="Role limits.", input_schema=SCHEMA),
        ]
    )

    assert to_claude_tools(listed) == [
        {
            "name": "get_employee_info",
            "description": "Look up an employee.",
            "input_schema": SCHEMA,
        },
        {"name": "get_policy_limits", "description": "Role limits.", "input_schema": SCHEMA},
    ]


def test_to_claude_tools_drops_mcp_only_fields() -> None:
    # Claude's tool definition has no title or output_schema; sending them would be a 400.
    listed = ListToolsResult(
        tools=[
            Tool(
                name="t",
                title="Title",
                description="d",
                input_schema=SCHEMA,
                output_schema={"type": "object"},
            )
        ]
    )

    assert set(to_claude_tools(listed)[0]) == {"name", "description", "input_schema"}


def test_to_claude_tools_missing_description_becomes_empty_string() -> None:
    listed = ListToolsResult(tools=[Tool(name="t", input_schema=SCHEMA)])

    assert to_claude_tools(listed)[0]["description"] == ""


# ---------- call_tool ----------


class FakeClient:
    """Records call_tool arguments and returns one canned CallToolResult."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any]
    ) -> CallToolResult:
        self.calls.append({"name": name, "arguments": arguments, "meta": meta})
        return self.result


TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def _ok_client() -> FakeClient:
    return FakeClient(
        CallToolResult(content=[], structured_content={"employee_id": "E1002"}, is_error=False)
    )


async def _call_e1002(client: FakeClient) -> dict[str, Any]:
    return await call_tool(
        client,
        "get_employee_info",
        {"employee_id": "E1002"},
        request_id="REQ-0501",
        traceparent=TRACEPARENT,
    )


@pytest.mark.anyio
async def test_call_tool_returns_structured_content() -> None:
    assert await _call_e1002(_ok_client()) == {"employee_id": "E1002"}


@pytest.mark.anyio
async def test_call_tool_sends_request_id_and_traceparent_in_meta() -> None:
    # request_id and the W3C traceparent ride in _meta, so the server can join them to its
    # logs (PLAN §5).
    client = _ok_client()

    await _call_e1002(client)

    assert client.calls == [
        {
            "name": "get_employee_info",
            "arguments": {"employee_id": "E1002"},
            "meta": {"request_id": "REQ-0501", "traceparent": TRACEPARENT},
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        # The tool itself failed (an exception in the server, not an error dict).
        CallToolResult(
            content=[TextContent(type="text", text="boom")], structured_content=None, is_error=True
        ),
        # is_error set even though content came back: still a failure.
        CallToolResult(content=[], structured_content={"x": 1}, is_error=True),
        # No structured content: nothing reliable to reason about.
        CallToolResult(content=[TextContent(type="text", text="text only")], is_error=False),
    ],
    ids=["server-exception", "is-error-with-content", "no-structured-content"],
)
async def test_call_tool_failed_call_is_returned_as_data(result: CallToolResult) -> None:
    client = FakeClient(result)

    out = await call_tool(
        client, "get_employee_info", {}, request_id="REQ-0501", traceparent=TRACEPARENT
    )

    # Errors as data (CLAUDE.md rule 6): the agent sees TOOL_CALL_FAILED and can escalate
    # (E11) instead of crashing.
    assert out == {"error": "TOOL_CALL_FAILED", "tool": "get_employee_info"}


@pytest.mark.anyio
async def test_call_tool_tool_error_dict_passes_through_unchanged() -> None:
    # A tool's own error (e.g. EMPLOYEE_NOT_FOUND) is a normal answer, not a failed call.
    client = FakeClient(
        CallToolResult(
            content=[],
            structured_content={"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"},
            is_error=False,
        )
    )

    out = await call_tool(
        client, "get_employee_info", {"employee_id": "E9999"}, request_id="R", traceparent="t"
    )

    assert out == {"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"}


# ---------- traceparent ----------


def test_traceparent_uses_w3c_format_with_padded_hex_ids() -> None:
    # version-trace_id(32 hex)-parent_span_id(16 hex)-flags, 01 = sampled.
    assert traceparent(1, 2) == "00-00000000000000000000000000000001-0000000000000002-01"


# ---------- span_safe_arguments ----------


def test_span_safe_arguments_redacts_free_text_request_and_reason() -> None:
    # request and reason are the employee's own words and can hold names or medical details.
    arguments = {"employee_id": "E1005", "request": "My doctor said...", "reason": "E9: back pain"}

    assert span_safe_arguments(arguments) == (
        '{"employee_id": "E1005", "reason": "[REDACTED]", "request": "[REDACTED]"}'
    )

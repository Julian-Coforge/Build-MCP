"""The bridge between the MCP server and Claude: tool definitions in, tool calls out."""

import json
from typing import Any, Protocol

from mcp.types import CallToolResult, ListToolsResult

# Free-text tool arguments that can hold names or other personal details. Kept out of spans
# for the same reason the server never logs them (PLAN §5): flag_for_human_review's
# `request` is the employee's own words.
FREE_TEXT_ARGUMENTS = frozenset({"request", "reason"})


def traceparent(trace_id: int, span_id: int) -> str:
    """W3C trace context: version-trace_id-parent_span_id-flags (01 = sampled)."""
    return f"00-{trace_id:032x}-{span_id:016x}-01"


def span_safe_arguments(arguments: dict[str, Any]) -> str:
    """Arguments as a JSON string (span attributes must be flat), free text redacted."""
    safe = {k: "[REDACTED]" if k in FREE_TEXT_ARGUMENTS else v for k, v in arguments.items()}
    return json.dumps(safe, sort_keys=True)


class MCPToolClient(Protocol):
    """The two MCP client calls the agent uses. mcp.Client fits; tests can pass a spy."""

    async def list_tools(self) -> ListToolsResult: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any]
    ) -> CallToolResult: ...


def to_claude_tools(listed: ListToolsResult) -> list[dict[str, Any]]:
    """Turn the server's tool list into Claude tool definitions. Pure.

    Only name, description and input_schema: Claude's tool definition has no title or
    output_schema, and unknown fields would be rejected. The descriptions are the server's
    own, so the model reads the REQUIREMENTS §7 text word for word.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in listed.tools
    ]


async def call_tool(
    client: MCPToolClient,
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: str,
    traceparent: str,
) -> dict[str, Any]:
    """Call one server tool and return its structured result.

    request_id and the W3C traceparent go in the request's _meta, so the server can join
    them to its logs and spans (PLAN §5; SDK 2.3 accepts `meta`, checked in M5). The server
    reading them is a later step.
    A failed call (is_error, or no structured content) becomes
    {"error": "TOOL_CALL_FAILED", ...}: errors are data the agent can escalate on (E11),
    never an exception (CLAUDE.md rule 6). A tool's own error dict passes through unchanged.
    """
    result = await client.call_tool(
        name, arguments, meta={"request_id": request_id, "traceparent": traceparent}
    )
    content = result.structured_content
    if result.is_error or not isinstance(content, dict):
        return {"error": "TOOL_CALL_FAILED", "tool": name}
    return content

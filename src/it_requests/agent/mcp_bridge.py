"""The bridge between the MCP server and Claude: tool definitions in, tool calls out."""

from typing import Any, Protocol

from mcp.types import CallToolResult, ListToolsResult


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

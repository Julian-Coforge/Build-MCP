"""The server reads request_id from each call's _meta into its log lines (PLAN §5).

Closes the M5 gap: the agent has sent request_id in _meta since M5, but the server's
tool.call lines logged request_id null. The tool signatures the model sees must not change.
"""

import io
import json
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from it_requests.observability import request_id_var

pytestmark = pytest.mark.anyio

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_employee_info", {"employee_id": "E1002"}),
    ("get_policy_limits", {"role": "engineer"}),
    ("check_request_eligibility", {"employee_id": "E1002", "item": "monitor"}),
    (
        "flag_for_human_review",
        {"employee_id": "E1003", "request": "Phone stolen.", "reason": "E6: Phone stolen."},
    ),
]

# What the model sees: exactly the REQUIREMENTS §7 arguments and descriptions.
EXPECTED_TOOLS = {
    "get_employee_info": (
        ["employee_id"],
        "Look up an employee's role, hire date, tenure, employment status, and the equipment "
        "currently issued to them. Use this first for any request about a specific employee.",
    ),
    "get_policy_limits": (
        ["role"],
        "Return the equipment a role is entitled to, the maximum units per item, and the "
        "refresh interval in months. Use this to explain a decision or a denial.",
    ),
    "check_request_eligibility": (
        ["employee_id", "item"],
        "Decide whether one unit of a catalog item is within policy for this employee. "
        "Returns ELIGIBLE, NOT_ELIGIBLE, or NEEDS_REVIEW, with the rule applied. This is the "
        "only source of truth for eligibility.",
    ),
    "flag_for_human_review": (
        ["employee_id", "request", "reason"],
        "Escalate a request to a human reviewer. Use this when any escalation trigger applies "
        "or the decision is not clear-cut. Do not use it for requests that are clearly "
        "approved or denied.",
    ),
}


def _tool_calls(log: io.StringIO) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in log.getvalue().splitlines()]
    return [r for r in records if r["event"] == "tool.call"]


@pytest.mark.parametrize(("tool", "arguments"), CALLS, ids=[c[0] for c in CALLS])
async def test_server_meta_request_id_is_logged(
    server: MCPServer, server_log: io.StringIO, tool: str, arguments: dict[str, Any]
) -> None:
    async with Client(server) as client:
        await client.call_tool(tool, arguments, meta={"request_id": "REQ-0042"})

    (record,) = _tool_calls(server_log)
    assert record["request_id"] == "REQ-0042"


@pytest.mark.parametrize(("tool", "arguments"), CALLS, ids=[c[0] for c in CALLS])
async def test_server_no_meta_logs_null_request_id(
    server: MCPServer, server_log: io.StringIO, tool: str, arguments: dict[str, Any]
) -> None:
    async with Client(server) as client:
        await client.call_tool(tool, arguments)

    (record,) = _tool_calls(server_log)
    assert record["request_id"] is None


async def test_server_request_id_does_not_leak_into_the_next_call(
    server: MCPServer, server_log: io.StringIO
) -> None:
    # Set only for the length of one call: a later call without meta must not inherit it.
    async with Client(server) as client:
        await client.call_tool(
            "get_employee_info", {"employee_id": "E1002"}, meta={"request_id": "REQ-0042"}
        )
        await client.call_tool("get_employee_info", {"employee_id": "E1002"})

    assert [r["request_id"] for r in _tool_calls(server_log)] == ["REQ-0042", None]


async def test_request_id_var_is_reset_after_the_call(server: MCPServer) -> None:
    async with Client(server) as client:
        await client.call_tool(
            "get_employee_info", {"employee_id": "E1002"}, meta={"request_id": "REQ-0042"}
        )

    assert request_id_var.get() is None


async def test_server_non_string_request_id_is_ignored(
    server: MCPServer, server_log: io.StringIO
) -> None:
    # _meta comes from the client: only a string is trusted as a correlation ID.
    async with Client(server) as client:
        await client.call_tool(
            "get_employee_info", {"employee_id": "E1002"}, meta={"request_id": 42}
        )

    (record,) = _tool_calls(server_log)
    assert record["request_id"] is None


async def _listed(server: MCPServer) -> list[Any]:
    async with Client(server) as client:
        return list((await client.list_tools()).tools)


async def test_tool_signatures_and_descriptions_are_unchanged(server: MCPServer) -> None:
    listed = await _listed(server)

    seen = {tool.name: (list(tool.input_schema["properties"]), tool.description) for tool in listed}

    assert seen == EXPECTED_TOOLS


async def test_tool_required_arguments_are_unchanged(server: MCPServer) -> None:
    # Every argument stays required: nothing about _meta leaks into the model's schema.
    listed = await _listed(server)

    required = {tool.name: tool.input_schema["required"] for tool in listed}

    assert required == {name: arguments for name, (arguments, _) in EXPECTED_TOOLS.items()}

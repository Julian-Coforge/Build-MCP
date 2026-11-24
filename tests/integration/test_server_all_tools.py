"""All four tools working together through the real MCP protocol, before the agent exists.

Each test follows the tool sequence the REQUIREMENTS §6 decision table requires for one
demo request, over one client session, as the agent will.
"""

from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio


async def test_t2_deny_path(server: MCPServer) -> None:
    # T2: E1001 asks for a new laptop. Deny (row 4) needs employee info, the eligibility
    # verdict and the policy limits to cite.
    async with Client(server) as client:
        employee = await client.call_tool("get_employee_info", {"employee_id": "E1001"})
        verdict = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1001", "item": "laptop"}
        )
        limits = await client.call_tool(
            "get_policy_limits", {"role": employee.structured_content["role"]}
        )

    assert employee.structured_content["role"] == "standard"
    assert verdict.structured_content["status"] == "NOT_ELIGIBLE"
    assert verdict.structured_content["rule_id"] == "R-REFRESH"
    assert limits.structured_content["limits"]["laptop"]["refresh_months"] == 36


async def test_t3_escalate_path(server: MCPServer, review_queue_path: Path) -> None:
    # T3: E1003 reports a stolen phone. Escalate (row 1, E6) needs one flag call.
    async with Client(server) as client:
        employee = await client.call_tool("get_employee_info", {"employee_id": "E1003"})
        flag = await client.call_tool(
            "flag_for_human_review",
            {
                "employee_id": employee.structured_content["employee_id"],
                "request": "My work phone was stolen from my car last night...",
                "reason": "E6: Employee reports company phone stolen.",
            },
        )

    assert employee.structured_content["status"] == "active"
    assert flag.structured_content["review_id"] == "REV-0001"
    assert review_queue_path.exists()

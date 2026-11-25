"""All four tools working together through the real MCP protocol, before the agent exists.

Each test follows the tool sequence the REQUIREMENTS §6 decision table requires for one
demo request, over one client session, as the agent will.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio

T3_REQUEST = "My work phone was stolen from my car last night..."


async def _t2_deny_path(server: MCPServer) -> dict[str, Any]:
    # T2: E1001 asks for a new laptop. Deny (row 4) needs employee info, the eligibility
    # verdict and the policy limits to cite; the role from step 1 feeds step 3.
    async with Client(server) as client:
        employee = await client.call_tool("get_employee_info", {"employee_id": "E1001"})
        verdict = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1001", "item": "laptop"}
        )
        limits = await client.call_tool(
            "get_policy_limits", {"role": employee.structured_content["role"]}
        )
    return {
        "employee": employee.structured_content,
        "verdict": verdict.structured_content,
        "limits": limits.structured_content,
    }


async def _t3_escalate_path(server: MCPServer) -> dict[str, Any]:
    # T3: E1003 reports a stolen phone. Escalate (row 1, E6) needs one flag call; the ID
    # from the lookup feeds the flag.
    async with Client(server) as client:
        employee = await client.call_tool("get_employee_info", {"employee_id": "E1003"})
        flag = await client.call_tool(
            "flag_for_human_review",
            {
                "employee_id": employee.structured_content["employee_id"],
                "request": T3_REQUEST,
                "reason": "E6: Employee reports company phone stolen.",
            },
        )
    return {"employee": employee.structured_content, "flag": flag.structured_content}


async def test_t2_deny_path_ends_not_eligible_under_r_refresh(server: MCPServer) -> None:
    path = await _t2_deny_path(server)

    assert (path["verdict"]["status"], path["verdict"]["rule_id"]) == ("NOT_ELIGIBLE", "R-REFRESH")


async def test_t2_deny_path_looks_up_the_standard_role_limits(server: MCPServer) -> None:
    path = await _t2_deny_path(server)

    assert (path["employee"]["role"], path["limits"]["limits"]["laptop"]["refresh_months"]) == (
        "standard",
        36,
    )


async def test_t3_escalate_path_flags_an_active_employee(server: MCPServer) -> None:
    path = await _t3_escalate_path(server)

    assert (path["employee"]["status"], path["flag"]["review_id"]) == ("active", "REV-0001")


async def test_t3_escalate_path_writes_the_queue_file(
    server: MCPServer, review_queue_path: Path
) -> None:
    await _t3_escalate_path(server)

    assert review_queue_path.exists()

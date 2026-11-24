"""Call the running MCP server over streamable HTTP (screenshot for deliverable #2).

Start the server first with `make serve`, then run `make client-demo` in a second terminal.
"""

import asyncio
import json

from mcp import Client

SERVER_URL = "http://127.0.0.1:8000/mcp"

# A normal ID, a messy ID that must normalise, and an unknown ID that returns an error as data.
REQUESTS = ["E1001", " e1001 ", "E9999"]

# A plain role, a mixed-case role (lookup is case-insensitive) and an unknown role.
ROLE_REQUESTS = ["standard", "Manager", "intern"]

# One case per kind of verdict: new unit (R-NEW), refresh not due (R-REFRESH), replacement
# with the unit to return (R-REFRESH-OK), on leave (R-STATUS) and an unknown item.
ELIGIBILITY_REQUESTS = [
    ("E1002", "monitor"),
    ("E1001", "laptop"),
    ("E1003", "monitor"),
    ("E1006", "monitor"),
    ("E1001", "standing_desk"),
]

# A new ticket, the identical call again (idempotent: same review_id, duplicate true) and a
# reason without an escalation code (rejected, nothing written). Run `make reset-reviews`
# first so the first ticket is REV-0001.
STOLEN_PHONE = {
    "employee_id": "E1003",
    "request": "My work phone was stolen from my car last night, I need a replacement.",
    "reason": "E6: Employee reports company phone stolen.",
}
REVIEW_REQUESTS = [STOLEN_PHONE, STOLEN_PHONE, {**STOLEN_PHONE, "reason": "phone stolen"}]


def heading(text: str) -> None:
    print(f"\n{'=' * 60}\n{text}\n{'=' * 60}")


async def main() -> None:
    async with Client(SERVER_URL) as client:
        heading(f"Connected to {SERVER_URL}")
        tools = await client.list_tools()
        print("Tools:", ", ".join(t.name for t in tools.tools))

        for employee_id in REQUESTS:
            # repr() shows the spaces in " e1001 ", so the normalisation is visible.
            heading(f"get_employee_info(employee_id={employee_id!r})")
            result = await client.call_tool("get_employee_info", {"employee_id": employee_id})
            print(json.dumps(result.structured_content, indent=2))

        for role in ROLE_REQUESTS:
            heading(f"get_policy_limits(role={role!r})")
            result = await client.call_tool("get_policy_limits", {"role": role})
            print(json.dumps(result.structured_content, indent=2))

        for employee_id, item in ELIGIBILITY_REQUESTS:
            heading(f"check_request_eligibility(employee_id={employee_id!r}, item={item!r})")
            result = await client.call_tool(
                "check_request_eligibility", {"employee_id": employee_id, "item": item}
            )
            print(json.dumps(result.structured_content, indent=2))

        for args in REVIEW_REQUESTS:
            heading(
                f"flag_for_human_review(employee_id={args['employee_id']!r}, "
                f"reason={args['reason']!r})"
            )
            result = await client.call_tool("flag_for_human_review", args)
            print(json.dumps(result.structured_content, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

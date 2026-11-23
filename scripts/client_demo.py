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


if __name__ == "__main__":
    asyncio.run(main())

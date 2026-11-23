import asyncio

from mcp import Client


async def main() -> None:
    async with Client("http://localhost:8000/mcp") as client:
        result = await client.call_tool("get_employee_id", {"employee_id": 1234})
        print(result.structured_content)  # {'result': 1234}


asyncio.run(main())

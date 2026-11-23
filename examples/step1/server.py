from mcp.server import MCPServer

mcp = MCPServer("Demo")


@mcp.tool()
def get_employee_id(employee_id: int) -> int:
    """Return the employee ID."""
    return employee_id


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

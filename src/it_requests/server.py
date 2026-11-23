"""The MCP server: thin wrappers that expose the pure functions as tools.

No business logic lives here. Each tool calls a tested function, times it and logs one
event, so the protocol layer can't drift from the rules the unit tests pin down.
"""

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from it_requests import config, employees
from it_requests.data import EmployeeDirectory, load_employees
from it_requests.observability import configure_logging, log_event

HOST = "127.0.0.1"  # Loopback only: the mock server must never be reachable from the network.
PORT = 8000

logger = logging.getLogger("server")


def build_server(directory: EmployeeDirectory, *, as_of: date = config.REFERENCE_DATE) -> MCPServer:
    """Build the server around injected data.

    A factory, not a module-level server: tests pass in their own data, and importing this
    module never reads files or opens ports.
    """
    server = MCPServer("it-requests")

    # Return type must be dict[str, Any], not bare dict: with bare dict the SDK sends only
    # text and no structured_content (verified on mcp 2.3.0).
    @server.tool()
    def get_employee_info(employee_id: str) -> dict[str, Any]:
        """Look up an employee's role, hire date, tenure, employment status, and the equipment currently issued to them. Use this first for any request about a specific employee."""  # noqa: E501
        # One line on purpose: the docstring is the tool description the model sees, and it
        # must equal REQUIREMENTS §7.1 exactly. A wrapped docstring would add line breaks.
        start = time.perf_counter()  # perf_counter: monotonic, unaffected by clock changes
        result = employees.get_employee_info(employee_id, directory=directory, as_of=as_of)
        log_event(
            logger,
            "tool.call",
            tool="get_employee_info",
            employee_id=employee_id,  # the raw input, so a bad ID can be seen in the logs
            status=result.get("error", "ok"),
            data_version=directory.data_version,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
        )  # no name field: employee names are never logged (CLAUDE.md)
        return result

    return server


def main() -> None:  # pragma: no cover
    # Not unit-tested: it starts a blocking network server. Everything it calls
    # (configure_logging, load_employees, build_server) is tested separately.
    configure_logging("server", log_file=Path("logs/app.jsonl"))
    directory = load_employees(config.EMPLOYEES_FILE)
    log_event(
        logger,
        "server.start",
        data_version=directory.data_version,
        employee_count=len(directory.employees),
        host=HOST,
        port=PORT,
    )
    build_server(directory).run(transport="streamable-http", host=HOST, port=PORT)


if __name__ == "__main__":  # pragma: no cover
    main()

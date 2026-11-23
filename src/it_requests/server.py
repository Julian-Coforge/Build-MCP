"""The MCP server: thin wrappers that expose the pure functions as tools.

No business logic lives here. Each tool calls a tested function, times it and logs one
event, so the protocol layer can't drift from the rules the unit tests pin down.
"""

import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from it_requests import config, eligibility, employees, policy
from it_requests.data import EmployeeDirectory, PolicyTable, load_employees, load_policy
from it_requests.observability import configure_logging, log_event

HOST = "127.0.0.1"  # Loopback only: the mock server must never be reachable from the network.
PORT = 8000

logger = logging.getLogger("server")

ToolResult = dict[str, Any]


def _no_extra_fields(_result: ToolResult) -> dict[str, Any]:
    return {}


def _ok_or_error(result: ToolResult) -> str:
    # Default status for lookup tools: "ok", or the error code.
    return str(result.get("error", "ok"))


def _verdict_or_error(result: ToolResult) -> str:
    # For the eligibility tool, the verdict itself (ELIGIBLE etc.) is logged as status, so a
    # jq query on status finds every denial or review without reading tool results.
    # Not the default: get_employee_info's result also has a "status" key (employment status).
    return str(result.get("error") or result["status"])


def _call_and_log(
    tool: str,
    call: Callable[[], ToolResult],
    *,
    data_version: str | dict[str, str],
    inputs: dict[str, Any],
    result_fields: Callable[[ToolResult], dict[str, Any]] = _no_extra_fields,
    status_of: Callable[[ToolResult], str] = _ok_or_error,
) -> ToolResult:
    """Run one tool call, log one tool.call event, and return the result unchanged.

    One helper for every tool, so each wrapper stays a few lines and the log shape (tool,
    inputs, status, data_version, duration_ms) can't drift between tools.
    `result_fields` adds log fields that depend on the result, such as resolved_employee_id.
    `status_of` decides the logged status; the default is "ok" or the error code.
    """
    start = time.perf_counter()  # perf_counter: monotonic, unaffected by clock changes
    result = call()
    log_event(
        logger,
        "tool.call",
        tool=tool,
        **inputs,
        **result_fields(result),
        status=status_of(result),
        data_version=data_version,
        duration_ms=round((time.perf_counter() - start) * 1000, 3),
    )
    return result


def _resolved_employee(result: ToolResult) -> dict[str, Any]:
    # Who the lookup matched: the normalised ID on success, None on any error (never a guess).
    return {"resolved_employee_id": None if "error" in result else result["employee_id"]}


def _eligibility_fields(result: ToolResult) -> dict[str, Any]:
    # Which employee was checked and which rule decided it; both null on an error.
    return _resolved_employee(result) | {"rule_id": result.get("rule_id")}


def build_server(
    directory: EmployeeDirectory,
    policy_table: PolicyTable,
    *,
    as_of: date = config.REFERENCE_DATE,
) -> MCPServer:
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
        return _call_and_log(
            "get_employee_info",
            lambda: employees.get_employee_info(employee_id, directory=directory, as_of=as_of),
            data_version=directory.data_version,
            # The raw input, so a bad ID stays visible exactly as sent. No name field:
            # employee names are never logged (CLAUDE.md).
            inputs={"employee_id": employee_id},
            result_fields=_resolved_employee,
        )

    @server.tool()
    def get_policy_limits(role: str) -> dict[str, Any]:
        """Return the equipment a role is entitled to, the maximum units per item, and the refresh interval in months. Use this to explain a decision or a denial."""  # noqa: E501
        # One line on purpose, as above: must equal REQUIREMENTS §7.2 exactly.
        return _call_and_log(
            "get_policy_limits",
            lambda: policy.get_policy_limits(role, policy=policy_table),
            data_version=policy_table.data_version,
            inputs={"role": role},
        )

    @server.tool()
    def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
        """Decide whether one unit of a catalog item is within policy for this employee. Returns ELIGIBLE, NOT_ELIGIBLE, or NEEDS_REVIEW, with the rule applied. This is the only source of truth for eligibility."""  # noqa: E501
        # One line on purpose, as above: must equal REQUIREMENTS §7.3 exactly.
        return _call_and_log(
            "check_request_eligibility",
            lambda: eligibility.check_request_eligibility(
                employee_id, item, directory=directory, policy=policy_table, as_of=as_of
            ),
            # Both files decide the verdict, so the log names both (same as data_versions).
            data_version={"employees": directory.data_version, "policy": policy_table.data_version},
            inputs={"employee_id": employee_id, "item": item},
            result_fields=_eligibility_fields,
            status_of=_verdict_or_error,
        )

    return server


def main() -> None:  # pragma: no cover
    # Not unit-tested: it starts a blocking network server. Everything it calls
    # (configure_logging, the loaders, build_server) is tested separately.
    configure_logging("server", log_file=Path("logs/app.jsonl"))
    directory = load_employees(config.EMPLOYEES_FILE)
    policy_table = load_policy(config.POLICY_FILE)
    log_event(
        logger,
        "server.start",
        data_version=directory.data_version,
        policy_version=policy_table.data_version,
        employee_count=len(directory.employees),
        host=HOST,
        port=PORT,
    )
    build_server(directory, policy_table).run(transport="streamable-http", host=HOST, port=PORT)


if __name__ == "__main__":  # pragma: no cover
    main()

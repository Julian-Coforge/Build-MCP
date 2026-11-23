"""get_employee_info called through the MCP protocol, in-process (no network)."""

import io
import json
import logging
from collections.abc import Iterator

import pytest
from mcp import Client
from mcp.server import MCPServer

from it_requests.observability import JsonFormatter

pytestmark = pytest.mark.anyio

# REQUIREMENTS §7.1 "Description for the model", word for word: the model chooses tools
# from this text, so any drift changes agent behaviour.
EXPECTED_DESCRIPTION = (
    "Look up an employee's role, hire date, tenure, employment status, and the equipment "
    "currently issued to them. Use this first for any request about a specific employee."
)


async def test_lists_exactly_the_employee_tool(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.list_tools()

    assert [t.name for t in result.tools] == ["get_employee_info"]
    assert result.tools[0].description == EXPECTED_DESCRIPTION


async def test_known_employee_returns_structured_record(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["employee_id"] == "E1001"
    assert result.structured_content["role"] == "standard"
    assert result.structured_content["tenure_months"] == 65


async def test_unknown_employee_is_data_not_protocol_error(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E9999"})

    # is_error stays False: "not found" is a normal answer the model reasons about
    # (ask again, or escalate under E1), not a failure of the tool itself (REQUIREMENTS §7).
    assert result.is_error is False
    assert result.structured_content == {"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"}


async def test_messy_id_normalises_through_protocol(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": " e1001 "})

    assert result.structured_content is not None
    assert result.structured_content["employee_id"] == "E1001"


@pytest.fixture
def server_log() -> Iterator[io.StringIO]:
    # Capture the "server" logger as JSON lines. configure_logging isn't called in tests,
    # so attach a handler and level here, and restore both afterwards.
    logger = logging.getLogger("server")
    old_handlers, old_level, old_propagate = logger.handlers, logger.level, logger.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield stream
    logger.handlers, logger.level, logger.propagate = old_handlers, old_level, old_propagate


async def test_tool_call_logs_one_event_without_name(
    server: MCPServer, server_log: io.StringIO
) -> None:
    async with Client(server) as client:
        await client.call_tool("get_employee_info", {"employee_id": "E1001"})

    lines = server_log.getvalue().splitlines()
    tool_calls = [json.loads(line) for line in lines if json.loads(line)["event"] == "tool.call"]
    assert len(tool_calls) == 1
    record = tool_calls[0]
    assert record["tool"] == "get_employee_info"
    assert record["employee_id"] == "E1001"
    assert record["status"] == "ok"
    assert record["data_version"] == "employees-v1"
    assert isinstance(record["duration_ms"], int | float)
    # Employee names are never logged (CLAUDE.md, PLAN §5): check the raw text, not just keys.
    assert "Priya" not in server_log.getvalue()

"""Shared fixtures for integration tests: the real server, called in-process."""

import pytest
from mcp.server import MCPServer

from it_requests import config
from it_requests.data import load_employees
from it_requests.server import build_server


@pytest.fixture
def anyio_backend() -> str:
    # The anyio pytest plugin (installed with mcp) runs async tests. Pin asyncio, because
    # the server and client are only used on asyncio, and trio isn't installed.
    return "asyncio"


@pytest.fixture
def server() -> MCPServer:
    # The real data and the real factory: only the network is skipped, because
    # Client(server) connects in-process.
    return build_server(load_employees(config.EMPLOYEES_FILE))

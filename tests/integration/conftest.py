"""Shared fixtures for integration tests: the real server, called in-process."""

import io
import logging
from collections.abc import Iterator

import pytest
from mcp.server import MCPServer

from it_requests import config
from it_requests.data import load_employees, load_policy
from it_requests.observability import JsonFormatter
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
    return build_server(load_employees(config.EMPLOYEES_FILE), load_policy(config.POLICY_FILE))


@pytest.fixture
def server_log() -> Iterator[io.StringIO]:
    # Shared here because every tool's tests check its tool.call line.
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

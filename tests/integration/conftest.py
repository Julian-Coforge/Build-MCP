"""Shared fixtures for integration tests: the real server, called in-process."""

import io
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

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


# Fixed time for created_at, so tickets written in tests are deterministic.
FIXED_NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def review_queue_path(tmp_path: Path) -> Path:
    # A fresh queue per test: tests never touch data/review_queue.json, and every test
    # starts at REV-0001.
    return tmp_path / "review_queue.json"


@pytest.fixture
def server(review_queue_path: Path) -> MCPServer:
    # The real data and the real factory: only the network is skipped, because
    # Client(server) connects in-process.
    return build_server(
        load_employees(config.EMPLOYEES_FILE),
        load_policy(config.POLICY_FILE),
        review_queue_path=review_queue_path,
        clock=lambda: FIXED_NOW,
    )


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

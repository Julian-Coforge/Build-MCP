"""Tests for the structured JSON logging module (PLAN §5)."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from it_requests.observability import (
    JsonFormatter,
    configure_logging,
    log_event,
    request_id_var,
)


@pytest.fixture
def captured(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    # A logger unique to each test, writing to an in-memory stream, so tests never see
    # each other's output or handlers.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.handlers = []


def _lines(stream: io.StringIO) -> list[str]:
    return stream.getvalue().splitlines()


def _only_record(stream: io.StringIO) -> dict[str, Any]:
    lines = _lines(stream)
    assert len(lines) == 1, f"expected exactly one log line, got {lines!r}"
    record: dict[str, Any] = json.loads(lines[0])
    return record


# --- JsonFormatter / log_event ---------------------------------------------------


def test_log_event_writes_one_json_line(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "tool.call", tool="get_employee_info", employee_id="E1001")

    record = _only_record(stream)
    assert record["level"] == "INFO"
    assert record["component"] == logger.name
    assert record["event"] == "tool.call"
    assert record["tool"] == "get_employee_info"
    assert record["employee_id"] == "E1001"
    assert "ts" in record


def test_log_event_respects_level(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "budget.near_limit", level=logging.WARNING)

    assert _only_record(stream)["level"] == "WARNING"


def test_ts_is_utc_iso_with_z(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "x")

    ts = _only_record(stream)["ts"]
    assert ts.endswith("Z")
    parsed = datetime.fromisoformat(ts)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_request_id_null_by_default(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "x")

    assert _only_record(stream)["request_id"] is None


def test_request_id_taken_from_context_var(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    token = request_id_var.set("REQ-0001")
    try:
        log_event(logger, "x")
    finally:
        # Reset with the token so the value can't leak into other tests.
        request_id_var.reset(token)

    assert _only_record(stream)["request_id"] == "REQ-0001"
    assert request_id_var.get() is None


@pytest.mark.parametrize("key", ["name", "api_key"])
def test_sensitive_fields_are_redacted(
    captured: tuple[logging.Logger, io.StringIO], key: str
) -> None:
    logger, stream = captured
    secret = "Priya Shah sk-ant-SECRET"

    log_event(logger, "x", **{key: secret})

    line = _lines(stream)[0]
    assert json.loads(line)[key] == "[REDACTED]"
    # Check the raw line too: the value must not appear anywhere, not just under its key.
    assert secret not in line


def test_date_serialises_as_iso_string(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "x", as_of=date(2026, 11, 22))

    assert _only_record(stream)["as_of"] == "2026-11-22"


def test_exception_adds_type_and_message(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    try:
        raise ValueError("bad data")
    except ValueError:
        logger.exception("load.failed")

    record = _only_record(stream)
    assert record["event"] == "load.failed"
    assert record["level"] == "ERROR"
    assert record["exc_type"] == "ValueError"
    assert record["exc_message"] == "bad data"


# --- Trace correlation (PLAN §5, §6) ------------------------------------------------------


def test_log_line_inside_span_carries_its_ids(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured
    # A local SDK provider, never the global one, so this test can't affect any other.
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("agent.run") as span:
        log_event(logger, "run.start")
        context = span.get_span_context()

    record = _only_record(stream)
    # Same hex format as the trace files, so a log line and its span can be matched.
    assert record["trace_id"] == format(context.trace_id, "032x")
    assert record["span_id"] == format(context.span_id, "016x")


def test_log_line_outside_span_has_null_ids(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    log_event(logger, "x")

    record = _only_record(stream)
    assert record["trace_id"] is None
    assert record["span_id"] is None


# --- configure_logging -------------------------------------------------------------


@pytest.fixture
def component(request: pytest.FixtureRequest) -> Iterator[str]:
    # Unique logger name per test; handlers are closed afterwards so file handles
    # don't leak between tests.
    name = f"cfg.{request.node.name}"
    yield name
    logger = logging.getLogger(name)
    for handler in logger.handlers:
        handler.close()
    logger.handlers = []


def test_configure_logging_returns_named_logger(component: str) -> None:
    logger = configure_logging(component)

    assert logger.name == component
    assert logger.propagate is False
    assert len(logger.handlers) >= 1
    assert all(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)


def test_configure_logging_is_idempotent(component: str, tmp_path: Path) -> None:
    log_file = tmp_path / "app.jsonl"
    first = len(configure_logging(component, log_file=log_file).handlers)

    second = len(configure_logging(component, log_file=log_file).handlers)

    assert second == first


def test_configure_logging_writes_json_lines_to_file(component: str, tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.jsonl"

    logger = configure_logging(component, log_file=log_file)
    log_event(logger, "run.start", request_id_hint="REQ-0001")
    log_event(logger, "run.end")
    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    events = [json.loads(line)["event"] for line in log_file.read_text().splitlines()]
    assert events == ["run.start", "run.end"]

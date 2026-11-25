"""Tests for the structured JSON logging module (PLAN §5)."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
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


# ---------- JsonFormatter / log_event ----------


def test_log_event_writes_one_json_line_with_fields(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    log_event(logger, "tool.call", tool="get_employee_info", employee_id="E1001")

    record = _only_record(stream)

    # Every field except ts (wall clock, checked below) and the null correlation ids.
    assert {k: record[k] for k in ("level", "component", "event", "tool", "employee_id")} == {
        "level": "INFO",
        "component": logger.name,
        "event": "tool.call",
        "tool": "get_employee_info",
        "employee_id": "E1001",
    }


def test_log_event_level_argument_sets_level(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "budget.near_limit", level=logging.WARNING)

    assert _only_record(stream)["level"] == "WARNING"


def test_log_event_ts_ends_with_z(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "x")

    assert _only_record(stream)["ts"].endswith("Z")


def test_log_event_ts_parses_as_utc(captured: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = captured

    log_event(logger, "x")
    parsed = datetime.fromisoformat(_only_record(stream)["ts"])

    assert parsed.utcoffset() == timedelta(0)


def test_log_event_request_id_is_null_by_default(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    log_event(logger, "x")

    assert _only_record(stream)["request_id"] is None


def test_log_event_request_id_comes_from_context_var(
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


def test_request_id_var_is_none_outside_a_request() -> None:
    # The previous test resets with its token; nothing may leak into other tests.
    assert request_id_var.get() is None


SECRET = "Priya Shah sk-ant-SECRET"
# Case-insensitive: API_KEY and Authorization are how env vars and HTTP headers spell them.
SENSITIVE_KEYS = ["name", "api_key", "API_KEY", "Authorization", "password", "token"]


@pytest.mark.parametrize("key", SENSITIVE_KEYS, ids=str)
def test_log_event_sensitive_field_is_redacted(
    captured: tuple[logging.Logger, io.StringIO], key: str
) -> None:
    logger, stream = captured

    log_event(logger, "x", **{key: SECRET})

    assert _only_record(stream)[key] == "[REDACTED]"


@pytest.mark.parametrize("key", SENSITIVE_KEYS, ids=str)
def test_log_event_sensitive_value_appears_nowhere_in_the_line(
    captured: tuple[logging.Logger, io.StringIO], key: str
) -> None:
    # Check the raw line: the value must not appear anywhere, not just under its key.
    logger, stream = captured

    log_event(logger, "x", **{key: SECRET})

    assert SECRET not in stream.getvalue()


def test_log_event_date_serialises_as_iso_string(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    log_event(logger, "x", as_of=date(2026, 11, 22))

    assert _only_record(stream)["as_of"] == "2026-11-22"


def test_logger_exception_adds_type_and_message(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    try:
        raise ValueError("bad data")
    except ValueError:
        logger.exception("load.failed")

    record = _only_record(stream)

    assert {k: record[k] for k in ("event", "level", "exc_type", "exc_message")} == {
        "event": "load.failed",
        "level": "ERROR",
        "exc_type": "ValueError",
        "exc_message": "bad data",
    }


# ---------- Trace correlation (PLAN §5, §6) ----------


def test_log_event_inside_span_carries_its_ids(
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
    assert (record["trace_id"], record["span_id"]) == (
        format(context.trace_id, "032x"),
        format(context.span_id, "016x"),
    )


def test_log_event_outside_span_has_null_ids(
    captured: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured

    log_event(logger, "x")

    record = _only_record(stream)

    assert (record["trace_id"], record["span_id"]) == (None, None)


# ---------- configure_logging ----------


def _flush(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


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


def test_configure_logging_returns_logger_with_the_component_name(component: str) -> None:
    assert configure_logging(component).name == component


def test_configure_logging_logger_does_not_propagate(component: str) -> None:
    # No double lines through the root logger.
    assert configure_logging(component).propagate is False


def test_configure_logging_every_handler_writes_json(component: str) -> None:
    handlers = configure_logging(component).handlers

    # At least one handler, and none that writes plain text.
    assert handlers and all(isinstance(h.formatter, JsonFormatter) for h in handlers)


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
    _flush(logger)

    events = [json.loads(line)["event"] for line in log_file.read_text().splitlines()]
    assert events == ["run.start", "run.end"]

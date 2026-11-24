"""Structured JSON logging for the MCP server and (later) the agent (PLAN §5).

Standard library only: one JSON object per line, so logs can be searched with grep or jq
and no new dependency is needed.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry import trace

# A ContextVar, not a global: each asyncio task (each MCP request) sees its own value, so
# two requests handled at the same time never log each other's ID. A global would be
# overwritten by whichever request set it last.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Never written to logs (CLAUDE.md, PLAN §5): employee names, credentials, secrets.
REDACTED_KEYS: frozenset[str] = frozenset({"name", "api_key", "authorization", "password", "token"})
_REDACTED = "[REDACTED]"


def _redact(fields: dict[str, Any]) -> dict[str, Any]:
    # Case-insensitive, so "Authorization" or "API_KEY" can't slip past on spelling alone.
    return {k: _REDACTED if k.lower() in REDACTED_KEYS else v for k, v in fields.items()}


def _trace_ids() -> dict[str, str | None]:
    # The active span's IDs, in the same hex format as the trace files, so a log line can be
    # matched to its span (PLAN §5, §6). Null outside a span: no tracer is ever required.
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {"trace_id": None, "span_id": None}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


class JsonFormatter(logging.Formatter):
    """Format each record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        # record.created is the time the event happened, not the time it is formatted.
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        fields: dict[str, Any] = getattr(record, "fields", None) or {}

        # Custom fields first, standard keys after: a field called "event" or "level" can
        # never overwrite the real values.
        payload: dict[str, Any] = {
            **_redact(fields),
            "ts": ts.replace("+00:00", "Z"),
            "level": record.levelname,
            "component": record.name,
            "event": record.getMessage(),
            "request_id": request_id_var.get(),
            **_trace_ids(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            # Type and message only: a full traceback would break the one-line format.
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc_message"] = str(record.exc_info[1])

        # default=str turns dates, Paths and other non-JSON values into readable strings
        # instead of crashing the logging call.
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """The one logging helper everything calls, so call sites stay short and consistent."""
    logger.log(level, event, extra={"fields": fields})


def configure_logging(
    component: str, *, log_file: Path | None = None, level: int = logging.INFO
) -> logging.Logger:
    """Return the component's logger, writing JSON to stderr and optionally to a file.

    Idempotent: existing handlers are closed and replaced, so calling this twice (e.g. once
    per test, or on server restart in-process) never duplicates every log line.
    """
    logger = logging.getLogger(component)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = JsonFormatter()
    # stderr, not stdout: an MCP server on the stdio transport uses stdout for protocol
    # messages, so logs there would corrupt the stream.
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.setLevel(level)
    # Don't also pass records to the root logger, which could print them a second time
    # in a different format.
    logger.propagate = False
    return logger

"""OpenTelemetry tracing to JSON-lines files: traces/<request_id>.jsonl (PLAN §6).

No tracing backend is needed for a prototype: one JSON line per span can be read with jq
and dropped into screenshots. Pointing at Jaeger later is a one-line exporter change.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Tracer


def _iso(ns: int | None) -> str | None:
    return None if ns is None else datetime.fromtimestamp(ns / 1e9, UTC).isoformat()


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    context = span.get_span_context()
    start, end = span.start_time, span.end_time
    return {
        "name": span.name,
        # Same hex format as the trace_id / span_id in log lines, so the two cross-reference.
        "trace_id": format(context.trace_id, "032x") if context else None,
        "span_id": format(context.span_id, "016x") if context else None,
        "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
        "start": _iso(start),
        "end": _iso(end),
        "duration_ms": (end - start) / 1e6 if start is not None and end is not None else None,
        "status": span.status.status_code.name,  # UNSET, OK or ERROR
        "attributes": dict(span.attributes or {}),
    }


class JsonLinesSpanExporter(SpanExporter):
    """Append one JSON line per finished span to `path`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Append, never overwrite: re-running a request keeps its earlier traces, and each
        # run is told apart by its own trace_id. Opened per export, so no handle is left open.
        with self._path.open("a", encoding="utf-8") as file:
            for span in spans:
                # default=str: an attribute type JSON can't hold is written as text, never
                # crashing the traced code.
                file.write(json.dumps(_span_to_dict(span), default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass  # nothing held open between exports

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True  # every export is written and closed immediately


def make_tracer(path: Path) -> tuple[Tracer, TracerProvider]:
    """Build a tracer that writes to `path`. The caller shuts the provider down after a run.

    A NEW provider each time, and never the global one: the global provider can only be set
    once per process, so tests would depend on their order. The tracer is passed in
    explicitly instead, like as_of and clock elsewhere. It is typed as the API's Tracer, so
    callers depend only on the stable API, not on the SDK.
    SimpleSpanProcessor writes each span as soon as it ends, so a crash mid-run still leaves
    every finished span on disk.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(JsonLinesSpanExporter(path)))
    return provider.get_tracer("it_requests"), provider

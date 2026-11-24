"""Tests for the JSON-lines span exporter and make_tracer (PLAN §6)."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from it_requests.tracing import make_tracer

HEX32 = re.compile(r"[0-9a-f]{32}")
HEX16 = re.compile(r"[0-9a-f]{16}")


@pytest.fixture
def trace_path(tmp_path: Path) -> Path:
    # In a folder that doesn't exist yet, so the exporter must create it.
    return tmp_path / "traces" / "REQ-0001.jsonl"


def _spans(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _by_name(spans: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(s for s in spans if s["name"] == name)


def _run_parent_and_child(path: Path) -> None:
    tracer, provider = make_tracer(path)
    with tracer.start_as_current_span("agent.run") as parent:
        parent.set_attribute("request_id", "REQ-0001")
        with tracer.start_as_current_span("llm.call") as child:
            child.set_attributes(
                {"model": "claude-haiku-5-5", "input_tokens": 1200, "cached": True}
            )
    # The caller shuts the provider down at the end of a run, which flushes the file.
    provider.shutdown()


def test_parent_and_child_write_two_lines(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)
    assert sorted(s["name"] for s in spans) == ["agent.run", "llm.call"]


def test_child_links_to_parent(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)
    parent, child = _by_name(spans, "agent.run"), _by_name(spans, "llm.call")
    assert child["parent_span_id"] == parent["span_id"]
    assert child["trace_id"] == parent["trace_id"]
    assert parent["parent_span_id"] is None  # the root span has no parent


def test_ids_are_hex_of_the_right_length(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    for span in _spans(trace_path):
        assert HEX32.fullmatch(span["trace_id"])
        assert HEX16.fullmatch(span["span_id"])


def test_times_are_utc_iso_with_duration(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    for span in _spans(trace_path):
        start, end = datetime.fromisoformat(span["start"]), datetime.fromisoformat(span["end"])
        assert start.utcoffset() is not None and start.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
        assert end >= start
        assert isinstance(span["duration_ms"], int | float)
        assert span["duration_ms"] >= 0


def test_attributes_round_trip(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)
    assert _by_name(spans, "agent.run")["attributes"] == {"request_id": "REQ-0001"}
    assert _by_name(spans, "llm.call")["attributes"] == {
        "model": "claude-haiku-5-5",
        "input_tokens": 1200,
        "cached": True,
    }


def test_second_run_appends_not_overwrites(trace_path: Path) -> None:
    # Re-running the same request keeps its history; each run has its own trace_id.
    _run_parent_and_child(trace_path)
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)
    assert len(spans) == 4
    assert len({s["trace_id"] for s in spans}) == 2


def test_span_with_exception_has_error_status(trace_path: Path) -> None:
    tracer, provider = make_tracer(trace_path)
    with pytest.raises(ValueError), tracer.start_as_current_span("tool.call"):
        raise ValueError("boom")
    provider.shutdown()

    span = _spans(trace_path)[0]
    assert span["status"] == "ERROR"


def test_normal_span_is_not_error(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    assert all(s["status"] != "ERROR" for s in _spans(trace_path))

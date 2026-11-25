"""Tests for the JSON-lines span exporter and make_tracer (PLAN §6)."""

import json
import re
from datetime import datetime, timedelta
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


def _written(path: Path, name: str) -> dict[str, Any]:
    """Run the parent/child pair, then return the span with this name from the file."""
    _run_parent_and_child(path)
    return _by_name(_spans(path), name)


SPAN_NAMES = ["agent.run", "llm.call"]


def test_child_span_links_to_parent_span_id(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)

    assert _by_name(spans, "llm.call")["parent_span_id"] == _by_name(spans, "agent.run")["span_id"]


def test_child_span_shares_the_parent_trace_id(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    spans = _spans(trace_path)

    assert _by_name(spans, "llm.call")["trace_id"] == _by_name(spans, "agent.run")["trace_id"]


def test_root_span_has_no_parent(trace_path: Path) -> None:
    assert _written(trace_path, "agent.run")["parent_span_id"] is None


@pytest.mark.parametrize("name", SPAN_NAMES, ids=str)
def test_span_trace_id_is_32_hex_chars(trace_path: Path, name: str) -> None:
    assert HEX32.fullmatch(_written(trace_path, name)["trace_id"])


@pytest.mark.parametrize("name", SPAN_NAMES, ids=str)
def test_span_span_id_is_16_hex_chars(trace_path: Path, name: str) -> None:
    assert HEX16.fullmatch(_written(trace_path, name)["span_id"])


@pytest.mark.parametrize("name", SPAN_NAMES, ids=str)
def test_span_start_is_utc(trace_path: Path, name: str) -> None:
    start = datetime.fromisoformat(_written(trace_path, name)["start"])

    assert start.utcoffset() == timedelta(0)


@pytest.mark.parametrize("name", SPAN_NAMES, ids=str)
def test_span_end_is_not_before_start(trace_path: Path, name: str) -> None:
    span = _written(trace_path, name)

    assert datetime.fromisoformat(span["end"]) >= datetime.fromisoformat(span["start"])


@pytest.mark.parametrize("name", SPAN_NAMES, ids=str)
def test_span_duration_is_a_non_negative_number(trace_path: Path, name: str) -> None:
    duration = _written(trace_path, name)["duration_ms"]

    assert isinstance(duration, int | float) and duration >= 0


@pytest.mark.parametrize(
    ("name", "attributes"),
    [
        ("agent.run", {"request_id": "REQ-0001"}),
        ("llm.call", {"model": "claude-haiku-5-5", "input_tokens": 1200, "cached": True}),
    ],
    ids=SPAN_NAMES,
)
def test_span_attributes_round_trip(
    trace_path: Path, name: str, attributes: dict[str, Any]
) -> None:
    assert _written(trace_path, name)["attributes"] == attributes


def test_second_run_appends_not_overwrites(trace_path: Path) -> None:
    # Re-running the same request keeps its history.
    _run_parent_and_child(trace_path)
    _run_parent_and_child(trace_path)

    assert len(_spans(trace_path)) == 4


def test_second_run_gets_its_own_trace_id(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)
    _run_parent_and_child(trace_path)

    assert len({s["trace_id"] for s in _spans(trace_path)}) == 2


def test_span_with_exception_has_error_status(trace_path: Path) -> None:
    tracer, provider = make_tracer(trace_path)
    with pytest.raises(ValueError), tracer.start_as_current_span("tool.call"):
        raise ValueError("boom")
    provider.shutdown()

    assert _spans(trace_path)[0]["status"] == "ERROR"


def test_normal_span_is_not_error(trace_path: Path) -> None:
    _run_parent_and_child(trace_path)

    assert all(s["status"] != "ERROR" for s in _spans(trace_path))

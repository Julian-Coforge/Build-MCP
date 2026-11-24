"""Tests for LLMClient, the one wrapper every paid model call goes through (PLAN §2, §4, §6).

Only the fake messages API is used here; nothing calls the real Claude API.
"""

import io
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from it_requests.agent.llm import LLMClient
from it_requests.agent.prompts import PROMPT_VERSION
from it_requests.observability import JsonFormatter

REQUEST_TEXT = "Hi, I'm Marcus (E1002). Could I get a second monitor?"
SYSTEM = "You are an IT equipment request assistant."
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_employee_info",
        "description": "Look up an employee.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    }
]

LEDGER_FIELDS = {
    "ts",
    "request_id",
    "run_id",
    "stage",
    "model",
    "prompt_version",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_ms",
    "env",
    "stop_reason",
}


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def log_stream(request: pytest.FixtureRequest) -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.llm.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield stream
    logger.handlers = []


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    # Never the real logs/ folder.
    return tmp_path / "logs" / "cost_ledger.jsonl"


@pytest.fixture
def llm(
    fake_messages: Any,
    spans: InMemorySpanExporter,
    log_stream: io.StringIO,
    ledger_path: Path,
    request: pytest.FixtureRequest,
) -> LLMClient:
    provider = TracerProvider()  # local, never global
    provider.add_span_processor(SimpleSpanProcessor(spans))
    # Each call reads the clock twice: 100.0 before, 100.25 after = 250 ms.
    ticks = iter([100.0, 100.25, 200.0, 200.5])
    return LLMClient(
        create=fake_messages.create,
        model="claude-haiku-5-5",
        env="dev",
        ledger_path=ledger_path,
        tracer=provider.get_tracer("test"),
        logger=logging.getLogger(f"test.llm.{request.node.name}"),
        clock=lambda: next(ticks),
    )


def _call(llm: LLMClient) -> Message:
    return llm.call(
        request_id="REQ-0001",
        run_id="run-1",
        stage="react_step_1",
        system=SYSTEM,
        messages=[{"role": "user", "content": REQUEST_TEXT}],
        tools=TOOLS,
    )


def _ledger(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Ledger ------------------------------------------------------------------------------


def test_one_call_writes_one_ledger_line(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(
        make_message(input_tokens=10_000, output_tokens=2_000, cache_read_input_tokens=1_000)
    )

    _call(llm)

    lines = _ledger(ledger_path)
    assert len(lines) == 1
    line = lines[0]
    assert set(line) == LEDGER_FIELDS
    assert line["request_id"] == "REQ-0001"
    assert line["run_id"] == "run-1"
    assert line["stage"] == "react_step_1"
    assert line["model"] == "claude-haiku-5-5"
    assert line["prompt_version"] == PROMPT_VERSION == "m5-v1"
    assert line["env"] == "dev"
    assert line["stop_reason"] == "tool_use"
    assert (line["input_tokens"], line["output_tokens"]) == (10_000, 2_000)
    assert (line["cache_read_tokens"], line["cache_write_tokens"]) == (1_000, 0)
    # Haiku: 10,000 x $0.10/M = $0.001; 2,000 x $0.50/M = $0.001; 1,000 cache read x
    # $0.01/M = $0.00001. Total $0.00201.
    assert line["cost_usd"] == pytest.approx(0.00201, abs=1e-9)


def test_missing_cache_counts_are_zero(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    # The API may send null for the cache counts; that means none, not unknown.
    fake_messages.queue(
        make_message(
            input_tokens=10_000,
            output_tokens=0,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
    )

    _call(llm)

    line = _ledger(ledger_path)[0]
    assert (line["cache_read_tokens"], line["cache_write_tokens"]) == (0, 0)
    assert line["cost_usd"] == pytest.approx(0.001, abs=1e-9)


def test_each_call_appends_a_line(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(), make_message())

    _call(llm)
    _call(llm)

    assert len(_ledger(ledger_path)) == 2


def test_latency_comes_from_injected_clock(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message())

    _call(llm)

    assert _ledger(ledger_path)[0]["latency_ms"] == pytest.approx(250.0)


def test_returns_the_sdk_message_unchanged(
    llm: LLMClient, fake_messages: Any, make_message: Callable[..., Message]
) -> None:
    message = make_message()
    fake_messages.queue(message)

    assert _call(llm) is message


# --- The request sent -----------------------------------------------------------------------


def test_request_uses_auto_tool_choice_adaptive_thinking_and_token_cap(
    llm: LLMClient, fake_messages: Any, make_message: Callable[..., Message]
) -> None:
    fake_messages.queue(make_message())

    _call(llm)

    sent = fake_messages.calls[0]
    # auto, not forced: forced tool_choice returns a 400 on Sonnet 5.5, and the same code
    # runs on both models.
    assert sent["tool_choice"] == {"type": "auto"}
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["max_tokens"] == 4000
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["system"] == SYSTEM
    assert sent["messages"] == [{"role": "user", "content": REQUEST_TEXT}]
    assert sent["tools"] == TOOLS


# --- Span ----------------------------------------------------------------------------------------


def test_span_records_model_tokens_and_cost(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    spans: InMemorySpanExporter,
) -> None:
    fake_messages.queue(
        make_message(
            input_tokens=10_000,
            output_tokens=2_000,
            cache_read_input_tokens=1_000,
            cache_creation_input_tokens=500,
        )
    )

    _call(llm)

    (span,) = spans.get_finished_spans()
    assert span.name == "llm.call"
    attributes = dict(span.attributes or {})
    assert attributes["model"] == "claude-haiku-5-5"
    assert attributes["prompt_version"] == PROMPT_VERSION
    assert attributes["stage"] == "react_step_1"
    assert attributes["input_tokens"] == 10_000
    assert attributes["output_tokens"] == 2_000
    assert attributes["cache_read_tokens"] == 1_000
    assert attributes["cache_write_tokens"] == 500
    assert attributes["cost_usd"] > 0
    assert attributes["latency_ms"] == pytest.approx(250.0)
    assert attributes["stop_reason"] == "tool_use"


# --- Failure ------------------------------------------------------------------------------------


def test_failed_call_writes_no_ledger_line_and_marks_span_error(
    llm: LLMClient,
    fake_messages: Any,
    spans: InMemorySpanExporter,
    ledger_path: Path,
) -> None:
    fake_messages.queue(RuntimeError("connection reset"))

    # The exception propagates: the SDK has already retried, so the wrapper doesn't.
    with pytest.raises(RuntimeError, match="connection reset"):
        _call(llm)

    # No tokens are billed for a failed request, so nothing is added to spend.
    assert not ledger_path.exists()
    (span,) = spans.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert len(fake_messages.calls) == 1  # no retry


# --- Log line -------------------------------------------------------------------------------------


def test_log_line_has_numbers_but_no_prompt_content(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    log_stream: io.StringIO,
) -> None:
    fake_messages.queue(make_message(input_tokens=10_000, output_tokens=2_000))

    _call(llm)

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    (record,) = [r for r in records if r["event"] == "llm.call"]
    assert record["level"] == "INFO"
    assert record["input_tokens"] == 10_000
    assert record["output_tokens"] == 2_000
    assert record["cost_usd"] > 0
    assert record["model"] == "claude-haiku-5-5"
    assert {"messages", "system", "tools"}.isdisjoint(record)
    # The request text holds an employee name: it must appear nowhere in the log.
    assert "Marcus" not in log_stream.getvalue()
    assert SYSTEM not in log_stream.getvalue()

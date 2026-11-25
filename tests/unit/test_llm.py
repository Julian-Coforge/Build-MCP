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

from it_requests.agent.llm import LLMClient, system_blocks
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


def _failed_call(llm: LLMClient, fake_messages: Any) -> None:
    fake_messages.queue(RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        _call(llm)


def _llm_record(log_stream: io.StringIO) -> dict[str, Any]:
    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    (record,) = [r for r in records if r["event"] == "llm.call"]
    return record


# Haiku: 10,000 x $0.10/M = $0.001; 2,000 x $0.50/M = $0.001; 1,000 cache read x
# $0.01/M = $0.00001. Total $0.00201.
TYPICAL_USAGE = {"input_tokens": 10_000, "output_tokens": 2_000, "cache_read_input_tokens": 1_000}
TYPICAL_COST = 0.00201


# ---------- ledger ----------


def test_llm_call_writes_one_ledger_line(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(**TYPICAL_USAGE))

    _call(llm)

    assert len(_ledger(ledger_path)) == 1


def test_llm_call_ledger_line_has_exactly_the_ledger_fields(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(**TYPICAL_USAGE))

    _call(llm)

    assert set(_ledger(ledger_path)[0]) == LEDGER_FIELDS


def test_llm_call_ledger_line_records_ids_model_and_tokens(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(**TYPICAL_USAGE))

    _call(llm)
    line = _ledger(ledger_path)[0]

    # Everything except the wall-clock ts, which is not injected; cost is approximate.
    assert {k: v for k, v in line.items() if k != "ts"} == pytest.approx(
        {
            "request_id": "REQ-0001",
            "run_id": "run-1",
            "stage": "react_step_1",
            "model": "claude-haiku-5-5",
            "prompt_version": PROMPT_VERSION,
            "input_tokens": 10_000,
            "output_tokens": 2_000,
            "cache_read_tokens": 1_000,
            "cache_write_tokens": 0,
            "cost_usd": TYPICAL_COST,
            "latency_ms": 250.0,
            "env": "dev",
            "stop_reason": "tool_use",
        },
        abs=1e-9,
    )


def test_llm_call_null_cache_counts_are_logged_as_zero(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    # The API may send null for the cache counts; that means none, not unknown.
    fake_messages.queue(
        make_message(cache_read_input_tokens=None, cache_creation_input_tokens=None)
    )

    _call(llm)
    line = _ledger(ledger_path)[0]

    assert (line["cache_read_tokens"], line["cache_write_tokens"]) == (0, 0)


def test_llm_call_null_cache_counts_add_no_cost(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(
        make_message(
            input_tokens=10_000,
            output_tokens=0,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
    )

    _call(llm)

    assert _ledger(ledger_path)[0]["cost_usd"] == pytest.approx(0.001, abs=1e-9)


def test_llm_call_each_call_appends_a_line(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(), make_message())

    _call(llm)
    _call(llm)

    assert len(_ledger(ledger_path)) == 2


def test_llm_call_latency_comes_from_injected_clock(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message())

    _call(llm)

    assert _ledger(ledger_path)[0]["latency_ms"] == pytest.approx(250.0)


def test_llm_call_returns_the_sdk_message_unchanged(
    llm: LLMClient, fake_messages: Any, make_message: Callable[..., Message]
) -> None:
    message = make_message()
    fake_messages.queue(message)

    assert _call(llm) is message


# ---------- the request sent ----------


def test_llm_call_sends_auto_tool_choice_adaptive_thinking_and_token_cap(
    llm: LLMClient, fake_messages: Any, make_message: Callable[..., Message]
) -> None:
    fake_messages.queue(make_message())

    _call(llm)

    # auto, not forced: forced tool_choice returns a 400 on Sonnet 5.5, and the same code
    # runs on both models. The whole request, so an unexpected extra argument also fails.
    assert fake_messages.calls[0] == {
        "model": "claude-haiku-5-5",
        "max_tokens": 4000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": REQUEST_TEXT}],
        "tools": TOOLS,
        "tool_choice": {"type": "auto"},
        "thinking": {"type": "adaptive"},
    }


def test_llm_call_block_list_system_prompt_is_passed_through_unchanged(
    llm: LLMClient, fake_messages: Any, make_message: Callable[..., Message]
) -> None:
    fake_messages.queue(make_message())
    blocks = system_blocks(SYSTEM)

    llm.call(
        request_id="REQ-0001",
        run_id="run-1",
        stage="react_step_1",
        system=blocks,
        messages=[{"role": "user", "content": REQUEST_TEXT}],
        tools=TOOLS,
    )

    assert fake_messages.calls[0]["system"] is blocks


def test_system_blocks_is_one_cached_text_block() -> None:
    # The system prompt and tools are resent on every loop step. One ephemeral breakpoint on
    # the system block caches tools + system, so steps 2 onwards read them from the cache.
    assert system_blocks(SYSTEM) == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]


# ---------- span ----------

SPAN_USAGE = {**TYPICAL_USAGE, "cache_creation_input_tokens": 500}


def test_llm_call_emits_one_llm_call_span(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    spans: InMemorySpanExporter,
) -> None:
    fake_messages.queue(make_message(**SPAN_USAGE))

    _call(llm)

    assert [span.name for span in spans.get_finished_spans()] == ["llm.call"]


def test_llm_call_span_records_model_tokens_and_latency(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    spans: InMemorySpanExporter,
) -> None:
    fake_messages.queue(make_message(**SPAN_USAGE))
    expected = {
        "model": "claude-haiku-5-5",
        "prompt_version": PROMPT_VERSION,
        "stage": "react_step_1",
        "input_tokens": 10_000,
        "output_tokens": 2_000,
        "cache_read_tokens": 1_000,
        "cache_write_tokens": 500,
        "latency_ms": 250.0,
        "stop_reason": "tool_use",
    }

    _call(llm)
    (span,) = spans.get_finished_spans()
    attributes = dict(span.attributes or {})

    assert {key: attributes[key] for key in expected} == expected


def test_llm_call_span_cost_equals_ledger_cost(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    spans: InMemorySpanExporter,
    ledger_path: Path,
) -> None:
    fake_messages.queue(make_message(**SPAN_USAGE))

    _call(llm)
    (span,) = spans.get_finished_spans()

    assert dict(span.attributes or {})["cost_usd"] == _ledger(ledger_path)[0]["cost_usd"]


# ---------- failure ----------


def test_llm_call_failure_propagates_the_exception(llm: LLMClient, fake_messages: Any) -> None:
    # The exception propagates: the SDK has already retried, so the wrapper doesn't.
    fake_messages.queue(RuntimeError("connection reset"))

    with pytest.raises(RuntimeError, match="connection reset"):
        _call(llm)


def test_llm_call_failure_writes_no_ledger_line(
    llm: LLMClient, fake_messages: Any, ledger_path: Path
) -> None:
    # No tokens are billed for a failed request, so nothing is added to spend.
    _failed_call(llm, fake_messages)

    assert not ledger_path.exists()


def test_llm_call_failure_marks_span_error(
    llm: LLMClient, fake_messages: Any, spans: InMemorySpanExporter
) -> None:
    _failed_call(llm, fake_messages)

    assert [s.status.status_code.name for s in spans.get_finished_spans()] == ["ERROR"]


def test_llm_call_failure_is_not_retried(llm: LLMClient, fake_messages: Any) -> None:
    _failed_call(llm, fake_messages)

    assert len(fake_messages.calls) == 1


# ---------- log line ----------


def test_llm_call_log_line_has_level_model_and_numbers(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    log_stream: io.StringIO,
) -> None:
    fake_messages.queue(make_message(input_tokens=10_000, output_tokens=2_000))

    _call(llm)
    record = _llm_record(log_stream)

    assert (
        record["level"],
        record["model"],
        record["input_tokens"],
        record["output_tokens"],
        record["cost_usd"] > 0,
    ) == ("INFO", "claude-haiku-5-5", 10_000, 2_000, True)


def test_llm_call_log_line_has_no_prompt_fields(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    log_stream: io.StringIO,
) -> None:
    fake_messages.queue(make_message())

    _call(llm)

    assert {"messages", "system", "tools"}.isdisjoint(_llm_record(log_stream))


@pytest.mark.parametrize("secret", ["Marcus", SYSTEM], ids=["employee-name", "system-prompt"])
def test_llm_call_log_never_contains_prompt_text(
    llm: LLMClient,
    fake_messages: Any,
    make_message: Callable[..., Message],
    log_stream: io.StringIO,
    secret: str,
) -> None:
    # The request text holds an employee name: it must appear nowhere in the log.
    fake_messages.queue(make_message())

    _call(llm)

    assert secret not in log_stream.getvalue()

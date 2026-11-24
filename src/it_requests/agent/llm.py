"""LLMClient: the one class every paid model call goes through (PLAN §2, §4, §6).

Cost tracking and tracing live here, so they can't be bypassed: each call opens an
"llm.call" span, appends one line to the cost ledger and logs one "llm.call" event.
"""

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from anthropic.types import Message, Usage
from opentelemetry.trace import Tracer

from it_requests import config
from it_requests.agent import costs
from it_requests.agent.prompts import PROMPT_VERSION
from it_requests.observability import log_event

Env = Literal["dev", "eval", "demo"]


class CreateMessage(Protocol):
    """The one call shape we use: client.messages.create(**kwargs) -> Message.

    A Protocol, not the SDK method's own type: mypy --strict rejects passing
    client.messages.create directly, because its overloads include streaming (which returns
    a Stream, not a Message). The caller adapts the SDK in a few lines:

        def create(**kwargs: Any) -> Message:
            message: Message = client.messages.create(stream=False, **kwargs)
            return message

    Tests pass FakeMessages.create, which has the same shape.
    """

    def __call__(self, **kwargs: Any) -> Message: ...


def usage_numbers(model: str, usage: Usage) -> dict[str, Any]:
    """Token counts and cost for one response, as written to the ledger, span and logs.

    One function for every caller, so a run's reported cost always equals its ledger line.
    """
    numbers: dict[str, Any] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        # The API may send null for these; that means no cache use, so 0.
        "cache_read_tokens": usage.cache_read_input_tokens or 0,
        "cache_write_tokens": usage.cache_creation_input_tokens or 0,
    }
    numbers["cost_usd"] = costs.cost_usd(model, **numbers)
    return numbers


def system_blocks(text: str) -> list[dict[str, Any]]:
    """The system prompt as one text block with an ephemeral cache breakpoint.

    The system prompt and tools are resent on every step of the loop. The cache prefix is
    built in the order tools, system, messages, so this one breakpoint caches both: steps 2
    onwards read them at the cache-read price. The ledger already records cache read and
    write tokens, so the saving shows there.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class LLMClient:
    """Send one model request, and record its tokens, cost, latency and span."""

    def __init__(
        self,
        *,
        create: CreateMessage,
        model: str,
        env: Env,
        ledger_path: Path,
        tracer: Tracer,
        logger: logging.Logger,
        clock: Callable[[], float],
    ) -> None:
        # Everything is injected: tests pass a fake create, a temporary ledger, a local
        # tracer and a scripted clock, so no test spends money or depends on timing.
        self._create = create
        self._model = model
        self._env = env
        self._ledger_path = ledger_path
        self._tracer = tracer
        self._logger = logger
        self._clock = clock  # monotonic, for latency only

    @property
    def model(self) -> str:
        return self._model

    def call(
        self,
        *,
        request_id: str,
        run_id: str,
        stage: str,
        # A plain string (M5), or text blocks with cache_control (system_blocks), passed as is.
        system: str | Sequence[Mapping[str, Any]],
        messages: Iterable[Mapping[str, Any]],
        tools: Iterable[Mapping[str, Any]],
    ) -> Message:
        """Make one model call and record it. Exceptions from `create` propagate.

        On failure the span is marked ERROR (the context manager does that) and no ledger
        line is written: no tokens are billed for a failed request. No retry here: the SDK
        already retries 429 and 5xx twice.
        """
        with self._tracer.start_as_current_span("llm.call") as span:
            span.set_attributes(
                {"model": self._model, "prompt_version": PROMPT_VERSION, "stage": stage}
            )
            start = self._clock()
            response = self._create(
                model=self._model,
                max_tokens=config.MAX_TOKENS_PER_CALL,  # caps the cost of one call
                system=system,
                messages=messages,
                tools=tools,
                # auto, not forced: forced tool_choice returns a 400 on Sonnet 5.5, and the
                # same code must run on both models. The prompt asks for the tool instead.
                tool_choice={"type": "auto"},
                thinking={"type": "adaptive"},
            )
            latency_ms = round((self._clock() - start) * 1000, 1)

            numbers = usage_numbers(self._model, response.usage)
            numbers["latency_ms"] = latency_ms
            stop_reason = response.stop_reason

            span.set_attributes({**numbers, "stop_reason": stop_reason or "none"})
            self._append_ledger(
                {
                    # Wall-clock time for the ledger entry; latency uses the monotonic clock.
                    "ts": datetime.now(UTC).isoformat(),
                    "request_id": request_id,
                    "run_id": run_id,
                    "stage": stage,
                    "model": self._model,
                    "prompt_version": PROMPT_VERSION,
                    **numbers,
                    "env": self._env,
                    "stop_reason": stop_reason,
                }
            )
            # Numbers only: never the messages, the system prompt, the request text or
            # anything from the environment (CLAUDE.md, PLAN §5).
            log_event(
                self._logger,
                "llm.call",
                request_id=request_id,
                run_id=run_id,
                stage=stage,
                model=self._model,
                prompt_version=PROMPT_VERSION,
                env=self._env,
                stop_reason=stop_reason,
                **numbers,
            )
            return response

    def _append_ledger(self, entry: dict[str, Any]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        # Append-only: the budget gate sums every line, so nothing is ever rewritten.
        with self._ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry) + "\n")

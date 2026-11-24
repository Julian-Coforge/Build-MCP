"""Fixtures shared by every test layer: a fake Anthropic messages API.

Tests and CI never call the real Claude API (CLAUDE.md). The fake returns real
anthropic.types.Message objects built with the SDK's own types, so a field-name mistake in
the code under test, or here, fails the test instead of passing against a hand-made dict.
"""

from collections.abc import Callable
from typing import Any

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage


class FakeMessages:
    """Stands in for client.messages: create(**kwargs) records the call, returns the next item.

    Queue Message objects to return, or exceptions to raise (to simulate a failed request).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._queue: list[Message | BaseException] = []

    def queue(self, *items: Message | BaseException) -> None:
        self._queue.extend(items)

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError("FakeMessages.create called with nothing queued")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def build_message(
    *,
    model: str = "claude-haiku-5-5",
    tool_name: str | None = "get_employee_info",
    tool_input: dict[str, object] | None = None,
    text: str | None = None,
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    stop_reason: str = "tool_use",
) -> Message:
    """A real SDK Message: an optional text block, then an optional tool_use block."""
    content: list[Any] = []
    if text is not None:
        content.append(TextBlock(type="text", text=text))
    if tool_name is not None:
        content.append(
            ToolUseBlock(
                type="tool_use",
                id="toolu_01",
                name=tool_name,
                input=tool_input if tool_input is not None else {"employee_id": "E1002"},
            )
        )
    return Message(
        id="msg_01",
        type="message",
        role="assistant",
        model=model,
        content=content,
        stop_reason=stop_reason,  # type: ignore[arg-type]
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        ),
    )


@pytest.fixture
def fake_messages() -> FakeMessages:
    return FakeMessages()


@pytest.fixture
def make_message() -> Callable[..., Message]:
    # A fixture (not an import), because test folders are not packages.
    return build_message

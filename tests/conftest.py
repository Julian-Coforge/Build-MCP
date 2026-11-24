"""Fixtures shared by every test layer: a fake Anthropic messages API.

Tests and CI never call the real Claude API (CLAUDE.md). The fake returns real
anthropic.types.Message objects built with the SDK's own types, so a field-name mistake in
the code under test, or here, fails the test instead of passing against a hand-made dict.
"""

from collections.abc import Callable
from typing import Any

import pytest
from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage


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
    tool_uses: list[tuple[str, dict[str, object]]] | None = None,
    thinking: str | None = None,
) -> Message:
    """A real SDK Message: optional thinking, optional text, then tool_use blocks.

    `tool_uses` gives several (name, input) pairs, one tool_use block each, with ids
    toolu_01, toolu_02, ... in order. When it is given, tool_name and tool_input are ignored,
    so every existing caller keeps its one-block behaviour.
    """
    content: list[Any] = []
    if thinking is not None:
        # A signature makes it a real, replayable block: the loop must send it back as is.
        content.append(ThinkingBlock(type="thinking", thinking=thinking, signature="sig_01"))
    if text is not None:
        content.append(TextBlock(type="text", text=text))
    if tool_uses is None and tool_name is not None:
        tool_uses = [
            (tool_name, tool_input if tool_input is not None else {"employee_id": "E1002"})
        ]
    for index, (name, block_input) in enumerate(tool_uses or [], start=1):
        content.append(
            ToolUseBlock(type="tool_use", id=f"toolu_{index:02d}", name=name, input=block_input)
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
def anyio_backend() -> str:
    # The anyio pytest plugin (installed with mcp) runs async tests in both layers. Pin
    # asyncio, because the server and client are only used on asyncio, and trio isn't
    # installed. Moved here from tests/integration/ once unit tests became async too.
    return "asyncio"


@pytest.fixture
def fake_messages() -> FakeMessages:
    return FakeMessages()


@pytest.fixture
def make_message() -> Callable[..., Message]:
    # A fixture (not an import), because test folders are not packages.
    return build_message

"""Tool definitions for the ReAct loop (PLAN §2 design decisions 2 and 3).

Every MCP tool gets one extra required `thought` argument, so every Action carries a Thought
even though the model's own thinking is hidden. The agent strips it before calling the
server, so the MCP tool signatures stay exactly as the brief specifies. The decision comes
back through `final_answer`, as data the guards can check instead of prose to parse.
"""

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from it_requests.agent.guards import FinalAnswer
from it_requests.reviews import ESCALATION_CODES

_THOUGHT_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": "Why you are calling this tool now, in one or two sentences.",
}
_DECISIONS = ("approve", "deny", "escalate")
_NULLABLE_STRING: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def with_thought(claude_tools: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy each tool with a required `thought` as its first property, in strict mode.

    `strict` is a field of the tool definition (next to input_schema) in the Messages API;
    strict mode needs additionalProperties: false in the schema. Strict replaces the forced
    tool_choice that Sonnet 5.5 rejects: the arguments are guaranteed to match the schema.
    Deep-copied, so the server's tool list can be reused unchanged.
    """
    wrapped = []
    for tool in claude_tools:
        new = copy.deepcopy(dict(tool))
        schema = new["input_schema"]
        properties = schema.get("properties", {})
        # thought first: the model writes properties in order, so it reasons before it
        # fills in the arguments the reasoning is about.
        schema["properties"] = {
            "thought": dict(_THOUGHT_PROPERTY),
            **{k: v for k, v in properties.items() if k != "thought"},
        }
        schema["required"] = ["thought", *(r for r in schema.get("required", []) if r != "thought")]
        schema["additionalProperties"] = False
        new["strict"] = True
        wrapped.append(new)
    return wrapped


FINAL_ANSWER_TOOL: dict[str, Any] = {
    "name": "final_answer",
    "description": (
        "Give the decision and the reply to the employee. Call this exactly once, last, when "
        "the decision is made and every tool call it needs has returned. Use null for a field "
        "that does not apply: rule_id is null when escalating; review_id and reason_code are "
        "null when approving or denying."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "thought": dict(_THOUGHT_PROPERTY),
            "decision": {"type": "string", "enum": list(_DECISIONS)},
            "rule_id": copy.deepcopy(_NULLABLE_STRING),
            "review_id": copy.deepcopy(_NULLABLE_STRING),
            # The same code list the review tool validates, so the two can't drift apart.
            "reason_code": {
                "anyOf": [{"type": "string", "enum": list(ESCALATION_CODES)}, {"type": "null"}]
            },
            "draft_reply": {"type": "string"},
        },
        # Every property required (null carries "not applicable"), so the model always
        # states each field explicitly instead of silently leaving one out.
        "required": ["thought", "decision", "rule_id", "review_id", "reason_code", "draft_reply"],
        "additionalProperties": False,
    },
}


def strip_thought(tool_input: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Split a tool call's input into (thought, arguments for the server). Pure."""
    thought = tool_input.get("thought")
    arguments = {k: v for k, v in tool_input.items() if k != "thought"}
    # A missing thought becomes "": the trace shows an empty Thought, the run goes on.
    return (thought if isinstance(thought, str) else ""), arguments


def parse_final_answer(tool_input: Mapping[str, Any]) -> FinalAnswer:
    """Build a FinalAnswer from final_answer's input. Raises ValueError if malformed.

    Strict mode guarantees the schema on a normal response, but a refusal or a response cut
    off at max_tokens may not match it. Only types are checked here; whether the answer is
    allowed is the guards' job (check_final_answer).
    """
    decision = tool_input.get("decision")
    if decision not in _DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(_DECISIONS)}")
    fields: dict[str, str | None] = {}
    for name in ("rule_id", "review_id", "reason_code", "draft_reply"):
        value = tool_input.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or null")
        fields[name] = value
    return FinalAnswer(decision=decision, **fields)

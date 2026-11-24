"""Tests for the ReAct tool definitions: `thought` on every tool, and final_answer (PLAN §2).

The input tools copy the real MCP server's schemas (as to_claude_tools returns them, with
the SDK's `title` keys), so with_thought is tested on the exact shape it will receive.
"""

import copy
from typing import Any

import pytest

from it_requests.agent.guards import FinalAnswer
from it_requests.agent.tools import (
    FINAL_ANSWER_TOOL,
    parse_final_answer,
    strip_thought,
    with_thought,
)

SERVER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_employee_info",
        "description": "Look up an employee.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"title": "Employee Id", "type": "string"}},
            "required": ["employee_id"],
            "title": "get_employee_infoArguments",
        },
    },
    {
        "name": "check_request_eligibility",
        "description": "Decide eligibility.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"title": "Employee Id", "type": "string"},
                "item": {"title": "Item", "type": "string"},
            },
            "required": ["employee_id", "item"],
            "title": "check_request_eligibilityArguments",
        },
    },
]

THOUGHT_DESCRIPTION = "Why you are calling this tool now, in one or two sentences."
CODES = [f"E{n}" for n in range(1, 12)]


# --- with_thought ---------------------------------------------------------------------------


def test_thought_is_the_first_property_and_required() -> None:
    for tool in with_thought(SERVER_TOOLS):
        schema = tool["input_schema"]
        assert next(iter(schema["properties"])) == "thought"
        assert schema["properties"]["thought"] == {
            "type": "string",
            "description": THOUGHT_DESCRIPTION,
        }
        assert schema["required"][0] == "thought"


def test_original_properties_and_required_are_kept_in_order() -> None:
    (_, eligibility) = with_thought(SERVER_TOOLS)
    schema = eligibility["input_schema"]
    assert list(schema["properties"]) == ["thought", "employee_id", "item"]
    assert schema["required"] == ["thought", "employee_id", "item"]
    assert schema["properties"]["item"] == {"title": "Item", "type": "string"}


def test_every_tool_is_strict_with_no_additional_properties() -> None:
    # `strict` is a field of the tool definition in the Messages API, next to input_schema;
    # additionalProperties: false belongs in the schema and is required by strict mode.
    for tool in with_thought(SERVER_TOOLS):
        assert tool["strict"] is True
        assert tool["input_schema"]["additionalProperties"] is False


def test_name_and_description_are_unchanged() -> None:
    wrapped = with_thought(SERVER_TOOLS)
    assert [(t["name"], t["description"]) for t in wrapped] == [
        (t["name"], t["description"]) for t in SERVER_TOOLS
    ]


def test_input_list_is_not_mutated() -> None:
    before = copy.deepcopy(SERVER_TOOLS)
    wrapped = with_thought(SERVER_TOOLS)
    assert before == SERVER_TOOLS
    # Deep copy, not shallow: editing the output must not reach back into the input.
    wrapped[0]["input_schema"]["properties"]["employee_id"]["type"] = "integer"
    assert SERVER_TOOLS[0]["input_schema"]["properties"]["employee_id"]["type"] == "string"


def test_empty_tool_list_gives_empty_list() -> None:
    assert with_thought([]) == []


# --- FINAL_ANSWER_TOOL ----------------------------------------------------------------------


def test_final_answer_tool_is_strict_and_named() -> None:
    assert FINAL_ANSWER_TOOL["name"] == "final_answer"
    assert FINAL_ANSWER_TOOL["strict"] is True
    assert FINAL_ANSWER_TOOL["input_schema"]["additionalProperties"] is False
    description = FINAL_ANSWER_TOOL["description"].lower()
    assert "exactly once" in description
    assert "last" in description


def test_final_answer_lists_every_property_as_required() -> None:
    schema = FINAL_ANSWER_TOOL["input_schema"]
    expected = ["thought", "decision", "rule_id", "review_id", "reason_code", "draft_reply"]
    assert list(schema["properties"]) == expected
    assert schema["required"] == expected


def test_final_answer_property_schemas() -> None:
    props = FINAL_ANSWER_TOOL["input_schema"]["properties"]
    assert props["thought"]["type"] == "string"
    assert props["decision"]["type"] == "string"
    assert props["decision"]["enum"] == ["approve", "deny", "escalate"]
    # null carries "not applicable": every property is required, so none can be left out.
    for field in ("rule_id", "review_id"):
        assert props[field]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert props["reason_code"]["anyOf"] == [
        {"type": "string", "enum": CODES},
        {"type": "null"},
    ]
    assert props["draft_reply"]["type"] == "string"


def test_final_answer_codes_match_the_review_tool() -> None:
    # One list of codes: the answer can't accept a code the review tool would reject.
    from it_requests.reviews import ESCALATION_CODES

    props = FINAL_ANSWER_TOOL["input_schema"]["properties"]
    assert props["reason_code"]["anyOf"][0]["enum"] == list(ESCALATION_CODES)


# --- strip_thought --------------------------------------------------------------------------


def test_strip_thought_splits_thought_from_arguments() -> None:
    tool_input = {"thought": "Need the role first.", "employee_id": "E1002"}
    assert strip_thought(tool_input) == ("Need the role first.", {"employee_id": "E1002"})


def test_strip_thought_does_not_mutate_input() -> None:
    tool_input = {"thought": "Why.", "employee_id": "E1002"}
    strip_thought(tool_input)
    assert tool_input == {"thought": "Why.", "employee_id": "E1002"}


def test_missing_thought_becomes_empty_string() -> None:
    # Strict mode should always send it; if not, the run goes on with an empty Thought line
    # rather than crashing on a trace detail.
    assert strip_thought({"employee_id": "E1002"}) == ("", {"employee_id": "E1002"})


# --- parse_final_answer ---------------------------------------------------------------------


def test_parse_final_answer_builds_a_final_answer() -> None:
    tool_input = {
        "thought": "Eligible under R-NEW.",
        "decision": "approve",
        "rule_id": "R-NEW",
        "review_id": None,
        "reason_code": None,
        "draft_reply": "Approved: your role allows 2 monitors and you have 1 (R-NEW).",
    }
    assert parse_final_answer(tool_input) == FinalAnswer(
        decision="approve",
        rule_id="R-NEW",
        review_id=None,
        reason_code=None,
        draft_reply="Approved: your role allows 2 monitors and you have 1 (R-NEW).",
    )


def test_parse_final_answer_escalation() -> None:
    tool_input = {
        "thought": "Stolen phone.",
        "decision": "escalate",
        "rule_id": None,
        "review_id": "REV-0001",
        "reason_code": "E6",
        "draft_reply": "Sent for review, reference REV-0001.",
    }
    answer = parse_final_answer(tool_input)
    assert (answer.decision, answer.review_id, answer.reason_code) == ("escalate", "REV-0001", "E6")


@pytest.mark.parametrize(
    "bad",
    [
        {"decision": "maybe", "rule_id": None, "draft_reply": "x"},
        {"rule_id": "R-NEW", "draft_reply": "x"},  # no decision
        {"decision": "approve", "rule_id": 7, "draft_reply": "x"},
        {"decision": "approve", "rule_id": "R-NEW", "draft_reply": 3},
    ],
)
def test_parse_final_answer_rejects_malformed_input(bad: dict[str, Any]) -> None:
    # Strict mode guarantees the schema, but a refusal or a truncated response can break it.
    # A ValueError lets the loop send the problem back instead of acting on bad data.
    with pytest.raises(ValueError):
        parse_final_answer(bad)

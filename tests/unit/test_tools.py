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


TOOL_IDS = [tool["name"] for tool in SERVER_TOOLS]
WRAPPED = with_thought(SERVER_TOOLS)


# ---------- with_thought ----------


@pytest.mark.parametrize("tool", WRAPPED, ids=TOOL_IDS)
def test_with_thought_puts_thought_first_in_properties(tool: dict[str, Any]) -> None:
    assert next(iter(tool["input_schema"]["properties"])) == "thought"


@pytest.mark.parametrize("tool", WRAPPED, ids=TOOL_IDS)
def test_with_thought_thought_is_a_described_string(tool: dict[str, Any]) -> None:
    assert tool["input_schema"]["properties"]["thought"] == {
        "type": "string",
        "description": THOUGHT_DESCRIPTION,
    }


@pytest.mark.parametrize("tool", WRAPPED, ids=TOOL_IDS)
def test_with_thought_thought_is_required_first(tool: dict[str, Any]) -> None:
    assert tool["input_schema"]["required"][0] == "thought"


def test_with_thought_keeps_original_properties_in_order() -> None:
    (_, eligibility) = with_thought(SERVER_TOOLS)

    assert eligibility["input_schema"]["properties"] == {
        "thought": {"type": "string", "description": THOUGHT_DESCRIPTION},
        "employee_id": {"title": "Employee Id", "type": "string"},
        "item": {"title": "Item", "type": "string"},
    }


def test_with_thought_keeps_original_required_in_order() -> None:
    (_, eligibility) = with_thought(SERVER_TOOLS)

    assert eligibility["input_schema"]["required"] == ["thought", "employee_id", "item"]


@pytest.mark.parametrize("tool", WRAPPED, ids=TOOL_IDS)
def test_with_thought_marks_every_tool_strict(tool: dict[str, Any]) -> None:
    # `strict` is a field of the tool definition in the Messages API, next to input_schema.
    assert tool["strict"] is True


@pytest.mark.parametrize("tool", WRAPPED, ids=TOOL_IDS)
def test_with_thought_forbids_additional_properties(tool: dict[str, Any]) -> None:
    # additionalProperties: false belongs in the schema and is required by strict mode.
    assert tool["input_schema"]["additionalProperties"] is False


def test_with_thought_keeps_name_and_description() -> None:
    wrapped = with_thought(SERVER_TOOLS)

    assert [(t["name"], t["description"]) for t in wrapped] == [
        (t["name"], t["description"]) for t in SERVER_TOOLS
    ]


def test_with_thought_does_not_mutate_the_input_list() -> None:
    before = copy.deepcopy(SERVER_TOOLS)

    with_thought(SERVER_TOOLS)

    assert before == SERVER_TOOLS


def test_with_thought_output_is_a_deep_copy() -> None:
    # Deep copy, not shallow: editing the output must not reach back into the input.
    wrapped = with_thought(SERVER_TOOLS)

    wrapped[0]["input_schema"]["properties"]["employee_id"]["type"] = "integer"

    assert SERVER_TOOLS[0]["input_schema"]["properties"]["employee_id"]["type"] == "string"


def test_with_thought_replaces_an_existing_thought_property() -> None:
    # A server tool that already has a `thought` gets ours, once, not two.
    tool = copy.deepcopy(SERVER_TOOLS[0])
    tool["input_schema"]["properties"]["thought"] = {"type": "integer"}
    tool["input_schema"]["required"].append("thought")

    (wrapped,) = with_thought([tool])

    assert (
        wrapped["input_schema"]["properties"]["thought"]["type"],
        wrapped["input_schema"]["required"].count("thought"),
    ) == ("string", 1)


def test_with_thought_empty_tool_list_gives_empty_list() -> None:
    assert with_thought([]) == []


# ---------- FINAL_ANSWER_TOOL ----------

FINAL_ANSWER_FIELDS = ["thought", "decision", "rule_id", "review_id", "reason_code", "draft_reply"]


def test_final_answer_tool_is_named_final_answer() -> None:
    assert FINAL_ANSWER_TOOL["name"] == "final_answer"


def test_final_answer_tool_is_strict() -> None:
    assert FINAL_ANSWER_TOOL["strict"] is True


def test_final_answer_tool_forbids_additional_properties() -> None:
    assert FINAL_ANSWER_TOOL["input_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("phrase", ["exactly once", "last"], ids=["exactly-once", "last"])
def test_final_answer_tool_description_says_call_once_last(phrase: str) -> None:
    assert phrase in FINAL_ANSWER_TOOL["description"].lower()


def test_final_answer_tool_lists_properties_in_order() -> None:
    assert list(FINAL_ANSWER_TOOL["input_schema"]["properties"]) == FINAL_ANSWER_FIELDS


def test_final_answer_tool_requires_every_property() -> None:
    # null carries "not applicable": every property is required, so none can be left out.
    assert FINAL_ANSWER_TOOL["input_schema"]["required"] == FINAL_ANSWER_FIELDS


def test_final_answer_tool_property_schemas() -> None:
    assert FINAL_ANSWER_TOOL["input_schema"]["properties"] == {
        "thought": {"type": "string", "description": THOUGHT_DESCRIPTION},
        "decision": {"type": "string", "enum": ["approve", "deny", "escalate"]},
        "rule_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "review_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "reason_code": {"anyOf": [{"type": "string", "enum": CODES}, {"type": "null"}]},
        "draft_reply": {"type": "string"},
    }


def test_final_answer_codes_match_the_review_tool() -> None:
    # One list of codes: the answer can't accept a code the review tool would reject.
    from it_requests.reviews import ESCALATION_CODES

    props = FINAL_ANSWER_TOOL["input_schema"]["properties"]

    assert props["reason_code"]["anyOf"][0]["enum"] == list(ESCALATION_CODES)


# ---------- strip_thought ----------


def test_strip_thought_splits_thought_from_arguments() -> None:
    tool_input = {"thought": "Need the role first.", "employee_id": "E1002"}

    assert strip_thought(tool_input) == ("Need the role first.", {"employee_id": "E1002"})


def test_strip_thought_does_not_mutate_input() -> None:
    tool_input = {"thought": "Why.", "employee_id": "E1002"}

    strip_thought(tool_input)

    assert tool_input == {"thought": "Why.", "employee_id": "E1002"}


def test_strip_thought_missing_thought_becomes_empty_string() -> None:
    # Strict mode should always send it; if not, the run goes on with an empty Thought line
    # rather than crashing on a trace detail.
    assert strip_thought({"employee_id": "E1002"}) == ("", {"employee_id": "E1002"})


# ---------- parse_final_answer ----------


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


def test_parse_final_answer_escalation_keeps_review_id_and_code() -> None:
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
    ids=["unknown-decision", "no-decision", "int-rule-id", "int-reply"],
)
def test_parse_final_answer_rejects_malformed_input(bad: dict[str, Any]) -> None:
    # Strict mode guarantees the schema, but a refusal or a truncated response can break it.
    # A ValueError lets the loop send the problem back instead of acting on bad data.
    with pytest.raises(ValueError):
        parse_final_answer(bad)

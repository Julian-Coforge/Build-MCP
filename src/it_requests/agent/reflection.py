"""Reflection, the deterministic half: code checks on a draft reply, plus the verdict tool.

REQUIREMENTS §11 asks the agent to review its draft before it is sent. What can be checked
mechanically is checked here, in pure code, against what the tools returned in THIS run:
invented dates (C4), a denial missing next_eligible_date (§11 q4) and an escalation missing
its review_id (§11 q5). The model's reflection pass (a later step) gets these findings and
must fix them; it judges what code can't, such as C5 promises. No I/O, no SDK, no model.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from it_requests.agent.guards import FinalAnswer, Observation, cites, latest_verdict

C4_UNSUPPORTED_DATE = "C4_UNSUPPORTED_DATE"
DENY_MISSING_NEXT_DATE = "DENY_MISSING_NEXT_DATE"
ESCALATE_MISSING_REVIEW_ID = "ESCALATE_MISSING_REVIEW_ID"
CODE_FINDINGS = (C4_UNSUPPORTED_DATE, DENY_MISSING_NEXT_DATE, ESCALATE_MISSING_REVIEW_ID)

QUESTIONS = tuple(f"q{n}" for n in range(1, 7))  # the six §11 questions
Verdict = Literal["CONFIRMED", "REVISED"]
_VERDICTS = ("CONFIRMED", "REVISED")

# Any YYYY-MM-DD, even an impossible one: an invented impossible date is still invented.
_DATE = re.compile(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b")
# A sentence ends at ". ", "! " or "? " (so "$1.50" stays whole), or at a line break. The
# group keeps each separator, so the sentences that remain are rejoined exactly as written.
_BOUNDARY = re.compile(r"((?<=[.!?]) +|\n+)")


def _supporting_text(observations: Sequence[Observation]) -> str:
    # The JSON text of every successful result: a date anywhere in it counts as support,
    # nested fields included, with no list of date fields to keep in step with the tools.
    # Error results are not data, so they support nothing (the same rule as the guards).
    return "\n".join(json.dumps(o.result) for o in observations if "error" not in o.result)


def unsupported_dates(reply: str, observations: Sequence[Observation]) -> list[str]:
    """Dates in `reply` that appear in no successful observation: in order, no duplicates."""
    support = _supporting_text(observations)
    found: list[str] = []
    for date in _DATE.findall(reply):
        if date not in support and date not in found:
            found.append(date)
    return found


def code_findings(answer: FinalAnswer, observations: Sequence[Observation]) -> list[str]:
    """Finding codes for the draft, in CODE_FINDINGS order. Empty when the draft is clean."""
    reply = answer.draft_reply or ""
    findings = []
    if unsupported_dates(reply, observations):
        findings.append(C4_UNSUPPORTED_DATE)
    if answer.decision == "deny":
        verdict = latest_verdict(observations)
        next_date = verdict.get("next_eligible_date") if verdict else None
        if isinstance(next_date, str) and next_date not in reply:
            findings.append(DENY_MISSING_NEXT_DATE)
    # The guards' C3 check already blocks this; kept because it is cheap and §11 asks it.
    if answer.decision == "escalate" and not cites(reply, answer.review_id):
        findings.append(ESCALATE_MISSING_REVIEW_ID)
    return findings


def remove_sentences_with(reply: str, tokens: Sequence[str]) -> str:
    """Drop every sentence that contains any token; return the rest, stripped.

    Used to remove an unsupported date without touching the rest of the reply. The
    separator after a dropped sentence goes with it, so no double spaces are left behind.
    """
    pieces = _BOUNDARY.split(reply)  # sentence, separator, sentence, ..., sentence
    kept = []
    for index in range(0, len(pieces), 2):
        sentence = pieces[index]
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        if not any(token in sentence for token in tokens):
            kept.append(sentence + separator)
    return "".join(kept).strip()


_NULLABLE_STRING: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "null"}]}

REFLECTION_TOOL: dict[str, Any] = {
    "name": "reflection_verdict",
    "description": (
        "Give your reflection on the draft reply. Call this exactly once. Answer each of the "
        "six checklist questions true (yes) or false (no). Use CONFIRMED, with revised_reply "
        "and what_changed null, only when every answer is true and there are no code "
        "findings; otherwise use REVISED with the full corrected reply."
    ),
    # Strict, like the agent's tools: the verdict comes back as data the loop can trust.
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "answers": {
                "type": "object",
                "properties": {q: {"type": "boolean"} for q in QUESTIONS},
                "required": list(QUESTIONS),
                "additionalProperties": False,
            },
            "verdict": {"type": "string", "enum": list(_VERDICTS)},
            "revised_reply": dict(_NULLABLE_STRING),
            "what_changed": dict(_NULLABLE_STRING),
        },
        # Every property required; null carries "not applicable" (as in final_answer).
        "required": ["thought", "answers", "verdict", "revised_reply", "what_changed"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class ReflectionVerdict:
    thought: str
    answers: dict[str, bool]
    verdict: Verdict
    revised_reply: str | None
    what_changed: str | None


def parse_verdict(tool_input: Mapping[str, Any]) -> ReflectionVerdict:
    """Build a ReflectionVerdict. Raises ValueError on any shape problem.

    Strict mode should guarantee the schema, but a refusal or a cut-off response may not
    match it, and two rules are beyond a schema: REVISED needs a non-blank reply, and
    CONFIRMED must not carry one (the two contradict, so neither can be trusted).
    """
    thought = tool_input.get("thought")
    if not isinstance(thought, str):
        raise ValueError("thought must be a string")
    answers = tool_input.get("answers")
    if (
        not isinstance(answers, Mapping)
        or set(answers) != set(QUESTIONS)
        or not all(isinstance(value, bool) for value in answers.values())
    ):
        raise ValueError("answers must hold q1 to q6, each true or false")
    verdict = tool_input.get("verdict")
    if verdict not in _VERDICTS:
        raise ValueError("verdict must be CONFIRMED or REVISED")
    revised_reply = tool_input.get("revised_reply")
    what_changed = tool_input.get("what_changed")
    for name, value in (("revised_reply", revised_reply), ("what_changed", what_changed)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or null")
    if verdict == "REVISED" and not (revised_reply and revised_reply.strip()):
        raise ValueError("a REVISED verdict needs a non-empty revised_reply")
    if verdict == "CONFIRMED" and revised_reply is not None:
        raise ValueError("a CONFIRMED verdict must have revised_reply null")
    return ReflectionVerdict(
        thought=thought,
        answers=dict(answers),  # a copy: the verdict never shares the caller's dict
        verdict=verdict,
        revised_reply=revised_reply,
        what_changed=what_changed,
    )

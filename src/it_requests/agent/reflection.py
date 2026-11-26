"""Reflection, the deterministic half: code checks on a draft reply, plus the verdict tool.

REQUIREMENTS §11 asks the agent to review its draft before it is sent. What can be checked
mechanically is checked here, in pure code, against what the tools returned in THIS run:
invented dates (C4), a denial missing next_eligible_date (§11 q4) and an escalation missing
its review_id (§11 q5). The model's reflection pass (a later step) gets these findings and
must fix them; it judges what code can't, such as C5 promises.

The functions are pure. Reflector makes the one model call (stage "reflection") and then
applies the same code checks again, so a CONFIRMED from the model can't let an invented
date through, and a model rewrite can never change the decision.
"""

import dataclasses
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from anthropic.types import Message, ToolUseBlock
from opentelemetry.trace import Tracer

from it_requests.agent.guards import (
    FinalAnswer,
    Observation,
    check_final_answer,
    cites,
    latest_verdict,
)
from it_requests.agent.llm import LLMClient, system_blocks, usage_numbers
from it_requests.agent.prompts import SYSTEM_PROMPT_REFLECTION
from it_requests.observability import log_event

C4_UNSUPPORTED_DATE = "C4_UNSUPPORTED_DATE"
DENY_MISSING_NEXT_DATE = "DENY_MISSING_NEXT_DATE"
ESCALATE_MISSING_REVIEW_ID = "ESCALATE_MISSING_REVIEW_ID"
CODE_FINDINGS = (C4_UNSUPPORTED_DATE, DENY_MISSING_NEXT_DATE, ESCALATE_MISSING_REVIEW_ID)

QUESTIONS = tuple(f"q{n}" for n in range(1, 7))  # the six §11 questions
Verdict = Literal["CONFIRMED", "REVISED"]
# SKIPPED: a fallback run's reply is fixed, code-written text, so there is nothing to review.
ReflectionStatus = Literal["CONFIRMED", "REVISED", "SKIPPED"]
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


@dataclass(frozen=True)
class ReflectionResult:
    """The outcome of one reflection: the answer to send and what reflection changed."""

    status: ReflectionStatus
    changes: tuple[str, ...]
    final: FinalAnswer
    draft_reply: str | None  # the reply before reflection, kept for the evidence log
    answers: dict[str, bool] | None  # the model's six answers; None if unparsed
    # The reflection call's own usage, so the run's totals match the ledger (PLAN §4).
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


def _user_message(
    request_text: str,
    answer: FinalAnswer,
    observations: Sequence[Observation],
    findings: Sequence[str],
) -> str:
    """Everything the review needs, in one message. Per-request, so never in the system
    prompt: the system prompt stays identical across runs and cached."""
    draft = {
        "decision": answer.decision,
        "rule_id": answer.rule_id,
        "review_id": answer.review_id,
        "reason_code": answer.reason_code,
        "draft_reply": answer.draft_reply,
    }
    observed = [
        {"tool": o.tool, "arguments": o.arguments, "result": o.result} for o in observations
    ]
    return "\n\n".join(
        [
            f"Employee request:\n{request_text}",
            f"Tool observations from this run (JSON):\n{json.dumps(observed, indent=2)}",
            f"Draft (JSON):\n{json.dumps(draft, indent=2)}",
            f"Code findings: {', '.join(findings) if findings else 'none'}",
        ]
    )


class Reflector:
    """Runs the §11 review of one accepted draft: one model call, then the code checks."""

    def __init__(self, *, llm: LLMClient, tracer: Tracer, logger: logging.Logger) -> None:
        # Injected, like LLMClient's own parts: tests pass FakeMessages and a local tracer.
        self._llm = llm
        self._tracer = tracer
        self._logger = logger

    def reflect(
        self,
        *,
        request_id: str,
        run_id: str,
        request_text: str,
        answer: FinalAnswer,
        observations: Sequence[Observation],
    ) -> ReflectionResult:
        findings = code_findings(answer, observations)
        with self._tracer.start_as_current_span("reflection") as span:
            response = self._llm.call(
                request_id=request_id,
                run_id=run_id,
                stage="reflection",
                system=system_blocks(SYSTEM_PROMPT_REFLECTION),
                messages=[
                    {
                        "role": "user",
                        "content": _user_message(request_text, answer, observations, findings),
                    }
                ],
                tools=[REFLECTION_TOOL],
            )
            verdict = self._verdict(response)
            reply, changes = self._model_revision(verdict, answer, observations)
            reply, code_changes = self._code_fixes(answer, reply, observations)
            changes += code_changes

            # The copy rule: only the reply text can come from reflection. The decision,
            # rule_id, review_id and reason_code are always the accepted answer's own.
            final = dataclasses.replace(answer, draft_reply=reply)
            status: Verdict = "REVISED" if reply != answer.draft_reply else "CONFIRMED"
            span.set_attributes(
                {"result": status, "changes": "; ".join(changes), "findings": list(findings)}
            )
            # Status and changes only: the reply can hold the employee's name (PLAN §5).
            log_event(self._logger, "reflection.result", status=status, changes=changes)
            log_event(self._logger, "reflection.reply", logging.DEBUG, final_reply=reply)
            usage = usage_numbers(self._llm.model, response.usage)
            return ReflectionResult(
                status=status,
                changes=tuple(changes),
                final=final,
                draft_reply=answer.draft_reply,
                answers=verdict.answers if verdict else None,
                cost_usd=usage["cost_usd"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )

    def _verdict(self, response: Message) -> ReflectionVerdict | None:
        """The first reflection_verdict call, parsed; None (logged) if absent or malformed.

        None is handled as CONFIRMED: the accepted draft stands, and the code checks after
        this still run, so unusable output can never make the reply worse.
        """
        tool_use = next(
            (
                b
                for b in response.content
                if isinstance(b, ToolUseBlock) and b.name == REFLECTION_TOOL["name"]
            ),
            None,
        )
        if tool_use is None:
            log_event(
                self._logger, "reflection.unparsed", logging.WARNING, reason="no verdict call"
            )
            return None
        try:
            return parse_verdict(dict(tool_use.input))
        except ValueError as err:
            log_event(self._logger, "reflection.unparsed", logging.WARNING, reason=str(err))
            return None

    def _model_revision(
        self,
        verdict: ReflectionVerdict | None,
        answer: FinalAnswer,
        observations: Sequence[Observation],
    ) -> tuple[str | None, list[str]]:
        """The model's revised reply if it passes the guards again, else the draft."""
        if verdict is None or verdict.verdict != "REVISED":
            return answer.draft_reply, []
        candidate = dataclasses.replace(answer, draft_reply=verdict.revised_reply)
        codes = check_final_answer(candidate, observations)
        if codes:
            # A rewrite that breaks C1-C3 (e.g. drops the rule ID) would undo what the guards
            # accepted. The draft stands, and the discarded change is not counted.
            log_event(self._logger, "reflection.revision_discarded", logging.WARNING, codes=codes)
            return answer.draft_reply, []
        return verdict.revised_reply, [verdict.what_changed] if verdict.what_changed else []

    def _code_fixes(
        self, answer: FinalAnswer, reply: str | None, observations: Sequence[Observation]
    ) -> tuple[str | None, list[str]]:
        """Remove unsupported dates in code; log any other finding that is still there.

        Code-made, so the fault-injection demo never depends on the model spotting it.
        """
        changes: list[str] = []
        remaining = code_findings(dataclasses.replace(answer, draft_reply=reply), observations)
        if C4_UNSUPPORTED_DATE in remaining and reply is not None:
            dates = unsupported_dates(reply, observations)
            reply = remove_sentences_with(reply, dates)
            changes = [f"removed unsupported date {date}" for date in dates]
        unresolved = [f for f in remaining if f != C4_UNSUPPORTED_DATE]
        if unresolved:
            log_event(self._logger, "reflection.unresolved", logging.WARNING, findings=unresolved)
        return reply, changes

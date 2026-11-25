"""Constraint guards: may the agent make this tool call, or give this final answer?

Pure functions over what the tools returned in THIS run (REQUIREMENTS §6, §6.1). The loop
calls them before each tool call and on every final answer, so C1, C2, C3, C6 and C7 are
enforced in code, not only asked for in the prompt (PLAN §2). No I/O, no SDK, no MCP.
Field names come from the real tool results: eligibility's verdict is `status`, a
successful flag returns `review_id`, and every tool error carries `error`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from it_requests.reviews import ESCALATION_CODES

Decision = Literal["approve", "deny", "escalate"]

INVESTIGATION_TOOLS = frozenset(
    {"get_employee_info", "get_policy_limits", "check_request_eligibility"}
)
MAX_INVESTIGATION_CALLS = 8  # C6
FLAG_TOOL = "flag_for_human_review"
ELIGIBILITY_TOOL = "check_request_eligibility"


@dataclass(frozen=True)
class Observation:
    """One tool call in this run and what it returned."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class FinalAnswer:
    """The final_answer tool's fields (PLAN §2 decision 3).

    Keyword-only: five similar string fields passed by position are easy to swap silently.
    rule_id has no default but may be None, because an escalation has no rule.
    """

    decision: Decision
    rule_id: str | None
    review_id: str | None = None
    reason_code: str | None = None
    draft_reply: str | None = None


def _succeeded(observation: Observation) -> bool:
    # Every tool error is {"error": ...}; an error is not data a decision can rest on.
    return "error" not in observation.result


def _first_review_id(observations: Sequence[Observation]) -> tuple[int, str] | None:
    """Index and review_id of the first flag that created (or found) a ticket."""
    for index, obs in enumerate(observations):
        if obs.tool == FLAG_TOOL and isinstance(obs.result.get("review_id"), str):
            return index, obs.result["review_id"]
    return None


def _successful_flags(observations: Sequence[Observation]) -> int:
    return sum(
        1
        for obs in observations
        if obs.tool == FLAG_TOOL and isinstance(obs.result.get("review_id"), str)
    )


def cites(text: str | None, token: str | None) -> bool:
    """True if `token` appears in `text` as a whole ID.

    Not a plain substring check: "R-REFRESH" is inside "R-REFRESH-OK", and citing the
    wrong rule must not count as citing the right one.
    """
    if not text or not token:
        return False
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def latest_verdict(observations: Sequence[Observation]) -> dict[str, Any] | None:
    """The newest successful eligibility result.

    The latest check decides: an older ELIGIBLE (perhaps for another item) can't back an
    approval after a newer NOT_ELIGIBLE. When checks disagree the agent should escalate,
    never pick the convenient one.
    """
    for obs in reversed(observations):
        if obs.tool == ELIGIBILITY_TOOL and _succeeded(obs):
            return obs.result
    return None


def _has_succeeded(observations: Sequence[Observation], tools: set[str]) -> bool:
    found = {obs.tool for obs in observations if _succeeded(obs)}
    return tools <= found


def check_tool_call(tool: str, observations: Sequence[Observation]) -> str | None:
    """A block code if `tool` may not be called now, else None."""
    if tool in INVESTIGATION_TOOLS:
        # Every call counts, failed ones too: C6 caps calls, so a model retrying a bad ID
        # still reaches the limit. The flag call is not counted (C6 wording).
        made = sum(1 for obs in observations if obs.tool in INVESTIGATION_TOOLS)
        if made >= MAX_INVESTIGATION_CALLS:
            return "C6_STEP_LIMIT"
    elif tool == FLAG_TOOL and _first_review_id(observations) is not None:
        # Only a flag that returned a review_id counts: a rejected call wrote no ticket
        # (§7.4), so a corrected retry must stay possible.
        return "C7_ALREADY_FLAGGED"
    return None


def _check_approve(answer: FinalAnswer, observations: Sequence[Observation]) -> list[str]:
    codes = []
    verdict = latest_verdict(observations)
    if not (
        verdict is not None
        and verdict.get("status") == "ELIGIBLE"
        and verdict.get("rule_id") == answer.rule_id
    ):
        codes.append("C1")
    if not _has_succeeded(observations, {"get_employee_info", ELIGIBILITY_TOOL}):
        codes.append("REQUIRED_CALLS")
    return codes


def _check_deny(answer: FinalAnswer, observations: Sequence[Observation]) -> list[str]:
    codes = []
    verdict = latest_verdict(observations)
    if not (
        verdict is not None
        and verdict.get("status") == "NOT_ELIGIBLE"
        and verdict.get("rule_id") == answer.rule_id
        and cites(answer.draft_reply, answer.rule_id)
    ):
        codes.append("C2")
    required = {"get_employee_info", ELIGIBILITY_TOOL, "get_policy_limits"}
    if not _has_succeeded(observations, required):
        codes.append("REQUIRED_CALLS")
    return codes


def _check_escalate(answer: FinalAnswer, observations: Sequence[Observation]) -> list[str]:
    codes = []
    flagged = _first_review_id(observations)
    if not (
        flagged is not None
        and answer.review_id == flagged[1]
        and cites(answer.draft_reply, answer.review_id)
        and answer.reason_code in ESCALATION_CODES
    ):
        codes.append("C3")
    if answer.reason_code == "E7":
        # Decision table row 3: the lookup and the check come BEFORE the flag, so the
        # escalation rests on them. Without a flag, only what exists is checked (C3 fires).
        before = observations[: flagged[0]] if flagged is not None else observations
        if not _has_succeeded(before, {"get_employee_info", ELIGIBILITY_TOOL}):
            codes.append("REQUIRED_CALLS")
    return codes


_CHECKS = {"approve": _check_approve, "deny": _check_deny, "escalate": _check_escalate}


def check_final_answer(answer: FinalAnswer, observations: Sequence[Observation]) -> list[str]:
    """Violation codes for `answer`, empty if it may be sent.

    All violations are returned, in the fixed order C1, C2, C3, REQUIRED_CALLS, C7, so the
    model can fix everything in one retry and tests can compare lists exactly.
    """
    codes = _CHECKS[answer.decision](answer, observations)
    # C7 is checked for every decision: a second ticket is wrong whatever the outcome.
    if _successful_flags(observations) > 1:
        codes.append("C7")
    return codes


_MESSAGES = {
    "C1": "C1: you may approve only after check_request_eligibility returned ELIGIBLE in "
    "this run, and rule_id must be the rule_id that check returned.",
    "C2": "C2: you may deny only after check_request_eligibility returned NOT_ELIGIBLE in "
    "this run, rule_id must be the rule_id that check returned, and draft_reply must cite it.",
    "C3": "C3: every escalation needs a successful flag_for_human_review call in this run, "
    "review_id must be the review_id it returned and appear in draft_reply, and reason_code "
    "must be one of E1 to E11.",
    "REQUIRED_CALLS": "REQUIRED_CALLS: approve needs successful get_employee_info and "
    "check_request_eligibility calls, deny also needs get_policy_limits, and an E7 "
    "escalation needs get_employee_info and check_request_eligibility before the flag.",
    "C7": "C7: flag_for_human_review may succeed at most once per request.",
    "C6_STEP_LIMIT": "C6_STEP_LIMIT: you have made 8 investigation calls, the maximum; "
    "escalate with flag_for_human_review using reason E11.",
    "C7_ALREADY_FLAGGED": "C7_ALREADY_FLAGGED: this request is already flagged; do not call "
    "flag_for_human_review again, use the review_id it returned.",
}


def violation_message(codes: Sequence[str]) -> str:
    """One plain-English sentence per code, in order, for the loop to send to the model.

    An unknown code raises KeyError: it is a programming error in the loop, not something
    the model could act on.
    """
    return " ".join(_MESSAGES[code] for code in codes)

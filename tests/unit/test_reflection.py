"""Tests for reflection (REQUIREMENTS §6.1 C4, §11; PLAN §2): code checks and the Reflector.

Observations come from the real pure tool functions, as in test_guards.py, so the date
search runs over the exact JSON the server returns. The Reflector runs over FakeMessages
and a temporary ledger: nothing calls the paid API.
"""

import io
import itertools
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from it_requests import config
from it_requests.agent.guards import FinalAnswer, Observation
from it_requests.agent.llm import LLMClient, system_blocks
from it_requests.agent.prompts import SYSTEM_PROMPT_REFLECTION
from it_requests.agent.reflection import (
    CODE_FINDINGS,
    REFLECTION_TOOL,
    ReflectionResult,
    ReflectionVerdict,
    Reflector,
    code_findings,
    parse_verdict,
    remove_sentences_with,
    unsupported_dates,
)
from it_requests.data import load_employees, load_policy
from it_requests.eligibility import check_request_eligibility
from it_requests.employees import get_employee_info
from it_requests.observability import JsonFormatter
from it_requests.policy import get_policy_limits
from it_requests.reviews import flag_for_human_review

DIRECTORY = load_employees(config.EMPLOYEES_FILE)
POLICY = load_policy(config.POLICY_FILE)
FIXED_NOW = datetime(2026, 11, 22, 9, 0, 0)


def _emp(employee_id: str) -> Observation:
    result = get_employee_info(employee_id, directory=DIRECTORY)
    return Observation("get_employee_info", {"employee_id": employee_id}, result)


def _elig(employee_id: str, item: str) -> Observation:
    result = check_request_eligibility(employee_id, item, directory=DIRECTORY, policy=POLICY)
    return Observation(
        "check_request_eligibility", {"employee_id": employee_id, "item": item}, result
    )


def _flag(queue_path: Path) -> Observation:
    arguments = {"employee_id": "E1003", "request": "Phone stolen.", "reason": "E6: Stolen."}
    result = flag_for_human_review(
        "E1003", "Phone stolen.", "E6: Stolen.", queue_path=queue_path, now=FIXED_NOW
    )
    return Observation("flag_for_human_review", arguments, result)


# E1001 laptop: NOT_ELIGIBLE under R-REFRESH, next_eligible_date 2028-03-10 (§8). The
# employee record also holds 2025-03-10 (the laptop's issue date) and 2021-06-14 (hire date).
DENY_OBSERVATIONS = [_emp("E1001"), _elig("E1001", "laptop")]
# E1001 mobile_phone: NOT_ELIGIBLE under R-ROLE, so next_eligible_date is null.
ROLE_DENY_OBSERVATIONS = [_emp("E1001"), _elig("E1001", "mobile_phone")]
CLEAN_DENY_REPLY = "Your laptop is due for refresh on 2028-03-10 (rule R-REFRESH)."
INVENTED_DATE = "2027-01-01"  # in no tool result for E1001


def _deny(reply: str, rule_id: str = "R-REFRESH") -> FinalAnswer:
    return FinalAnswer(decision="deny", rule_id=rule_id, draft_reply=reply)


def _escalate(reply: str, review_id: str | None = "REV-0001") -> FinalAnswer:
    return FinalAnswer(
        decision="escalate", rule_id=None, review_id=review_id, reason_code="E6", draft_reply=reply
    )


@pytest.fixture
def flag_observations(tmp_path: Path) -> list[Observation]:
    # A fresh queue per test, so the review ID is REV-0001.
    return [_flag(tmp_path / "queue.json")]


# ---------- CODE_FINDINGS ----------


def test_code_findings_constant_lists_codes_in_fixed_order() -> None:
    assert CODE_FINDINGS == (
        "C4_UNSUPPORTED_DATE",
        "DENY_MISSING_NEXT_DATE",
        "ESCALATE_MISSING_REVIEW_ID",
    )


# ---------- code_findings: clean drafts ----------


def test_code_findings_clean_deny_returns_empty() -> None:
    assert code_findings(_deny(CLEAN_DENY_REPLY), DENY_OBSERVATIONS) == []


def test_code_findings_clean_escalation_returns_empty(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("Sent for review, reference REV-0001.")

    assert code_findings(answer, flag_observations) == []


def test_code_findings_approve_without_dates_returns_empty() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW", draft_reply="Approved (R-NEW).")

    assert code_findings(answer, [_emp("E1002"), _elig("E1002", "monitor")]) == []


# ---------- code_findings: C4_UNSUPPORTED_DATE ----------


def test_code_findings_invented_date_is_flagged() -> None:
    reply = f"{CLEAN_DENY_REPLY} A new one will arrive by {INVENTED_DATE}."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == ["C4_UNSUPPORTED_DATE"]


def test_code_findings_date_from_nested_observation_is_not_flagged() -> None:
    # 2025-03-10 is the laptop's issued_date, nested in the employee's equipment list.
    reply = f"{CLEAN_DENY_REPLY} Your laptop was issued on 2025-03-10."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == []


def test_code_findings_date_only_in_a_failed_observation_is_flagged() -> None:
    # Hand-built: an error result is not data, so a date inside it supports nothing.
    # 2026-01-15 is in no successful result (the real ones hold as_of 2026-11-22).
    failed = Observation("check_request_eligibility", {}, {"error": "X", "as_of": "2026-01-15"})
    reply = f"{CLEAN_DENY_REPLY} Checked on 2026-01-15."

    assert code_findings(_deny(reply), [*DENY_OBSERVATIONS, failed]) == ["C4_UNSUPPORTED_DATE"]


def test_code_findings_no_reply_returns_empty_for_c4() -> None:
    answer = FinalAnswer(decision="approve", rule_id="R-NEW", draft_reply=None)

    assert code_findings(answer, [_emp("E1002"), _elig("E1002", "monitor")]) == []


# ---------- code_findings: DENY_MISSING_NEXT_DATE ----------


def test_code_findings_deny_without_next_eligible_date_is_flagged() -> None:
    reply = "Your laptop is not due for refresh yet (rule R-REFRESH)."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == ["DENY_MISSING_NEXT_DATE"]


def test_code_findings_deny_with_null_next_date_is_not_flagged() -> None:
    reply = "Your role is not entitled to a phone (rule R-ROLE)."

    assert code_findings(_deny(reply, "R-ROLE"), ROLE_DENY_OBSERVATIONS) == []


def test_code_findings_deny_uses_the_latest_eligibility_check() -> None:
    # The newest check (mobile_phone, R-ROLE) has no next date, so none is required.
    observations = [*DENY_OBSERVATIONS, _elig("E1001", "mobile_phone")]
    reply = "Your role is not entitled to a phone (rule R-ROLE)."

    assert code_findings(_deny(reply, "R-ROLE"), observations) == []


# ---------- code_findings: ESCALATE_MISSING_REVIEW_ID ----------


def test_code_findings_escalation_without_review_id_in_reply_is_flagged(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("A reviewer will follow up.")

    assert code_findings(answer, flag_observations) == ["ESCALATE_MISSING_REVIEW_ID"]


def test_code_findings_escalation_with_null_review_id_is_flagged(
    flag_observations: list[Observation],
) -> None:
    answer = _escalate("A reviewer will follow up.", review_id=None)

    assert code_findings(answer, flag_observations) == ["ESCALATE_MISSING_REVIEW_ID"]


# ---------- code_findings: several at once ----------


def test_code_findings_several_findings_come_in_fixed_order() -> None:
    reply = f"Not due yet (rule R-REFRESH). A new one will arrive by {INVENTED_DATE}."

    assert code_findings(_deny(reply), DENY_OBSERVATIONS) == [
        "C4_UNSUPPORTED_DATE",
        "DENY_MISSING_NEXT_DATE",
    ]


# ---------- unsupported_dates ----------


def test_unsupported_dates_reports_each_once_in_order() -> None:
    reply = "Ships 2027-02-02, arrives 2027-01-01, confirmed 2027-02-02."

    assert unsupported_dates(reply, DENY_OBSERVATIONS) == ["2027-02-02", "2027-01-01"]


def test_unsupported_dates_supported_date_is_omitted() -> None:
    reply = "Due 2028-03-10, ships 2027-01-01."

    assert unsupported_dates(reply, DENY_OBSERVATIONS) == ["2027-01-01"]


def test_unsupported_dates_empty_reply_returns_empty() -> None:
    assert unsupported_dates("", DENY_OBSERVATIONS) == []


def test_unsupported_dates_no_observations_flags_every_date() -> None:
    assert unsupported_dates("Due 2028-03-10.", []) == ["2028-03-10"]


# ---------- remove_sentences_with ----------


def test_remove_sentences_with_drops_the_sentence_holding_the_token() -> None:
    reply = "Denied (R-REFRESH). It ships by 2027-01-01. Ask the helpdesk."

    assert remove_sentences_with(reply, ["2027-01-01"]) == "Denied (R-REFRESH). Ask the helpdesk."


def test_remove_sentences_with_drops_the_last_sentence_cleanly() -> None:
    assert remove_sentences_with("Denied. Ships 2027-01-01.", ["2027-01-01"]) == "Denied."


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Approved! Ships 2027-01-01! Thanks.", "Approved! Thanks."),
        ("Approved? Ships 2027-01-01? Thanks.", "Approved? Thanks."),
        ("Line one\nShips 2027-01-01\nLine three", "Line one\nLine three"),
    ],
    ids=["exclamation", "question", "newline"],
)
def test_remove_sentences_with_splits_on_every_boundary(reply: str, expected: str) -> None:
    assert remove_sentences_with(reply, ["2027-01-01"]) == expected


def test_remove_sentences_with_keeps_decimal_points_inside_a_sentence() -> None:
    # Only ". " ends a sentence, so "$1.50" stays whole.
    reply = "The fee is $1.50 today. Ships 2027-01-01."

    assert remove_sentences_with(reply, ["2027-01-01"]) == "The fee is $1.50 today."


def test_remove_sentences_with_several_tokens_drops_each_sentence() -> None:
    reply = "A. Ships 2027-01-01. B. Arrives 2027-02-02. C."

    assert remove_sentences_with(reply, ["2027-01-01", "2027-02-02"]) == "A. B. C."


def test_remove_sentences_with_absent_token_returns_reply_stripped() -> None:
    assert remove_sentences_with("  Denied. Ask the helpdesk.  ", ["2027-01-01"]) == (
        "Denied. Ask the helpdesk."
    )


def test_remove_sentences_with_no_tokens_returns_reply_stripped() -> None:
    assert remove_sentences_with(" Denied. ", []) == "Denied."


def test_remove_sentences_with_every_sentence_removed_returns_empty() -> None:
    assert remove_sentences_with("Ships 2027-01-01.", ["2027-01-01"]) == ""


# ---------- REFLECTION_TOOL ----------

VERDICT_PROPERTIES = ["thought", "answers", "verdict", "revised_reply", "what_changed"]
QUESTIONS = [f"q{n}" for n in range(1, 7)]
SCHEMA = REFLECTION_TOOL["input_schema"]
# The six §11 answers: each a required boolean, nothing else allowed.
ANSWERS_SCHEMA = {
    "type": "object",
    "properties": {q: {"type": "boolean"} for q in QUESTIONS},
    "required": QUESTIONS,
    "additionalProperties": False,
}


def test_reflection_tool_is_named_reflection_verdict() -> None:
    assert REFLECTION_TOOL["name"] == "reflection_verdict"


def test_reflection_tool_is_strict() -> None:
    assert REFLECTION_TOOL["strict"] is True


def test_reflection_tool_forbids_additional_properties() -> None:
    assert SCHEMA["additionalProperties"] is False


def test_reflection_tool_requires_every_property_in_order() -> None:
    assert SCHEMA["required"] == VERDICT_PROPERTIES


def test_reflection_tool_lists_properties_in_order() -> None:
    assert list(SCHEMA["properties"]) == VERDICT_PROPERTIES


def test_reflection_tool_answers_are_six_required_booleans() -> None:
    assert SCHEMA["properties"]["answers"] == ANSWERS_SCHEMA


def test_reflection_tool_verdict_is_confirmed_or_revised() -> None:
    assert SCHEMA["properties"]["verdict"] == {
        "type": "string",
        "enum": ["CONFIRMED", "REVISED"],
    }


@pytest.mark.parametrize("field", ["revised_reply", "what_changed"], ids=str)
def test_reflection_tool_optional_text_is_string_or_null(field: str) -> None:
    assert SCHEMA["properties"][field] == {"anyOf": [{"type": "string"}, {"type": "null"}]}


# ---------- parse_verdict ----------

ALL_YES = {q: True for q in QUESTIONS}
CONFIRMED_INPUT: dict[str, Any] = {
    "thought": "Every answer is yes.",
    "answers": ALL_YES,
    "verdict": "CONFIRMED",
    "revised_reply": None,
    "what_changed": None,
}
REVISED_INPUT: dict[str, Any] = {
    "thought": "The draft promised a delivery date.",
    "answers": ALL_YES | {"q3": False},
    "verdict": "REVISED",
    "revised_reply": "Approved (R-NEW). IT will arrange fulfilment.",
    "what_changed": "Removed the promised delivery date.",
}


def test_parse_verdict_confirmed_builds_the_verdict() -> None:
    assert parse_verdict(CONFIRMED_INPUT) == ReflectionVerdict(
        thought="Every answer is yes.",
        answers=ALL_YES,
        verdict="CONFIRMED",
        revised_reply=None,
        what_changed=None,
    )


def test_parse_verdict_revised_keeps_the_revised_reply() -> None:
    assert parse_verdict(REVISED_INPUT).revised_reply == (
        "Approved (R-NEW). IT will arrange fulfilment."
    )


def test_parse_verdict_revised_keeps_the_answers() -> None:
    assert parse_verdict(REVISED_INPUT).answers == ALL_YES | {"q3": False}


def test_parse_verdict_does_not_mutate_input() -> None:
    before = {**REVISED_INPUT, "answers": dict(REVISED_INPUT["answers"])}

    parse_verdict(REVISED_INPUT)

    assert REVISED_INPUT == before


@pytest.mark.parametrize(
    "bad",
    [
        CONFIRMED_INPUT | {"verdict": "MAYBE"},
        {k: v for k, v in CONFIRMED_INPUT.items() if k != "verdict"},
        CONFIRMED_INPUT | {"thought": 7},
        CONFIRMED_INPUT | {"answers": "all yes"},
        CONFIRMED_INPUT | {"answers": {q: True for q in QUESTIONS[:5]}},
        CONFIRMED_INPUT | {"answers": ALL_YES | {"q7": True}},
        CONFIRMED_INPUT | {"answers": ALL_YES | {"q1": "yes"}},
        REVISED_INPUT | {"revised_reply": None},
        REVISED_INPUT | {"revised_reply": ""},
        REVISED_INPUT | {"revised_reply": "   "},
        REVISED_INPUT | {"revised_reply": 3},
        REVISED_INPUT | {"what_changed": 3},
        CONFIRMED_INPUT | {"revised_reply": "A different reply."},
    ],
    ids=[
        "unknown-verdict",
        "missing-verdict",
        "thought-not-string",
        "answers-not-object",
        "answers-missing-q6",
        "answers-extra-q7",
        "answer-not-boolean",
        "revised-with-null-reply",
        "revised-with-empty-reply",
        "revised-with-blank-reply",
        "revised-reply-not-string",
        "what-changed-not-string",
        "confirmed-with-revised-reply",
    ],
)
def test_parse_verdict_malformed_input_raises(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_verdict(bad)


# ---------- Reflector ----------

REQUEST_TEXT = "This is Priya, E1001. My laptop feels slow lately, can I get a new one?"
# A guard-safe rewrite of the clean denial: still cites R-REFRESH and the next date.
SAFE_REVISION = (
    "Your laptop is not due for refresh until 2028-03-10 (rule R-REFRESH). "
    "The IT helpdesk can help with performance."
)
SAFE_CHANGE = "Added a next step for the slow laptop."
# Drops the rule ID, so check_final_answer rejects it with C2.
UNSAFE_REVISION = "Your laptop is not due for refresh until 2028-03-10."
DRAFT_WITH_INVENTED_DATE = f"{CLEAN_DENY_REPLY} A new one will arrive by {INVENTED_DATE}."
# Leaves out next_eligible_date (2028-03-10), which no model verdict below adds.
DRAFT_MISSING_NEXT_DATE = "Your laptop is not due for refresh yet (rule R-REFRESH)."
# Reflection only ever sees drafts the guards accepted, so a denial's observations include
# the policy lookup (REQUIRED_CALLS). Without it, every revision would fail the guards.
ACCEPTED_DENY_OBSERVATIONS = [
    *DENY_OBSERVATIONS,
    Observation(
        "get_policy_limits", {"role": "standard"}, get_policy_limits("standard", policy=POLICY)
    ),
]


@dataclass
class _Env:
    """A Reflector over FakeMessages, plus everything it writes, read back for one assert."""

    reflector: Reflector
    fake: Any
    make_message: Callable[..., Message]
    spans: InMemorySpanExporter
    log: io.StringIO
    ledger_path: Path

    def queue_verdict(self, **overrides: Any) -> None:
        tool_input = CONFIRMED_INPUT | overrides
        self.fake.queue(self.make_message(tool_uses=[("reflection_verdict", tool_input)]))

    def queue_revised(self, **overrides: Any) -> None:
        tool_input = REVISED_INPUT | {"revised_reply": SAFE_REVISION, "what_changed": SAFE_CHANGE}
        self.fake.queue(
            self.make_message(tool_uses=[("reflection_verdict", tool_input | overrides)])
        )

    def reflect(self, answer: FinalAnswer) -> ReflectionResult:
        return self.reflector.reflect(
            request_id="REQ-0002",
            run_id="run-1",
            request_text=REQUEST_TEXT,
            answer=answer,
            observations=ACCEPTED_DENY_OBSERVATIONS,
        )

    def records(self, event: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in self.log.getvalue().splitlines()]
        return [r for r in lines if r["event"] == event]

    def span(self, name: str) -> ReadableSpan:
        return next(s for s in self.spans.get_finished_spans() if s.name == name)

    def sent(self) -> dict[str, Any]:
        call: dict[str, Any] = self.fake.calls[0]
        return call

    def user_message(self) -> str:
        content: str = self.sent()["messages"][0]["content"]
        return content


@pytest.fixture
def env(
    tmp_path: Path,
    fake_messages: Any,
    make_message: Callable[..., Message],
    request: pytest.FixtureRequest,
) -> _Env:
    spans = InMemorySpanExporter()
    provider = TracerProvider()  # local, never global
    provider.add_span_processor(SimpleSpanProcessor(spans))
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.reflection.{request.node.name}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)  # DEBUG on, to prove the reply stays out of INFO lines
    logger.propagate = False
    ledger_path = tmp_path / "logs" / "cost_ledger.jsonl"  # never the real logs/
    ticks = itertools.count(0.0, 0.25)
    llm = LLMClient(
        create=fake_messages.create,
        model=config.DEV_MODEL,
        env="dev",
        ledger_path=ledger_path,
        tracer=provider.get_tracer("test"),
        logger=logger,
        clock=lambda: next(ticks),
    )
    reflector = Reflector(llm=llm, tracer=provider.get_tracer("test"), logger=logger)
    return _Env(reflector, fake_messages, make_message, spans, log, ledger_path)


# ---------- Reflector: CONFIRMED ----------


def test_reflect_confirmed_clean_draft_returns_confirmed(env: _Env) -> None:
    env.queue_verdict()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.status == "CONFIRMED"


def test_reflect_confirmed_clean_draft_returns_answer_unchanged(env: _Env) -> None:
    answer = _deny(CLEAN_DENY_REPLY)
    env.queue_verdict()

    result = env.reflect(answer)

    assert result.final == answer


def test_reflect_confirmed_clean_draft_records_no_changes(env: _Env) -> None:
    env.queue_verdict()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.changes == ()


def test_reflect_keeps_the_draft_reply_before_reflection(env: _Env) -> None:
    env.queue_revised()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.draft_reply == CLEAN_DENY_REPLY


def test_reflect_returns_the_model_answers(env: _Env) -> None:
    env.queue_verdict()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.answers == ALL_YES


# ---------- Reflector: REVISED by the model ----------


def test_reflect_guard_safe_revision_returns_revised(env: _Env) -> None:
    env.queue_revised()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.status == "REVISED"


def test_reflect_guard_safe_revision_uses_the_revised_reply(env: _Env) -> None:
    env.queue_revised()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.final.draft_reply == SAFE_REVISION


def test_reflect_guard_safe_revision_records_what_changed(env: _Env) -> None:
    env.queue_revised()

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.changes == (SAFE_CHANGE,)


def test_reflect_revision_cannot_change_the_decision(env: _Env) -> None:
    # The schema has no decision field; a model that sends one anyway is ignored.
    env.queue_revised(decision="approve", rule_id="R-NEW")

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert (result.final.decision, result.final.rule_id) == ("deny", "R-REFRESH")


# ---------- Reflector: revision discarded ----------


def test_reflect_revision_failing_the_guards_is_discarded(env: _Env) -> None:
    env.queue_revised(revised_reply=UNSAFE_REVISION)

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.final.draft_reply == CLEAN_DENY_REPLY


def test_reflect_discarded_revision_gives_confirmed(env: _Env) -> None:
    env.queue_revised(revised_reply=UNSAFE_REVISION)

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.status == "CONFIRMED"


def test_reflect_discarded_revision_logs_the_guard_codes(env: _Env) -> None:
    env.queue_revised(revised_reply=UNSAFE_REVISION)

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert [(r["level"], r["codes"]) for r in env.records("reflection.revision_discarded")] == [
        ("WARNING", ["C2"])
    ]


# ---------- Reflector: code-made fixes ----------


def test_reflect_code_removes_an_unsupported_date_the_model_missed(env: _Env) -> None:
    env.queue_verdict()  # the model says CONFIRMED despite the invented date

    result = env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert result.final.draft_reply == CLEAN_DENY_REPLY


def test_reflect_code_removed_date_gives_revised(env: _Env) -> None:
    env.queue_verdict()

    result = env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert result.status == "REVISED"


def test_reflect_code_removed_date_is_named_in_changes(env: _Env) -> None:
    env.queue_verdict()

    result = env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert result.changes == (f"removed unsupported date {INVENTED_DATE}",)


def test_reflect_model_and_code_changes_are_both_recorded(env: _Env) -> None:
    # The model's own revision adds an invented date; the code then removes that sentence.
    env.queue_revised(revised_reply=f"{SAFE_REVISION} It ships by {INVENTED_DATE}.")

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.changes == (SAFE_CHANGE, f"removed unsupported date {INVENTED_DATE}")


def test_reflect_unresolved_finding_is_logged(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(DRAFT_MISSING_NEXT_DATE))

    assert [(r["level"], r["findings"]) for r in env.records("reflection.unresolved")] == [
        ("WARNING", ["DENY_MISSING_NEXT_DATE"])
    ]


# ---------- Reflector: unusable model output ----------


def test_reflect_no_tool_use_is_treated_as_confirmed(env: _Env) -> None:
    env.fake.queue(env.make_message(tool_name=None, text="Looks fine.", stop_reason="end_turn"))

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.status == "CONFIRMED"


def test_reflect_no_tool_use_logs_one_unparsed_warning(env: _Env) -> None:
    env.fake.queue(env.make_message(tool_name=None, text="Looks fine.", stop_reason="end_turn"))

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert [r["level"] for r in env.records("reflection.unparsed")] == ["WARNING"]


def test_reflect_malformed_verdict_is_treated_as_confirmed(env: _Env) -> None:
    env.queue_verdict(verdict="REVISED", revised_reply=None)

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.final.draft_reply == CLEAN_DENY_REPLY


def test_reflect_unparsed_verdict_has_no_answers(env: _Env) -> None:
    env.fake.queue(env.make_message(tool_name=None, text="Looks fine.", stop_reason="end_turn"))

    result = env.reflect(_deny(CLEAN_DENY_REPLY))

    assert result.answers is None


def test_reflect_unparsed_verdict_still_runs_the_code_checks(env: _Env) -> None:
    env.fake.queue(env.make_message(tool_name=None, text="Looks fine.", stop_reason="end_turn"))

    result = env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert result.final.draft_reply == CLEAN_DENY_REPLY


# ---------- Reflector: the model call ----------


def test_reflect_makes_exactly_one_model_call(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert len(env.fake.calls) == 1


def test_reflect_ledger_line_has_stage_reflection(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert [json.loads(line)["stage"] for line in env.ledger_path.read_text().splitlines()] == [
        "reflection"
    ]


def test_reflect_sends_the_cached_reflection_system_prompt(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert env.sent()["system"] == system_blocks(SYSTEM_PROMPT_REFLECTION)


def test_reflect_offers_only_the_verdict_tool(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert env.sent()["tools"] == [REFLECTION_TOOL]


@pytest.mark.parametrize(
    "expected",
    [REQUEST_TEXT, '"next_eligible_date": "2028-03-10"', CLEAN_DENY_REPLY, "R-REFRESH"],
    ids=["request-text", "observation-json", "draft-reply", "rule-id"],
)
def test_reflect_user_message_holds_the_review_material(env: _Env, expected: str) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert expected in env.user_message()


def test_reflect_user_message_lists_the_code_findings(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert "C4_UNSUPPORTED_DATE" in env.user_message()


# ---------- Reflector: span and logs ----------


def test_reflect_span_is_parent_of_the_llm_call(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert env.span("llm.call").parent.span_id == env.span("reflection").context.span_id  # type: ignore[union-attr]


def test_reflect_span_records_the_result(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert dict(env.span("reflection").attributes or {})["result"] == "REVISED"


def test_reflect_span_records_the_findings(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert dict(env.span("reflection").attributes or {})["findings"] == ("C4_UNSUPPORTED_DATE",)


def test_reflect_result_log_carries_status_and_changes(env: _Env) -> None:
    env.queue_verdict()

    env.reflect(_deny(DRAFT_WITH_INVENTED_DATE))

    assert [(r["status"], r["changes"]) for r in env.records("reflection.result")] == [
        ("REVISED", [f"removed unsupported date {INVENTED_DATE}"])
    ]


def test_reflect_result_log_has_no_reply_text(env: _Env) -> None:
    env.queue_revised()

    env.reflect(_deny(CLEAN_DENY_REPLY))

    assert SAFE_REVISION not in json.dumps(env.records("reflection.result"))

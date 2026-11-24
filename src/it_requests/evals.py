"""Evaluation helpers: load the fixed cases, grade one run, summarise many (PLAN §8).

Pure apart from load_cases reading its file, so the live scripts (scripts/run_agent.py,
scripts/run_eval.py) stay thin and every judgement they print is unit-tested.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from it_requests import config
from it_requests.agent.guards import cites
from it_requests.agent.loop import AgentResult

CASES_FILE = config.REPO_ROOT / "evals" / "cases.json"

# PLAN §4 per-run estimates, 20% margin included. Labelled as estimates when printed.
PER_RUN_ESTIMATE_USD = {config.EVAL_MODEL: 0.085, config.DEV_MODEL: 0.004}
_MODEL_ALIASES = {"haiku": config.DEV_MODEL, "sonnet": config.EVAL_MODEL}

ToolName = Literal[
    "get_employee_info", "get_policy_limits", "check_request_eligibility", "flag_for_human_review"
]


class CaseFileError(ValueError):
    """The cases file is missing, malformed, or has a bad or duplicate case."""


class Case(BaseModel):
    # Strict and closed, like the mock data models: a typo in the case file must fail the
    # load, not silently grade every run against the wrong expectation.
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    request_id: str
    text: str
    expected_decision: Literal["approve", "deny", "escalate"]
    expected_code: str
    required_tools: tuple[ToolName, ...]


class _CaseFile(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    version: str
    source: str | None = None
    cases: tuple[Case, ...]


def load_cases(path: Path) -> list[Case]:
    """Read and validate the cases file. Any problem raises CaseFileError naming it."""
    try:
        raw = path.read_text(encoding="utf-8")
        # model_validate_json, not json.loads + model_validate: strict JSON mode accepts a
        # JSON array for a tuple field, and malformed JSON becomes the same error type.
        cases = list(_CaseFile.model_validate_json(raw).cases)
    except (OSError, ValidationError) as err:
        raise CaseFileError(f"invalid cases file {path.name}: {err}") from err
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseFileError(f"duplicate case id {case.id} in {path.name}")
        seen.add(case.id)
    return cases


@dataclass(frozen=True)
class Grade:
    case_id: str
    decision_ok: bool
    code_ok: bool
    tools_ok: bool
    reply_ok: bool
    fallback: bool
    passed: bool


def grade(case: Case, result: AgentResult) -> Grade:
    """Check one run against its case: decision, code, required calls, reply citation."""
    final = result.final
    # The code that decided it: the rule for approve and deny, the E-code for escalate.
    code = final.reason_code if final.decision == "escalate" else final.rule_id
    # Only successful calls count, the same rule the guards apply: an error is not data.
    succeeded = {s.tool for s in result.steps if "error" not in s.observation}
    if final.decision == "escalate":
        reply_ok = cites(final.draft_reply, final.review_id)  # C3
    elif final.decision == "deny":
        reply_ok = cites(final.draft_reply, final.rule_id)  # C2
    else:
        reply_ok = True  # §10 asks approvals to cite the rule in words, not by ID
    checks = {
        "decision_ok": final.decision == case.expected_decision,
        "code_ok": code == case.expected_code,
        "tools_ok": set(case.required_tools) <= succeeded,
        "reply_ok": reply_ok,
    }
    return Grade(
        case_id=case.id,
        **checks,
        fallback=result.fallback,
        # A fallback never passes: the code escalated because the model didn't finish.
        passed=all(checks.values()) and not result.fallback,
    )


@dataclass(frozen=True)
class Summary:
    per_case: dict[str, str]
    passed: int
    total: int
    pass_rate: float


def summarise(grades: Iterable[Grade]) -> Summary:
    """Per-case pass counts ("3/3"), in first-seen order, and the overall pass rate."""
    counts: dict[str, list[int]] = {}
    for g in grades:
        passed_total = counts.setdefault(g.case_id, [0, 0])
        passed_total[0] += int(g.passed)
        passed_total[1] += 1
    passed = sum(p for p, _ in counts.values())
    total = sum(t for _, t in counts.values())
    return Summary(
        per_case={case_id: f"{p}/{t}" for case_id, (p, t) in counts.items()},
        passed=passed,
        total=total,
        pass_rate=passed / total if total else 0.0,
    )


def format_summary(summary: Summary) -> str:
    lines = [f"{'Case':<6} Passed"]
    lines += [f"{case_id:<6} {count}" for case_id, count in summary.per_case.items()]
    lines.append(f"Overall: {summary.passed}/{summary.total} ({summary.pass_rate:.0%})")
    return "\n".join(lines)


def resolve_model(name: str) -> str:
    """Map "haiku" / "sonnet" (or a full model ID) to a model ID. Anything else is refused.

    Only the two agent models are accepted: Opus is ruled out by the budget (CLAUDE.md).
    """
    key = name.strip().lower()
    model = _MODEL_ALIASES.get(key, key)
    if model not in PER_RUN_ESTIMATE_USD:
        raise ValueError(f"unknown model {name!r}: use haiku, sonnet or a full model ID")
    return model


def estimate_usd(model: str, *, runs: int) -> float:
    """Estimated cost of `runs` agent runs (PLAN §4 figures; an estimate, not a quote)."""
    return PER_RUN_ESTIMATE_USD[model] * runs

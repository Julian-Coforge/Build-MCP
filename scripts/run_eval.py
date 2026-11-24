"""Run eval cases LIVE (paid) several times each, grade every run, print a pass/fail table.

Run `make serve` in another terminal first (consider `make reset-reviews` before), then
`make eval`. It prints the run count and an estimated cost and asks before spending
anything. The budget gate is checked before EVERY run, and the eval stops cleanly if it
trips. Grading, summarising and estimating are tested in it_requests.evals.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import anthropic
from anthropic.types import Message
from dotenv import load_dotenv
from mcp import Client

from it_requests import config
from it_requests.agent import costs
from it_requests.agent.llm import LLMClient
from it_requests.agent.loop import AgentRequest, AgentResult, run_agent
from it_requests.agent.prompts import PROMPT_VERSION
from it_requests.data import load_employees, load_policy
from it_requests.evals import (
    CASES_FILE,
    Case,
    Grade,
    estimate_usd,
    format_summary,
    grade,
    load_cases,
    resolve_model,
    summarise,
)
from it_requests.observability import configure_logging, log_event
from it_requests.tracing import make_tracer

SERVER_URL = "http://127.0.0.1:8000/mcp"
RESULTS_DIR = config.REPO_ROOT / "evals" / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="T1,T2,T3,T4", help="comma-separated case IDs")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model", default="sonnet", type=resolve_model)
    parser.add_argument("--env", default="eval", choices=["dev", "eval", "demo"])
    return parser.parse_args()


def budget_ok(logger: logging.Logger) -> bool:
    """The budget gate, as in agent_once.py: refuse at or over budget, warn near it."""
    spent = costs.ledger_total(config.COST_LEDGER_FILE)
    budget = costs.budget_from_env(os.environ)
    try:
        status = costs.check_budget(spent, budget=budget, warn_at=config.BUDGET_WARN_USD)
    except costs.BudgetExceededError as err:
        print(f"Budget gate tripped, stopping: {err}")
        return False
    if status == "warn":
        log_event(logger, "budget.warn", level=logging.WARNING, spent_usd=spent, budget_usd=budget)
    return True


def record(
    case: Case,
    repeat: int,
    result: AgentResult,
    g: Grade,
    *,
    model: str,
    env: str,
    data_versions: dict[str, str],
) -> dict[str, Any]:
    """One results line: enough to compare runs one variable at a time (PLAN §8)."""
    final = result.final
    return {
        "ts": datetime.now(UTC).isoformat(),
        "case_id": case.id,
        "repeat": repeat,
        "request_id": result.request_id,
        "run_id": result.run_id,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "env": env,
        "data_versions": data_versions,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "decision": final.decision,
        "rule_id": final.rule_id,
        "reason_code": final.reason_code,
        "review_id": final.review_id,
        # Kept so C4/C5 (no invented dates, no promises) can be reviewed by hand. Mock data.
        "draft_reply": final.draft_reply,
        "fallback": result.fallback,
        "tools": [s.tool for s in result.steps],
        "guard_events": [asdict(e) for e in result.guard_events],
        "grade": asdict(g),
    }


async def run_all(
    cases: list[Case], args: argparse.Namespace, out: TextIO, logger: logging.Logger
) -> tuple[list[Grade], float]:
    # Anthropic() reads ANTHROPIC_API_KEY itself; the key is never read, printed or logged here.
    client = anthropic.Anthropic()

    def create(**kwargs: Any) -> Message:
        message: Message = client.messages.create(stream=False, **kwargs)
        return message

    data_versions = {
        "employees": load_employees(config.EMPLOYEES_FILE).data_version,
        "policy": load_policy(config.POLICY_FILE).data_version,
    }
    grades: list[Grade] = []
    total_cost = 0.0
    async with Client(SERVER_URL) as mcp_client:
        for repeat in range(1, args.repeat + 1):
            for case in cases:
                if not budget_ok(logger):
                    return grades, total_cost  # stop cleanly: results so far are kept
                trace_path = config.TRACES_DIR / f"{case.request_id}-r{repeat}.jsonl"
                tracer, provider = make_tracer(trace_path)
                try:
                    llm = LLMClient(
                        create=create,
                        model=args.model,
                        env=args.env,
                        ledger_path=config.COST_LEDGER_FILE,
                        tracer=tracer,
                        logger=logger,
                        clock=time.monotonic,
                    )
                    result = await run_agent(
                        AgentRequest(case.request_id, case.text),
                        llm=llm,
                        mcp_client=mcp_client,  # type: ignore[arg-type]  # meta: RequestParamsMeta
                        tracer=tracer,
                        logger=logger,
                    )
                finally:
                    provider.shutdown()
                g = grade(case, result)
                grades.append(g)
                total_cost += result.cost_usd
                line = record(
                    case,
                    repeat,
                    result,
                    g,
                    model=args.model,
                    env=args.env,
                    data_versions=data_versions,
                )
                out.write(json.dumps(line) + "\n")
                out.flush()  # every finished run is on disk, even if a later one fails
                code = result.final.rule_id or result.final.reason_code
                print(
                    f"{case.id} run {repeat}/{args.repeat}: {'PASS' if g.passed else 'FAIL'} "
                    f"({result.final.decision} {code}, ${result.cost_usd:.4f})"
                )
    return grades, total_cost


def main() -> None:
    args = parse_args()
    load_dotenv()  # ANTHROPIC_API_KEY and BUDGET_USD from .env (gitignored)
    logger = configure_logging("agent", log_file=config.APP_LOG_FILE)

    all_cases = {c.id: c for c in load_cases(CASES_FILE)}
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in all_cases]
    if unknown or args.repeat < 1:
        sys.exit(f"Bad arguments: unknown cases {unknown} or --repeat < 1")
    cases = [all_cases[c] for c in wanted]

    runs = len(cases) * args.repeat
    estimate = estimate_usd(args.model, runs=runs)
    print(f"{runs} runs ({len(cases)} cases x {args.repeat}) on {args.model}, env={args.env}.")
    print(f"Estimated cost: ${estimate:.2f} (estimate from PLAN §4, not a quote).")
    # A human says yes before any paid run (CLAUDE.md: no live run without the user's ask).
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled; nothing was spent.")
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_path: Path = RESULTS_DIR / f"{stamp}-{args.model}.jsonl"
    with results_path.open("w", encoding="utf-8") as out:
        grades, total_cost = asyncio.run(run_all(cases, args, out, logger))

    print()
    print(format_summary(summarise(grades)))
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Results: {results_path.relative_to(config.REPO_ROOT)}")


if __name__ == "__main__":
    main()

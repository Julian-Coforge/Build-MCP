"""Run the ReAct agent once, LIVE (paid), and print the full Thought/Action/Observation trace.

Run `make serve` in another terminal first, then `make agent CASE=T1` or
`python scripts/run_agent.py --text "..."`. Thin on purpose: run_agent, format_trace, the
budget gate and LLMClient are all tested; this file only wires them to the real services.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any

import anthropic
from anthropic.types import Message
from dotenv import load_dotenv
from mcp import Client

from it_requests import config
from it_requests.agent import costs
from it_requests.agent.llm import LLMClient
from it_requests.agent.loop import AgentRequest, run_agent
from it_requests.agent.trace_format import format_trace
from it_requests.evals import CASES_FILE, load_cases, resolve_model
from it_requests.observability import configure_logging, log_event
from it_requests.tracing import make_tracer

SERVER_URL = "http://127.0.0.1:8000/mcp"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="a case ID from evals/cases.json, e.g. T1")
    source.add_argument("--text", help="free request text, including the employee ID")
    parser.add_argument("--request-id", default="REQ-0100", help="used with --text only")
    # type=resolve_model: "haiku" / "sonnet" accepted; Opus refused with a clear error.
    parser.add_argument("--model", default=config.DEV_MODEL, type=resolve_model)
    parser.add_argument("--env", default="dev", choices=["dev", "eval", "demo"])
    return parser.parse_args()


def budget_ok(logger: logging.Logger) -> bool:
    """The budget gate, as in agent_once.py: refuse at or over budget, warn near it."""
    spent = costs.ledger_total(config.COST_LEDGER_FILE)
    budget = costs.budget_from_env(os.environ)
    try:
        status = costs.check_budget(spent, budget=budget, warn_at=config.BUDGET_WARN_USD)
    except costs.BudgetExceededError as err:
        print(f"Refusing to run: {err}")
        return False
    if status == "warn":
        log_event(logger, "budget.warn", level=logging.WARNING, spent_usd=spent, budget_usd=budget)
    return True


async def run(request: AgentRequest, name: str, args: argparse.Namespace) -> None:
    logger = logging.getLogger("agent")
    # Anthropic() reads ANTHROPIC_API_KEY itself; the key is never read, printed or logged here.
    client = anthropic.Anthropic()

    def create(**kwargs: Any) -> Message:
        message: Message = client.messages.create(stream=False, **kwargs)
        return message

    tracer, provider = make_tracer(config.TRACES_DIR / f"{request.request_id}.jsonl")
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
        async with Client(SERVER_URL) as mcp_client:
            result = await run_agent(
                request,
                llm=llm,
                mcp_client=mcp_client,  # type: ignore[arg-type]  # meta typed as RequestParamsMeta
                tracer=tracer,
                logger=logger,
            )
    finally:
        provider.shutdown()  # flush every span to the trace file, even after an error

    trace = format_trace(result)
    print(trace)
    tokens = result.input_tokens + result.output_tokens
    print(f"\nRun cost: ${result.cost_usd:.6f} ({args.model}, env={args.env}, {tokens} tokens)")
    # The printed trace is the graded evidence (deliverable 6). traces/ is gitignored.
    text_path = config.TRACES_DIR / f"{name}.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(trace + "\n", encoding="utf-8")
    print(f"Trace saved: {text_path.relative_to(config.REPO_ROOT)}")


def main() -> None:
    args = parse_args()
    load_dotenv()  # ANTHROPIC_API_KEY and BUDGET_USD from .env (gitignored)
    logger = configure_logging("agent", log_file=config.APP_LOG_FILE)

    if args.case:
        cases = {c.id: c for c in load_cases(CASES_FILE)}
        if args.case not in cases:
            sys.exit(f"Unknown case {args.case!r}; choose from {', '.join(cases)}")
        case = cases[args.case]
        request, name = AgentRequest(case.request_id, case.text), case.id
    else:
        request, name = AgentRequest(args.request_id, args.text), args.request_id

    # Budget gate FIRST, before any client exists: a refused run spends nothing.
    if not budget_ok(logger):
        sys.exit(1)
    asyncio.run(run(request, name, args))


if __name__ == "__main__":
    main()

"""Live entry point for brief step 5a: one PAID model call, one tool call on the real server.

Run `make serve` in another terminal first, then `make agent-once`. Thin on purpose: every
piece it wires together (budget gate, LLMClient, run_single_tool_call, tracing) is tested.
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
from it_requests.agent.single_call import run_single_tool_call
from it_requests.observability import configure_logging, log_event
from it_requests.tracing import make_tracer

SERVER_URL = "http://127.0.0.1:8000/mcp"
REQUEST_ID = "REQ-0501"
REQUEST_TEXT = "Hi, I'm E1002. I'd like to request a monitor for my desk, please."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=config.DEV_MODEL, choices=sorted(config.PRICES_PER_MTOK))
    parser.add_argument("--env", default="dev", choices=["dev", "eval", "demo"])
    return parser.parse_args()


async def run(args: argparse.Namespace, logger: logging.Logger) -> None:
    # Adapt the SDK to LLMClient's one call shape (see CreateMessage in agent/llm.py).
    # Anthropic() reads ANTHROPIC_API_KEY itself; the key is never read, printed or logged here.
    client = anthropic.Anthropic()

    def create(**kwargs: Any) -> Message:
        message: Message = client.messages.create(stream=False, **kwargs)
        return message

    trace_path = config.TRACES_DIR / f"{REQUEST_ID}.jsonl"
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
        async with Client(SERVER_URL) as mcp_client:
            outcome = await run_single_tool_call(
                REQUEST_TEXT,
                request_id=REQUEST_ID,
                llm=llm,
                mcp_client=mcp_client,  # type: ignore[arg-type]
                tracer=tracer,
                logger=logger,
            )
    finally:
        provider.shutdown()  # flush every span to the trace file, even after an error

    if "error" in outcome:
        print(f"No tool call: {outcome['error']} (stop_reason={outcome.get('stop_reason')})")
    else:
        print(f"Tool chosen:   {outcome['tool']}")
        print(f"Arguments:     {outcome['arguments']}")
        print(f"Result status: {outcome['result_status']}")
    print(f"Run cost:      ${outcome['cost_usd']:.6f} ({args.model}, env={args.env})")
    print(f"Trace file:    {trace_path.relative_to(config.REPO_ROOT)}")


def main() -> None:
    args = parse_args()
    load_dotenv()  # ANTHROPIC_API_KEY and BUDGET_USD from .env (gitignored)
    logger = configure_logging("agent", log_file=config.APP_LOG_FILE)

    # Budget gate FIRST, before any client exists: a refused run spends nothing.
    spent = costs.ledger_total(config.COST_LEDGER_FILE)
    budget = costs.budget_from_env(os.environ)
    try:
        status = costs.check_budget(spent, budget=budget, warn_at=config.BUDGET_WARN_USD)
    except costs.BudgetExceededError as err:
        print(f"Refusing to run: {err}")
        sys.exit(1)
    if status == "warn":
        log_event(logger, "budget.warn", level=logging.WARNING, spent_usd=spent, budget_usd=budget)

    asyncio.run(run(args, logger))


if __name__ == "__main__":
    main()

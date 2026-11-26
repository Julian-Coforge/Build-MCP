"""Live twin of the T1 loop test: the real Claude API (Haiku) and the real HTTP server.

PAID (about $0.004 a run) and slow, so it never runs by default: `slow` is deselected by
pytest's addopts, `make check` and CI. Run it on purpose with `make serve` in one terminal
and `make test-live` in another. It skips, rather than fails, when there is no API key or
no running server, and it respects the same budget gate as scripts/run_agent.py.

One run is shared by every test in this module (module-scoped fixture), so the behaviours
below cost one agent run (three loop calls plus one reflection call), not one each.
"""

import asyncio
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

import anthropic
import pytest
from anthropic.types import Message
from dotenv import load_dotenv
from mcp import Client

from it_requests import config
from it_requests.agent import costs
from it_requests.agent.llm import LLMClient
from it_requests.agent.loop import AgentRequest, AgentResult, run_agent
from it_requests.agent.reflection import Reflector
from it_requests.evals import CASES_FILE, load_cases
from it_requests.tracing import make_tracer

load_dotenv()  # ANTHROPIC_API_KEY from .env (gitignored); the key itself is never read here

SERVER_HOST, SERVER_PORT = "127.0.0.1", 8000
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/mcp"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY is not set"
    ),
]


def _server_is_up() -> bool:
    try:
        with socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=1):
            return True
    except OSError:
        return False


async def _run_t1(trace_path: Path) -> AgentResult:
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY itself

    def create(**kwargs: Any) -> Message:
        message: Message = client.messages.create(stream=False, **kwargs)
        return message

    case = {c.id: c for c in load_cases(CASES_FILE)}["T1"]
    logger = logging.getLogger("test.live")
    tracer, provider = make_tracer(trace_path)
    try:
        llm = LLMClient(
            create=create,
            model=config.DEV_MODEL,
            env="dev",
            ledger_path=config.COST_LEDGER_FILE,  # real spend goes in the real ledger
            tracer=tracer,
            logger=logger,
            clock=time.monotonic,
        )
        async with Client(SERVER_URL) as mcp_client:
            return await run_agent(
                AgentRequest(case.request_id, case.text),
                llm=llm,
                mcp_client=mcp_client,  # type: ignore[arg-type]
                tracer=tracer,
                logger=logger,
                reflector=Reflector(llm=llm, tracer=tracer, logger=logger),
            )
    finally:
        provider.shutdown()


@pytest.fixture(scope="module")
def t1_result(tmp_path_factory: pytest.TempPathFactory) -> AgentResult:
    if not _server_is_up():
        pytest.skip(f"no MCP server on {SERVER_URL}; run `make serve` first")
    spent = costs.ledger_total(config.COST_LEDGER_FILE)
    try:
        costs.check_budget(
            spent, budget=costs.budget_from_env(os.environ), warn_at=config.BUDGET_WARN_USD
        )
    except costs.BudgetExceededError as err:
        pytest.skip(f"budget gate: {err}")
    trace_path = tmp_path_factory.mktemp("traces") / "REQ-0001.jsonl"
    return asyncio.run(_run_t1(trace_path))


# ---------- T1 on the real model ----------


def test_live_t1_is_approved(t1_result: AgentResult) -> None:
    assert t1_result.final.decision == "approve"


def test_live_t1_cites_r_new(t1_result: AgentResult) -> None:
    assert t1_result.final.rule_id == "R-NEW"


def test_live_t1_finishes_without_fallback(t1_result: AgentResult) -> None:
    assert t1_result.fallback is False


def test_live_t1_reflection_confirms(t1_result: AgentResult) -> None:
    # A clean T1 draft should pass the §11 checklist unchanged.
    assert t1_result.reflection is not None and t1_result.reflection.status == "CONFIRMED"

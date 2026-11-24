"""Project-wide configuration constants.

Kept in one module so every component (tools, agent, tests) reads the same values.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Reference date for every policy check in tests and demos (REQUIREMENTS.md, header).
# Eligibility depends on date arithmetic, so it is passed in as `as_of` and never
# read from the system clock. Tests that use today's date would break over time.
REFERENCE_DATE: date = date(2026, 11, 22)

# Repo-root data/ folder. This file is src/it_requests/config.py, so parents[2] is the repo
# root. Resolving from __file__ (not the working directory) means tests, the server and
# scripts find the same files wherever they are launched from.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = REPO_ROOT / "data"
EMPLOYEES_FILE: Path = DATA_DIR / "employees.json"
POLICY_FILE: Path = DATA_DIR / "policy.json"
# Written at runtime by flag_for_human_review; gitignored, never committed.
REVIEW_QUEUE_FILE: Path = DATA_DIR / "review_queue.json"

# --- Runtime output (gitignored) ---------------------------------------------------------
# Resolved from the repo root like DATA_DIR, so every entry point writes to the same place.
LOGS_DIR: Path = REPO_ROOT / "logs"
APP_LOG_FILE: Path = LOGS_DIR / "app.jsonl"
# One JSON line per model call; summed by the budget gate before every run (PLAN §4).
COST_LEDGER_FILE: Path = LOGS_DIR / "cost_ledger.jsonl"
TRACES_DIR: Path = REPO_ROOT / "traces"

# --- Models (PLAN §2 decision 5) ------------------------------------------------------------
# Haiku for development (about 20x cheaper, so iterating is nearly free); Sonnet for the
# graded eval and demo runs, where escalation judgment matters. Never Opus: the $5 budget.
DEV_MODEL = "claude-haiku-5-5"
EVAL_MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class ModelPrices:
    """USD per 1M tokens. Named fields, so a price can't be read from the wrong position."""

    input: float  # uncached input only, which is how the API reports input_tokens
    output: float
    cache_read: float
    cache_write: float


# Date of the price source. Prices change; the ledger is only as right as this table, which
# is one reason the budget gate stops at $4.00, not $5.00 (PLAN §4). Julian confirms these
# on the Anthropic pricing page.
PRICES_CHECKED = "2026-11-03"
PRICES_PER_MTOK: Mapping[str, ModelPrices] = {
    DEV_MODEL: ModelPrices(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    EVAL_MODEL: ModelPrices(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
}

# Haiku 5.5 costs more when one prompt's total input exceeds 100K tokens. The source gives
# long-prompt input and output prices only, so cache prices are kept at the normal rates.
HAIKU_LONG_PROMPT_TOKENS = 100_000
HAIKU_LONG_PRICES = ModelPrices(input=0.50, output=2.50, cache_read=0.01, cache_write=0.125)

# --- Budget and per-call limits (PLAN §4) -----------------------------------------------------
# $4.00, not $5.00: the $1 gap absorbs any error in our own price table.
BUDGET_USD_DEFAULT = 4.00
# Warn at 75% of the default budget, so there's time to stop before the gate does.
BUDGET_WARN_USD = 3.00
# Caps one model call's output, so one misbehaving run can't cost more than about $0.50.
MAX_TOKENS_PER_CALL = 4000

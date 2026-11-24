"""Cost maths and the budget gate (PLAN §4). Pure: no network, no SDK, no logging.

The caller (the LLM wrapper and the run start-up) does the I/O and the logging; these
functions only compute, so every rule here is unit-tested without spending a cent.
"""

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from it_requests import config

BudgetStatus = Literal["ok", "warn"]


class UnknownModelError(ValueError):
    """A model ID with no price entry. A programming error, so it is raised, not returned."""


class LedgerCorruptError(Exception):
    """The cost ledger can't be read, so total spend is unknown."""


class BudgetExceededError(Exception):
    """Spend has reached the budget; no new run may start."""


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost of one model call in USD, rounded to 6 decimals.

    `input_tokens` is uncached input only, as the API reports it; cache reads and writes
    are separate counts with their own prices.
    """
    try:
        prices = config.PRICES_PER_MTOK[model]
    except KeyError:
        raise UnknownModelError(f"no price entry for model {model!r}") from None

    # The long-prompt tier depends on the whole prompt, so cached tokens count too.
    total_input = input_tokens + cache_read_tokens + cache_write_tokens
    if model == config.DEV_MODEL and total_input > config.HAIKU_LONG_PROMPT_TOKENS:
        prices = config.HAIKU_LONG_PRICES

    dollars = (
        input_tokens * prices.input
        + output_tokens * prices.output
        + cache_read_tokens * prices.cache_read
        + cache_write_tokens * prices.cache_write
    ) / 1_000_000
    return round(dollars, 6)


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Every entry of the JSONL ledger, each checked to have a numeric cost_usd.

    A missing file means no entries. Any unreadable line raises LedgerCorruptError naming
    the line: if we can't read the ledger we don't know what has been spent, so the safe
    answer is to refuse to run, not to guess.
    """
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue  # blank lines (e.g. a trailing newline) carry no cost
        try:
            record = json.loads(line)
        except ValueError as err:
            raise LedgerCorruptError(f"{path} line {number}: not valid JSON") from err
        cost = record.get("cost_usd") if isinstance(record, dict) else None
        # bool is a subclass of int in Python, so `true` would otherwise count as $1.
        if isinstance(cost, bool) or not isinstance(cost, int | float):
            raise LedgerCorruptError(f"{path} line {number}: no numeric cost_usd")
        entries.append(record)
    return entries


def ledger_total(path: Path) -> float:
    """Sum cost_usd over the ledger. A missing file means $0 spent; a bad line raises.

    Built on read_ledger, so the budget gate and the cost report apply the same checks.
    """
    return float(sum(entry["cost_usd"] for entry in read_ledger(path)))


def check_budget(spent: float, *, budget: float, warn_at: float) -> BudgetStatus:
    """Return "ok" or "warn"; raise BudgetExceededError once spent reaches the budget.

    At the budget counts as over (>=): the run that would cross the line is refused, so
    real spend stays near the limit. No logging here; the caller logs the status.
    """
    if spent >= budget:
        raise BudgetExceededError(f"spent ${spent:.4f} of ${budget:.2f} budget; refusing to run")
    return "warn" if spent >= warn_at else "ok"


def budget_from_env(environ: Mapping[str, str]) -> float:
    """Read BUDGET_USD (default BUDGET_USD_DEFAULT). Non-numeric or negative raises ValueError.

    The environment is passed in (not read from os.environ), so tests stay pure.
    """
    raw = environ.get("BUDGET_USD")
    if raw is None:
        return config.BUDGET_USD_DEFAULT
    try:
        budget = float(raw)
    except ValueError:
        raise ValueError(f"BUDGET_USD must be a number, got {raw!r}") from None
    # nan and inf parse as floats but would switch the gate off: spent >= nan is always
    # False, and no spend ever reaches inf.
    if not math.isfinite(budget) or budget < 0:
        raise ValueError(f"BUDGET_USD must be a finite number >= 0, got {raw!r}")
    return budget

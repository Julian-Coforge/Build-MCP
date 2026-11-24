"""Cost report: python -m it_requests.cost_report (PLAN §4 Reporting).

Prints total spend from the cost ledger, then spend by env, by stage and by request_id,
and the budget left. The logic is the pure `summarise`; `main` only reads and prints.
"""

import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from it_requests import config
from it_requests.agent.costs import budget_from_env, read_ledger


@dataclass(frozen=True)
class CostSummary:
    total: float
    calls: int
    by_env: dict[str, float]
    by_stage: dict[str, float]
    by_request: dict[str, float]
    budget: float
    remaining: float


def _group(entries: list[Mapping[str, Any]], key: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for entry in entries:
        # A line without the key still counts towards spend, under "unknown".
        totals[str(entry.get(key) or "unknown")] += entry["cost_usd"]
    return dict(totals)


def summarise(entries: Iterable[Mapping[str, Any]], *, budget: float) -> CostSummary:
    """Totals and groupings for ledger entries. Pure."""
    items = list(entries)
    total = float(sum(entry["cost_usd"] for entry in items))
    return CostSummary(
        total=total,
        calls=len(items),
        by_env=_group(items, "env"),
        by_stage=_group(items, "stage"),
        by_request=_group(items, "request_id"),
        budget=budget,
        # Not clamped at 0: an overspend must be visible, not hidden.
        remaining=budget - total,
    )


def _section(title: str, totals: dict[str, float]) -> list[str]:
    lines = [f"\n{title}"]
    # Biggest spend first, so the line worth looking at is on top.
    for name, cost in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {name:<24} ${cost:.4f}")
    return lines


def format_report(summary: CostSummary) -> str:
    """The report as text. Four decimals: development runs cost fractions of a cent."""
    lines = [
        f"Total spend: ${summary.total:.4f} over {summary.calls} model call(s)",
        *_section("By env", summary.by_env),
        *_section("By stage", summary.by_stage),
        *_section("By request", summary.by_request),
        f"\nRemaining budget: ${summary.remaining:.4f} of ${summary.budget:.2f}",
    ]
    return "\n".join(lines)


def main(*, ledger_path: Path | None = None, environ: Mapping[str, str] | None = None) -> None:
    """Print the report. Parameters exist so tests use a tmp ledger, never the real logs/."""
    entries = read_ledger(ledger_path or config.COST_LEDGER_FILE)
    budget = budget_from_env(os.environ if environ is None else environ)
    print(format_report(summarise(entries, budget=budget)))


if __name__ == "__main__":  # pragma: no cover
    main()

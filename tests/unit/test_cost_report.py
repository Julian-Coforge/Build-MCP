"""Tests for the cost report (PLAN §4 Reporting). Only tmp ledgers: never the real logs/."""

import json
from pathlib import Path
from typing import Any

import pytest

from it_requests.cost_report import format_report, main, summarise

ENTRIES: list[dict[str, Any]] = [
    {"env": "dev", "stage": "single_call", "request_id": "REQ-0501", "cost_usd": 0.001},
    {"env": "eval", "stage": "react_step_1", "request_id": "REQ-0001", "cost_usd": 0.05},
    {"env": "eval", "stage": "reflection", "request_id": "REQ-0001", "cost_usd": 0.02},
]


SUMMARY = summarise(ENTRIES, budget=4.00)
EMPTY = summarise([], budget=4.00)
UNKNOWN = summarise([{"cost_usd": 0.01}], budget=4.0)


# ---------- summarise ----------


def test_summarise_total_adds_every_entry() -> None:
    assert SUMMARY.total == pytest.approx(0.071)


def test_summarise_groups_cost_by_env() -> None:
    assert SUMMARY.by_env == pytest.approx({"dev": 0.001, "eval": 0.07})


def test_summarise_groups_cost_by_stage() -> None:
    assert SUMMARY.by_stage == pytest.approx(
        {"single_call": 0.001, "react_step_1": 0.05, "reflection": 0.02}
    )


def test_summarise_groups_cost_by_request() -> None:
    assert SUMMARY.by_request == pytest.approx({"REQ-0501": 0.001, "REQ-0001": 0.07})


def test_summarise_counts_calls_and_keeps_budget() -> None:
    assert (SUMMARY.calls, SUMMARY.budget) == (3, 4.00)


def test_summarise_remaining_is_budget_minus_total() -> None:
    assert SUMMARY.remaining == pytest.approx(3.929)


def test_summarise_empty_ledger_has_zero_total_and_calls() -> None:
    assert (EMPTY.total, EMPTY.calls, EMPTY.by_env) == (0.0, 0, {})


def test_summarise_empty_ledger_has_full_budget_remaining() -> None:
    assert EMPTY.remaining == 4.00


def test_summarise_overspend_shows_negative_remaining() -> None:
    # Shown as negative, not clamped at 0: an overspend must be visible, not hidden.
    summary = summarise(
        [{"env": "eval", "stage": "s", "request_id": "R", "cost_usd": 4.5}], budget=4.0
    )

    assert summary.remaining == pytest.approx(-0.5)


@pytest.mark.parametrize("group", ["by_env", "by_stage", "by_request"], ids=str)
def test_summarise_missing_group_key_goes_to_unknown(group: str) -> None:
    assert getattr(UNKNOWN, group) == pytest.approx({"unknown": 0.01})


# ---------- format_report ----------

# Four decimals: development runs cost fractions of a cent.
REPORT_TEXT = [
    "Total",
    "By env",
    "By stage",
    "By request",
    "Remaining",
    "$0.0710",
    "$3.9290",
    "REQ-0001",
]


@pytest.mark.parametrize("expected", REPORT_TEXT, ids=str)
def test_format_report_shows_every_section_and_figure(expected: str) -> None:
    assert expected in format_report(SUMMARY)


# ---------- main ----------


@pytest.mark.parametrize(
    "expected",
    ["$0.0710", "$1.9290"],  # the remaining figure uses BUDGET_USD
    ids=["total", "remaining-from-env-budget"],
)
def test_main_prints_report_for_a_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], expected: str
) -> None:
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in ENTRIES) + "\n", encoding="utf-8")

    main(ledger_path=ledger, environ={"BUDGET_USD": "2.00"})

    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "expected", ["$0.0000", "$4.0000"], ids=["zero-total", "default-budget-remaining"]
)
def test_main_with_no_ledger_reports_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], expected: str
) -> None:
    main(ledger_path=tmp_path / "missing.jsonl", environ={})

    assert expected in capsys.readouterr().out

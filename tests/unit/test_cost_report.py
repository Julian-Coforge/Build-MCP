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


def test_summarise_totals_and_groups() -> None:
    summary = summarise(ENTRIES, budget=4.00)

    assert summary.total == pytest.approx(0.071)
    assert summary.by_env == pytest.approx({"dev": 0.001, "eval": 0.07})
    assert summary.by_stage == pytest.approx(
        {"single_call": 0.001, "react_step_1": 0.05, "reflection": 0.02}
    )
    assert summary.by_request == pytest.approx({"REQ-0501": 0.001, "REQ-0001": 0.07})
    assert summary.calls == 3
    assert summary.budget == 4.00
    assert summary.remaining == pytest.approx(3.929)


def test_empty_ledger_has_full_budget_remaining() -> None:
    summary = summarise([], budget=4.00)

    assert summary.total == 0.0
    assert summary.calls == 0
    assert summary.by_env == {}
    assert summary.remaining == 4.00


def test_overspend_shows_negative_remaining() -> None:
    # Shown as negative, not clamped at 0: an overspend must be visible, not hidden.
    summary = summarise(
        [{"env": "eval", "stage": "s", "request_id": "R", "cost_usd": 4.5}], budget=4.0
    )

    assert summary.remaining == pytest.approx(-0.5)


def test_missing_group_keys_go_to_unknown() -> None:
    summary = summarise([{"cost_usd": 0.01}], budget=4.0)

    assert summary.by_env == pytest.approx({"unknown": 0.01})
    assert summary.by_stage == pytest.approx({"unknown": 0.01})
    assert summary.by_request == pytest.approx({"unknown": 0.01})


def test_format_report_shows_every_section() -> None:
    text = format_report(summarise(ENTRIES, budget=4.00))

    for heading in ("Total", "By env", "By stage", "By request", "Remaining"):
        assert heading in text
    # Four decimals: development runs cost fractions of a cent.
    assert "$0.0710" in text
    assert "$3.9290" in text
    assert "REQ-0001" in text


def test_main_prints_report_for_a_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in ENTRIES) + "\n", encoding="utf-8")

    main(ledger_path=ledger, environ={"BUDGET_USD": "2.00"})

    out = capsys.readouterr().out
    assert "$0.0710" in out
    assert "$1.9290" in out  # the budget comes from BUDGET_USD


def test_main_with_no_ledger_reports_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(ledger_path=tmp_path / "missing.jsonl", environ={})

    out = capsys.readouterr().out
    assert "$0.0000" in out
    assert "$4.0000" in out

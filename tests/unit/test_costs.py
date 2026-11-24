"""Tests for cost maths and the budget gate (PLAN §4). No network, no Anthropic SDK."""

import json
from pathlib import Path

import pytest

from it_requests.agent.costs import (
    BudgetExceededError,
    LedgerCorruptError,
    UnknownModelError,
    budget_from_env,
    check_budget,
    cost_usd,
    ledger_total,
)

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"

# Costs are rounded to 6 decimals; approx absorbs float representation, not real error.
EXACT = 1e-9


# --- cost_usd ---------------------------------------------------------------------------


def test_haiku_cost_for_known_usage() -> None:
    # 10,000 input x $0.10/M = $0.001; 2,000 output x $0.50/M = $0.001. Total $0.002.
    cost = cost_usd(HAIKU, input_tokens=10_000, output_tokens=2_000)

    assert cost == pytest.approx(0.002, abs=EXACT)


def test_sonnet_cost_for_known_usage() -> None:
    # 10,000 input x $2.00/M = $0.02; 2,000 output x $10.00/M = $0.02. Total $0.04.
    cost = cost_usd(SONNET, input_tokens=10_000, output_tokens=2_000)

    assert cost == pytest.approx(0.04, abs=EXACT)


def test_cache_tokens_priced_separately_from_input() -> None:
    # Sonnet: 1,000 input x $2.00/M = $0.002; 100,000 cache read x $0.10/M = $0.01;
    # 10,000 cache write x $2.50/M = $0.025. Total $0.037.
    cost = cost_usd(
        SONNET,
        input_tokens=1_000,
        output_tokens=0,
        cache_read_tokens=100_000,
        cache_write_tokens=10_000,
    )

    assert cost == pytest.approx(0.037, abs=EXACT)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        # One million tokens of each kind on Sonnet, so each price shows directly.
        ("input_tokens", 2.00),
        ("cache_read_tokens", 0.10),
        ("cache_write_tokens", 2.50),
        ("output_tokens", 10.00),
    ],
)
def test_each_token_kind_has_its_own_price(kind: str, expected: float) -> None:
    usage = {"input_tokens": 0, "output_tokens": 0, kind: 1_000_000}

    assert cost_usd(SONNET, **usage) == pytest.approx(expected, abs=EXACT)


def test_haiku_exactly_100k_uses_normal_prices() -> None:
    # Not above the threshold: 100,000 x $0.10/M = $0.01; 1,000 x $0.50/M = $0.0005.
    cost = cost_usd(HAIKU, input_tokens=100_000, output_tokens=1_000)

    assert cost == pytest.approx(0.0105, abs=EXACT)


def test_haiku_just_above_100k_uses_long_prompt_prices() -> None:
    # Above the threshold, so the long-prompt prices apply to the whole call:
    # 100,002 x $0.50/M = $0.050001; 1,000 x $2.50/M = $0.0025. Total $0.052501.
    cost = cost_usd(HAIKU, input_tokens=100_002, output_tokens=1_000)

    assert cost == pytest.approx(0.052501, abs=EXACT)


def test_sonnet_has_no_long_prompt_tier() -> None:
    # 200,000 x $2.00/M = $0.40: Sonnet's price doesn't change above 100K.
    assert cost_usd(SONNET, input_tokens=200_000, output_tokens=0) == pytest.approx(0.40, abs=EXACT)


def test_cost_is_rounded_to_six_decimals() -> None:
    # 1 Haiku input token = $0.0000001, which rounds to 0 at 6 decimals.
    assert cost_usd(HAIKU, input_tokens=1, output_tokens=0) == 0.0


def test_unknown_model_raises() -> None:
    # A programming error (wrong model ID in config), so raising is right here.
    with pytest.raises(UnknownModelError, match="claude-opus-5-5"):
        cost_usd("claude-opus-5-5", input_tokens=1, output_tokens=1)


# --- ledger_total --------------------------------------------------------------------------


def _write_ledger(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_missing_ledger_is_zero(tmp_path: Path) -> None:
    assert ledger_total(tmp_path / "cost_ledger.jsonl") == 0.0


def test_ledger_sums_every_line(tmp_path: Path) -> None:
    path = _write_ledger(
        tmp_path / "cost_ledger.jsonl",
        [json.dumps({"model": HAIKU, "cost_usd": c}) for c in (0.1, 0.2, 0.3)],
    )

    assert ledger_total(path) == pytest.approx(0.6, abs=EXACT)


def test_blank_trailing_line_is_ignored(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path / "cost_ledger.jsonl", [json.dumps({"cost_usd": 0.5}), "", "   "])

    assert ledger_total(path) == pytest.approx(0.5, abs=EXACT)


@pytest.mark.parametrize(
    "bad_line",
    [
        "not json",
        json.dumps({"model": HAIKU}),  # no cost_usd
        json.dumps({"cost_usd": "0.10"}),  # not numeric
        json.dumps({"cost_usd": None}),
        json.dumps({"cost_usd": True}),  # a bool is not a cost, even though bool is an int
        json.dumps([0.1]),  # not an object
    ],
)
def test_corrupt_line_raises_with_line_number(tmp_path: Path, bad_line: str) -> None:
    # Unreadable ledger = unknown spend, so the safe answer is to refuse, not guess 0.
    path = _write_ledger(tmp_path / "cost_ledger.jsonl", [json.dumps({"cost_usd": 0.1}), bad_line])

    with pytest.raises(LedgerCorruptError, match="line 2"):
        ledger_total(path)


# --- check_budget --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spent", "expected"),
    [(0.0, "ok"), (2.99, "ok"), (3.00, "warn"), (3.99, "warn")],
)
def test_check_budget_status(spent: float, expected: str) -> None:
    assert check_budget(spent, budget=4.00, warn_at=3.00) == expected


@pytest.mark.parametrize("spent", [4.00, 4.01])
def test_check_budget_refuses_at_or_over_budget(spent: float) -> None:
    # At the budget counts as over: the gate refuses at $4.00, not after it.
    with pytest.raises(BudgetExceededError):
        check_budget(spent, budget=4.00, warn_at=3.00)


# --- budget_from_env -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, 4.00),
        ({"BUDGET_USD": "2.5"}, 2.5),
        ({"BUDGET_USD": " 3 "}, 3.0),
        ({"BUDGET_USD": "0"}, 0.0),
    ],
)
def test_budget_from_env(environ: dict[str, str], expected: float) -> None:
    assert budget_from_env(environ) == expected


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "-1",
        "",
        # nan and inf parse as floats, but would switch the gate off: spent >= nan is
        # always False, and nothing reaches inf. Both must be refused.
        "nan",
        "inf",
    ],
)
def test_bad_budget_value_raises(value: str) -> None:
    with pytest.raises(ValueError, match="BUDGET_USD"):
        budget_from_env({"BUDGET_USD": value})

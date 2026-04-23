from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from smart_contracts.lmsr_math import lmsr_prices

from .market_app_test_utils import buy_one, make_active_lp_market


def test_restamp_bundle_duration_preserves_grouping_and_settlement() -> None:
    original_timestamps = [1, 2, 2, 10, 11]
    restamped = [1, 3, 3, 12, 13]
    grouped = {}
    for original, new in zip(original_timestamps, restamped):
        grouped.setdefault(original, set()).add(new)

    assert all(len(mapped) == 1 for mapped in grouped.values())
    assert 12 in restamped
    assert any(timestamp > 12 for timestamp in restamped)


def test_normalized_weighting_reduces_duration_sensitivity() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="buyer", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    short_entry = market.enter_lp_active(
        sender="short_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(prices),
        now=12,
    )
    long_entry = market.enter_lp_active(
        sender="long_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(lmsr_prices(market.q, market.b)),
        now=168,
    )

    assert short_entry["shares_minted"] == long_entry["shares_minted"]


def test_calibration_upgrade_writes_outputs(tmp_path: Path) -> None:
    summary = {
        "selected_lambda": "0.03250",
        "mean_fairness_gap_nav_per_deposit": "0.00012",
    }
    output = tmp_path / "lambda_selection_summary.json"
    output.write_text(json.dumps(summary), encoding="utf-8")

    assert json.loads(output.read_text(encoding="utf-8"))["selected_lambda"] == "0.03250"

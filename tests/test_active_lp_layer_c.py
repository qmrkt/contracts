from __future__ import annotations

from smart_contracts.lmsr_math import lmsr_prices, lmsr_q_from_prices_with_floor

from .market_app_test_utils import buy_one, make_active_lp_market


def test_layer_c_runner_produces_stable_neutral_outputs() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    trade = buy_one(market, sender="buyer", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    entry = market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(prices),
        now=3,
    )

    assert trade["total_cost"] > trade["cost"]
    assert entry["shares_minted"] == 25_000_000
    assert market.pool_balance > 200_000_000


def test_layer_c_preserves_lp_entry_prices_on_neutral_path() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    prices = lmsr_prices(market.q, market.b)
    repriced_q = lmsr_q_from_prices_with_floor(prices, market.b + 10_000_000, market.q)

    assert max(abs(a - b) for a, b in zip(prices, lmsr_prices(repriced_q, market.b + 10_000_000))) <= 2

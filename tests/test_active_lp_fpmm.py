from __future__ import annotations

from smart_contracts.lmsr_math import SCALE, lmsr_collateral_required_from_prices, lmsr_prices

from .market_app_test_utils import buy_one, make_active_lp_market, resolve_market


def test_fpmm_bootstrap_starts_uniform() -> None:
    market = make_active_lp_market(num_outcomes=4)
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)

    prices = lmsr_prices(market.q, market.b)

    assert all(abs(price - SCALE // 4) <= 1 for price in prices)
    assert market.pool_balance == 200_000_000


def test_fpmm_lp_entry_preserves_prices_and_preexisting_nav() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="alice", outcome_index=0, now=2)
    before_prices = lmsr_prices(market.q, market.b)
    required = lmsr_collateral_required_from_prices(40_000_000, before_prices)

    result = market.enter_lp_active(
        sender="late_lp",
        target_delta_b=40_000_000,
        max_deposit=required + 1,
        expected_prices=list(before_prices),
        now=3,
    )

    assert result["deposit_required"] == required
    assert max(abs(a - b) for a, b in zip(before_prices, lmsr_prices(market.q, market.b))) <= 2


def test_fpmm_late_lp_only_receives_future_fees() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    first = buy_one(market, sender="alice", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(prices),
        now=3,
    )
    second = buy_one(market, sender="bob", outcome_index=2, now=4)

    late_claim = market.claim_lp_fees(sender="late_lp")

    assert first["lp_fee"] > 0
    assert second["lp_fee"] > 0
    assert 0 < late_claim < second["lp_fee"]


def test_fpmm_residual_claims_zero_terminal_nav() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="winner", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(prices),
        now=3,
    )

    resolve_market(market, outcome_index=0)
    market.claim_lp_residual(sender="creator")
    market.claim_lp_residual(sender="late_lp")

    assert market._claimable_residual("creator") == 0
    assert market._claimable_residual("late_lp") == 0

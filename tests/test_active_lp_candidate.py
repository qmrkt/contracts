from __future__ import annotations

from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.model import SHARE_UNIT, STATUS_RESOLVED

from .market_app_test_utils import buy_one, make_active_lp_market, resolve_market


def test_candidate_lp_entry_preserves_prices_and_preexisting_nav() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="trader", outcome_index=1, now=2)

    before_prices = lmsr_prices(market.q, market.b)
    result = market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(before_prices),
        now=3,
    )
    after_prices = lmsr_prices(market.q, market.b)

    assert result["shares_minted"] == 50_000_000
    assert result["deposit_required"] <= 200_000_000
    assert max(abs(before - after) for before, after in zip(before_prices, after_prices)) <= 2


def test_candidate_resolved_claims_and_lp_residuals_conserve_funds() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    market.buy(sender="winner", outcome_index=1, max_cost=20_000_000, now=2, shares=SHARE_UNIT)
    prices = lmsr_prices(market.q, market.b)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(prices),
        now=3,
    )

    resolve_market(market, outcome_index=1)
    payout = market.claim(sender="winner", outcome_index=1)
    creator_residual = market.claim_lp_residual(sender="creator")
    late_residual = market.claim_lp_residual(sender="late_lp")

    assert market.status == STATUS_RESOLVED
    assert payout["payout"] == SHARE_UNIT
    assert creator_residual > 0
    assert late_residual > 0
    assert market.pool_balance >= 0


def test_candidate_scenario_runner_produces_canonical_outputs() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    first_trade = buy_one(market, sender="buyer", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    entry = market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(prices),
        now=3,
    )

    assert first_trade["lp_fee"] > 0
    assert entry["shares_minted"] == 25_000_000

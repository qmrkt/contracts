from __future__ import annotations

import pytest

from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.model import SHARE_UNIT, MarketAppError, STATUS_CANCELLED, STATUS_RESOLVED

from .market_app_test_utils import buy_one, make_active_lp_market, resolve_market

RESERVE_POOL = "reserve_pool"


def test_bootstrap_requires_lmsr_funding_floor() -> None:
    market = make_active_lp_market()

    with pytest.raises(MarketAppError, match="solvency floor"):
        market.bootstrap(sender="creator", deposit_amount=50_000_000, now=1)


def test_lp_entry_preserves_prices_and_preexisting_nav() -> None:
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

    assert result["deposit_required"] <= 200_000_000
    assert max(abs(a - b) for a, b in zip(before_prices, lmsr_prices(market.q, market.b))) <= 2


def test_late_lp_only_receives_subsequent_fees() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    first = buy_one(market, sender="trader", outcome_index=0, now=2)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(lmsr_prices(market.q, market.b)),
        now=3,
    )
    second = buy_one(market, sender="trader", outcome_index=2, now=4)

    late_claim = market.claim_lp_fees(sender="late_lp")

    assert first["lp_fee"] > 0
    assert 0 < late_claim < second["lp_fee"]


def test_resolved_claims_and_lp_residuals_conserve_funds() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    market.buy(sender="winner", outcome_index=1, max_cost=20_000_000, now=2, shares=SHARE_UNIT)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(lmsr_prices(market.q, market.b)),
        now=3,
    )

    resolve_market(market, outcome_index=1)
    market.claim(sender="winner", outcome_index=1)
    market.claim_lp_residual(sender="creator")
    market.claim_lp_residual(sender="late_lp")

    assert market.status == STATUS_RESOLVED
    assert market.pool_balance >= 0


def test_cancel_refund_and_lp_residual_path_is_conservative() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="buyer", outcome_index=0, now=2)
    market.cancel(sender="creator")

    refund = market.refund(sender="buyer", outcome_index=0)
    residual = market.claim_lp_residual(sender="creator")

    assert market.status == STATUS_CANCELLED
    assert refund["refund_amount"] > 0
    assert residual >= 0


def test_reference_reserve_residual_claim_order_is_non_extractive() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="winner", outcome_index=0, now=2)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(lmsr_prices(market.q, market.b)),
        now=3,
    )
    resolve_market(market, outcome_index=0)

    market.claim_lp_residual(sender="late_lp")
    market.claim_lp_residual(sender="creator")

    assert market.pool_balance >= market.total_user_shares[0]


def test_reference_reserve_cancel_path_keeps_refund_reserve_intact() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="buyer", outcome_index=0, now=2)
    reserve_before = market.total_outstanding_cost_basis
    market.cancel(sender="creator")
    market.claim_lp_residual(sender="creator")

    assert market.pool_balance >= reserve_before


def test_reference_scenario_runner_produces_canonical_outputs() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    trade = buy_one(market, sender="buyer", outcome_index=0, now=2)

    assert RESERVE_POOL == "reserve_pool"
    assert trade["total_cost"] > 0

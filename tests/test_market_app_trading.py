from __future__ import annotations

import pytest

from smart_contracts.market_app.model import SHARE_UNIT, MarketAppError

from .market_app_test_utils import buy_one, make_market


def test_buy_applies_lmsr_fees_slippage_and_transfers_outcome_asa() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    result = market.buy(sender="buyer", outcome_index=1, max_cost=10_000_000, now=5_000)

    assert result["shares"] == SHARE_UNIT
    assert result["cost"] > 0
    assert result["lp_fee"] > 0
    assert result["protocol_fee"] > 0
    assert result["total_cost"] == result["cost"] + result["lp_fee"] + result["protocol_fee"]
    assert market.q[1] == SHARE_UNIT
    assert market.user_outcome_shares["buyer"][1] == SHARE_UNIT
    assert market.user_cost_basis["buyer"][1] == result["cost"]
    assert market.pool_balance == 200_000_000 + result["cost"]
    assert market.events[-1]["event"] == "Buy"

    with pytest.raises(MarketAppError, match="slippage exceeded"):
        market.buy(sender="buyer", outcome_index=1, max_cost=1, now=5_000)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        market.buy(sender="buyer", outcome_index=1, max_cost=10_000_000, now=5_000, shares=0)


def test_buy_30_shares_same_outcome_3_outcome_market_solvency() -> None:
    """Regression test for the old active-trading solvency assertion bug.

    The historic bug was that active trading incorrectly enforced a resolved-state
    solvency check and could abort a valid buy sequence mid-market. After the
    bootstrap-floor hardening, this scenario is now safely overcollateralized, so
    the regression proof is that repeated buys continue to succeed and leave the
    market ACTIVE without tripping a solvency error.
    """
    from smart_contracts.market_app.model import MarketAppModel, STATUS_ACTIVE

    market = MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=[1000, 1001, 1002],
        b=10_000_000,
        lp_fee_bps=0,
        protocol_fee_bps=0,
        deadline=100_000,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=True,
    )
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    total_cost = 0
    for i in range(35):
        result = market.buy(sender=f"buyer{i}", outcome_index=0, max_cost=50_000_000, now=5_000 + i)
        total_cost += result["total_cost"]

    assert market.status == STATUS_ACTIVE
    assert market.q[0] == 35 * SHARE_UNIT
    assert market.pool_balance == 200_000_000 + total_cost
    assert market.pool_balance >= max(market.q)


def test_sell_applies_reverse_lmsr_fees_slippage_and_transfers_usdc() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="seller", outcome_index=1)
    basis_before = market.user_cost_basis["seller"][1]

    result = market.sell(sender="seller", outcome_index=1, min_return=1, now=5_001)

    assert result["shares"] == SHARE_UNIT
    assert result["gross_return"] > result["net_return"] > 0
    assert result["lp_fee"] > 0
    assert result["protocol_fee"] > 0
    assert market.user_outcome_shares["seller"][1] == 0
    assert basis_before > 0
    assert market.user_cost_basis["seller"][1] == 0
    assert market.q[1] == 0
    assert market.events[-1]["event"] == "Sell"

    buy_one(market, sender="seller2", outcome_index=1)
    with pytest.raises(MarketAppError, match="slippage exceeded"):
        market.sell(sender="seller2", outcome_index=1, min_return=10_000_000, now=5_002)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        market.sell(sender="seller2", outcome_index=1, min_return=1, now=5_002, shares=0)


def test_buy_and_sell_multiple_shares_in_single_call() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    buy_shares = 3 * SHARE_UNIT
    buy_result = market.buy(
        sender="buyer",
        outcome_index=1,
        shares=buy_shares,
        max_cost=50_000_000,
        now=5_000,
    )

    assert buy_result["shares"] == buy_shares
    assert market.q[1] == buy_shares
    assert market.user_outcome_shares["buyer"][1] == buy_shares

    sell_shares = 2 * SHARE_UNIT
    cost_basis_before = market.user_cost_basis["buyer"][1]
    sell_result = market.sell(
        sender="buyer",
        outcome_index=1,
        shares=sell_shares,
        min_return=1,
        now=5_001,
    )

    assert sell_result["shares"] == sell_shares
    assert sell_result["net_return"] > 0
    assert market.user_outcome_shares["buyer"][1] == SHARE_UNIT
    assert market.q[1] == SHARE_UNIT
    assert 0 < market.user_cost_basis["buyer"][1] < cost_basis_before

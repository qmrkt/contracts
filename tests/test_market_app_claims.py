from __future__ import annotations

import pytest

from smart_contracts.market_app.model import SHARE_UNIT, STATUS_CANCELLED, MarketAppError

from .market_app_test_utils import buy_one, make_market, resolve_market


def test_claim_redeems_only_winning_outcome_one_to_one_and_preserves_lp_residual() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="winner", outcome_index=0)
    buy_one(market, sender="loser", outcome_index=1)
    resolve_market(market)

    starting_pool = market.pool_balance
    outstanding = market.q[0]
    cost_basis_before = market.user_cost_basis["winner"][0]
    claim_result = market.claim(sender="winner", outcome_index=0)

    assert claim_result["shares"] == SHARE_UNIT
    assert claim_result["payout"] == SHARE_UNIT
    assert cost_basis_before > 0
    assert market.user_outcome_shares["winner"][0] == 0
    assert market.user_cost_basis["winner"][0] == 0
    assert market.q[0] == outstanding - SHARE_UNIT
    assert market.pool_balance == starting_pool - SHARE_UNIT

    with pytest.raises(MarketAppError, match="only winning outcome"):
        market.claim(sender="loser", outcome_index=1, shares=SHARE_UNIT)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        market.claim(sender="winner", outcome_index=0, shares=0)


def test_cancel_and_refund_path() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="buyer", outcome_index=2)
    cost_basis_before = market.user_cost_basis["buyer"][2]

    with pytest.raises(MarketAppError, match="only creator"):
        market.cancel(sender="not-creator")

    market.cancel(sender="creator")
    assert market.status == STATUS_CANCELLED

    refund_result = market.refund(sender="buyer", outcome_index=2)
    assert refund_result["shares"] == SHARE_UNIT
    assert refund_result["refund_amount"] == cost_basis_before
    assert market.user_outcome_shares["buyer"][2] == 0
    assert market.user_cost_basis["buyer"][2] == 0
    with pytest.raises(MarketAppError, match="shares must be positive"):
        market.refund(sender="buyer", outcome_index=2, shares=0)


def test_claim_and_refund_multiple_shares_in_single_call() -> None:
    claim_market = make_market()
    claim_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    claim_market.buy(
        sender="winner",
        outcome_index=0,
        shares=3 * SHARE_UNIT,
        max_cost=50_000_000,
        now=5_000,
    )
    claim_market.buy(sender="loser", outcome_index=1, max_cost=10_000_000, now=5_001)
    resolve_market(claim_market)

    claim_shares = 2 * SHARE_UNIT
    outstanding_before_claim = claim_market.q[0]
    pool_before_claim = claim_market.pool_balance
    claim_result = claim_market.claim(sender="winner", outcome_index=0, shares=claim_shares)

    assert claim_result["shares"] == claim_shares
    assert claim_result["payout"] == claim_shares
    assert claim_market.user_outcome_shares["winner"][0] == SHARE_UNIT
    assert claim_market.q[0] == outstanding_before_claim - claim_shares
    assert claim_market.pool_balance == pool_before_claim - claim_shares

    refund_market = make_market()
    refund_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    refund_market.buy(
        sender="buyer",
        outcome_index=2,
        shares=3 * SHARE_UNIT,
        max_cost=50_000_000,
        now=5_000,
    )
    cost_basis_before_refund = refund_market.user_cost_basis["buyer"][2]
    refund_market.cancel(sender="creator")

    refund_shares = 2 * SHARE_UNIT
    refund_result = refund_market.refund(sender="buyer", outcome_index=2, shares=refund_shares)

    assert refund_result["shares"] == refund_shares
    assert refund_result["refund_amount"] > 0
    assert refund_market.user_outcome_shares["buyer"][2] == SHARE_UNIT
    assert refund_market.q[2] == SHARE_UNIT
    assert refund_market.user_cost_basis["buyer"][2] + refund_result["refund_amount"] == cost_basis_before_refund

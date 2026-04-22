from __future__ import annotations

import pytest

from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.model import SHARE_UNIT, STATUS_ACTIVE, MarketAppError, MarketAppModel

from .market_app_test_utils import buy_one, make_market, resolve_market


def test_provide_liq_scales_b_and_q_mints_shares_and_preserves_prices() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="trader", outcome_index=2)

    before_prices = lmsr_prices(market.q, market.b)
    before_q = list(market.q)
    before_b = market.b
    before_pool = market.pool_balance
    before_total_shares = market.lp_shares_total

    minted = market.provide_liq(sender="lp2", deposit_amount=50_000_000, now=6_000)
    after_prices = lmsr_prices(market.q, market.b)

    assert minted == (before_total_shares * 50_000_000) // before_pool
    assert market.b > before_b
    assert all(after >= before for before, after in zip(before_q, market.q))
    assert all(abs(before - after) <= 1 for before, after in zip(before_prices, after_prices))
    assert market.user_lp_shares["lp2"] == minted
    assert market.user_fee_snapshot["lp2"] == market.cumulative_fee_per_share


def test_withdraw_liq_burns_shares_returns_usdc_and_fees_and_preserves_prices_when_active() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="trader", outcome_index=0)
    market.provide_liq(sender="lp2", deposit_amount=50_000_000, now=6_000)

    creator_before = market.user_lp_shares["creator"]
    before_prices = lmsr_prices(market.q, market.b)
    result = market.withdraw_liq(sender="creator", shares_to_burn=creator_before // 10)
    after_prices = lmsr_prices(market.q, market.b)

    assert result["usdc_return"] > 0
    assert result["fee_return"] >= 0
    assert all(abs(before - after) <= 1 for before, after in zip(before_prices, after_prices))

    cancelled_market = make_market()
    cancelled_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    cancelled_market.cancel(sender="creator")
    withdraw_cancelled = cancelled_market.withdraw_liq(sender="creator", shares_to_burn=1)
    assert withdraw_cancelled["usdc_return"] >= 0
    assert cancelled_market.pool_balance >= cancelled_market.total_outstanding_cost_basis


def test_cancelled_lp_withdraw_leaves_trader_refund_reserve_intact() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="trader", outcome_index=0)
    trader_basis = market.user_cost_basis["trader"][0]
    pool_before_cancel_withdraw = market.pool_balance
    creator_lp = market.user_lp_shares["creator"]

    market.cancel(sender="creator")
    result = market.withdraw_liq(sender="creator", shares_to_burn=creator_lp)

    assert result["usdc_return"] == pool_before_cancel_withdraw - trader_basis
    assert market.pool_balance == trader_basis

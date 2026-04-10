from __future__ import annotations

import pytest

from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.active_lp_model import (
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    ActiveLpMarketAppModel,
)
from smart_contracts.market_app.model import SHARE_UNIT, MarketAppError

from .market_app_test_utils import buy_one, resolve_market


def make_active_lp_market(*, num_outcomes: int = 3, deadline: int = 10_000) -> ActiveLpMarketAppModel:
    return ActiveLpMarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=[1000 + i for i in range(num_outcomes)],
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=deadline,
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
    )


def test_v4_lp_entry_preserves_prices_and_disables_active_withdrawal() -> None:
    market = make_active_lp_market()
    minted = market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)
    assert minted == market.b
    assert market.activation_timestamp == 0

    buy_one(market, sender="trader", outcome_index=2)
    before_prices = lmsr_prices(market.q, market.b)

    result = market.enter_lp_active(
        sender="lp2",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(before_prices),
        now=6_000,
    )
    after_prices = lmsr_prices(market.q, market.b)

    assert result["shares_minted"] == 25_000_000
    assert market.user_lp_shares["lp2"] == 25_000_000
    assert max(abs(before - after) for before, after in zip(before_prices, after_prices)) <= 2

    with pytest.raises(MarketAppError, match="disables LP principal withdrawal"):
        market.withdraw_liq(sender="creator", shares_to_burn=1)


def test_v4_rejects_sub_share_granularity() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)

    with pytest.raises(MarketAppError, match="whole share"):
        market.buy(sender="buyer", outcome_index=0, max_cost=10_000_000, now=5_000, shares=SHARE_UNIT - 1)


def test_v4_lp_fees_are_strictly_prospective() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)

    first_trade = buy_one(market, sender="buyer1", outcome_index=0)
    creator_first_claim = market.claim_lp_fees(sender="creator")
    assert creator_first_claim > 0
    assert creator_first_claim <= first_trade["lp_fee"]

    current_prices = lmsr_prices(market.q, market.b)
    market.enter_lp_active(
        sender="lp2",
        target_delta_b=50_000_000,
        max_deposit=200_000_000,
        expected_prices=list(current_prices),
        now=6_000,
    )

    with pytest.raises(MarketAppError, match="no claimable LP fees"):
        market.claim_lp_fees(sender="lp2")

    buy_one(market, sender="buyer2", outcome_index=1, now=6_001)
    creator_second_claim = market.claim_lp_fees(sender="creator")
    lp2_claim = market.claim_lp_fees(sender="lp2")

    assert creator_second_claim > 0
    assert lp2_claim > 0
    assert creator_second_claim > lp2_claim


def test_v4_rejects_active_lp_entry_above_skew_cap() -> None:
    market = make_active_lp_market(num_outcomes=2)
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)

    market.buy(
        sender="buyer",
        outcome_index=0,
        max_cost=2_000_000_000,
        now=5_000,
        shares=150 * SHARE_UNIT,
    )
    current_prices = lmsr_prices(market.q, market.b)
    assert max(current_prices) > DEFAULT_LP_ENTRY_MAX_PRICE_FP

    with pytest.raises(MarketAppError, match="skew cap"):
        market.enter_lp_active(
            sender="lp2",
            target_delta_b=25_000_000,
            max_deposit=100_000_000,
            expected_prices=list(current_prices),
            now=6_000,
        )


def test_v4_lp_fee_withdrawal_uses_fee_balance_not_non_fee_pool() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)
    buy_one(market, sender="buyer", outcome_index=0)

    claimed = market.claim_lp_fees(sender="creator")
    pool_before = market.pool_balance
    fee_balance_before = market.lp_fee_balance
    withdrawn = market.withdraw_lp_fees(sender="creator", amount=claimed // 2)

    assert withdrawn == claimed // 2
    assert market.pool_balance == pool_before
    assert market.lp_fee_balance == fee_balance_before - withdrawn


def test_v4_residual_release_respects_winner_reserve() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)
    buy_one(market, sender="winner", outcome_index=0)
    buy_one(market, sender="loser", outcome_index=1, now=5_001)
    resolve_market(market)

    reserve_before = market.total_user_shares[0]
    first_lp_payout = market.claim_lp_residual(sender="creator")

    assert first_lp_payout > 0
    assert market.pool_balance >= reserve_before

    winner_claim = market.claim(sender="winner", outcome_index=0)

    assert winner_claim["payout"] == SHARE_UNIT
    assert market.pool_balance >= market.total_user_shares[0]
    with pytest.raises(MarketAppError, match="no claimable residual"):
        market.claim_lp_residual(sender="creator")


def test_v4_time_weighted_residual_favors_earlier_lp_for_equal_depth() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=0)
    market.enter_lp_active(
        sender="lp2",
        target_delta_b=100_000_000,
        max_deposit=200_000_000,
        expected_prices=list(lmsr_prices(market.q, market.b)),
        now=5_000,
    )

    market.cancel(sender="creator")

    creator_payout = market.claim_lp_residual(sender="creator")
    lp2_payout = market.claim_lp_residual(sender="lp2")

    assert market.residual_linear_lambda_fp == DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP
    assert creator_payout > lp2_payout


def test_v4_normalized_residual_weight_is_duration_invariant() -> None:
    short = make_active_lp_market(deadline=20)
    long = make_active_lp_market(deadline=200)

    short.bootstrap(sender="creator", deposit_amount=200_000_000, now=10)
    long.bootstrap(sender="creator", deposit_amount=200_000_000, now=100)

    short.enter_lp_active(
        sender="lp2",
        target_delta_b=100_000_000,
        max_deposit=200_000_000,
        expected_prices=list(lmsr_prices(short.q, short.b)),
        now=13,
    )
    long.enter_lp_active(
        sender="lp2",
        target_delta_b=100_000_000,
        max_deposit=200_000_000,
        expected_prices=list(lmsr_prices(long.q, long.b)),
        now=133,
    )

    short.cancel(sender="creator")
    long.cancel(sender="creator")

    short_creator_weight = short._residual_weight("creator")
    short_lp2_weight = short._residual_weight("lp2")
    long_creator_weight = long._residual_weight("creator")
    long_lp2_weight = long._residual_weight("lp2")

    assert short_creator_weight * long_lp2_weight == long_creator_weight * short_lp2_weight

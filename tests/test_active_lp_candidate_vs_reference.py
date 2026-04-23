from __future__ import annotations

from smart_contracts.lmsr_math import lmsr_prices

from .market_app_test_utils import buy_one, make_active_lp_market


def test_candidate_tracks_reference_on_neutral_late_lp() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="buyer", outcome_index=0, now=2)

    before_q = list(market.q)
    before_b = market.b
    before_prices = lmsr_prices(before_q, before_b)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(before_prices),
        now=3,
    )

    assert market.b == before_b + 25_000_000
    assert max(abs(a - b) for a, b in zip(before_prices, lmsr_prices(market.q, market.b))) <= 2


def test_candidate_tracks_reference_on_reserve_residual_claim_ordering() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="winner", outcome_index=0, now=2)
    prices = lmsr_prices(market.q, market.b)
    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(prices),
        now=3,
    )

    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)
    late_first = market.claim_lp_residual(sender="late_lp")
    creator_second = market.claim_lp_residual(sender="creator")

    assert late_first > 0
    assert creator_second > 0
    assert market.pool_balance >= market.total_user_shares[0]

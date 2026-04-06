from __future__ import annotations

import pytest

from smart_contracts.lmsr_math import SCALE, lmsr_prices
from smart_contracts.market_app.model import STATUS_RESOLVED, MarketAppError

from .market_app_test_utils import bootstrap_and_buy, buy_one, make_market, resolve_market


def test_market_app_invariants() -> None:
    cases = []

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    cases.append(market)

    market = bootstrap_and_buy()
    cases.append(market)

    market = bootstrap_and_buy()
    market.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
    cases.append(market)

    market = bootstrap_and_buy()
    market.provide_liq(sender="lp2", deposit_amount=10_000_000, now=5_500)
    cases.append(market)

    market = bootstrap_and_buy()
    market.withdraw_liq(sender="creator", shares_to_burn=10_000_000)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    market.challenge_resolution(sender="challenger", bond_paid=market.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=market.deadline + 2)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="winner", outcome_index=0)
    resolve_market(market)
    market.claim(sender="winner", outcome_index=0)
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.cancel(sender="creator")
    cases.append(market)

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="buyer", outcome_index=1)
    market.cancel(sender="creator")
    market.refund(sender="buyer", outcome_index=1)
    cases.append(market)

    for case in cases:
        assert case.pool_balance >= max(case.q, default=0)
        if case.status != STATUS_RESOLVED and case.b > 0:
            assert abs(sum(lmsr_prices(case.q, case.b)) - SCALE) <= case.num_outcomes


def test_authorization_and_status_negative_cases() -> None:
    market = make_market()
    with pytest.raises(MarketAppError, match="only creator"):
        market.bootstrap(sender="bad", deposit_amount=100)

    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    with pytest.raises(MarketAppError, match="only resolution authority"):
        market.propose_resolution(sender="bad", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)

    active_market = make_market()
    active_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="only creator"):
        active_market.cancel(sender="bad")


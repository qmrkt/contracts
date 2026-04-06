from __future__ import annotations

import pytest

from smart_contracts.market_app.model import (
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    MarketAppError,
)

from .market_app_test_utils import buy_one, make_market


def test_market_app_state_machine() -> None:
    market = make_market()

    with pytest.raises(MarketAppError, match="invalid status"):
        market.buy(sender="trader", outcome_index=0, max_cost=1_000_000, now=1)

    with pytest.raises(MarketAppError, match="only creator"):
        market.bootstrap(sender="alice", deposit_amount=200_000_000)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    with pytest.raises(MarketAppError, match="deadline not reached"):
        market.trigger_resolution(sender="anyone", now=market.deadline - 1)

    with pytest.raises(MarketAppError, match="only creator"):
        market.cancel(sender="not-creator")

    market.trigger_resolution(sender="anyone", now=market.deadline)
    assert market.status == STATUS_RESOLUTION_PENDING

    with pytest.raises(MarketAppError, match="invalid status"):
        market.buy(sender="trader", outcome_index=0, max_cost=1_000_000, now=market.deadline - 1)

    with pytest.raises(MarketAppError, match="only resolution authority"):
        market.propose_resolution(sender="intruder", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)

    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    assert market.status == STATUS_RESOLUTION_PROPOSED

    with pytest.raises(MarketAppError, match="challenge bond too small"):
        market.challenge_resolution(sender="challenger", bond_paid=1, reason_code=1, evidence_hash=b"c" * 32, now=market.deadline + 2)

    challenged_market = make_market()
    challenged_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(challenged_market, sender="buyer", outcome_index=1)
    challenged_market.trigger_resolution(sender="anyone", now=challenged_market.deadline)
    challenged_market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=challenged_market.deadline + 1)
    challenged_market.challenge_resolution(
        sender="challenger",
        bond_paid=challenged_market.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=challenged_market.deadline + 2,
    )
    assert challenged_market.status == STATUS_DISPUTED

    with pytest.raises(MarketAppError, match="invalid status"):
        challenged_market.claim(sender="buyer", outcome_index=1)

    with pytest.raises(MarketAppError, match="challenge window not elapsed"):
        market.finalize_resolution(sender="anyone", now=market.deadline + 2)

    market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)
    assert market.status == STATUS_RESOLVED

    with pytest.raises(MarketAppError, match="invalid status"):
        market.refund(sender="creator", outcome_index=0)


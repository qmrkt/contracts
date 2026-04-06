from __future__ import annotations

import pytest

from smart_contracts.market_app.model import STATUS_DISPUTED, STATUS_RESOLUTION_PENDING, STATUS_RESOLUTION_PROPOSED, STATUS_RESOLVED, MarketAppError

from .market_app_test_utils import make_market


def test_market_app_resolution_lifecycle() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    with pytest.raises(MarketAppError, match="deadline not reached"):
        market.trigger_resolution(sender="anyone", now=market.deadline - 1)

    market.trigger_resolution(sender="anyone", now=market.deadline)
    assert market.status == STATUS_RESOLUTION_PENDING

    with pytest.raises(MarketAppError, match="only resolution authority"):
        market.propose_resolution(sender="intruder", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)

    market.propose_resolution(sender="resolver", outcome_index=1, evidence_hash=b"e" * 32, now=market.deadline + 1)
    assert market.status == STATUS_RESOLUTION_PROPOSED

    with pytest.raises(MarketAppError, match="challenge bond too small"):
        market.challenge_resolution(sender="challenger", bond_paid=1, reason_code=1, evidence_hash=b"c" * 32, now=market.deadline + 2)

    with pytest.raises(MarketAppError, match="challenge window not elapsed"):
        market.finalize_resolution(sender="anyone", now=market.deadline + 2)

    winning = market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)
    assert winning == 1
    assert market.status == STATUS_RESOLVED
    assert market.winning_outcome == 1

    challenged_market = make_market()
    challenged_market.bootstrap(sender="creator", deposit_amount=200_000_000)
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
    assert challenged_market.challenger == "challenger"


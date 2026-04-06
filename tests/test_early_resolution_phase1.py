from __future__ import annotations

import pytest

from smart_contracts.market_app.model import (
    ZERO_ADDRESS,
    MarketAppError,
    STATUS_ACTIVE,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
)

from .market_app_test_utils import buy_one, make_market


def market_with_early_proposal(*, outcome_index: int = 0, proposal_offset: int = 10):
    market = make_market(deadline=10_000)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="buyer", outcome_index=outcome_index, now=5_000)
    proposal_time = market.deadline - proposal_offset
    market.propose_early_resolution(
        sender="resolver",
        outcome_index=outcome_index,
        evidence_hash=b"e" * 32,
        now=proposal_time,
    )
    return market, proposal_time


def early_disputed_market(*, challenge_time: int | None = None):
    market, proposal_time = market_with_early_proposal()
    if challenge_time is None:
        challenge_time = proposal_time + 1
    market.challenge_resolution(
        sender="challenger",
        bond_paid=market.challenge_bond,
        reason_code=7,
        evidence_hash=b"c" * 32,
        now=challenge_time,
    )
    return market, proposal_time, challenge_time


def test_propose_early_resolution_requires_authority_and_predeadline() -> None:
    market = make_market(deadline=10_000)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    with pytest.raises(MarketAppError, match="only resolution authority may early propose"):
        market.propose_early_resolution(
            sender="attacker",
            outcome_index=0,
            evidence_hash=b"e" * 32,
            now=market.deadline - 1,
        )

    with pytest.raises(MarketAppError, match="deadline passed"):
        market.propose_early_resolution(
            sender="resolver",
            outcome_index=0,
            evidence_hash=b"e" * 32,
            now=market.deadline,
        )


def test_early_proposal_enters_proposed_and_can_finalize_unchallenged() -> None:
    market, proposal_time = market_with_early_proposal()

    assert market.status == STATUS_RESOLUTION_PROPOSED
    assert market.proposer == "resolver"
    assert market.proposer_bond_held == market.proposal_bond
    assert market.proposed_outcome == 0
    assert market.proposal_timestamp == proposal_time

    with pytest.raises(MarketAppError, match="invalid status"):
        market.buy(sender="late-buyer", outcome_index=1, max_cost=10_000_000, now=proposal_time + 1)

    winning_outcome = market.finalize_resolution(
        sender="anyone",
        now=proposal_time + market.challenge_window_secs,
    )

    assert winning_outcome == 0
    assert market.status == STATUS_RESOLVED
    assert market.winning_outcome == 0
    assert market.proposer_bond_held == 0


def test_abort_early_resolution_reopens_active_and_clears_metadata() -> None:
    market, proposal_time, challenge_time = early_disputed_market()

    assert market.status == STATUS_DISPUTED

    market.abort_early_resolution(
        sender="resolver",
        ruling_hash=b"r" * 32,
        now=challenge_time + 1,
    )

    assert market.status == STATUS_ACTIVE
    assert market.proposed_outcome == -1
    assert market.proposal_timestamp == 0
    assert market.proposal_evidence_hash == b""
    assert market.proposer == ZERO_ADDRESS
    assert market.challenger == ZERO_ADDRESS
    assert market.proposer_bond_held == 0
    assert market.challenger_bond_held == 0
    assert market.challenge_reason_code == 0
    assert market.challenge_evidence_hash == b""
    assert market.dispute_ref_hash == b""
    assert market.dispute_opened_at == 0
    assert market.dispute_deadline == 0
    assert market.ruling_hash == b""
    assert market.resolution_path_used == 0
    assert market.dispute_backend_kind == 0
    assert market.pending_responder_role == 0
    assert market.dispute_sink_balance == market.proposal_bond // 2

    abort_event = market.events[-1]
    assert abort_event["event"] == "AbortEarlyResolution"
    assert abort_event["challenger_payout"] == market.challenge_bond + (market.proposal_bond // 2)
    assert abort_event["resumed_status"] == STATUS_ACTIVE

    reopened_trade = market.buy(
        sender="reopened-trader",
        outcome_index=1,
        max_cost=10_000_000,
        now=proposal_time + 3,
    )
    assert reopened_trade["total_cost"] > 0


def test_abort_early_resolution_after_deadline_returns_pending() -> None:
    market, proposal_time = market_with_early_proposal(proposal_offset=5)
    market.challenge_resolution(
        sender="challenger",
        bond_paid=market.challenge_bond,
        reason_code=2,
        evidence_hash=b"c" * 32,
        now=market.deadline + 1,
    )

    market.abort_early_resolution(
        sender="resolver",
        ruling_hash=b"r" * 32,
        now=market.deadline + 2,
    )

    assert proposal_time < market.deadline
    assert market.status == STATUS_RESOLUTION_PENDING

    market.propose_resolution(
        sender="resolver",
        outcome_index=1,
        evidence_hash=b"n" * 32,
        now=market.deadline + 3,
    )
    assert market.status == STATUS_RESOLUTION_PROPOSED
    assert market.proposed_outcome == 1


def test_abort_early_resolution_rejects_non_early_disputes() -> None:
    market = make_market(deadline=10_000)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(
        sender="resolver",
        outcome_index=0,
        evidence_hash=b"e" * 32,
        now=market.deadline + 1,
    )
    market.challenge_resolution(
        sender="challenger",
        bond_paid=market.challenge_bond,
        reason_code=3,
        evidence_hash=b"c" * 32,
        now=market.deadline + 2,
    )

    with pytest.raises(MarketAppError, match="proposal was not early"):
        market.abort_early_resolution(
            sender="resolver",
            ruling_hash=b"r" * 32,
            now=market.deadline + 3,
        )

    with pytest.raises(MarketAppError, match="only resolution authority may abort early resolution"):
        early_market, _, challenge_time = early_disputed_market()
        early_market.abort_early_resolution(
            sender="attacker",
            ruling_hash=b"r" * 32,
            now=challenge_time + 1,
        )

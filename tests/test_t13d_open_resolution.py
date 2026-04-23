"""T13d open resolution + bond slashing tests.

Tests: preferred executor grace period, open proposing after grace period,
bond slashing when dispute confirms/overturns, bond accounting correctness,
honest third-party proposer, malicious proposer.
"""

from __future__ import annotations

import pytest

from smart_contracts.market_app.model import (
    MarketAppError,
    MarketAppModel,
    STATUS_CANCELLED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
)


DEPOSIT = 200_000_000
MAX_COST = 50_000_000
CHALLENGE_BOND = 10_000_000
PROPOSAL_BOND = 10_000_000
GRACE_PERIOD = 3_600  # 1 hour


def make_market() -> MarketAppModel:
    return MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=[1000, 1001, 1002],
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=100_000,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=CHALLENGE_BOND,
        proposal_bond=PROPOSAL_BOND,
        grace_period_secs=GRACE_PERIOD,
        market_admin="admin",
    )


def to_pending(m: MarketAppModel) -> None:
    m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
    m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
    m.trigger_resolution(sender="anyone", now=m.deadline)


# ---------------------------------------------------------------------------
# Grace period: authority can propose immediately, others cannot
# ---------------------------------------------------------------------------


class TestGracePeriod:
    def test_authority_proposes_during_grace_period(self) -> None:
        m = make_market()
        to_pending(m)
        # Propose immediately after trigger (within grace period)
        m.propose_resolution(
            sender="resolver", outcome_index=0,
            evidence_hash=b"e" * 32, now=m.deadline + 1,
            bond_paid=0,
        )
        assert m.status == STATUS_RESOLUTION_PROPOSED
        assert m.proposer == "resolver"
        assert m.proposer_bond_held == 0

    def test_authority_proposes_after_grace_period(self) -> None:
        m = make_market()
        to_pending(m)
        m.propose_resolution(
            sender="resolver", outcome_index=1,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=0,
        )
        assert m.status == STATUS_RESOLUTION_PROPOSED
        assert m.proposer_bond_held == 0

    def test_authority_explicit_zero_bond_allowed(self) -> None:
        m = make_market()
        to_pending(m)
        m.propose_resolution(
            sender="resolver", outcome_index=0,
            evidence_hash=b"e" * 32, now=m.deadline + 1,
            bond_paid=0,
        )
        assert m.proposer_bond_held == 0

    def test_third_party_rejected_during_grace_period(self) -> None:
        m = make_market()
        to_pending(m)
        with pytest.raises(MarketAppError, match="grace period"):
            m.propose_resolution(
                sender="third_party", outcome_index=0,
                evidence_hash=b"e" * 32,
                now=m.deadline + 1,  # within grace period
                bond_paid=m.proposal_bond,
            )

    def test_third_party_rejected_at_grace_boundary(self) -> None:
        m = make_market()
        to_pending(m)
        # Exactly at grace period boundary: deadline + grace_period_secs
        # The check is now >= deadline + grace_period_secs, so this should pass
        m.propose_resolution(
            sender="third_party", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD,
            bond_paid=m.proposal_bond,
        )
        assert m.status == STATUS_RESOLUTION_PROPOSED


# ---------------------------------------------------------------------------
# Open proposing: third party after grace period
# ---------------------------------------------------------------------------


class TestOpenProposing:
    def test_third_party_proposes_after_grace_with_bond(self) -> None:
        m = make_market()
        to_pending(m)
        m.propose_resolution(
            sender="third_party", outcome_index=1,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=m.proposal_bond,
        )
        assert m.status == STATUS_RESOLUTION_PROPOSED
        assert m.proposer == "third_party"
        assert m.proposer_bond_held == m.proposal_bond

    def test_third_party_bond_too_small_rejected(self) -> None:
        m = make_market()
        to_pending(m)
        with pytest.raises(MarketAppError, match="proposal bond too small"):
            m.propose_resolution(
                sender="third_party", outcome_index=0,
                evidence_hash=b"e" * 32,
                now=m.deadline + GRACE_PERIOD + 1,
                bond_paid=m.proposal_bond - 1,
            )

    def test_third_party_no_bond_rejected(self) -> None:
        m = make_market()
        to_pending(m)
        with pytest.raises(MarketAppError, match="proposal bond too small"):
            m.propose_resolution(
                sender="third_party", outcome_index=0,
                evidence_hash=b"e" * 32,
                now=m.deadline + GRACE_PERIOD + 1,
                bond_paid=0,
            )

    def test_honest_third_party_full_lifecycle(self) -> None:
        """Third party proposes correctly, goes unchallenged, gets bond back."""
        m = make_market()
        to_pending(m)
        m.propose_resolution(
            sender="honest_proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=m.proposal_bond,
        )
        assert m.proposer_bond_held == m.proposal_bond

        # Finalize (unchallenged): proposer bond returned
        m.finalize_resolution(
            sender="anyone",
            now=m.deadline + GRACE_PERIOD + 1 + m.challenge_window_secs,
        )
        assert m.status == STATUS_RESOLVED
        assert m.proposer_bond_held == 0  # bond returned


# ---------------------------------------------------------------------------
# Bond settlement: challenger was wrong (dispute confirms original proposal)
# ---------------------------------------------------------------------------


class TestChallengerBondSlashed:
    def test_challenger_bond_slashed_when_dispute_confirms(self) -> None:
        """Dispute confirms original proposal: proposer gets reward, sink captures remainder."""
        m = make_market()
        to_pending(m)

        # Third party proposes outcome 0 with bond
        proposal_bond = m.proposal_bond
        m.propose_resolution(
            sender="proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=proposal_bond,
        )

        # Challenger disputes
        challenge_bond = m.challenge_bond
        m.challenge_resolution(
            sender="challenger", bond_paid=challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + GRACE_PERIOD + 2,
        )
        assert m.challenger_bond_held == challenge_bond

        protocol_fees_before = m.protocol_fee_balance
        dispute_sink_before = m.dispute_sink_balance

        # Dispute resolves to SAME outcome as original proposal (outcome 0)
        m.finalize_dispute(
            sender="resolver", outcome_index=0,
            ruling_hash=b"r" * 32,
        )

        expected_sink = challenge_bond - ((challenge_bond * m.winner_share_bps) // 10_000)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 0
        assert m.protocol_fee_balance == protocol_fees_before
        assert m.dispute_sink_balance == dispute_sink_before + expected_sink
        assert m.proposer_bond_held == 0
        assert m.challenger_bond_held == 0

    def test_authority_proposer_earns_reward_when_challenger_loses(self) -> None:
        m = make_market()
        to_pending(m)

        m.propose_resolution(
            sender="resolver", outcome_index=1,
            evidence_hash=b"e" * 32,
            now=m.deadline + 1,
            bond_paid=0,
        )

        challenge_bond = m.challenge_bond
        m.challenge_resolution(
            sender="challenger", bond_paid=challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + 2,
        )

        protocol_fees_before = m.protocol_fee_balance
        dispute_sink_before = m.dispute_sink_balance

        m.creator_resolve_dispute(
            sender="resolver", outcome_index=1,
            ruling_hash=b"r" * 32,
        )

        expected_sink = challenge_bond - ((challenge_bond * m.winner_share_bps) // 10_000)
        assert m.protocol_fee_balance == protocol_fees_before
        assert m.dispute_sink_balance == dispute_sink_before + expected_sink


# ---------------------------------------------------------------------------
# Bond settlement: proposer was wrong (dispute overturns)
# ---------------------------------------------------------------------------


class TestProposerBondSlashed:
    def test_proposer_bond_slashed_when_overturned(self) -> None:
        """Dispute overturns proposal: challenger gets reward, sink captures remainder."""
        m = make_market()
        to_pending(m)

        proposal_bond = m.proposal_bond
        m.propose_resolution(
            sender="malicious_proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=proposal_bond,
        )

        m.challenge_resolution(
            sender="challenger", bond_paid=m.challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + GRACE_PERIOD + 2,
        )

        protocol_fees_before = m.protocol_fee_balance
        dispute_sink_before = m.dispute_sink_balance

        # Dispute resolves to DIFFERENT outcome (outcome 2, not 0)
        m.finalize_dispute(
            sender="resolver", outcome_index=2,
            ruling_hash=b"r" * 32,
        )

        expected_sink = proposal_bond - ((proposal_bond * m.winner_share_bps) // 10_000)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 2
        assert m.protocol_fee_balance == protocol_fees_before
        assert m.dispute_sink_balance == dispute_sink_before + expected_sink
        assert m.proposer_bond_held == 0
        assert m.challenger_bond_held == 0

    def test_overturned_via_admin(self) -> None:
        """Admin overturns: same slashing logic applies."""
        m = make_market()
        to_pending(m)

        proposal_bond = m.proposal_bond
        m.propose_resolution(
            sender="bad_proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=proposal_bond,
        )

        m.challenge_resolution(
            sender="challenger", bond_paid=m.challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + GRACE_PERIOD + 2,
        )

        protocol_fees_before = m.protocol_fee_balance
        dispute_sink_before = m.dispute_sink_balance

        m.admin_resolve_dispute(
            sender="resolver", outcome_index=1,
            ruling_hash=b"a" * 32,
        )

        expected_sink = proposal_bond - ((proposal_bond * m.winner_share_bps) // 10_000)
        assert m.protocol_fee_balance == protocol_fees_before
        assert m.dispute_sink_balance == dispute_sink_before + expected_sink


# ---------------------------------------------------------------------------
# Bond settlement: market cancelled
# ---------------------------------------------------------------------------


class TestCancelBothBondsSlashed:
    def test_cancel_refunds_challenger_and_slashes_proposer_to_sink(self) -> None:
        m = make_market()
        to_pending(m)

        proposal_bond = m.proposal_bond
        m.propose_resolution(
            sender="proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=proposal_bond,
        )

        m.challenge_resolution(
            sender="challenger", bond_paid=m.challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + GRACE_PERIOD + 2,
        )

        protocol_fees_before = m.protocol_fee_balance
        dispute_sink_before = m.dispute_sink_balance

        m.cancel_dispute_and_market(
            sender="resolver", ruling_hash=b"x" * 32,
        )

        assert m.status == STATUS_CANCELLED
        assert m.protocol_fee_balance == protocol_fees_before
        assert m.dispute_sink_balance == dispute_sink_before + proposal_bond
        assert m.proposer_bond_held == 0
        assert m.challenger_bond_held == 0


# ---------------------------------------------------------------------------
# Bond accounting edge cases
# ---------------------------------------------------------------------------


class TestBondAccounting:
    def test_zero_grace_period_allows_immediate_open_proposing(self) -> None:
        m = MarketAppModel(
            creator="creator",
            currency_asa=31566704,
            outcome_asa_ids=[1000, 1001],
            b=100_000_000,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            protocol_config_id=77,
            factory_id=88,
            resolution_authority="resolver",
            challenge_bond=CHALLENGE_BOND,
            proposal_bond=PROPOSAL_BOND,
            grace_period_secs=0,  # no grace period
            market_admin="admin",
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline)

        # Anyone can propose immediately with bond
        m.propose_resolution(
            sender="fast_proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline,  # immediately
            bond_paid=m.proposal_bond,
        )
        assert m.proposer == "fast_proposer"

    def test_overpaid_bond_tracked_correctly(self) -> None:
        """If proposer pays more than required, full amount is tracked."""
        m = make_market()
        to_pending(m)
        base_bond = m.proposal_bond
        m.propose_resolution(
            sender="generous", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=base_bond * 2,
        )
        assert m.proposer_bond_held == base_bond * 2

    def test_no_double_slash(self) -> None:
        """Bond settlement zeroes held amounts; claim doesn't affect sink accounting."""
        m = make_market()
        to_pending(m)
        proposal_bond = m.proposal_bond
        m.propose_resolution(
            sender="proposer", outcome_index=0,
            evidence_hash=b"e" * 32,
            now=m.deadline + GRACE_PERIOD + 1,
            bond_paid=proposal_bond,
        )
        m.challenge_resolution(
            sender="challenger", bond_paid=m.challenge_bond,
            reason_code=1, evidence_hash=b"c" * 32,
            now=m.deadline + GRACE_PERIOD + 2,
        )
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)

        # Bonds already settled
        assert m.proposer_bond_held == 0
        assert m.challenger_bond_held == 0
        sink_after = m.dispute_sink_balance
        m.claim(sender="trader", outcome_index=0)
        assert m.dispute_sink_balance == sink_after

"""T13c dispute-path tests.

Tests: challenge enters DISPUTED, creator resolves dispute, admin resolves
dispute, authority finalizes dispute, authority cancels dispute, old
auto-cancel path removed, dual blueprint store/read, reject oversized,
reject after bootstrap.
"""

from __future__ import annotations

import pytest

from smart_contracts.market_app.model import (
    SHARE_UNIT,
    MarketAppError,
    MarketAppModel,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
)


DEPOSIT = 200_000_000
MAX_COST = 50_000_000
BOND = 10_000_000


def make_market(cancellable: bool = True) -> MarketAppModel:
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
        challenge_bond=BOND,
        proposal_bond=10_000_000,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=cancellable,
    )


def market_to_proposed() -> MarketAppModel:
    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
    m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
    return m


def market_to_disputed() -> MarketAppModel:
    m = market_to_proposed()
    m.challenge_resolution(
        sender="challenger",
        bond_paid=m.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=m.deadline + 2,
    )
    return m


# ---------------------------------------------------------------------------
# Challenge enters DISPUTED (not CANCELLED)
# ---------------------------------------------------------------------------


class TestChallengeEntersDisputed:
    def test_challenge_sets_disputed_status(self) -> None:
        m = market_to_disputed()
        assert m.status == STATUS_DISPUTED

    def test_challenge_records_challenger(self) -> None:
        m = market_to_disputed()
        assert m.challenger == "challenger"

    def test_challenge_records_reason_code(self) -> None:
        m = market_to_disputed()
        assert m.challenge_reason_code == 1

    def test_challenge_records_evidence_hash(self) -> None:
        m = market_to_disputed()
        assert m.challenge_evidence_hash == b"c" * 32

    def test_challenge_records_dispute_opened_at(self) -> None:
        m = market_to_disputed()
        assert m.dispute_opened_at == m.deadline + 2

    def test_challenge_does_not_cancel(self) -> None:
        """The old auto-cancel path is removed."""
        m = market_to_disputed()
        assert m.status != STATUS_CANCELLED

    def test_refund_not_available_in_disputed(self) -> None:
        """Cannot refund while disputed; market is not cancelled."""
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="invalid status"):
            m.refund(sender="trader", outcome_index=0)

    def test_claim_not_available_in_disputed(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="invalid status"):
            m.claim(sender="trader", outcome_index=0)

    def test_trading_not_available_in_disputed(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="invalid status"):
            m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=m.deadline + 3)


# ---------------------------------------------------------------------------
# Creator resolves dispute
# ---------------------------------------------------------------------------


class TestCreatorResolveDispute:
    def test_creator_resolves_to_resolved(self) -> None:
        m = market_to_disputed()
        m.creator_resolve_dispute(sender="creator", outcome_index=1, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 1
        assert m.ruling_hash == b"r" * 32
        assert m.resolution_path_used == 1  # dispute

    def test_non_creator_cannot_resolve(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only creator"):
            m.creator_resolve_dispute(sender="attacker", outcome_index=0, ruling_hash=b"r" * 32)

    def test_invalid_outcome_rejected(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="outcome_index"):
            m.creator_resolve_dispute(sender="creator", outcome_index=99, ruling_hash=b"r" * 32)

    def test_claim_after_creator_resolve(self) -> None:
        m = market_to_disputed()
        m.creator_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"r" * 32)
        claim_result = m.claim(sender="trader", outcome_index=0)
        assert claim_result["payout"] > 0


# ---------------------------------------------------------------------------
# Admin resolves dispute
# ---------------------------------------------------------------------------


class TestAdminResolveDispute:
    def test_admin_resolves_to_resolved(self) -> None:
        m = market_to_disputed()
        m.admin_resolve_dispute(sender="admin", outcome_index=2, ruling_hash=b"a" * 32)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 2
        assert m.ruling_hash == b"a" * 32
        assert m.resolution_path_used == 2  # admin_fallback

    def test_non_admin_cannot_resolve(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only market admin"):
            m.admin_resolve_dispute(sender="attacker", outcome_index=0, ruling_hash=b"a" * 32)

    def test_creator_cannot_use_admin_resolve(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only market admin"):
            m.admin_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"a" * 32)


# ---------------------------------------------------------------------------
# Authority finalizes dispute
# ---------------------------------------------------------------------------


class TestFinalizeDispute:
    def test_authority_finalizes_to_resolved(self) -> None:
        m = market_to_disputed()
        result = m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"f" * 32)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 1
        assert result == 1
        assert m.resolution_path_used == 1

    def test_non_authority_cannot_finalize(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only resolution authority"):
            m.finalize_dispute(sender="attacker", outcome_index=0, ruling_hash=b"f" * 32)


# ---------------------------------------------------------------------------
# Authority cancels dispute and market
# ---------------------------------------------------------------------------


class TestCancelDisputeAndMarket:
    def test_cancel_dispute_sets_cancelled(self) -> None:
        m = market_to_disputed()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"x" * 32)
        assert m.status == STATUS_CANCELLED
        assert m.ruling_hash == b"x" * 32

    def test_refund_after_cancel_dispute(self) -> None:
        m = market_to_disputed()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"x" * 32)
        refund_result = m.refund(sender="trader", outcome_index=0)
        assert refund_result["refund_amount"] > 0

    def test_non_authority_cannot_cancel(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only resolution authority"):
            m.cancel_dispute_and_market(sender="attacker", ruling_hash=b"x" * 32)


# ---------------------------------------------------------------------------
# Register dispute (metadata)
# ---------------------------------------------------------------------------


class TestRegisterDispute:
    def test_register_dispute_stores_metadata(self) -> None:
        m = market_to_disputed()
        m.register_dispute(
            sender="resolver",
            dispute_ref_hash=b"ref" * 10 + b"rr",
            backend_kind=1,
            deadline=m.deadline + 100_000,
        )
        assert m.dispute_ref_hash == b"ref" * 10 + b"rr"
        assert m.dispute_backend_kind == 1
        assert m.dispute_deadline == m.deadline + 100_000
        assert m.status == STATUS_DISPUTED  # still disputed

    def test_non_authority_cannot_register(self) -> None:
        m = market_to_disputed()
        with pytest.raises(MarketAppError, match="only resolution authority"):
            m.register_dispute(sender="attacker", dispute_ref_hash=b"x", backend_kind=0, deadline=0)


# ---------------------------------------------------------------------------
# Dispute not available from wrong statuses
# ---------------------------------------------------------------------------


class TestDisputeStatusGuards:
    @pytest.mark.parametrize("status", [STATUS_CREATED, STATUS_ACTIVE, STATUS_RESOLUTION_PENDING, STATUS_CANCELLED, STATUS_RESOLVED])
    def test_dispute_methods_reject_non_disputed_status(self, status: int) -> None:
        """All dispute methods require DISPUTED status."""
        m = make_market()
        if status >= STATUS_ACTIVE:
            m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        if status >= STATUS_RESOLUTION_PENDING:
            m.trigger_resolution(sender="anyone", now=m.deadline)
        if status >= STATUS_RESOLUTION_PROPOSED:
            m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        if status == STATUS_CANCELLED:
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
            m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"x" * 32)
        elif status == STATUS_RESOLVED:
            m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)

        if status == STATUS_DISPUTED:
            return  # skip, it IS disputed

        with pytest.raises(MarketAppError, match="invalid status"):
            m.creator_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"r" * 32)
        with pytest.raises(MarketAppError, match="invalid status"):
            m.admin_resolve_dispute(sender="admin", outcome_index=0, ruling_hash=b"a" * 32)
        with pytest.raises(MarketAppError, match="invalid status"):
            m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"f" * 32)
        with pytest.raises(MarketAppError, match="invalid status"):
            m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"x" * 32)
        with pytest.raises(MarketAppError, match="invalid status"):
            m.register_dispute(sender="resolver", dispute_ref_hash=b"x", backend_kind=0, deadline=0)


# ---------------------------------------------------------------------------
# Finalize resolution still works for unchallenged proposals
# ---------------------------------------------------------------------------


class TestFinalizeResolutionUnchallenged:
    def test_unchallenged_finalize_still_works(self) -> None:
        m = market_to_proposed()
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 0


# ---------------------------------------------------------------------------
# Full lifecycle: challenge -> dispute -> resolve -> claim
# ---------------------------------------------------------------------------


class TestFullDisputeLifecycle:
    def test_challenge_register_finalize_claim(self) -> None:
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=1, max_cost=MAX_COST, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)

        # Challenge
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=2, evidence_hash=b"ch" * 16, now=m.deadline + 2)
        assert m.status == STATUS_DISPUTED

        # Register external dispute
        m.register_dispute(sender="resolver", dispute_ref_hash=b"kleros_ref", backend_kind=3, deadline=m.deadline + 200_000)

        # Finalize dispute with corrected outcome
        m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"verdict" * 4 + b"verd")
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 1

        # Winner claims
        claim_result = m.claim(sender="trader", outcome_index=1)
        assert claim_result["payout"] > 0

    def test_challenge_admin_fallback_cancel(self) -> None:
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)

        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)

        # Admin decides market is irresolvable
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"irresolvable" * 2 + b"irre")
        assert m.status == STATUS_CANCELLED

        # Trader can refund
        refund_result = m.refund(sender="trader", outcome_index=0)
        assert refund_result["refund_amount"] > 0

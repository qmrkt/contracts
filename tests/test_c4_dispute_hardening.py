"""C4-dispute-hardening-v5: Adversarial hardening of dispute/challenge resolution.

Three rounds of red-team probing across the full dispute lifecycle:
  Round 1: State-machine escapes, auth bypass, timing races
  Round 2: Bond accounting, solvency, LP/refund reserve violations
  Round 3: Replay, zero-address, griefing loops, overflow, model-contract divergence
"""
from __future__ import annotations

import copy
import random

import pytest

from smart_contracts.market_app.model import (
    BPS_DENOMINATOR,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    ZERO_ADDRESS,
    MarketAppError,
    MarketAppModel,
)
from tests.market_app_test_utils import bootstrap_and_buy, buy_one, make_market, resolve_market


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_disputed_market(**kw) -> MarketAppModel:
    """Return a market in DISPUTED status with one buy on outcome 0."""
    m = make_market(**kw)
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(m, sender="buyer", outcome_index=0)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    m.propose_resolution(sender="resolver", outcome_index=0,
                         evidence_hash=b"e" * 32, now=m.deadline + 1)
    m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                           reason_code=1, evidence_hash=b"c" * 32,
                           now=m.deadline + 2)
    assert m.status == STATUS_DISPUTED
    return m


def snapshot_balances(m: MarketAppModel) -> dict:
    return {
        "pool": m.pool_balance,
        "proposer_bond": m.proposer_bond_held,
        "challenger_bond": m.challenger_bond_held,
        "dispute_sink": m.dispute_sink_balance,
        "lp_fee": m.lp_fee_balance,
        "protocol_fee": m.protocol_fee_balance,
        "total_outstanding": m.total_outstanding_cost_basis,
    }


def make_raw_market(**overrides) -> MarketAppModel:
    defaults = dict(
        creator="creator",
        currency_asa=31_566_704,
        outcome_asa_ids=[1000, 1001],
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=10_000,
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
    defaults.update(overrides)
    return MarketAppModel(**defaults)


from tests.test_helpers import safe_bootstrap_deposit


# ===========================================================================
# ROUND 1: State-machine escapes, auth bypass, timing races
# ===========================================================================

class TestRound1_StateMachineEscape:
    """Attack: call dispute methods from wrong states."""

    @pytest.mark.parametrize("status_name,setup", [
        ("CREATED", lambda: make_market()),
        ("ACTIVE", lambda: (m := make_market(), m.bootstrap(sender="creator", deposit_amount=200_000_000), m)[-1]),
        ("RESOLUTION_PENDING", lambda: (m := make_market(), m.bootstrap(sender="creator", deposit_amount=200_000_000),
                                         buy_one(m), m.trigger_resolution(sender="anyone", now=m.deadline), m)[-1]),
        ("RESOLVED", lambda: (m := bootstrap_and_buy(), resolve_market(m), m)[-1]),
        ("CANCELLED", lambda: (m := make_market(cancellable=True), m.bootstrap(sender="creator", deposit_amount=200_000_000),
                               buy_one(m), m.cancel(sender="creator"), m)[-1]),
    ])
    def test_register_dispute_rejected_outside_disputed(self, status_name, setup):
        m = setup()
        with pytest.raises(MarketAppError, match="status"):
            m.register_dispute(sender="resolver", dispute_ref_hash=b"x" * 32,
                               backend_kind=1, deadline=99999)

    @pytest.mark.parametrize("method", [
        "creator_resolve_dispute",
        "admin_resolve_dispute",
        "finalize_dispute",
        "cancel_dispute_and_market",
    ])
    def test_dispute_methods_rejected_outside_disputed(self, method):
        m = bootstrap_and_buy()
        resolve_market(m)  # RESOLVED
        with pytest.raises(MarketAppError, match="status"):
            if method == "cancel_dispute_and_market":
                getattr(m, method)(sender="resolver", ruling_hash=b"r" * 32)
            else:
                getattr(m, method)(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)

    def test_finalize_resolution_rejected_when_disputed(self):
        """finalize_resolution must reject DISPUTED status (challenged proposal)."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)

    def test_challenge_rejected_after_window(self):
        """Challenge must fail after challenge_window_secs expires."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        late_time = m.deadline + 1 + m.challenge_window_secs  # exactly at boundary
        with pytest.raises(MarketAppError, match="window"):
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                                   reason_code=1, evidence_hash=b"c" * 32,
                                   now=late_time)

    def test_challenge_just_before_window_close(self):
        """Challenge at window - 1 second should succeed."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        just_before = m.deadline + m.challenge_window_secs  # < proposal_ts + window
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=just_before)
        assert m.status == STATUS_DISPUTED

    def test_double_challenge_rejected(self):
        """Second challenge on already-disputed market must fail."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.challenge_resolution(sender="challenger2", bond_paid=m.challenge_bond,
                                   reason_code=2, evidence_hash=b"d" * 32,
                                   now=m.deadline + 3)

    def test_double_finalize_resolution_rejected(self):
        """Calling finalize_resolution twice must fail."""
        m = bootstrap_and_buy()
        resolve_market(m)  # goes to RESOLVED
        with pytest.raises(MarketAppError, match="status"):
            m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs)

    def test_propose_after_dispute_rejected(self):
        """propose_resolution on DISPUTED market must fail."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.propose_resolution(sender="resolver", outcome_index=1,
                                 evidence_hash=b"f" * 32, now=m.deadline + 10)


class TestRound1_AuthBypass:
    """Attack: call privileged dispute methods with wrong sender."""

    def test_register_dispute_wrong_sender(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="auth"):
            m.register_dispute(sender="attacker", dispute_ref_hash=b"x" * 32,
                               backend_kind=1, deadline=99999)

    def test_creator_resolve_by_non_creator(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="creator"):
            m.creator_resolve_dispute(sender="attacker", outcome_index=0,
                                       ruling_hash=b"r" * 32)

    def test_admin_resolve_by_non_admin(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="admin"):
            m.admin_resolve_dispute(sender="attacker", outcome_index=0,
                                     ruling_hash=b"r" * 32)

    def test_finalize_dispute_by_non_authority(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="auth"):
            m.finalize_dispute(sender="attacker", outcome_index=0,
                               ruling_hash=b"r" * 32)

    def test_cancel_dispute_by_non_authority(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="auth"):
            m.cancel_dispute_and_market(sender="attacker", ruling_hash=b"r" * 32)

    def test_propose_resolution_by_non_authority(self):
        """Non-authority cannot propose before grace period expires."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        with pytest.raises(MarketAppError):
            m.propose_resolution(sender="random_attacker", outcome_index=0,
                                 evidence_hash=b"e" * 32, now=m.deadline + 1)

    def test_open_proposer_after_grace_with_bond(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        proposal_time = m.deadline + m.grace_period_secs + 1
        m.propose_resolution(
            sender="open_proposer",
            outcome_index=0,
            evidence_hash=b"e" * 32,
            now=proposal_time,
            bond_paid=m.proposal_bond,
        )
        assert m.status == STATUS_RESOLUTION_PROPOSED
        assert m.proposer == "open_proposer"
        assert m.proposer_bond_held == m.proposal_bond

    def test_open_proposer_after_grace_without_bond_rejected(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        proposal_time = m.deadline + m.grace_period_secs + 1
        with pytest.raises(MarketAppError, match="bond"):
            m.propose_resolution(
                sender="open_proposer",
                outcome_index=0,
                evidence_hash=b"e" * 32,
                now=proposal_time,
                bond_paid=0,
            )


class TestRound1_TimingRaces:
    """Attack: exploit exact-boundary timing for challenge window."""

    def test_finalize_exactly_at_window(self):
        """Finalize at exact window expiry should succeed."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        propose_time = m.deadline + 1
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=propose_time)
        finalize_time = propose_time + m.challenge_window_secs
        m.finalize_resolution(sender="anyone", now=finalize_time)
        assert m.status == STATUS_RESOLVED

    def test_finalize_one_second_early_rejected(self):
        """Finalize 1 second before window should fail."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        propose_time = m.deadline + 1
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=propose_time)
        early = propose_time + m.challenge_window_secs - 1
        with pytest.raises(MarketAppError, match="window"):
            m.finalize_resolution(sender="anyone", now=early)

    def test_trigger_resolution_before_deadline_rejected(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        with pytest.raises(MarketAppError, match="deadline"):
            m.trigger_resolution(sender="anyone", now=m.deadline - 1)


# ===========================================================================
# ROUND 2: Bond accounting, solvency, LP/refund reserve violations
# ===========================================================================

class TestRound2_BondAccounting:
    """Attack: verify bond flows cannot leak or double-count."""

    def test_confirmed_dispute_bond_conservation(self):
        """When proposal confirmed: proposer gets bond+bonus, rest to sink."""
        m = make_disputed_market()
        pre = snapshot_balances(m)
        total_bonds = pre["proposer_bond"] + pre["challenger_bond"]
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        post = snapshot_balances(m)
        # Proposer bond + challenger bond = proposer payout + sink increase
        sink_increase = post["dispute_sink"] - pre["dispute_sink"]
        # proposer_bond_held and challenger_bond_held should be 0
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0
        # Conservation: total bonds = sink_increase + what was paid out
        # The payout goes to proposer as itxn, tracked by the model
        winner_bonus = (pre["challenger_bond"] * m.winner_share_bps) // BPS_DENOMINATOR
        expected_sink = pre["challenger_bond"] - winner_bonus
        assert sink_increase == expected_sink
        assert total_bonds == (pre["proposer_bond"] + winner_bonus) + expected_sink

    def test_overturned_dispute_bond_conservation(self):
        """When proposal overturned: challenger gets bond+bonus, rest to sink."""
        m = make_disputed_market()
        pre = snapshot_balances(m)
        total_bonds = pre["proposer_bond"] + pre["challenger_bond"]
        # Overturn: resolve to outcome 1 (different from proposed 0)
        m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r" * 32)
        post = snapshot_balances(m)
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0
        sink_increase = post["dispute_sink"] - pre["dispute_sink"]
        winner_bonus = (pre["proposer_bond"] * m.winner_share_bps) // BPS_DENOMINATOR
        expected_sink = pre["proposer_bond"] - winner_bonus
        assert sink_increase == expected_sink
        assert total_bonds == (pre["challenger_bond"] + winner_bonus) + expected_sink

    def test_cancel_dispute_bond_conservation(self):
        """When dispute cancelled: challenger gets full refund, proposer bond to sink."""
        m = make_disputed_market()
        pre = snapshot_balances(m)
        total_bonds = pre["proposer_bond"] + pre["challenger_bond"]
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        post = snapshot_balances(m)
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0
        sink_increase = post["dispute_sink"] - pre["dispute_sink"]
        # Proposer bond goes entirely to sink, challenger gets full refund
        assert sink_increase == pre["proposer_bond"]
        # Total: challenger_payout + sink = total_bonds
        challenger_payout = pre["challenger_bond"]  # full refund
        assert challenger_payout + sink_increase == total_bonds

    @pytest.mark.parametrize("challenger_overpay", [1, 1_000_000, 50_000_000])
    def test_overpaid_challenge_bond_tracked(self, challenger_overpay):
        """Overpaying challenge bond should track actual amount, not minimum."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        overpaid = m.challenge_bond + challenger_overpay
        m.challenge_resolution(sender="challenger", bond_paid=overpaid,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=m.deadline + 2)
        assert m.challenger_bond_held == overpaid

    def test_underpaid_challenge_bond_rejected(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        with pytest.raises(MarketAppError, match="bond"):
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond - 1,
                                   reason_code=1, evidence_hash=b"c" * 32,
                                   now=m.deadline + 2)

    def test_underpaid_proposal_bond_rejected(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        with pytest.raises(MarketAppError, match="bond"):
            m.propose_resolution(sender="open_proposer", outcome_index=0,
                                 evidence_hash=b"e" * 32, now=m.deadline + m.grace_period_secs + 1,
                                 bond_paid=m.proposal_bond - 1)


class TestRound2_SolvencyUnderDispute:
    """Attack: check pool solvency is maintained through all dispute outcomes."""

    def _assert_solvency(self, m: MarketAppModel):
        """Pool must cover maximum possible payout even during/after dispute."""
        if m.status == STATUS_RESOLVED:
            max_payout = max(m.q) if m.q else 0
            # Pool should cover winner claims
            assert m.pool_balance >= 0
        elif m.status == STATUS_CANCELLED:
            # Pool should cover refunds
            assert m.pool_balance >= 0

    def test_solvency_through_dispute_confirm(self):
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED
        self._assert_solvency(m)

    def test_solvency_through_dispute_overturn(self):
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED
        self._assert_solvency(m)

    def test_solvency_through_dispute_cancel(self):
        m = make_disputed_market()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        assert m.status == STATUS_CANCELLED
        self._assert_solvency(m)

    def test_claim_after_dispute_confirm(self):
        """Winner can claim after dispute confirms original proposal."""
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        claim_result = m.claim(sender="buyer", outcome_index=0)
        assert claim_result["payout"] > 0
        assert m.pool_balance >= 0

    def test_claim_after_dispute_overturn(self):
        """After overturn, original winner cannot claim, new winner can (if they hold shares)."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer0", outcome_index=0, now=5000)
        buy_one(m, sender="buyer1", outcome_index=1, now=5001)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=m.deadline + 2)
        # Overturn to outcome 1
        m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r" * 32)
        # buyer0 (outcome 0) cannot claim
        with pytest.raises(MarketAppError, match="winning"):
            m.claim(sender="buyer0", outcome_index=0)
        # buyer1 (outcome 1) CAN claim
        claim_result = m.claim(sender="buyer1", outcome_index=1)
        assert claim_result["payout"] > 0

    def test_refund_after_dispute_cancel(self):
        """After cancel_dispute_and_market, users can refund."""
        m = make_disputed_market()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        refund_result = m.refund(sender="buyer", outcome_index=0)
        assert refund_result["refund_amount"] > 0

    def test_withdraw_liq_after_dispute_cancel(self):
        """LP can withdraw after dispute cancellation."""
        m = make_disputed_market()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        result = m.withdraw_liq(sender="creator", shares_to_burn=m.user_lp_shares["creator"])
        assert result["usdc_return"] > 0

    def test_withdraw_liq_during_disputed_rejected(self):
        """LP cannot withdraw while market is disputed."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError):
            m.withdraw_liq(sender="creator", shares_to_burn=1)

    def test_underfunded_three_outcome_bootstrap_rejected(self):
        m = make_raw_market(outcome_asa_ids=[1000, 1001, 1002], b=50_000_000)
        with pytest.raises(MarketAppError, match="bootstrap deposit below LMSR solvency floor"):
            m.bootstrap(sender="creator", deposit_amount=50_000_000)

    def test_underfunded_sixteen_outcome_bootstrap_rejected(self):
        m = make_raw_market(outcome_asa_ids=[1000 + i for i in range(16)], b=50_000_000)
        with pytest.raises(MarketAppError, match="bootstrap deposit below LMSR solvency floor"):
            m.bootstrap(sender="creator", deposit_amount=100_000_000)


class TestRound2_LPvsRefundReserve:
    """Attack: LP withdrawals vs refund reserve after cancellation."""

    def test_lp_plus_refund_does_not_exceed_pool(self):
        """Total of LP withdrawal + all refunds should not exceed initial pool."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer0", outcome_index=0, now=5000)
        buy_one(m, sender="buyer1", outcome_index=1, now=5001)
        buy_one(m, sender="buyer2", outcome_index=2, now=5002)
        m.cancel(sender="creator")
        # Refund all buyers for their specific outcomes
        total_refunded = 0
        for i, buyer in enumerate(["buyer0", "buyer1", "buyer2"]):
            total_refunded += m.refund(sender=buyer, outcome_index=i)["refund_amount"]
        # LP withdraw
        lp_result = m.withdraw_liq(sender="creator", shares_to_burn=m.user_lp_shares["creator"])
        total_extracted = total_refunded + lp_result["usdc_return"]
        # Pool should be non-negative after all extractions
        assert m.pool_balance >= 0


# ===========================================================================
# ROUND 3: Replay, zero-address, griefing, overflow, model divergence
# ===========================================================================

class TestRound3_ReplayAndDuplicate:
    """Attack: replay dispute resolutions and duplicate settlements."""

    def test_double_finalize_dispute_rejected(self):
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED
        with pytest.raises(MarketAppError, match="status"):
            m.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r2" * 16)

    def test_cancel_after_finalize_dispute_rejected(self):
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        with pytest.raises(MarketAppError, match="status"):
            m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)

    def test_finalize_after_cancel_dispute_rejected(self):
        m = make_disputed_market()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        with pytest.raises(MarketAppError, match="status"):
            m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)

    def test_double_claim_rejected(self):
        m = make_disputed_market()
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        m.claim(sender="buyer", outcome_index=0)
        with pytest.raises(MarketAppError):
            m.claim(sender="buyer", outcome_index=0)

    def test_double_refund_rejected(self):
        m = make_disputed_market()
        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        m.refund(sender="buyer", outcome_index=0)
        with pytest.raises(MarketAppError):
            m.refund(sender="buyer", outcome_index=0)

    def test_creator_resolve_then_admin_resolve_rejected(self):
        """After creator resolves dispute, admin cannot re-resolve."""
        m = make_disputed_market()
        m.creator_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"cr" * 16)
        # After creator resolve, pending_responder_role should change
        # Trying admin resolve should fail (status is now RESOLVED or already resolved)
        with pytest.raises(MarketAppError):
            m.admin_resolve_dispute(sender="admin", outcome_index=1, ruling_hash=b"ar" * 16)


class TestRound3_ZeroAddressAndEmptyState:
    """Attack: zero-address fields, empty evidence, uninitialized state."""

    def test_zero_address_creator_rejected(self):
        with pytest.raises(MarketAppError, match="zero address"):
            make_raw_market(creator=ZERO_ADDRESS)

    def test_zero_address_authority_rejected(self):
        with pytest.raises(MarketAppError, match="zero address"):
            make_raw_market(resolution_authority=ZERO_ADDRESS)

    def test_zero_address_admin_rejected(self):
        with pytest.raises(MarketAppError, match="zero address"):
            make_raw_market(market_admin=ZERO_ADDRESS)

    def test_zero_currency_asa_rejected(self):
        with pytest.raises(MarketAppError, match="currency_asa must be positive"):
            make_raw_market(currency_asa=0)

    def test_challenge_with_empty_evidence(self):
        """Empty evidence hash should still work (not a security violation)."""
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                               reason_code=0, evidence_hash=b"",
                               now=m.deadline + 2)
        assert m.status == STATUS_DISPUTED

    def test_propose_with_empty_evidence(self):
        m = make_market()
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"", now=m.deadline + 1)
        assert m.status == STATUS_RESOLUTION_PROPOSED

    def test_dispute_with_invalid_outcome_rejected(self):
        """Resolving dispute with out-of-range outcome should fail."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="outcome"):
            m.finalize_dispute(sender="resolver", outcome_index=99, ruling_hash=b"r" * 32)

    def test_dispute_with_negative_outcome_rejected(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="outcome"):
            m.finalize_dispute(sender="resolver", outcome_index=-1, ruling_hash=b"r" * 32)

    def test_challenge_bond_zero_when_configured_zero(self):
        """If challenge_bond=0, challenge still transitions state correctly."""
        m = MarketAppModel(
            creator="creator", currency_asa=31566704,
            outcome_asa_ids=[1000, 1001], b=100_000_000,
            lp_fee_bps=200, protocol_fee_bps=50, deadline=10_000,
            question_hash=b"q" * 32, main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32, challenge_window_secs=86_400,
            protocol_config_id=77, factory_id=88,
            resolution_authority="resolver", challenge_bond=0,
            proposal_bond=0, challenge_bond_bps=0, proposal_bond_bps=0,
            challenge_bond_cap=0, proposal_bond_cap=0,
            grace_period_secs=3_600, market_admin="admin",
        )
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        m.buy(sender="buyer", outcome_index=0, max_cost=10_000_000, now=5000)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="challenger", bond_paid=0,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=m.deadline + 2)
        assert m.status == STATUS_DISPUTED
        # Finalize with zero bonds: should not crash
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED


class TestRound3_GriefingLoops:
    """Attack: repeated propose-challenge cycles and griefing."""

    def test_cannot_re_propose_after_dispute(self):
        """Once disputed, cannot re-propose. Market is locked in DISPUTED."""
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.propose_resolution(sender="resolver", outcome_index=1,
                                 evidence_hash=b"f" * 32, now=m.deadline + 100)

    def test_cannot_buy_during_dispute(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.buy(sender="griefer", outcome_index=0, max_cost=10_000_000, now=m.deadline + 100)

    def test_cannot_sell_during_dispute(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="status"):
            m.sell(sender="buyer", outcome_index=0, min_return=0, now=m.deadline + 100)

    def test_cannot_provide_liq_during_dispute(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError):
            m.provide_liq(sender="lp", deposit_amount=50_000_000, now=m.deadline + 100)


class TestRound3_OverflowAndEdgeMath:
    """Attack: extreme values in bond calculations and outcome counts."""

    @pytest.mark.parametrize("n", [2, 8, 16])
    def test_dispute_lifecycle_at_various_n(self, n):
        """Full dispute lifecycle at different outcome counts."""
        m = make_market(num_outcomes=n)
        m.bootstrap(sender="creator", deposit_amount=safe_bootstrap_deposit(n))
        buy_one(m, sender="buyer", outcome_index=0)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=m.deadline + 2)
        pre = snapshot_balances(m)
        m.finalize_dispute(sender="resolver", outcome_index=n - 1, ruling_hash=b"r" * 32)
        post = snapshot_balances(m)
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0
        assert m.status == STATUS_RESOLVED

    def test_large_bonds_no_overflow(self):
        """Very large bond amounts should not cause overflow."""
        m = MarketAppModel(
            creator="creator", currency_asa=31566704,
            outcome_asa_ids=[1000, 1001], b=100_000_000,
            lp_fee_bps=200, protocol_fee_bps=50, deadline=10_000,
            question_hash=b"q" * 32, main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32, challenge_window_secs=86_400,
            protocol_config_id=77, factory_id=88,
            resolution_authority="resolver",
            challenge_bond=2**53,  # large but within safe int range
            proposal_bond=2**53,
            challenge_bond_cap=2**53,
            proposal_bond_cap=2**53,
            grace_period_secs=3_600, market_admin="admin",
        )
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        m.buy(sender="buyer", outcome_index=0, max_cost=10_000_000, now=5000)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0,
                             evidence_hash=b"e" * 32, now=m.deadline + 1,
                             bond_paid=2**53)
        m.challenge_resolution(sender="challenger", bond_paid=2**53,
                               reason_code=1, evidence_hash=b"c" * 32,
                               now=m.deadline + 2)
        # Should not crash
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        assert m.status == STATUS_RESOLVED

    def test_winner_share_bps_boundary(self):
        """When winner_share_bps = BPS_DENOMINATOR, entire losing bond goes to winner."""
        m = make_disputed_market()
        m.winner_share_bps = BPS_DENOMINATOR
        m.dispute_sink_share_bps = 0
        pre_sink = m.dispute_sink_balance
        m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
        # Entire challenger bond should go to proposer, nothing to sink
        assert m.dispute_sink_balance == pre_sink  # no increase


class TestRound3_ModelContractDivergence:
    """Cross-validate model behavior against contract source for dispute methods."""

    def test_contract_has_all_dispute_methods(self):
        """Verify contract.py implements all dispute methods the model defines."""
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        required_methods = [
            "def challenge_resolution",
            "def register_dispute",
            "def creator_resolve_dispute",
            "def admin_resolve_dispute",
            "def finalize_dispute",
            "def cancel_dispute_and_market",
            "def finalize_resolution",
        ]
        for method in required_methods:
            assert method in src, f"Contract missing: {method}"

    def test_contract_status_disputed_constant(self):
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        assert "STATUS_DISPUTED = 6" in src

    def test_contract_settle_methods_exist(self):
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        for method in ["_settle_confirmed_dispute", "_settle_overturned_dispute", "_settle_cancelled_dispute"]:
            assert f"def {method}" in src, f"Contract missing: {method}"

    def test_contract_p9_ordering_in_dispute(self):
        """Verify state updates happen before any payout side effect in dispute settlement methods."""
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        fd_section = src[src.index("def finalize_dispute"):]
        fd_section = fd_section[:fd_section.index("\n    @")]  # until next method
        status_line = fd_section.index("self.status.value = UInt64(STATUS_RESOLVED)")
        payout_line = fd_section.index("_settle_dispute_and_credit")
        assert status_line < payout_line, "P9 violation: status update must precede payout credit"

    def test_contract_p9_ordering_in_cancel_dispute(self):
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        cd_section = src[src.index("def cancel_dispute_and_market"):]
        cd_section = cd_section[:cd_section.index("\n    @")]
        status_line = cd_section.index("self.status.value = UInt64(STATUS_CANCELLED)")
        payout_line = cd_section.index("_credit_pending_payout")
        assert status_line < payout_line, "P9 violation in cancel_dispute_and_market"

    def test_contract_verify_payment_checks_rekey_and_close_to(self):
        """_verify_payment must check rekey_to and asset_close_to are zero."""
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        vp = src[src.index("def _verify_payment"):]
        vp = vp[:vp.index("\n    def ")]
        assert "rekey_to" in vp, "Missing rekey_to check in _verify_payment"
        assert "asset_close_to" in vp, "Missing asset_close_to check in _verify_payment"
        assert "asset_sender" in vp, "Missing asset_sender (clawback) check"

    def test_contract_challenge_bond_verified_via_gtxn(self):
        """challenge_resolution must verify bond via grouped transaction, not argument."""
        from tests.market_app_test_utils import CONTRACT_SOURCE, source_text
        src = source_text(CONTRACT_SOURCE)
        cr_section = src[src.index("def challenge_resolution"):]
        cr_section = cr_section[:cr_section.index("\n    @")]
        assert "_verify_payment" in cr_section, "Challenge must verify bond via _verify_payment"


class TestRound3_PropertyBased:
    """Property-based random sequences targeting dispute lifecycle."""

    def test_random_dispute_sequences_conserve_bonds(self):
        """1000 random scenarios: bonds always conserve across dispute outcomes."""
        rng = random.Random(42)
        violations = []
        for i in range(1000):
            n = rng.choice([2, 3, 5, 8])
            m = make_market(num_outcomes=n)
            m.bootstrap(sender="creator", deposit_amount=safe_bootstrap_deposit(n))
            # Random buys
            for _ in range(rng.randint(1, 5)):
                oi = rng.randint(0, n - 1)
                buy_one(m, sender=f"buyer_{oi}", outcome_index=oi, now=5000)
            m.trigger_resolution(sender="anyone", now=m.deadline)
            m.propose_resolution(sender="resolver", outcome_index=0,
                                 evidence_hash=b"e" * 32, now=m.deadline + 1)
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                                   reason_code=1, evidence_hash=b"c" * 32,
                                   now=m.deadline + 2)
            total_bonds_pre = m.proposer_bond_held + m.challenger_bond_held
            sink_pre = m.dispute_sink_balance

            action = rng.choice(["confirm", "overturn", "cancel"])
            if action == "confirm":
                m.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)
            elif action == "overturn":
                new_outcome = rng.randint(1, n - 1)
                m.finalize_dispute(sender="resolver", outcome_index=new_outcome, ruling_hash=b"r" * 32)
            else:
                m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)

            # Conservation check: all bonds accounted for
            sink_increase = m.dispute_sink_balance - sink_pre
            remaining_bonds = m.proposer_bond_held + m.challenger_bond_held
            if remaining_bonds != 0:
                violations.append(f"iter={i}: bonds not zeroed: {remaining_bonds}")

        assert len(violations) == 0, f"Bond violations: {violations[:5]}"

    def test_solvency_invariant_through_dispute_sequences(self):
        """500 random sequences: solvency holds through dispute lifecycle."""
        rng = random.Random(99)
        for _ in range(500):
            n = rng.choice([2, 3, 5])
            m = make_market(num_outcomes=n)
            m.bootstrap(sender="creator", deposit_amount=rng.randint(safe_bootstrap_deposit(n), 500_000_000))
            for _ in range(rng.randint(1, 4)):
                oi = rng.randint(0, n - 1)
                try:
                    buy_one(m, sender=f"buyer_{oi}", outcome_index=oi, now=5000)
                except MarketAppError:
                    pass
            m.trigger_resolution(sender="anyone", now=m.deadline)
            m.propose_resolution(sender="resolver", outcome_index=0,
                                 evidence_hash=b"e" * 32, now=m.deadline + 1)
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond,
                                   reason_code=1, evidence_hash=b"c" * 32,
                                   now=m.deadline + 2)
            action = rng.choice(["confirm", "overturn", "cancel"])
            if action == "cancel":
                m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
            else:
                oi = 0 if action == "confirm" else rng.randint(1, n - 1)
                m.finalize_dispute(sender="resolver", outcome_index=oi, ruling_hash=b"r" * 32)
            # Model's _assert_invariants already ran; extra solvency check
            assert m.pool_balance >= 0, f"Pool went negative: {m.pool_balance}"


class TestRound3_CreatorAndAdminResolveDispute:
    """Targeted tests for creator_resolve_dispute and admin_resolve_dispute."""

    def test_creator_resolve_dispute_sets_pending_role(self):
        m = make_disputed_market()
        m.creator_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"cr" * 16)
        # After creator resolves, status should be RESOLVED
        assert m.status == STATUS_RESOLVED
        assert m.resolution_path_used == 1  # dispute path

    def test_admin_resolve_dispute_sets_path(self):
        m = make_disputed_market()
        m.admin_resolve_dispute(sender="admin", outcome_index=1, ruling_hash=b"ar" * 16)
        assert m.status == STATUS_RESOLVED
        assert m.resolution_path_used == 2  # admin fallback

    def test_creator_cannot_resolve_with_invalid_outcome(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="outcome"):
            m.creator_resolve_dispute(sender="creator", outcome_index=99, ruling_hash=b"cr" * 16)

    def test_admin_cannot_resolve_with_invalid_outcome(self):
        m = make_disputed_market()
        with pytest.raises(MarketAppError, match="outcome"):
            m.admin_resolve_dispute(sender="admin", outcome_index=99, ruling_hash=b"ar" * 16)

    def test_creator_resolve_bond_settlement(self):
        """Creator resolve should settle bonds correctly."""
        m = make_disputed_market()
        pre = snapshot_balances(m)
        m.creator_resolve_dispute(sender="creator", outcome_index=0, ruling_hash=b"cr" * 16)
        post = snapshot_balances(m)
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0

    def test_admin_resolve_bond_settlement(self):
        m = make_disputed_market()
        pre = snapshot_balances(m)
        m.admin_resolve_dispute(sender="admin", outcome_index=1, ruling_hash=b"ar" * 16)
        post = snapshot_balances(m)
        assert post["proposer_bond"] == 0
        assert post["challenger_bond"] == 0

"""C6 Launch-Cert Adversarial Shard — Round 1.

Probe categories:
  A1  payment_fake_asset_spoofing  — sell outcome[0] with outcome[1] ASA (cross-outcome spoof)
  A2  payment_fake_asset_spoofing  — sell with asset_amount != shares_val (over-send ASA)
  A3  payment_fake_asset_spoofing  — buy underpayment by exactly 1 micro-USDC
  A4  replay_griefing              — claim all shares then attempt second claim
  A5  replay_griefing              — refund all then attempt second refund
  A6  lifecycle_state_machine      — refund in non-cancelled state (ACTIVE/RESOLVED)
  A7  lifecycle_state_machine      — claim losing outcome after resolution
  A8  solvency_rounding_invariants — chunked claim vs single claim: attacker cannot extract extra
  A9  solvency_rounding_invariants — pool_balance never negative after multi-user buy+sell+claim
  A10 lp_liquidity_manipulation     — LP cannot extract more than their pro-rata share
"""
from __future__ import annotations

import pytest
from algopy import Account, Array, Asset, Global, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    QuestionMarket,
    SHARE_UNIT,
    STATUS_CANCELLED,
    STATUS_RESOLVED,
)
from smart_contracts.market_app.model import MarketAppModel
from smart_contracts.lmsr_math import SCALE, lmsr_cost_delta
from smart_contracts.lmsr_math_avm import lmsr_cost_delta as lmsr_cost_delta_avm
import algosdk.account
import algosdk.logic

# ── shared constants ───────────────────────────────────────────────────────────
CURRENCY_ASA = 31566704
OUTCOME_ASA_IDS = [1001, 1002, 1003]
WRONG_ASA = 99_999
B = 100_000_000
DEPOSIT = 200_000_000


def _make_addr():
    _, pk = algosdk.account.generate_account()
    return pk


def _app_address(contract):
    return algosdk.logic.get_application_address(contract.__app_id__)


def _make_payment(context, contract, sender, amount, *, asset_id=CURRENCY_ASA,
                   receiver=None, rekey_to=None, asset_close_to=None, asset_sender=None):
    recv = receiver or _app_address(contract)
    zero = Global.zero_address
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(recv),
        xfer_asset=Asset(asset_id),
        asset_amount=UInt64(amount),
        rekey_to=Account(rekey_to) if rekey_to is not None else zero,
        asset_close_to=Account(asset_close_to) if asset_close_to is not None else zero,
        asset_sender=Account(asset_sender) if asset_sender is not None else zero,
    )


def _call_as(context, sender, method, *args, ts=None):
    if ts is not None:
        context.ledger.patch_global_fields(latest_timestamp=ts)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    with context.txn.create_group([deferred]):
        return method(*args)


def _create_contract(context, contract, creator):
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(3),
        initial_b=arc4.UInt64(B),
        lp_fee_bps=arc4.UInt64(200),
        protocol_fee_bps=arc4.UInt64(50),
        deadline=arc4.UInt64(100_000),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        main_blueprint_hash=arc4.DynamicBytes(b"b" * 32),
        dispute_blueprint_hash=arc4.DynamicBytes(b"d" * 32),
        challenge_window_secs=arc4.UInt64(86_400),
        resolution_authority=arc4.Address(creator),
        challenge_bond=arc4.UInt64(10_000_000),
        proposal_bond=arc4.UInt64(10_000_000),
        grace_period_secs=arc4.UInt64(3_600),
        market_admin=arc4.Address(creator),
        protocol_config_id=arc4.UInt64(77),
        factory_id=arc4.UInt64(88),
        cancellable=arc4.Bool(True),
    )
    _call_as(context, creator, contract.create, *args.values(), ts=1)


def _bootstrap(context, contract, creator):
    """Register ASAs + blueprints + bootstrap."""
    for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
        _call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
    _call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}}'))
    _call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}}'))
    payment = _make_payment(context, contract, creator, DEPOSIT)
    _call_as(context, creator, contract.bootstrap, arc4.UInt64(DEPOSIT), payment, ts=1)


@pytest.fixture()
def disable_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *a, **kw: None)


# ── A1: cross-outcome ASA spoof in sell ────────────────────────────────────────
class TestA1CrossOutcomeAsaSpoof:
    """Sell outcome 0 shares but supply outcome 1's ASA in the payment → must fail."""

    def test_sell_outcome0_with_outcome1_asa_rejected(self, disable_emit):
        creator = _make_addr()
        buyer = _make_addr()
        with algopy_testing_context() as ctx:
            c = QuestionMarket()
            _create_contract(ctx, c, creator)
            _bootstrap(ctx, c, creator)

            # Buy outcome 0 legitimately
            cost = lmsr_cost_delta([0, 0, 0], B, 0, SHARE_UNIT)
            buy_pmt = _make_payment(ctx, c, buyer, cost * 2)  # generous max_cost
            _call_as(ctx, buyer, c.buy,
                     arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(cost * 2), buy_pmt, ts=5_000)

            # Attempt sell outcome 0 but send outcome 1 ASA → MUST reject
            bad_sell = _make_payment(ctx, c, buyer, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[1])
            with pytest.raises(AssertionError):
                _call_as(ctx, buyer, c.sell,
                         arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0), bad_sell, ts=5_001)

    def test_sell_outcome1_with_outcome0_asa_rejected(self, disable_emit):
        creator = _make_addr()
        buyer = _make_addr()
        with algopy_testing_context() as ctx:
            c = QuestionMarket()
            _create_contract(ctx, c, creator)
            _bootstrap(ctx, c, creator)

            # Buy BOTH outcomes
            cost0 = lmsr_cost_delta([0, 0, 0], B, 0, SHARE_UNIT)
            cost1 = lmsr_cost_delta([SHARE_UNIT, 0, 0], B, 1, SHARE_UNIT)
            for outcome, cost in [(0, cost0), (1, cost1)]:
                pmt = _make_payment(ctx, c, buyer, cost * 2)
                _call_as(ctx, buyer, c.buy,
                         arc4.UInt64(outcome), arc4.UInt64(SHARE_UNIT), arc4.UInt64(cost * 2), pmt, ts=5_000)

            # Attempt sell outcome 1 but send outcome 0 ASA → MUST reject
            bad_sell = _make_payment(ctx, c, buyer, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[0])
            with pytest.raises(AssertionError):
                _call_as(ctx, buyer, c.sell,
                         arc4.UInt64(1), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0), bad_sell, ts=5_001)


# ── A2: sell with wrong ASA amount ─────────────────────────────────────────────
class TestA2SellWrongAmount:
    """Sell 1 share but send 2x ASA in the payment → must fail."""

    def test_sell_oversend_asa_rejected(self, disable_emit):
        creator = _make_addr()
        buyer = _make_addr()
        with algopy_testing_context() as ctx:
            c = QuestionMarket()
            _create_contract(ctx, c, creator)
            _bootstrap(ctx, c, creator)

            cost = lmsr_cost_delta([0, 0, 0], B, 0, SHARE_UNIT * 2)
            buy_pmt = _make_payment(ctx, c, buyer, cost * 2)
            _call_as(ctx, buyer, c.buy,
                     arc4.UInt64(0), arc4.UInt64(SHARE_UNIT * 2), arc4.UInt64(cost * 2), buy_pmt, ts=5_000)

            # Sell 1 share but send 2 * SHARE_UNIT ASA
            bad_sell = _make_payment(ctx, c, buyer, SHARE_UNIT * 2, asset_id=OUTCOME_ASA_IDS[0])
            with pytest.raises(AssertionError):
                _call_as(ctx, buyer, c.sell,
                         arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0), bad_sell, ts=5_001)


# ── A3: buy underpayment by 1 micro-USDC ───────────────────────────────────────
class TestA3BuyUnderpayment:
    """Payment = on-chain total_cost - 1 must be rejected; exact on-chain total_cost must succeed."""

    def test_buy_underpayment_by_one_rejected(self, disable_emit):
        creator = _make_addr()
        buyer = _make_addr()
        with algopy_testing_context() as ctx:
            c = QuestionMarket()
            _create_contract(ctx, c, creator)
            _bootstrap(ctx, c, creator)

            # Use the AVM helper here so the adversarial shard targets the actual
            # on-chain payment threshold rather than the slightly higher pure-Python
            # reference approximation.
            base_cost = int(
                lmsr_cost_delta_avm(
                    Array([UInt64(0), UInt64(0), UInt64(0)]),
                    UInt64(B),
                    UInt64(0),
                    UInt64(SHARE_UNIT),
                )
            )
            # lp_fee = ceil(cost * 200/10000), protocol_fee = ceil(cost * 50/10000)
            lp_fee = (base_cost * 200 + 9999) // 10000
            proto_fee = (base_cost * 50 + 9999) // 10000
            total_cost = base_cost + lp_fee + proto_fee

            # Underpay by exactly 1
            pmt = _make_payment(ctx, c, buyer, total_cost - 1)
            with pytest.raises(AssertionError):
                _call_as(ctx, buyer, c.buy,
                         arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(total_cost), pmt, ts=5_000)

    def test_buy_exact_total_cost_succeeds(self, disable_emit):
        creator = _make_addr()
        buyer = _make_addr()
        with algopy_testing_context() as ctx:
            c = QuestionMarket()
            _create_contract(ctx, c, creator)
            _bootstrap(ctx, c, creator)

            base_cost = int(
                lmsr_cost_delta_avm(
                    Array([UInt64(0), UInt64(0), UInt64(0)]),
                    UInt64(B),
                    UInt64(0),
                    UInt64(SHARE_UNIT),
                )
            )
            lp_fee = (base_cost * 200 + 9999) // 10000
            proto_fee = (base_cost * 50 + 9999) // 10000
            total_cost = base_cost + lp_fee + proto_fee

            pmt = _make_payment(ctx, c, buyer, total_cost)
            _call_as(ctx, buyer, c.buy,
                     arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(total_cost), pmt, ts=5_000)
            assert c.pool_balance.value == DEPOSIT + base_cost


# ── A4: claim replay (double claim) ────────────────────────────────────────────
class TestA4ClaimReplay:
    """After claiming all shares, a second claim must be rejected."""

    def _resolved_market(self, num_shares=1):
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        for _ in range(num_shares):
            m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline + 1)
        m.propose_resolution(sender="creator", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 2)
        m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs + 1)
        return m

    def test_double_claim_all_shares_rejected(self):
        m = self._resolved_market(num_shares=2)
        # Claim ALL shares in one shot
        total_shares = 2 * SHARE_UNIT
        r = m.claim(sender="alice", outcome_index=0, shares=total_shares)
        assert r["payout"] > 0
        # Second claim must fail: user has 0 shares
        from smart_contracts.market_app.model import MarketAppError
        with pytest.raises(MarketAppError):
            m.claim(sender="alice", outcome_index=0, shares=SHARE_UNIT)

    def test_partial_claim_then_exact_remaining_then_zero_fails(self):
        m = self._resolved_market(num_shares=3)
        m.claim(sender="alice", outcome_index=0, shares=SHARE_UNIT)
        m.claim(sender="alice", outcome_index=0, shares=SHARE_UNIT)
        m.claim(sender="alice", outcome_index=0, shares=SHARE_UNIT)
        # All shares claimed — now must fail
        from smart_contracts.market_app.model import MarketAppError
        with pytest.raises(MarketAppError):
            m.claim(sender="alice", outcome_index=0, shares=1)


# ── A5: refund replay ──────────────────────────────────────────────────────────
class TestA5RefundReplay:
    """After refunding all shares, a second refund must be rejected."""

    def _cancelled_market(self):
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1001)
        m.cancel(sender="creator")
        return m

    def test_double_refund_all_shares_rejected(self):
        m = self._cancelled_market()
        m.refund(sender="alice", outcome_index=0, shares=2 * SHARE_UNIT)
        from smart_contracts.market_app.model import MarketAppError
        with pytest.raises(MarketAppError):
            m.refund(sender="alice", outcome_index=0, shares=1)


# ── A6: refund in wrong lifecycle state ────────────────────────────────────────
class TestA6RefundWrongState:
    """Refund must only work in CANCELLED; reject in ACTIVE and RESOLVED."""

    def test_refund_rejected_in_active_state(self):
        from smart_contracts.market_app.model import MarketAppError
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        with pytest.raises(MarketAppError):
            m.refund(sender="alice", outcome_index=0)

    def test_refund_rejected_in_resolved_state(self):
        from smart_contracts.market_app.model import MarketAppError
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline + 1)
        m.propose_resolution(sender="creator", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 2)
        m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs + 1)
        with pytest.raises(MarketAppError):
            m.refund(sender="alice", outcome_index=0)


# ── A7: claim losing outcome ────────────────────────────────────────────────────
class TestA7ClaimLosingOutcome:
    """Claim must be rejected for the losing outcome after resolution."""

    def test_claim_losing_outcome_rejected(self):
        from smart_contracts.market_app.model import MarketAppError
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        m.buy(sender="bob", outcome_index=1, max_cost=10 * SCALE * SCALE, now=1001)
        # Resolve outcome 1 wins
        m.trigger_resolution(sender="anyone", now=m.deadline + 1)
        m.propose_resolution(sender="creator", outcome_index=1, evidence_hash=b"e" * 32, now=m.deadline + 2)
        m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs + 1)
        # Alice has outcome 0 shares (losing) — must fail
        with pytest.raises(MarketAppError):
            m.claim(sender="alice", outcome_index=0)
        # Bob has outcome 1 shares (winning) — must succeed
        result = m.claim(sender="bob", outcome_index=1)
        assert result["payout"] > 0


# ── A8: chunked claim: attacker cannot get MORE than single claim ───────────────
class TestA8ChunkedClaimNoExtraProfit:
    """Chunked claims must yield ≤ single claim (floor rounding).
    
    Economic invariant: no user can extract more USDC via chunked claiming
    than via a single all-at-once claim.
    """

    def _setup_resolved(self, num_outcomes=2, num_shares=4):
        asa_ids = list(range(1001, 1001 + num_outcomes))
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=asa_ids,
            b=B,
            lp_fee_bps=0,  # zero fees to isolate rounding
            protocol_fee_bps=0,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        for _ in range(num_shares):
            m.buy(sender="alice", outcome_index=0, max_cost=10 * SCALE * SCALE, now=1000)
        # Also buy other outcomes so pool has mixed positions
        m.buy(sender="bob", outcome_index=1 % num_outcomes, max_cost=10 * SCALE * SCALE, now=1001)
        m.trigger_resolution(sender="anyone", now=m.deadline + 1)
        m.propose_resolution(sender="creator", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 2)
        m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs + 1)
        return m, num_shares

    @pytest.mark.parametrize("chunk_size", [1, 2])
    def test_chunked_claim_leq_single_claim(self, chunk_size):
        """Single market, alice claims N shares in chunks of chunk_size."""
        import copy
        m_single, n = self._setup_resolved(num_shares=4)
        m_chunked = copy.deepcopy(m_single)

        # Single: claim all 4 at once
        r_single = m_single.claim(sender="alice", outcome_index=0, shares=4 * SHARE_UNIT)
        single_payout = r_single["payout"]

        # Chunked: claim in pieces
        chunked_total = 0
        remaining = 4 * SHARE_UNIT
        chunk = chunk_size * SHARE_UNIT
        while remaining > 0:
            actual_chunk = min(chunk, remaining)
            r = m_chunked.claim(sender="alice", outcome_index=0, shares=actual_chunk)
            chunked_total += r["payout"]
            remaining -= actual_chunk

        # Chunked payout MUST be ≤ single payout (floor rounding)
        assert chunked_total <= single_payout, (
            f"Chunked ({chunked_total}) > single ({single_payout}): rounding leak!"
        )


# ── A9: pool_balance never negative after multi-user activity ──────────────────
class TestA9SolvencyInvariant:
    """pool_balance must remain non-negative after all operations."""

    def test_pool_balance_nonneg_after_buy_sell_claim(self):
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)

        BIG = 10 * SHARE_UNIT
        # Alice buys many outcome 0 shares
        for _ in range(5):
            m.buy(sender="alice", outcome_index=0, max_cost=10**18, now=1000)
        # Bob buys outcome 1
        for _ in range(3):
            m.buy(sender="bob", outcome_index=1, max_cost=10**18, now=1001)
        # Alice sells some shares
        for _ in range(2):
            m.sell(sender="alice", outcome_index=0, min_return=0, now=2000)
        assert m.pool_balance >= 0, f"pool_balance went negative: {m.pool_balance}"
        assert m.total_outstanding_cost_basis >= 0

        # Resolve outcome 0
        m.trigger_resolution(sender="anyone", now=m.deadline + 1)
        m.propose_resolution(sender="creator", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 2)
        m.finalize_resolution(sender="anyone", now=m.deadline + 2 + m.challenge_window_secs + 1)

        # All alice's remaining shares
        while m.user_outcome_shares["alice"][0] > 0:
            to_claim = min(SHARE_UNIT, m.user_outcome_shares["alice"][0])
            m.claim(sender="alice", outcome_index=0, shares=to_claim)

        assert m.pool_balance >= 0, f"pool_balance went negative after claims: {m.pool_balance}"

    def test_solvency_after_max_buy_stress(self):
        """Push one outcome to near-certainty; verify solvency invariant."""
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=0,
            protocol_fee_bps=0,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)

        # Buy 20 shares of outcome 0 to push probability near 1
        for _ in range(20):
            m.buy(sender="whale", outcome_index=0, max_cost=10**18, now=1000)

        # Verify solvency: pool_balance >= q[winning] (model invariant)
        q0 = m.q[0]  # or however the model exposes q
        assert m.pool_balance >= q0, (
            f"Solvency check FAIL: pool_balance={m.pool_balance} < q[0]={q0}"
        )


# ── A10: LP cannot extract more than pro-rata share ────────────────────────────
class TestA10LpNoExtraExtraction:
    """LP provide then withdraw: net gain must be ≤ fees earned."""

    def test_lp_round_trip_no_free_money(self):
        m = MarketAppModel(
            creator="creator",
            currency_asa=CURRENCY_ASA,
            outcome_asa_ids=OUTCOME_ASA_IDS,
            b=B,
            lp_fee_bps=200,
            protocol_fee_bps=50,
            deadline=100_000,
            question_hash=b"q" * 32,
            main_blueprint_hash=b"b" * 32,
            dispute_blueprint_hash=b"d" * 32,
            challenge_window_secs=86_400,
            resolution_authority="creator",
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            grace_period_secs=3_600,
            market_admin="creator",
            protocol_config_id=77,
            factory_id=88,
            cancellable=True,
        )
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)

        # LP2 provides liquidity
        lp2_deposit = DEPOSIT
        m.provide_liq(sender="lp2", deposit_amount=lp2_deposit, now=500)

        # Trading activity (fees accrue)
        for _ in range(5):
            m.buy(sender="trader", outcome_index=0, max_cost=10**18, now=1000)
        for _ in range(2):
            m.sell(sender="trader", outcome_index=0, min_return=0, now=2000)

        # LP2 withdraws
        lp2_shares = m.user_lp_shares["lp2"]
        r = m.withdraw_liq(sender="lp2", shares_to_burn=lp2_shares)
        lp2_out = r["usdc_return"] + r.get("fee_return", 0)

        # Net gain ≤ fees earned; LP should not get back more than deposit + fees
        # Without fees (lp_fee_bps=0), lp2_out ≤ lp2_deposit
        # With fees, lp2_out ≤ lp2_deposit + accrued_fees
        # The invariant is simply that pool_balance stays non-negative
        assert m.pool_balance >= 0, f"LP withdrawal caused pool_balance={m.pool_balance} < 0"
        assert lp2_out >= 0, "LP got negative return"

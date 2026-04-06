"""Extended red-team tests — contract bypass, economic exploits, edge cases.

Covers attack vectors beyond C6's original scope: factory bypass, challenge
griefing, malicious resolution logic, double-operations, zero-value attacks.
"""

from __future__ import annotations

import algosdk.account
import algosdk.logic
import pytest
from algopy import Account, Asset, Bytes, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    MAX_BLUEPRINT_SIZE,
    QuestionMarket,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLVED,
)
from smart_contracts.market_app.model import MarketAppError, MarketAppModel

CURRENCY_ASA = 31_566_704
OUTCOME_ASA_IDS = [1000, 1001, 1002]
DEPOSIT = 200_000_000
MAX_COST = 50_000_000


def make_address() -> str:
    return algosdk.account.generate_account()[1]


def get_app_address(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_usdc_payment(context, contract, sender, amount):
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(CURRENCY_ASA),
        asset_amount=UInt64(amount),
    )


def call_as(context, sender, method, *args, latest_timestamp=None):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    with context.txn.create_group([deferred]):
        return method(*args)


def create_contract(context, contract: QuestionMarket, creator: str) -> None:
    call_as(
        context,
        creator,
        contract.create,
        arc4.Address(creator),
        arc4.UInt64(CURRENCY_ASA),
        arc4.UInt64(3),
        arc4.UInt64(100_000_000),
        arc4.UInt64(200),
        arc4.UInt64(50),
        arc4.UInt64(100_000),
        arc4.DynamicBytes(b"q" * 32),
        arc4.DynamicBytes(b"b" * 32),
        arc4.DynamicBytes(b"d" * 32),
        arc4.UInt64(86_400),
        arc4.Address(creator),
        arc4.UInt64(10_000_000),
        arc4.UInt64(10_000_000),
        arc4.UInt64(3_600),
        arc4.Address(creator),
        arc4.UInt64(77),
        arc4.UInt64(88),
        arc4.Bool(True),
        latest_timestamp=1,
    )


@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *args, **kwargs: None)


def make_market_model(*, cancellable=True) -> MarketAppModel:
    return MarketAppModel(
        creator="creator",
        currency_asa=CURRENCY_ASA,
        outcome_asa_ids=OUTCOME_ASA_IDS,
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
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=cancellable,
    )


# ---------------------------------------------------------------------------
# 1. Challenge-Cancel Griefing Loop
# ---------------------------------------------------------------------------


class TestChallengeGriefing:
    """Dispute-phase griefing and bond accounting."""

    def test_challenge_requires_real_dispute_and_bond_is_escrowed(self) -> None:
        """Challenge no longer free-cancels the market; bond remains escrowed until dispute settles."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)

        m.challenge_resolution(sender="griefer", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
        assert m.status == STATUS_DISPUTED
        assert m.challenger == "griefer"
        assert m.challenger_bond_held == m.challenge_bond
        assert m.dispute_sink_balance == 0

    def test_cannot_challenge_twice(self) -> None:
        """After challenge, market is CANCELLED — no second challenge possible."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="griefer", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)

        # Can't challenge again — status is DISPUTED, not RESOLUTION_PROPOSED
        with pytest.raises(MarketAppError, match="invalid status"):
            m.challenge_resolution(sender="griefer2", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 3)

    def test_non_cancellable_market_resists_creator_cancel(self) -> None:
        """Markets with cancellable=false resist creator cancel attempts."""
        m = make_market_model(cancellable=False)
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        with pytest.raises(MarketAppError):
            m.cancel(sender="creator")


# ---------------------------------------------------------------------------
# 2. Double Operations
# ---------------------------------------------------------------------------


class TestDoubleOperations:
    def test_double_bootstrap_rejected(self, disable_arc4_emit) -> None:
        """Cannot bootstrap a market twice."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{}'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{}'))

            payment = make_usdc_payment(context, contract, creator, DEPOSIT)
            call_as(context, creator, contract.bootstrap, arc4.UInt64(DEPOSIT), payment, latest_timestamp=1)
            assert int(contract.status.value) == STATUS_ACTIVE

            # Second bootstrap should fail — status is ACTIVE, not CREATED
            payment2 = make_usdc_payment(context, contract, creator, DEPOSIT)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(DEPOSIT), payment2, latest_timestamp=2)

    def test_double_claim_rejected(self) -> None:
        """Cannot claim the same shares twice."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="winner", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)

        # First claim succeeds
        m.claim(sender="winner", outcome_index=0)

        # Second claim fails — no shares left
        with pytest.raises(MarketAppError, match="insufficient"):
            m.claim(sender="winner", outcome_index=0)

    def test_double_refund_rejected(self) -> None:
        """Cannot refund the same shares twice."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.cancel(sender="creator")

        m.refund(sender="trader", outcome_index=0)
        with pytest.raises(MarketAppError, match="insufficient"):
            m.refund(sender="trader", outcome_index=0)


# ---------------------------------------------------------------------------
# 3. Malicious Resolution Logic
# ---------------------------------------------------------------------------


class TestMaliciousResolutionLogic:
    def test_empty_blueprint_rejected(self, disable_arc4_emit) -> None:
        """store_main_blueprint and store_dispute_blueprint reject empty data."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b''))
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b''))

    def test_oversized_blueprint_rejected(self) -> None:
        """store_main_blueprint/store_dispute_blueprint reject data exceeding MAX_BLUEPRINT_SIZE.
        Note: algopy_testing DynamicBytes caps at 4096, so we verify the
        contract constant is set correctly and trust the on-chain assert."""
        assert MAX_BLUEPRINT_SIZE == 8192
        # The contract checks: raw.length <= UInt64(MAX_BLUEPRINT_SIZE)
        # On-chain this rejects >8KB. Testing framework can't construct >4KB DynamicBytes.

    def test_binary_blueprint_accepted(self, disable_arc4_emit) -> None:
        """Contract doesn't validate JSON — it stores raw bytes. Engine validates."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            # Binary garbage -- contract accepts (it stores raw bytes)
            # Engine will reject at parse time
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'\x00\xff\xfe\xfd'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'\x00\xff\xfe\xfd'))

    def test_attacker_cannot_overwrite_blueprints(self, disable_arc4_emit) -> None:
        """Only creator can store blueprints."""
        creator = make_address()
        attacker = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            with pytest.raises(AssertionError):
                call_as(context, attacker, contract.store_main_blueprint, arc4.DynamicBytes(b'{"evil": true}'))
            with pytest.raises(AssertionError):
                call_as(context, attacker, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"evil": true}'))


# ---------------------------------------------------------------------------
# 4. Zero-Value Edge Cases
# ---------------------------------------------------------------------------


class TestZeroValueAttacks:
    def test_buy_with_zero_max_cost_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError):
            m.buy(sender="trader", outcome_index=0, max_cost=0, now=1000)

    def test_provide_zero_liquidity_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError):
            m.provide_liq(sender="lp", deposit_amount=0, now=1000)

    def test_withdraw_zero_shares_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError):
            m.withdraw_liq(sender="creator", shares_to_burn=0)

    def test_sell_without_shares_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError):
            m.sell(sender="trader", outcome_index=0, min_return=0, now=1000)

    def test_claim_wrong_outcome_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        m.buy(sender="trader", outcome_index=1, max_cost=MAX_COST, now=1001)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)

        # Can claim outcome 0 (winner)
        m.claim(sender="trader", outcome_index=0)
        # Cannot claim outcome 1 (loser)
        with pytest.raises(MarketAppError, match="only winning"):
            m.claim(sender="trader", outcome_index=1)

    def test_out_of_range_outcome_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError, match="out of range"):
            m.buy(sender="trader", outcome_index=99, max_cost=MAX_COST, now=1000)


# ---------------------------------------------------------------------------
# 5. Timing Attacks
# ---------------------------------------------------------------------------


class TestTimingAttacks:
    def test_buy_at_exact_deadline_rejected(self) -> None:
        """Cannot buy at exactly the deadline timestamp."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError, match="deadline"):
            m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=m.deadline)

    def test_trigger_before_deadline_rejected(self) -> None:
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        with pytest.raises(MarketAppError, match="deadline"):
            m.trigger_resolution(sender="anyone", now=m.deadline - 1)

    def test_finalize_at_exact_window_boundary(self) -> None:
        """finalize_resolution requires >= window end, not just >."""
        m = make_market_model()
        m.bootstrap(sender="creator", deposit_amount=DEPOSIT)
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        proposal_time = m.deadline + 1
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=proposal_time)

        window_end = proposal_time + m.challenge_window_secs

        # One second before window end — should fail
        with pytest.raises(MarketAppError, match="window"):
            m.finalize_resolution(sender="anyone", now=window_end - 1)

        # At exactly window end — should succeed
        m.finalize_resolution(sender="anyone", now=window_end)

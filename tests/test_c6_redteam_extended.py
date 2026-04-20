"""Extended red-team tests — contract bypass, economic exploits, edge cases.

Covers attack vectors beyond C6's original scope: factory bypass, challenge
griefing, malicious resolution logic, double-operations, zero-value attacks.
"""

from __future__ import annotations

import algosdk.account
import algosdk.logic
import pytest
from algopy import Account, Application, Asset, Bytes, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    QuestionMarket,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLVED,
)
from smart_contracts.market_app.model import MarketAppError, MarketAppModel
from smart_contracts.protocol_config.contract import (
    KEY_CHALLENGE_BOND,
    KEY_CHALLENGE_BOND_BPS,
    KEY_CHALLENGE_BOND_CAP,
    KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    KEY_MARKET_FACTORY_ID,
    KEY_MAX_ACTIVE_LP_V4_OUTCOMES,
    KEY_MIN_CHALLENGE_WINDOW_SECS,
    KEY_PROPOSAL_BOND,
    KEY_PROPOSAL_BOND_BPS,
    KEY_PROPOSAL_BOND_CAP,
    KEY_PROPOSER_FEE_BPS,
    KEY_PROPOSER_FEE_FLOOR_BPS,
    KEY_PROTOCOL_FEE_BPS,
    KEY_PROTOCOL_TREASURY,
)

CURRENCY_ASA = 31_566_704
OUTCOME_ASA_IDS = [1000, 1001, 1002]
DEPOSIT = 200_000_000
MAX_COST = 50_000_000
PROTOCOL_CONFIG_APP_ID = 77
DEFAULT_FACTORY_APP_ID = 8_001


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


def _seed_protocol_min_window(context, minimum: int = 86_400) -> Application:
    app = context.any.application(id=PROTOCOL_CONFIG_APP_ID)
    context.ledger.set_global_state(app, KEY_MIN_CHALLENGE_WINDOW_SECS, minimum)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND, 10_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND, 10_000_000)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_BPS, 500)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_BPS, 500)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_CAP, 100_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_CAP, 100_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_BPS, 0)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_FLOOR_BPS, 0)
    # Keys required by contract.create() since protocol_fee_bps/treasury/lambda moved to config
    context.ledger.set_global_state(app, KEY_PROTOCOL_FEE_BPS, 50)
    context.ledger.set_global_state(app, KEY_PROTOCOL_TREASURY, Account(make_address()).bytes.value)
    context.ledger.set_global_state(app, KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP, 150_000)
    context.ledger.set_global_state(app, KEY_MAX_ACTIVE_LP_V4_OUTCOMES, 8)
    context.ledger.set_global_state(app, KEY_MARKET_FACTORY_ID, DEFAULT_FACTORY_APP_ID)
    return app


def create_contract(
    context,
    contract: QuestionMarket,
    creator: str,
    *,
    blueprint_cid: bytes = b"ipfs://blueprint-cid",
) -> None:
    protocol_app = _seed_protocol_min_window(context)
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(3),
        initial_b=arc4.UInt64(100_000_000),
        lp_fee_bps=arc4.UInt64(200),
        deadline=arc4.UInt64(100_000),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        blueprint_cid=arc4.DynamicBytes(blueprint_cid),
        challenge_window_secs=arc4.UInt64(86_400),
        resolution_authority=arc4.Address(creator),
        grace_period_secs=arc4.UInt64(3_600),
        market_admin=arc4.Address(creator),
        protocol_config_id=arc4.UInt64(PROTOCOL_CONFIG_APP_ID),
        cancellable=arc4.Bool(True),
        lp_entry_max_price_fp=arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP),
    )
    app_data = context.ledger._app_data[contract.__app_id__]
    app_data.fields["creator"] = Account(algosdk.logic.get_application_address(DEFAULT_FACTORY_APP_ID))
    context.ledger.patch_global_fields(latest_timestamp=1)
    context._default_sender = Account(creator)
    deferred = context.txn.defer_app_call(contract.create, **args)
    deferred._txns[-1].fields["apps"] = (protocol_app,)
    with context.txn.create_group([deferred]):
        contract.create(**args)


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
    def test_missing_blueprint_cid_rejected_at_bootstrap(self, disable_arc4_emit) -> None:
        """Bootstrap fails when create-time blueprint metadata is missing."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator, blueprint_cid=b"")
            payment = make_usdc_payment(context, contract, creator, DEPOSIT)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(DEPOSIT), payment, latest_timestamp=1)

    def test_no_runtime_blueprint_upload_surface(self, disable_arc4_emit) -> None:
        """Blueprint metadata is fixed at create time; runtime upload methods are gone."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            assert not hasattr(contract, "store_main_blueprint")
            assert not hasattr(contract, "store_dispute_blueprint")


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

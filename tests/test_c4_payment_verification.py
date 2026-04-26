"""C4 payment verification tests (P11, P12, P13).

Tests that the Algopy contract correctly rejects invalid payments
and sends correct outbound transfers.
"""

from __future__ import annotations

import algosdk.account
import algosdk.logic

import pytest
from algopy import Account, Application, Asset, Global, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    COST_BOX_MBR,
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    SHARE_BOX_MBR,
    SHARE_UNIT,
    STATUS_ACTIVE,
)
from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.protocol_config.contract import (
    KEY_CHALLENGE_BOND,
    KEY_CHALLENGE_BOND_BPS,
    KEY_CHALLENGE_BOND_CAP,
    KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    KEY_MAX_ACTIVE_LP_V4_OUTCOMES,
    KEY_MARKET_FACTORY_ID,
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
WRONG_ASA = 99_999
PROTOCOL_CONFIG_APP_ID = 77
DEFAULT_FACTORY_APP_ID = 8_001


def make_address() -> str:
    return algosdk.account.generate_account()[1]


def get_app_address(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_payment(
    context,
    contract,
    sender,
    amount,
    *,
    asset_id=CURRENCY_ASA,
    receiver=None,
    rekey_to=None,
    asset_close_to=None,
    asset_sender=None,
):
    """Create an ASA transfer transaction."""
    recv = receiver or get_app_address(contract)
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


def make_mbr_payment(context, contract, sender, amount):
    """Create an ALGO Payment txn funding MBR top-up for a box-creating call."""
    zero = Global.zero_address
    return context.any.txn.payment(
        sender=Account(sender),
        receiver=Account(get_app_address(contract)),
        amount=UInt64(amount),
        rekey_to=zero,
        close_remainder_to=zero,
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
    # Keys required by contract.create() — read from protocol config app
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
    resolver: str | None = None,
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
        resolution_authority=arc4.Address(resolver or creator),
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


def _price_array(values: list[int]) -> arc4.DynamicArray[arc4.UInt64]:
    return arc4.DynamicArray[arc4.UInt64](*(arc4.UInt64(value) for value in values))


@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *args, **kwargs: None)


def setup_bootstrapped_contract(context, creator, disable_arc4_emit_fixture=None):
    """Create and bootstrap a QuestionMarket contract, returning it."""
    contract = QuestionMarket()
    create_contract(context, contract, creator)
    payment = make_payment(context, contract, creator, 200_000_000)
    call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
    return contract


# ---------------------------------------------------------------------------
# P11: Payment verification — reject bad payments
# ---------------------------------------------------------------------------


class TestP11PaymentVerification:
    def test_bootstrap_rejects_underpayment(self, disable_arc4_emit) -> None:
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)

            # Underpay: send 100 but claim 200_000_000
            payment = make_payment(context, contract, creator, 100)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_bootstrap_rejects_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)

            # Wrong ASA
            payment = make_payment(context, contract, creator, 200_000_000, asset_id=WRONG_ASA)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_bootstrap_rejects_wrong_receiver(self, disable_arc4_emit) -> None:
        creator = make_address()
        wrong_receiver = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)

            # Wrong receiver
            payment = make_payment(context, contract, creator, 200_000_000, receiver=wrong_receiver)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_buy_rejects_underpayment(self, disable_arc4_emit) -> None:
        creator = make_address()
        buyer = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            # Underpay: send 1 microunit
            payment = make_payment(context, contract, buyer, 1)
            mbr = make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    buyer,
                    contract.buy,
                    arc4.UInt64(0),
                    arc4.UInt64(50_000_000),
                    arc4.UInt64(1_000_000_000),
                    payment,
                    mbr,
                    latest_timestamp=5000,
                )

    def test_buy_rejects_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        buyer = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            payment = make_payment(context, contract, buyer, 50_000_000, asset_id=WRONG_ASA)
            mbr = make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    buyer,
                    contract.buy,
                    arc4.UInt64(0),
                    arc4.UInt64(50_000_000),
                    arc4.UInt64(1_000_000_000),
                    payment,
                    mbr,
                    latest_timestamp=5000,
                )

    def test_buy_rejects_reused_payment_gtxn(self, disable_arc4_emit) -> None:
        """
        Regression for the gtxn-index reuse double-spend (qmrkt/contracts#11).

        An attacker submits one USDC payment + two buy() AppCalls in the same
        group, both passing the SAME payment txn as their `payment` argument.
        Without the `payment.group_index == Txn.group_index - 2` check, both
        buys would settle against a single payment, letting the attacker mint
        free shares. With the check in place, the second buy must reject.
        """
        creator = make_address()
        attacker = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            single_payment = make_payment(context, contract, attacker, 10_000_000)
            mbr1 = make_mbr_payment(context, contract, attacker, SHARE_BOX_MBR + COST_BOX_MBR)
            mbr2 = make_mbr_payment(context, contract, attacker, SHARE_BOX_MBR + COST_BOX_MBR)

            context.ledger.patch_global_fields(latest_timestamp=5_000)
            context._default_sender = Account(attacker)

            d1 = context.txn.defer_app_call(
                contract.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(10_000_000),
                single_payment, mbr1,
            )
            d2 = context.txn.defer_app_call(
                contract.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(10_000_000),
                single_payment, mbr2,  # SAME single_payment reused
            )

            with pytest.raises(AssertionError):
                with context.txn.create_group([d1, d2]):
                    contract.buy(arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(10_000_000), single_payment, mbr1)
                    contract.buy(arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(10_000_000), single_payment, mbr2)

    @pytest.mark.parametrize(
        ("payment_kwargs", "method_name", "method_args", "sender_role"),
        [
            ({"rekey_to": make_address()}, "bootstrap", (arc4.UInt64(200_000_000),), "creator"),
            ({"asset_close_to": make_address()}, "bootstrap", (arc4.UInt64(200_000_000),), "creator"),
            ({"asset_sender": make_address()}, "bootstrap", (arc4.UInt64(200_000_000),), "creator"),
            (
                {"rekey_to": make_address()},
                "buy",
                (arc4.UInt64(0), arc4.UInt64(50_000_000), arc4.UInt64(1_000_000_000)),
                "buyer",
            ),
            (
                {"asset_close_to": make_address()},
                "buy",
                (arc4.UInt64(0), arc4.UInt64(50_000_000), arc4.UInt64(1_000_000_000)),
                "buyer",
            ),
            (
                {"asset_sender": make_address()},
                "buy",
                (arc4.UInt64(0), arc4.UInt64(50_000_000), arc4.UInt64(1_000_000_000)),
                "buyer",
            ),
            (
                {"rekey_to": make_address()},
                "enter_lp_active",
                (arc4.UInt64(50_000_000), arc4.UInt64(100_000_000), _price_array(lmsr_prices([0, 0, 0], 100_000_000)), arc4.UInt64(PRICE_TOLERANCE_BASE)),
                "lp",
            ),
            (
                {"asset_close_to": make_address()},
                "enter_lp_active",
                (arc4.UInt64(50_000_000), arc4.UInt64(100_000_000), _price_array(lmsr_prices([0, 0, 0], 100_000_000)), arc4.UInt64(PRICE_TOLERANCE_BASE)),
                "lp",
            ),
            (
                {"asset_sender": make_address()},
                "enter_lp_active",
                (arc4.UInt64(50_000_000), arc4.UInt64(100_000_000), _price_array(lmsr_prices([0, 0, 0], 100_000_000)), arc4.UInt64(PRICE_TOLERANCE_BASE)),
                "lp",
            ),
        ],
    )
    def test_rejects_payment_with_privileged_or_redirect_fields(
        self,
        disable_arc4_emit,
        payment_kwargs,
        method_name,
        method_args,
        sender_role,
    ) -> None:
        creator = make_address()
        buyer = make_address()
        lp = make_address()
        with algopy_testing_context() as context:
            if method_name == "bootstrap":
                contract = QuestionMarket()
                create_contract(context, contract, creator)
                sender = creator
                amount = 200_000_000
            else:
                contract = setup_bootstrapped_contract(context, creator)
                sender = buyer if sender_role == "buyer" else lp
                amount = 50_000_000

            payment = make_payment(context, contract, sender, amount, **payment_kwargs)
            method = getattr(contract, method_name)
            if method_name == "buy":
                extra = (make_mbr_payment(context, contract, sender, SHARE_BOX_MBR + COST_BOX_MBR),)
            else:
                # enter_lp_active does not create boxes; LP fees accrue in local state.
                extra = ()
            with pytest.raises(AssertionError):
                call_as(context, sender, method, *method_args, payment, *extra, latest_timestamp=5000 if method_name != "bootstrap" else 1)

    def test_sell_rejects_sender_without_internal_shares(self, disable_arc4_emit) -> None:
        creator = make_address()
        seller = make_address()
        attacker = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            buy_payment = make_payment(context, contract, seller, 50_000_000)
            buy_mbr = make_mbr_payment(context, contract, seller, SHARE_BOX_MBR + COST_BOX_MBR)
            call_as(
                context,
                seller,
                contract.buy,
                arc4.UInt64(0),
                arc4.UInt64(50_000_000),
                arc4.UInt64(1_000_000_000),
                buy_payment,
                buy_mbr,
                latest_timestamp=5_000,
            )

            with pytest.raises(AssertionError):
                call_as(
                    context,
                    attacker,
                    contract.sell,
                    arc4.UInt64(0),
                    arc4.UInt64(SHARE_UNIT),
                    arc4.UInt64(0),
                    latest_timestamp=5_001,
                )

    def test_enter_lp_active_rejects_underpayment(self, disable_arc4_emit) -> None:
        creator = make_address()
        lp = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            payment = make_payment(context, contract, lp, 1)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    lp,
                    contract.enter_lp_active,
                    arc4.UInt64(50_000_000),
                    arc4.UInt64(100_000_000),
                    _price_array(lmsr_prices([0, 0, 0], 100_000_000)),
                    arc4.UInt64(PRICE_TOLERANCE_BASE),
                    payment,
                    latest_timestamp=5000,
                )

    def test_propose_resolution_rejects_underpayment_and_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        proposer = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            call_as(context, proposer, contract.trigger_resolution, latest_timestamp=100_000)

            underpaid = make_payment(context, contract, proposer, 1)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    proposer,
                    contract.propose_resolution,
                    arc4.UInt64(0),
                    arc4.DynamicBytes(b"e" * 32),
                    underpaid,
                    latest_timestamp=103_601,
                )

            wrong_asset = make_payment(context, contract, proposer, 10_000_000, asset_id=WRONG_ASA)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    proposer,
                    contract.propose_resolution,
                    arc4.UInt64(0),
                    arc4.DynamicBytes(b"e" * 32),
                    wrong_asset,
                    latest_timestamp=103_601,
                )

    def test_challenge_resolution_rejects_underpayment_and_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        proposer = make_address()
        challenger = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            call_as(context, proposer, contract.trigger_resolution, latest_timestamp=100_000)
            proposal_payment = make_payment(context, contract, proposer, 10_000_000)
            call_as(
                context,
                proposer,
                contract.propose_resolution,
                arc4.UInt64(0),
                arc4.DynamicBytes(b"e" * 32),
                proposal_payment,
                latest_timestamp=103_601,
            )

            underpaid = make_payment(context, contract, challenger, 1)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    challenger,
                    contract.challenge_resolution,
                    underpaid,
                    arc4.UInt64(7),
                    arc4.DynamicBytes(b"c" * 32),
                    latest_timestamp=103_602,
                )

            wrong_asset = make_payment(context, contract, challenger, 10_000_000, asset_id=WRONG_ASA)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    challenger,
                    contract.challenge_resolution,
                    wrong_asset,
                    arc4.UInt64(7),
                    arc4.DynamicBytes(b"c" * 32),
                    latest_timestamp=103_602,
                )

    @pytest.mark.parametrize(
        ("method_name", "payment_kwargs", "sender_role", "timestamp"),
        [
            ("propose_resolution", {"rekey_to": make_address()}, "proposer", 103_601),
            ("propose_resolution", {"asset_close_to": make_address()}, "proposer", 103_601),
            ("propose_resolution", {"asset_sender": make_address()}, "proposer", 103_601),
            ("challenge_resolution", {"rekey_to": make_address()}, "challenger", 103_602),
            ("challenge_resolution", {"asset_close_to": make_address()}, "challenger", 103_602),
            ("challenge_resolution", {"asset_sender": make_address()}, "challenger", 103_602),
        ],
    )
    def test_resolution_bond_payments_reject_privileged_or_redirect_fields(
        self,
        disable_arc4_emit,
        method_name,
        payment_kwargs,
        sender_role,
        timestamp,
    ) -> None:
        creator = make_address()
        proposer = make_address()
        challenger = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            call_as(context, proposer, contract.trigger_resolution, latest_timestamp=100_000)

            if method_name == "propose_resolution":
                sender = proposer
                payment = make_payment(context, contract, sender, 10_000_000, **payment_kwargs)
                with pytest.raises(AssertionError):
                    call_as(
                        context,
                        sender,
                        contract.propose_resolution,
                        arc4.UInt64(0),
                        arc4.DynamicBytes(b"e" * 32),
                        payment,
                        latest_timestamp=timestamp,
                    )
            else:
                proposal_payment = make_payment(context, contract, proposer, 10_000_000)
                call_as(
                    context,
                    proposer,
                    contract.propose_resolution,
                    arc4.UInt64(0),
                    arc4.DynamicBytes(b"e" * 32),
                    proposal_payment,
                    latest_timestamp=103_601,
                )
                sender = challenger
                payment = make_payment(context, contract, sender, 10_000_000, **payment_kwargs)
                with pytest.raises(AssertionError):
                    call_as(
                        context,
                        sender,
                        contract.challenge_resolution,
                        payment,
                        arc4.UInt64(7),
                        arc4.DynamicBytes(b"c" * 32),
                        latest_timestamp=timestamp,
                    )


# ---------------------------------------------------------------------------
# P13: Ledger-only lifecycle
# ---------------------------------------------------------------------------


class TestP13LedgerOnlyLifecycle:
    def test_bootstrap_rejects_missing_blueprint_cid(self, disable_arc4_emit) -> None:
        """Bootstrap requires create-time blueprint metadata in the new flow."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator, blueprint_cid=b"")

            payment = make_payment(context, contract, creator, 200_000_000)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_contract_exposes_no_legacy_blueprint_storage_surface(self, disable_arc4_emit) -> None:
        """Atomic markets no longer expose runtime blueprint upload methods."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            assert not hasattr(contract, "store_main_blueprint")
            assert not hasattr(contract, "store_dispute_blueprint")

    def test_contract_exposes_no_outcome_registration_surface(self, disable_arc4_emit) -> None:
        """Ledger-only markets remove mutable outcome-ASA registration entirely."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            assert not hasattr(contract, "register_outcome_asa")
            assert not hasattr(contract, "opt_in_to_asa")

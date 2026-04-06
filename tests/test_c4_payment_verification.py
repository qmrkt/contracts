"""C4 payment verification tests (P11, P12, P13).

Tests that the Algopy contract correctly rejects invalid payments
and sends correct outbound transfers.
"""

from __future__ import annotations

import algosdk.account
import algosdk.logic

import pytest
from algopy import Account, Asset, Global, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    QuestionMarket,
    SHARE_UNIT,
    STATUS_ACTIVE,
)

CURRENCY_ASA = 31_566_704
WRONG_ASA = 99_999
OUTCOME_ASA_IDS = [1000, 1001, 1002]


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


def call_as(context, sender, method, *args, latest_timestamp=None):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    with context.txn.create_group([deferred]):
        return method(*args)


def create_contract(context, contract: QuestionMarket, creator: str, resolver: str | None = None) -> None:
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(3),
        initial_b=arc4.UInt64(100_000_000),
        lp_fee_bps=arc4.UInt64(200),
        protocol_fee_bps=arc4.UInt64(50),
        deadline=arc4.UInt64(100_000),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        main_blueprint_hash=arc4.DynamicBytes(b"b" * 32),
        dispute_blueprint_hash=arc4.DynamicBytes(b"d" * 32),
        challenge_window_secs=arc4.UInt64(86_400),
        resolution_authority=arc4.Address(resolver or creator),
        challenge_bond=arc4.UInt64(10_000_000),
        proposal_bond=arc4.UInt64(10_000_000),
        grace_period_secs=arc4.UInt64(3_600),
        market_admin=arc4.Address(creator),
        protocol_config_id=arc4.UInt64(77),
        factory_id=arc4.UInt64(88),
        cancellable=arc4.Bool(True),
    )
    call_as(context, creator, contract.create, *args.values(), latest_timestamp=1)


@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *args, **kwargs: None)


def setup_bootstrapped_contract(context, creator, disable_arc4_emit_fixture=None):
    """Create and bootstrap a QuestionMarket contract, returning it."""
    contract = QuestionMarket()
    create_contract(context, contract, creator)
    for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
        call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
    call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
    call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

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
            for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

            # Underpay: send 100 but claim 200_000_000
            payment = make_payment(context, contract, creator, 100)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_bootstrap_rejects_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

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
            for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

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
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    buyer,
                    contract.buy,
                    arc4.UInt64(0),
                    arc4.UInt64(50_000_000),
                    arc4.UInt64(1_000_000_000),
                    payment,
                    latest_timestamp=5000,
                )

    def test_buy_rejects_wrong_asset(self, disable_arc4_emit) -> None:
        creator = make_address()
        buyer = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            payment = make_payment(context, contract, buyer, 50_000_000, asset_id=WRONG_ASA)
            with pytest.raises(AssertionError):
                call_as(
                    context,
                    buyer,
                    contract.buy,
                    arc4.UInt64(0),
                    arc4.UInt64(50_000_000),
                    arc4.UInt64(1_000_000_000),
                    payment,
                    latest_timestamp=5000,
                )

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
            ({"rekey_to": make_address()}, "provide_liq", (arc4.UInt64(50_000_000),), "lp"),
            ({"asset_close_to": make_address()}, "provide_liq", (arc4.UInt64(50_000_000),), "lp"),
            ({"asset_sender": make_address()}, "provide_liq", (arc4.UInt64(50_000_000),), "lp"),
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
                for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                    call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
                call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
                call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
                sender = creator
                amount = 200_000_000
            else:
                contract = setup_bootstrapped_contract(context, creator)
                sender = buyer if sender_role == "buyer" else lp
                amount = 50_000_000

            payment = make_payment(context, contract, sender, amount, **payment_kwargs)
            method = getattr(contract, method_name)
            with pytest.raises(AssertionError):
                call_as(context, sender, method, *method_args, payment, latest_timestamp=5000 if method_name != "bootstrap" else 1)

    def test_sell_rejects_payment_with_sender_mismatch_or_redirect_fields(self, disable_arc4_emit) -> None:
        creator = make_address()
        seller = make_address()
        attacker = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            buy_payment = make_payment(context, contract, seller, 50_000_000)
            call_as(
                context,
                seller,
                contract.buy,
                arc4.UInt64(0),
                arc4.UInt64(50_000_000),
                arc4.UInt64(1_000_000_000),
                buy_payment,
                latest_timestamp=5_000,
            )

            bad_payments = [
                make_payment(context, contract, attacker, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[0]),
                make_payment(context, contract, seller, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[0], rekey_to=attacker),
                make_payment(context, contract, seller, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[0], asset_close_to=attacker),
                make_payment(context, contract, seller, SHARE_UNIT, asset_id=OUTCOME_ASA_IDS[0], asset_sender=attacker),
            ]

            for payment in bad_payments:
                with pytest.raises(AssertionError):
                    call_as(
                        context,
                        seller,
                        contract.sell,
                        arc4.UInt64(0),
                        arc4.UInt64(SHARE_UNIT),
                        arc4.UInt64(0),
                        payment,
                        latest_timestamp=5_001,
                    )

    def test_provide_liq_rejects_underpayment(self, disable_arc4_emit) -> None:
        creator = make_address()
        lp = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)

            payment = make_payment(context, contract, lp, 1)
            with pytest.raises(AssertionError):
                call_as(context, lp, contract.provide_liq, arc4.UInt64(50_000_000), payment, latest_timestamp=5000)

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
# P13: ASA lifecycle — outcome ASA registration
# ---------------------------------------------------------------------------


class TestP13ASALifecycle:
    def test_bootstrap_rejects_missing_outcome_asa(self, disable_arc4_emit) -> None:
        """Bootstrap fails if not all outcome ASAs have been registered."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            # Only register 2 out of 3 outcome ASAs
            call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(0), Asset(1000))
            call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(1), Asset(1001))
            # Intentionally skip outcome 2
            call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
            call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

            payment = make_payment(context, contract, creator, 200_000_000)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_bootstrap_rejects_missing_resolution_logic(self, disable_arc4_emit) -> None:
        """Bootstrap fails if resolution logic has not been stored."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
            # Intentionally skip store_main_blueprint and store_dispute_blueprint

            payment = make_payment(context, contract, creator, 200_000_000)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

    def test_register_outcome_asa_only_in_created_status(self, disable_arc4_emit) -> None:
        """register_outcome_asa rejects after bootstrap."""
        creator = make_address()
        with algopy_testing_context() as context:
            contract = setup_bootstrapped_contract(context, creator)
            with pytest.raises(AssertionError):
                call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(0), Asset(9999))

    def test_register_outcome_asa_only_by_creator(self, disable_arc4_emit) -> None:
        """register_outcome_asa rejects non-creator."""
        creator = make_address()
        attacker = make_address()
        with algopy_testing_context() as context:
            contract = QuestionMarket()
            create_contract(context, contract, creator)
            with pytest.raises(AssertionError):
                call_as(context, attacker, contract.register_outcome_asa, arc4.UInt64(0), Asset(1000))

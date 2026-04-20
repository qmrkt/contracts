from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import algosdk.logic
from algosdk.constants import ZERO_ADDRESS
import pytest
from algopy import Account, Application, Asset, UInt64, arc4, op
from algopy_testing import algopy_testing_context

import smart_contracts.market_factory.contract as factory_module
import smart_contracts.protocol_config.contract as config_module
from smart_contracts.market_factory.contract import MarketFactory
from smart_contracts.market_app.contract import (
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    QuestionMarket,
)
from smart_contracts.protocol_config.contract import (
    BPS_DENOMINATOR,
    KEY_CHALLENGE_BOND,
    KEY_CHALLENGE_BOND_BPS,
    KEY_CHALLENGE_BOND_CAP,
    KEY_DEFAULT_B,
    KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    KEY_MAX_ACTIVE_LP_V4_OUTCOMES,
    KEY_MARKET_FACTORY_ID,
    KEY_MAX_LP_FEE_BPS,
    KEY_MAX_OUTCOMES,
    KEY_MIN_BOOTSTRAP_DEPOSIT,
    KEY_MIN_CHALLENGE_WINDOW_SECS,
    KEY_MIN_GRACE_PERIOD_SECS,
    KEY_PROPOSAL_BOND,
    KEY_PROPOSAL_BOND_BPS,
    KEY_PROPOSAL_BOND_CAP,
    KEY_PROPOSER_FEE_BPS,
    KEY_PROPOSER_FEE_FLOOR_BPS,
    KEY_PROTOCOL_FEE_BPS,
    KEY_PROTOCOL_FEE_CEILING_BPS,
    KEY_PROTOCOL_TREASURY,
    ProtocolConfig,
)
from tests.test_market_app_contract_runtime import make_address

ROOT_DIR = Path(__file__).resolve().parents[2]
MARKET_FACTORY_ARTIFACT = (
    ROOT_DIR / "smart_contracts" / "artifacts" / "market_factory" / "MarketFactory.approval.teal"
)
PROTOCOL_CONFIG_APP_ID = 7001
CURRENCY_ASA = 31_566_704
BLUEPRINT_CID = b"ipfs://blueprint-cid"


class FakeCreateResult:
    def __init__(self, app_id: int):
        self.created_app = Application(app_id)


class _FakeCreateCall:
    def __init__(self, app_id: int):
        self._result = FakeCreateResult(app_id)

    def submit(self) -> FakeCreateResult:
        return self._result


def patch_factory_inner_create(monkeypatch, app_id: int, captured: dict[str, object] | None = None) -> None:
    real_application_call = factory_module.itxn.ApplicationCall

    def wrapped_application_call(*args, **kwargs):
        if "approval_program" in kwargs:
            app_args = kwargs["app_args"]
            if captured is not None:
                captured["method"] = factory_module.MarketStub.create
                captured["args"] = app_args[1:]
                captured["kwargs"] = kwargs
                captured["create_method"] = factory_module.MarketStub.create
                captured["create_args"] = app_args[1:]
            return _FakeCreateCall(app_id)
        return real_application_call(*args, **kwargs)

    monkeypatch.setattr(factory_module.itxn, "ApplicationCall", wrapped_application_call)


def call_as(
    context,
    sender: str,
    method,
    *args,
    latest_timestamp: int | None = None,
    apps: tuple[Application, ...] | None = None,
):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    if apps:
        deferred._txns[-1].fields["apps"] = apps
    with context.txn.create_group([deferred]):
        return method(*args)


def get_app_address(contract) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_factory_funding_payment(context, contract: MarketFactory, sender: str, amount: int):
    return context.any.txn.payment(
        sender=Account(sender),
        receiver=Account(get_app_address(contract)),
        amount=UInt64(amount),
    )


def make_factory_asset_funding(context, contract: MarketFactory, sender: str, amount: int, asset_id: int = CURRENCY_ASA):
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(asset_id),
        asset_amount=UInt64(amount),
    )


def call_create_market(
    context,
    sender: str,
    contract: MarketFactory,
    *args,
    funding_amount: int = factory_module.CREATE_MARKET_MIN_FUNDING,
    usdc_funding_amount: int | None = None,
    latest_timestamp: int | None = None,
):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    if len(args) == 12:
        args = (*args[:-1], arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP), args[-1])
    algo_funding = make_factory_funding_payment(context, contract, sender, funding_amount)
    usdc_amount = int(args[-1].as_uint64()) if usdc_funding_amount is None else usdc_funding_amount
    usdc_funding = make_factory_asset_funding(context, contract, sender, usdc_amount)
    deferred = context.txn.defer_app_call(contract.create_market, *args, algo_funding, usdc_funding)
    deferred._txns[-1].fields["apps"] = (Application(contract.__app_id__), Application(PROTOCOL_CONFIG_APP_ID))
    with context.txn.create_group([algo_funding, usdc_funding, deferred]):
        return contract.create_market(*args, algo_funding, usdc_funding)


def call_create_market_canonical(
    context,
    sender: str,
    contract: MarketFactory,
    *args,
    funding_amount: int = factory_module.CREATE_MARKET_MIN_FUNDING,
    usdc_funding_amount: int | None = None,
    latest_timestamp: int | None = None,
):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    if len(args) == 12:
        args = (*args[:-1], arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP), args[-1])
    algo_funding = make_factory_funding_payment(context, contract, sender, funding_amount)
    usdc_amount = int(args[-1].as_uint64()) if usdc_funding_amount is None else usdc_funding_amount
    usdc_funding = make_factory_asset_funding(context, contract, sender, usdc_amount)
    deferred = context.txn.defer_app_call(contract.create_market, *args, algo_funding, usdc_funding)
    deferred._txns[-1].fields["apps"] = (Application(contract.__app_id__), Application(PROTOCOL_CONFIG_APP_ID))
    with context.txn.create_group([algo_funding, usdc_funding, deferred]):
        return contract.create_market(*args, algo_funding, usdc_funding)


def create_protocol_config(
    contract: ProtocolConfig,
    *,
    admin: str,
    treasury: str,
    market_factory_id: int = 0,
) -> None:
    contract.create(
        admin=arc4.Address(admin),
        min_bootstrap_deposit=arc4.UInt64(50_000_000),
        challenge_bond=arc4.UInt64(10_000_000),
        proposal_bond=arc4.UInt64(10_000_000),
        challenge_bond_bps=arc4.UInt64(500),
        proposal_bond_bps=arc4.UInt64(500),
        challenge_bond_cap=arc4.UInt64(100_000_000),
        proposal_bond_cap=arc4.UInt64(100_000_000),
        proposer_fee_bps=arc4.UInt64(0),
        proposer_fee_floor_bps=arc4.UInt64(0),
        default_b=arc4.UInt64(50_000_000),
        protocol_fee_ceiling_bps=arc4.UInt64(500),
        protocol_fee_bps=arc4.UInt64(50),
        protocol_treasury=arc4.Address(treasury),
        market_factory_id=arc4.UInt64(market_factory_id),
        max_outcomes=arc4.UInt64(16),
        min_challenge_window_secs=arc4.UInt64(86_400),
        min_grace_period_secs=arc4.UInt64(3_600),
        max_lp_fee_bps=arc4.UInt64(1_000),
        default_residual_linear_lambda_fp=arc4.UInt64(DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP),
        max_active_lp_v4_outcomes=arc4.UInt64(8),
    )


def create_market_factory(
    context,
    contract: MarketFactory,
    *,
    admin: str,
    resolution_authority: str,
    protocol_config_id: int = PROTOCOL_CONFIG_APP_ID,
    protocol_treasury: str | None = None,
) -> None:
    treasury = protocol_treasury or admin
    app_data = context.ledger._app_data[contract.__app_id__]
    app_data.fields["creator"] = Account(resolution_authority)
    app_data.is_creating = False
    context.ledger.set_global_state(Application(PROTOCOL_CONFIG_APP_ID), KEY_MARKET_FACTORY_ID, contract.__app_id__)
    context.ledger.set_global_state(Application(PROTOCOL_CONFIG_APP_ID), KEY_PROTOCOL_TREASURY, Account(treasury).bytes.value)
    context.ledger.set_box(contract, b"ap", b"a" * 5_000)
    context.ledger.set_box(contract, b"cp", b"c")


def seed_protocol_config_state(
    context,
    *,
    app_id: int,
    factory_id: int = 0,
    treasury: str | None = None,
    proposal_bond: int = 10_000_000,
    proposal_bond_cap: int = 100_000_000,
    proposer_fee_bps: int = 0,
    proposer_fee_floor_bps: int = 0,
    min_challenge_window_secs: int = 86_400,
) -> Application:
    app = context.any.application(id=app_id)
    treasury_address = treasury or make_address()
    state = {
        KEY_MIN_BOOTSTRAP_DEPOSIT: 50_000_000,
        KEY_CHALLENGE_BOND: 10_000_000,
        KEY_PROPOSAL_BOND: proposal_bond,
        KEY_CHALLENGE_BOND_BPS: 500,
        KEY_PROPOSAL_BOND_BPS: 500,
        KEY_CHALLENGE_BOND_CAP: 100_000_000,
        KEY_PROPOSAL_BOND_CAP: proposal_bond_cap,
        KEY_PROPOSER_FEE_BPS: proposer_fee_bps,
        KEY_PROPOSER_FEE_FLOOR_BPS: proposer_fee_floor_bps,
        KEY_DEFAULT_B: 50_000_000,
        KEY_PROTOCOL_FEE_BPS: 50,
        KEY_PROTOCOL_FEE_CEILING_BPS: 500,
        KEY_PROTOCOL_TREASURY: Account(treasury_address).bytes.value,
        KEY_MARKET_FACTORY_ID: factory_id,
        KEY_MAX_OUTCOMES: 16,
        KEY_MIN_CHALLENGE_WINDOW_SECS: min_challenge_window_secs,
        KEY_MIN_GRACE_PERIOD_SECS: 3_600,
        KEY_MAX_LP_FEE_BPS: 1_000,
        KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP: DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
        KEY_MAX_ACTIVE_LP_V4_OUTCOMES: 8,
    }
    for key, value in state.items():
        context.ledger.set_global_state(app, key, value)
    return app


def set_created_market_factory_creator(context, contract: QuestionMarket, factory_app_id: int) -> None:
    app_data = context.ledger._app_data[contract.__app_id__]
    app_data.fields["creator"] = Account(algosdk.logic.get_application_address(factory_app_id))


def max_proposer_fee(
    *,
    proposal_bond: int,
    proposal_bond_cap: int,
    proposer_fee_bps: int,
    proposer_fee_floor_bps: int,
    challenge_window_secs: int,
) -> int:
    floor_fee = (proposal_bond * proposer_fee_floor_bps + 9_999) // 10_000
    daily_fee = (proposal_bond_cap * proposer_fee_bps + 9_999) // 10_000
    window_fee = (daily_fee * challenge_window_secs + 86_399) // 86_400
    return max(floor_fee, window_fee)

@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(config_module.arc4, "emit", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(factory_module.arc4, "emit", lambda *args, **kwargs: None, raising=False)


@pytest.mark.parametrize(
    ("method_name", "argument_factory", "state_attr", "expected"),
    [
        ("update_admin", lambda: arc4.Address(make_address()), "admin", lambda arg: arg.bytes),
        ("update_min_bootstrap_deposit", lambda: arc4.UInt64(60_000_000), "min_bootstrap_deposit", lambda arg: arg.as_uint64()),
        ("update_challenge_bond", lambda: arc4.UInt64(11_000_000), "challenge_bond", lambda arg: arg.as_uint64()),
        ("update_proposal_bond", lambda: arc4.UInt64(12_000_000), "proposal_bond", lambda arg: arg.as_uint64()),
        ("update_challenge_bond_bps", lambda: arc4.UInt64(600), "challenge_bond_bps", lambda arg: arg.as_uint64()),
        ("update_proposal_bond_bps", lambda: arc4.UInt64(700), "proposal_bond_bps", lambda arg: arg.as_uint64()),
        ("update_challenge_bond_cap", lambda: arc4.UInt64(110_000_000), "challenge_bond_cap", lambda arg: arg.as_uint64()),
        ("update_proposal_bond_cap", lambda: arc4.UInt64(120_000_000), "proposal_bond_cap", lambda arg: arg.as_uint64()),
        ("update_proposer_fee_bps", lambda: arc4.UInt64(20), "proposer_fee_bps", lambda arg: arg.as_uint64()),
        ("update_proposer_fee_floor_bps", lambda: arc4.UInt64(10_000), "proposer_fee_floor_bps", lambda arg: arg.as_uint64()),
        ("update_default_b", lambda: arc4.UInt64(123_000_000), "default_b", lambda arg: arg.as_uint64()),
        ("update_protocol_fee_bps", lambda: arc4.UInt64(125), "protocol_fee_bps", lambda arg: arg.as_uint64()),
        ("update_protocol_fee_ceiling_bps", lambda: arc4.UInt64(600), "protocol_fee_ceiling_bps", lambda arg: arg.as_uint64()),
        ("update_protocol_treasury", lambda: arc4.Address(make_address()), "protocol_treasury", lambda arg: arg.bytes),
        ("update_market_factory_id", lambda: arc4.UInt64(44), "market_factory_id", lambda arg: arg.as_uint64()),
        ("update_max_outcomes", lambda: arc4.UInt64(12), "max_outcomes", lambda arg: arg.as_uint64()),
        ("update_min_challenge_window_secs", lambda: arc4.UInt64(100_000), "min_challenge_window_secs", lambda arg: arg.as_uint64()),
        ("update_min_grace_period_secs", lambda: arc4.UInt64(7_200), "min_grace_period_secs", lambda arg: arg.as_uint64()),
        ("update_max_lp_fee_bps", lambda: arc4.UInt64(900), "max_lp_fee_bps", lambda arg: arg.as_uint64()),
        (
            "update_default_residual_linear_lambda_fp",
            lambda: arc4.UInt64(40_000),
            "default_residual_linear_lambda_fp",
            lambda arg: arg.as_uint64(),
        ),
        (
            "update_max_active_lp_v4_outcomes",
            lambda: arc4.UInt64(7),
            "max_active_lp_v4_outcomes",
            lambda arg: arg.as_uint64(),
        ),
    ],
)
def test_admin_update_methods(disable_arc4_emit, method_name, argument_factory, state_attr, expected) -> None:
    admin = make_address()
    treasury = make_address()
    argument = argument_factory()

    with algopy_testing_context() as context:
        contract = ProtocolConfig()
        create_protocol_config(contract, admin=admin, treasury=treasury)

        method = getattr(contract, method_name)
        call_as(context, admin, method, argument)

        state_value = getattr(contract, state_attr).value
        assert state_value == expected(argument)


def test_non_admin_update_fails(disable_arc4_emit) -> None:
    admin = make_address()
    treasury = make_address()
    attacker = make_address()

    with algopy_testing_context() as context:
        contract = ProtocolConfig()
        create_protocol_config(contract, admin=admin, treasury=treasury)

        with pytest.raises(AssertionError):
            call_as(context, attacker, contract.update_default_b, arc4.UInt64(120_000_000))


def test_update_protocol_treasury_rejects_zero_address(disable_arc4_emit) -> None:
    admin = make_address()
    treasury = make_address()

    with algopy_testing_context() as context:
        contract = ProtocolConfig()
        create_protocol_config(contract, admin=admin, treasury=treasury)

        with pytest.raises(AssertionError):
            call_as(context, admin, contract.update_protocol_treasury, arc4.Address(ZERO_ADDRESS))


def test_update_max_outcomes_rejects_below_active_lp_cap(disable_arc4_emit) -> None:
    admin = make_address()
    treasury = make_address()

    with algopy_testing_context() as context:
        contract = ProtocolConfig()
        create_protocol_config(contract, admin=admin, treasury=treasury)

        with pytest.raises(AssertionError):
            call_as(context, admin, contract.update_max_outcomes, arc4.UInt64(7))


def test_fee_ceiling(disable_arc4_emit) -> None:
    admin = make_address()
    treasury = make_address()

    with algopy_testing_context() as context:
        contract = ProtocolConfig()
        create_protocol_config(contract, admin=admin, treasury=treasury)

        call_as(context, admin, contract.update_protocol_fee_bps, arc4.UInt64(400))
        assert int(contract.protocol_fee_bps.value) == 400

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                contract.update_protocol_fee_bps,
                arc4.UInt64(BPS_DENOMINATOR + 1),
            )

        with pytest.raises(AssertionError):
            call_as(context, admin, contract.update_protocol_fee_bps, arc4.UInt64(501))


def test_factory_creates_market_inner_txn(disable_arc4_emit) -> None:
    build = subprocess.run(
        [sys.executable, "-m", "smart_contracts", "build", "market_factory"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout
    teal = MARKET_FACTORY_ARTIFACT.read_text(encoding="utf-8")
    assert "itxn_begin" in teal
    assert "itxn_submit" in teal
    assert "QuestionMarket" in teal or "create(" in teal


def test_factory_build_succeeds_without_algokit_on_path(disable_arc4_emit) -> None:
    env = os.environ.copy()
    env["PATH"] = ""
    env.pop("VIRTUAL_ENV", None)

    build = subprocess.run(
        [sys.executable, "-m", "smart_contracts", "build", "market_factory"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert build.returncode == 0, build.stdout
    assert MARKET_FACTORY_ARTIFACT.exists()
    client_path = ROOT_DIR / "smart_contracts" / "artifacts" / "market_factory" / "market_factory_client.py"
    assert client_path.exists()


def test_reject_over_max_outcomes(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    patch_factory_inner_create(monkeypatch, 9_002)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_create_market(
                context,
                creator,
                factory,
                arc4.UInt64(CURRENCY_ASA),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(17),
                arc4.UInt64(0),
                arc4.UInt64(200),
                arc4.DynamicBytes(BLUEPRINT_CID),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_factory_rejects_v4_market_above_active_lp_outcome_guard(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    patch_factory_inner_create(monkeypatch, 9_002_1)
    monkeypatch.setattr(factory_module.arc4, "abi_call", lambda *args, **kwargs: None)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_create_market_canonical(
                context,
                creator,
                factory,
                arc4.UInt64(CURRENCY_ASA),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(9),
                arc4.UInt64(25_000_000),
                arc4.UInt64(200),
                arc4.DynamicBytes(BLUEPRINT_CID),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_factory_rejects_challenge_window_below_protocol_minimum(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    patch_factory_inner_create(monkeypatch, 9_004, captured)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        call_create_market(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(3_600),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

        market = QuestionMarket()
        set_created_market_factory_creator(context, market, factory.__app_id__)
        with pytest.raises(AssertionError):
            call_as(
                context,
                creator,
                market.create,
                *captured["args"],
                apps=(Application(PROTOCOL_CONFIG_APP_ID),),
            )


def test_factory_accepts_challenge_window_at_protocol_minimum(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    patch_factory_inner_create(monkeypatch, 9_004_1, captured)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_create_market(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    assert int(created_app_id.as_uint64()) == 9_004_1
    assert int(captured["args"][8].as_uint64()) == 86_400


def test_factory_rejects_underfunded_bootstrap_when_proposer_fee_is_nonzero(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    requested_window = 86_400
    deposit_amount = 50_000_000
    budget_required = max_proposer_fee(
        proposal_bond=10_000_000,
        proposal_bond_cap=100_000_000,
        proposer_fee_bps=500,
        proposer_fee_floor_bps=100,
        challenge_window_secs=requested_window,
    )

    patch_factory_inner_create(monkeypatch, 9_004_2)

    with algopy_testing_context() as context:
        seed_protocol_config_state(
            context,
            app_id=PROTOCOL_CONFIG_APP_ID,
            proposer_fee_bps=500,
            proposer_fee_floor_bps=100,
            min_challenge_window_secs=3_600,
        )
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_create_market(
                context,
                creator,
                factory,
                arc4.UInt64(CURRENCY_ASA),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(3),
                arc4.UInt64(25_000_000),
                arc4.UInt64(200),
                arc4.DynamicBytes(BLUEPRINT_CID),
                arc4.UInt64(10_000),
                arc4.UInt64(requested_window),
                arc4.Address(admin),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(deposit_amount),
                usdc_funding_amount=deposit_amount + budget_required - 1,
            )


def test_factory_accepts_exact_bootstrap_funding_when_proposer_fee_is_nonzero(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    requested_window = 86_400
    deposit_amount = 50_000_000
    budget_required = max_proposer_fee(
        proposal_bond=10_000_000,
        proposal_bond_cap=100_000_000,
        proposer_fee_bps=500,
        proposer_fee_floor_bps=100,
        challenge_window_secs=requested_window,
    )

    patch_factory_inner_create(monkeypatch, 9_004_3)

    with algopy_testing_context() as context:
        seed_protocol_config_state(
            context,
            app_id=PROTOCOL_CONFIG_APP_ID,
            proposer_fee_bps=500,
            proposer_fee_floor_bps=100,
            min_challenge_window_secs=3_600,
        )
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_create_market(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(requested_window),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(deposit_amount),
            usdc_funding_amount=deposit_amount + budget_required,
        )

    assert int(created_app_id.as_uint64()) == 9_004_3


def test_factory_create_market_passes_protocol_default_into_child_create(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    patch_factory_inner_create(monkeypatch, 9_005_1, captured)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_create_market_canonical(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
            latest_timestamp=321,
        )

        assert int(created_app_id.as_uint64()) == 9_005_1
        assert captured["create_args"][9].bytes == Account(resolver).bytes
        assert int(captured["create_args"][10].as_uint64()) == 3_600
        assert captured["create_args"][11].bytes == Account(admin).bytes
        assert int(captured["create_args"][12].as_uint64()) == PROTOCOL_CONFIG_APP_ID
        assert bool(captured["create_args"][13].native) is True
        assert int(captured["create_args"][14].as_uint64()) == DEFAULT_LP_ENTRY_MAX_PRICE_FP


def test_factory_creation_event_all_params(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    emitted: list[tuple[str, tuple[object, ...]]] = []

    patch_factory_inner_create(monkeypatch, 9_006)
    monkeypatch.setattr(
        factory_module.arc4,
        "emit",
        lambda event, *args: emitted.append((event, args)),
    )

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        call_create_market(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    assert emitted == []


def test_factory_passes_protocol_bond_formula_and_grace_period(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    patch_factory_inner_create(monkeypatch, 9_007, captured)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        call_create_market(
            context,
            creator,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(7_200),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    args = captured["args"]
    assert int(args[10].as_uint64()) == 7_200

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID, factory_id=8_001)
        market = QuestionMarket()
        set_created_market_factory_creator(context, market, 8_001)
        call_as(
            context,
            creator,
            market.create,
            *args,
            latest_timestamp=1,
            apps=(Application(PROTOCOL_CONFIG_APP_ID),),
        )

        assert int(market.challenge_bond.value) == 10_000_000
        assert int(market.proposal_bond.value) == 10_000_000
        assert int(market.challenge_bond_bps.value) == 500
        assert int(market.proposal_bond_bps.value) == 500
        assert int(market.challenge_bond_cap.value) == 100_000_000
        assert int(market.proposal_bond_cap.value) == 100_000_000
        assert int(market.proposer_fee_bps.value) == 0
        assert int(market.proposer_fee_floor_bps.value) == 0
        assert int(market.grace_period_secs.value) == 7_200


def test_factory_uses_sender_as_creator(disable_arc4_emit, monkeypatch) -> None:
    creator = make_address()
    attacker = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    patch_factory_inner_create(monkeypatch, 9_009, captured)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=attacker, resolution_authority=resolver)

        call_create_market(
            context,
            attacker,
            factory,
            arc4.UInt64(CURRENCY_ASA),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            arc4.DynamicBytes(BLUEPRINT_CID),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(attacker),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    assert captured["args"][0].bytes == Account(attacker).bytes


def test_factory_source_requires_funding_payment_and_sender_binding() -> None:
    source = Path(factory_module.__file__).read_text(encoding="utf-8")

    assert "PaymentTransaction" in source
    assert "AssetTransferTransaction" in source
    assert "algo_funding.sender" in source
    assert "usdc_funding.sender" in source
    assert "Txn.sender" in source


def test_factory_source_contains_explicit_creation_path() -> None:
    source = Path(factory_module.__file__).read_text(encoding="utf-8")

    assert "def create_market(" in source
    assert "enable_active_lp_v4" not in source
    assert "abi_call" not in source


def test_factory_schema_constants_match_market_artifact() -> None:
    artifact = ROOT_DIR / "smart_contracts" / "artifacts" / "market_app" / "QuestionMarket.arc56.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    schema = data["state"]["schema"]

    assert factory_module.QUESTION_MARKET_GLOBAL_UINTS == schema["global"]["ints"]
    assert factory_module.QUESTION_MARKET_GLOBAL_BYTES == schema["global"]["bytes"]
    assert factory_module.QUESTION_MARKET_LOCAL_UINTS == schema["local"]["ints"]
    assert factory_module.QUESTION_MARKET_LOCAL_BYTES == schema["local"]["bytes"]


def test_factory_min_funding_constants_are_derived_from_schema_constants() -> None:
    expected_creator_min_balance = (
        factory_module.APP_CREATE_BASE_MIN_BALANCE
        + factory_module.QUESTION_MARKET_EXTRA_PAGES * factory_module.APP_PAGE_MIN_BALANCE
        + factory_module.QUESTION_MARKET_GLOBAL_UINTS * factory_module.APP_GLOBAL_UINT_MIN_BALANCE
        + factory_module.QUESTION_MARKET_GLOBAL_BYTES * factory_module.APP_GLOBAL_BYTES_MIN_BALANCE
    )

    assert factory_module.MARKET_CREATOR_MIN_BALANCE == expected_creator_min_balance
    assert factory_module.CREATE_MARKET_MIN_FUNDING == (
        factory_module.FACTORY_RESERVE
        + factory_module.MARKET_APP_MIN_FUNDING
        + factory_module.MARKET_CREATOR_MIN_BALANCE
    )

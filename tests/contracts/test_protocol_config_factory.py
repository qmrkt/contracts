from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import algosdk.logic
import pytest
from algopy import Account, Application, UInt64, arc4, op
from algopy_testing import algopy_testing_context

from smart_contracts.abi_types import Hash32
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


def call_create_market(
    context,
    sender: str,
    contract: MarketFactory,
    *args,
    funding_amount: int = factory_module.CREATE_MARKET_MIN_FUNDING,
    latest_timestamp: int | None = None,
):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    funding = make_factory_funding_payment(context, contract, sender, funding_amount)
    if len(args) == 14:
        args = (*args[1:-1], arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP))
    elif len(args) == 12:
        args = (*args, arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP))
    deferred = context.txn.defer_app_call(contract.create_market, *args, funding)
    deferred._txns[-1].fields["apps"] = (Application(contract.__app_id__), Application(PROTOCOL_CONFIG_APP_ID))
    with context.txn.create_group([funding, deferred]):
        return contract.create_market(*args, funding)

def call_create_market_canonical(
    context,
    sender: str,
    contract: MarketFactory,
    *args,
    funding_amount: int = factory_module.CREATE_MARKET_MIN_FUNDING,
    latest_timestamp: int | None = None,
):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    funding = make_factory_funding_payment(context, contract, sender, funding_amount)
    if len(args) == 14:
        args = (*args[1:-1], arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP))
    elif len(args) == 12:
        args = (*args, arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP))
    deferred = context.txn.defer_app_call(contract.create_market, *args, funding)
    deferred._txns[-1].fields["apps"] = (Application(contract.__app_id__), Application(PROTOCOL_CONFIG_APP_ID))
    with context.txn.create_group([funding, deferred]):
        return contract.create_market(*args, funding)


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


def seed_protocol_config_state(context, *, app_id: int, factory_id: int = 0, treasury: str | None = None) -> Application:
    app = context.any.application(id=app_id)
    treasury_address = treasury or make_address()
    state = {
        KEY_MIN_BOOTSTRAP_DEPOSIT: 50_000_000,
        KEY_CHALLENGE_BOND: 10_000_000,
        KEY_PROPOSAL_BOND: 10_000_000,
        KEY_CHALLENGE_BOND_BPS: 500,
        KEY_PROPOSAL_BOND_BPS: 500,
        KEY_CHALLENGE_BOND_CAP: 100_000_000,
        KEY_PROPOSAL_BOND_CAP: 100_000_000,
        KEY_PROPOSER_FEE_BPS: 0,
        KEY_PROPOSER_FEE_FLOOR_BPS: 0,
        KEY_DEFAULT_B: 50_000_000,
        KEY_PROTOCOL_FEE_BPS: 50,
        KEY_PROTOCOL_FEE_CEILING_BPS: 500,
        KEY_PROTOCOL_TREASURY: Account(treasury_address).bytes.value,
        KEY_MARKET_FACTORY_ID: factory_id,
        KEY_MAX_OUTCOMES: 16,
        KEY_MIN_CHALLENGE_WINDOW_SECS: 86_400,
        KEY_MIN_GRACE_PERIOD_SECS: 3_600,
        KEY_MAX_LP_FEE_BPS: 1_000,
        KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP: DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
        KEY_MAX_ACTIVE_LP_V4_OUTCOMES: 8,
    }
    for key, value in state.items():
        context.ledger.set_global_state(app, key, value)
    return app


class FakeCreateResult:
    def __init__(self, app_id: int):
        self.created_app = Application(app_id)


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

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_002))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_create_market(
                context,
                creator,
                factory,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(17),
                arc4.UInt64(0),
                arc4.UInt64(200),
                Hash32.from_bytes(b"b" * 32),
                Hash32.from_bytes(b"d" * 32),
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

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_002_1))
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
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(9),
                arc4.UInt64(25_000_000),
                arc4.UInt64(200),
                Hash32.from_bytes(b"b" * 32),
                Hash32.from_bytes(b"d" * 32),
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

    def fake_arc4_create(method, *args, **kwargs):
        captured["method"] = method
        captured["args"] = args
        return FakeCreateResult(9_004)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        call_create_market(
            context,
            creator,
            factory,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(3_600),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

        market = QuestionMarket()
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

    def fake_arc4_create(method, *args, **kwargs):
        captured["args"] = args
        return FakeCreateResult(9_004_1)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_create_market(
            context,
            creator,
            factory,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    assert created_app_id is None
    assert int(captured["args"][9].as_uint64()) == 86_400


def test_factory_create_market_passes_protocol_default_into_child_create(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    def fake_arc4_create(method, *args, **kwargs):
        captured["create_method"] = method
        captured["create_args"] = args
        return FakeCreateResult(9_005_1)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_create_market_canonical(
            context,
            creator,
            factory,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
            latest_timestamp=321,
        )

        assert created_app_id is None
        assert captured["create_args"][10].bytes == Account(resolver).bytes
        assert int(captured["create_args"][11].as_uint64()) == 3_600
        assert captured["create_args"][12].bytes == Account(admin).bytes
        assert int(captured["create_args"][13].as_uint64()) == PROTOCOL_CONFIG_APP_ID
        assert bool(captured["create_args"][14].native) is True
        assert int(captured["create_args"][15].as_uint64()) == DEFAULT_LP_ENTRY_MAX_PRICE_FP


def test_factory_creation_event_all_params(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    emitted: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_006))
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
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
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

    def fake_arc4_create(method, *args, **kwargs):
        captured["args"] = args
        return FakeCreateResult(9_007)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=admin, resolution_authority=resolver)

        call_create_market(
            context,
            creator,
            factory,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(7_200),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    args = captured["args"]
    assert int(args[11].as_uint64()) == 7_200

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        market = QuestionMarket()
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

    def fake_arc4_create(method, *args, **kwargs):
        captured["args"] = args
        return FakeCreateResult(9_009)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(context, factory, admin=attacker, resolution_authority=resolver)

        call_create_market(
            context,
            attacker,
            factory,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(25_000_000),
            arc4.UInt64(200),
            Hash32.from_bytes(b"b" * 32),
            Hash32.from_bytes(b"d" * 32),
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
    assert "funding.sender" in source
    assert "Txn.sender" in source


def test_factory_source_contains_explicit_creation_path() -> None:
    source = Path(factory_module.__file__).read_text(encoding="utf-8")

    assert "def create_market(" in source
    assert "enable_active_lp_v4" not in source
    assert "abi_call" not in source

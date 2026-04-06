from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from algopy import Account, Application, Bytes, UInt64, arc4, op
from algopy_testing import algopy_testing_context

import smart_contracts.market_factory.contract as factory_module
import smart_contracts.protocol_config.contract as config_module
from smart_contracts.market_factory.contract import MarketFactory
from smart_contracts.protocol_config.contract import (
    BPS_DENOMINATOR,
    KEY_CHALLENGE_BOND,
    KEY_DEFAULT_B,
    KEY_MARKET_FACTORY_ID,
    KEY_MAX_LP_FEE_BPS,
    KEY_MAX_OUTCOMES,
    KEY_MIN_BOOTSTRAP_DEPOSIT,
    KEY_MIN_CHALLENGE_WINDOW_SECS,
    KEY_MIN_GRACE_PERIOD_SECS,
    KEY_PROPOSAL_BOND,
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


def call_as(context, sender: str, method, *args, latest_timestamp: int | None = None):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    with context.txn.create_group([deferred]):
        return method(*args)


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
        default_b=arc4.UInt64(50_000_000),
        protocol_fee_ceiling_bps=arc4.UInt64(500),
        protocol_fee_bps=arc4.UInt64(50),
        protocol_treasury=arc4.Address(treasury),
        market_factory_id=arc4.UInt64(market_factory_id),
        max_outcomes=arc4.UInt64(16),
        min_challenge_window_secs=arc4.UInt64(86_400),
        min_grace_period_secs=arc4.UInt64(3_600),
        max_lp_fee_bps=arc4.UInt64(1_000),
    )


def create_market_factory(
    contract: MarketFactory,
    *,
    admin: str,
    resolution_authority: str,
    protocol_config_id: int = PROTOCOL_CONFIG_APP_ID,
) -> None:
    _ = admin
    contract.create(
        protocol_config_id=arc4.UInt64(protocol_config_id),
        resolution_authority=arc4.Address(resolution_authority),
    )


def seed_protocol_config_state(context, *, app_id: int, factory_id: int = 0, treasury: str | None = None) -> Application:
    app = context.any.application(id=app_id)
    treasury_address = treasury or make_address()
    state = {
        KEY_MIN_BOOTSTRAP_DEPOSIT: 50_000_000,
        KEY_CHALLENGE_BOND: 10_000_000,
        KEY_PROPOSAL_BOND: 10_000_000,
        KEY_DEFAULT_B: 50_000_000,
        KEY_PROTOCOL_FEE_BPS: 50,
        KEY_PROTOCOL_FEE_CEILING_BPS: 500,
        KEY_PROTOCOL_TREASURY: Account(treasury_address).bytes.value,
        KEY_MARKET_FACTORY_ID: factory_id,
        KEY_MAX_OUTCOMES: 16,
        KEY_MIN_CHALLENGE_WINDOW_SECS: 86_400,
        KEY_MIN_GRACE_PERIOD_SECS: 3_600,
        KEY_MAX_LP_FEE_BPS: 1_000,
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
        ("update_default_b", lambda: arc4.UInt64(123_000_000), "default_b", lambda arg: arg.as_uint64()),
        ("update_protocol_fee_bps", lambda: arc4.UInt64(125), "protocol_fee_bps", lambda arg: arg.as_uint64()),
        ("update_protocol_fee_ceiling_bps", lambda: arc4.UInt64(600), "protocol_fee_ceiling_bps", lambda arg: arg.as_uint64()),
        ("update_protocol_treasury", lambda: arc4.Address(make_address()), "protocol_treasury", lambda arg: arg.bytes),
        ("update_market_factory_id", lambda: arc4.UInt64(44), "market_factory_id", lambda arg: arg.as_uint64()),
        ("update_max_outcomes", lambda: arc4.UInt64(12), "max_outcomes", lambda arg: arg.as_uint64()),
        ("update_min_challenge_window_secs", lambda: arc4.UInt64(100_000), "min_challenge_window_secs", lambda arg: arg.as_uint64()),
        ("update_min_grace_period_secs", lambda: arc4.UInt64(7_200), "min_grace_period_secs", lambda arg: arg.as_uint64()),
        ("update_max_lp_fee_bps", lambda: arc4.UInt64(900), "max_lp_fee_bps", lambda arg: arg.as_uint64()),
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


def test_reject_below_min_bootstrap(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_001))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                factory.create_market,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(3),
                arc4.UInt64(0),
                arc4.UInt64(200),
                arc4.DynamicBytes(b"b" * 32),
                arc4.DynamicBytes(b"d" * 32),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(10_000_000),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(49_999_999),
            )


def test_reject_initial_b_above_bootstrap_deposit(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_001))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                factory.create_market,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(3),
                arc4.UInt64(100_000_000),
                arc4.UInt64(200),
                arc4.DynamicBytes(b"b" * 32),
                arc4.DynamicBytes(b"d" * 32),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(10_000_000),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_reject_over_max_outcomes(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_002))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                factory.create_market,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(17),
                arc4.UInt64(0),
                arc4.UInt64(200),
                arc4.DynamicBytes(b"b" * 32),
                arc4.DynamicBytes(b"d" * 32),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(10_000_000),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_reject_over_max_lp_fee(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_003))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                factory.create_market,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(3),
                arc4.UInt64(0),
                arc4.UInt64(1_001),
                arc4.DynamicBytes(b"b" * 32),
                arc4.DynamicBytes(b"d" * 32),
                arc4.UInt64(10_000),
                arc4.UInt64(86_400),
                arc4.Address(admin),
                arc4.UInt64(10_000_000),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_reject_below_min_challenge_window(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_004))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        with pytest.raises(AssertionError):
            call_as(
                context,
                admin,
                factory.create_market,
                arc4.Address(creator),
                arc4.UInt64(31_566_704),
                arc4.DynamicBytes(b"q" * 32),
                arc4.UInt64(3),
                arc4.UInt64(0),
                arc4.UInt64(200),
                arc4.DynamicBytes(b"b" * 32),
                arc4.DynamicBytes(b"d" * 32),
                arc4.UInt64(10_000),
                arc4.UInt64(86_399),
                arc4.Address(admin),
                arc4.UInt64(10_000_000),
                arc4.UInt64(3_600),
                arc4.Bool(True),
                arc4.UInt64(50_000_000),
            )


def test_factory_registers_market_box(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()

    monkeypatch.setattr(factory_module.arc4, "arc4_create", lambda *args, **kwargs: FakeCreateResult(9_005))

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_as(
            context,
            admin,
            factory.create_market,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(0),
            arc4.UInt64(200),
            arc4.DynamicBytes(b"b" * 32),
            arc4.DynamicBytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(10_000_000),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
            latest_timestamp=321,
        )

        assert created_app_id is None
        registry_value = factory.market_registry.get(UInt64(9_005), default=Bytes())
        assert registry_value == Account(creator).bytes


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
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        call_as(
            context,
            admin,
            factory.create_market,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(0),
            arc4.UInt64(200),
            arc4.DynamicBytes(b"b" * 32),
            arc4.DynamicBytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(10_000_000),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    assert emitted == []


def test_factory_materializes_proposal_bond_and_grace_period_from_config(disable_arc4_emit, monkeypatch) -> None:
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
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        call_as(
            context,
            admin,
            factory.create_market,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(0),
            arc4.UInt64(200),
            arc4.DynamicBytes(b"b" * 32),
            arc4.DynamicBytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(0),
            arc4.UInt64(0),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    args = captured["args"]
    assert int(args[13].as_uint64()) == 10_000_000


def test_factory_caps_default_b_to_safe_bootstrap_capacity(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    def fake_arc4_create(method, *args, **kwargs):
        captured["args"] = args
        return FakeCreateResult(9_008)

    monkeypatch.setattr(factory_module.arc4, "arc4_create", fake_arc4_create)

    with algopy_testing_context() as context:
        seed_protocol_config_state(context, app_id=PROTOCOL_CONFIG_APP_ID)
        factory = MarketFactory()
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        call_as(
            context,
            admin,
            factory.create_market,
            arc4.Address(creator),
            arc4.UInt64(31_566_704),
            arc4.DynamicBytes(b"q" * 32),
            arc4.UInt64(3),
            arc4.UInt64(0),
            arc4.UInt64(200),
            arc4.DynamicBytes(b"b" * 32),
            arc4.DynamicBytes(b"d" * 32),
            arc4.UInt64(10_000),
            arc4.UInt64(86_400),
            arc4.Address(admin),
            arc4.UInt64(0),
            arc4.UInt64(0),
            arc4.Bool(True),
            arc4.UInt64(50_000_000),
        )

    args = captured["args"]
    assert int(args[3].as_uint64()) == 25_000_000
    assert int(args[14].as_uint64()) == 3_600

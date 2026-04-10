from __future__ import annotations

import algosdk.logic

from algopy import Account, Application, Asset, UInt64, arc4
from algopy_testing import algopy_testing_context

from smart_contracts.abi_types import Hash32
import smart_contracts.market_factory.contract as factory_module
import smart_contracts.market_app.contract as market_app_module
from smart_contracts.lmsr_math import lmsr_prices as reference_lmsr_prices
from smart_contracts.market_app.contract import (
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    STATUS_ACTIVE,
    STATUS_RESOLVED,
)
from smart_contracts.market_factory.contract import MarketFactory
from tests.contracts.test_protocol_config_factory import (
    PROTOCOL_CONFIG_APP_ID,
    call_as,
    call_create_market,
    call_create_market_canonical,
    create_market_factory,
    disable_arc4_emit,
    make_address,
    seed_protocol_config_state,
)

CURRENCY_ASA = 31_566_704


class FakeCreateResult:
    def __init__(self, app_id: int):
        self.created_app = Application(app_id)


def get_app_address(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_usdc_payment(context, contract: QuestionMarket, sender: str, amount: int):
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(CURRENCY_ASA),
        asset_amount=UInt64(amount),
    )


def test_factory_created_market_passes_c2_lifecycle(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    buyer = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    monkeypatch.setattr(market_app_module.arc4, "emit", lambda *args, **kwargs: None)

    def fake_arc4_create(method, *args, **kwargs):
        captured["method"] = method
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeCreateResult(9_999)

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
            arc4.UInt64(CURRENCY_ASA),
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
            latest_timestamp=1,
        )

        assert created_app_id is None
        assert captured["method"] is QuestionMarket.create

        # Create market from captured factory args
        market = QuestionMarket()
        call_as(
            context,
            creator,
            market.create,
            *captured["args"],
            latest_timestamp=1,
            apps=(Application(PROTOCOL_CONFIG_APP_ID),),
        )

        assert int(market.protocol_config_id.value) == PROTOCOL_CONFIG_APP_ID
        assert market.resolution_authority.value == Account(resolver).bytes
        assert market.protocol_treasury.value == Account(admin).bytes
        assert int(market.challenge_bond.value) == 10_000_000
        assert int(market.challenge_bond_bps.value) == 500
        assert int(market.challenge_bond_cap.value) == 100_000_000
        assert int(market.protocol_fee_bps.value) == 50

        # Store blueprints and bootstrap with payment
        call_as(context, creator, market.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
        call_as(context, creator, market.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

        payment = make_usdc_payment(context, market, creator, 200_000_000)
        call_as(context, creator, market.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=2)
        assert int(market.status.value) == STATUS_ACTIVE

        # Buy with payment
        buy_payment = make_usdc_payment(context, market, buyer, 10_000_000)
        call_as(
            context,
            buyer,
            market.buy,
            arc4.UInt64(0),
            arc4.UInt64(market_app_module.SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        # Resolution lifecycle
        call_as(context, buyer, market.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, market, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            market.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=10_001,
        )
        call_as(context, buyer, market.finalize_resolution, latest_timestamp=96_402)
        call_as(
            context,
            buyer,
            market.claim,
            arc4.UInt64(0),
            arc4.UInt64(market_app_module.SHARE_UNIT),
            latest_timestamp=96_403,
        )

        assert int(market.status.value) == STATUS_RESOLVED
        assert int(market.pool_balance.value) >= 0


def test_factory_created_market_bootstraps_and_enters_active_lp(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    buyer = make_address()
    resolver = make_address()
    lp2 = make_address()
    captured: dict[str, object] = {}

    monkeypatch.setattr(market_app_module.arc4, "emit", lambda *args, **kwargs: None)

    def fake_arc4_create(method, *args, **kwargs):
        captured["create_method"] = method
        captured["create_args"] = args
        return FakeCreateResult(10_001)

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
            arc4.UInt64(CURRENCY_ASA),
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
            latest_timestamp=1,
        )

        assert created_app_id is None
        assert captured["create_method"] is QuestionMarket.create
        assert captured["create_args"][10].bytes == Account(resolver).bytes
        assert int(captured["create_args"][13].as_uint64()) == PROTOCOL_CONFIG_APP_ID
        assert bool(captured["create_args"][14].native) is True
        assert int(captured["create_args"][15].as_uint64()) == DEFAULT_LP_ENTRY_MAX_PRICE_FP

        market = QuestionMarket()
        call_as(
            context,
            creator,
            market.create,
            *captured["create_args"],
            latest_timestamp=1,
            apps=(Application(PROTOCOL_CONFIG_APP_ID),),
        )

        assert int(market.residual_linear_lambda_fp.value) == DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP
        assert int(market.lp_entry_max_price_fp.value) == DEFAULT_LP_ENTRY_MAX_PRICE_FP

        call_as(context, creator, market.store_main_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))
        call_as(context, creator, market.store_dispute_blueprint, arc4.DynamicBytes(b'{"nodes":[],"edges":[]}'))

        payment = make_usdc_payment(context, market, creator, 200_000_000)
        call_as(context, creator, market.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=2)
        assert int(market.status.value) == STATUS_ACTIVE
        assert int(market.lp_shares_total.value) == int(market.b.value)

        buy_payment = make_usdc_payment(context, market, buyer, 10_000_000)
        call_as(
            context,
            buyer,
            market.buy,
            arc4.UInt64(1),
            arc4.UInt64(market_app_module.SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        prices_before = reference_lmsr_prices(
            [int(market.outcome_quantities_box.get(UInt64(i), default=UInt64(0))) for i in range(3)],
            int(market.b.value),
        )
        lp_payment = make_usdc_payment(context, market, lp2, 200_000_000)
        call_as(
            context,
            lp2,
            market.enter_lp_active,
            arc4.UInt64(25_000_000),
            arc4.UInt64(200_000_000),
            arc4.DynamicArray[arc4.UInt64](*(arc4.UInt64(value) for value in prices_before)),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=5_001,
        )

        prices_after = reference_lmsr_prices(
            [int(market.outcome_quantities_box.get(UInt64(i), default=UInt64(0))) for i in range(3)],
            int(market.b.value),
        )
        assert max(abs(before - after) for before, after in zip(prices_before, prices_after)) <= 2

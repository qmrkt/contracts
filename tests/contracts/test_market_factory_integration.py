from __future__ import annotations

import algosdk.logic

from algopy import Account, Application, Asset, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_factory.contract as factory_module
import smart_contracts.market_app.contract as market_app_module
from smart_contracts.market_app.contract import QuestionMarket, STATUS_ACTIVE, STATUS_RESOLVED
from smart_contracts.market_factory.contract import MarketFactory
from tests.contracts.test_protocol_config_factory import (
    PROTOCOL_CONFIG_APP_ID,
    call_as,
    create_market_factory,
    disable_arc4_emit,
    make_address,
    seed_protocol_config_state,
)

CURRENCY_ASA = 31_566_704
OUTCOME_ASA_IDS = [1000, 1001, 1002]


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
        create_market_factory(factory, admin=admin, resolution_authority=resolver)

        created_app_id = call_as(
            context,
            admin,
            factory.create_market,
            arc4.Address(creator),
            arc4.UInt64(CURRENCY_ASA),
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
            latest_timestamp=1,
        )

        assert created_app_id is None
        assert captured["method"] is QuestionMarket.create

        # Create market from captured factory args
        market = QuestionMarket()
        market.create(*captured["args"])

        assert int(market.protocol_config_id.value) == PROTOCOL_CONFIG_APP_ID
        assert int(market.factory_id.value) > 0
        assert market.resolution_authority.value == Account(resolver).bytes
        assert int(market.challenge_bond.value) == 10_000_000
        assert int(market.protocol_fee_bps.value) == 50

        # Register outcome ASAs, store blueprints, and bootstrap with payment
        for idx, asa_id in enumerate(OUTCOME_ASA_IDS):
            call_as(context, creator, market.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))
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

from __future__ import annotations

import algosdk.logic

from algopy import Account, Application, Asset, Global, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_factory.contract as factory_module
import smart_contracts.market_app.contract as market_app_module
from smart_contracts.lmsr_math import lmsr_prices as reference_lmsr_prices
from smart_contracts.market_app.contract import (
    COST_BOX_MBR,
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    FEE_BOX_MBR,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    SHARE_BOX_MBR,
    STATUS_ACTIVE,
    STATUS_RESOLVED,
)
from smart_contracts.market_factory.contract import MarketFactory
from tests.contracts.test_protocol_config_factory import (
    BLUEPRINT_CID,
    PROTOCOL_CONFIG_APP_ID,
    call_as,
    call_create_market,
    call_create_market_canonical,
    create_market_factory,
    disable_arc4_emit,
    max_proposer_fee,
    make_address,
    patch_factory_inner_create,
    set_created_market_factory_creator,
    seed_protocol_config_state,
)

CURRENCY_ASA = 31_566_704


def get_app_address(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_usdc_payment(context, contract: QuestionMarket, sender: str, amount: int):
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(CURRENCY_ASA),
        asset_amount=UInt64(amount),
    )


def make_mbr_payment(context, contract: QuestionMarket, sender: str, amount: int):
    """ALGO Payment funding MBR top-up for a box-creating method call."""
    zero = Global.zero_address
    return context.any.txn.payment(
        sender=Account(sender),
        receiver=Account(get_app_address(contract)),
        amount=UInt64(amount),
        rekey_to=zero,
        close_remainder_to=zero,
    )


def read_quantities(contract: QuestionMarket) -> list[int]:
    return [int(contract._get_quantity(UInt64(i))) for i in range(int(contract.num_outcomes.value))]


def test_factory_created_market_passes_c2_lifecycle(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    buyer = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}

    monkeypatch.setattr(market_app_module.arc4, "emit", lambda *args, **kwargs: None)

    patch_factory_inner_create(monkeypatch, 9_999, captured)

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
            latest_timestamp=1,
        )

        assert int(created_app_id.as_uint64()) == 9_999
        assert captured["method"] is factory_module.MarketStub.create

        # Create market from captured factory args
        market = QuestionMarket()
        set_created_market_factory_creator(context, market, factory.__app_id__)
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
            make_mbr_payment(context, market, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
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

    patch_factory_inner_create(monkeypatch, 10_001, captured)

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
            latest_timestamp=1,
        )

        assert int(created_app_id.as_uint64()) == 10_001
        assert captured["create_method"] is factory_module.MarketStub.create
        assert captured["create_args"][9].bytes == Account(resolver).bytes
        assert int(captured["create_args"][12].as_uint64()) == PROTOCOL_CONFIG_APP_ID
        assert bool(captured["create_args"][13].native) is True
        assert int(captured["create_args"][14].as_uint64()) == DEFAULT_LP_ENTRY_MAX_PRICE_FP

        market = QuestionMarket()
        set_created_market_factory_creator(context, market, factory.__app_id__)
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
            make_mbr_payment(context, market, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        prices_before = reference_lmsr_prices(
            read_quantities(market),
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
            read_quantities(market),
            int(market.b.value),
        )
        assert max(abs(before - after) for before, after in zip(prices_before, prices_after)) <= 2


def test_factory_created_market_handles_nonzero_proposer_fee_budget(disable_arc4_emit, monkeypatch) -> None:
    admin = make_address()
    creator = make_address()
    resolver = make_address()
    captured: dict[str, object] = {}
    requested_window = 86_400
    deposit_amount = 50_000_000
    budget_required = max_proposer_fee(
        proposal_bond=10_000_000,
        proposal_bond_cap=100_000_000,
        proposer_fee_bps=500,
        proposer_fee_floor_bps=100,
        challenge_window_secs=requested_window,
    )

    monkeypatch.setattr(market_app_module.arc4, "emit", lambda *args, **kwargs: None)

    patch_factory_inner_create(monkeypatch, 10_002, captured)

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
            arc4.UInt64(requested_window),
            arc4.Address(admin),
            arc4.UInt64(3_600),
            arc4.Bool(True),
            arc4.UInt64(deposit_amount),
            usdc_funding_amount=deposit_amount + budget_required,
            latest_timestamp=1,
        )

        assert int(created_app_id.as_uint64()) == 10_002

        market = QuestionMarket()
        set_created_market_factory_creator(context, market, factory.__app_id__)
        call_as(
            context,
            creator,
            market.create,
            *captured["create_args"],
            latest_timestamp=1,
            apps=(Application(PROTOCOL_CONFIG_APP_ID),),
        )

        payment = make_usdc_payment(context, market, creator, deposit_amount + budget_required)
        call_as(context, creator, market.bootstrap, arc4.UInt64(deposit_amount), payment, latest_timestamp=2)

        assert int(market.status.value) == STATUS_ACTIVE
        assert int(market.resolution_budget_balance.value) == budget_required

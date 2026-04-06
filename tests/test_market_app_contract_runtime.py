import algosdk.account
import algosdk.logic
import pytest
from algopy import Account, Application, Asset, Bytes, UInt64, arc4, op
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    BOX_KEY_USER_COST_BASIS,
    BOX_KEY_USER_FEES,
    BOX_KEY_USER_SHARES,
    MARKET_CONTRACT_VERSION,
    MAX_COMMENT_BYTES,
    QuestionMarket,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    ZERO_ADDRESS_BYTES,
)
from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.model import MarketAppModel
from smart_contracts.protocol_config.contract import KEY_PROTOCOL_TREASURY

CURRENCY_ASA = 31_566_704
OUTCOME_ASA_IDS = [1000, 1001, 1002]
PROTOCOL_CONFIG_APP_ID = 7_001
LARGE_ACTIVE_POOL = 100_000_000_000
LARGE_PROVIDE_DEPOSIT = 18_446_744_073_710
LARGE_CLAIM_POOL = 18_446_744_073_710


def make_address() -> str:
    return algosdk.account.generate_account()[1]


def make_model(*, creator: str, resolver: str, deadline: int = 10_000, cancellable: bool = True) -> MarketAppModel:
    return MarketAppModel(
        creator=creator,
        currency_asa=CURRENCY_ASA,
        outcome_asa_ids=OUTCOME_ASA_IDS,
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=deadline,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority=resolver,
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=cancellable,
    )


def create_contract(
    context,
    contract: QuestionMarket,
    *,
    creator: str,
    resolver: str,
    deadline: int = 10_000,
    num_outcomes: int = 3,
    initial_b: int = 100_000_000,
    cancellable: bool = True,
) -> None:
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(num_outcomes),
        initial_b=arc4.UInt64(initial_b),
        lp_fee_bps=arc4.UInt64(200),
        protocol_fee_bps=arc4.UInt64(50),
        deadline=arc4.UInt64(deadline),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        main_blueprint_hash=arc4.DynamicBytes(b"b" * 32),
        dispute_blueprint_hash=arc4.DynamicBytes(b"d" * 32),
        challenge_window_secs=arc4.UInt64(86_400),
        resolution_authority=arc4.Address(resolver),
        challenge_bond=arc4.UInt64(10_000_000),
        proposal_bond=arc4.UInt64(10_000_000),
        grace_period_secs=arc4.UInt64(3_600),
        market_admin=arc4.Address(creator),
        protocol_config_id=arc4.UInt64(PROTOCOL_CONFIG_APP_ID),
        factory_id=arc4.UInt64(88),
        cancellable=arc4.Bool(cancellable),
    )
    context.ledger.patch_global_fields(latest_timestamp=1)
    context._default_sender = Account(creator)
    deferred = context.txn.defer_app_call(contract.create, **args)
    with context.txn.create_group([deferred]):
        contract.create(**args)


def get_app_address(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def make_usdc_payment(context, contract: QuestionMarket, sender: str, amount: int):
    """Create an ASA transfer transaction for USDC to the app."""
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(CURRENCY_ASA),
        asset_amount=UInt64(amount),
    )


def make_asa_payment(context, contract: QuestionMarket, sender: str, asa_id: int, amount: int):
    """Create an ASA transfer transaction for an outcome ASA."""
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(get_app_address(contract)),
        xfer_asset=Asset(asa_id),
        asset_amount=UInt64(amount),
    )


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


SAMPLE_RESOLUTION_LOGIC = b'{"nodes":[{"id":"submit","type":"submit_result"}],"edges":[]}'


def register_outcome_asas(context, contract, creator, outcome_asa_ids: list[int] | None = None):
    """Register outcome ASAs on a CREATED contract."""
    asa_ids = outcome_asa_ids or OUTCOME_ASA_IDS
    for idx, asa_id in enumerate(asa_ids):
        call_as(context, creator, contract.register_outcome_asa, arc4.UInt64(idx), Asset(asa_id))


def store_blueprints(context, contract, creator):
    """Store main and dispute blueprints on a CREATED contract."""
    call_as(context, creator, contract.store_main_blueprint, arc4.DynamicBytes(SAMPLE_RESOLUTION_LOGIC))
    call_as(context, creator, contract.store_dispute_blueprint, arc4.DynamicBytes(SAMPLE_RESOLUTION_LOGIC))


def initialize_market(context, contract, creator):
    """Initialize a CREATED contract using the atomic market setup helper."""
    call_as(context, creator, contract.initialize)


def seed_protocol_config_state(context, *, admin: str, treasury: str):
    app = context.any.application(id=PROTOCOL_CONFIG_APP_ID)
    context.ledger.set_global_state(app, b"admin", Account(admin).bytes.value)
    context.ledger.set_global_state(app, KEY_PROTOCOL_TREASURY, Account(treasury).bytes.value)
    return app


def contract_q(contract: QuestionMarket) -> list[int]:
    return [int(contract.outcome_quantities_box.get(UInt64(idx), default=UInt64(0))) for idx in range(int(contract.num_outcomes.value))]


def contract_user_shares(contract: QuestionMarket, sender: str, outcome_index: int) -> int:
    key = op.concat(Account(sender).bytes, op.itob(UInt64(outcome_index)))
    return int(contract.user_outcome_shares_box.get(key, default=UInt64(0)))


def contract_claimable_fees(contract: QuestionMarket, sender: str) -> int:
    return int(contract.user_claimable_fees_box.get(Account(sender).bytes, default=UInt64(0)))


def contract_user_cost_basis(contract: QuestionMarket, sender: str, outcome_index: int) -> int:
    key = op.concat(Account(sender).bytes, op.itob(UInt64(outcome_index)))
    return int(contract.user_cost_basis_box.get(key, default=UInt64(0)))


def contract_pending_payout(contract: QuestionMarket, sender: str) -> int:
    return int(contract.pending_payouts_box.get(Account(sender).bytes, default=UInt64(0)))


def ensure_currency_asset(context) -> None:
    if not context.ledger.asset_exists(CURRENCY_ASA):
        context.any.asset(asset_id=CURRENCY_ASA)


def seed_usdc_balance(context, address: str, amount: int) -> None:
    ensure_currency_asset(context)
    context.ledger.update_asset_holdings(CURRENCY_ASA, address, balance=amount)


def remove_usdc_opt_in(context, address: str) -> None:
    account_data = context.ledger._account_data.get(address)
    if not account_data:
        return
    account_data.opted_assets.pop(CURRENCY_ASA, None)


def usdc_balance(context, address: str) -> int:
    account_data = context.ledger._account_data.get(address)
    if not account_data:
        return 0
    holding = account_data.opted_assets.get(CURRENCY_ASA)
    if not holding:
        return 0
    return int(holding.balance)


def last_inner_asset_transfers(context) -> list:
    group = context.txn.last_group
    return [group.get_itxn_group(index).asset_transfer(0) for index in range(len(group.itxn_groups))]


@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *args, **kwargs: None)


def test_contract_create_and_bootstrap_persist_state(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)

        assert int(contract.status.value) == STATUS_CREATED
        assert int(contract.contract_version.value) == MARKET_CONTRACT_VERSION
        assert int(contract.num_outcomes.value) == 3
        assert int(contract.b.value) == 100_000_000
        assert contract.creator.value == Account(creator).bytes
        assert contract_q(contract) == [0, 0, 0]

        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)
        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000
        assert int(contract.lp_shares_total.value) == 200_000_000
        assert int(contract.lp_shares[Account(creator)]) == 200_000_000


def test_contract_post_comment_emits_for_lp_and_holder(monkeypatch) -> None:
    creator = make_address()
    resolver = make_address()
    holder = make_address()
    captured: list[tuple[str, list[object]]] = []

    def capture_emit(signature, *values):
        rendered = [value.as_uint64() if hasattr(value, "as_uint64") else value for value in values]
        captured.append((signature, rendered))

    monkeypatch.setattr(contract_module.arc4, "emit", capture_emit)

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        call_as(context, creator, contract.post_comment, arc4.String("lp comment"), latest_timestamp=2)

        buy_payment = make_usdc_payment(context, contract, holder, 10_000_000)
        call_as(
            context,
            holder,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        call_as(context, holder, contract.post_comment, arc4.String("holder comment"), latest_timestamp=5_001)

        comment_events = [entry for entry in captured if entry[0] == "CommentPosted(string)"]
        assert comment_events == [
            ("CommentPosted(string)", ["lp comment"]),
            ("CommentPosted(string)", ["holder comment"]),
        ]


def test_contract_bootstrap_rejects_underfunded_sixteen_outcome_market(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    outcome_asa_ids = [1000 + i for i in range(16)]

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            num_outcomes=16,
            initial_b=50_000_000,
        )
        register_outcome_asas(context, contract, creator, outcome_asa_ids)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 100_000_000)
        with pytest.raises(AssertionError):
            call_as(context, creator, contract.bootstrap, arc4.UInt64(100_000_000), payment, latest_timestamp=1)


def test_contract_post_comment_holder_path_skips_lp_local_state(disable_arc4_emit, monkeypatch) -> None:
    creator = make_address()
    resolver = make_address()
    holder = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, holder, 10_000_000)
        call_as(
            context,
            holder,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        def fail_lp_lookup():
            raise AssertionError("lp state lookup should not run for holder comments")

        monkeypatch.setattr(contract, "_get_lp_shares", fail_lp_lookup)

        call_as(context, holder, contract.post_comment, arc4.String("holder comment"), latest_timestamp=5_001)


def test_contract_post_comment_enforces_participant_and_size_rules(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    outsider = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        with pytest.raises(AssertionError):
            call_as(context, outsider, contract.post_comment, arc4.String("hello"), latest_timestamp=2)

        with pytest.raises(AssertionError):
            call_as(context, creator, contract.post_comment, arc4.String(""), latest_timestamp=2)

        exact_limit = "a" * MAX_COMMENT_BYTES
        call_as(context, creator, contract.post_comment, arc4.String(exact_limit), latest_timestamp=2)

        with pytest.raises(AssertionError):
            call_as(
                context,
                creator,
                contract.post_comment,
                arc4.String("a" * (MAX_COMMENT_BYTES + 1)),
                latest_timestamp=2,
            )


def test_contract_initialize_then_bootstrap_persist_state(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)

        initialize_market(context, contract, creator)
        store_blueprints(context, contract, creator)

        assert int(contract.status.value) == STATUS_CREATED
        assert int(contract.outcome_asa_ids_box[UInt64(0)]) > 0
        assert int(contract.outcome_asa_ids_box[UInt64(1)]) > 0
        assert bool(contract.main_blueprint_box)
        assert bool(contract.dispute_blueprint_box)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000
        assert int(contract.lp_shares_total.value) == 200_000_000
        assert int(contract.lp_shares[Account(creator)]) == 200_000_000


def test_contract_initialize_rejects_non_creator(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    attacker = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)

        with pytest.raises(AssertionError):
            call_as(context, attacker, contract.initialize)


def test_contract_trade_and_liquidity_paths_match_model(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000

        # Buy
        pre_buy_pool = int(contract.pool_balance.value)
        buy_payment = make_usdc_payment(context, contract, buyer, 10_000_000)
        buy_result = call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        post_buy_pool = int(contract.pool_balance.value)
        assert post_buy_pool > pre_buy_pool
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert contract_user_cost_basis(contract, buyer, 1) > 0
        assert buy_result is None

        # Sell
        pre_sell_pool = int(contract.pool_balance.value)
        sell_payment = make_asa_payment(context, contract, buyer, OUTCOME_ASA_IDS[1], SHARE_UNIT)
        sell_result = call_as(
            context,
            buyer,
            contract.sell,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(1),
            sell_payment,
            latest_timestamp=5_001,
        )
        post_sell_pool = int(contract.pool_balance.value)
        assert post_sell_pool < pre_sell_pool
        assert contract_user_shares(contract, buyer, 1) == 0
        assert contract_user_cost_basis(contract, buyer, 1) == 0
        assert sell_result is None

        # Provide liquidity
        prices_before = lmsr_prices(contract_q(contract), int(contract.b.value))
        creator_lp_before = int(contract.lp_shares[Account(creator)])
        lp_payment = make_usdc_payment(context, contract, lp2, 50_000_000)
        call_as(context, lp2, contract.provide_liq, arc4.UInt64(50_000_000), lp_payment, latest_timestamp=6_000)
        prices_after_provide = lmsr_prices(contract_q(contract), int(contract.b.value))
        assert all(abs(a - b) <= 1 for a, b in zip(prices_before, prices_after_provide))
        assert int(contract.lp_shares[Account(lp2)]) > 0
        assert int(contract.pool_balance.value) > post_sell_pool

        # Withdraw liquidity
        prices_before_withdraw = lmsr_prices(contract_q(contract), int(contract.b.value))
        call_as(context, creator, contract.withdraw_liq, arc4.UInt64(20_000_000), latest_timestamp=6_100)
        prices_after_withdraw = lmsr_prices(contract_q(contract), int(contract.b.value))
        assert all(abs(a - b) <= 1 for a, b in zip(prices_before_withdraw, prices_after_withdraw))
        assert int(contract.lp_shares[Account(creator)]) == creator_lp_before - 20_000_000
        assert contract_claimable_fees(contract, creator) >= 0
        assert contract_claimable_fees(contract, lp2) >= 0


def test_contract_buy_refunds_surplus_payment(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)
        seed_usdc_balance(context, buyer, 25_000_000)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        pool_before = int(contract.pool_balance.value)
        lp_fee_before = int(contract.lp_fee_balance.value)
        protocol_fee_before = int(contract.protocol_fee_balance.value)

        buy_payment = make_usdc_payment(context, contract, buyer, 10_000_000)
        buy_result = call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        retained = (int(contract.pool_balance.value) - pool_before) + (int(contract.lp_fee_balance.value) - lp_fee_before) + (int(contract.protocol_fee_balance.value) - protocol_fee_before)
        transfers = last_inner_asset_transfers(context)

        assert retained > 0
        assert retained < 10_000_000
        assert len(transfers) == 2
        assert int(transfers[0].xfer_asset.id) == OUTCOME_ASA_IDS[1]
        assert int(transfers[0].asset_amount) == SHARE_UNIT
        assert int(transfers[1].xfer_asset.id) == CURRENCY_ASA
        assert int(transfers[1].asset_amount) == 10_000_000 - retained
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert buy_result is None


def test_contract_multi_share_buy_and_sell_single_call_paths(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        buy_shares = 3 * SHARE_UNIT
        buy_payment = make_usdc_payment(context, contract, buyer, 50_000_000)
        buy_result = call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(buy_shares),
            arc4.UInt64(50_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        assert buy_result is None
        assert contract_user_shares(contract, buyer, 1) == buy_shares
        assert contract_q(contract)[1] == buy_shares

        sell_shares = 2 * SHARE_UNIT
        sell_payment = make_asa_payment(context, contract, buyer, OUTCOME_ASA_IDS[1], sell_shares)
        sell_result = call_as(
            context,
            buyer,
            contract.sell,
            arc4.UInt64(1),
            arc4.UInt64(sell_shares),
            arc4.UInt64(1),
            sell_payment,
            latest_timestamp=5_001,
        )

        assert sell_result is None
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert contract_user_cost_basis(contract, buyer, 1) > 0
        assert contract_q(contract)[1] == SHARE_UNIT


def test_contract_sell_rejects_outcome_over_transfer(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    seller = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        held_shares = 3 * SHARE_UNIT
        buy_payment = make_usdc_payment(context, contract, seller, 50_000_000)
        call_as(
            context,
            seller,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(held_shares),
            arc4.UInt64(50_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        sell_shares = 2 * SHARE_UNIT
        over_transfer_payment = make_asa_payment(context, contract, seller, OUTCOME_ASA_IDS[1], held_shares)
        with pytest.raises(AssertionError):
            call_as(
                context,
                seller,
                contract.sell,
                arc4.UInt64(1),
                arc4.UInt64(sell_shares),
                arc4.UInt64(1),
                over_transfer_payment,
                latest_timestamp=5_001,
            )

        assert contract_user_shares(contract, seller, 1) == held_shares
        assert contract_q(contract)[1] == held_shares


def test_contract_finalize_resolution_credits_pending_payout_even_without_receiver_opt_in(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)
        seed_usdc_balance(context, resolver, 20_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        remove_usdc_opt_in(context, resolver)

        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_401)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.proposer_bond_held.value) == 0
        assert contract_pending_payout(contract, resolver) == 10_000_000

        seed_usdc_balance(context, resolver, 0)
        call_as(context, resolver, contract.withdraw_pending_payouts, latest_timestamp=96_402)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].xfer_asset.id) == CURRENCY_ASA
        assert int(transfers[0].asset_amount) == 10_000_000
        assert transfers[0].asset_receiver == Account(resolver)
        assert contract_pending_payout(contract, resolver) == 0


def test_contract_finalize_dispute_credits_pending_payout_even_without_receiver_opt_in(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        remove_usdc_opt_in(context, challenger)

        call_as(context, resolver, contract.finalize_dispute, arc4.UInt64(1), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.proposer_bond_held.value) == 0
        assert int(contract.challenger_bond_held.value) == 0
        assert contract_pending_payout(contract, challenger) == 15_000_000

        seed_usdc_balance(context, challenger, 0)
        call_as(context, challenger, contract.withdraw_pending_payouts, latest_timestamp=10_004)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].xfer_asset.id) == CURRENCY_ASA
        assert int(transfers[0].asset_amount) == 15_000_000
        assert transfers[0].asset_receiver == Account(challenger)
        assert contract_pending_payout(contract, challenger) == 0


def test_contract_resolution_claim_and_refund_paths_match_model(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()
    challenger = make_address()

    # Happy path: resolve and claim
    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, winner, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        pre_claim_pool = int(contract.pool_balance.value)

        call_as(context, winner, contract.trigger_resolution, latest_timestamp=10_000)
        assert int(contract.status.value) == STATUS_RESOLUTION_PENDING
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        assert int(contract.status.value) == STATUS_RESOLUTION_PROPOSED
        call_as(context, winner, contract.finalize_resolution, latest_timestamp=96_401)
        assert int(contract.status.value) == STATUS_RESOLVED

        claim_result = call_as(
            context,
            winner,
            contract.claim,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            latest_timestamp=96_402,
        )
        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.pool_balance.value) < pre_claim_pool
        assert contract_q(contract)[0] == 0
        assert contract_user_shares(contract, winner, 0) == 0
        assert contract_user_cost_basis(contract, winner, 0) == 0
        assert claim_result is None

    # Dispute cancel path: cancel disputed market, then refund
    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, winner, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        cost_basis_before = contract_user_cost_basis(contract, winner, 1)
        pool_before_refund = int(contract.pool_balance.value)

        call_as(context, winner, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(1), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)

        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        assert int(contract.status.value) == STATUS_DISPUTED

        # Cancel disputed market via resolution authority so refund is possible
        call_as(
            context, resolver, contract.cancel_dispute_and_market,
            arc4.DynamicBytes(b"r" * 32),
            latest_timestamp=10_003,
        )
        assert int(contract.status.value) == STATUS_CANCELLED

        refund_result = call_as(
            context,
            winner,
            contract.refund,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            latest_timestamp=10_004,
        )
        refunded = pool_before_refund - int(contract.pool_balance.value)
        assert refunded == cost_basis_before
        assert contract_user_shares(contract, winner, 1) == 0
        assert contract_user_cost_basis(contract, winner, 1) == 0
        assert contract_q(contract)[1] == 0
        assert refund_result is None


def test_contract_multi_share_claim_and_refund_single_call_paths(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()
    buyer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        winning_shares = 3 * SHARE_UNIT
        winner_buy_payment = make_usdc_payment(context, contract, winner, 50_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(winning_shares),
            arc4.UInt64(50_000_000),
            winner_buy_payment,
            latest_timestamp=5_000,
        )

        losing_buy_payment = make_usdc_payment(context, contract, buyer, 10_000_000)
        call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            losing_buy_payment,
            latest_timestamp=5_001,
        )

        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=10_001,
        )
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_401)

        claim_shares = 2 * SHARE_UNIT
        outstanding_before = contract_q(contract)[0]
        pool_before = int(contract.pool_balance.value)
        claim_result = call_as(
            context,
            winner,
            contract.claim,
            arc4.UInt64(0),
            arc4.UInt64(claim_shares),
            latest_timestamp=96_402,
        )

        assert claim_result is None
        assert pool_before - int(contract.pool_balance.value) == (pool_before * claim_shares) // outstanding_before
        assert contract_user_shares(contract, winner, 0) == SHARE_UNIT
        assert contract_q(contract)[0] == SHARE_UNIT
        assert contract_user_cost_basis(contract, winner, 0) > 0

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        held_shares = 3 * SHARE_UNIT
        buy_payment = make_usdc_payment(context, contract, buyer, 50_000_000)
        call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(2),
            arc4.UInt64(held_shares),
            arc4.UInt64(50_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        basis_before = contract_user_cost_basis(contract, buyer, 2)

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)

        refund_shares = 2 * SHARE_UNIT
        refund_result = call_as(
            context,
            buyer,
            contract.refund,
            arc4.UInt64(2),
            arc4.UInt64(refund_shares),
            latest_timestamp=5_002,
        )

        assert refund_result is None
        assert contract_user_shares(contract, buyer, 2) == SHARE_UNIT
        assert contract_q(contract)[2] == SHARE_UNIT
        assert basis_before - contract_user_cost_basis(contract, buyer, 2) > 0


def test_contract_dispute_confirmation_routes_half_losing_bond_to_sink(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )

        protocol_fees_before = int(contract.protocol_fee_balance.value)
        call_as(context, creator, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.protocol_fee_balance.value) == protocol_fees_before
        assert int(contract.dispute_sink_balance.value) == 5_000_000
        assert int(contract.proposer_bond_held.value) == 0
        assert int(contract.challenger_bond_held.value) == 0


def test_contract_dispute_overturn_routes_half_losing_bond_to_sink(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )

        protocol_fees_before = int(contract.protocol_fee_balance.value)
        call_as(context, resolver, contract.finalize_dispute, arc4.UInt64(1), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.protocol_fee_balance.value) == protocol_fees_before
        assert int(contract.dispute_sink_balance.value) == 5_000_000
        assert int(contract.proposer_bond_held.value) == 0
        assert int(contract.challenger_bond_held.value) == 0


def test_contract_dispute_cancel_refunds_challenger_and_slashes_proposer_to_sink(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )

        protocol_fees_before = int(contract.protocol_fee_balance.value)
        call_as(context, resolver, contract.cancel_dispute_and_market, arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.status.value) == STATUS_CANCELLED
        assert int(contract.protocol_fee_balance.value) == protocol_fees_before
        assert int(contract.dispute_sink_balance.value) == 10_000_000
        assert int(contract.proposer_bond_held.value) == 0
        assert int(contract.challenger_bond_held.value) == 0


def test_contract_abort_early_resolution_reopens_active_before_deadline(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_early_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=9_990,
        )
        assert int(contract.status.value) == STATUS_RESOLUTION_PROPOSED
        assert int(contract.proposal_timestamp.value) == 9_990

        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(7),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=9_991,
        )
        assert int(contract.status.value) == STATUS_DISPUTED

        call_as(
            context,
            resolver,
            contract.abort_early_resolution,
            arc4.DynamicBytes(b"r" * 32),
            latest_timestamp=9_992,
        )

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.proposal_timestamp.value) == 0
        assert contract.proposer.value == Bytes(ZERO_ADDRESS_BYTES)
        assert contract.challenger.value == Bytes(ZERO_ADDRESS_BYTES)
        assert int(contract.proposer_bond_held.value) == 0
        assert int(contract.challenger_bond_held.value) == 0
        assert int(contract.dispute_sink_balance.value) == 5_000_000

        reopened_buy = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            reopened_buy,
            latest_timestamp=9_993,
        )
        assert contract_user_shares(contract, trader, 1) == SHARE_UNIT


def test_contract_abort_early_resolution_after_deadline_returns_pending(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_early_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=9_998,
        )

        challenge_payment = make_usdc_payment(context, contract, challenger, 10_000_000)
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_001,
        )

        call_as(
            context,
            resolver,
            contract.abort_early_resolution,
            arc4.DynamicBytes(b"r" * 32),
            latest_timestamp=10_002,
        )

        assert int(contract.status.value) == STATUS_RESOLUTION_PENDING
        assert int(contract.proposal_timestamp.value) == 0
        assert int(contract.dispute_sink_balance.value) == 5_000_000

        repropose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"n" * 32),
            repropose_payment,
            latest_timestamp=10_003,
        )

        assert int(contract.status.value) == STATUS_RESOLUTION_PROPOSED
        assert int(contract.proposed_outcome.value) == 1


def test_contract_cancelled_lp_withdraw_preserves_refund_reserve(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        trader_basis = contract_user_cost_basis(contract, trader, 0)
        pool_before_cancel_withdraw = int(contract.pool_balance.value)
        creator_lp = int(contract.lp_shares[Account(creator)])

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)
        assert int(contract.status.value) == STATUS_CANCELLED

        call_as(context, creator, contract.withdraw_liq, arc4.UInt64(creator_lp), latest_timestamp=5_002)
        assert int(contract.pool_balance.value) == trader_basis
        assert int(contract.total_outstanding_cost_basis.value) == trader_basis
        assert contract_q(contract)[0] == SHARE_UNIT

        call_as(
            context,
            trader,
            contract.refund,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            latest_timestamp=5_003,
        )
        assert int(contract.pool_balance.value) == 0
        assert int(contract.total_outstanding_cost_basis.value) == 0
        assert contract_q(contract)[0] == 0


def test_contract_provide_liq_handles_large_amounts_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 1_000_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(1_000_000_000), bootstrap_payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        pool_before = int(contract.pool_balance.value)
        shares_before = int(contract.lp_shares_total.value)
        provide_payment = make_usdc_payment(context, contract, lp2, LARGE_PROVIDE_DEPOSIT)
        call_as(
            context,
            lp2,
            contract.provide_liq,
            arc4.UInt64(LARGE_PROVIDE_DEPOSIT),
            provide_payment,
            latest_timestamp=6_000,
        )

        expected_minted = (shares_before * LARGE_PROVIDE_DEPOSIT) // pool_before
        assert int(contract.pool_balance.value) == pool_before + LARGE_PROVIDE_DEPOSIT
        assert int(contract.lp_shares_total.value) == shares_before + expected_minted
        assert int(contract.lp_shares[Account(lp2)]) == expected_minted


def test_contract_withdraw_liq_handles_large_active_pool_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, LARGE_ACTIVE_POOL)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(LARGE_ACTIVE_POOL), bootstrap_payment, latest_timestamp=1)

        total_shares_before = int(contract.lp_shares_total.value)
        burn = total_shares_before // 2
        expected_return = (LARGE_ACTIVE_POOL * burn) // total_shares_before

        call_as(context, creator, contract.withdraw_liq, arc4.UInt64(burn), latest_timestamp=5_000)

        assert int(contract.pool_balance.value) == LARGE_ACTIVE_POOL - expected_return
        assert int(contract.lp_shares_total.value) == total_shares_before - burn
        assert int(contract.lp_shares[Account(creator)]) == total_shares_before - burn


def test_contract_withdraw_liq_handles_large_cancelled_pool_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, LARGE_ACTIVE_POOL)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(LARGE_ACTIVE_POOL), bootstrap_payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )
        trader_basis = contract_user_cost_basis(contract, trader, 0)
        creator_lp = int(contract.lp_shares[Account(creator)])

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)
        call_as(context, creator, contract.withdraw_liq, arc4.UInt64(creator_lp), latest_timestamp=5_002)

        assert int(contract.pool_balance.value) == trader_basis
        assert int(contract.total_outstanding_cost_basis.value) == trader_basis
        assert contract_q(contract)[0] == SHARE_UNIT


def test_contract_claim_handles_large_pool_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        bootstrap_payment = make_usdc_payment(context, contract, creator, LARGE_CLAIM_POOL)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(LARGE_CLAIM_POOL), bootstrap_payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, winner, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=10_001,
        )
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_402)

        payout_before_claim = int(contract.pool_balance.value)
        call_as(
            context,
            winner,
            contract.claim,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            latest_timestamp=96_403,
        )

        assert payout_before_claim > LARGE_CLAIM_POOL
        assert int(contract.pool_balance.value) == 0
        assert contract_user_shares(contract, winner, 0) == 0
        assert contract_q(contract)[0] == 0


def test_withdraw_protocol_fees_uses_protocol_admin_and_governed_treasury(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    protocol_admin = make_address()
    treasury = make_address()
    attacker = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        protocol_config_app = seed_protocol_config_state(context, admin=protocol_admin, treasury=treasury)

        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        store_blueprints(context, contract, creator)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            latest_timestamp=5_000,
        )

        accrued_protocol_fees = int(contract.protocol_fee_balance.value)
        assert accrued_protocol_fees > 0

        with pytest.raises(AssertionError):
            call_as(
                context,
                attacker,
                contract.withdraw_protocol_fees,
                latest_timestamp=5_001,
                apps=(protocol_config_app,),
            )

        call_as(
            context,
            protocol_admin,
            contract.withdraw_protocol_fees,
            latest_timestamp=5_002,
            apps=(protocol_config_app,),
        )
        assert int(contract.protocol_fee_balance.value) == 0

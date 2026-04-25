import algosdk.account
import algosdk.logic
import pytest
from algopy import Account, Application, Asset, Bytes, Global, OnCompleteAction, UInt64, arc4, op
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    BOX_KEY_USER_COST_BASIS,
    BOX_KEY_USER_SHARES,
    COST_BOX_MBR,
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    MAX_COMMENT_BYTES,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    SHARE_BOX_MBR,
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
from smart_contracts.market_app.model import MarketAppModel, ZERO_ADDRESS
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
PROTOCOL_CONFIG_APP_ID = 7_001
DEFAULT_FACTORY_APP_ID = 8_001
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
        proposer_fee_bps=0,
        proposer_fee_floor_bps=0,
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
    sender: str | None = None,
    market_admin: str | None = None,
    blueprint_cid: bytes = b"ipfs://blueprint-cid",
    deadline: int = 10_000,
    num_outcomes: int = 3,
    initial_b: int = 100_000_000,
    cancellable: bool = True,
    protocol_treasury: str | None = None,
    factory_id: int = DEFAULT_FACTORY_APP_ID,
    app_creator: str | None = None,
    challenge_window_secs: int = 86_400,
    protocol_min_challenge_window_secs: int = 86_400,
    challenge_bond: int = 10_000_000,
    proposal_bond: int = 10_000_000,
    challenge_bond_bps: int = 500,
    proposal_bond_bps: int = 500,
    challenge_bond_cap: int = 100_000_000,
    proposal_bond_cap: int = 100_000_000,
    proposer_fee_bps: int = 0,
    proposer_fee_floor_bps: int = 0,
    grace_period_secs: int = 3_600,
) -> None:
    txn_sender = sender or creator
    admin = market_admin or creator
    treasury = protocol_treasury or creator
    protocol_app = seed_protocol_config_state(
        context,
        admin=txn_sender,
        treasury=treasury,
        factory_id=factory_id,
        min_challenge_window_secs=protocol_min_challenge_window_secs,
        challenge_bond=challenge_bond,
        proposal_bond=proposal_bond,
        challenge_bond_bps=challenge_bond_bps,
        proposal_bond_bps=proposal_bond_bps,
        challenge_bond_cap=challenge_bond_cap,
        proposal_bond_cap=proposal_bond_cap,
        proposer_fee_bps=proposer_fee_bps,
        proposer_fee_floor_bps=proposer_fee_floor_bps,
    )
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(num_outcomes),
        initial_b=arc4.UInt64(initial_b),
        lp_fee_bps=arc4.UInt64(200),
        deadline=arc4.UInt64(deadline),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        blueprint_cid=arc4.DynamicBytes(blueprint_cid),
        challenge_window_secs=arc4.UInt64(challenge_window_secs),
        resolution_authority=arc4.Address(resolver),
        grace_period_secs=arc4.UInt64(grace_period_secs),
        market_admin=arc4.Address(admin),
        protocol_config_id=arc4.UInt64(PROTOCOL_CONFIG_APP_ID),
        cancellable=arc4.Bool(cancellable),
        lp_entry_max_price_fp=arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP),
    )
    app_data = context.ledger._app_data[contract.__app_id__]
    app_data.fields["creator"] = Account(app_creator or algosdk.logic.get_application_address(factory_id))
    context.ledger.patch_global_fields(latest_timestamp=1)
    context._default_sender = Account(txn_sender)
    deferred = context.txn.defer_app_call(contract.create, **args)
    deferred._txns[-1].fields["apps"] = (protocol_app,)
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
    """Ledger-only markets no longer need outcome ASA registration."""
    _ = (context, contract, creator, outcome_asa_ids)


def ensure_blueprint_cid(contract: QuestionMarket) -> None:
    """Atomic creation configures blueprint metadata at create time."""
    assert contract.blueprint_cid.value.length > UInt64(0)


def initialize_market(context, contract, creator):
    """Initialize a CREATED contract using the atomic market setup helper."""
    call_as(context, creator, contract.initialize)


def opt_in_market(context, contract, account: str, latest_timestamp: int | None = None) -> None:
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(account)
    deferred = context.txn.defer_app_call(contract.opt_in)
    deferred._txns[-1].fields["on_completion"] = OnCompleteAction.OptIn
    with context.txn.create_group([deferred]):
        contract.opt_in()


def seed_protocol_config_state(
    context,
    *,
    admin: str,
    treasury: str,
    factory_id: int = DEFAULT_FACTORY_APP_ID,
    min_challenge_window_secs: int = 86_400,
    challenge_bond: int = 10_000_000,
    proposal_bond: int = 10_000_000,
    challenge_bond_bps: int = 500,
    proposal_bond_bps: int = 500,
    challenge_bond_cap: int = 100_000_000,
    proposal_bond_cap: int = 100_000_000,
    proposer_fee_bps: int = 0,
    proposer_fee_floor_bps: int = 0,
    protocol_fee_bps: int = 50,
    residual_linear_lambda_fp: int = 150_000,
):
    if PROTOCOL_CONFIG_APP_ID in context.ledger._app_data:
        app = Application(PROTOCOL_CONFIG_APP_ID)
    else:
        app = context.any.application(id=PROTOCOL_CONFIG_APP_ID)
    context.ledger.set_global_state(app, b"admin", Account(admin).bytes.value)
    context.ledger.set_global_state(app, KEY_PROTOCOL_TREASURY, Account(treasury).bytes.value)
    context.ledger.set_global_state(app, KEY_MARKET_FACTORY_ID, factory_id)
    context.ledger.set_global_state(app, KEY_MAX_ACTIVE_LP_V4_OUTCOMES, 8)
    context.ledger.set_global_state(app, KEY_MIN_CHALLENGE_WINDOW_SECS, min_challenge_window_secs)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND, challenge_bond)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND, proposal_bond)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_BPS, challenge_bond_bps)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_BPS, proposal_bond_bps)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_CAP, challenge_bond_cap)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_CAP, proposal_bond_cap)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_BPS, proposer_fee_bps)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_FLOOR_BPS, proposer_fee_floor_bps)
    context.ledger.set_global_state(app, KEY_PROTOCOL_FEE_BPS, protocol_fee_bps)
    context.ledger.set_global_state(app, KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP, residual_linear_lambda_fp)
    return app


def contract_q(contract: QuestionMarket) -> list[int]:
    return [int(contract._get_quantity(UInt64(idx))) for idx in range(int(contract.num_outcomes.value))]


def contract_user_shares(contract: QuestionMarket, sender: str, outcome_index: int) -> int:
    key = op.concat(Account(sender).bytes, op.itob(UInt64(outcome_index)))
    return int(contract.user_outcome_shares_box.get(key, default=UInt64(0)))


def contract_withdrawable_fee_surplus(contract: QuestionMarket, sender: str) -> int:
    return int(contract.withdrawable_fee_surplus.get(Account(sender), default=UInt64(0)))


def contract_user_cost_basis(contract: QuestionMarket, sender: str, outcome_index: int) -> int:
    key = op.concat(Account(sender).bytes, op.itob(UInt64(outcome_index)))
    return int(contract.user_cost_basis_box.get(key, default=UInt64(0)))


def contract_pending_payout(contract: QuestionMarket, sender: str) -> int:
    return int(contract.pending_payouts_box.get(Account(sender).bytes, default=UInt64(0)))


def required_bond(contract: QuestionMarket, *, proposal: bool) -> int:
    pool_balance = int(contract.pool_balance.value)
    bootstrap_deposit = int(contract.bootstrap_deposit.value)
    scale_base = max(pool_balance, bootstrap_deposit)
    if proposal:
        minimum = int(contract.proposal_bond.value)
        bps = int(contract.proposal_bond_bps.value)
        cap = int(contract.proposal_bond_cap.value)
    else:
        minimum = int(contract.challenge_bond.value)
        bps = int(contract.challenge_bond_bps.value)
        cap = int(contract.challenge_bond_cap.value)
    proportional = (scale_base * bps + 9_999) // 10_000
    return min(cap, max(minimum, proportional))


def required_proposer_fee(contract: QuestionMarket, *, max_budget: bool = False) -> int:
    required = int(contract.proposal_bond_cap.value) if max_budget else required_bond(contract, proposal=True)
    floor_fee = (int(contract.proposal_bond.value) * int(contract.proposer_fee_floor_bps.value) + 9_999) // 10_000
    daily_fee = (required * int(contract.proposer_fee_bps.value) + 9_999) // 10_000
    window_fee = (daily_fee * int(contract.challenge_window_secs.value) + 86_399) // 86_400
    return max(floor_fee, window_fee)


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


def price_array(values: list[int]) -> arc4.DynamicArray[arc4.UInt64]:
    return arc4.DynamicArray[arc4.UInt64](*(arc4.UInt64(value) for value in values))


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
        assert int(contract.num_outcomes.value) == 3
        assert int(contract.b.value) == 100_000_000
        assert contract.creator.value == Account(creator).bytes
        assert contract_q(contract) == [0, 0, 0]

        ensure_blueprint_cid(contract)
        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000
        assert int(contract.lp_shares_total.value) == int(contract.b.value)
        assert contract.bootstrapper_lp_shares.value > UInt64(0)
        assert contract.bootstrapper_lp_entry.value > UInt64(0)

        opt_in_market(context, contract, creator, latest_timestamp=2)
        assert contract.bootstrapper_lp_shares.value == UInt64(0)
        assert int(contract.lp_shares[Account(creator)]) == int(contract.b.value)


def test_contract_create_persists_explicit_challenge_window(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            challenge_window_secs=3_600,
            protocol_min_challenge_window_secs=3_600,
        )

        assert int(contract.challenge_window_secs.value) == 3_600


def test_contract_create_rejects_unapproved_factory_creator(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        with pytest.raises(AssertionError):
            create_contract(
                context,
                contract,
                creator=creator,
                resolver=resolver,
                factory_id=DEFAULT_FACTORY_APP_ID,
                app_creator=algosdk.logic.get_application_address(DEFAULT_FACTORY_APP_ID + 1),
            )


@pytest.mark.parametrize("label", ["creator", "resolution_authority", "market_admin", "protocol_treasury"])
def test_contract_create_rejects_zero_address_roles(disable_arc4_emit, label) -> None:
    creator = make_address()
    resolver = make_address()
    market_admin = make_address()
    treasury = make_address()
    params = {
        "creator": creator,
        "resolver": resolver,
        "sender": creator,
        "market_admin": market_admin,
        "protocol_treasury": treasury,
    }
    if label == "creator":
        params["creator"] = ZERO_ADDRESS
        params["sender"] = make_address()
    elif label == "resolution_authority":
        params["resolver"] = ZERO_ADDRESS
    elif label == "market_admin":
        params["market_admin"] = ZERO_ADDRESS
    else:
        params["protocol_treasury"] = ZERO_ADDRESS

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        with pytest.raises(AssertionError):
            create_contract(context, contract, **params)


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
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        opt_in_market(context, contract, creator, latest_timestamp=2)

        call_as(context, creator, contract.post_comment, arc4.String("lp comment"), latest_timestamp=3)

        buy_payment = make_usdc_payment(context, contract, holder, 10_000_000)
        call_as(
            context,
            holder,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            make_mbr_payment(context, contract, holder, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        call_as(context, holder, contract.post_comment, arc4.String("holder comment"), latest_timestamp=5_001)

        comment_events = [entry for entry in captured if entry[0] == "CommentPosted(string)"]
        assert comment_events == [
            ("CommentPosted(string)", ["lp comment"]),
            ("CommentPosted(string)", ["holder comment"]),
        ]


def test_contract_bootstrap_rejects_underfunded_max_outcome_market(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    outcome_asa_ids = [1000 + i for i in range(8)]

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            num_outcomes=8,
            initial_b=50_000_000,
        )
        register_outcome_asas(context, contract, creator, outcome_asa_ids)
        ensure_blueprint_cid(contract)

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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, holder, SHARE_BOX_MBR + COST_BOX_MBR),
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
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        opt_in_market(context, contract, creator, latest_timestamp=2)

        with pytest.raises(AssertionError):
            call_as(context, outsider, contract.post_comment, arc4.String("hello"), latest_timestamp=3)

        with pytest.raises(AssertionError):
            call_as(context, creator, contract.post_comment, arc4.String(""), latest_timestamp=3)

        exact_limit = "a" * MAX_COMMENT_BYTES
        call_as(context, creator, contract.post_comment, arc4.String(exact_limit), latest_timestamp=3)

        with pytest.raises(AssertionError):
            call_as(
                context,
                creator,
                contract.post_comment,
                arc4.String("a" * (MAX_COMMENT_BYTES + 1)),
                latest_timestamp=3,
            )


def test_contract_initialize_then_bootstrap_persist_state(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)

        initialize_market(context, contract, creator)
        ensure_blueprint_cid(contract)

        assert int(contract.status.value) == STATUS_CREATED
        assert contract.blueprint_cid.value.length > UInt64(0)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000
        assert int(contract.lp_shares_total.value) == int(contract.b.value)
        assert contract.bootstrapper_lp_shares.value > UInt64(0)
        assert contract.bootstrapper_lp_entry.value > UInt64(0)

        opt_in_market(context, contract, creator, latest_timestamp=2)
        assert int(contract.lp_shares[Account(creator)]) == int(contract.b.value)


def test_contract_initialize_rejects_non_creator(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    attacker = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)

        with pytest.raises(AssertionError):
            call_as(context, attacker, contract.initialize)


def test_contract_trade_and_active_lp_paths_match_greenfield_semantics(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.pool_balance.value) == 200_000_000
        opt_in_market(context, contract, creator, latest_timestamp=2)

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
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        post_buy_pool = int(contract.pool_balance.value)
        assert post_buy_pool > pre_buy_pool
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert contract_user_cost_basis(contract, buyer, 1) > 0
        assert buy_result is None

        # Sell
        pre_sell_pool = int(contract.pool_balance.value)
        sell_result = call_as(
            context,
            buyer,
            contract.sell,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(1),
            latest_timestamp=5_001,
        )
        post_sell_pool = int(contract.pool_balance.value)
        assert post_sell_pool < pre_sell_pool
        assert contract_user_shares(contract, buyer, 1) == 0
        assert contract_user_cost_basis(contract, buyer, 1) == 0
        assert sell_result is None

        # Active LP entry
        prices_before = lmsr_prices(contract_q(contract), int(contract.b.value))
        creator_lp_before = int(contract.lp_shares[Account(creator)])
        lp_payment = make_usdc_payment(context, contract, lp2, 100_000_000)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(50_000_000),
            arc4.UInt64(100_000_000),
            price_array(prices_before),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=6_000,
        )
        prices_after_entry = lmsr_prices(contract_q(contract), int(contract.b.value))
        assert all(abs(a - b) <= 2 for a, b in zip(prices_before, prices_after_entry))
        assert int(contract.lp_shares[Account(lp2)]) == 50_000_000
        assert int(contract.pool_balance.value) > post_sell_pool
        assert int(contract.lp_shares[Account(creator)]) == creator_lp_before

        followup_buyer = make_address()
        followup_payment = make_usdc_payment(context, contract, followup_buyer, 10_000_000)
        call_as(
            context,
            followup_buyer,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            followup_payment,
            make_mbr_payment(context, contract, followup_buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=6_001,
        )

        # Both creator and lp2 settle LP fees into local withdrawable balances.
        call_as(context, creator, contract.claim_lp_fees, latest_timestamp=6_001)
        call_as(context, lp2, contract.claim_lp_fees, latest_timestamp=6_002)
        assert contract_withdrawable_fee_surplus(contract, creator) >= 0
        assert contract_withdrawable_fee_surplus(contract, lp2) > 0


def test_contract_buy_refunds_surplus_payment(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)
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
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        retained = (int(contract.pool_balance.value) - pool_before) + (int(contract.lp_fee_balance.value) - lp_fee_before) + (int(contract.protocol_fee_balance.value) - protocol_fee_before)
        transfers = last_inner_asset_transfers(context)

        assert retained > 0
        assert retained < 10_000_000
        assert len(transfers) == 1
        assert int(transfers[0].xfer_asset.id) == CURRENCY_ASA
        assert int(transfers[0].asset_amount) == 10_000_000 - retained
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert buy_result is None


def test_contract_resolved_claim_is_one_to_one_and_lp_can_claim_residual(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()
    loser = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)

        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)

        winning_buy = make_usdc_payment(context, contract, winner, 10_000_000)
        losing_buy = make_usdc_payment(context, contract, loser, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            winning_buy,
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        call_as(
            context,
            loser,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            losing_buy,
            make_mbr_payment(context, contract, loser, SHARE_BOX_MBR + COST_BOX_MBR),
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
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_402)
        opt_in_market(context, contract, creator, latest_timestamp=96_402)

        reserve_before = int(contract._get_total_user_shares(UInt64(0)))
        pool_before_residual = int(contract.pool_balance.value)
        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=96_402)
        first_residual_claim = int(contract.total_residual_claimed.value)

        assert first_residual_claim > 0
        assert int(contract.pool_balance.value) == pool_before_residual - first_residual_claim
        assert int(contract.pool_balance.value) >= reserve_before

        starting_pool = int(contract.pool_balance.value)
        call_as(
            context,
            winner,
            contract.claim,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            latest_timestamp=96_403,
        )

        assert int(contract.pool_balance.value) == starting_pool - SHARE_UNIT
        assert int(contract.pool_balance.value) >= int(contract._get_total_user_shares(UInt64(0)))

        with pytest.raises(AssertionError):
            call_as(context, creator, contract.claim_lp_residual, latest_timestamp=96_404)


def test_contract_multi_share_buy_and_sell_single_call_paths(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        assert buy_result is None
        assert contract_user_shares(contract, buyer, 1) == buy_shares
        assert contract_q(contract)[1] == buy_shares

        sell_shares = 2 * SHARE_UNIT
        sell_result = call_as(
            context,
            buyer,
            contract.sell,
            arc4.UInt64(1),
            arc4.UInt64(sell_shares),
            arc4.UInt64(1),
            latest_timestamp=5_001,
        )

        assert sell_result is None
        assert contract_user_shares(contract, buyer, 1) == SHARE_UNIT
        assert contract_user_cost_basis(contract, buyer, 1) > 0
        assert contract_q(contract)[1] == SHARE_UNIT


def test_contract_sell_rejects_oversell_amount(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    seller = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, seller, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        sell_shares = held_shares + SHARE_UNIT
        with pytest.raises(AssertionError):
            call_as(
                context,
                seller,
                contract.sell,
                arc4.UInt64(1),
                arc4.UInt64(sell_shares),
                arc4.UInt64(1),
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
        ensure_blueprint_cid(contract)
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


def test_contract_finalize_resolution_pays_configured_proposer_fee_and_reclaims_unused_budget(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            proposer_fee_bps=20,
            proposer_fee_floor_bps=0,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)

        bootstrap_budget = required_proposer_fee(contract, max_budget=True)
        payment = make_usdc_payment(context, contract, creator, 200_000_000 + bootstrap_budget)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        expected_fee = required_proposer_fee(contract)
        expected_leftover = bootstrap_budget - expected_fee

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_401)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert contract_pending_payout(contract, resolver) == 10_000_000 + expected_fee
        assert int(contract.resolution_budget_balance.value) == expected_leftover

        seed_usdc_balance(context, creator, 0)
        call_as(context, creator, contract.reclaim_resolution_budget, latest_timestamp=96_402)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].asset_amount) == expected_leftover
        assert transfers[0].asset_receiver == Account(creator)
        assert int(contract.resolution_budget_balance.value) == 0


def test_contract_resolution_authority_can_propose_with_zero_bond(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 0)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        zero_payment = make_usdc_payment(context, contract, resolver, 0)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            zero_payment,
            latest_timestamp=10_001,
        )

        assert int(contract.status.value) == STATUS_RESOLUTION_PROPOSED
        assert int(contract.proposer_bond_held.value) == 0


def test_contract_resolution_authority_propose_does_not_evaluate_overflowing_grace(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            grace_period_secs=(1 << 64) - 1,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 0)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        zero_payment = make_usdc_payment(context, contract, resolver, 0)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            zero_payment,
            latest_timestamp=10_001,
        )

        assert int(contract.status.value) == STATUS_RESOLUTION_PROPOSED
        assert contract.proposer.value == Account(resolver).bytes


def test_contract_open_proposer_underbonded_after_grace_period_fails(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    open_proposer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, open_proposer, 20_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        underbonded_payment = make_usdc_payment(context, contract, open_proposer, 9_999_999)
        with pytest.raises(AssertionError):
            call_as(
                context,
                open_proposer,
                contract.propose_resolution,
                arc4.UInt64(0),
                arc4.DynamicBytes(b"e" * 32),
                underbonded_payment,
                latest_timestamp=13_601,
            )


def test_contract_challenge_bond_cap_is_enforced(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            challenge_bond=10_000_000,
            proposal_bond=10_000_000,
            challenge_bond_bps=5_000,
            proposal_bond_bps=5_000,
            challenge_bond_cap=20_000_000,
            proposal_bond_cap=20_000_000,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 0)
        seed_usdc_balance(context, challenger, 25_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        zero_payment = make_usdc_payment(context, contract, resolver, 0)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            zero_payment,
            latest_timestamp=10_001,
        )

        underbonded_payment = make_usdc_payment(context, contract, challenger, 19_999_999)
        with pytest.raises(AssertionError):
            call_as(
                context,
                challenger,
                contract.challenge_resolution,
                underbonded_payment,
                arc4.UInt64(1),
                arc4.DynamicBytes(b"c" * 32),
                latest_timestamp=10_002,
            )

        challenge_payment = make_usdc_payment(context, contract, challenger, 20_000_000)
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_003,
        )

        assert int(contract.challenger_bond_held.value) == 20_000_000


def test_contract_finalize_dispute_credits_pending_payout_even_without_receiver_opt_in(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_bond_required = required_bond(contract, proposal=False)
        challenge_payment = make_usdc_payment(context, contract, challenger, challenge_bond_required)
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
        assert contract_pending_payout(contract, challenger) == challenge_bond_required + 5_000_000

        seed_usdc_balance(context, challenger, 0)
        call_as(context, challenger, contract.withdraw_pending_payouts, latest_timestamp=10_004)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].xfer_asset.id) == CURRENCY_ASA
        assert int(transfers[0].asset_amount) == challenge_bond_required + 5_000_000
        assert transfers[0].asset_receiver == Account(challenger)
        assert contract_pending_payout(contract, challenger) == 0


def test_contract_creator_and_admin_dispute_paths_require_resolution_authority(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    admin = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver, market_admin=admin)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )

        with pytest.raises(AssertionError):
            call_as(context, creator, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)
        with pytest.raises(AssertionError):
            call_as(context, admin, contract.admin_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_004)

        call_as(context, resolver, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_005)
        assert int(contract.status.value) == STATUS_RESOLVED


def test_contract_confirmed_dispute_pays_proposer_fee_once(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            proposer_fee_bps=20,
            proposer_fee_floor_bps=0,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        bootstrap_budget = required_proposer_fee(contract, max_budget=True)
        payment = make_usdc_payment(context, contract, creator, 200_000_000 + bootstrap_budget)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        expected_fee = required_proposer_fee(contract)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        call_as(context, resolver, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert contract_pending_payout(contract, resolver) == 10_000_000 + 5_000_000 + expected_fee
        assert int(contract.resolution_budget_balance.value) == bootstrap_budget - expected_fee


def test_contract_overturned_dispute_keeps_resolution_budget_for_creator_reclaim(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            proposer_fee_bps=20,
            proposer_fee_floor_bps=0,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        bootstrap_budget = required_proposer_fee(contract, max_budget=True)
        payment = make_usdc_payment(context, contract, creator, 200_000_000 + bootstrap_budget)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        call_as(context, resolver, contract.finalize_dispute, arc4.UInt64(1), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.resolution_budget_balance.value) == bootstrap_budget

        seed_usdc_balance(context, creator, 0)
        call_as(context, creator, contract.reclaim_resolution_budget, latest_timestamp=10_004)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].asset_amount) == bootstrap_budget
        assert transfers[0].asset_receiver == Account(creator)


def test_contract_cancelled_market_keeps_resolution_budget_for_creator_reclaim(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            proposer_fee_bps=20,
            proposer_fee_floor_bps=0,
        )
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, resolver, 20_000_000)
        seed_usdc_balance(context, challenger, 20_000_000)

        bootstrap_budget = required_proposer_fee(contract, max_budget=True)
        payment = make_usdc_payment(context, contract, creator, 200_000_000 + bootstrap_budget)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context,
            challenger,
            contract.challenge_resolution,
            challenge_payment,
            arc4.UInt64(1),
            arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        call_as(context, resolver, contract.cancel_dispute_and_market, arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        assert int(contract.status.value) == STATUS_CANCELLED
        assert int(contract.resolution_budget_balance.value) == bootstrap_budget

        seed_usdc_balance(context, creator, 0)
        call_as(context, creator, contract.reclaim_resolution_budget, latest_timestamp=10_004)
        transfers = last_inner_asset_transfers(context)
        assert len(transfers) == 1
        assert int(transfers[0].asset_amount) == bootstrap_budget
        assert transfers[0].asset_receiver == Account(creator)


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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        cost_basis_before = contract_user_cost_basis(contract, winner, 1)
        pool_before_refund = int(contract.pool_balance.value)

        call_as(context, winner, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(1), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)

        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
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
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
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
        assert pool_before - int(contract.pool_balance.value) == claim_shares
        assert contract_user_shares(contract, winner, 0) == SHARE_UNIT
        assert contract_q(contract)[0] == SHARE_UNIT
        assert contract_user_cost_basis(contract, winner, 0) > 0

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
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
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )

        protocol_fees_before = int(contract.protocol_fee_balance.value)
        call_as(context, resolver, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

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
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
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
        ensure_blueprint_cid(contract)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
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

        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
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
        ensure_blueprint_cid(contract)

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

        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
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


def test_contract_cancelled_lp_residual_claim_preserves_refund_reserve(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        trader_basis = contract_user_cost_basis(contract, trader, 0)
        pool_before_cancel_claim = int(contract.pool_balance.value)

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)
        assert int(contract.status.value) == STATUS_CANCELLED
        opt_in_market(context, contract, creator, latest_timestamp=5_002)

        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=5_003)
        assert int(contract.pool_balance.value) < pool_before_cancel_claim
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


def test_contract_enter_lp_active_handles_large_amounts_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        pool_before = int(contract.pool_balance.value)
        shares_before = int(contract.lp_shares_total.value)
        prices_before = lmsr_prices(contract_q(contract), int(contract.b.value))
        provide_payment = make_usdc_payment(context, contract, lp2, LARGE_PROVIDE_DEPOSIT)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(1_000_000_000),
            arc4.UInt64(LARGE_PROVIDE_DEPOSIT),
            price_array(prices_before),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            provide_payment,
            latest_timestamp=6_000,
        )

        assert int(contract.pool_balance.value) > pool_before
        assert int(contract.pool_balance.value) <= pool_before + LARGE_PROVIDE_DEPOSIT
        assert int(contract.lp_shares_total.value) == shares_before + 1_000_000_000
        assert int(contract.lp_shares[Account(lp2)]) == 1_000_000_000


def test_contract_claim_lp_residual_handles_large_resolved_pool_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

        bootstrap_payment = make_usdc_payment(context, contract, creator, LARGE_ACTIVE_POOL)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(LARGE_ACTIVE_POOL), bootstrap_payment, latest_timestamp=1)

        buy_payment = make_usdc_payment(context, contract, winner, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
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
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_401)
        opt_in_market(context, contract, creator, latest_timestamp=96_402)

        reserve_before = int(contract._get_total_user_shares(UInt64(0)))
        pool_before = int(contract.pool_balance.value)
        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=96_403)

        assert int(contract.total_residual_claimed.value) > 0
        assert int(contract.pool_balance.value) < pool_before
        assert int(contract.pool_balance.value) >= reserve_before


def test_contract_claim_lp_residual_handles_large_cancelled_pool_without_overflow(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        trader_basis = contract_user_cost_basis(contract, trader, 0)

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)
        opt_in_market(context, contract, creator, latest_timestamp=5_002)
        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=5_003)

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
        ensure_blueprint_cid(contract)

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
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
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
        assert int(contract.pool_balance.value) == payout_before_claim - SHARE_UNIT
        assert contract_user_shares(contract, winner, 0) == 0
        assert contract_q(contract)[0] == 0


def test_withdraw_protocol_fees_sends_to_stored_treasury(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    treasury = make_address()
    attacker = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver, protocol_treasury=treasury)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, treasury, 0)

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
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )

        accrued_protocol_fees = int(contract.protocol_fee_balance.value)
        assert accrued_protocol_fees > 0

        attacker_before = usdc_balance(context, attacker)
        call_as(
            context,
            attacker,
            contract.withdraw_protocol_fees,
            latest_timestamp=5_001,
        )
        transfers = last_inner_asset_transfers(context)
        assert int(contract.protocol_fee_balance.value) == 0
        assert len(transfers) == 1
        assert transfers[0].asset_receiver == Account(treasury)
        assert int(transfers[0].asset_amount) == accrued_protocol_fees
        assert usdc_balance(context, attacker) == attacker_before


def test_withdraw_protocol_fees_drains_dispute_sink_to_treasury(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    challenger = make_address()
    treasury = make_address()
    attacker = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver, protocol_treasury=treasury)
        register_outcome_asas(context, contract, creator)
        ensure_blueprint_cid(contract)
        seed_usdc_balance(context, treasury, 0)

        payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)

        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(context, resolver, contract.propose_resolution, arc4.UInt64(0), arc4.DynamicBytes(b"e" * 32), propose_payment, latest_timestamp=10_001)
        challenge_payment = make_usdc_payment(context, contract, challenger, required_bond(contract, proposal=False))
        call_as(
            context, challenger, contract.challenge_resolution,
            challenge_payment, arc4.UInt64(1), arc4.DynamicBytes(b"c" * 32),
            latest_timestamp=10_002,
        )
        call_as(context, resolver, contract.creator_resolve_dispute, arc4.UInt64(0), arc4.DynamicBytes(b"r" * 32), latest_timestamp=10_003)

        sink_accrued = int(contract.dispute_sink_balance.value)
        protocol_fees_before = int(contract.protocol_fee_balance.value)
        assert sink_accrued > 0

        call_as(context, attacker, contract.withdraw_protocol_fees, latest_timestamp=10_004)

        transfers = last_inner_asset_transfers(context)
        assert int(contract.dispute_sink_balance.value) == 0
        assert int(contract.protocol_fee_balance.value) == 0
        assert len(transfers) == 1
        assert transfers[0].asset_receiver == Account(treasury)
        assert int(transfers[0].asset_amount) == sink_accrued + protocol_fees_before

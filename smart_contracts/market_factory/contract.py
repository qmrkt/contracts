"""Lightweight market factory that stores QuestionMarket bytecode in boxes
and creates fully initialized markets via inner transactions.

Uses a MarketStub for ABI encoding without embedding the real contract bytecode.
"""

from algopy import (
    ARC4Contract,
    Application,
    Asset,
    Box,
    Bytes,
    CompiledContract,
    Global,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
)

from smart_contracts.market_factory.market_stub import MarketStub

MAX_ACTIVE_LP_OUTCOMES = 8
FACTORY_RESERVE = 100_000
MARKET_APP_MIN_FUNDING = 1_616_400
APP_CREATE_BASE_MIN_BALANCE = 100_000
APP_PAGE_MIN_BALANCE = 100_000
APP_GLOBAL_UINT_MIN_BALANCE = 28_500
APP_GLOBAL_BYTES_MIN_BALANCE = 50_000
QUESTION_MARKET_EXTRA_PAGES = 3
QUESTION_MARKET_GLOBAL_UINTS = 48
QUESTION_MARKET_GLOBAL_BYTES = 15
MARKET_CREATOR_MIN_BALANCE = (
    APP_CREATE_BASE_MIN_BALANCE
    + QUESTION_MARKET_EXTRA_PAGES * APP_PAGE_MIN_BALANCE
    + QUESTION_MARKET_GLOBAL_UINTS * APP_GLOBAL_UINT_MIN_BALANCE
    + QUESTION_MARKET_GLOBAL_BYTES * APP_GLOBAL_BYTES_MIN_BALANCE
)
CREATE_MARKET_MIN_FUNDING = (
    FACTORY_RESERVE + MARKET_APP_MIN_FUNDING + MARKET_CREATOR_MIN_BALANCE
)



class MarketFactory(ARC4Contract):
    """Factory for atomic market deployment.

    Stores QuestionMarket approval/clear programs in boxes.
    Creates markets via arc4_create with compiled bytecode from boxes.
    """

    def __init__(self) -> None:
        self.approval_program_box = Box(Bytes, key=b"ap")
        self.clear_program_box = Box(Bytes, key=b"cp")

    @arc4.baremethod(create="require")
    def create(self) -> None:
        pass

    @arc4.abimethod()
    def noop(self) -> None:
        """No-op for box IO budget pooling in transaction groups."""
        pass

    @arc4.abimethod()
    def create_program_box(self, box_key: arc4.DynamicBytes, size: arc4.UInt64) -> None:
        """Create or recreate a program box with the given size. Creator-only."""
        assert Txn.sender == Global.creator_address
        key = box_key.native
        _existed, _val = op.Box.get(key)
        if _existed:
            op.Box.delete(key)
        op.Box.create(key, size.as_uint64())

    @arc4.abimethod()
    def write_program_chunk(self, box_key: arc4.DynamicBytes, offset: arc4.UInt64, data: arc4.DynamicBytes) -> None:
        """Write a chunk of bytecode to a program box at the given offset. Creator-only."""
        assert Txn.sender == Global.creator_address
        op.Box.replace(box_key.native, offset.as_uint64(), data.native)

    @arc4.abimethod()
    def opt_into_asset(self, asset: arc4.UInt64) -> None:
        """Opt factory into a currency ASA so it can receive and forward deposits. Creator-only."""
        assert Txn.sender == Global.creator_address
        itxn.AssetTransfer(
            xfer_asset=Asset(asset.as_uint64()),
            asset_receiver=Global.current_application_address,
            asset_amount=0,
            fee=0,
        ).submit()

    @arc4.abimethod()
    def create_market(
        self,
        currency_asa: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        num_outcomes: arc4.UInt64,
        initial_liquidity_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        blueprint_cid: arc4.DynamicBytes,
        deadline: arc4.UInt64,
        challenge_window_secs: arc4.UInt64,
        market_admin: arc4.Address,
        grace_period_secs: arc4.UInt64,
        cancellable: arc4.Bool,
        lp_entry_max_price_fp: arc4.UInt64,
        deposit_amount: arc4.UInt64,
        algo_funding: gtxn.PaymentTransaction,
        usdc_funding: gtxn.AssetTransferTransaction,
    ) -> arc4.UInt64:
        # Validate ALGO funding
        assert algo_funding.sender == Txn.sender
        assert algo_funding.receiver == Global.current_application_address
        assert algo_funding.amount >= CREATE_MARKET_MIN_FUNDING
        assert algo_funding.rekey_to == Global.zero_address
        assert algo_funding.close_remainder_to == Global.zero_address

        # Validate USDC funding
        assert usdc_funding.sender == Txn.sender
        assert usdc_funding.asset_receiver == Global.current_application_address
        assert usdc_funding.xfer_asset.id == currency_asa.as_uint64()
        assert usdc_funding.asset_amount >= deposit_amount.as_uint64()
        assert usdc_funding.rekey_to == Global.zero_address
        assert usdc_funding.asset_close_to == Global.zero_address

        assert num_outcomes.as_uint64() <= MAX_ACTIVE_LP_OUTCOMES
        protocol_config_id = Txn.applications(1).id
        linked_factory_id, _exists = op.AppGlobal.get_ex_uint64(
            Application(protocol_config_id), b"mfi"
        )
        assert linked_factory_id == Global.current_application_id.id

        # Read stored bytecode from boxes in chunks (AVM stack limit: 4096 bytes per value)
        ap_len, _ap_exists = op.Box.length(Bytes(b"ap"))
        ap_page0 = op.Box.extract(Bytes(b"ap"), UInt64(0), UInt64(4096))
        ap_page1 = op.Box.extract(Bytes(b"ap"), UInt64(4096), ap_len - UInt64(4096))
        clear_program = self.clear_program_box.value

        # ── Inner txn 1: Create market app via arc4_create with compiled bytecode ──
        create_itxn = arc4.arc4_create(
            MarketStub.create,
            arc4.Address(Txn.sender),
            currency_asa,
            num_outcomes,
            initial_liquidity_b,
            lp_fee_bps,
            deadline,
            question_hash,
            blueprint_cid,
            challenge_window_secs,
            arc4.Address(Global.creator_address),
            grace_period_secs,
            market_admin,
            arc4.UInt64(protocol_config_id),
            cancellable,
            lp_entry_max_price_fp,
            compiled=CompiledContract(
                approval_program=(ap_page0, ap_page1),
                clear_state_program=(clear_program, Bytes()),
                extra_program_pages=UInt64(3),
                global_uints=UInt64(48),
                global_bytes=UInt64(15),
                local_uints=UInt64(6),
                local_bytes=UInt64(0),
            ),
            fee=0,
        )

        created_app = create_itxn.created_app
        created_app_addr = created_app.address

        # Retain the creator-side app MBR in the factory account; only forward the
        # created market's app-account funding to the new app address.
        itxn.Payment(
            receiver=created_app_addr,
            amount=algo_funding.amount - FACTORY_RESERVE - MARKET_CREATOR_MIN_BALANCE,
            fee=0,
        ).submit()

        # ── Inner txn 3: Call initialize (opt into currency ASA) ──
        market_initialize_sel = Bytes.from_hex("FD2C93CD")
        itxn.ApplicationCall(
            app_id=created_app,
            app_args=(market_initialize_sel,),
            fee=0,
            assets=(Asset(currency_asa.as_uint64()),),
        ).submit()

        # ── Inner txns 4+5: Forward USDC + bootstrap as a grouped pair ──
        # bootstrap expects a gtxn.AssetTransferTransaction, so the USDC transfer
        # must be in the same inner group as the bootstrap call.
        market_bootstrap_sel = Bytes.from_hex("B49094E1")
        itxn.submit_txns(
            itxn.AssetTransfer(
                xfer_asset=Asset(currency_asa.as_uint64()),
                asset_receiver=created_app_addr,
                asset_amount=usdc_funding.asset_amount,
                fee=0,
            ),
            itxn.ApplicationCall(
                app_id=created_app,
                app_args=(market_bootstrap_sel, deposit_amount.bytes),
                fee=0,
            ),
        )

        return arc4.UInt64(created_app.id)

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
    Global,
    GlobalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
    subroutine,
)

from smart_contracts.market_factory.market_stub import MarketStub

MAX_ACTIVE_LP_OUTCOMES = 8
BPS_DENOMINATOR = 10_000
SECONDS_PER_DAY = 86_400
FACTORY_RESERVE = 100_000
UINT64_MAX = 18_446_744_073_709_551_615

# MBR the factory transfers to the new market app account. Must cover:
#   1. App-account base                            100_000  (0.1 ALGO)
#   2. USDC ASA opt-in                             100_000  (0.1 ALGO)
#   3. Dispute-resolution pp: box buffer           200_000  (~10 pp: boxes × 19_700 μA)
#
# Trader-facing boxes (us:, uc:) are NOT prefunded here — traders pay
# per-buy MBR top-ups that stay in the market app account until app deletion.
# The trader path is the high-volume attack vector.
#
# LP fee accrual uses local state, not boxes. Dispute-resolution callers are
# not necessarily the payout recipient, so pp: boxes remain prefunded.
APP_ACCOUNT_BASE_MBR = 100_000
USDC_OPTIN_MBR = 100_000
DISPUTE_PP_BUFFER = 200_000
MARKET_APP_MIN_FUNDING = (
    APP_ACCOUNT_BASE_MBR + USDC_OPTIN_MBR + DISPUTE_PP_BUFFER
)  # 400_000 μA = 0.4 ALGO

APP_CREATE_BASE_MIN_BALANCE = 100_000
APP_PAGE_MIN_BALANCE = 100_000
APP_GLOBAL_UINT_MIN_BALANCE = 28_500
APP_GLOBAL_BYTES_MIN_BALANCE = 50_000
QUESTION_MARKET_EXTRA_PAGES = 3
QUESTION_MARKET_GLOBAL_UINTS = 49
QUESTION_MARKET_GLOBAL_BYTES = 14
QUESTION_MARKET_LOCAL_UINTS = 5
QUESTION_MARKET_LOCAL_BYTES = 0
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
        self.protocol_config_id = GlobalState(UInt64, key="pci")

    @subroutine
    def _config_uint64(self, app: Application, key: Bytes) -> UInt64:
        value, exists = op.AppGlobal.get_ex_uint64(app, key)
        assert exists
        return value

    @subroutine
    def _mul_div_ceil(self, a: UInt64, b: UInt64, denominator: UInt64) -> UInt64:
        assert denominator > UInt64(0)
        product_high, product_low = op.mulw(a, b)
        quotient_high, quotient_low, remainder_high, remainder_low = op.divmodw(
            product_high,
            product_low,
            UInt64(0),
            denominator,
        )
        assert quotient_high == UInt64(0)
        if remainder_high == UInt64(0) and remainder_low == UInt64(0):
            return quotient_low
        return quotient_low + UInt64(1)

    @subroutine
    def _max_proposer_fee(
        self,
        proposal_bond: UInt64,
        proposal_bond_cap: UInt64,
        proposer_fee_bps: UInt64,
        proposer_fee_floor_bps: UInt64,
        challenge_window_secs: UInt64,
    ) -> UInt64:
        floor_fee = self._mul_div_ceil(proposal_bond, proposer_fee_floor_bps, UInt64(BPS_DENOMINATOR))
        daily_fee = self._mul_div_ceil(proposal_bond_cap, proposer_fee_bps, UInt64(BPS_DENOMINATOR))
        window_fee = self._mul_div_ceil(daily_fee, challenge_window_secs, UInt64(SECONDS_PER_DAY))
        if window_fee > floor_fee:
            return window_fee
        return floor_fee

    @arc4.abimethod(create="require")
    def create(self, protocol_config_id: arc4.UInt64) -> None:
        assert protocol_config_id.as_uint64() > UInt64(0)
        self.protocol_config_id.value = protocol_config_id.as_uint64()

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
        assert algo_funding.amount == CREATE_MARKET_MIN_FUNDING
        assert algo_funding.rekey_to == Global.zero_address
        assert algo_funding.close_remainder_to == Global.zero_address

        # Validate USDC funding
        assert usdc_funding.sender == Txn.sender
        assert usdc_funding.asset_receiver == Global.current_application_address
        assert usdc_funding.xfer_asset.id == currency_asa.as_uint64()
        assert usdc_funding.asset_amount >= deposit_amount.as_uint64()
        assert usdc_funding.asset_sender == Global.zero_address
        assert usdc_funding.rekey_to == Global.zero_address
        assert usdc_funding.asset_close_to == Global.zero_address

        assert num_outcomes.as_uint64() <= MAX_ACTIVE_LP_OUTCOMES
        protocol_config_id = Txn.applications(1).id
        assert protocol_config_id == self.protocol_config_id.value
        protocol_config_app = Application(protocol_config_id)
        linked_factory_id = self._config_uint64(protocol_config_app, Bytes(b"mfi"))
        assert linked_factory_id == Global.current_application_id.id
        assert num_outcomes.as_uint64() <= self._config_uint64(protocol_config_app, Bytes(b"max_outcomes"))
        assert lp_fee_bps.as_uint64() <= self._config_uint64(protocol_config_app, Bytes(b"max_lp_fee_bps"))
        assert (
            lp_fee_bps.as_uint64() + self._config_uint64(protocol_config_app, Bytes(b"pfb"))
            <= UInt64(BPS_DENOMINATOR)
        )
        assert grace_period_secs.as_uint64() >= self._config_uint64(protocol_config_app, Bytes(b"min_grace_period_secs"))
        assert deadline.as_uint64() <= UInt64(UINT64_MAX) - grace_period_secs.as_uint64()
        budget_required = self._max_proposer_fee(
            self._config_uint64(protocol_config_app, Bytes(b"pb")),
            self._config_uint64(protocol_config_app, Bytes(b"pbc")),
            self._config_uint64(protocol_config_app, Bytes(b"pfd")),
            self._config_uint64(protocol_config_app, Bytes(b"pff")),
            challenge_window_secs.as_uint64(),
        )
        assert usdc_funding.asset_amount == deposit_amount.as_uint64() + budget_required

        # Read stored bytecode from boxes in chunks (AVM stack limit: 4096 bytes per value)
        ap_len, _ap_exists = op.Box.length(Bytes(b"ap"))
        ap_page0_len = ap_len
        if ap_page0_len > UInt64(4096):
            ap_page0_len = UInt64(4096)
        ap_page0 = op.Box.extract(Bytes(b"ap"), UInt64(0), ap_page0_len)
        ap_page1 = Bytes()
        ap_page2 = Bytes()
        if ap_len > UInt64(4096):
            ap_page1_len = ap_len - UInt64(4096)
            if ap_page1_len > UInt64(4096):
                ap_page1_len = UInt64(4096)
            ap_page1 = op.Box.extract(Bytes(b"ap"), UInt64(4096), ap_page1_len)
        if ap_len > UInt64(8192):
            ap_page2 = op.Box.extract(Bytes(b"ap"), UInt64(8192), ap_len - UInt64(8192))
        clear_program = self.clear_program_box.value

        # The current market approval program spans five 2KB AVM pages, which no
        # longer fits through CompiledContract's two-slot page tuple. Use a manual
        # inner app-create so we can append all approval pages explicitly.
        market_create_sel = Bytes.from_hex("6F22BE0A")
        create_itxn = itxn.ApplicationCall(
            approval_program=(ap_page0, ap_page1, ap_page2),
            clear_state_program=(clear_program,),
            global_num_uint=UInt64(QUESTION_MARKET_GLOBAL_UINTS),
            global_num_bytes=UInt64(QUESTION_MARKET_GLOBAL_BYTES),
            local_num_uint=UInt64(QUESTION_MARKET_LOCAL_UINTS),
            local_num_bytes=UInt64(QUESTION_MARKET_LOCAL_BYTES),
            extra_program_pages=UInt64(QUESTION_MARKET_EXTRA_PAGES),
            app_args=(
                market_create_sel,
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
            ),
            fee=0,
        ).submit()

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

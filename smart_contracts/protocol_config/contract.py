from algopy import (
    Application,
    ARC4Contract,
    Bytes,
    GlobalState,
    OnCompleteAction,
    Txn,
    UInt64,
    arc4,
    itxn,
    urange,
)

BPS_DENOMINATOR = 10_000
MIN_OUTCOMES = 2

KEY_MIN_BOOTSTRAP_DEPOSIT = b"min_bootstrap_deposit"
KEY_CHALLENGE_BOND = b"challenge_bond"
KEY_PROPOSAL_BOND = b"proposal_bond"
KEY_DEFAULT_B = b"default_b"
KEY_PROTOCOL_FEE_BPS = b"protocol_fee_bps"
KEY_PROTOCOL_FEE_CEILING_BPS = b"protocol_fee_ceiling_bps"
KEY_PROTOCOL_TREASURY = b"protocol_treasury"
KEY_MARKET_FACTORY_ID = b"market_factory_id"
KEY_MAX_OUTCOMES = b"max_outcomes"
KEY_MIN_CHALLENGE_WINDOW_SECS = b"min_challenge_window_secs"
KEY_MIN_GRACE_PERIOD_SECS = b"min_grace_period_secs"
KEY_MAX_LP_FEE_BPS = b"max_lp_fee_bps"


class ProtocolConfig(ARC4Contract):
    """Protocol-wide governance/configuration state stored in global state."""

    def __init__(self) -> None:
        self.admin = GlobalState(Bytes, key="admin")
        self.min_bootstrap_deposit = GlobalState(UInt64, key=KEY_MIN_BOOTSTRAP_DEPOSIT)
        self.challenge_bond = GlobalState(UInt64, key=KEY_CHALLENGE_BOND)
        self.proposal_bond = GlobalState(UInt64, key=KEY_PROPOSAL_BOND)
        self.default_b = GlobalState(UInt64, key=KEY_DEFAULT_B)
        self.protocol_fee_bps = GlobalState(UInt64, key=KEY_PROTOCOL_FEE_BPS)
        self.protocol_fee_ceiling_bps = GlobalState(UInt64, key=KEY_PROTOCOL_FEE_CEILING_BPS)
        self.protocol_treasury = GlobalState(Bytes, key=KEY_PROTOCOL_TREASURY)
        self.market_factory_id = GlobalState(UInt64, key=KEY_MARKET_FACTORY_ID)
        self.max_outcomes = GlobalState(UInt64, key=KEY_MAX_OUTCOMES)
        self.min_challenge_window_secs = GlobalState(UInt64, key=KEY_MIN_CHALLENGE_WINDOW_SECS)
        self.min_grace_period_secs = GlobalState(UInt64, key=KEY_MIN_GRACE_PERIOD_SECS)
        self.max_lp_fee_bps = GlobalState(UInt64, key=KEY_MAX_LP_FEE_BPS)

    def _require(self, condition: bool) -> None:
        assert condition

    def _require_admin(self) -> None:
        self._require(Txn.sender.bytes == self.admin.value)

    def _require_fee_within_ceiling(self, fee_bps: UInt64, ceiling_bps: UInt64) -> None:
        self._require(fee_bps <= ceiling_bps)

    def _require_bps(self, value: UInt64) -> None:
        self._require(value <= UInt64(BPS_DENOMINATOR))

    @arc4.abimethod(create="require")
    def create(
        self,
        admin: arc4.Address,
        min_bootstrap_deposit: arc4.UInt64,
        challenge_bond: arc4.UInt64,
        proposal_bond: arc4.UInt64,
        default_b: arc4.UInt64,
        protocol_fee_ceiling_bps: arc4.UInt64,
        protocol_fee_bps: arc4.UInt64,
        protocol_treasury: arc4.Address,
        market_factory_id: arc4.UInt64,
        max_outcomes: arc4.UInt64,
        min_challenge_window_secs: arc4.UInt64,
        min_grace_period_secs: arc4.UInt64,
        max_lp_fee_bps: arc4.UInt64,
    ) -> None:
        self._require(default_b.as_uint64() > UInt64(0))
        self._require(max_outcomes.as_uint64() >= UInt64(MIN_OUTCOMES))
        self._require(min_challenge_window_secs.as_uint64() > UInt64(0))
        self._require(min_grace_period_secs.as_uint64() > UInt64(0))
        self._require_bps(protocol_fee_ceiling_bps.as_uint64())
        self._require_bps(protocol_fee_bps.as_uint64())
        self._require_bps(max_lp_fee_bps.as_uint64())
        self._require_fee_within_ceiling(protocol_fee_bps.as_uint64(), protocol_fee_ceiling_bps.as_uint64())

        self.admin.value = admin.bytes
        self.min_bootstrap_deposit.value = min_bootstrap_deposit.as_uint64()
        self.challenge_bond.value = challenge_bond.as_uint64()
        self.proposal_bond.value = proposal_bond.as_uint64()
        self.default_b.value = default_b.as_uint64()
        self.protocol_fee_bps.value = protocol_fee_bps.as_uint64()
        self.protocol_fee_ceiling_bps.value = protocol_fee_ceiling_bps.as_uint64()
        self.protocol_treasury.value = protocol_treasury.bytes
        self.market_factory_id.value = market_factory_id.as_uint64()
        self.max_outcomes.value = max_outcomes.as_uint64()
        self.min_challenge_window_secs.value = min_challenge_window_secs.as_uint64()
        self.min_grace_period_secs.value = min_grace_period_secs.as_uint64()
        self.max_lp_fee_bps.value = max_lp_fee_bps.as_uint64()

    @arc4.abimethod()
    def update_admin(self, admin: arc4.Address) -> None:
        self._require_admin()
        self._require(admin.bytes != Bytes(b"\x00" * 32))
        self.admin.value = admin.bytes

    @arc4.abimethod()
    def update_min_bootstrap_deposit(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self.min_bootstrap_deposit.value = value.as_uint64()

    @arc4.abimethod()
    def update_challenge_bond(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self.challenge_bond.value = value.as_uint64()

    @arc4.abimethod()
    def update_proposal_bond(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self.proposal_bond.value = value.as_uint64()

    @arc4.abimethod()
    def update_default_b(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require(value.as_uint64() > UInt64(0))
        self.default_b.value = value.as_uint64()

    @arc4.abimethod()
    def update_protocol_fee_bps(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require_fee_within_ceiling(value.as_uint64(), self.protocol_fee_ceiling_bps.value)
        self.protocol_fee_bps.value = value.as_uint64()

    @arc4.abimethod()
    def update_protocol_fee_ceiling_bps(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require_bps(value.as_uint64())
        self._require_fee_within_ceiling(self.protocol_fee_bps.value, value.as_uint64())
        self.protocol_fee_ceiling_bps.value = value.as_uint64()

    @arc4.abimethod()
    def update_protocol_treasury(self, value: arc4.Address) -> None:
        self._require_admin()
        self.protocol_treasury.value = value.bytes

    @arc4.abimethod()
    def update_market_factory_id(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self.market_factory_id.value = value.as_uint64()

    @arc4.abimethod()
    def update_max_outcomes(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require(value.as_uint64() >= UInt64(MIN_OUTCOMES))
        self.max_outcomes.value = value.as_uint64()

    @arc4.abimethod()
    def update_min_challenge_window_secs(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require(value.as_uint64() > UInt64(0))
        self.min_challenge_window_secs.value = value.as_uint64()

    @arc4.abimethod()
    def update_min_grace_period_secs(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require(value.as_uint64() > UInt64(0))
        self.min_grace_period_secs.value = value.as_uint64()

    @arc4.abimethod()
    def update_max_lp_fee_bps(self, value: arc4.UInt64) -> None:
        self._require_admin()
        self._require_bps(value.as_uint64())
        self.max_lp_fee_bps.value = value.as_uint64()

    @arc4.baremethod()
    def bare_noop(self) -> None:
        pass

    @arc4.abimethod()
    def op_up(self, count: arc4.UInt64) -> None:
        count_val = count.as_uint64()
        self._require(count_val > UInt64(0))
        self._require(self.market_factory_id.value > UInt64(0))
        target_app = Application(self.market_factory_id.value)
        for _i in urange(count_val):
            itxn.ApplicationCall(
                app_id=target_app,
                on_completion=OnCompleteAction.NoOp,
                fee=0,
            ).submit()

    @arc4.abimethod()
    def noop(self) -> None:
        return

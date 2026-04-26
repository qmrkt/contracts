"""Per-market application contract for question.market.

Implements the full market lifecycle: creation, LMSR trading, liquidity provision,
resolution with challenge/dispute, claims, and refunds. All state lives in Algopy
GlobalState, LocalState, and BoxMap storage.
"""

from algopy import (
    Account,
    Application,
    ARC4Contract,
    Array,
    Asset,
    BoxMap,
    Bytes,
    Global,
    GlobalState,
    LocalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
    subroutine,
    urange,
)

from smart_contracts.lmsr_math_avm import (
    SCALE,
    _mul_div_ceil as lmsr_mul_div_ceil,
    _mul_div_floor as lmsr_mul_div_floor,
    lmsr_collateral_required_from_prices,
    lmsr_cost_delta,
    lmsr_prices,
    lmsr_q_from_prices_with_floor,
    lmsr_sell_return,
)
MIN_OUTCOMES = 2
MAX_OUTCOMES = 8
BPS_DENOMINATOR = 10_000
PRICE_TOLERANCE_BASE = 1
STATUS_CREATED = 0
STATUS_ACTIVE = 1
STATUS_RESOLUTION_PENDING = 2
STATUS_RESOLUTION_PROPOSED = 3
STATUS_CANCELLED = 4
STATUS_RESOLVED = 5
STATUS_DISPUTED = 6
SHARE_UNIT = SCALE
MAX_COMMENT_BYTES = 512
ZERO_ADDRESS_BYTES = b"\x00" * 32
DEFAULT_WINNER_SHARE_BPS = 5_000
DEFAULT_DISPUTE_SINK_SHARE_BPS = 5_000
DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP = 150_000
DEFAULT_LP_ENTRY_MAX_PRICE_FP = 800_000
SECONDS_PER_DAY = 86_400

BOX_KEY_USER_SHARES = b"us:"
BOX_KEY_USER_COST_BASIS = b"uc:"
BOX_KEY_PENDING_PAYOUTS = b"pp:"

# ---------------------------------------------------------------------------
# Per-box MBR top-up costs.
# AVM formula: MBR = 2_500 + 400 * (key_size + value_size).
# Every BoxMap below stores a UInt64 (8-byte) value.
#
#   us: key = "us:"(3) + sender(32) + outcome index u64(8) = 43 bytes
#   uc: key = "uc:"(3) + sender(32) + outcome index u64(8) = 43 bytes
#   pp: key = "pp:"(3) + account(32)                       = 35 bytes
#
# Trader-facing buy actions pay these at call time via an ALGO payment
# grouped with the call. The current 4-page approval cap prevents
# delete-on-zero, so the paid MBR stays in the market app account until app
# deletion.
#
# Dispute-resolution methods still create pp: boxes, but they are paid out
# of the market app account's MARKET_APP_MIN_FUNDING prefund — see the
# buffer decomposition in smart_contracts/market_factory/contract.py.
SHARE_BOX_MBR = 2_500 + 400 * (3 + 32 + 8 + 8)   # 22_900
COST_BOX_MBR = 2_500 + 400 * (3 + 32 + 8 + 8)    # 22_900
PENDING_PAYOUT_BOX_MBR = 2_500 + 400 * (3 + 32 + 8)  # 19_700

# State and event schema consumed by the acceptance harness and ARC-56 tooling.
# creator:
# currency_asa:
# num_outcomes:
# b:
# pool_balance:
# lp_shares_total:
# lp_fee_bps:
# protocol_fee_bps:
# cumulative_fee_per_share:
# status:
# deadline:
# question_hash:
# blueprint_cid:
# qp (packed q0..q7):
# tp (packed t0..t7):
# proposed_outcome:
# proposal_timestamp:
# proposal_evidence_hash:
# challenge_window_secs:
# challenger:
# market_admin:
# challenge_reason_code:
# challenge_evidence_hash:
# dispute_ref_hash:
# dispute_opened_at:
# dispute_deadline:
# ruling_hash:
# resolution_path_used:
# dispute_backend_kind:
# pending_responder_role:
# total_outstanding_cost_basis:
# dispute_sink_balance:
# winner_share_bps:
# dispute_sink_share_bps:
# protocol_treasury:
# lp_shares:
# fee_snapshot:
# activation_timestamp:
# settlement_timestamp:
# residual_linear_lambda_fp:
# lp_entry_max_price_fp:
# total_lp_weighted_entry_sum:
# total_residual_claimed:
# withdrawable_fee_surplus:
# lp_weighted_entry_sum:
# residual_claimed:
# ARC-28 events:
# arc4.emit("Bootstrap()")
# arc4.emit("Buy(uint64)")
# arc4.emit("Sell(uint64)")
# arc4.emit("TriggerResolution()")
# arc4.emit("ProposeResolution(uint64,byte[])")
# arc4.emit("ProposeEarlyResolution(uint64,byte[])")
# arc4.emit("ChallengeResolution()")
# arc4.emit("RegisterDispute()")
# arc4.emit("CreatorResolveDispute()")
# arc4.emit("AdminResolveDispute()")
# arc4.emit("FinalizeDispute()")
# arc4.emit("AbortEarlyResolution(byte[],uint64)")
# arc4.emit("CancelDisputeAndMarket()")
# arc4.emit("FinalizeResolution()")
# arc4.emit("Claim(uint64)")
# arc4.emit("Cancel()")
# arc4.emit("Refund(uint64)")
# arc4.emit("WithdrawPendingPayouts(uint64)")
# arc4.emit("CommentPosted(string)")


class QuestionMarket(ARC4Contract):
    @arc4.baremethod(allow_actions=["OptIn"])
    def opt_in(self) -> None:
        bs = self.bootstrapper_lp_shares.value
        if bs > UInt64(0) and Txn.sender.bytes == self.creator.value:
            self.lp_shares[Txn.sender] = bs
            self.fee_snapshot[Txn.sender] = self.cumulative_fee_per_share.value
            self.lp_weighted_entry_sum[Txn.sender] = self.bootstrapper_lp_entry.value
            self.bootstrapper_lp_shares.value = UInt64(0)

    @arc4.baremethod(allow_actions=["NoOp"])
    def bare_noop(self) -> None:
        pass

    def __init__(self) -> None:
        self.creator = GlobalState(Bytes, key="cr")
        self.currency_asa = GlobalState(UInt64, key="ca")
        self.num_outcomes = GlobalState(UInt64, key="no")
        self.b = GlobalState(UInt64, key="b")
        self.pool_balance = GlobalState(UInt64, key="pb")
        self.bootstrap_deposit = GlobalState(UInt64, key="bd")
        self.lp_shares_total = GlobalState(UInt64, key="lst")
        self.lp_fee_bps = GlobalState(UInt64, key="lfb")
        self.protocol_fee_bps = GlobalState(UInt64, key="pfb")
        self.cumulative_fee_per_share = GlobalState(UInt64, key="cfs")
        self.status = GlobalState(UInt64, key="st")
        self.deadline = GlobalState(UInt64, key="dl")
        self.question_hash = GlobalState(Bytes, key="qh")
        self.proposed_outcome = GlobalState(UInt64, key="po")
        self.proposal_timestamp = GlobalState(UInt64, key="pts")
        self.proposal_evidence_hash = GlobalState(Bytes, key="peh")
        self.challenge_window_secs = GlobalState(UInt64, key="cws")
        self.challenger = GlobalState(Bytes, key="ch")
        self.protocol_config_id = GlobalState(UInt64, key="pc")
        self.protocol_treasury = GlobalState(Bytes, key="pt")
        self.activation_timestamp = GlobalState(UInt64, key="ats")
        self.settlement_timestamp = GlobalState(UInt64, key="sts")
        self.residual_linear_lambda_fp = GlobalState(UInt64, key="rlf")
        self.lp_entry_max_price_fp = GlobalState(UInt64, key="lpm")
        self.total_lp_weighted_entry_sum = GlobalState(UInt64, key="les")
        self.total_residual_claimed = GlobalState(UInt64, key="rct")

        self.resolution_authority = GlobalState(Bytes, key="ra")
        self.resolution_budget_balance = GlobalState(UInt64, key="rbb")
        self.challenge_bond = GlobalState(UInt64, key="cb")
        self.challenge_bond_bps = GlobalState(UInt64, key="cbb")
        self.challenge_bond_cap = GlobalState(UInt64, key="cbc")
        self.market_admin = GlobalState(Bytes, key="ma")
        self.cancellable = GlobalState(UInt64, key="cn")
        self.winning_outcome = GlobalState(UInt64, key="wo")
        self.lp_fee_balance = GlobalState(UInt64, key="lfbl")
        self.protocol_fee_balance = GlobalState(UInt64, key="pfbl")
        self.total_outstanding_cost_basis = GlobalState(UInt64, key="tcb")
        self.dispute_sink_balance = GlobalState(UInt64, key="dsb")
        self.winner_share_bps = GlobalState(UInt64, key="wsb")
        self.dispute_sink_share_bps = GlobalState(UInt64, key="ssb")

        # Dispute metadata
        self.challenge_reason_code = GlobalState(UInt64, key="crc")
        self.challenge_evidence_hash = GlobalState(Bytes, key="ceh")
        self.dispute_ref_hash = GlobalState(Bytes, key="drh")
        self.dispute_opened_at = GlobalState(UInt64, key="doa")
        self.dispute_deadline = GlobalState(UInt64, key="ddl")
        self.ruling_hash = GlobalState(Bytes, key="rh")
        self.resolution_path_used = GlobalState(UInt64, key="rpu")
        self.dispute_backend_kind = GlobalState(UInt64, key="dbk")
        self.pending_responder_role = GlobalState(UInt64, key="prr")

        self.proposer = GlobalState(Bytes, key="pr")
        self.proposer_bond_held = GlobalState(UInt64, key="pbh")
        self.challenger_bond_held = GlobalState(UInt64, key="cbh")
        self.proposal_bond = GlobalState(UInt64, key="prb")
        self.proposal_bond_bps = GlobalState(UInt64, key="pbb")
        self.proposal_bond_cap = GlobalState(UInt64, key="pbc")
        self.proposer_fee_bps = GlobalState(UInt64, key="pfd")
        self.proposer_fee_floor_bps = GlobalState(UInt64, key="pff")
        self.grace_period_secs = GlobalState(UInt64, key="gps")

        self.lp_shares = LocalState(UInt64, key="ls")
        self.fee_snapshot = LocalState(UInt64, key="fs")
        self.withdrawable_fee_surplus = LocalState(UInt64, key="wfs")
        self.lp_weighted_entry_sum = LocalState(UInt64, key="les")
        self.residual_claimed = LocalState(UInt64, key="rc")

        # Packed outcome quantities: 8 x UInt64 = 64 bytes
        self.quantities_packed = GlobalState(Bytes, key="qp")
        # Packed total user shares: 8 x UInt64 = 64 bytes
        self.total_shares_packed = GlobalState(Bytes, key="tp")

        # IPFS CID for combined blueprint (main + dispute)
        self.blueprint_cid = GlobalState(Bytes, key="bcid")
        # Bootstrapper LP info (claimed on first opt-in by creator)
        self.bootstrapper_lp_shares = GlobalState(UInt64, key="bls")
        self.bootstrapper_lp_entry = GlobalState(UInt64, key="ble")

        self.user_outcome_shares_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_USER_SHARES)
        self.user_cost_basis_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_USER_COST_BASIS)
        self.pending_payouts_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_PENDING_PAYOUTS)

    def _require(self, condition: bool) -> None:
        assert condition

    def _require_share_granularity(self, shares: UInt64) -> None:
        self._require(shares >= UInt64(SHARE_UNIT))
        self._require(shares % UInt64(SHARE_UNIT) == UInt64(0))

    def _sender_fee_key(self) -> Bytes:
        return Txn.sender.bytes

    def _sender_outcome_key(self, outcome_index: UInt64) -> Bytes:
        return op.concat(Txn.sender.bytes, op.itob(outcome_index))

    def _get_lp_shares(self) -> UInt64:
        return self.lp_shares.get(Txn.sender, default=UInt64(0))

    def _set_lp_shares(self, value: UInt64) -> None:
        self.lp_shares[Txn.sender] = value

    def _get_fee_snapshot(self) -> UInt64:
        return self.fee_snapshot.get(Txn.sender, default=UInt64(0))

    def _set_fee_snapshot(self, value: UInt64) -> None:
        self.fee_snapshot[Txn.sender] = value

    def _get_withdrawable_fee_surplus(self) -> UInt64:
        return self.withdrawable_fee_surplus.get(Txn.sender, default=UInt64(0))

    def _set_withdrawable_fee_surplus(self, value: UInt64) -> None:
        self.withdrawable_fee_surplus[Txn.sender] = value

    def _get_lp_weighted_entry_sum(self) -> UInt64:
        return self.lp_weighted_entry_sum.get(Txn.sender, default=UInt64(0))

    def _set_lp_weighted_entry_sum(self, value: UInt64) -> None:
        self.lp_weighted_entry_sum[Txn.sender] = value

    def _get_residual_claimed(self) -> UInt64:
        return self.residual_claimed.get(Txn.sender, default=UInt64(0))

    def _set_residual_claimed(self, value: UInt64) -> None:
        self.residual_claimed[Txn.sender] = value

    def _get_user_outcome_shares(self, outcome_index: UInt64) -> UInt64:
        return self.user_outcome_shares_box.get(self._sender_outcome_key(outcome_index), default=UInt64(0))

    def _set_user_outcome_shares(self, outcome_index: UInt64, value: UInt64) -> None:
        self.user_outcome_shares_box[self._sender_outcome_key(outcome_index)] = value

    def _get_user_cost_basis(self, outcome_index: UInt64) -> UInt64:
        return self.user_cost_basis_box.get(self._sender_outcome_key(outcome_index), default=UInt64(0))

    def _set_user_cost_basis(self, outcome_index: UInt64, value: UInt64) -> None:
        self.user_cost_basis_box[self._sender_outcome_key(outcome_index)] = value

    def _get_pending_payout(self, account: Bytes) -> UInt64:
        return self.pending_payouts_box.get(account, default=UInt64(0))

    def _set_pending_payout(self, account: Bytes, value: UInt64) -> None:
        self.pending_payouts_box[account] = value

    def _get_quantity(self, idx: UInt64) -> UInt64:
        return op.btoi(op.extract(self.quantities_packed.value, idx * UInt64(8), UInt64(8)))

    def _set_quantity(self, idx: UInt64, val: UInt64) -> None:
        self.quantities_packed.value = op.replace(self.quantities_packed.value, idx * UInt64(8), op.itob(val))

    def _get_total_user_shares(self, outcome_index: UInt64) -> UInt64:
        self._assert_valid_outcome(outcome_index)
        return op.btoi(op.extract(self.total_shares_packed.value, outcome_index * UInt64(8), UInt64(8)))

    def _set_total_user_shares(self, outcome_index: UInt64, value: UInt64) -> None:
        self._assert_valid_outcome(outcome_index)
        self.total_shares_packed.value = op.replace(self.total_shares_packed.value, outcome_index * UInt64(8), op.itob(value))

    def _increment_total_user_shares(self, outcome_index: UInt64, amount: UInt64) -> None:
        self._set_total_user_shares(outcome_index, self._get_total_user_shares(outcome_index) + amount)

    def _decrement_total_user_shares(self, outcome_index: UInt64, amount: UInt64) -> None:
        current = self._get_total_user_shares(outcome_index)
        self._require(current >= amount)
        self._set_total_user_shares(outcome_index, current - amount)

    def _credit_pending_payout(self, account: Bytes, amount: UInt64) -> None:
        if amount == UInt64(0):
            return
        self._require(account != Bytes(ZERO_ADDRESS_BYTES))
        self._set_pending_payout(account, self._get_pending_payout(account) + amount)

    def _settle_dispute_and_credit(self, outcome: UInt64, original_proposal: UInt64) -> None:
        if outcome == original_proposal:
            proposer_payout = self._settle_confirmed_dispute()
            self._credit_pending_payout(self.proposer.value, proposer_payout)
        else:
            challenger_payout = self._settle_overturned_dispute()
            self._credit_pending_payout(self.challenger.value, challenger_payout)

    @subroutine
    def _basis_reduction(self, outcome_index: UInt64, shares: UInt64) -> UInt64:
        current_shares = self._get_user_outcome_shares(outcome_index)
        current_basis = self._get_user_cost_basis(outcome_index)
        self._require(current_shares >= shares)
        if current_shares == shares:
            return current_basis
        return lmsr_mul_div_floor(current_basis, shares, current_shares)

    @subroutine
    def _get_q(self) -> Array[UInt64]:
        self._require(self.num_outcomes.value >= UInt64(1))
        values = Array[UInt64]((self._get_quantity(UInt64(0)),))
        for offset in urange(self.num_outcomes.value - UInt64(1)):
            idx = offset + UInt64(1)
            values.append(self._get_quantity(idx))
        return values

    @subroutine
    def _set_q(self, values: Array[UInt64]) -> None:
        for idx in urange(values.length):
            self._set_quantity(idx, values[idx])

    @subroutine
    def _sum_array(self, values: Array[UInt64]) -> UInt64:
        total = UInt64(0)
        for idx in urange(values.length):
            total = total + values[idx]
        return total

    @subroutine
    def _max_array(self, values: Array[UInt64]) -> UInt64:
        max_value = UInt64(0)
        for idx in urange(values.length):
            if values[idx] > max_value:
                max_value = values[idx]
        return max_value

    def _abs_diff(self, a: UInt64, b: UInt64) -> UInt64:
        if a >= b:
            return a - b
        return b - a

    @subroutine
    def _ceil_div(self, numerator: UInt64, denominator: UInt64) -> UInt64:
        self._require(denominator > UInt64(0))
        quotient = numerator // denominator
        remainder = numerator % denominator
        if remainder == UInt64(0):
            return quotient
        return quotient + UInt64(1)


    @subroutine
    def _calc_fee_up(self, amount: UInt64, bps: UInt64) -> UInt64:
        return lmsr_mul_div_ceil(amount, bps, UInt64(BPS_DENOMINATOR))

    @subroutine
    def _config_uint64(self, app: Application, key: Bytes) -> UInt64:
        value, exists = op.AppGlobal.get_ex_uint64(app, key)
        self._require(exists)
        return value

    @subroutine
    def _config_bytes(self, app: Application, key: Bytes) -> Bytes:
        value, exists = op.AppGlobal.get_ex_bytes(app, key)
        self._require(exists)
        return value

    @subroutine
    def _bond_scale_base(self) -> UInt64:
        if self.pool_balance.value >= self.bootstrap_deposit.value:
            return self.pool_balance.value
        return self.bootstrap_deposit.value

    @subroutine
    def _required_bond(self, minimum: UInt64, bps: UInt64, cap: UInt64) -> UInt64:
        proportional = lmsr_mul_div_ceil(self._bond_scale_base(), bps, UInt64(BPS_DENOMINATOR))
        bounded = proportional
        if bounded < minimum:
            bounded = minimum
        if bounded > cap:
            bounded = cap
        return bounded

    @subroutine
    def _required_proposal_bond(self) -> UInt64:
        return self._required_bond(
            self.proposal_bond.value,
            self.proposal_bond_bps.value,
            self.proposal_bond_cap.value,
        )

    @subroutine
    def _required_challenge_bond(self) -> UInt64:
        return self._required_bond(
            self.challenge_bond.value,
            self.challenge_bond_bps.value,
            self.challenge_bond_cap.value,
        )

    @subroutine
    def _proposer_fee_for_bond(self, required_bond: UInt64) -> UInt64:
        floor_fee = self._calc_fee_up(self.proposal_bond.value, self.proposer_fee_floor_bps.value)
        daily_fee = self._calc_fee_up(required_bond, self.proposer_fee_bps.value)
        window_fee = lmsr_mul_div_ceil(daily_fee, self.challenge_window_secs.value, UInt64(SECONDS_PER_DAY))
        if window_fee > floor_fee:
            return window_fee
        return floor_fee

    @subroutine
    def _required_proposer_fee(self) -> UInt64:
        return self._proposer_fee_for_bond(self._required_proposal_bond())

    @subroutine
    def _max_proposer_fee(self) -> UInt64:
        return self._proposer_fee_for_bond(self.proposal_bond_cap.value)

    @subroutine
    def _consume_proposer_fee(self) -> UInt64:
        fee = self._required_proposer_fee()
        self._require(self.resolution_budget_balance.value >= fee)
        self.resolution_budget_balance.value = self.resolution_budget_balance.value - fee
        return fee

    @subroutine
    def _lmsr_bootstrap_multiplier(self) -> UInt64:
        if self.num_outcomes.value <= UInt64(2):
            return UInt64(1)
        if self.num_outcomes.value <= UInt64(7):
            return UInt64(2)
        return UInt64(3)

    @subroutine
    def _require_lmsr_bootstrap_floor(self, deposit: UInt64) -> None:
        required_high, required_low = op.mulw(self.b.value, self._lmsr_bootstrap_multiplier())
        self._require(required_high == UInt64(0))
        self._require(deposit >= required_low)

    def _require_status(self, expected: UInt64) -> None:
        self._require(self.status.value == expected)

    def _require_status_any2(self, expected_a: UInt64, expected_b: UInt64) -> None:
        self._require(self.status.value == expected_a or self.status.value == expected_b)

    def _require_authorized(self, expected: Bytes) -> None:
        self._require(Txn.sender.bytes == expected)

    def _assert_valid_outcome(self, outcome_index: UInt64) -> None:
        self._require(outcome_index < self.num_outcomes.value)

    def _sender_can_comment(self) -> bool:
        for idx in urange(self.num_outcomes.value):
            if self._get_user_outcome_shares(idx) > UInt64(0):
                return True
        if self._get_lp_shares() > UInt64(0):
            return True
        return False

    @subroutine
    def _settle_lp_fees(self) -> None:
        shares = self._get_lp_shares()
        snapshot = self._get_fee_snapshot()
        cumulative = self.cumulative_fee_per_share.value
        if shares == UInt64(0):
            self._set_fee_snapshot(cumulative)
            return
        if cumulative > snapshot:
            delta = cumulative - snapshot
            accrued = lmsr_mul_div_floor(delta, shares, UInt64(SCALE))
            self._set_withdrawable_fee_surplus(self._get_withdrawable_fee_surplus() + accrued)
        self._set_fee_snapshot(cumulative)

    @subroutine
    def _current_reserve_requirement(self) -> UInt64:
        if self.status.value == UInt64(STATUS_RESOLVED) and self.winning_outcome.value < self.num_outcomes.value:
            return self._get_total_user_shares(self.winning_outcome.value)
        if self.status.value == UInt64(STATUS_CANCELLED):
            return self.total_outstanding_cost_basis.value
        return UInt64(0)

    @subroutine
    def _releasable_residual_pool(self) -> UInt64:
        if self.status.value != UInt64(STATUS_RESOLVED) and self.status.value != UInt64(STATUS_CANCELLED):
            return UInt64(0)
        free_pool = self.pool_balance.value + self.total_residual_claimed.value
        reserve = self._current_reserve_requirement()
        if free_pool <= reserve:
            return UInt64(0)
        return free_pool - reserve

    @subroutine
    def _normalized_residual_window(self) -> UInt64:
        if self.settlement_timestamp.value <= self.activation_timestamp.value + UInt64(1):
            return UInt64(0)
        return self.settlement_timestamp.value - self.activation_timestamp.value - UInt64(1)

    @subroutine
    def _calculate_weight(self, shares: UInt64, entry_sum: UInt64) -> UInt64:
        if shares == UInt64(0):
            return UInt64(0)
        window = self._normalized_residual_window()
        if window == UInt64(0):
            return shares
        elapsed_scaled = self._entry_weighted_sum_checked(shares, self.settlement_timestamp.value - UInt64(1))
        if elapsed_scaled <= entry_sum:
            return shares
        window_high, window_scaled = op.mulw(window, UInt64(SCALE))
        self._require(window_high == UInt64(0))
        premium = lmsr_mul_div_floor(
            self.residual_linear_lambda_fp.value,
            elapsed_scaled - entry_sum,
            window_scaled,
        )
        return shares + premium

    @subroutine
    def _residual_weight(self) -> UInt64:
        return self._calculate_weight(self._get_lp_shares(), self._get_lp_weighted_entry_sum())

    @subroutine
    def _total_residual_weight(self) -> UInt64:
        return self._calculate_weight(self.lp_shares_total.value, self.total_lp_weighted_entry_sum.value)

    @subroutine
    def _claimable_residual(self) -> UInt64:
        if self.status.value != UInt64(STATUS_RESOLVED) and self.status.value != UInt64(STATUS_CANCELLED):
            return UInt64(0)
        total_weight = self._total_residual_weight()
        if total_weight == UInt64(0):
            return UInt64(0)
        entitled = lmsr_mul_div_floor(self._releasable_residual_pool(), self._residual_weight(), total_weight)
        already_claimed = self._get_residual_claimed()
        if entitled <= already_claimed:
            return UInt64(0)
        return entitled - already_claimed

    @subroutine
    def _entry_weighted_sum_checked(self, shares: UInt64, timestamp: UInt64) -> UInt64:
        high, low = op.mulw(shares, timestamp)
        self._require(high == UInt64(0))
        return low

    @subroutine
    def _record_settlement_timestamp_now(self) -> None:
        self.settlement_timestamp.value = self._now()

    @subroutine
    def _record_settlement_timestamp_dispute(self) -> None:
        timestamp = self.proposal_timestamp.value
        if self.dispute_opened_at.value > timestamp:
            timestamp = self.dispute_opened_at.value
        if timestamp == UInt64(0):
            timestamp = UInt64(1)
        self.settlement_timestamp.value = timestamp

    @subroutine
    def _distribute_lp_fee(self, fee_amount: UInt64) -> None:
        self.lp_fee_balance.value = self.lp_fee_balance.value + fee_amount
        if self.lp_shares_total.value > UInt64(0) and fee_amount > UInt64(0):
            increment = lmsr_mul_div_floor(fee_amount, UInt64(SCALE), self.lp_shares_total.value)
            self.cumulative_fee_per_share.value = self.cumulative_fee_per_share.value + increment

    @subroutine
    def _assert_price_sum(self) -> None:
        if self.status.value == UInt64(STATUS_CREATED) or self.b.value == UInt64(0):
            return
        prices = lmsr_prices(self._get_q(), self.b.value)
        total_price = self._sum_array(prices)
        self._require(self._abs_diff(total_price, UInt64(SCALE)) <= self.num_outcomes.value)

    @subroutine
    def _assert_solvency(self) -> None:
        # During active trading, LMSR's bounded-loss property means pool_balance
        # can be less than max(q) by up to b*ln(N). This is expected and provably
        # bounded. We only enforce the strict solvency invariant after resolution,
        # when the actual payout obligation is known.
        if self.status.value == UInt64(STATUS_RESOLVED) and self.winning_outcome.value < self.num_outcomes.value:
            winning_payout = self._get_total_user_shares(self.winning_outcome.value)
            self._require(self.pool_balance.value >= winning_payout)

    @subroutine
    def _assert_invariants(self) -> None:
        st = self.status.value
        if st >= UInt64(STATUS_ACTIVE) and st <= UInt64(STATUS_DISPUTED):
            self._require(self.winner_share_bps.value + self.dispute_sink_share_bps.value <= UInt64(BPS_DENOMINATOR))
            self._assert_solvency()
            if st == UInt64(STATUS_CANCELLED) or st == UInt64(STATUS_DISPUTED):
                self._require(self.pool_balance.value >= self.total_outstanding_cost_basis.value)
            if st != UInt64(STATUS_RESOLVED):
                self._assert_price_sum()

    def _active_before_deadline(self) -> None:
        self._require_status(UInt64(STATUS_ACTIVE))
        self._require(self._now() < self.deadline.value)

    def _now(self) -> UInt64:
        # The identity op ensures UInt64 type for both AVM and testing framework
        return Global.latest_timestamp + UInt64(0)

    def _verify_payment(self, payment: gtxn.AssetTransferTransaction, min_amount: UInt64, expected_index: UInt64) -> None:
        self._require(payment.group_index == expected_index)
        self._require(payment.sender.bytes == Txn.sender.bytes)
        self._require(payment.asset_receiver == Global.current_application_address)
        self._require(payment.xfer_asset.id == self.currency_asa.value)
        self._require(payment.asset_amount >= min_amount)
        self._require(payment.asset_sender.bytes == Global.zero_address.bytes)
        self._require(payment.rekey_to.bytes == Global.zero_address.bytes)
        self._require(payment.asset_close_to.bytes == Global.zero_address.bytes)

    def _send_currency(self, receiver: Account, amount: UInt64) -> None:
        itxn.AssetTransfer(
            xfer_asset=Asset(self.currency_asa.value),
            asset_receiver=receiver,
            asset_amount=amount,
            fee=0,
        ).submit()

    # -----------------------------------------------------------------
    # MBR top-up helpers.
    # -----------------------------------------------------------------


    @arc4.abimethod()
    def claim_lp_fees(self) -> None:
        before = self._get_withdrawable_fee_surplus()
        self._settle_lp_fees()
        claimed = self._get_withdrawable_fee_surplus() - before
        self._require(claimed > UInt64(0))
        arc4.emit("ClaimLpFees(uint64)", arc4.UInt64(claimed))

    @arc4.abimethod()
    def withdraw_lp_fees(self, amount: arc4.UInt64) -> None:
        withdraw_amount = amount.as_uint64()
        self._require(withdraw_amount > UInt64(0))
        self._require(self._get_withdrawable_fee_surplus() >= withdraw_amount)
        self._require(self.lp_fee_balance.value >= withdraw_amount)
        self._set_withdrawable_fee_surplus(self._get_withdrawable_fee_surplus() - withdraw_amount)
        self.lp_fee_balance.value = self.lp_fee_balance.value - withdraw_amount
        self._send_currency(Txn.sender, withdraw_amount)
        arc4.emit("WithdrawLpFees(uint64)", amount)

    @arc4.abimethod()
    def claim_lp_residual(self) -> None:
        self._require_status_any2(UInt64(STATUS_RESOLVED), UInt64(STATUS_CANCELLED))
        payout = self._claimable_residual()
        self._require(payout > UInt64(0))
        reserve = self._current_reserve_requirement()
        self._require(self.pool_balance.value >= reserve + payout)
        self.pool_balance.value = self.pool_balance.value - payout
        self.total_residual_claimed.value = self.total_residual_claimed.value + payout
        self._set_residual_claimed(self._get_residual_claimed() + payout)
        self._send_currency(Txn.sender, payout)
        arc4.emit("ClaimLpResidual(uint64)", arc4.UInt64(payout))

    @subroutine
    def _winner_bonus_from_bond(self, losing_bond: UInt64) -> UInt64:
        return lmsr_mul_div_floor(losing_bond, self.winner_share_bps.value, UInt64(BPS_DENOMINATOR))

    @subroutine
    def _settle_confirmed_dispute(self) -> UInt64:
        losing_bond = self.challenger_bond_held.value
        winner_bonus = self._winner_bonus_from_bond(losing_bond)
        proposer_fee = self._consume_proposer_fee()
        self.dispute_sink_balance.value = self.dispute_sink_balance.value + (losing_bond - winner_bonus)
        proposer_payout = self.proposer_bond_held.value + winner_bonus + proposer_fee
        self.proposer_bond_held.value = UInt64(0)
        self.challenger_bond_held.value = UInt64(0)
        return proposer_payout

    @subroutine
    def _settle_overturned_dispute(self) -> UInt64:
        losing_bond = self.proposer_bond_held.value
        winner_bonus = self._winner_bonus_from_bond(losing_bond)
        self.dispute_sink_balance.value = self.dispute_sink_balance.value + (losing_bond - winner_bonus)
        challenger_payout = self.challenger_bond_held.value + winner_bonus
        self.proposer_bond_held.value = UInt64(0)
        self.challenger_bond_held.value = UInt64(0)
        return challenger_payout

    @subroutine
    def _settle_cancelled_dispute(self) -> UInt64:
        challenger_payout = self.challenger_bond_held.value
        self.dispute_sink_balance.value = self.dispute_sink_balance.value + self.proposer_bond_held.value
        self.proposer_bond_held.value = UInt64(0)
        self.challenger_bond_held.value = UInt64(0)
        return challenger_payout

    @subroutine
    def _clear_proposal_and_dispute_metadata(self) -> None:
        self.proposed_outcome.value = UInt64(0)
        self.proposal_timestamp.value = UInt64(0)
        self.proposal_evidence_hash.value = Bytes()
        self.proposer.value = Bytes(ZERO_ADDRESS_BYTES)
        self.challenger.value = Bytes(ZERO_ADDRESS_BYTES)
        self.challenge_reason_code.value = UInt64(0)
        self.challenge_evidence_hash.value = Bytes()
        self.dispute_ref_hash.value = Bytes()
        self.dispute_opened_at.value = UInt64(0)
        self.dispute_deadline.value = UInt64(0)
        self.ruling_hash.value = Bytes()
        self.resolution_path_used.value = UInt64(0)
        self.dispute_backend_kind.value = UInt64(0)
        self.pending_responder_role.value = UInt64(0)

    @arc4.abimethod(create="require")
    def create(
        self,
        creator: arc4.Address,
        currency_asa: arc4.UInt64,
        num_outcomes: arc4.UInt64,
        initial_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        deadline: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        blueprint_cid: arc4.DynamicBytes,
        challenge_window_secs: arc4.UInt64,
        resolution_authority: arc4.Address,
        grace_period_secs: arc4.UInt64,
        market_admin: arc4.Address,
        protocol_config_id: arc4.UInt64,
        cancellable: arc4.Bool,
        lp_entry_max_price_fp: arc4.UInt64,
    ) -> None:
        outcome_count = num_outcomes.as_uint64()
        protocol_config = protocol_config_id.as_uint64()
        lp_entry_cap = lp_entry_max_price_fp.as_uint64()
        config_app = Application(protocol_config)
        zero_address = Bytes(ZERO_ADDRESS_BYTES)
        min_challenge_window_secs = self._config_uint64(config_app, Bytes(b"mcw"))
        challenge_bond_min = self._config_uint64(config_app, Bytes(b"cb"))
        proposal_bond_min = self._config_uint64(config_app, Bytes(b"pb"))
        challenge_bond_bps_val = self._config_uint64(config_app, Bytes(b"cbb"))
        proposal_bond_bps_val = self._config_uint64(config_app, Bytes(b"pbb"))
        challenge_bond_cap_val = self._config_uint64(config_app, Bytes(b"cbc"))
        proposal_bond_cap_val = self._config_uint64(config_app, Bytes(b"pbc"))
        proposer_fee_bps_val = self._config_uint64(config_app, Bytes(b"pfd"))
        proposer_fee_floor_bps_val = self._config_uint64(config_app, Bytes(b"pff"))
        protocol_fee_bps_val = self._config_uint64(config_app, Bytes(b"pfb"))
        linked_factory_id = self._config_uint64(config_app, Bytes(b"mfi"))
        protocol_treasury_val = self._config_bytes(config_app, Bytes(b"pt"))
        residual_linear_lambda_fp_val = self._config_uint64(config_app, Bytes(b"rlf"))
        self._require(outcome_count >= UInt64(MIN_OUTCOMES))
        self._require(outcome_count <= UInt64(MAX_OUTCOMES))
        self._require(initial_b.as_uint64() > UInt64(0))
        self._require(challenge_window_secs.as_uint64() >= min_challenge_window_secs)
        self._require(lp_entry_cap <= UInt64(SCALE))
        self._require(linked_factory_id > UInt64(0))
        self._require(Global.creator_address == Application(linked_factory_id).address)
        self._require(creator.bytes != zero_address)
        self._require(resolution_authority.bytes != zero_address)
        self._require(market_admin.bytes != zero_address)
        self._require(protocol_treasury_val != zero_address)

        self.creator.value = creator.bytes
        self.currency_asa.value = currency_asa.as_uint64()
        self.num_outcomes.value = outcome_count
        self.b.value = initial_b.as_uint64()
        self.pool_balance.value = UInt64(0)
        self.bootstrap_deposit.value = UInt64(0)
        self.lp_shares_total.value = UInt64(0)
        self.lp_fee_bps.value = lp_fee_bps.as_uint64()
        self.protocol_fee_bps.value = protocol_fee_bps_val
        self.cumulative_fee_per_share.value = UInt64(0)
        self.status.value = UInt64(STATUS_CREATED)
        self._require(deadline.as_uint64() > self._now())
        self.deadline.value = deadline.as_uint64()
        self.question_hash.value = question_hash.bytes
        self.blueprint_cid.value = blueprint_cid.native
        self.proposed_outcome.value = UInt64(0)
        self.proposal_timestamp.value = UInt64(0)
        self.proposal_evidence_hash.value = Bytes()
        self.challenge_window_secs.value = challenge_window_secs.as_uint64()
        self.challenger.value = Bytes(ZERO_ADDRESS_BYTES)
        self.protocol_config_id.value = protocol_config
        self.protocol_treasury.value = protocol_treasury_val
        self.residual_linear_lambda_fp.value = residual_linear_lambda_fp_val
        self.lp_entry_max_price_fp.value = lp_entry_cap
        self.activation_timestamp.value = UInt64(0)
        self.settlement_timestamp.value = UInt64(0)
        self.total_lp_weighted_entry_sum.value = UInt64(0)
        self.total_residual_claimed.value = UInt64(0)
        self.resolution_authority.value = resolution_authority.bytes
        self.resolution_budget_balance.value = UInt64(0)
        self.challenge_bond.value = challenge_bond_min
        self.proposal_bond.value = proposal_bond_min
        self.challenge_bond_bps.value = challenge_bond_bps_val
        self.proposal_bond_bps.value = proposal_bond_bps_val
        self.challenge_bond_cap.value = challenge_bond_cap_val
        self.proposal_bond_cap.value = proposal_bond_cap_val
        self.proposer_fee_bps.value = proposer_fee_bps_val
        self.proposer_fee_floor_bps.value = proposer_fee_floor_bps_val
        self.grace_period_secs.value = grace_period_secs.as_uint64()
        self.market_admin.value = market_admin.bytes
        if cancellable.native:
            self.cancellable.value = UInt64(1)
        else:
            self.cancellable.value = UInt64(0)
        self.winning_outcome.value = UInt64(0)
        self.lp_fee_balance.value = UInt64(0)
        self.protocol_fee_balance.value = UInt64(0)
        self.total_outstanding_cost_basis.value = UInt64(0)
        self.dispute_sink_balance.value = UInt64(0)
        self.winner_share_bps.value = UInt64(DEFAULT_WINNER_SHARE_BPS)
        self.dispute_sink_share_bps.value = UInt64(DEFAULT_DISPUTE_SINK_SHARE_BPS)

        # Dispute metadata defaults
        self.challenge_reason_code.value = UInt64(0)
        self.challenge_evidence_hash.value = Bytes()
        self.dispute_ref_hash.value = Bytes()
        self.dispute_opened_at.value = UInt64(0)
        self.dispute_deadline.value = UInt64(0)
        self.ruling_hash.value = Bytes()
        self.resolution_path_used.value = UInt64(0)
        self.dispute_backend_kind.value = UInt64(0)
        self.pending_responder_role.value = UInt64(0)

        # Proposal bond defaults
        self.proposer.value = Bytes(ZERO_ADDRESS_BYTES)
        self.proposer_bond_held.value = UInt64(0)
        self.challenger_bond_held.value = UInt64(0)

        # Zero-init packed outcome quantities and total user shares
        self.quantities_packed.value = op.bzero(UInt64(64))
        self.total_shares_packed.value = op.bzero(UInt64(64))

    @arc4.abimethod()
    def initialize(self) -> None:
        """Opt the app into the currency ASA. Called by the factory after funding."""
        self._require_status(UInt64(STATUS_CREATED))
        sender = Txn.sender.bytes
        assert sender == self.creator.value or sender == Global.creator_address.bytes
        itxn.AssetTransfer(
            xfer_asset=Asset(self.currency_asa.value),
            asset_receiver=Global.current_application_address,
            asset_amount=0,
            fee=0,
        ).submit()

    @arc4.abimethod()
    def post_comment(self, message: arc4.String) -> None:
        raw = message.native.bytes
        self._require(raw.length > UInt64(0))
        self._require(raw.length <= UInt64(MAX_COMMENT_BYTES))
        self._require(self._sender_can_comment())
        arc4.emit("CommentPosted(string)", message)

    @arc4.abimethod()
    def bootstrap(
        self,
        deposit_amount: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._require_status(UInt64(STATUS_CREATED))
        # Accept calls from the market creator OR the app creator (factory)
        sender = Txn.sender.bytes
        assert sender == self.creator.value or sender == Global.creator_address.bytes
        deposit = deposit_amount.as_uint64()
        budget_required = self._max_proposer_fee()
        funding_required = deposit + budget_required
        self._require(deposit > UInt64(0))
        self._require_lmsr_bootstrap_floor(deposit)
        self._verify_payment(payment, funding_required, Txn.group_index - UInt64(1))

        self._require(self.blueprint_cid.value.length > UInt64(0))

        initial_lp_units = self.b.value
        weighted_entry_sum = self._entry_weighted_sum_checked(initial_lp_units, self._now())

        self.pool_balance.value = deposit
        self.bootstrap_deposit.value = deposit
        self.resolution_budget_balance.value = budget_required
        self.lp_shares_total.value = initial_lp_units
        self.activation_timestamp.value = self._now()
        self.total_lp_weighted_entry_sum.value = weighted_entry_sum
        self.total_residual_claimed.value = UInt64(0)
        self.settlement_timestamp.value = UInt64(0)
        self.bootstrapper_lp_shares.value = initial_lp_units
        self.bootstrapper_lp_entry.value = weighted_entry_sum
        self.status.value = UInt64(STATUS_ACTIVE)

        if payment.asset_amount > funding_required:
            self._send_currency(Txn.sender, payment.asset_amount - funding_required)

        arc4.emit("Bootstrap(uint64,uint64)", deposit_amount, arc4.UInt64(initial_lp_units))
        self._assert_invariants()

    @arc4.abimethod()
    def buy(
        self,
        outcome_index: arc4.UInt64,
        shares: arc4.UInt64,
        max_cost: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
        mbr_payment: gtxn.PaymentTransaction,
    ) -> None:
        self._active_before_deadline()
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require_share_granularity(shares_val)
        max_total = max_cost.as_uint64()
        self._require(max_total > UInt64(0))

        # Trader funds MBR for the us:/uc: pair on every buy. The us:/uc:
        # existence check that would let repeat buyers pay 0 costs ~8 bytes
        # we can't afford at the 4-page program cap, so repeat buys also pay
        # SHARE+COST. Excess accumulates as app-account slack rather than
        # being refunded — one-time ~0.05 ALGO fee per (trader, outcome,
        # buy-call) instead of per (trader, outcome) first-touch.
        # The payment must actually fund the app account before box creation.
        # Rekey/close checks are omitted as a size-cap compromise; nonzero
        # values only affect the trader's own payment account.
        self._require(mbr_payment.group_index == Txn.group_index - UInt64(1))
        assert mbr_payment.sender.bytes == Txn.sender.bytes
        assert mbr_payment.receiver == Global.current_application_address
        assert mbr_payment.amount == UInt64(SHARE_BOX_MBR + COST_BOX_MBR)

        q = self._get_q()
        cost = lmsr_cost_delta(q, self.b.value, outcome, shares_val)
        lp_fee = self._calc_fee_up(cost, self.lp_fee_bps.value)
        protocol_fee = self._calc_fee_up(cost, self.protocol_fee_bps.value)
        total_cost = cost + lp_fee + protocol_fee
        self._require(total_cost <= max_total)
        self._verify_payment(payment, total_cost, Txn.group_index - UInt64(2))

        # State updates first (P9)
        q[outcome] = q[outcome] + shares_val
        self._set_q(q)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) + shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) + cost)
        self._increment_total_user_shares(outcome, shares_val)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value + cost
        self.pool_balance.value = self.pool_balance.value + cost
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance.value = self.protocol_fee_balance.value + protocol_fee

        if payment.asset_amount > total_cost:
            self._send_currency(Txn.sender, payment.asset_amount - total_cost)

        arc4.emit("Buy(uint64)", outcome_index)
        self._assert_invariants()

    @arc4.abimethod()
    def sell(
        self,
        outcome_index: arc4.UInt64,
        shares: arc4.UInt64,
        min_return: arc4.UInt64,
    ) -> None:
        self._active_before_deadline()
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require_share_granularity(shares_val)
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)

        q = self._get_q()
        gross_return = lmsr_sell_return(q, self.b.value, outcome, shares_val)
        lp_fee = self._calc_fee_up(gross_return, self.lp_fee_bps.value)
        protocol_fee = self._calc_fee_up(gross_return, self.protocol_fee_bps.value)
        self._require(gross_return >= lp_fee + protocol_fee)
        net_return = gross_return - lp_fee - protocol_fee
        self._require(net_return >= min_return.as_uint64())

        # State updates first (P9)
        basis_reduction = self._basis_reduction(outcome, shares_val)
        q[outcome] = q[outcome] - shares_val
        self._set_q(q)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) - shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) - basis_reduction)
        self._decrement_total_user_shares(outcome, shares_val)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        self.pool_balance.value = self.pool_balance.value - gross_return
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance.value = self.protocol_fee_balance.value + protocol_fee

        # Send USDC to seller
        self._send_currency(Txn.sender, net_return)

        # Delete-on-zero skipped: would push the approval program past the
        # 4-page AVM cap (8192 bytes). Boxes are kept as zero-valued stubs
        # after full exit. Attack surface stays closed because pay-per-box
        # on buy means the attacker funds every new box themselves — the
        # market app never loses MBR headroom. Box-slot reuse is the only
        # feature lost, and per-trader-per-outcome first-touch MBR becomes
        # a one-time ~0.05 ALGO protocol fee rather than a refundable deposit.

        arc4.emit("Sell(uint64)", outcome_index)
        self._assert_invariants()

    @arc4.abimethod()
    def enter_lp_active(
        self,
        target_delta_b: arc4.UInt64,
        max_deposit: arc4.UInt64,
        expected_prices: arc4.DynamicArray[arc4.UInt64],
        price_tolerance: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._active_before_deadline()
        delta_b = target_delta_b.as_uint64()
        max_deposit_val = max_deposit.as_uint64()
        tolerance = price_tolerance.as_uint64()
        self._require(delta_b > UInt64(0))
        self._require(max_deposit_val > UInt64(0))
        self._require(expected_prices.length == self.num_outcomes.value)
        # LP fee accrual uses local state, so this path does not create boxes.

        current_prices = lmsr_prices(self._get_q(), self.b.value)
        max_price = UInt64(0)
        for idx in urange(current_prices.length):
            price = current_prices[idx]
            self._require(self._abs_diff(price, expected_prices[idx].as_uint64()) <= tolerance)
            if price > max_price:
                max_price = price
        self._require(max_price <= self.lp_entry_max_price_fp.value)

        deposit_required = lmsr_collateral_required_from_prices(delta_b, current_prices)
        self._require(deposit_required <= max_deposit_val)
        self._verify_payment(payment, deposit_required, Txn.group_index - UInt64(1))

        self._settle_lp_fees()

        next_b = self.b.value + delta_b
        floor_q = Array[UInt64]((self._get_total_user_shares(UInt64(0)),))
        for _off in urange(self.num_outcomes.value - UInt64(1)):
            floor_q.append(self._get_total_user_shares(_off + UInt64(1)))
        self._set_q(lmsr_q_from_prices_with_floor(current_prices, next_b, floor_q))
        self.b.value = next_b
        self.pool_balance.value = self.pool_balance.value + deposit_required
        self.lp_shares_total.value = self.lp_shares_total.value + delta_b
        self.total_lp_weighted_entry_sum.value = (
            self.total_lp_weighted_entry_sum.value + self._entry_weighted_sum_checked(delta_b, self._now())
        )
        self._set_lp_shares(self._get_lp_shares() + delta_b)
        self._set_lp_weighted_entry_sum(
            self._get_lp_weighted_entry_sum() + self._entry_weighted_sum_checked(delta_b, self._now())
        )
        self._set_fee_snapshot(self.cumulative_fee_per_share.value)

        if payment.asset_amount > deposit_required:
            self._send_currency(Txn.sender, payment.asset_amount - deposit_required)

        arc4.emit("EnterLpActive(uint64,uint64)", target_delta_b, arc4.UInt64(deposit_required))
        self._assert_invariants()

    @arc4.abimethod()
    def trigger_resolution(self) -> None:
        self._require_status(UInt64(STATUS_ACTIVE))
        self._require(self._now() >= self.deadline.value)
        self.status.value = UInt64(STATUS_RESOLUTION_PENDING)
        arc4.emit("TriggerResolution(uint64)", arc4.UInt64(self.status.value))
        self._assert_invariants()

    @arc4.abimethod()
    def propose_resolution(
        self,
        outcome_index: arc4.UInt64,
        evidence_hash: arc4.DynamicBytes,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._require_status(UInt64(STATUS_RESOLUTION_PENDING))
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)

        is_authority = Txn.sender.bytes == self.resolution_authority.value
        if not is_authority:
            # Open proposing: only after grace period.
            self._require(self._now() >= self.deadline.value + self.grace_period_secs.value)

        required_bond = UInt64(0)
        if not is_authority:
            required_bond = self._required_proposal_bond()
        self._verify_payment(payment, required_bond, Txn.group_index - UInt64(1))

        # State updates (P9)
        self.proposer.value = Txn.sender.bytes
        self.proposer_bond_held.value = payment.asset_amount
        self.proposed_outcome.value = outcome
        self.proposal_timestamp.value = self._now()
        self.proposal_evidence_hash.value = evidence_hash.bytes
        self.status.value = UInt64(STATUS_RESOLUTION_PROPOSED)
        arc4.emit("ProposeResolution(uint64,byte[])", outcome_index, evidence_hash)
        self._assert_invariants()

    @arc4.abimethod()
    def propose_early_resolution(
        self,
        outcome_index: arc4.UInt64,
        evidence_hash: arc4.DynamicBytes,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._active_before_deadline()
        self._require_authorized(self.resolution_authority.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._verify_payment(payment, UInt64(0), Txn.group_index - UInt64(1))

        self.proposer.value = Txn.sender.bytes
        self.proposer_bond_held.value = payment.asset_amount
        self.proposed_outcome.value = outcome
        self.proposal_timestamp.value = self._now()
        self.proposal_evidence_hash.value = evidence_hash.bytes
        self.status.value = UInt64(STATUS_RESOLUTION_PROPOSED)
        arc4.emit("ProposeEarlyResolution(uint64,byte[])", outcome_index, evidence_hash)
        self._assert_invariants()

    @arc4.abimethod()
    def challenge_resolution(
        self,
        payment: gtxn.AssetTransferTransaction,
        reason_code: arc4.UInt64,
        evidence_hash: arc4.DynamicBytes,
    ) -> None:
        self._require_status(UInt64(STATUS_RESOLUTION_PROPOSED))
        self._require(self._now() < self.proposal_timestamp.value + self.challenge_window_secs.value)
        self._verify_payment(payment, self._required_challenge_bond(), Txn.group_index - UInt64(1))

        # State updates first (P9). Bond retained by contract until dispute resolves.
        self.challenger.value = Txn.sender.bytes
        self.challenger_bond_held.value = payment.asset_amount
        self.challenge_reason_code.value = reason_code.as_uint64()
        self.challenge_evidence_hash.value = evidence_hash.bytes
        self.dispute_opened_at.value = self._now()
        self.status.value = UInt64(STATUS_DISPUTED)

        arc4.emit("ChallengeResolution(uint64,uint64,byte[])", arc4.UInt64(payment.asset_amount), reason_code, evidence_hash)
        self._assert_invariants()

    @arc4.abimethod()
    def register_dispute(
        self,
        dispute_ref_hash: arc4.DynamicBytes,
        backend_kind: arc4.UInt64,
        deadline: arc4.UInt64,
    ) -> None:
        """Register external dispute details. Resolution-authority-only, DISPUTED status only."""
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        self.dispute_ref_hash.value = dispute_ref_hash.bytes
        self.dispute_backend_kind.value = backend_kind.as_uint64()
        self.dispute_deadline.value = deadline.as_uint64()
        arc4.emit("RegisterDispute(byte[],uint64,uint64)", dispute_ref_hash, backend_kind, deadline)
        self._assert_invariants()

    @subroutine
    def _resolve_dispute_core(self, outcome: UInt64, ruling_hash_bytes: Bytes, resolution_path: UInt64) -> None:
        original_proposal = self.proposed_outcome.value
        self.ruling_hash.value = ruling_hash_bytes
        self.resolution_path_used.value = resolution_path
        self.pending_responder_role.value = UInt64(0)
        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = outcome
        self._record_settlement_timestamp_dispute()
        self._assert_invariants()
        self._settle_dispute_and_credit(outcome, original_proposal)

    @arc4.abimethod()
    def creator_resolve_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._resolve_dispute_core(outcome, ruling_hash.bytes, UInt64(1))
        arc4.emit("CreatorResolveDispute(uint64,byte[])", outcome_index, ruling_hash)

    @arc4.abimethod()
    def admin_resolve_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._resolve_dispute_core(outcome, ruling_hash.bytes, UInt64(2))
        arc4.emit("AdminResolveDispute(uint64,byte[])", outcome_index, ruling_hash)

    @arc4.abimethod()
    def finalize_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._resolve_dispute_core(outcome, ruling_hash.bytes, UInt64(1))
        arc4.emit("FinalizeDispute(uint64,byte[])", outcome_index, ruling_hash)

    @arc4.abimethod()
    def abort_early_resolution(self, ruling_hash: arc4.DynamicBytes) -> None:
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        self._require(self.proposal_timestamp.value > UInt64(0))
        self._require(self.proposal_timestamp.value < self.deadline.value)

        challenger_account = Account(self.challenger.value)
        challenger_payout = self._settle_overturned_dispute()

        self._clear_proposal_and_dispute_metadata()
        if self._now() < self.deadline.value:
            self.status.value = UInt64(STATUS_ACTIVE)
        else:
            self.status.value = UInt64(STATUS_RESOLUTION_PENDING)
        arc4.emit("AbortEarlyResolution(byte[],uint64)", ruling_hash, arc4.UInt64(self.status.value))
        self._assert_invariants()

        self._credit_pending_payout(challenger_account.bytes, challenger_payout)

    @arc4.abimethod()
    def cancel_dispute_and_market(self, ruling_hash: arc4.DynamicBytes) -> None:
        """Cancel a disputed market (irresolvable). Resolution-authority-only, DISPUTED status only."""
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)

        challenger_payout = self._settle_cancelled_dispute()

        self.ruling_hash.value = ruling_hash.bytes
        self.resolution_path_used.value = UInt64(1)
        self.pending_responder_role.value = UInt64(0)
        self.status.value = UInt64(STATUS_CANCELLED)
        self._record_settlement_timestamp_dispute()
        arc4.emit("CancelDisputeAndMarket(byte[])", ruling_hash)
        self._assert_invariants()
        self._credit_pending_payout(self.challenger.value, challenger_payout)

    @arc4.abimethod()
    def finalize_resolution(self) -> None:
        self._require_status(UInt64(STATUS_RESOLUTION_PROPOSED))
        self._require(self._now() >= self.proposal_timestamp.value + self.challenge_window_secs.value)
        self._require(self.challenger.value == Bytes(ZERO_ADDRESS_BYTES))

        # Return proposer bond (unchallenged proposal accepted)
        proposer_payout = self.proposer_bond_held.value + self._consume_proposer_fee()
        self.proposer_bond_held.value = UInt64(0)

        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = self.proposed_outcome.value
        self._record_settlement_timestamp_now()
        arc4.emit("FinalizeResolution(uint64)", arc4.UInt64(self.winning_outcome.value))
        self._assert_invariants()

        self._credit_pending_payout(self.proposer.value, proposer_payout)

    @arc4.abimethod()
    def claim(self, outcome_index: arc4.UInt64, shares: arc4.UInt64) -> None:
        self._require_status(UInt64(STATUS_RESOLVED))
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._require(outcome == self.winning_outcome.value)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require_share_granularity(shares_val)
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)
        self._require(self.pool_balance.value >= shares_val)
        q = self._get_q()
        payout = shares_val

        # State updates first (P9)
        basis_reduction = self._basis_reduction(outcome, shares_val)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) - shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) - basis_reduction)
        self._decrement_total_user_shares(outcome, shares_val)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        q[outcome] = q[outcome] - shares_val
        self._set_q(q)
        self.pool_balance.value = self.pool_balance.value - payout

        # Send payout to claimer
        self._send_currency(Txn.sender, payout)

        # Delete-on-zero omitted here: claim runs only on RESOLVED markets
        # where no new buys can happen, so recycling boxes has no consumer.
        # Stale zeroed us:/uc: boxes stay in storage until the market app is
        # destroyed. Omitting the call keeps the approval program under the
        # 4-page AVM size cap. Same for refund below.

        arc4.emit("Claim(uint64)", outcome_index)
        self._assert_solvency()

    @arc4.abimethod()
    def cancel(self) -> None:
        self._require_status(UInt64(STATUS_ACTIVE))
        self._require(self.cancellable.value == UInt64(1))
        self._require_authorized(self.creator.value)
        self.status.value = UInt64(STATUS_CANCELLED)
        self.settlement_timestamp.value = self.deadline.value
        arc4.emit("Cancel(uint64)", arc4.UInt64(self.status.value))
        self._assert_invariants()

    @arc4.abimethod()
    def withdraw_protocol_fees(self) -> None:
        """Withdraw accumulated protocol fees and dispute-sink balance to the configured protocol treasury."""
        amount = self.protocol_fee_balance.value + self.dispute_sink_balance.value
        self._require(amount > UInt64(0))
        self.protocol_fee_balance.value = UInt64(0)
        self.dispute_sink_balance.value = UInt64(0)
        self._send_currency(Account(self.protocol_treasury.value), amount)
        arc4.emit("WithdrawFees(uint64)", arc4.UInt64(amount))

    @arc4.abimethod()
    def refund(self, outcome_index: arc4.UInt64, shares: arc4.UInt64) -> None:
        self._require_status(UInt64(STATUS_CANCELLED))
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require_share_granularity(shares_val)
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)

        q = self._get_q()
        basis_reduction = self._basis_reduction(outcome, shares_val)

        # State updates first (P9)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) - shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) - basis_reduction)
        self._decrement_total_user_shares(outcome, shares_val)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        q[outcome] = q[outcome] - shares_val
        self._set_q(q)
        self.pool_balance.value = self.pool_balance.value - basis_reduction

        # Send refund to user
        self._send_currency(Txn.sender, basis_reduction)

        # Delete-on-zero omitted here (see claim); CANCELLED markets no
        # longer accept buys so recycling has no consumer.

        arc4.emit("Refund(uint64)", outcome_index)
        self._assert_solvency()

    @arc4.abimethod()
    def withdraw_pending_payouts(self) -> None:
        amount = self._get_pending_payout(Txn.sender.bytes)
        self._require(amount > UInt64(0))
        self._set_pending_payout(Txn.sender.bytes, UInt64(0))
        self._send_currency(Txn.sender, amount)
        arc4.emit("WithdrawPayouts(uint64)", arc4.UInt64(amount))

    @arc4.abimethod()
    def reclaim_resolution_budget(self) -> None:
        self._require_status_any2(UInt64(STATUS_RESOLVED), UInt64(STATUS_CANCELLED))
        self._require_authorized(self.creator.value)
        amount = self.resolution_budget_balance.value
        self._require(amount > UInt64(0))
        self.resolution_budget_balance.value = UInt64(0)
        self._send_currency(Txn.sender, amount)
        arc4.emit("ReclaimBudget(uint64)", arc4.UInt64(amount))


__all__ = [
    "BOX_KEY_PENDING_PAYOUTS",
    "BOX_KEY_USER_COST_BASIS",
    "BOX_KEY_USER_SHARES",
    "DEFAULT_LP_ENTRY_MAX_PRICE_FP",
    "MAX_OUTCOMES",
    "MIN_OUTCOMES",
    "PENDING_PAYOUT_BOX_MBR",
    "PRICE_TOLERANCE_BASE",
    "QuestionMarket",
    "SHARE_UNIT",
    "STATUS_ACTIVE",
    "STATUS_CANCELLED",
    "STATUS_CREATED",
    "STATUS_DISPUTED",
    "STATUS_RESOLUTION_PENDING",
    "STATUS_RESOLUTION_PROPOSED",
    "STATUS_RESOLVED",
]

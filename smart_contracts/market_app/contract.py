"""Per-market application contract for question.market.

Implements the full market lifecycle: creation, LMSR trading, liquidity provision,
resolution with challenge/dispute, claims, and refunds. All state lives in Algopy
GlobalState, LocalState, and BoxMap storage.
"""

from algopy import (
    Account,
    ARC4Contract,
    Array,
    Asset,
    BoxMap,
    BoxRef,
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
    lmsr_cost_delta,
    lmsr_liquidity_scale_b,
    lmsr_liquidity_scale_q,
    lmsr_prices,
    lmsr_sell_return,
)
from smart_contracts.protocol_config.contract import KEY_PROTOCOL_TREASURY

MIN_OUTCOMES = 2
MAX_OUTCOMES = 16
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
MAX_BLUEPRINT_SIZE = 8_192
MARKET_CONTRACT_VERSION = 3
MAX_COMMENT_BYTES = 512
ZERO_ADDRESS_BYTES = b"\x00" * 32
DEFAULT_WINNER_SHARE_BPS = 5_000
DEFAULT_DISPUTE_SINK_SHARE_BPS = 5_000
OUTCOME_ASA_TOTAL = 10_000_000_000_000

BOX_KEY_Q = b"q"
BOX_KEY_ASA = b"asa"
BOX_KEY_MAIN_BLUEPRINT = b"main_blueprint"
BOX_KEY_DISPUTE_BLUEPRINT = b"dispute_blueprint"
BOX_KEY_USER_SHARES = b"user_shares:"
BOX_KEY_USER_FEES = b"user_fees:"
BOX_KEY_USER_COST_BASIS = b"user_cost_basis:"
BOX_KEY_PENDING_PAYOUTS = b"pending_payouts:"
KEY_PROTOCOL_ADMIN = b"admin"

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
# main_blueprint_hash:
# dispute_blueprint_hash:
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
# protocol_config_id:
# factory_id:
# contract_version:
# lp_shares:
# fee_snapshot:
# ARC-28 events:
# arc4.emit("Bootstrap()")
# arc4.emit("Buy(uint64)")
# arc4.emit("Sell(uint64)")
# arc4.emit("ProvideLiquidity(uint64)")
# arc4.emit("WithdrawLiquidity(uint64)")
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
        pass

    @arc4.baremethod()
    def bare_noop(self) -> None:
        pass

    def __init__(self) -> None:
        self.creator = GlobalState(Bytes, key="creator")
        self.currency_asa = GlobalState(UInt64, key="currency_asa")
        self.num_outcomes = GlobalState(UInt64, key="num_outcomes")
        self.b = GlobalState(UInt64, key="b")
        self.pool_balance = GlobalState(UInt64, key="pool_balance")
        self.lp_shares_total = GlobalState(UInt64, key="lp_shares_total")
        self.lp_fee_bps = GlobalState(UInt64, key="lp_fee_bps")
        self.protocol_fee_bps = GlobalState(UInt64, key="protocol_fee_bps")
        self.cumulative_fee_per_share = GlobalState(UInt64, key="cumulative_fee_per_share")
        self.status = GlobalState(UInt64, key="status")
        self.deadline = GlobalState(UInt64, key="deadline")
        self.question_hash = GlobalState(Bytes, key="question_hash")
        self.main_blueprint_hash = GlobalState(Bytes, key="main_blueprint_hash")
        self.dispute_blueprint_hash = GlobalState(Bytes, key="dispute_blueprint_hash")
        self.proposed_outcome = GlobalState(UInt64, key="proposed_outcome")
        self.proposal_timestamp = GlobalState(UInt64, key="pts")
        self.proposal_evidence_hash = GlobalState(Bytes, key="peh")
        self.challenge_window_secs = GlobalState(UInt64, key="challenge_window_secs")
        self.challenger = GlobalState(Bytes, key="challenger")
        self.protocol_config_id = GlobalState(UInt64, key="protocol_config_id")
        self.factory_id = GlobalState(UInt64, key="factory_id")
        self.contract_version = GlobalState(UInt64, key="contract_version")

        self.resolution_authority = GlobalState(Bytes, key="resolution_authority")
        self.challenge_bond = GlobalState(UInt64, key="challenge_bond")
        self.market_admin = GlobalState(Bytes, key="market_admin")
        self.cancellable = GlobalState(UInt64, key="cancellable")
        self.winning_outcome = GlobalState(UInt64, key="winning_outcome")
        self.lp_fee_balance = GlobalState(UInt64, key="lp_fee_balance")
        self.protocol_fee_balance = GlobalState(UInt64, key="protocol_fee_balance")
        self.total_outstanding_cost_basis = GlobalState(UInt64, key="total_outstanding_cost_basis")
        self.dispute_sink_balance = GlobalState(UInt64, key="dispute_sink_balance")
        self.winner_share_bps = GlobalState(UInt64, key="winner_share_bps")
        self.dispute_sink_share_bps = GlobalState(UInt64, key="dispute_sink_share_bps")

        # Dispute metadata
        self.challenge_reason_code = GlobalState(UInt64, key="crc")
        self.challenge_evidence_hash = GlobalState(Bytes, key="ceh")
        self.dispute_ref_hash = GlobalState(Bytes, key="drh")
        self.dispute_opened_at = GlobalState(UInt64, key="doa")
        self.dispute_deadline = GlobalState(UInt64, key="ddl")
        self.ruling_hash = GlobalState(Bytes, key="ruling_hash")
        self.resolution_path_used = GlobalState(UInt64, key="rpu")
        self.dispute_backend_kind = GlobalState(UInt64, key="dbk")
        self.pending_responder_role = GlobalState(UInt64, key="prr")

        self.proposer = GlobalState(Bytes, key="proposer")
        self.proposer_bond_held = GlobalState(UInt64, key="proposer_bond_held")
        self.challenger_bond_held = GlobalState(UInt64, key="challenger_bond_held")
        self.proposal_bond = GlobalState(UInt64, key="proposal_bond")
        self.grace_period_secs = GlobalState(UInt64, key="grace_period_secs")

        self.lp_shares = LocalState(UInt64, key="lp_shares")
        self.fee_snapshot = LocalState(UInt64, key="fee_snapshot")

        self.outcome_quantities_box = BoxMap(UInt64, UInt64, key_prefix=BOX_KEY_Q)
        self.outcome_asa_ids_box = BoxMap(UInt64, UInt64, key_prefix=BOX_KEY_ASA)
        self.user_outcome_shares_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_USER_SHARES)
        self.user_claimable_fees_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_USER_FEES)
        self.user_cost_basis_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_USER_COST_BASIS)
        self.pending_payouts_box = BoxMap(Bytes, UInt64, key_prefix=BOX_KEY_PENDING_PAYOUTS)
        self.main_blueprint_box = BoxRef(key=BOX_KEY_MAIN_BLUEPRINT)
        self.dispute_blueprint_box = BoxRef(key=BOX_KEY_DISPUTE_BLUEPRINT)

    def _require(self, condition: bool) -> None:
        assert condition

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

    def _get_claimable_fees(self) -> UInt64:
        return self.user_claimable_fees_box.get(self._sender_fee_key(), default=UInt64(0))

    def _set_claimable_fees(self, value: UInt64) -> None:
        self.user_claimable_fees_box[self._sender_fee_key()] = value

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

    def _protocol_admin(self) -> Bytes:
        value, exists = op.AppGlobal.get_ex_bytes(self.protocol_config_id.value, KEY_PROTOCOL_ADMIN)
        self._require(exists)
        return value

    def _protocol_treasury(self) -> Bytes:
        value, exists = op.AppGlobal.get_ex_bytes(self.protocol_config_id.value, KEY_PROTOCOL_TREASURY)
        self._require(exists)
        return value

    @subroutine
    def _basis_reduction(self, outcome_index: UInt64, shares: UInt64) -> UInt64:
        current_shares = self._get_user_outcome_shares(outcome_index)
        current_basis = self._get_user_cost_basis(outcome_index)
        self._require(current_shares >= shares)
        if current_shares == shares:
            return current_basis
        return self._mul_div_floor(current_basis, shares, current_shares)

    @subroutine
    def _get_q(self) -> Array[UInt64]:
        self._require(self.num_outcomes.value >= UInt64(1))
        values = Array[UInt64]((self.outcome_quantities_box.get(UInt64(0), default=UInt64(0)),))
        for offset in urange(self.num_outcomes.value - UInt64(1)):
            idx = offset + UInt64(1)
            values.append(self.outcome_quantities_box.get(idx, default=UInt64(0)))
        return values

    @subroutine
    def _set_q(self, values: Array[UInt64]) -> None:
        for idx in urange(values.length):
            self.outcome_quantities_box[idx] = values[idx]

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
    def _mul_div_floor(self, a: UInt64, b: UInt64, denominator: UInt64) -> UInt64:
        self._require(denominator > UInt64(0))
        product_high, product_low = op.mulw(a, b)
        return op.divw(product_high, product_low, denominator)

    @subroutine
    def _mul_div_ceil(self, a: UInt64, b: UInt64, denominator: UInt64) -> UInt64:
        self._require(denominator > UInt64(0))
        product_high, product_low = op.mulw(a, b)
        quotient_high, quotient_low, remainder_high, remainder_low = op.divmodw(
            product_high,
            product_low,
            UInt64(0),
            denominator,
        )
        self._require(quotient_high == UInt64(0))
        if remainder_high == UInt64(0) and remainder_low == UInt64(0):
            return quotient_low
        return quotient_low + UInt64(1)

    @subroutine
    def _calc_fee_up(self, amount: UInt64, bps: UInt64) -> UInt64:
        return self._mul_div_ceil(amount, bps, UInt64(BPS_DENOMINATOR))

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

    @subroutine
    def _store_blueprint(self, raw: Bytes, is_dispute: UInt64) -> None:
        self._require(raw.length > UInt64(0))
        self._require(raw.length <= UInt64(MAX_BLUEPRINT_SIZE))
        digest = op.sha256(raw)
        if is_dispute == UInt64(1):
            self.dispute_blueprint_box.create(size=raw.length)
            self.dispute_blueprint_box.replace(0, raw)
            self.dispute_blueprint_hash.value = digest
        else:
            self.main_blueprint_box.create(size=raw.length)
            self.main_blueprint_box.replace(0, raw)
            self.main_blueprint_hash.value = digest

    def _require_status(self, expected: UInt64) -> None:
        self._require(self.status.value == expected)

    def _require_status_any2(self, expected_a: UInt64, expected_b: UInt64) -> None:
        self._require(self.status.value == expected_a or self.status.value == expected_b)

    def _require_status_any3(self, expected_a: UInt64, expected_b: UInt64, expected_c: UInt64) -> None:
        self._require(
            self.status.value == expected_a or self.status.value == expected_b or self.status.value == expected_c
        )

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
            accrued = self._mul_div_floor(delta, shares, UInt64(SCALE))
            self._set_claimable_fees(self._get_claimable_fees() + accrued)
        self._set_fee_snapshot(cumulative)

    @subroutine
    def _distribute_lp_fee(self, fee_amount: UInt64) -> None:
        self.lp_fee_balance.value = self.lp_fee_balance.value + fee_amount
        if self.lp_shares_total.value > UInt64(0) and fee_amount > UInt64(0):
            increment = self._mul_div_floor(fee_amount, UInt64(SCALE), self.lp_shares_total.value)
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
            q = self._get_q()
            winning_payout = q[self.winning_outcome.value]
            self._require(self.pool_balance.value >= winning_payout)

    @subroutine
    def _assert_refund_reserve(self) -> None:
        if self.status.value == UInt64(STATUS_CANCELLED) or self.status.value == UInt64(STATUS_DISPUTED):
            self._require(self.pool_balance.value >= self.total_outstanding_cost_basis.value)

    @subroutine
    def _assert_invariants(self) -> None:
        if (
            self.status.value == UInt64(STATUS_ACTIVE)
            or self.status.value == UInt64(STATUS_RESOLUTION_PENDING)
            or self.status.value == UInt64(STATUS_RESOLUTION_PROPOSED)
            or self.status.value == UInt64(STATUS_CANCELLED)
            or self.status.value == UInt64(STATUS_RESOLVED)
            or self.status.value == UInt64(STATUS_DISPUTED)
        ):
            self._require(self.winner_share_bps.value + self.dispute_sink_share_bps.value <= UInt64(BPS_DENOMINATOR))
            self._assert_solvency()
            self._assert_refund_reserve()
            if self.status.value != UInt64(STATUS_RESOLVED):
                self._assert_price_sum()

    def _active_before_deadline(self) -> None:
        self._require_status(UInt64(STATUS_ACTIVE))
        self._require(self._now() < self.deadline.value)

    def _now(self) -> UInt64:
        # The identity op ensures UInt64 type for both AVM and testing framework
        return Global.latest_timestamp + UInt64(0)

    def _verify_payment(self, payment: gtxn.AssetTransferTransaction, min_amount: UInt64) -> None:
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

    @subroutine
    def _winner_bonus_from_bond(self, losing_bond: UInt64) -> UInt64:
        return self._mul_div_floor(losing_bond, self.winner_share_bps.value, UInt64(BPS_DENOMINATOR))

    @subroutine
    def _settle_confirmed_dispute(self) -> UInt64:
        losing_bond = self.challenger_bond_held.value
        winner_bonus = self._winner_bonus_from_bond(losing_bond)
        self.dispute_sink_balance.value = self.dispute_sink_balance.value + (losing_bond - winner_bonus)
        proposer_payout = self.proposer_bond_held.value + winner_bonus
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
        protocol_fee_bps: arc4.UInt64,
        deadline: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        main_blueprint_hash: arc4.DynamicBytes,
        dispute_blueprint_hash: arc4.DynamicBytes,
        challenge_window_secs: arc4.UInt64,
        resolution_authority: arc4.Address,
        challenge_bond: arc4.UInt64,
        proposal_bond: arc4.UInt64,
        grace_period_secs: arc4.UInt64,
        market_admin: arc4.Address,
        protocol_config_id: arc4.UInt64,
        factory_id: arc4.UInt64,
        cancellable: arc4.Bool,
    ) -> None:
        outcome_count = num_outcomes.as_uint64()
        self._require(outcome_count >= UInt64(MIN_OUTCOMES))
        self._require(outcome_count <= UInt64(MAX_OUTCOMES))
        self._require(initial_b.as_uint64() > UInt64(0))
        self._require(challenge_window_secs.as_uint64() > UInt64(0))
        self._require(currency_asa.as_uint64() > UInt64(0))
        self._require(creator.bytes != Bytes(ZERO_ADDRESS_BYTES))
        self._require(resolution_authority.bytes != Bytes(ZERO_ADDRESS_BYTES))
        self._require(market_admin.bytes != Bytes(ZERO_ADDRESS_BYTES))

        self.creator.value = creator.bytes
        self.currency_asa.value = currency_asa.as_uint64()
        self.num_outcomes.value = outcome_count
        self.b.value = initial_b.as_uint64()
        self.pool_balance.value = UInt64(0)
        self.lp_shares_total.value = UInt64(0)
        self.lp_fee_bps.value = lp_fee_bps.as_uint64()
        self.protocol_fee_bps.value = protocol_fee_bps.as_uint64()
        self.cumulative_fee_per_share.value = UInt64(0)
        self.status.value = UInt64(STATUS_CREATED)
        self._require(deadline.as_uint64() > self._now())
        self.deadline.value = deadline.as_uint64()
        self.question_hash.value = question_hash.bytes
        self.main_blueprint_hash.value = main_blueprint_hash.bytes
        self.dispute_blueprint_hash.value = dispute_blueprint_hash.bytes
        self.proposed_outcome.value = UInt64(0)
        self.proposal_timestamp.value = UInt64(0)
        self.proposal_evidence_hash.value = Bytes()
        self.challenge_window_secs.value = challenge_window_secs.as_uint64()
        self.challenger.value = Bytes(ZERO_ADDRESS_BYTES)
        self.protocol_config_id.value = protocol_config_id.as_uint64()
        self.factory_id.value = factory_id.as_uint64()
        self.contract_version.value = UInt64(MARKET_CONTRACT_VERSION)
        self.resolution_authority.value = resolution_authority.bytes
        self.challenge_bond.value = challenge_bond.as_uint64()
        self.proposal_bond.value = proposal_bond.as_uint64()
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

        # Box initialization deferred until initialize/bootstrap — app needs MBR funding first

    @arc4.abimethod()
    def post_comment(self, message: arc4.String) -> None:
        raw = message.native.bytes
        self._require(raw.length > UInt64(0))
        self._require(raw.length <= UInt64(MAX_COMMENT_BYTES))
        self._require(self._sender_can_comment())
        arc4.emit("CommentPosted(string)", message)

    @arc4.abimethod()
    def opt_in_to_asa(self, asset: Asset) -> None:
        """Opt contract into an ASA. Called by creator before bootstrap for currency_asa
        and each outcome ASA. SDK calls this N+1 times."""
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)
        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
            fee=0,
        ).submit()

    @arc4.abimethod()
    def register_outcome_asa(self, outcome_index: arc4.UInt64, asset: Asset) -> None:
        """Register an outcome ASA ID in box storage. Called by creator before bootstrap."""
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self.outcome_asa_ids_box[outcome] = asset.id

    @arc4.abimethod()
    def store_main_blueprint(self, data: arc4.DynamicBytes) -> None:
        """Store main resolution blueprint on-chain. Creator-only, CREATED status only.
        Must be called before bootstrap. Size capped by MAX_BLUEPRINT_SIZE."""
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)
        self._store_blueprint(data.native, UInt64(0))

    @arc4.abimethod()
    def store_dispute_blueprint(self, data: arc4.DynamicBytes) -> None:
        """Store dispute resolution blueprint on-chain. Creator-only, CREATED status only.
        Must be called before bootstrap. Size capped by MAX_BLUEPRINT_SIZE."""
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)
        self._store_blueprint(data.native, UInt64(1))

    @arc4.abimethod()
    def initialize(
        self,
    ) -> None:
        """Prepare a CREATED market for bootstrap in a single call.

        This opts the app into the currency ASA and creates outcome ASAs
        internally. Existing blueprint storage and bootstrap methods can then be
        grouped after it to activate the market atomically.
        """
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)

        itxn.AssetTransfer(
            xfer_asset=Asset(self.currency_asa.value),
            asset_receiver=Global.current_application_address,
            asset_amount=0,
            fee=0,
        ).submit()

        for idx in urange(self.num_outcomes.value):
            created_asset = itxn.AssetConfig(
                total=UInt64(OUTCOME_ASA_TOTAL),
                decimals=UInt64(6),
                default_frozen=False,
                unit_name=Bytes(b"QM"),
                asset_name=Bytes(b"Question Outcome"),
                manager=Global.current_application_address,
                reserve=Global.current_application_address,
                fee=0,
            ).submit().created_asset
            self.outcome_asa_ids_box[idx] = created_asset.id

        self._assert_invariants()

    @arc4.abimethod()
    def bootstrap(
        self,
        deposit_amount: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._require_status(UInt64(STATUS_CREATED))
        self._require_authorized(self.creator.value)
        deposit = deposit_amount.as_uint64()
        self._require(deposit > UInt64(0))
        self._require_lmsr_bootstrap_floor(deposit)
        self._verify_payment(payment, deposit)

        # Verify both blueprints were stored
        self._require(bool(self.main_blueprint_box))
        self._require(bool(self.dispute_blueprint_box))

        # Initialize outcome quantity boxes
        for idx in urange(self.num_outcomes.value):
            self.outcome_quantities_box[idx] = UInt64(0)
            # Verify outcome ASA was registered
            self._require(self.outcome_asa_ids_box.get(idx, default=UInt64(0)) > UInt64(0))

        self.pool_balance.value = deposit
        self.lp_shares_total.value = deposit
        self._set_lp_shares(deposit)
        self._set_fee_snapshot(self.cumulative_fee_per_share.value)
        self._set_claimable_fees(UInt64(0))
        self.status.value = UInt64(STATUS_ACTIVE)

        arc4.emit("Bootstrap(uint64)", deposit_amount)
        self._assert_invariants()

    @arc4.abimethod()
    def buy(
        self,
        outcome_index: arc4.UInt64,
        shares: arc4.UInt64,
        max_cost: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._active_before_deadline()
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        max_total = max_cost.as_uint64()
        self._require(max_total > UInt64(0))

        q = self._get_q()
        cost = lmsr_cost_delta(q, self.b.value, outcome, shares_val)
        lp_fee = self._calc_fee_up(cost, self.lp_fee_bps.value)
        protocol_fee = self._calc_fee_up(cost, self.protocol_fee_bps.value)
        total_cost = cost + lp_fee + protocol_fee
        self._require(total_cost <= max_total)
        self._verify_payment(payment, total_cost)

        # State updates first (P9)
        q[outcome] = q[outcome] + shares_val
        self._set_q(q)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) + shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) + cost)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value + cost
        self.pool_balance.value = self.pool_balance.value + cost
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance.value = self.protocol_fee_balance.value + protocol_fee

        # Transfer outcome ASA to buyer
        outcome_asa_id = self.outcome_asa_ids_box[outcome]
        itxn.AssetTransfer(
            xfer_asset=Asset(outcome_asa_id),
            asset_receiver=Txn.sender,
            asset_amount=shares_val,
            fee=0,
        ).submit()
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
        asa_payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._active_before_deadline()
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)

        # Verify seller sent outcome ASA
        outcome_asa_id = self.outcome_asa_ids_box[outcome]
        self._require(asa_payment.sender.bytes == Txn.sender.bytes)
        self._require(asa_payment.asset_receiver == Global.current_application_address)
        self._require(asa_payment.xfer_asset.id == outcome_asa_id)
        self._require(asa_payment.asset_amount == shares_val)
        self._require(asa_payment.asset_sender.bytes == Global.zero_address.bytes)
        self._require(asa_payment.rekey_to.bytes == Global.zero_address.bytes)
        self._require(asa_payment.asset_close_to.bytes == Global.zero_address.bytes)

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
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        self.pool_balance.value = self.pool_balance.value - gross_return
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance.value = self.protocol_fee_balance.value + protocol_fee

        # Send USDC to seller
        self._send_currency(Txn.sender, net_return)

        arc4.emit("Sell(uint64)", outcome_index)
        self._assert_invariants()

    @arc4.abimethod()
    def provide_liq(
        self,
        deposit_amount: arc4.UInt64,
        payment: gtxn.AssetTransferTransaction,
    ) -> None:
        self._active_before_deadline()
        deposit = deposit_amount.as_uint64()
        self._require(deposit > UInt64(0))
        self._require(self.pool_balance.value > UInt64(0))
        self._verify_payment(payment, deposit)

        self._settle_lp_fees()
        q = self._get_q()
        old_prices = lmsr_prices(q, self.b.value)
        scaled_q = lmsr_liquidity_scale_q(q, self.b.value, deposit, self.pool_balance.value)
        scaled_b = lmsr_liquidity_scale_b(q, self.b.value, deposit, self.pool_balance.value)
        shares_minted = self._mul_div_floor(self.lp_shares_total.value, deposit, self.pool_balance.value)
        self._require(shares_minted > UInt64(0))

        self._set_q(scaled_q)
        self.b.value = scaled_b
        self.pool_balance.value = self.pool_balance.value + deposit
        self.lp_shares_total.value = self.lp_shares_total.value + shares_minted
        self._set_lp_shares(self._get_lp_shares() + shares_minted)
        self._set_fee_snapshot(self.cumulative_fee_per_share.value)

        q_after = self._get_q()
        new_prices = lmsr_prices(q_after, self.b.value)
        for idx in urange(old_prices.length):
            self._require(self._abs_diff(old_prices[idx], new_prices[idx]) <= UInt64(PRICE_TOLERANCE_BASE))

        arc4.emit("ProvideLiquidity(uint64)", deposit_amount)
        self._assert_invariants()

    @arc4.abimethod()
    def withdraw_liq(self, shares_to_burn: arc4.UInt64) -> None:
        self._require_status_any2(UInt64(STATUS_ACTIVE), UInt64(STATUS_CANCELLED))
        burn = shares_to_burn.as_uint64()
        self._require(burn > UInt64(0))

        current_status = self.status.value
        user_shares = self._get_lp_shares()
        total_shares = self.lp_shares_total.value
        self._require(user_shares >= burn)
        if current_status == UInt64(STATUS_ACTIVE):
            self._require(burn < total_shares)

        self._settle_lp_fees()
        old_prices = Array[UInt64]((UInt64(0),))
        if current_status == UInt64(STATUS_ACTIVE):
            q = self._get_q()
            old_prices = lmsr_prices(q, self.b.value)
        else:
            self._require(self.pool_balance.value >= self.total_outstanding_cost_basis.value)

        claimable_fees = self._get_claimable_fees()
        fee_return = self._mul_div_floor(claimable_fees, burn, user_shares)
        remaining_total = total_shares - burn
        usdc_return = UInt64(0)

        if current_status == UInt64(STATUS_ACTIVE):
            q = self._get_q()
            usdc_return = self._mul_div_floor(self.pool_balance.value, burn, total_shares)
            if remaining_total == UInt64(0):
                for idx in urange(q.length):
                    q[idx] = UInt64(0)
                self.b.value = UInt64(0)
            else:
                for idx in urange(q.length):
                    q[idx] = self._mul_div_floor(q[idx], remaining_total, total_shares)
                self.b.value = self._mul_div_floor(self.b.value, remaining_total, total_shares)
            self._set_q(q)
        else:
            residual_pool = self.pool_balance.value - self.total_outstanding_cost_basis.value
            usdc_return = self._mul_div_floor(residual_pool, burn, total_shares)

        self.pool_balance.value = self.pool_balance.value - usdc_return
        self.lp_fee_balance.value = self.lp_fee_balance.value - fee_return
        self.lp_shares_total.value = remaining_total
        self._set_lp_shares(user_shares - burn)
        self._set_claimable_fees(claimable_fees - fee_return)
        self._set_fee_snapshot(self.cumulative_fee_per_share.value)

        if current_status == UInt64(STATUS_ACTIVE) and self.b.value > UInt64(0):
            new_prices = lmsr_prices(self._get_q(), self.b.value)
            for idx in urange(old_prices.length):
                self._require(self._abs_diff(old_prices[idx], new_prices[idx]) <= UInt64(PRICE_TOLERANCE_BASE))
        self._assert_invariants()

        # Send USDC + fees to LP
        total_payout = usdc_return + fee_return
        if total_payout > UInt64(0):
            self._send_currency(Txn.sender, total_payout)

        arc4.emit("WithdrawLiquidity(uint64)", shares_to_burn)

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
        grace_expired = self._now() >= self.deadline.value + self.grace_period_secs.value

        if not is_authority:
            # Open proposing: only after grace period.
            self._require(grace_expired)

        self._verify_payment(payment, self.proposal_bond.value)

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
        self._verify_payment(payment, self.proposal_bond.value)

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
        self._verify_payment(payment, self.challenge_bond.value)

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

    @arc4.abimethod()
    def creator_resolve_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        """Creator adjudicates the dispute. Creator-only, DISPUTED status only."""
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.creator.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)

        original_proposal = self.proposed_outcome.value

        self.ruling_hash.value = ruling_hash.bytes
        self.resolution_path_used.value = UInt64(1)  # dispute
        self.pending_responder_role.value = UInt64(0)
        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = outcome
        arc4.emit("CreatorResolveDispute(uint64,byte[])", outcome_index, ruling_hash)
        self._assert_invariants()
        self._settle_dispute_and_credit(outcome, original_proposal)

    @arc4.abimethod()
    def admin_resolve_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        """Market admin adjudicates the dispute as final fallback. Admin-only, DISPUTED status only."""
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.market_admin.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)

        original_proposal = self.proposed_outcome.value

        self.ruling_hash.value = ruling_hash.bytes
        self.resolution_path_used.value = UInt64(2)  # admin_fallback
        self.pending_responder_role.value = UInt64(0)
        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = outcome
        arc4.emit("AdminResolveDispute(uint64,byte[])", outcome_index, ruling_hash)
        self._assert_invariants()
        self._settle_dispute_and_credit(outcome, original_proposal)

    @arc4.abimethod()
    def finalize_dispute(
        self,
        outcome_index: arc4.UInt64,
        ruling_hash: arc4.DynamicBytes,
    ) -> None:
        """Finalize a dispute with a ruling. Resolution-authority-only, DISPUTED status only."""
        self._require_status(UInt64(STATUS_DISPUTED))
        self._require_authorized(self.resolution_authority.value)
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)

        original_proposal = self.proposed_outcome.value

        self.ruling_hash.value = ruling_hash.bytes
        self.resolution_path_used.value = UInt64(1)  # dispute
        self.pending_responder_role.value = UInt64(0)
        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = outcome
        arc4.emit("FinalizeDispute(uint64,byte[])", outcome_index, ruling_hash)
        self._assert_invariants()
        self._settle_dispute_and_credit(outcome, original_proposal)

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
        arc4.emit("CancelDisputeAndMarket(byte[])", ruling_hash)
        self._assert_invariants()
        self._credit_pending_payout(self.challenger.value, challenger_payout)

    @arc4.abimethod()
    def finalize_resolution(self) -> None:
        self._require_status(UInt64(STATUS_RESOLUTION_PROPOSED))
        self._require(self._now() >= self.proposal_timestamp.value + self.challenge_window_secs.value)
        self._require(self.challenger.value == Bytes(ZERO_ADDRESS_BYTES))

        # Return proposer bond (unchallenged proposal accepted)
        proposer_bond = self.proposer_bond_held.value
        self.proposer_bond_held.value = UInt64(0)

        self.status.value = UInt64(STATUS_RESOLVED)
        self.winning_outcome.value = self.proposed_outcome.value
        arc4.emit("FinalizeResolution(uint64)", arc4.UInt64(self.winning_outcome.value))
        self._assert_invariants()

        self._credit_pending_payout(self.proposer.value, proposer_bond)

    @arc4.abimethod()
    def claim(self, outcome_index: arc4.UInt64, shares: arc4.UInt64) -> None:
        self._require_status(UInt64(STATUS_RESOLVED))
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        self._require(outcome == self.winning_outcome.value)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)

        q = self._get_q()
        outstanding = q[outcome]
        self._require(outstanding > UInt64(0))
        payout = self._mul_div_floor(self.pool_balance.value, shares_val, outstanding)

        # State updates first (P9)
        basis_reduction = self._basis_reduction(outcome, shares_val)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) - shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) - basis_reduction)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        q[outcome] = q[outcome] - shares_val
        self._set_q(q)
        self.pool_balance.value = self.pool_balance.value - payout

        # Send payout to claimer
        self._send_currency(Txn.sender, payout)

        arc4.emit("Claim(uint64)", outcome_index)
        self._assert_solvency()

    @arc4.abimethod()
    def cancel(self) -> None:
        self._require_status(UInt64(STATUS_ACTIVE))
        self._require(self.cancellable.value == UInt64(1))
        self._require_authorized(self.creator.value)
        self.status.value = UInt64(STATUS_CANCELLED)
        arc4.emit("Cancel(uint64)", arc4.UInt64(self.status.value))
        self._assert_invariants()

    @arc4.abimethod()
    def withdraw_protocol_fees(self) -> None:
        """Withdraw accumulated protocol fees to the governed protocol treasury. Admin-only."""
        self._require_authorized(self._protocol_admin())
        amount = self.protocol_fee_balance.value
        self._require(amount > UInt64(0))
        treasury = self._protocol_treasury()
        self.protocol_fee_balance.value = UInt64(0)
        self._send_currency(Account(treasury), amount)
        arc4.emit("WithdrawProtocolFees(uint64)", arc4.UInt64(amount))

    @arc4.abimethod()
    def refund(self, outcome_index: arc4.UInt64, shares: arc4.UInt64) -> None:
        self._require_status(UInt64(STATUS_CANCELLED))
        outcome = outcome_index.as_uint64()
        self._assert_valid_outcome(outcome)
        shares_val = shares.as_uint64()
        self._require(shares_val > UInt64(0))
        self._require(self._get_user_outcome_shares(outcome) >= shares_val)

        q = self._get_q()
        basis_reduction = self._basis_reduction(outcome, shares_val)

        # State updates first (P9)
        self._set_user_outcome_shares(outcome, self._get_user_outcome_shares(outcome) - shares_val)
        self._set_user_cost_basis(outcome, self._get_user_cost_basis(outcome) - basis_reduction)
        self.total_outstanding_cost_basis.value = self.total_outstanding_cost_basis.value - basis_reduction
        q[outcome] = q[outcome] - shares_val
        self._set_q(q)
        self.pool_balance.value = self.pool_balance.value - basis_reduction

        # Send refund to user
        self._send_currency(Txn.sender, basis_reduction)

        arc4.emit("Refund(uint64)", outcome_index)
        self._assert_solvency()

    @arc4.abimethod()
    def withdraw_pending_payouts(self) -> None:
        amount = self._get_pending_payout(Txn.sender.bytes)
        self._require(amount > UInt64(0))
        self._set_pending_payout(Txn.sender.bytes, UInt64(0))
        self._send_currency(Txn.sender, amount)
        arc4.emit("WithdrawPendingPayouts(uint64)", arc4.UInt64(amount))


__all__ = [
    "BOX_KEY_ASA",
    "BOX_KEY_Q",
    "BOX_KEY_DISPUTE_BLUEPRINT",
    "BOX_KEY_MAIN_BLUEPRINT",
    "BOX_KEY_PENDING_PAYOUTS",
    "MAX_BLUEPRINT_SIZE",
    "BOX_KEY_USER_COST_BASIS",
    "BOX_KEY_USER_FEES",
    "BOX_KEY_USER_SHARES",
    "MAX_OUTCOMES",
    "MIN_OUTCOMES",
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

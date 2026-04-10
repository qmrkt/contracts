from __future__ import annotations

"""Versioned Python model for the active-LP market line (`v4`).

This model keeps the legacy `MarketAppModel` intact while providing a protocol-facing
reference for the new active-LP semantics:

- LP entry during ACTIVE
- no ACTIVE LP principal withdrawal
- strictly prospective LP fees
- reserve-based residual release
- affine time-weighted residual distribution
"""

from dataclasses import dataclass, field

from smart_contracts.lmsr_math import (
    SCALE,
    lmsr_collateral_required_from_prices,
    lmsr_cost_delta,
    lmsr_normalized_q_from_prices,
    lmsr_prices,
    lmsr_sell_return,
)
from smart_contracts.market_app.model import (
    MAX_COMMENT_BYTES,
    MARKET_CONTRACT_VERSION,
    PRICE_TOLERANCE_BASE,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    ZERO_ADDRESS,
    MarketAppError,
    MarketAppModel,
)

ACTIVE_LP_MARKET_CONTRACT_VERSION = MARKET_CONTRACT_VERSION + 1
DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP = 150_000
DEFAULT_LP_ENTRY_MAX_PRICE_FP = 800_000


@dataclass
class ActiveLpMarketAppModel(MarketAppModel):
    residual_linear_lambda_fp: int = DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP
    lp_entry_max_price_fp: int = DEFAULT_LP_ENTRY_MAX_PRICE_FP
    contract_version: int = field(default=ACTIVE_LP_MARKET_CONTRACT_VERSION, init=False)
    activation_timestamp: int = field(default=0, init=False)
    settlement_timestamp: int = field(default=0, init=False)
    total_lp_weighted_entry_sum: int = field(default=0, init=False)
    total_residual_claimed: int = field(default=0, init=False)
    user_withdrawable_fee_surplus: dict[str, int] = field(default_factory=dict, init=False)
    user_lp_weighted_entry_sum: dict[str, int] = field(default_factory=dict, init=False)
    user_residual_claimed: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0 <= self.residual_linear_lambda_fp <= SCALE):
            raise MarketAppError("residual_linear_lambda_fp must be between 0 and SCALE")
        if not (0 < self.lp_entry_max_price_fp <= SCALE):
            raise MarketAppError("lp_entry_max_price_fp must be between 1 and SCALE")
        self.contract_version = ACTIVE_LP_MARKET_CONTRACT_VERSION

    def _ensure_user(self, sender: str) -> None:
        super()._ensure_user(sender)
        self.user_withdrawable_fee_surplus.setdefault(sender, 0)
        self.user_lp_weighted_entry_sum.setdefault(sender, 0)
        self.user_residual_claimed.setdefault(sender, 0)

    def _max_price_diff(self, left: list[int], right: list[int]) -> int:
        self._require(len(left) == len(right), "price vector length mismatch")
        return max((abs(a - b) for a, b in zip(left, right)), default=0)

    def _current_reserve_requirement(self) -> int:
        if self.status == STATUS_RESOLVED and 0 <= self.winning_outcome < self.num_outcomes:
            return self.total_user_shares[self.winning_outcome]
        if self.status == STATUS_CANCELLED:
            return self.total_outstanding_cost_basis
        return 0

    def _releasable_residual_pool(self) -> int:
        if self.status not in (STATUS_RESOLVED, STATUS_CANCELLED):
            return 0
        return max(0, self.pool_balance + self.total_residual_claimed - self._current_reserve_requirement())

    def _normalized_residual_window(self) -> int:
        if self.settlement_timestamp <= self.activation_timestamp + 1:
            return 0
        return self.settlement_timestamp - self.activation_timestamp - 1

    def _residual_weight(self, sender: str) -> int:
        self._ensure_user(sender)
        shares = self.user_lp_shares[sender]
        if shares <= 0:
            return 0
        window = self._normalized_residual_window()
        if window <= 0:
            return shares
        extra_step_units = max(0, (self.settlement_timestamp - 1) * shares - self.user_lp_weighted_entry_sum[sender])
        premium = (self.residual_linear_lambda_fp * extra_step_units) // (SCALE * window)
        return shares + premium

    def _total_residual_weight(self) -> int:
        if self.lp_shares_total <= 0:
            return 0
        window = self._normalized_residual_window()
        if window <= 0:
            return self.lp_shares_total
        extra_step_units = max(0, (self.settlement_timestamp - 1) * self.lp_shares_total - self.total_lp_weighted_entry_sum)
        premium = (self.residual_linear_lambda_fp * extra_step_units) // (SCALE * window)
        return self.lp_shares_total + premium

    def _claimable_residual(self, sender: str) -> int:
        self._ensure_user(sender)
        if self.status not in (STATUS_RESOLVED, STATUS_CANCELLED):
            return 0
        total_weight = self._total_residual_weight()
        if total_weight <= 0:
            return 0
        user_weight = self._residual_weight(sender)
        entitled = (self._releasable_residual_pool() * user_weight) // total_weight
        return max(0, entitled - self.user_residual_claimed[sender])

    def _assert_solvency(self) -> None:
        if self.status in (STATUS_RESOLVED, STATUS_CANCELLED):
            reserve_required = self._current_reserve_requirement()
            self._require(self.pool_balance >= reserve_required, "winner/refund reserve invariant violated")

    def _assert_refund_reserve(self) -> None:
        self._assert_solvency()

    def _assert_invariants(self) -> None:
        if self.status in (
            STATUS_ACTIVE,
            STATUS_RESOLUTION_PENDING,
            STATUS_RESOLUTION_PROPOSED,
            STATUS_RESOLVED,
            STATUS_CANCELLED,
            STATUS_DISPUTED,
        ):
            self._require(self.winner_share_bps + self.dispute_sink_share_bps <= 10_000, "dispute split invariant violated")
            self._assert_solvency()
            if self.status != STATUS_RESOLVED:
                prices = lmsr_prices(self.q, self.b)
                self._require(abs(sum(prices) - SCALE) <= self.num_outcomes, "price sum invariant violated")

    def bootstrap(self, *, sender: str, deposit_amount: int, now: int = 0) -> int:
        self._require_status(STATUS_CREATED)
        self._require_authorized(sender, self.creator, "only creator can bootstrap")
        self._require(deposit_amount > 0, "bootstrap deposit must be positive")
        self._require_lmsr_bootstrap_floor(deposit_amount)
        self._ensure_user(sender)
        self.pool_balance = deposit_amount
        self.lp_shares_total = self.b
        self.activation_timestamp = now
        self.user_lp_shares[sender] = self.b
        self.user_lp_weighted_entry_sum[sender] = self.b * now
        self.total_lp_weighted_entry_sum = self.b * now
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
        self.user_claimable_fees[sender] = 0
        self.user_withdrawable_fee_surplus[sender] = 0
        self.user_residual_claimed[sender] = 0
        self.status = STATUS_ACTIVE
        self._emit(
            "Bootstrap",
            sender=sender,
            deposit_amount=deposit_amount,
            lp_share_units_minted=self.b,
            residual_linear_lambda_fp=self.residual_linear_lambda_fp,
            status=self.status,
        )
        self._assert_invariants()
        return self.b

    def buy(self, *, sender: str, outcome_index: int, max_cost: int, now: int, shares: int = SHARE_UNIT) -> dict[str, int]:
        self._active_before_deadline(now)
        self._assert_valid_outcome(outcome_index)
        self._require(max_cost > 0, "max_cost must be positive")
        self._require(shares > 0, "shares must be positive")
        self._require_share_granularity(shares)
        self._ensure_user(sender)
        cost = lmsr_cost_delta(self.q, self.b, outcome_index, shares)
        lp_fee = self._calc_fee_up(cost, self.lp_fee_bps)
        protocol_fee = self._calc_fee_up(cost, self.protocol_fee_bps)
        total_cost = cost + lp_fee + protocol_fee
        self._require(total_cost <= max_cost, "slippage exceeded")
        refund_amount = max_cost - total_cost
        self.q[outcome_index] += shares
        self.user_outcome_shares[sender][outcome_index] += shares
        self.user_cost_basis[sender][outcome_index] += cost
        self.total_user_shares[outcome_index] += shares
        self.total_outstanding_cost_basis += cost
        self.pool_balance += cost
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance += protocol_fee
        self._emit("Buy", outcome_index=outcome_index, shares=shares, total_cost=total_cost)
        self._assert_invariants()
        return {
            "shares": shares,
            "cost": cost,
            "lp_fee": lp_fee,
            "protocol_fee": protocol_fee,
            "total_cost": total_cost,
            "refund_amount": refund_amount,
        }

    def sell(self, *, sender: str, outcome_index: int, min_return: int, now: int, shares: int = SHARE_UNIT) -> dict[str, int]:
        self._active_before_deadline(now)
        self._assert_valid_outcome(outcome_index)
        self._require(min_return >= 0, "min_return must be non-negative")
        self._require(shares > 0, "shares must be positive")
        self._require_share_granularity(shares)
        self._ensure_user(sender)
        self._require(self.user_outcome_shares[sender][outcome_index] >= shares, "insufficient outcome shares")
        gross_return = lmsr_sell_return(self.q, self.b, outcome_index, shares)
        lp_fee = self._calc_fee_up(gross_return, self.lp_fee_bps)
        protocol_fee = self._calc_fee_up(gross_return, self.protocol_fee_bps)
        self._require(gross_return >= lp_fee + protocol_fee, "fees exceed gross return")
        net_return = gross_return - lp_fee - protocol_fee
        self._require(net_return >= min_return, "slippage exceeded")
        basis_reduction = self._basis_reduction(sender, outcome_index, shares)
        self.q[outcome_index] -= shares
        self.user_outcome_shares[sender][outcome_index] -= shares
        self.user_cost_basis[sender][outcome_index] -= basis_reduction
        self.total_user_shares[outcome_index] -= shares
        self.total_outstanding_cost_basis -= basis_reduction
        self.pool_balance -= gross_return
        self._distribute_lp_fee(lp_fee)
        self.protocol_fee_balance += protocol_fee
        self._emit("Sell", outcome_index=outcome_index, shares=shares, net_return=net_return)
        self._assert_invariants()
        return {
            "shares": shares,
            "gross_return": gross_return,
            "lp_fee": lp_fee,
            "protocol_fee": protocol_fee,
            "net_return": net_return,
        }

    def enter_lp_active(
        self,
        *,
        sender: str,
        target_delta_b: int,
        max_deposit: int,
        expected_prices: list[int],
        now: int,
        price_tolerance: int = PRICE_TOLERANCE_BASE,
    ) -> dict[str, int]:
        self._active_before_deadline(now)
        self._require(target_delta_b > 0, "target_delta_b must be positive")
        self._require(max_deposit > 0, "max_deposit must be positive")
        current_prices = lmsr_prices(self.q, self.b)
        self._require(len(expected_prices) == self.num_outcomes, "expected_prices length mismatch")
        self._require(self._max_price_diff(current_prices, expected_prices) <= price_tolerance, "stale LP entry price")
        self._require(max(current_prices, default=0) <= self.lp_entry_max_price_fp, "active LP entry disabled above skew cap")
        deposit_required = lmsr_collateral_required_from_prices(target_delta_b, current_prices)
        self._require(deposit_required <= max_deposit, "max_deposit too small")
        self._settle_lp_fees(sender)
        self._ensure_user(sender)
        next_b = self.b + target_delta_b
        self.q = lmsr_normalized_q_from_prices(current_prices, next_b)
        self.b = next_b
        self.pool_balance += deposit_required
        self.lp_shares_total += target_delta_b
        self.user_lp_shares[sender] += target_delta_b
        self.user_lp_weighted_entry_sum[sender] += target_delta_b * now
        self.total_lp_weighted_entry_sum += target_delta_b * now
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
        self._emit(
            "LpEnterActive",
            sender=sender,
            target_delta_b=target_delta_b,
            deposit_required=deposit_required,
            lp_shares_total=self.lp_shares_total,
            status=self.status,
        )
        self._assert_invariants()
        return {
            "shares_minted": target_delta_b,
            "deposit_required": deposit_required,
        }

    def claim_lp_fees(self, *, sender: str) -> int:
        self._ensure_user(sender)
        self._settle_lp_fees(sender)
        claimable = self.user_claimable_fees[sender]
        self._require(claimable > 0, "no claimable LP fees")
        self.user_claimable_fees[sender] = 0
        self.user_withdrawable_fee_surplus[sender] += claimable
        self._emit("ClaimLpFees", sender=sender, amount=claimable, status=self.status)
        return claimable

    def withdraw_lp_fees(self, *, sender: str, amount: int) -> int:
        self._ensure_user(sender)
        self._settle_lp_fees(sender)
        self._require(amount > 0, "amount must be positive")
        self._require(self.user_withdrawable_fee_surplus[sender] >= amount, "insufficient withdrawable LP fees")
        self._require(self.lp_fee_balance >= amount, "LP fee balance underfunded")
        self.user_withdrawable_fee_surplus[sender] -= amount
        self.lp_fee_balance -= amount
        self._emit("WithdrawLpFees", sender=sender, amount=amount, status=self.status)
        return amount

    def claim_lp_residual(self, *, sender: str) -> int:
        self._ensure_user(sender)
        self._require_status(STATUS_RESOLVED, STATUS_CANCELLED)
        payout = self._claimable_residual(sender)
        self._require(payout > 0, "no claimable residual")
        reserve_required = self._current_reserve_requirement()
        self._require(self.pool_balance - payout >= reserve_required, "residual claim would breach reserve")
        self.user_residual_claimed[sender] += payout
        self.total_residual_claimed += payout
        self.pool_balance -= payout
        self._emit(
            "ClaimLpResidual",
            sender=sender,
            amount=payout,
            reserve_required=reserve_required,
            releasable_pool=self._releasable_residual_pool(),
            status=self.status,
        )
        self._assert_invariants()
        return payout

    def provide_liq(self, *, sender: str, deposit_amount: int, now: int) -> int:
        raise MarketAppError("v4 uses enter_lp_active with target_delta_b and max_deposit")

    def withdraw_liq(self, *, sender: str, shares_to_burn: int) -> dict[str, int]:
        raise MarketAppError("v4 disables LP principal withdrawal; use claim_lp_residual after settlement")

    def finalize_resolution(self, *, sender: str, now: int) -> int:
        outcome = super().finalize_resolution(sender=sender, now=now)
        self.settlement_timestamp = now
        return outcome

    def creator_resolve_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> None:
        super().creator_resolve_dispute(sender=sender, outcome_index=outcome_index, ruling_hash=ruling_hash)
        self.settlement_timestamp = max(self.dispute_opened_at, self.proposal_timestamp, 1)

    def admin_resolve_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> None:
        super().admin_resolve_dispute(sender=sender, outcome_index=outcome_index, ruling_hash=ruling_hash)
        self.settlement_timestamp = max(self.dispute_opened_at, self.proposal_timestamp, 1)

    def finalize_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> int:
        outcome = super().finalize_dispute(sender=sender, outcome_index=outcome_index, ruling_hash=ruling_hash)
        self.settlement_timestamp = max(self.dispute_opened_at, self.proposal_timestamp, 1)
        return outcome

    def cancel_dispute_and_market(self, *, sender: str, ruling_hash: bytes) -> None:
        super().cancel_dispute_and_market(sender=sender, ruling_hash=ruling_hash)
        self.settlement_timestamp = max(self.dispute_opened_at, self.proposal_timestamp, 1)

    def cancel(self, *, sender: str) -> None:
        super().cancel(sender=sender)
        self.settlement_timestamp = self.deadline

    def claim(self, *, sender: str, outcome_index: int, shares: int = SHARE_UNIT) -> dict[str, int]:
        self._require_status(STATUS_RESOLVED)
        self._assert_valid_outcome(outcome_index)
        self._require(outcome_index == self.winning_outcome, "only winning outcome may be claimed")
        self._ensure_user(sender)
        self._require(shares > 0, "shares must be positive")
        self._require_share_granularity(shares)
        self._require(self.user_outcome_shares[sender][outcome_index] >= shares, "insufficient winning shares")
        payout = shares
        self._require(self.pool_balance >= payout, "insufficient pool balance for claim")
        basis_reduction = self._basis_reduction(sender, outcome_index, shares)
        self.user_outcome_shares[sender][outcome_index] -= shares
        self.user_cost_basis[sender][outcome_index] -= basis_reduction
        self.total_user_shares[outcome_index] -= shares
        self.total_outstanding_cost_basis -= basis_reduction
        self.q[outcome_index] -= shares
        self.pool_balance -= payout
        self._emit("Claim", outcome_index=outcome_index, shares=shares, payout=payout)
        self._assert_invariants()
        return {"shares": shares, "payout": payout}

    def refund(self, *, sender: str, outcome_index: int, shares: int = SHARE_UNIT) -> dict[str, int]:
        self._require_status(STATUS_CANCELLED)
        self._assert_valid_outcome(outcome_index)
        self._ensure_user(sender)
        self._require(shares > 0, "shares must be positive")
        self._require_share_granularity(shares)
        self._require(self.user_outcome_shares[sender][outcome_index] >= shares, "insufficient shares for refund")
        basis_reduction = self._basis_reduction(sender, outcome_index, shares)
        refund_amount = basis_reduction
        self.user_outcome_shares[sender][outcome_index] -= shares
        self.user_cost_basis[sender][outcome_index] -= basis_reduction
        self.total_user_shares[outcome_index] -= shares
        self.total_outstanding_cost_basis -= basis_reduction
        self.q[outcome_index] -= shares
        self.pool_balance -= refund_amount
        self._emit("Refund", outcome_index=outcome_index, shares=shares, refund_amount=refund_amount)
        self._assert_invariants()
        return {"shares": shares, "refund_amount": refund_amount}


__all__ = [
    "ACTIVE_LP_MARKET_CONTRACT_VERSION",
    "ActiveLpMarketAppModel",
    "DEFAULT_LP_ENTRY_MAX_PRICE_FP",
    "DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP",
]

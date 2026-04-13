from __future__ import annotations

"""Pure Python market-app model used to specify C2 contract behavior.

This module is intentionally deterministic and chain-free so the market operation
semantics can be tested before the full Puya transaction wiring is finished.

The actual contract wrapper lives in ``market_app/contract.py`` and is expected to
mirror this model's state machine and financial calculations.
"""

from dataclasses import dataclass, field

from smart_contracts.lmsr_math import SCALE, lmsr_cost_delta, lmsr_liquidity_scale, lmsr_prices, lmsr_sell_return

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
ZERO_ADDRESS = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
DEFAULT_WINNER_SHARE_BPS = 5_000
DEFAULT_DISPUTE_SINK_SHARE_BPS = 5_000
MARKET_CONTRACT_VERSION = 3
MAX_COMMENT_BYTES = 512
SECONDS_PER_DAY = 86_400


class MarketAppError(ValueError):
    """Raised when a market operation would violate the contract rules."""


@dataclass
class MarketAppModel:
    creator: str
    currency_asa: int
    outcome_asa_ids: list[int]
    b: int
    lp_fee_bps: int
    protocol_fee_bps: int
    deadline: int
    question_hash: bytes
    main_blueprint_hash: bytes
    dispute_blueprint_hash: bytes
    challenge_window_secs: int
    protocol_config_id: int
    factory_id: int
    resolution_authority: str
    challenge_bond: int
    proposal_bond: int
    grace_period_secs: int
    market_admin: str
    challenge_bond_bps: int = 500
    proposal_bond_bps: int = 500
    challenge_bond_cap: int = 100_000_000
    proposal_bond_cap: int = 100_000_000
    proposer_fee_bps: int = 0
    proposer_fee_floor_bps: int = 0
    cancellable: bool = True
    initial_status: int = STATUS_CREATED
    cumulative_fee_per_share: int = 0
    contract_version: int = field(default=MARKET_CONTRACT_VERSION, init=False)
    min_challenge_bond: int = field(init=False)
    min_proposal_bond: int = field(init=False)
    status: int = field(init=False)
    pool_balance: int = field(default=0, init=False)
    lp_shares_total: int = field(default=0, init=False)
    proposed_outcome: int = field(default=-1, init=False)
    proposal_timestamp: int = field(default=0, init=False)
    proposal_evidence_hash: bytes = field(default=b"", init=False)
    proposer: str = field(default=ZERO_ADDRESS, init=False)
    proposer_bond_held: int = field(default=0, init=False)
    challenger_bond_held: int = field(default=0, init=False)
    challenger: str = field(default=ZERO_ADDRESS, init=False)
    challenge_reason_code: int = field(default=0, init=False)
    challenge_evidence_hash: bytes = field(default=b"", init=False)
    dispute_ref_hash: bytes = field(default=b"", init=False)
    dispute_opened_at: int = field(default=0, init=False)
    dispute_deadline: int = field(default=0, init=False)
    ruling_hash: bytes = field(default=b"", init=False)
    resolution_path_used: int = field(default=0, init=False)  # 0=main, 1=dispute, 2=admin_fallback
    dispute_backend_kind: int = field(default=0, init=False)
    pending_responder_role: int = field(default=0, init=False)  # 0=none, 1=creator, 2=admin
    winning_outcome: int = field(default=-1, init=False)
    q: list[int] = field(init=False)
    lp_fee_balance: int = field(default=0, init=False)
    protocol_fee_balance: int = field(default=0, init=False)
    bootstrap_deposit: int = field(default=0, init=False)
    total_outstanding_cost_basis: int = field(default=0, init=False)
    dispute_sink_balance: int = field(default=0, init=False)
    resolution_budget_balance: int = field(default=0, init=False)
    winner_share_bps: int = field(default=DEFAULT_WINNER_SHARE_BPS, init=False)
    dispute_sink_share_bps: int = field(default=DEFAULT_DISPUTE_SINK_SHARE_BPS, init=False)
    user_lp_shares: dict[str, int] = field(default_factory=dict, init=False)
    user_fee_snapshot: dict[str, int] = field(default_factory=dict, init=False)
    user_claimable_fees: dict[str, int] = field(default_factory=dict, init=False)
    user_outcome_shares: dict[str, list[int]] = field(default_factory=dict, init=False)
    user_cost_basis: dict[str, list[int]] = field(default_factory=dict, init=False)
    total_user_shares: list[int] = field(init=False)
    pending_payouts: dict[str, int] = field(default_factory=dict, init=False)
    events: list[dict[str, object]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not (MIN_OUTCOMES <= len(self.outcome_asa_ids) <= MAX_OUTCOMES):
            raise MarketAppError("num_outcomes must be between 2 and 16")
        if self.currency_asa <= 0:
            raise MarketAppError("currency_asa must be positive")
        if self.b <= 0:
            raise MarketAppError("b must be positive")
        if self.challenge_window_secs <= 0:
            raise MarketAppError("challenge_window_secs must be positive")
        if self.challenge_bond < 0:
            raise MarketAppError("challenge_bond must be non-negative")
        if self.proposal_bond < 0:
            raise MarketAppError("proposal_bond must be non-negative")
        if self.challenge_bond_bps < 0 or self.challenge_bond_bps > BPS_DENOMINATOR:
            raise MarketAppError("challenge_bond_bps must be between 0 and 10000")
        if self.proposal_bond_bps < 0 or self.proposal_bond_bps > BPS_DENOMINATOR:
            raise MarketAppError("proposal_bond_bps must be between 0 and 10000")
        if self.challenge_bond_cap < self.challenge_bond:
            raise MarketAppError("challenge_bond_cap must be at least challenge_bond")
        if self.proposal_bond_cap < self.proposal_bond:
            raise MarketAppError("proposal_bond_cap must be at least proposal_bond")
        if self.proposer_fee_bps < 0 or self.proposer_fee_bps > BPS_DENOMINATOR:
            raise MarketAppError("proposer_fee_bps must be between 0 and 10000")
        if self.proposer_fee_floor_bps < 0 or self.proposer_fee_floor_bps > BPS_DENOMINATOR:
            raise MarketAppError("proposer_fee_floor_bps must be between 0 and 10000")
        if self.grace_period_secs < 0:
            raise MarketAppError("grace_period_secs must be non-negative")
        if self.winner_share_bps < 0 or self.dispute_sink_share_bps < 0:
            raise MarketAppError("dispute split bps must be non-negative")
        if self.winner_share_bps + self.dispute_sink_share_bps > BPS_DENOMINATOR:
            raise MarketAppError("dispute split bps exceed 100%")
        if self.lp_fee_bps < 0 or self.protocol_fee_bps < 0:
            raise MarketAppError("fee bps must be non-negative")
        if self.creator == ZERO_ADDRESS:
            raise MarketAppError("creator must not be zero address")
        if self.resolution_authority == ZERO_ADDRESS:
            raise MarketAppError("resolution_authority must not be zero address")
        if self.market_admin == ZERO_ADDRESS:
            raise MarketAppError("market_admin must not be zero address")
        self.min_challenge_bond = self.challenge_bond
        self.min_proposal_bond = self.proposal_bond
        self.status = self.initial_status
        self.q = [0] * len(self.outcome_asa_ids)
        self.total_user_shares = [0] * len(self.outcome_asa_ids)
        self._refresh_required_bonds()

    @property
    def num_outcomes(self) -> int:
        return len(self.outcome_asa_ids)

    def _emit(self, name: str, **payload: object) -> None:
        self.events.append({"event": name, **payload})

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise MarketAppError(message)

    def _require_status(self, *allowed: int) -> None:
        self._require(self.status in allowed, f"invalid status {self.status}")

    def _require_authorized(self, sender: str, expected: str, message: str) -> None:
        self._require(sender == expected, message)

    def _ensure_user(self, sender: str) -> None:
        self.user_outcome_shares.setdefault(sender, [0] * self.num_outcomes)
        self.user_cost_basis.setdefault(sender, [0] * self.num_outcomes)
        self.user_lp_shares.setdefault(sender, 0)
        self.user_fee_snapshot.setdefault(sender, self.cumulative_fee_per_share)
        self.user_claimable_fees.setdefault(sender, 0)

    def _basis_reduction(self, sender: str, outcome_index: int, shares: int) -> int:
        current_shares = self.user_outcome_shares[sender][outcome_index]
        current_basis = self.user_cost_basis[sender][outcome_index]
        self._require(current_shares >= shares, "insufficient shares for basis reduction")
        if current_shares == shares:
            return current_basis
        return (current_basis * shares) // current_shares

    def _credit_pending_payout(self, sender: str, amount: int) -> None:
        if amount <= 0:
            return
        self._require(sender != ZERO_ADDRESS, "pending payout recipient must be set")
        self.pending_payouts[sender] = self.pending_payouts.get(sender, 0) + amount

    def _settle_dispute_and_credit(self, outcome_index: int, original_proposal: int) -> dict[str, int]:
        if outcome_index == original_proposal:
            bond_settlement = self._settle_confirmed_dispute()
            self._credit_pending_payout(self.proposer, bond_settlement["proposer_payout"])
        else:
            bond_settlement = self._settle_overturned_dispute()
            self._credit_pending_payout(self.challenger, bond_settlement["challenger_payout"])
        return bond_settlement

    def _ceil_div(self, numerator: int, denominator: int) -> int:
        self._require(denominator > 0, "division by zero")
        return (numerator + denominator - 1) // denominator

    def _calc_fee_up(self, amount: int, bps: int) -> int:
        return self._ceil_div(amount * bps, BPS_DENOMINATOR)

    def _bond_scale_base(self) -> int:
        return max(self.pool_balance, self.bootstrap_deposit)

    def _required_bond(self, minimum: int, bps: int, cap: int) -> int:
        proportional = self._ceil_div(self._bond_scale_base() * bps, BPS_DENOMINATOR)
        return min(cap, max(minimum, proportional))

    def _required_proposal_bond(self) -> int:
        return self._required_bond(self.min_proposal_bond, self.proposal_bond_bps, self.proposal_bond_cap)

    def _required_challenge_bond(self) -> int:
        return self._required_bond(self.min_challenge_bond, self.challenge_bond_bps, self.challenge_bond_cap)

    def _proposer_fee_for_bond(self, required_bond: int) -> int:
        floor_fee = self._calc_fee_up(self.min_proposal_bond, self.proposer_fee_floor_bps)
        daily_fee = self._calc_fee_up(required_bond, self.proposer_fee_bps)
        window_fee = self._ceil_div(daily_fee * self.challenge_window_secs, SECONDS_PER_DAY)
        return max(floor_fee, window_fee)

    def _required_proposer_fee(self) -> int:
        return self._proposer_fee_for_bond(self._required_proposal_bond())

    def _max_proposer_fee(self) -> int:
        return self._proposer_fee_for_bond(self.proposal_bond_cap)

    def _consume_proposer_fee(self) -> int:
        fee = self._required_proposer_fee()
        self._require(self.resolution_budget_balance >= fee, "resolution budget exhausted")
        self.resolution_budget_balance -= fee
        return fee

    def _refresh_required_bonds(self) -> None:
        self.challenge_bond = self._required_challenge_bond()
        self.proposal_bond = self._required_proposal_bond()

    def _settle_lp_fees(self, sender: str) -> None:
        self._ensure_user(sender)
        shares = self.user_lp_shares[sender]
        if shares == 0:
            self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
            return
        delta = self.cumulative_fee_per_share - self.user_fee_snapshot[sender]
        if delta > 0:
            accrued = (delta * shares) // SCALE
            self.user_claimable_fees[sender] += accrued
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share

    def _distribute_lp_fee(self, fee_amount: int) -> None:
        self.lp_fee_balance += fee_amount
        if self.lp_shares_total > 0 and fee_amount > 0:
            increment = (fee_amount * SCALE) // self.lp_shares_total
            self.cumulative_fee_per_share += increment

    def _assert_valid_outcome(self, outcome_index: int) -> None:
        self._require(0 <= outcome_index < self.num_outcomes, "outcome_index out of range")

    def _require_share_granularity(self, shares: int) -> None:
        self._require(shares >= SHARE_UNIT, "shares must be at least one whole share")
        self._require(shares % SHARE_UNIT == 0, "shares must be whole-share multiples")

    def _assert_price_sum(self) -> None:
        if self.status == STATUS_CREATED or self.b == 0:
            return
        prices = lmsr_prices(self.q, self.b)
        tolerance = self.num_outcomes
        self._require(abs(sum(prices) - SCALE) <= tolerance, "price sum invariant violated")

    def _assert_claim_inventory_coverage(self) -> None:
        for idx, (q_i, outstanding) in enumerate(zip(self.q, self.total_user_shares)):
            self._require(q_i >= outstanding, f"claim inventory coverage violated at outcome {idx}")

    def _assert_solvency(self) -> None:
        # During active trading, LMSR's bounded-loss property means pool_balance
        # can be less than max(q) by up to b*ln(N). This is expected and provably
        # bounded. We only enforce the strict solvency invariant after resolution,
        # when the actual payout obligation is known.
        if self.status == STATUS_RESOLVED and 0 <= self.winning_outcome < self.num_outcomes:
            winning_payout = self.total_user_shares[self.winning_outcome]
            self._require(self.pool_balance >= winning_payout, "solvency invariant violated")

    def _assert_refund_reserve(self) -> None:
        if self.status in (STATUS_CANCELLED, STATUS_DISPUTED):
            self._require(
                self.pool_balance >= self.total_outstanding_cost_basis,
                "refund reserve invariant violated",
            )

    def _assert_invariants(self) -> None:
        if self.status in (STATUS_ACTIVE, STATUS_RESOLUTION_PENDING, STATUS_RESOLUTION_PROPOSED, STATUS_RESOLVED, STATUS_CANCELLED, STATUS_DISPUTED):
            self._require(self.winner_share_bps + self.dispute_sink_share_bps <= BPS_DENOMINATOR, "dispute split invariant violated")
            self._assert_solvency()
            self._assert_refund_reserve()
            self._assert_claim_inventory_coverage()
            if self.status != STATUS_RESOLVED:
                self._assert_price_sum()

    def _active_before_deadline(self, now: int) -> None:
        self._require_status(STATUS_ACTIVE)
        self._require(now < self.deadline, "deadline passed")

    def post_comment(self, *, sender: str, message: str) -> None:
        encoded = message.encode("utf-8")
        self._require(len(encoded) > 0, "comment must not be empty")
        self._require(len(encoded) <= MAX_COMMENT_BYTES, "comment too long")
        self._ensure_user(sender)
        has_outcome_position = any(shares > 0 for shares in self.user_outcome_shares[sender])
        self._require(self.user_lp_shares[sender] > 0 or has_outcome_position, "only participants can comment")
        self._emit("CommentPosted", sender=sender, message=message)

    def _lmsr_bootstrap_multiplier(self) -> int:
        if self.num_outcomes <= 2:
            return 1
        if self.num_outcomes <= 7:
            return 2
        return 3

    def _require_lmsr_bootstrap_floor(self, deposit_amount: int) -> None:
        required_deposit = self.b * self._lmsr_bootstrap_multiplier()
        self._require(
            deposit_amount >= required_deposit,
            "bootstrap deposit below LMSR solvency floor",
        )

    def bootstrap(self, *, sender: str, deposit_amount: int, budget_amount: int | None = None) -> int:
        self._require_status(STATUS_CREATED)
        self._require_authorized(sender, self.creator, "only creator can bootstrap")
        self._require(deposit_amount > 0, "bootstrap deposit must be positive")
        self._require_lmsr_bootstrap_floor(deposit_amount)
        required_budget = self._max_proposer_fee()
        if budget_amount is None:
            budget_amount = required_budget
        self._require(budget_amount >= required_budget, "resolution budget too small")
        self.pool_balance = deposit_amount
        self.bootstrap_deposit = deposit_amount
        self.resolution_budget_balance = budget_amount
        self.lp_shares_total = deposit_amount
        self.user_lp_shares[sender] = deposit_amount
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
        self.user_claimable_fees[sender] = 0
        self.user_outcome_shares.setdefault(sender, [0] * self.num_outcomes)
        self.user_cost_basis.setdefault(sender, [0] * self.num_outcomes)
        self.status = STATUS_ACTIVE
        self._refresh_required_bonds()
        self._emit(
            "Bootstrap",
            sender=sender,
            deposit_amount=deposit_amount,
            lp_shares_minted=deposit_amount,
            status=self.status,
        )
        self._assert_invariants()
        return deposit_amount

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
        self._refresh_required_bonds()
        self._emit(
            "Buy",
            outcome_index=outcome_index,
            shares=shares,
            total_cost=total_cost,
        )
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
        self._refresh_required_bonds()
        self._emit(
            "Sell",
            outcome_index=outcome_index,
            shares=shares,
            net_return=net_return,
        )
        self._assert_invariants()
        return {
            "shares": shares,
            "gross_return": gross_return,
            "lp_fee": lp_fee,
            "protocol_fee": protocol_fee,
            "net_return": net_return,
        }

    def provide_liq(self, *, sender: str, deposit_amount: int, now: int) -> int:
        self._active_before_deadline(now)
        self._require(deposit_amount > 0, "deposit_amount must be positive")
        self._require(self.pool_balance > 0, "pool_balance must be positive")
        self._settle_lp_fees(sender)
        old_prices = lmsr_prices(self.q, self.b)
        scaled_q, scaled_b = lmsr_liquidity_scale(self.q, self.b, deposit_amount, self.pool_balance)
        shares_minted = (self.lp_shares_total * deposit_amount) // self.pool_balance
        self._require(shares_minted > 0, "shares_minted must be positive")
        self.q = scaled_q
        self.b = scaled_b
        self.pool_balance += deposit_amount
        self.lp_shares_total += shares_minted
        self.user_lp_shares[sender] = self.user_lp_shares.get(sender, 0) + shares_minted
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
        self.user_outcome_shares.setdefault(sender, [0] * self.num_outcomes)
        self._refresh_required_bonds()
        new_prices = lmsr_prices(self.q, self.b)
        for before, after in zip(old_prices, new_prices):
            self._require(abs(before - after) <= PRICE_TOLERANCE_BASE, "price invariance violated")
        self._emit(
            "ProvideLiquidity",
            sender=sender,
            deposit_amount=deposit_amount,
            shares_minted=shares_minted,
            status=self.status,
        )
        self._assert_invariants()
        return shares_minted

    def withdraw_liq(self, *, sender: str, shares_to_burn: int) -> dict[str, int]:
        self._require_status(STATUS_ACTIVE, STATUS_CANCELLED, STATUS_RESOLVED)
        self._require(shares_to_burn > 0, "shares_to_burn must be positive")
        self._ensure_user(sender)
        user_shares = self.user_lp_shares[sender]
        self._require(user_shares >= shares_to_burn, "insufficient lp shares")
        if self.status == STATUS_ACTIVE:
            self._require(shares_to_burn < self.lp_shares_total, "cannot drain active market liquidity")
        self._settle_lp_fees(sender)
        old_prices = lmsr_prices(self.q, self.b) if self.status == STATUS_ACTIVE else []
        fee_return = (self.user_claimable_fees[sender] * shares_to_burn) // user_shares
        remaining_total = self.lp_shares_total - shares_to_burn
        if self.status == STATUS_ACTIVE:
            usdc_return = (self.pool_balance * shares_to_burn) // self.lp_shares_total
            new_q = [(qi * remaining_total) // self.lp_shares_total for qi in self.q]
            new_b = (self.b * remaining_total) // self.lp_shares_total
            self._require(new_b > 0, "lp withdrawal would zero liquidity parameter")
            for idx, qi in enumerate(new_q):
                self._require(qi >= self.total_user_shares[idx], "lp withdrawal would strand user shares")
            self.q = new_q
            self.b = new_b
        elif self.status == STATUS_CANCELLED:
            self._require(
                self.pool_balance >= self.total_outstanding_cost_basis,
                "refund reserve invariant violated",
            )
            residual_pool = self.pool_balance - self.total_outstanding_cost_basis
            usdc_return = (residual_pool * shares_to_burn) // self.lp_shares_total
        else:
            self._require(0 <= self.winning_outcome < self.num_outcomes, "winning outcome not set")
            claim_liability = self.total_user_shares[self.winning_outcome]
            self._require(self.pool_balance >= claim_liability, "solvency invariant violated")
            residual_pool = self.pool_balance - claim_liability
            usdc_return = (residual_pool * shares_to_burn) // self.lp_shares_total
        self.pool_balance -= usdc_return
        self.lp_fee_balance -= fee_return
        self.lp_shares_total = remaining_total
        self.user_lp_shares[sender] -= shares_to_burn
        self.user_claimable_fees[sender] -= fee_return
        self.user_fee_snapshot[sender] = self.cumulative_fee_per_share
        self._refresh_required_bonds()
        if self.status == STATUS_ACTIVE and self.b > 0:
            new_prices = lmsr_prices(self.q, self.b)
            for before, after in zip(old_prices, new_prices):
                self._require(abs(before - after) <= PRICE_TOLERANCE_BASE, "price invariance violated")
        self._emit(
            "WithdrawLiquidity",
            sender=sender,
            shares_to_burn=shares_to_burn,
            usdc_return=usdc_return,
            fee_return=fee_return,
            status=self.status,
        )
        if self.status == STATUS_ACTIVE and self.b > 0:
            self._assert_invariants()
        return {
            "usdc_return": usdc_return,
            "fee_return": fee_return,
        }

    def trigger_resolution(self, *, sender: str, now: int) -> None:
        self._require_status(STATUS_ACTIVE)
        self._require(now >= self.deadline, "deadline not reached")
        self.status = STATUS_RESOLUTION_PENDING
        self._emit("TriggerResolution", sender=sender, status=self.status)
        self._assert_invariants()

    def propose_resolution(self, *, sender: str, outcome_index: int, evidence_hash: bytes, now: int, bond_paid: int | None = None) -> None:
        self._require_status(STATUS_RESOLUTION_PENDING)
        self._assert_valid_outcome(outcome_index)

        resolution_became_pending = self.deadline  # trigger_resolution sets status at deadline
        grace_expired = now >= resolution_became_pending + self.grace_period_secs

        if bond_paid is None:
            bond_paid = 0 if sender == self.resolution_authority else self._required_proposal_bond()

        if sender == self.resolution_authority:
            self._require(bond_paid >= 0, "proposal bond too small")
            self.proposer = sender
            self.proposer_bond_held = bond_paid
        elif grace_expired:
            self._require(bond_paid >= self._required_proposal_bond(), "proposal bond too small")
            self.proposer = sender
            self.proposer_bond_held = bond_paid
        else:
            raise MarketAppError("only resolution authority may propose during grace period")

        self.proposed_outcome = outcome_index
        self.proposal_timestamp = now
        self.proposal_evidence_hash = evidence_hash
        self.status = STATUS_RESOLUTION_PROPOSED
        self._emit(
            "ProposeResolution",
            sender=sender,
            outcome_index=outcome_index,
            evidence_hash=evidence_hash,
            bond_paid=bond_paid,
            status=self.status,
        )
        self._assert_invariants()

    def propose_early_resolution(
        self,
        *,
        sender: str,
        outcome_index: int,
        evidence_hash: bytes,
        now: int,
        bond_paid: int | None = None,
    ) -> None:
        self._active_before_deadline(now)
        self._assert_valid_outcome(outcome_index)
        self._require_authorized(sender, self.resolution_authority, "only resolution authority may early propose")

        if bond_paid is None:
            bond_paid = 0

        self._require(bond_paid >= 0, "proposal bond too small")
        self.proposer = sender
        self.proposer_bond_held = bond_paid
        self.proposed_outcome = outcome_index
        self.proposal_timestamp = now
        self.proposal_evidence_hash = evidence_hash
        self.status = STATUS_RESOLUTION_PROPOSED
        self._emit(
            "ProposeEarlyResolution",
            sender=sender,
            outcome_index=outcome_index,
            evidence_hash=evidence_hash,
            bond_paid=bond_paid,
            status=self.status,
        )
        self._assert_invariants()

    def challenge_resolution(self, *, sender: str, bond_paid: int, reason_code: int, evidence_hash: bytes, now: int) -> None:
        self._require_status(STATUS_RESOLUTION_PROPOSED)
        self._require(now < self.proposal_timestamp + self.challenge_window_secs, "challenge window closed")
        self._require(bond_paid >= self._required_challenge_bond(), "challenge bond too small")
        self.challenger = sender
        self.challenger_bond_held = bond_paid
        self.challenge_reason_code = reason_code
        self.challenge_evidence_hash = evidence_hash
        self.dispute_opened_at = now
        self.status = STATUS_DISPUTED
        self._emit(
            "ChallengeResolution",
            sender=sender,
            bond_paid=bond_paid,
            reason_code=reason_code,
            evidence_hash=evidence_hash,
            status=self.status,
        )
        self._assert_invariants()

    def _winner_bonus_from_bond(self, losing_bond: int) -> int:
        return (losing_bond * self.winner_share_bps) // BPS_DENOMINATOR

    def _settle_confirmed_dispute(self) -> dict[str, int]:
        losing_bond = self.challenger_bond_held
        winner_bonus = self._winner_bonus_from_bond(losing_bond)
        proposer_fee = self._consume_proposer_fee()
        dispute_sink_capture = losing_bond - winner_bonus
        proposer_payout = self.proposer_bond_held + winner_bonus + proposer_fee
        proposer_refund = self.proposer_bond_held
        self.dispute_sink_balance += dispute_sink_capture
        self.proposer_bond_held = 0
        self.challenger_bond_held = 0
        return {
            "proposer_refund": proposer_refund,
            "proposer_fee": proposer_fee,
            "proposer_reward": winner_bonus,
            "proposer_payout": proposer_payout,
            "challenger_refund": 0,
            "challenger_reward": 0,
            "dispute_sink_capture": dispute_sink_capture,
        }

    def _settle_overturned_dispute(self) -> dict[str, int]:
        losing_bond = self.proposer_bond_held
        winner_bonus = self._winner_bonus_from_bond(losing_bond)
        dispute_sink_capture = losing_bond - winner_bonus
        challenger_payout = self.challenger_bond_held + winner_bonus
        challenger_refund = self.challenger_bond_held
        self.dispute_sink_balance += dispute_sink_capture
        self.proposer_bond_held = 0
        self.challenger_bond_held = 0
        return {
            "proposer_refund": 0,
            "proposer_fee": 0,
            "proposer_reward": 0,
            "challenger_refund": challenger_refund,
            "challenger_reward": winner_bonus,
            "challenger_payout": challenger_payout,
            "dispute_sink_capture": dispute_sink_capture,
        }

    def _settle_cancel_bonds(self) -> dict[str, int]:
        challenger_refund = self.challenger_bond_held
        proposer_slash = self.proposer_bond_held
        self.dispute_sink_balance += proposer_slash
        self.proposer_bond_held = 0
        self.challenger_bond_held = 0
        return {
            "challenger_refund": challenger_refund,
            "proposer_slash": proposer_slash,
            "dispute_sink_capture": proposer_slash,
        }

    def _clear_proposal_and_dispute_metadata(self) -> None:
        self.proposed_outcome = -1
        self.proposal_timestamp = 0
        self.proposal_evidence_hash = b""
        self.proposer = ZERO_ADDRESS
        self.challenger = ZERO_ADDRESS
        self.challenge_reason_code = 0
        self.challenge_evidence_hash = b""
        self.dispute_ref_hash = b""
        self.dispute_opened_at = 0
        self.dispute_deadline = 0
        self.ruling_hash = b""
        self.resolution_path_used = 0
        self.dispute_backend_kind = 0
        self.pending_responder_role = 0

    def register_dispute(self, *, sender: str, dispute_ref_hash: bytes, backend_kind: int, deadline: int) -> None:
        self._require_status(STATUS_DISPUTED)
        self._require_authorized(sender, self.resolution_authority, "only resolution authority may register dispute")
        self.dispute_ref_hash = dispute_ref_hash
        self.dispute_backend_kind = backend_kind
        self.dispute_deadline = deadline
        self._emit(
            "RegisterDispute",
            sender=sender,
            dispute_ref_hash=dispute_ref_hash,
            backend_kind=backend_kind,
            deadline=deadline,
            status=self.status,
        )
        self._assert_invariants()

    def creator_resolve_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> None:
        self._require_status(STATUS_DISPUTED)
        self._require_authorized(sender, self.creator, "only creator may resolve dispute")
        self._assert_valid_outcome(outcome_index)
        original_proposal = self.proposed_outcome
        bond_settlement = self._settle_dispute_and_credit(outcome_index, original_proposal)
        self.proposed_outcome = outcome_index
        self.ruling_hash = ruling_hash
        self.resolution_path_used = 1  # dispute
        self.pending_responder_role = 0
        self.status = STATUS_RESOLVED
        self.winning_outcome = outcome_index
        self._emit(
            "CreatorResolveDispute",
            sender=sender,
            outcome_index=outcome_index,
            ruling_hash=ruling_hash,
            status=self.status,
            **bond_settlement,
        )
        self._assert_invariants()

    def admin_resolve_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> None:
        self._require_status(STATUS_DISPUTED)
        self._require_authorized(sender, self.market_admin, "only market admin may resolve dispute")
        self._assert_valid_outcome(outcome_index)
        original_proposal = self.proposed_outcome
        bond_settlement = self._settle_dispute_and_credit(outcome_index, original_proposal)
        self.proposed_outcome = outcome_index
        self.ruling_hash = ruling_hash
        self.resolution_path_used = 2  # admin_fallback
        self.pending_responder_role = 0
        self.status = STATUS_RESOLVED
        self.winning_outcome = outcome_index
        self._emit(
            "AdminResolveDispute",
            sender=sender,
            outcome_index=outcome_index,
            ruling_hash=ruling_hash,
            status=self.status,
            **bond_settlement,
        )
        self._assert_invariants()

    def finalize_dispute(self, *, sender: str, outcome_index: int, ruling_hash: bytes) -> int:
        self._require_status(STATUS_DISPUTED)
        self._assert_valid_outcome(outcome_index)
        self._require_authorized(sender, self.resolution_authority, "only resolution authority may finalize dispute")
        original_proposal = self.proposed_outcome
        bond_settlement = self._settle_dispute_and_credit(outcome_index, original_proposal)
        self.ruling_hash = ruling_hash
        self.resolution_path_used = 1  # dispute
        self.pending_responder_role = 0
        self.status = STATUS_RESOLVED
        self.winning_outcome = outcome_index
        self._emit(
            "FinalizeDispute",
            sender=sender,
            outcome_index=outcome_index,
            ruling_hash=ruling_hash,
            status=self.status,
            **bond_settlement,
        )
        self._assert_invariants()
        return self.winning_outcome

    def abort_early_resolution(self, *, sender: str, ruling_hash: bytes, now: int) -> None:
        self._require_status(STATUS_DISPUTED)
        self._require_authorized(sender, self.resolution_authority, "only resolution authority may abort early resolution")
        self._require(self.proposal_timestamp > 0, "proposal was not early")
        self._require(self.proposal_timestamp < self.deadline, "proposal was not early")
        challenger = self.challenger
        bond_settlement = self._settle_overturned_dispute()
        self._credit_pending_payout(challenger, bond_settlement["challenger_payout"])
        self._clear_proposal_and_dispute_metadata()
        self.status = STATUS_ACTIVE if now < self.deadline else STATUS_RESOLUTION_PENDING
        self._emit(
            "AbortEarlyResolution",
            sender=sender,
            challenger=challenger,
            ruling_hash=ruling_hash,
            resumed_status=self.status,
            status=self.status,
            **bond_settlement,
        )
        self._assert_invariants()

    def cancel_dispute_and_market(self, *, sender: str, ruling_hash: bytes) -> None:
        self._require_status(STATUS_DISPUTED)
        self._require_authorized(sender, self.resolution_authority, "only resolution authority may cancel dispute")
        bond_settlement = self._settle_cancel_bonds()
        self._credit_pending_payout(self.challenger, bond_settlement["challenger_refund"])
        self.ruling_hash = ruling_hash
        self.resolution_path_used = 1
        self.pending_responder_role = 0
        self.status = STATUS_CANCELLED
        self._emit(
            "CancelDisputeAndMarket",
            sender=sender,
            ruling_hash=ruling_hash,
            status=self.status,
            **bond_settlement,
        )
        self._assert_invariants()

    def finalize_resolution(self, *, sender: str, now: int) -> int:
        self._require_status(STATUS_RESOLUTION_PROPOSED)
        self._require(now >= self.proposal_timestamp + self.challenge_window_secs, "challenge window not elapsed")
        self._require(self.challenger == ZERO_ADDRESS, "market was challenged")
        proposer_fee = self._consume_proposer_fee()
        proposer_refund = self.proposer_bond_held
        self.proposer_bond_held = 0
        self._credit_pending_payout(self.proposer, proposer_refund + proposer_fee)
        self.status = STATUS_RESOLVED
        self.winning_outcome = self.proposed_outcome
        self._emit(
            "FinalizeResolution",
            sender=sender,
            winning_outcome=self.winning_outcome,
            proposer_refund=proposer_refund,
            proposer_fee=proposer_fee,
            status=self.status,
        )
        self._assert_invariants()
        return self.winning_outcome

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
        self._refresh_required_bonds()
        self._emit(
            "Claim",
            outcome_index=outcome_index,
            shares=shares,
            payout=payout,
        )
        self._assert_solvency()
        return {"shares": shares, "payout": payout}

    def cancel(self, *, sender: str) -> None:
        self._require_status(STATUS_ACTIVE)
        self._require(self.cancellable, "market is not cancellable")
        self._require_authorized(sender, self.creator, "only creator may cancel")
        self.status = STATUS_CANCELLED
        self._emit("Cancel", sender=sender, status=self.status)
        self._assert_invariants()

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
        self._refresh_required_bonds()
        self._emit(
            "Refund",
            outcome_index=outcome_index,
            shares=shares,
            refund_amount=refund_amount,
        )
        self._assert_solvency()
        return {"shares": shares, "refund_amount": refund_amount}

    def withdraw_pending_payouts(self, *, sender: str) -> int:
        amount = self.pending_payouts.get(sender, 0)
        self._require(amount > 0, "no pending payouts")
        self.pending_payouts[sender] = 0
        self._emit(
            "WithdrawPendingPayouts",
            sender=sender,
            amount=amount,
            status=self.status,
        )
        return amount

    def reclaim_resolution_budget(self, *, sender: str) -> int:
        self._require_status(STATUS_RESOLVED, STATUS_CANCELLED)
        self._require_authorized(sender, self.creator, "only creator may reclaim resolution budget")
        amount = self.resolution_budget_balance
        self._require(amount > 0, "no resolution budget")
        self.resolution_budget_balance = 0
        self._emit(
            "ReclaimResolutionBudget",
            sender=sender,
            amount=amount,
            status=self.status,
        )
        return amount


__all__ = [
    "BPS_DENOMINATOR",
    "MAX_OUTCOMES",
    "MIN_OUTCOMES",
    "MarketAppError",
    "MarketAppModel",
    "PRICE_TOLERANCE_BASE",
    "SHARE_UNIT",
    "STATUS_ACTIVE",
    "STATUS_CANCELLED",
    "STATUS_CREATED",
    "STATUS_DISPUTED",
    "STATUS_RESOLUTION_PENDING",
    "STATUS_RESOLUTION_PROPOSED",
    "STATUS_RESOLVED",
    "ZERO_ADDRESS",
]

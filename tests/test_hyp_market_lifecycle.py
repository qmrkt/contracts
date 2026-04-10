from __future__ import annotations

from hypothesis import assume, given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from smart_contracts.lmsr_math import LMSRMathError
from smart_contracts.market_app.model import (
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    MarketAppError,
)
from tests.market_app_test_utils import make_market
from tests.test_helpers import safe_bootstrap_deposit


PARTICIPANTS = ("alice", "bob", "lp2")
OUTCOMES = (0, 1, 2)


def escrow_held_total(market) -> int:
    return (
        market.pool_balance
        + market.lp_fee_balance
        + market.protocol_fee_balance
        + market.proposer_bond_held
        + market.challenger_bond_held
        + market.dispute_sink_balance
        + sum(market.pending_payouts.values())
    )


class MarketLifecycleMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.market = make_market()
        self.now = 1
        self.usdc_in = 0
        self.usdc_out = 0

    @initialize()
    def create_and_bootstrap(self) -> None:
        deposit = safe_bootstrap_deposit(self.market.num_outcomes, self.market.b, minimum=200_000_000)
        self.market.bootstrap(sender="creator", deposit_amount=deposit)
        self.usdc_in += deposit

    def _tick(self) -> None:
        self.now += 1

    @precondition(lambda self: self.market.status == STATUS_ACTIVE and self.now < self.market.deadline)
    @rule(sender=st.sampled_from(PARTICIPANTS), outcome=st.sampled_from(OUTCOMES))
    def buy(self, sender: str, outcome: int) -> None:
        result = self.market.buy(sender=sender, outcome_index=outcome, max_cost=10**18, now=self.now)
        self.usdc_in += result["total_cost"]
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_ACTIVE and self.now < self.market.deadline)
    @rule(sender=st.sampled_from(PARTICIPANTS), outcome=st.sampled_from(OUTCOMES))
    def sell(self, sender: str, outcome: int) -> None:
        shares = self.market.user_outcome_shares.get(sender, [0] * self.market.num_outcomes)[outcome]
        assume(shares >= SHARE_UNIT)

        try:
            result = self.market.sell(sender=sender, outcome_index=outcome, min_return=0, now=self.now)
        except (LMSRMathError, MarketAppError):
            assume(False)
        self.usdc_out += result["net_return"]
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_ACTIVE and self.now < self.market.deadline)
    @rule(sender=st.sampled_from(("creator", "lp2")), deposit=st.integers(min_value=1_000_000, max_value=50_000_000))
    def provide_liq(self, sender: str, deposit: int) -> None:
        minted = self.market.provide_liq(sender=sender, deposit_amount=deposit, now=self.now)
        assume(minted > 0)
        self.usdc_in += deposit
        self._tick()

    @precondition(lambda self: self.market.status in (STATUS_ACTIVE, STATUS_CANCELLED))
    @rule(sender=st.sampled_from(("creator", "lp2")), numerator=st.integers(min_value=1, max_value=4))
    def withdraw_liq(self, sender: str, numerator: int) -> None:
        shares = self.market.user_lp_shares.get(sender, 0)
        assume(shares > 0)
        burn = max(1, shares // numerator)
        burn = min(burn, shares)

        try:
            result = self.market.withdraw_liq(sender=sender, shares_to_burn=burn)
        except MarketAppError:
            assume(False)
        self.usdc_out += result["usdc_return"] + result["fee_return"]
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_ACTIVE)
    @rule()
    def advance_past_deadline(self) -> None:
        self.now = max(self.now, self.market.deadline)

    @precondition(lambda self: self.market.status == STATUS_ACTIVE and self.now >= self.market.deadline)
    @rule()
    def trigger_resolution(self) -> None:
        self.market.trigger_resolution(sender="anyone", now=self.now)
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_RESOLUTION_PENDING)
    @rule(outcome=st.sampled_from(OUTCOMES))
    def propose_resolution(self, outcome: int) -> None:
        self.market.propose_resolution(
            sender="resolver",
            outcome_index=outcome,
            evidence_hash=b"e" * 32,
            now=self.now,
            bond_paid=0,
        )
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_RESOLUTION_PROPOSED)
    @rule()
    def finalize_resolution(self) -> None:
        self.now = max(self.now, self.market.proposal_timestamp + self.market.challenge_window_secs)
        self.market.finalize_resolution(sender="anyone", now=self.now)
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_ACTIVE and self.market.cancellable)
    @rule()
    def cancel(self) -> None:
        self.market.cancel(sender="creator")
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_CANCELLED)
    @rule(sender=st.sampled_from(PARTICIPANTS), outcome=st.sampled_from(OUTCOMES))
    def refund(self, sender: str, outcome: int) -> None:
        shares = self.market.user_outcome_shares.get(sender, [0] * self.market.num_outcomes)[outcome]
        assume(shares > 0)
        result = self.market.refund(sender=sender, outcome_index=outcome, shares=min(SHARE_UNIT, shares))
        self.usdc_out += result["refund_amount"]
        self._tick()

    @precondition(lambda self: self.market.status == STATUS_RESOLVED)
    @rule(sender=st.sampled_from(PARTICIPANTS))
    def claim(self, sender: str) -> None:
        winning = self.market.winning_outcome
        assume(0 <= winning < self.market.num_outcomes)
        shares = self.market.user_outcome_shares.get(sender, [0] * self.market.num_outcomes)[winning]
        assume(shares > 0)
        result = self.market.claim(sender=sender, outcome_index=winning, shares=min(SHARE_UNIT, shares))
        self.usdc_out += result["payout"]
        self._tick()

    @rule()
    def withdraw_pending_payouts(self) -> None:
        for sender, amount in list(self.market.pending_payouts.items()):
            if amount > 0:
                withdrawn = self.market.withdraw_pending_payouts(sender=sender)
                self.usdc_out += withdrawn
                self._tick()
                return
        assume(False)

    @invariant()
    def balances_are_non_negative(self) -> None:
        assert self.market.pool_balance >= 0
        assert self.market.lp_fee_balance >= 0
        assert self.market.protocol_fee_balance >= 0
        assert self.market.proposer_bond_held >= 0
        assert self.market.challenger_bond_held >= 0
        assert self.market.dispute_sink_balance >= 0
        assert self.market.lp_shares_total >= 0
        assert self.market.b >= 0

    @invariant()
    def escrow_accounting_matches_cashflow(self) -> None:
        assert self.usdc_in >= self.usdc_out
        assert self.usdc_in - self.usdc_out == escrow_held_total(self.market)

    @invariant()
    def user_share_consistency_holds(self) -> None:
        for holdings in self.market.user_outcome_shares.values():
            for outcome_index, shares in enumerate(holdings):
                assert shares <= self.market.q[outcome_index]

    @invariant()
    def active_market_viability_holds(self) -> None:
        assert self.market.status != STATUS_ACTIVE or self.market.b > 0


TestMarketLifecycleMachine = MarketLifecycleMachine.TestCase


@given(st.integers(min_value=25_000_000, max_value=75_000_000))
@settings(max_examples=100)
def test_buy_then_lp_cycle_then_sell_keeps_user_supply_within_q(lp_deposit: int) -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.buy(sender="alice", outcome_index=0, max_cost=10**18, now=5_000)
    market.provide_liq(sender="lp2", deposit_amount=lp_deposit, now=6_000)

    creator_lp = market.user_lp_shares["creator"]
    try:
        market.withdraw_liq(sender="creator", shares_to_burn=creator_lp)
    except MarketAppError:
        pass

    assert market.user_outcome_shares["alice"][0] <= market.q[0]


@given(st.integers(min_value=1, max_value=3))
@settings(max_examples=30)
def test_buy_then_resolve_then_claim_vs_lp_preserves_residual(num_winner_buys: int) -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    for _ in range(num_winner_buys):
        market.buy(sender="alice", outcome_index=0, max_cost=10**18, now=5_000)
    market.buy(sender="bob", outcome_index=1, max_cost=10**18, now=5_001)

    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(
        sender="resolver",
        outcome_index=0,
        evidence_hash=b"e" * 32,
        now=market.deadline + 1,
    )
    market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)

    starting_pool = market.pool_balance
    claim_result = market.claim(sender="alice", outcome_index=0, shares=SHARE_UNIT)

    assert claim_result["payout"] == SHARE_UNIT
    creator_lp = market.user_lp_shares["creator"]
    withdraw_result = market.withdraw_liq(sender="creator", shares_to_burn=creator_lp)
    assert withdraw_result["usdc_return"] == starting_pool - (num_winner_buys * SHARE_UNIT)


def test_extreme_lp_withdrawal_never_bricks_active_market() -> None:
    market = make_market(num_outcomes=2)
    market.b = 1
    market.bootstrap(sender="creator", deposit_amount=2)

    try:
        market.withdraw_liq(sender="creator", shares_to_burn=1)
    except MarketAppError:
        pass

    assert market.status != STATUS_ACTIVE or market.b > 0

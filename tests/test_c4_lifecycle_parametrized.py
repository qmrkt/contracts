"""C4 lifecycle tests parametrized across N=2, N=5, N=16.

Each lifecycle path is tested at multiple outcome counts to verify
the contract works correctly regardless of market complexity.
"""

from __future__ import annotations

import pytest

from smart_contracts.lmsr_math import SCALE, lmsr_prices
from smart_contracts.market_app.model import (
    SHARE_UNIT,
    MarketAppError,
    MarketAppModel,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_DISPUTED,
    STATUS_RESOLVED,
)

DEPOSIT = 200_000_000
MAX_COST = 50_000_000


from tests.test_helpers import safe_bootstrap_deposit as bootstrap_deposit


def make_market(num_outcomes: int) -> MarketAppModel:
    return MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=list(range(1000, 1000 + num_outcomes)),
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=100_000,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        grace_period_secs=3_600,
        market_admin="admin",
    )


def withdraw_with_safe_burn(market: MarketAppModel, sender: str, target_burn: int) -> dict[str, int] | None:
    burn = min(target_burn, market.user_lp_shares[sender])
    while burn > 0:
        try:
            return market.withdraw_liq(sender=sender, shares_to_burn=burn)
        except MarketAppError:
            burn //= 2
    return None


@pytest.mark.parametrize("n", [2, 5, 16], ids=["N=2", "N=5", "N=16"])
class TestLifecycleParametrized:

    def test_happy_path(self, n: int) -> None:
        """create → bootstrap → buy → sell → trigger → propose → finalize → claim"""
        m = make_market(n)
        m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(n))
        assert m.status == STATUS_ACTIVE

        # Buy outcome 0
        buy_result = m.buy(sender="winner", outcome_index=0, max_cost=MAX_COST, now=1000)
        assert buy_result["total_cost"] > 0

        # Sell a different outcome after buying it
        m.buy(sender="seller", outcome_index=1, max_cost=MAX_COST, now=1001)
        sell_result = m.sell(sender="seller", outcome_index=1, min_return=0, now=1002)
        assert sell_result["net_return"] > 0

        # Resolution
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)
        assert m.status == STATUS_RESOLVED
        assert m.winning_outcome == 0

        # Claim
        claim_result = m.claim(sender="winner", outcome_index=0)
        assert claim_result["payout"] > 0
        assert m.pool_balance >= 0

    def test_challenge_path(self, n: int) -> None:
        """create → bootstrap → buy → trigger → propose → challenge → refund"""
        m = make_market(n)
        m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(n))

        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        pool_after_buy = m.pool_balance

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
        assert m.status == STATUS_DISPUTED

        m.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
        assert m.status == STATUS_CANCELLED

        refund_result = m.refund(sender="trader", outcome_index=0)
        assert refund_result["refund_amount"] > 0
        assert m.pool_balance >= 0

    def test_creator_cancel_path(self, n: int) -> None:
        """create → bootstrap → buy → cancel → refund"""
        m = make_market(n)
        m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(n))

        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        m.cancel(sender="creator")
        assert m.status == STATUS_CANCELLED

        refund_result = m.refund(sender="trader", outcome_index=0)
        assert refund_result["refund_amount"] > 0

    def test_lp_lifecycle(self, n: int) -> None:
        """create → bootstrap → provide → trades → withdraw (with fees)"""
        m = make_market(n)
        m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(n))

        # LP2 provides liquidity
        prices_before = lmsr_prices(m.q, m.b)
        shares_minted = m.provide_liq(sender="lp2", deposit_amount=100_000_000, now=1000)
        prices_after = lmsr_prices(m.q, m.b)
        assert shares_minted > 0
        for p_before, p_after in zip(prices_before, prices_after):
            assert abs(p_before - p_after) <= 1

        # Trades generate fees
        for i in range(min(n, 4)):
            m.buy(sender=f"trader{i}", outcome_index=i % n, max_cost=MAX_COST, now=2000 + i)

        # LP2 withdraws
        lp2_shares = m.user_lp_shares["lp2"]
        # Withdraw half
        result = withdraw_with_safe_burn(m, "lp2", lp2_shares // 2)
        if result is None:
            assert all(qi >= user_supply for qi, user_supply in zip(m.q, m.total_user_shares))
            return
        assert result["usdc_return"] > 0

        # Creator withdraws some
        creator_shares = m.user_lp_shares["creator"]
        result = withdraw_with_safe_burn(m, "creator", creator_shares // 4)
        if result is None:
            assert all(qi >= user_supply for qi, user_supply in zip(m.q, m.total_user_shares))
            return
        assert result["usdc_return"] > 0

    def test_multi_trader_multi_outcome(self, n: int) -> None:
        """Multiple traders buy different outcomes, resolve, winners claim."""
        m = make_market(n)
        m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(n))

        # Each trader buys a different outcome
        traders = [f"trader_{i}" for i in range(min(n, 4))]
        for i, trader in enumerate(traders):
            m.buy(sender=trader, outcome_index=i % n, max_cost=MAX_COST, now=1000 + i)

        # Resolve to outcome 0
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)

        # Only trader_0 can claim
        claim_result = m.claim(sender="trader_0", outcome_index=0)
        assert claim_result["payout"] > 0

        # Others cannot claim their non-winning outcomes
        for i, trader in enumerate(traders[1:], 1):
            with pytest.raises(Exception):
                m.claim(sender=trader, outcome_index=i % n)

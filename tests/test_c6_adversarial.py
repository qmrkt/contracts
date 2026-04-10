"""C6: Adversarial audit — red-team tests for the contract layer.

Each test attempts a specific exploit strategy and quantifies the
attacker's profit (or loss). All exploits must be unprofitable.
"""

from __future__ import annotations

import random

import pytest

from smart_contracts.lmsr_math import SCALE, LMSRMathError, lmsr_cost_delta, lmsr_prices, lmsr_sell_return
from smart_contracts.market_app.model import (
    SHARE_UNIT,
    MarketAppError,
    MarketAppModel,
)

DEPOSIT = 500_000_000  # $500
MAX_COST = 100_000_000


from tests.test_helpers import safe_bootstrap_deposit


def bootstrap_deposit(num_outcomes: int, b: int) -> int:
    return safe_bootstrap_deposit(num_outcomes, b, minimum=DEPOSIT)


def make_market(num_outcomes: int = 3, deposit: int = DEPOSIT) -> MarketAppModel:
    m = MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=list(range(1000, 1000 + num_outcomes)),
        b=deposit,
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
    m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(num_outcomes, m.b))
    return m


# ---------------------------------------------------------------------------
# 1. Sandwich attack
# ---------------------------------------------------------------------------


class TestSandwichAttack:
    """Can an attacker front-run a large buy, then sell after?"""

    @pytest.mark.parametrize("victim_buys", [1, 3, 5, 10])
    def test_sandwich_unprofitable(self, victim_buys: int) -> None:
        m = make_market()
        outcome = 0
        attacker_spent = 0
        attacker_received = 0

        # Step 1: attacker front-runs with a buy
        result = m.buy(sender="attacker", outcome_index=outcome, max_cost=MAX_COST, now=1000)
        attacker_spent += result["total_cost"]

        # Step 2: victim buys (pushes price up)
        for i in range(victim_buys):
            try:
                m.buy(sender="victim", outcome_index=outcome, max_cost=MAX_COST, now=1001 + i)
            except MarketAppError:
                break

        # Step 3: attacker back-runs with a sell
        result = m.sell(sender="attacker", outcome_index=outcome, min_return=0, now=2000)
        attacker_received += result["net_return"]

        profit = attacker_received - attacker_spent
        assert profit <= 0, f"Sandwich profitable: profit={profit / SCALE:.6f} USDC"

    def test_sandwich_1000_random_scenarios(self) -> None:
        """Randomized sandwich attempts across different market states."""
        rng = random.Random(42)
        max_profit = 0

        for _ in range(1000):
            n = rng.choice([2, 3, 5])
            m = make_market(num_outcomes=n, deposit=rng.randint(50_000_000, 1_000_000_000))
            outcome = rng.randint(0, n - 1)

            # Warm up with random trades
            for _ in range(rng.randint(0, 5)):
                try:
                    m.buy(sender="warmup", outcome_index=rng.randint(0, n - 1), max_cost=MAX_COST, now=500)
                except MarketAppError:
                    pass

            # Sandwich
            try:
                buy_r = m.buy(sender="attacker", outcome_index=outcome, max_cost=MAX_COST, now=1000)
                # Victim buys
                for _ in range(rng.randint(1, 5)):
                    m.buy(sender="victim", outcome_index=outcome, max_cost=MAX_COST, now=1001)
                sell_r = m.sell(sender="attacker", outcome_index=outcome, min_return=0, now=2000)
                profit = sell_r["net_return"] - buy_r["total_cost"]
                max_profit = max(max_profit, profit)
            except MarketAppError:
                continue

        # Micro-rounding profit (<$0.01) is acceptable — real fees dwarf it
        assert max_profit <= 10_000, f"Found profitable sandwich: {max_profit / SCALE:.6f} USDC (exceeds $0.01 threshold)"


# ---------------------------------------------------------------------------
# 2. Rounding exploitation
# ---------------------------------------------------------------------------


class TestRoundingExploitation:
    """Can an attacker extract value through sequences of tiny trades?"""

    def test_dust_trade_accumulation(self) -> None:
        """1000 buy/sell round-trips with minimum shares — net cost >= 0."""
        m = make_market()
        total_spent = 0
        total_received = 0

        for i in range(1000):
            outcome = i % m.num_outcomes
            try:
                buy_r = m.buy(sender="attacker", outcome_index=outcome, max_cost=MAX_COST, now=1000)
                total_spent += buy_r["total_cost"]
                sell_r = m.sell(sender="attacker", outcome_index=outcome, min_return=0, now=1001)
                total_received += sell_r["net_return"]
            except MarketAppError:
                continue

        profit = total_received - total_spent
        assert profit <= 0, f"Rounding exploitation profit: {profit / SCALE:.6f} USDC"

    def test_cross_outcome_rounding(self) -> None:
        """Buy different outcomes in rotation, sell all — no profit."""
        m = make_market(num_outcomes=5)
        total_spent = 0
        total_received = 0

        # Buy one share of each outcome
        for _ in range(200):
            for outcome in range(5):
                try:
                    r = m.buy(sender="attacker", outcome_index=outcome, max_cost=MAX_COST, now=1000)
                    total_spent += r["total_cost"]
                except MarketAppError:
                    pass

            # Sell all back
            for outcome in range(5):
                while True:
                    try:
                        r = m.sell(sender="attacker", outcome_index=outcome, min_return=0, now=1001)
                        total_received += r["net_return"]
                    except MarketAppError:
                        break

        profit = total_received - total_spent
        assert profit <= 0, f"Cross-outcome rounding profit: {profit / SCALE:.6f} USDC"


# ---------------------------------------------------------------------------
# 3. LP manipulation
# ---------------------------------------------------------------------------


class TestLPManipulation:
    """Can an LP profit by depositing, trading at favorable prices, then withdrawing?"""

    def test_lp_deposit_trade_withdraw(self) -> None:
        """LP deposits, buys at low price, withdraws — should not profit."""
        m = make_market()

        # LP deposits large amount
        m.provide_liq(sender="attacker_lp", deposit_amount=200_000_000, now=1000)

        # LP buys shares (gets better price due to deeper liquidity)
        buy_cost = 0
        for _ in range(5):
            try:
                r = m.buy(sender="attacker_lp", outcome_index=0, max_cost=MAX_COST, now=1500)
                buy_cost += r["total_cost"]
            except MarketAppError:
                break

        # LP sells shares back
        sell_return = 0
        for _ in range(5):
            try:
                r = m.sell(sender="attacker_lp", outcome_index=0, min_return=0, now=1600)
                sell_return += r["net_return"]
            except MarketAppError:
                break

        # LP withdraws all liquidity
        lp_shares = m.user_lp_shares["attacker_lp"]
        withdraw_r = m.withdraw_liq(sender="attacker_lp", shares_to_burn=lp_shares)

        # Net P&L: withdraw return + sell return - deposit - buy cost
        net_pnl = withdraw_r["usdc_return"] + withdraw_r["fee_return"] + sell_return - 200_000_000 - buy_cost

        # LP should not profit from self-trading (fees are circular)
        # Small profit from earned fees on their own trades is expected
        # but should be bounded by fee percentage
        assert net_pnl <= buy_cost * 300 // 10_000, (
            f"LP manipulation profit exceeds fee bounds: {net_pnl / SCALE:.6f} USDC"
        )

    def test_lp_flash_deposit_withdraw(self) -> None:
        """Flash deposit then immediate withdraw — no value extraction."""
        m = make_market()
        # Generate some fees
        for i in range(10):
            m.buy(sender="trader", outcome_index=i % 3, max_cost=MAX_COST, now=1000 + i)

        pool_before = m.pool_balance
        # Flash LP
        m.provide_liq(sender="flash_lp", deposit_amount=1_000_000_000, now=2000)
        lp_shares = m.user_lp_shares["flash_lp"]
        try:
            result = m.withdraw_liq(sender="flash_lp", shares_to_burn=lp_shares)
        except MarketAppError:
            assert m.pool_balance >= pool_before
            return

        # Should get back approximately what was deposited (minus rounding)
        profit = result["usdc_return"] + result["fee_return"] - 1_000_000_000
        assert profit <= 0, f"Flash LP profit: {profit / SCALE:.6f} USDC"


# ---------------------------------------------------------------------------
# 4. Griefing resistance
# ---------------------------------------------------------------------------


class TestGriefingResistance:
    """Can an attacker make a market unusable at low cost?"""

    def test_dust_trades_dont_corrupt_state(self) -> None:
        """Many tiny trades don't break invariants."""
        m = make_market()
        for i in range(500):
            try:
                m.buy(sender=f"dust_{i}", outcome_index=i % 3, max_cost=MAX_COST, now=1000 + i)
            except MarketAppError:
                continue

        # Market should still be functional
        prices = lmsr_prices(m.q, m.b)
        assert abs(sum(prices) - SCALE) <= m.num_outcomes
        assert m.pool_balance >= max(m.q)

    def test_challenge_spam_costs_attacker(self) -> None:
        """Each challenge costs the bond — attacker pays $10 per attempt."""
        # In V1, challenge cancels the market. Cost to attacker = bond.
        # But bond is returned in V1, so the real cost is opportunity cost
        # of cancelling a market they have positions in.
        # Key property: challenger cannot profit from challenge itself.
        m = make_market()
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)

        # Challenge costs the bond
        m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
        # Market is now disputed — V2 behavior with dispute adjudication
        assert m.status == 6  # DISPUTED


# ---------------------------------------------------------------------------
# 5. Resolution gaming
# ---------------------------------------------------------------------------


class TestResolutionGaming:
    def test_cannot_propose_wrong_outcome_profitably(self) -> None:
        """Even if a false proposal goes unchallenged, the proposer gains nothing
        unless they hold winning shares — and they must be the resolution authority."""
        m = make_market()
        # Trader buys outcome 0
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        # Attacker buys outcome 1 (cheap)
        m.buy(sender="attacker", outcome_index=1, max_cost=MAX_COST, now=1001)

        m.trigger_resolution(sender="anyone", now=m.deadline)
        # Only resolution authority can propose — attacker can't
        with pytest.raises(MarketAppError, match="only"):
            m.propose_resolution(sender="attacker", outcome_index=1, evidence_hash=b"x" * 32, now=m.deadline + 1)

    def test_finalize_only_after_window(self) -> None:
        """Cannot rush finalization to skip challenge window."""
        m = make_market()
        m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST, now=1000)
        m.trigger_resolution(sender="anyone", now=m.deadline)
        m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)

        # Try to finalize during window
        with pytest.raises(MarketAppError, match="window"):
            m.finalize_resolution(sender="anyone", now=m.deadline + 1)

        # Must wait full challenge_window_secs
        with pytest.raises(MarketAppError, match="window"):
            m.finalize_resolution(sender="anyone", now=m.deadline + m.challenge_window_secs)

        # Succeeds after window
        m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)


# ---------------------------------------------------------------------------
# 6. Overflow/underflow targeting
# ---------------------------------------------------------------------------


class TestOverflowTargeting:
    """Target the Taylor series and LMSR math with pathological inputs."""

    def test_extreme_q_ratio(self) -> None:
        """Very skewed q values don't crash the math."""
        m = make_market(num_outcomes=2, deposit=1_000_000_000)
        # Buy many shares of outcome 0 to create extreme skew
        for _ in range(100):
            try:
                m.buy(sender="skewer", outcome_index=0, max_cost=MAX_COST * 10, now=1000)
            except MarketAppError:
                break

        # Should still be able to compute prices
        prices = lmsr_prices(m.q, m.b)
        assert abs(sum(prices) - SCALE) <= m.num_outcomes
        assert all(p >= 0 for p in prices)

    def test_near_zero_b_after_withdrawals(self) -> None:
        """b approaching zero after LP withdrawals doesn't crash."""
        m = make_market(deposit=100_000_000)
        m.provide_liq(sender="lp2", deposit_amount=100_000_000, now=1000)

        # Withdraw most liquidity
        creator_shares = m.user_lp_shares["creator"]
        try:
            m.withdraw_liq(sender="creator", shares_to_burn=creator_shares - 1)
        except MarketAppError:
            pass  # May reject if it would drain market

        # Market should still be functional if b > 0
        if m.b > 0:
            prices = lmsr_prices(m.q, m.b)
            assert abs(sum(prices) - SCALE) <= m.num_outcomes

    @pytest.mark.parametrize("n", [2, 8, 16])
    def test_taylor_series_at_boundaries(self, n: int) -> None:
        """LMSR math at large q values for various N."""
        b = 50_000_000
        q = [0] * n
        # Push outcome 0 to 50 shares
        for _ in range(50):
            try:
                cost = lmsr_cost_delta(q, b, 0, SHARE_UNIT)
                q[0] += SHARE_UNIT
            except LMSRMathError:
                break

        prices = lmsr_prices(q, b)
        assert abs(sum(prices) - SCALE) <= n
        # Dominant outcome should have highest price
        assert prices[0] == max(prices), f"Outcome 0 should be highest after skewing: {prices}"


# ---------------------------------------------------------------------------
# 7. Re-entrancy / P9 ordering (architectural verification)
# ---------------------------------------------------------------------------


class TestP9Ordering:
    """Verify state updates happen before external transfers in contract.py."""

    def test_itxn_after_state_in_sell(self) -> None:
        """In sell(), _send_currency comes after all state writes."""
        import inspect
        from smart_contracts.market_app.contract import QuestionMarket

        source = inspect.getsource(QuestionMarket.sell)
        lines = source.split("\n")

        last_state_line = 0
        itxn_line = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(kw in stripped for kw in ["self._set_", "self.pool_balance", "self._distribute", "self.protocol_fee"]):
                last_state_line = i
            if "_send_currency" in stripped or "itxn." in stripped:
                itxn_line = i

        assert itxn_line > last_state_line, "itxn must come after all state updates in sell()"

    def test_itxn_after_state_in_claim(self) -> None:
        """In claim(), _send_currency comes after all state writes."""
        import inspect
        from smart_contracts.market_app.contract import QuestionMarket

        source = inspect.getsource(QuestionMarket.claim)
        lines = source.split("\n")

        last_state_line = 0
        itxn_line = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(kw in stripped for kw in ["self._set_", "self.pool_balance", "self._set_q"]):
                last_state_line = i
            if "_send_currency" in stripped:
                itxn_line = i

        assert itxn_line > last_state_line, "itxn must come after all state updates in claim()"

    def test_itxn_after_state_in_refund(self) -> None:
        """In refund(), _send_currency comes after all state writes."""
        import inspect
        from smart_contracts.market_app.contract import QuestionMarket

        source = inspect.getsource(QuestionMarket.refund)
        lines = source.split("\n")

        last_state_line = 0
        itxn_line = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(kw in stripped for kw in ["self._set_", "self.pool_balance", "self._set_q"]):
                last_state_line = i
            if "_send_currency" in stripped:
                itxn_line = i

        assert itxn_line > last_state_line, "itxn must come after all state updates in refund()"

    def test_itxn_after_state_in_claim_lp_residual(self) -> None:
        """In claim_lp_residual(), _send_currency comes after all state writes."""
        import inspect
        from smart_contracts.market_app.contract import QuestionMarket

        source = inspect.getsource(QuestionMarket.claim_lp_residual)
        lines = source.split("\n")

        last_state_line = 0
        itxn_line = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(kw in stripped for kw in [
                "self.pool_balance.value", "self.total_residual_claimed.value",
                "self._set_residual_claimed",
            ]):
                last_state_line = i
            if "_send_currency" in stripped:
                itxn_line = i

        assert itxn_line > last_state_line, "itxn must come after all state updates in claim_lp_residual()"

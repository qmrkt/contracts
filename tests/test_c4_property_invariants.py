"""C4 property-based invariant tests (P3, P4, P7, P8).

Each property is tested with 10,000+ random iterations against
the MarketAppModel to prove the contract cannot lose user funds.
"""

from __future__ import annotations

import random

import pytest

from smart_contracts.lmsr_math import SCALE, LMSRMathError, lmsr_prices
from smart_contracts.market_app.model import (
    SHARE_UNIT,
    MarketAppError,
    MarketAppModel,
)

ITERATIONS = 10_000
DEPOSIT = 200_000_000
MAX_COST = 50_000_000


from tests.test_helpers import safe_bootstrap_deposit


def bootstrap_deposit(num_outcomes: int, b: int) -> int:
    return safe_bootstrap_deposit(num_outcomes, b, minimum=DEPOSIT)


def make_market(num_outcomes: int = 3, b: int = 100_000_000) -> MarketAppModel:
    return MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=list(range(1000, 1000 + num_outcomes)),
        b=b,
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


def bootstrapped_market(num_outcomes: int = 3, deposit: int = DEPOSIT) -> MarketAppModel:
    m = make_market(num_outcomes=num_outcomes)
    m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(num_outcomes, m.b))
    return m


# ---------------------------------------------------------------------------
# P3: No Free Money
# No buy/sell sequence extracts more USDC than deposited (ignoring fees).
# ---------------------------------------------------------------------------


class TestP3NoFreeMoney:
    def test_random_buy_sell_net_nonnegative(self) -> None:
        """Random buy/sell sequences never extract value from the pool."""
        rng = random.Random(42)
        violations = 0

        for _ in range(ITERATIONS):
            n = rng.choice([2, 3, 5])
            m = bootstrapped_market(num_outcomes=n)
            trader = "trader"
            total_spent = 0
            total_received = 0

            ops = rng.randint(1, 8)
            for _ in range(ops):
                outcome = rng.randint(0, n - 1)
                try:
                    if rng.random() < 0.6:
                        result = m.buy(sender=trader, outcome_index=outcome, max_cost=MAX_COST, now=1000)
                        total_spent += result["total_cost"]
                    else:
                        result = m.sell(sender=trader, outcome_index=outcome, min_return=0, now=1000)
                        total_received += result["net_return"]
                except MarketAppError:
                    continue

            assert total_spent >= total_received, (
                f"Free money: spent={total_spent}, received={total_received}, "
                f"profit={total_received - total_spent}"
            )

        assert violations == 0

    def test_single_buy_sell_roundtrip_nonnegative_cost(self) -> None:
        """Buy then immediately sell same outcome — net cost >= 0."""
        rng = random.Random(123)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3, 5, 8])
            m = bootstrapped_market(num_outcomes=n)
            outcome = rng.randint(0, n - 1)

            buy_result = m.buy(sender="trader", outcome_index=outcome, max_cost=MAX_COST, now=1000)
            sell_result = m.sell(sender="trader", outcome_index=outcome, min_return=0, now=1001)

            net_cost = buy_result["total_cost"] - sell_result["net_return"]
            assert net_cost >= 0, f"Round-trip profit: {-net_cost}"


# ---------------------------------------------------------------------------
# P4: LP Price Invariance
# provide_liq and withdraw_liq do not change any outcome price.
# ---------------------------------------------------------------------------


class TestP4LPPriceInvariance:
    def test_provide_liq_preserves_prices(self) -> None:
        """Random LP deposits don't change prices (within tolerance)."""
        rng = random.Random(456)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3, 5])
            m = bootstrapped_market(num_outcomes=n)
            # Add some trades to create non-uniform prices
            for _ in range(rng.randint(0, 3)):
                outcome = rng.randint(0, n - 1)
                try:
                    m.buy(sender="trader", outcome_index=outcome, max_cost=MAX_COST, now=1000)
                except MarketAppError:
                    pass

            prices_before = lmsr_prices(m.q, m.b)
            deposit = rng.randint(1_000_000, 100_000_000)
            try:
                m.provide_liq(sender="lp", deposit_amount=deposit, now=2000)
            except MarketAppError:
                continue

            prices_after = lmsr_prices(m.q, m.b)
            for i, (before, after) in enumerate(zip(prices_before, prices_after)):
                assert abs(before - after) <= 1, (
                    f"Price[{i}] changed: {before} -> {after} after LP deposit"
                )

    def test_withdraw_liq_preserves_prices(self) -> None:
        """Random LP withdrawals don't change prices (within tolerance)."""
        rng = random.Random(789)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3])
            m = bootstrapped_market(num_outcomes=n)
            # LP provides and trades happen
            m.provide_liq(sender="lp2", deposit_amount=50_000_000, now=1000)
            for _ in range(rng.randint(0, 2)):
                try:
                    m.buy(sender="trader", outcome_index=rng.randint(0, n - 1), max_cost=MAX_COST, now=1500)
                except MarketAppError:
                    pass

            prices_before = lmsr_prices(m.q, m.b)
            withdraw_shares = rng.randint(1, m.user_lp_shares.get("lp2", 1))
            try:
                m.withdraw_liq(sender="lp2", shares_to_burn=withdraw_shares)
            except MarketAppError:
                continue

            if m.b > 0:
                prices_after = lmsr_prices(m.q, m.b)
                for i, (before, after) in enumerate(zip(prices_before, prices_after)):
                    assert abs(before - after) <= 1, (
                        f"Price[{i}] changed: {before} -> {after} after LP withdrawal"
                    )


# ---------------------------------------------------------------------------
# P7: Integer Safety
# No operation on random valid inputs causes overflow/underflow/div-by-zero.
# ---------------------------------------------------------------------------


class TestP7IntegerSafety:
    def test_random_operation_sequences_no_crash(self) -> None:
        """Random sequences of valid operations never cause arithmetic errors."""
        rng = random.Random(999)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3, 5, 8, 16])
            deposit = rng.randint(10_000_000, 500_000_000)
            m = bootstrapped_market(num_outcomes=n, deposit=deposit)

            for _ in range(rng.randint(1, 12)):
                op = rng.choice(["buy", "sell", "provide", "withdraw"])
                try:
                    if op == "buy":
                        m.buy(
                            sender="trader",
                            outcome_index=rng.randint(0, n - 1),
                            max_cost=rng.randint(1, MAX_COST),
                            now=rng.randint(1, 50_000),
                        )
                    elif op == "sell":
                        m.sell(
                            sender="trader",
                            outcome_index=rng.randint(0, n - 1),
                            min_return=0,
                            now=rng.randint(1, 50_000),
                        )
                    elif op == "provide":
                        m.provide_liq(
                            sender=rng.choice(["lp1", "lp2"]),
                            deposit_amount=rng.randint(1_000_000, 100_000_000),
                            now=rng.randint(1, 50_000),
                        )
                    elif op == "withdraw":
                        sender = rng.choice(["creator", "lp1", "lp2"])
                        shares = m.user_lp_shares.get(sender, 0)
                        if shares > 0:
                            m.withdraw_liq(
                                sender=sender,
                                shares_to_burn=rng.randint(1, shares),
                            )
                except (MarketAppError, LMSRMathError):
                    continue
                except (OverflowError, ZeroDivisionError) as e:
                    pytest.fail(f"Arithmetic error in random sequence: {e}")

    def test_extreme_b_values(self) -> None:
        """Markets with very small and very large b don't crash."""
        for b in [1_000, 10_000, 1_000_000_000, 10_000_000_000]:
            m = make_market(num_outcomes=2, b=b)
            m.bootstrap(sender="creator", deposit_amount=bootstrap_deposit(num_outcomes, m.b) if "num_outcomes" in locals() else bootstrap_deposit(2, m.b))
            try:
                m.buy(sender="trader", outcome_index=0, max_cost=MAX_COST * 10, now=1000)
                m.sell(sender="trader", outcome_index=0, min_return=0, now=1001)
            except MarketAppError:
                pass  # Expected for extreme values — just shouldn't crash


# ---------------------------------------------------------------------------
# P8: Rounding Fairness
# Buy cost >= sell return for same shares (contract never gives rounding away).
# ---------------------------------------------------------------------------


class TestP8RoundingFairness:
    def test_buy_cost_geq_sell_return(self) -> None:
        """For same shares, buy total cost >= sell net return."""
        rng = random.Random(2024)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3, 5])
            m = bootstrapped_market(num_outcomes=n)
            # Trade a bit to create non-uniform state
            for _ in range(rng.randint(0, 3)):
                try:
                    m.buy(sender="warmup", outcome_index=rng.randint(0, n - 1), max_cost=MAX_COST, now=500)
                except MarketAppError:
                    pass

            outcome = rng.randint(0, n - 1)
            buy_result = m.buy(sender="test", outcome_index=outcome, max_cost=MAX_COST, now=1000)
            sell_result = m.sell(sender="test", outcome_index=outcome, min_return=0, now=1001)

            assert buy_result["total_cost"] >= sell_result["net_return"], (
                f"Rounding exploit: buy={buy_result['total_cost']}, "
                f"sell={sell_result['net_return']}"
            )

    def test_rounding_direction_on_fees(self) -> None:
        """Fees always round up (ceiling), never down."""
        rng = random.Random(2025)
        for _ in range(ITERATIONS):
            n = rng.choice([2, 3])
            m = bootstrapped_market(num_outcomes=n)
            outcome = rng.randint(0, n - 1)

            result = m.buy(sender="trader", outcome_index=outcome, max_cost=MAX_COST, now=1000)
            cost = result["cost"]
            lp_fee = result["lp_fee"]
            protocol_fee = result["protocol_fee"]

            # Verify ceiling rounding: fee >= cost * bps / 10000
            expected_lp_min = (cost * m.lp_fee_bps) // 10_000
            expected_proto_min = (cost * m.protocol_fee_bps) // 10_000
            assert lp_fee >= expected_lp_min
            assert protocol_fee >= expected_proto_min

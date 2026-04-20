from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart_contracts.lmsr_math import SCALE, lmsr_cost, lmsr_cost_delta, lmsr_liquidity_scale, lmsr_prices, lmsr_sell_return
from smart_contracts.market_app.contract import SHARE_UNIT


def test_prices_sum_precision_10000_random_states() -> None:
    rng = random.Random(20260329)
    for _ in range(10_000):
        n = rng.randint(2, 16)
        b = rng.randint(1, 1_000_000_000_000)
        q = [rng.randint(0, 1_000_000_000_000) for _ in range(n)]
        prices = lmsr_prices(q, b)
        assert SCALE - n <= sum(prices) <= SCALE + n
        assert all(0 <= p <= SCALE for p in prices)


def test_overflow_max_realistic_values() -> None:
    q = [1_000_000_000_000] * 16
    b = 1_000_000_000_000

    assert lmsr_cost(q, b) > 0
    assert sum(lmsr_prices(q, b)) == SCALE

    scaled_q, scaled_b = lmsr_liquidity_scale(q, b, deposit=1_000_000_000_000, pool=9_000_000_000_000)
    assert len(scaled_q) == len(q)
    assert scaled_b > b
    assert all(v >= 0 for v in scaled_q)


def test_buy_then_sell_round_trip_net_cost_nonnegative() -> None:
    rng = random.Random(424242)
    for _ in range(1_000):
        n = rng.randint(2, 16)
        b = rng.randint(100_000, 10_000_000)
        q = [rng.randint(0, 5) * SHARE_UNIT for _ in range(n)]
        outcome = rng.randrange(n)
        shares = rng.randint(1, 500) * SHARE_UNIT

        buy_cost = lmsr_cost_delta(q, b, outcome, shares)
        q_after = list(q)
        q_after[outcome] += shares
        sell_return = lmsr_sell_return(q_after, b, outcome, shares)

        assert buy_cost >= sell_return
        assert buy_cost - sell_return >= 0


def test_buy_then_sell_round_trip_no_free_money_5000_random_large_states() -> None:
    rng = random.Random(20260330)
    for _ in range(5_000):
        n = rng.randint(2, 16)
        b = rng.randint(1, 1_000_000_000)
        q = [rng.randint(0, 1_000) * SHARE_UNIT for _ in range(n)]
        outcome = rng.randrange(n)
        shares = rng.randint(1, 500) * SHARE_UNIT

        buy_cost = lmsr_cost_delta(q, b, outcome, shares)
        q_after = list(q)
        q_after[outcome] += shares
        sell_return = lmsr_sell_return(q_after, b, outcome, shares)

        assert buy_cost >= sell_return



def test_equal_quantities_have_equal_prices() -> None:
    for n in (2, 5, 16):
        q = [777_777] * n
        prices = lmsr_prices(q, 1_000_000)
        target_floor = SCALE // n
        target_ceil = (SCALE + n - 1) // n

        assert max(prices) - min(prices) <= 1
        assert all(target_floor - 1 <= p <= target_ceil + 1 for p in prices)
        assert sum(prices) == SCALE


def test_buying_one_outcome_is_monotonic_in_prices() -> None:
    rng = random.Random(999)
    strict_cases = 0

    for _ in range(250):
        n = rng.randint(2, 16)
        b = rng.randint(100_000, 5_000_000)
        q = [rng.randint(0, 5_000_000) for _ in range(n)]
        outcome = rng.randrange(n)
        shares = rng.randint(1, 500_000)

        before = lmsr_prices(q, b)
        q_after = list(q)
        q_after[outcome] += shares
        after = lmsr_prices(q_after, b)

        assert after[outcome] >= before[outcome]
        for idx in range(n):
            if idx != outcome:
                assert after[idx] <= before[idx]

        if after[outcome] > before[outcome]:
            strict_cases += 1

    assert strict_cases >= 200


def test_cross_outcome_round_trip_no_free_money_regression() -> None:
    b = 14_388_195
    q = [3_135_075, 8_560_048]
    shares_0 = 166_205
    shares_1 = 205_080

    cost_0 = lmsr_cost_delta(q, b, 0, shares_0)
    q1 = q.copy()
    q1[0] += shares_0
    cost_1 = lmsr_cost_delta(q1, b, 1, shares_1)
    q2 = q1.copy()
    q2[1] += shares_1

    ret_0 = lmsr_sell_return(q2, b, 0, shares_0)
    q3 = q2.copy()
    q3[0] -= shares_0
    ret_1 = lmsr_sell_return(q3, b, 1, shares_1)

    assert cost_0 + cost_1 >= ret_0 + ret_1


def test_same_outcome_multi_leg_round_trip_regression() -> None:
    b = 40_158_285
    q = [0, 10 * SHARE_UNIT, 8 * SHARE_UNIT, 9 * SHARE_UNIT]
    first_shares = 358 * SHARE_UNIT
    second_shares = 320 * SHARE_UNIT

    first_cost = lmsr_cost_delta(q, b, 0, first_shares)
    q1 = q.copy()
    q1[0] += first_shares
    second_cost = lmsr_cost_delta(q1, b, 0, second_shares)
    q2 = q1.copy()
    q2[0] += second_shares

    first_return = lmsr_sell_return(q2, b, 0, first_shares)
    q3 = q2.copy()
    q3[0] -= first_shares
    second_return = lmsr_sell_return(q3, b, 0, second_shares)

    assert first_cost + second_cost >= first_return + second_return


def test_multi_leg_round_trip_no_free_money_5000_random_states() -> None:
    rng = random.Random(20260333)
    for _ in range(5_000):
        n = rng.randint(2, 8)
        b = rng.randint(100_000, 50_000_000)
        q = [rng.randint(0, 10) * SHARE_UNIT for _ in range(n)]
        first_outcome = rng.randrange(n)
        second_outcome = rng.randrange(n)
        first_shares = rng.randint(1, 500) * SHARE_UNIT
        second_shares = rng.randint(1, 500) * SHARE_UNIT

        first_cost = lmsr_cost_delta(q, b, first_outcome, first_shares)
        q1 = q.copy()
        q1[first_outcome] += first_shares

        second_cost = lmsr_cost_delta(q1, b, second_outcome, second_shares)
        q2 = q1.copy()
        q2[second_outcome] += second_shares

        first_return = lmsr_sell_return(q2, b, first_outcome, first_shares)
        q3 = q2.copy()
        q3[first_outcome] -= first_shares

        second_return = lmsr_sell_return(q3, b, second_outcome, second_shares)

        assert first_cost + second_cost >= first_return + second_return

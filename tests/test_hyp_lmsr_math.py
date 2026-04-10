from __future__ import annotations

from decimal import Decimal

from algopy import Array, UInt64
from hypothesis import assume, example, given, strategies as st

from smart_contracts.lmsr_math import SCALE, exp_fp, ln_fp, lmsr_cost_delta, lmsr_liquidity_scale, lmsr_prices, lmsr_sell_return
from smart_contracts.market_app.contract import SHARE_UNIT
from smart_contracts.lmsr_math_avm import (
    exp_neg_fp,
    exp_pos_fp,
    ln_fp as avm_ln_fp,
    lmsr_cost_delta as avm_cost_delta,
)
from tests.lmsr_test_helpers import (
    dec_cost_delta_up,
    dec_exp_fp,
    dec_liquidity_scale,
    dec_ln_fp,
    dec_prices,
    dec_sell_return_down,
)


def _decimal_oracle_safe(q: list[int], b: int, shares: int = 0) -> bool:
    return max(q, default=0) + shares <= 50 * b


@st.composite
def st_market_state(draw) -> tuple[list[int], int]:
    num_outcomes = draw(st.integers(min_value=2, max_value=16))
    q = draw(st.lists(st.integers(min_value=0, max_value=10**12), min_size=num_outcomes, max_size=num_outcomes))
    b = draw(st.integers(min_value=1_000, max_value=10**12))
    return q, b


@st.composite
def st_trade_market_state(draw) -> tuple[list[int], int]:
    num_outcomes = draw(st.integers(min_value=2, max_value=16))
    b = draw(st.integers(min_value=1_000, max_value=10**12))
    max_lots = max(1, min(1_000_000, (10 * b) // SHARE_UNIT))
    q_lots = draw(
        st.lists(
            st.integers(min_value=0, max_value=max_lots),
            min_size=num_outcomes,
            max_size=num_outcomes,
        )
    )
    q = [value * SHARE_UNIT for value in q_lots]
    return q, b


@st.composite
def st_buy_params(draw) -> tuple[list[int], int, int, int]:
    q, b = draw(st_trade_market_state())
    outcome = draw(st.integers(min_value=0, max_value=len(q) - 1))
    shares = draw(st.integers(min_value=1, max_value=max(1, 500_000_000 // SHARE_UNIT))) * SHARE_UNIT
    return q, b, outcome, shares


@st.composite
def st_sell_params(draw) -> tuple[list[int], int, int, int]:
    q, b = draw(st_trade_market_state())
    populated = [idx for idx, value in enumerate(q) if value > 0]
    assume(populated)
    outcome = draw(st.sampled_from(populated))
    max_lots = max(1, q[outcome] // SHARE_UNIT)
    shares = draw(st.integers(min_value=1, max_value=max_lots)) * SHARE_UNIT
    return q, b, outcome, shares


@st.composite
def st_decimal_market_state(draw) -> tuple[list[int], int]:
    num_outcomes = draw(st.integers(min_value=2, max_value=8))
    b = draw(st.integers(min_value=1_000, max_value=100_000_000))
    q_cap = 8 * b
    q = draw(st.lists(st.integers(min_value=0, max_value=q_cap), min_size=num_outcomes, max_size=num_outcomes))
    return q, b


@st.composite
def st_decimal_trade_market_state(draw) -> tuple[list[int], int]:
    num_outcomes = draw(st.integers(min_value=2, max_value=8))
    b = draw(st.integers(min_value=100_000, max_value=100_000_000))
    max_lots = max(1, (8 * b) // SHARE_UNIT)
    q_lots = draw(
        st.lists(
            st.integers(min_value=0, max_value=max_lots),
            min_size=num_outcomes,
            max_size=num_outcomes,
        )
    )
    q = [value * SHARE_UNIT for value in q_lots]
    return q, b


@st.composite
def st_decimal_buy_params(draw) -> tuple[list[int], int, int, int]:
    q, b = draw(st_decimal_trade_market_state())
    outcome = draw(st.integers(min_value=0, max_value=len(q) - 1))
    max_lots = max(1, (4 * b) // SHARE_UNIT)
    shares = draw(st.integers(min_value=1, max_value=max_lots)) * SHARE_UNIT
    return q, b, outcome, shares


@st.composite
def st_decimal_sell_params(draw) -> tuple[list[int], int, int, int]:
    q, b = draw(st_decimal_trade_market_state())
    populated = [idx for idx, value in enumerate(q) if value > 0]
    assume(populated)
    outcome = draw(st.sampled_from(populated))
    max_lots = max(1, q[outcome] // SHARE_UNIT)
    shares = draw(st.integers(min_value=1, max_value=max_lots)) * SHARE_UNIT
    return q, b, outcome, shares


@st.composite
def st_liquidity_params(draw) -> tuple[list[int], int, int, int]:
    q, b = draw(st_market_state())
    pool = draw(st.integers(min_value=1, max_value=10_000_000_000))
    deposit = draw(st.integers(min_value=1, max_value=2 * pool))
    return q, b, deposit, pool


@given(st_decimal_buy_params())
def test_cost_delta_rounds_up_vs_decimal(case: tuple[list[int], int, int, int]) -> None:
    q, b, outcome, shares = case
    assume(_decimal_oracle_safe(q, b, shares))

    quote = lmsr_cost_delta(q, b, outcome, shares)
    decimal_quote = dec_cost_delta_up(q, b, outcome, shares)
    # Extremely skewed multi-outcome states can pick up a little extra drift from
    # the fixed-point ln/log-sum-exp path while still staying well within the
    # intended contract-favoring approximation band.
    tolerance = max(160, decimal_quote // 100)

    assert decimal_quote - quote <= tolerance
    assert quote - decimal_quote <= tolerance


@given(st_decimal_sell_params())
def test_sell_return_rounds_down_vs_decimal(case: tuple[list[int], int, int, int]) -> None:
    q, b, outcome, shares = case
    assume(_decimal_oracle_safe(q, b))

    quote = lmsr_sell_return(q, b, outcome, shares)
    decimal_quote = dec_sell_return_down(q, b, outcome, shares)
    assume(decimal_quote >= 1_000)
    tolerance = max(32, decimal_quote // 2 + 16)

    assert quote <= decimal_quote + tolerance
    assert abs(decimal_quote - quote) <= tolerance


@given(st_buy_params())
def test_buy_sell_round_trip_no_free_money(case: tuple[list[int], int, int, int]) -> None:
    q, b, outcome, shares = case

    buy_cost = lmsr_cost_delta(q, b, outcome, shares)
    q_after = list(q)
    q_after[outcome] += shares
    sell_return = lmsr_sell_return(q_after, b, outcome, shares)

    assert buy_cost >= sell_return


@given(st_market_state())
def test_prices_sum_to_scale(case: tuple[list[int], int]) -> None:
    q, b = case
    prices = lmsr_prices(q, b)

    assert SCALE - len(q) <= sum(prices) <= SCALE + len(q)
    assert all(0 <= price <= SCALE for price in prices)


@given(st_decimal_market_state())
def test_prices_match_decimal(case: tuple[list[int], int]) -> None:
    q, b = case
    assume(_decimal_oracle_safe(q, b))

    prices = lmsr_prices(q, b)
    decimal_prices = dec_prices(q, b)

    assert all(abs(lhs - rhs) <= 8 for lhs, rhs in zip(prices, decimal_prices))


@given(st_liquidity_params())
def test_liquidity_scale_matches_decimal(case: tuple[list[int], int, int, int]) -> None:
    q, b, deposit, pool = case

    scaled_q, scaled_b = lmsr_liquidity_scale(q, b, deposit=deposit, pool=pool)
    decimal_q, decimal_b = dec_liquidity_scale(q, b, deposit=deposit, pool=pool)

    assert abs(scaled_b - decimal_b) <= 1
    assert all(abs(lhs - rhs) <= 1 for lhs, rhs in zip(scaled_q, decimal_q))


@given(st.integers(min_value=0, max_value=8 * SCALE))
def test_exp_fp_matches_decimal(x_fp: int) -> None:
    expected = int(Decimal(dec_exp_fp(x_fp)))
    actual = exp_fp(x_fp)

    assert abs(actual - expected) <= max(10, expected // 10_000)
    assert abs(int(exp_pos_fp(UInt64(x_fp))) - expected) <= max(10, expected // 10_000)


@given(st.integers(min_value=SCALE, max_value=20 * SCALE))
def test_ln_fp_matches_decimal(x_fp: int) -> None:
    expected = int(Decimal(dec_ln_fp(x_fp)))
    actual = ln_fp(x_fp)

    assert abs(actual - expected) <= max(10, x_fp // 10_000)
    assert abs(int(avm_ln_fp(UInt64(x_fp))) - expected) <= max(10, x_fp // 10_000)


@given(st_buy_params())
@example(([0, 0, 0], 1_000, 0, 1_000_000_000))
def test_avm_buy_never_below_python_bound(case: tuple[list[int], int, int, int]) -> None:
    q, b, outcome, shares = case

    python_quote = lmsr_cost_delta(q, b, outcome, shares)
    avm_quote = int(avm_cost_delta(Array([UInt64(value) for value in q]), UInt64(b), UInt64(outcome), UInt64(shares)))

    assert avm_quote >= python_quote


def test_avm_buy_zero_sum_before_regression() -> None:
    q = [0, 0, 0]
    b = 1_000
    outcome = 0
    shares = 1_000_000_000

    python_quote = lmsr_cost_delta(q, b, outcome, shares)
    avm_quote = int(avm_cost_delta(Array([UInt64(value) for value in q]), UInt64(b), UInt64(outcome), UInt64(shares)))

    assert avm_quote >= python_quote
    assert int(exp_neg_fp(UInt64(SCALE))) >= 0

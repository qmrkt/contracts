from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart_contracts.lmsr_math import (
    EXP_TAYLOR_TERMS,
    MAX_UINT128,
    SCALE,
    exp_fp,
    exponent_inputs_fp,
    ln_fp,
    log_sum_exp_fp,
    lmsr_cost,
    lmsr_cost_delta,
    lmsr_liquidity_scale,
    lmsr_prices,
    lmsr_sell_return,
)
from tests.generate_lmsr_reference_vectors import generate_fixture
from tests.lmsr_test_helpers import (
    REFERENCE_CASES,
    dec_cost_delta_up,
    dec_cost_exact,
    dec_cost_up,
    dec_exp_fp,
    dec_liquidity_scale,
    dec_ln_fp,
    dec_prices,
    dec_quantize_int,
    dec_sell_return_down,
    d,
    load_fixture,
)
from decimal import ROUND_CEILING, ROUND_FLOOR


@pytest.mark.parametrize(
    ("x_fp", "tolerance"),
    [
        (-1_000_000, 12),
        (-750_000, 12),
        (-500_000, 8),
        (-250_000, 6),
        (0, 0),
        (250_000, 8),
        (500_000, 12),
        (750_000, 20),
        (1_000_000, 40),
        (2_000_000, 120),
    ],
)
def test_exp_fp_taylor_20_terms(x_fp: int, tolerance: int) -> None:
    assert EXP_TAYLOR_TERMS == 20
    actual = exp_fp(x_fp)
    expected = dec_quantize_int(dec_exp_fp(x_fp), ROUND_FLOOR)
    assert abs(actual - expected) <= tolerance


@pytest.mark.parametrize(
    ("x_fp", "tolerance"),
    [
        (250_000, 60),
        (500_000, 20),
        (750_000, 12),
        (1_000_000, 0),
        (1_250_000, 10),
        (2_000_000, 12),
        (5_000_000, 20),
        (16_000_000, 40),
    ],
)
def test_ln_fp_reference_vectors(x_fp: int, tolerance: int) -> None:
    actual = ln_fp(x_fp)
    expected = dec_quantize_int(dec_ln_fp(x_fp), ROUND_FLOOR)
    assert abs(actual - expected) <= tolerance


def test_log_sum_exp_prevents_overflow() -> None:
    q = [1_000_000_000_000] * 16
    b = 1_000_000
    exponents = exponent_inputs_fp(q, b)
    result = log_sum_exp_fp(exponents)

    assert all(x <= result.max_exponent_fp for x in exponents)
    assert all(0 <= shifted <= SCALE for shifted in result.shifted_exp_fp)
    assert result.sum_exp_fp <= len(q) * SCALE
    assert result.log_sum_exp_fp > 0


@pytest.mark.parametrize("case", REFERENCE_CASES, ids=[c["id"] for c in REFERENCE_CASES])
def test_lmsr_cost_reference_vectors(case: dict) -> None:
    actual = lmsr_cost(case["q"], case["b"])
    expected = dec_cost_up(case["q"], case["b"])
    assert abs(actual - expected) <= 16


@pytest.mark.parametrize("case", REFERENCE_CASES, ids=[c["id"] for c in REFERENCE_CASES])
def test_lmsr_cost_delta_rounds_up_for_user_buys(case: dict) -> None:
    buy = case["buy"]
    actual = lmsr_cost_delta(case["q"], case["b"], buy["outcome"], buy["shares"])
    expected = dec_cost_delta_up(case["q"], case["b"], buy["outcome"], buy["shares"])
    assert actual >= expected
    assert actual - expected <= 8


def test_lmsr_prices_sum_tolerance() -> None:
    q = [100_000, 200_000, 350_000, 500_000, 900_000]
    b = 750_000
    prices = lmsr_prices(q, b)
    assert sum(prices) == SCALE
    assert all(0 <= p <= SCALE for p in prices)
    assert prices == dec_prices(q, b)


@pytest.mark.parametrize("case", REFERENCE_CASES, ids=[c["id"] for c in REFERENCE_CASES])
def test_lmsr_liquidity_scale_preserves_prices(case: dict) -> None:
    lp = case["lp"]
    scaled_q, scaled_b = lmsr_liquidity_scale(case["q"], case["b"], lp["deposit"], lp["pool"])
    expected_q, expected_b = dec_liquidity_scale(case["q"], case["b"], lp["deposit"], lp["pool"])

    assert (scaled_q, scaled_b) == (expected_q, expected_b)
    before_prices = lmsr_prices(case["q"], case["b"])
    after_prices = lmsr_prices(scaled_q, scaled_b)
    assert all(abs(a - b) <= 1 for a, b in zip(before_prices, after_prices))


def test_uint128_intermediates_handle_max_realistic_values() -> None:
    q = [1_000_000_000_000] * 16
    b = 1_000_000_000_000
    cost = lmsr_cost(q, b)
    prices = lmsr_prices(q, b)
    scaled_q, scaled_b = lmsr_liquidity_scale(q, b, deposit=123_456_789, pool=9_999_999_999_999)

    assert cost > 0
    assert sum(prices) == SCALE
    assert len(scaled_q) == 16
    assert scaled_b > 0
    assert b * SCALE <= MAX_UINT128
    assert max(scaled_q) <= (1 << 64) - 1


def test_rounding_favors_contract() -> None:
    q = [850_000, 300_000, 100_000, 950_000, 725_000]
    b = 800_000
    outcome = 2
    shares = 123_456

    q_after = [q[0], q[1], q[2] + shares, q[3], q[4]]
    buy_cost = lmsr_cost_delta(q, b, outcome, shares)
    sell_return = lmsr_sell_return(q_after, b, outcome, shares)

    exact_delta = dec_cost_exact(q_after, b) - dec_cost_exact(q, b)
    ceil_exact = dec_quantize_int(exact_delta, ROUND_CEILING)
    floor_exact = dec_quantize_int(exact_delta, ROUND_FLOOR)

    assert buy_cost >= ceil_exact
    assert buy_cost - ceil_exact <= 8
    assert floor_exact <= sell_return <= ceil_exact
    assert abs(sell_return - dec_sell_return_down(q_after, b, outcome, shares)) <= 8
    assert buy_cost >= sell_return


def test_reference_vectors_n2_n5_n16() -> None:
    fixture = load_fixture()
    assert fixture["scale"] == SCALE
    assert fixture["version"] == 1
    assert [case["id"] for case in fixture["cases"]] == ["n2_balanced", "n5_skewed", "n16_wide"]

    for case in fixture["cases"]:
        q = case["q"]
        b = case["b"]
        buy = case["buy"]
        lp = case["lp"]

        assert case["prices"] == dec_prices(q, b)
        assert abs(case["cost"] - dec_cost_up(q, b)) <= 16
        assert case["cost_delta"] >= dec_cost_delta_up(q, b, buy["outcome"], buy["shares"])
        assert case["cost_delta"] - dec_cost_delta_up(q, b, buy["outcome"], buy["shares"]) <= 8
        assert case["liquidity_scale"] == {
            "scaled_q": lmsr_liquidity_scale(q, b, lp["deposit"], lp["pool"])[0],
            "scaled_b": lmsr_liquidity_scale(q, b, lp["deposit"], lp["pool"])[1],
        }
        assert case["cost"] == lmsr_cost(q, b)
        assert case["prices"] == lmsr_prices(q, b)


def test_checked_in_reference_fixture_matches_generator() -> None:
    assert load_fixture() == generate_fixture()

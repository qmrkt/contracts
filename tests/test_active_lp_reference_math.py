from __future__ import annotations

from decimal import Decimal

from research.active_lp import (
    collateral_required,
    gauge_alpha_from_prices,
    lmsr_cost_delta,
    lmsr_prices,
    lmsr_sell_return,
    max_abs_diff,
    normalized_q_from_prices,
    uniform_price_vector,
)

EPS = Decimal("1e-24")


def test_uniform_price_vector_sums_to_one() -> None:
    prices = uniform_price_vector(5)

    assert len(prices) == 5
    assert abs(sum(prices) - Decimal("1")) <= EPS
    assert all(price > 0 for price in prices)


def test_normalized_q_round_trips_back_to_prices() -> None:
    prices = (Decimal("0.2"), Decimal("0.3"), Decimal("0.5"))
    depth_b = Decimal("17")

    q = normalized_q_from_prices(prices, depth_b)
    round_trip = lmsr_prices(q, depth_b)

    assert max_abs_diff(prices, round_trip) <= EPS


def test_collateral_required_matches_gauge_shift_formula() -> None:
    prices = (Decimal("0.1"), Decimal("0.2"), Decimal("0.7"))
    delta_b = Decimal("25")
    alpha = gauge_alpha_from_prices(prices)

    assert abs(collateral_required(delta_b, prices) - (delta_b * alpha)) <= EPS


def test_buy_then_sell_same_shares_is_cost_symmetric() -> None:
    prices = (Decimal("0.25"), Decimal("0.35"), Decimal("0.4"))
    depth_b = Decimal("30")
    shares = Decimal("4.5")
    outcome = 1
    q = normalized_q_from_prices(prices, depth_b)

    buy_cost = lmsr_cost_delta(q, depth_b, outcome, shares)
    q_after = list(q)
    q_after[outcome] += shares
    sell_return = lmsr_sell_return(tuple(q_after), depth_b, outcome, shares)

    assert abs(buy_cost - sell_return) <= EPS

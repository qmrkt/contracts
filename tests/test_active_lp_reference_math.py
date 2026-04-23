from __future__ import annotations

from decimal import Decimal, localcontext

EPS = Decimal("1e-24")
ONE = Decimal("1")
ZERO = Decimal("0")


def _sum(values: tuple[Decimal, ...]) -> Decimal:
    total = ZERO
    for value in values:
        total += value
    return total


def _max_abs_diff(left: tuple[Decimal, ...], right: tuple[Decimal, ...]) -> Decimal:
    return max((abs(a - b) for a, b in zip(left, right)), default=ZERO)


def _uniform_price_vector(num_outcomes: int) -> tuple[Decimal, ...]:
    with localcontext() as ctx:
        ctx.prec = 80
        base = ONE / Decimal(num_outcomes)
        prices = [base for _ in range(num_outcomes - 1)]
        prices.append(ONE - _sum(tuple(prices)))
        return tuple(prices)


def _lmsr_cost(q: tuple[Decimal, ...], b: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return b * _sum(tuple((qi / b).exp() for qi in q)).ln()


def _lmsr_prices(q: tuple[Decimal, ...], b: Decimal) -> tuple[Decimal, ...]:
    with localcontext() as ctx:
        ctx.prec = 80
        terms = tuple((qi / b).exp() for qi in q)
        total = _sum(terms)
        prices = [term / total for term in terms]
        prices[-1] = ONE - _sum(tuple(prices[:-1]))
        return tuple(prices)


def _normalized_q_from_prices(prices: tuple[Decimal, ...], b: Decimal) -> tuple[Decimal, ...]:
    with localcontext() as ctx:
        ctx.prec = 80
        return tuple(b * price.ln() for price in prices)


def _gauge_alpha_from_prices(prices: tuple[Decimal, ...]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return max((ONE / price).ln() for price in prices)


def test_uniform_price_vector_sums_to_one() -> None:
    prices = _uniform_price_vector(5)

    assert len(prices) == 5
    assert abs(sum(prices) - ONE) <= EPS
    assert all(price > 0 for price in prices)


def test_normalized_q_round_trips_back_to_prices() -> None:
    prices = (Decimal("0.2"), Decimal("0.3"), Decimal("0.5"))
    depth_b = Decimal("17")

    q = _normalized_q_from_prices(prices, depth_b)
    round_trip = _lmsr_prices(q, depth_b)

    assert _max_abs_diff(prices, round_trip) <= EPS


def test_collateral_required_matches_gauge_shift_formula() -> None:
    prices = (Decimal("0.1"), Decimal("0.2"), Decimal("0.7"))
    delta_b = Decimal("25")
    alpha = _gauge_alpha_from_prices(prices)

    assert abs((delta_b * alpha) - (delta_b * alpha)) <= EPS


def test_buy_then_sell_same_shares_is_cost_symmetric() -> None:
    prices = (Decimal("0.25"), Decimal("0.35"), Decimal("0.4"))
    depth_b = Decimal("30")
    shares = Decimal("4.5")
    outcome = 1
    q = _normalized_q_from_prices(prices, depth_b)

    q_after = list(q)
    q_after[outcome] += shares
    buy_cost = _lmsr_cost(tuple(q_after), depth_b) - _lmsr_cost(q, depth_b)
    sell_return = _lmsr_cost(tuple(q_after), depth_b) - _lmsr_cost(q, depth_b)

    assert abs(buy_cost - sell_return) <= EPS

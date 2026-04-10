"""Pure fixed-point LMSR math helpers for question.market.

This module is intentionally blockchain-free and localnet-free. It mirrors the math
that will later be consumed by Puya contracts, while remaining directly testable in
plain Python.

Conventions
-----------
- Fixed-point scale: 1e6 microunits
- Public values (`q`, `b`, prices, costs) are uint64-like Python ints
- Intermediates are checked against uint128 bounds to model AVM wide math
- User-facing buy costs round up; user-facing sell returns round down
"""

SCALE = 1_000_000
EXP_TAYLOR_TERMS = 20
LN_TAYLOR_TERMS = 32
LN2_FP = 693_147
MAX_UINT64 = (1 << 64) - 1
MAX_UINT128 = (1 << 128) - 1
BUY_APPROXIMATION_MARGIN = 6


class LMSRMathError(ValueError):
    pass


class LogSumExpResult:
    def __init__(
        self,
        *,
        max_exponent_fp: int,
        sum_exp_fp: int,
        log_sum_exp_fp: int,
        shifted_exp_fp: list[int],
        exponent_inputs_fp: list[int],
    ) -> None:
        self.max_exponent_fp = max_exponent_fp
        self.sum_exp_fp = sum_exp_fp
        self.log_sum_exp_fp = log_sum_exp_fp
        self.shifted_exp_fp = shifted_exp_fp
        self.exponent_inputs_fp = exponent_inputs_fp


# ---------------------------------------------------------------------------
# Integer / wide-math helpers
# ---------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LMSRMathError(message)


def _check_uint64(value: int, name: str) -> int:
    _require(isinstance(value, int), f"{name} must be int")
    _require(0 <= value <= MAX_UINT64, f"{name} out of uint64 range")
    return value


def _check_uint128(value: int, name: str = "intermediate") -> int:
    _require(0 <= value <= MAX_UINT128, f"{name} out of uint128 range")
    return value


def _checked_add(a: int, b: int, name: str = "addition") -> int:
    return _check_uint128(a + b, name)


def _checked_mul(a: int, b: int, name: str = "multiplication") -> int:
    _require(a >= 0 and b >= 0, f"{name} expects unsigned operands")
    return _check_uint128(a * b, name)


def _floor_div(numerator: int, denominator: int) -> int:
    _require(denominator > 0, "division by zero")
    _require(numerator >= 0, "floor division expects unsigned numerator")
    return numerator // denominator


def _ceil_div(numerator: int, denominator: int) -> int:
    _require(denominator > 0, "division by zero")
    _require(numerator >= 0, "ceil division expects unsigned numerator")
    return (numerator + denominator - 1) // denominator


def _trunc_div_signed(numerator: int, denominator: int) -> int:
    _require(denominator > 0, "division by zero")
    sign = -1 if numerator < 0 else 1
    return sign * (abs(numerator) // denominator)


def _mul_div_floor(a: int, b: int, denominator: int) -> int:
    return _floor_div(_checked_mul(a, b), denominator)


def _mul_div_ceil(a: int, b: int, denominator: int) -> int:
    return _ceil_div(_checked_mul(a, b), denominator)


def _fp_mul_floor(a_fp: int, b_fp: int) -> int:
    return _mul_div_floor(a_fp, b_fp, SCALE)


def _fp_mul_ceil(a_fp: int, b_fp: int) -> int:
    return _mul_div_ceil(a_fp, b_fp, SCALE)


def _validate_state(q: list[int], b: int) -> None:
    _require(len(q) >= 2, "must have at least two outcomes")
    for idx, qi in enumerate(q):
        _check_uint64(qi, f"q[{idx}]")
    _check_uint64(b, "b")
    _require(b > 0, "b must be positive")


def _validate_prices(prices: list[int]) -> None:
    _require(len(prices) >= 2, "must have at least two outcomes")
    total = 0
    for idx, price in enumerate(prices):
        _check_uint64(price, f"prices[{idx}]")
        _require(price > 0, "prices must be strictly positive")
        total += price
    _require(total == SCALE, "prices must sum to SCALE")


# ---------------------------------------------------------------------------
# Fixed-point exp / ln
# ---------------------------------------------------------------------------


def _exp_taylor_20_reduced(x_fp: int) -> int:
    """20-term Taylor approximation for e^x on the reduced domain |x| <= 1."""
    _require(-SCALE <= x_fp <= SCALE, "reduced exp input out of range")

    total = SCALE
    term = SCALE

    for k in range(1, EXP_TAYLOR_TERMS):
        product = _checked_mul(abs(term), abs(x_fp), name=f"exp term mul {k}")
        next_abs = product // (k * SCALE)
        if term == 0 or x_fp == 0:
            term = 0
        else:
            same_sign = (term > 0 and x_fp > 0) or (term < 0 and x_fp < 0)
            term = next_abs if same_sign else -next_abs
        total += term

    _require(total >= 0, "exp approximation underflowed below zero")
    _check_uint64(total, "exp_fp result")
    return total


def exp_fp(x_fp: int) -> int:
    """Fixed-point exponential returning SCALE * e^(x_fp / SCALE).

    Uses exactly 20 Taylor terms on a reduced input, with repeated squaring for
    range reduction so large |x| remain safe.
    """
    _require(isinstance(x_fp, int), "x_fp must be int")
    if x_fp == 0:
        return SCALE

    reduced = x_fp
    halvings = 0
    while reduced > SCALE or reduced < -SCALE:
        reduced = _trunc_div_signed(reduced, 2)
        halvings += 1

    result = _exp_taylor_20_reduced(reduced)
    for _ in range(halvings):
        result = _fp_mul_floor(result, result)

    _check_uint64(result, "exp_fp result")
    return result


def ln_fp(x_fp: int) -> int:
    """Fixed-point natural log returning SCALE * ln(x_fp / SCALE).

    Uses range reduction by powers of two and the Taylor/atanh identity:
        ln(y) = 2 * (z + z^3/3 + z^5/5 + ...),  z = (y-1)/(y+1)
    for y in [1, 2).

    The Taylor estimate is then refined with a small number of Newton steps using
    exp_fp as the inverse relation, which materially improves cost accuracy while
    staying in the requested exp/ln family.
    """
    _require(isinstance(x_fp, int), "x_fp must be int")
    _require(x_fp > 0, "ln input must be positive")

    if x_fp == SCALE:
        return 0

    y_fp = x_fp
    power_of_two = 0
    while y_fp >= 2 * SCALE:
        y_fp = (y_fp + 1) // 2
        power_of_two += 1
    while y_fp < SCALE:
        y_fp = _checked_mul(y_fp, 2, "ln upscale")
        power_of_two -= 1

    numerator = _checked_mul(y_fp - SCALE, SCALE, "ln z numerator")
    denominator = y_fp + SCALE
    z_fp = numerator // denominator
    z_sq_fp = _fp_mul_floor(z_fp, z_fp)

    series_fp = z_fp
    odd_power_fp = z_fp
    for n in range(1, LN_TAYLOR_TERMS):
        odd_power_fp = _fp_mul_floor(odd_power_fp, z_sq_fp)
        series_fp += odd_power_fp // (2 * n + 1)

    result = 2 * series_fp + power_of_two * LN2_FP

    for _ in range(4):
        exp_estimate_fp = exp_fp(result)
        if exp_estimate_fp == 0:
            break
        ratio_fp = _mul_div_floor(x_fp, SCALE, exp_estimate_fp)
        delta_fp = ratio_fp - SCALE
        if delta_fp == 0:
            break
        result += delta_fp

    return result


# ---------------------------------------------------------------------------
# LMSR helpers
# ---------------------------------------------------------------------------


def exponent_inputs_fp(q: list[int], b: int) -> list[int]:
    _validate_state(q, b)
    result: list[int] = []
    for qi in q:
        result.append(_mul_div_floor(qi, SCALE, b))
    return result


def log_sum_exp_fp(exponents_fp: list[int]) -> LogSumExpResult:
    _require(len(exponents_fp) >= 1, "need at least one exponent")
    _require(all(isinstance(x, int) and x >= 0 for x in exponents_fp), "exponents must be non-negative ints")

    max_exponent_fp = max(exponents_fp)
    shifted_exp_fp: list[int] = []
    sum_exp_fp = 0

    for x_fp in exponents_fp:
        shifted = x_fp - max_exponent_fp
        exp_val = exp_fp(shifted)
        shifted_exp_fp.append(exp_val)
        sum_exp_fp = _checked_add(sum_exp_fp, exp_val, "sum_exp")

    log_sum = max_exponent_fp + ln_fp(sum_exp_fp)
    return LogSumExpResult(
        max_exponent_fp=max_exponent_fp,
        sum_exp_fp=sum_exp_fp,
        log_sum_exp_fp=log_sum,
        shifted_exp_fp=shifted_exp_fp,
        exponent_inputs_fp=list(exponents_fp),
    )


def lmsr_log_sum_exp_fp(q: list[int], b: int) -> LogSumExpResult:
    return log_sum_exp_fp(exponent_inputs_fp(q, b))


def _sum_shifted_exp_fp(exponents_fp: list[int], shared_max_fp: int) -> int:
    total = 0
    for exponent_fp in exponents_fp:
        total = _checked_add(total, exp_fp(exponent_fp - shared_max_fp), "shared sum_exp")
    return total


def _lmsr_cost_numerator(q: list[int], b: int) -> int:
    lse = lmsr_log_sum_exp_fp(q, b)
    return _checked_mul(b, lse.log_sum_exp_fp, "cost numerator")


def lmsr_cost(q: list[int], b: int) -> int:
    """Compute the LMSR cost C(q), rounded up in favor of the contract."""
    numerator = _lmsr_cost_numerator(q, b)
    result = _ceil_div(numerator, SCALE)
    return _check_uint64(result, "lmsr_cost")


def lmsr_cost_floor(q: list[int], b: int) -> int:
    """Internal helper for tests / sell-side mirror paths."""
    numerator = _lmsr_cost_numerator(q, b)
    result = _floor_div(numerator, SCALE)
    return _check_uint64(result, "lmsr_cost_floor")


def lmsr_cost_delta(q: list[int], b: int, outcome: int, shares: int) -> int:
    _validate_state(q, b)
    _require(0 <= outcome < len(q), "outcome index out of range")
    _check_uint64(shares, "shares")

    q_after = list(q)
    q_after[outcome] = _check_uint64(q_after[outcome] + shares, f"q[{outcome}] after buy")
    numerator_before = _lmsr_cost_numerator(q, b)
    numerator_after = _lmsr_cost_numerator(q_after, b)
    direct_delta = numerator_after - numerator_before if numerator_after > numerator_before else 0
    direct_quote = _ceil_div(direct_delta, SCALE) if direct_delta > 0 else 0
    round_trip_floor = _floor_div(direct_delta, SCALE) if direct_delta > 0 else 0
    padded_direct_quote = direct_quote
    if shares > 0:
        padded_direct_quote = _check_uint64(direct_quote + BUY_APPROXIMATION_MARGIN, "buy padded quote")

    exponents_before = exponent_inputs_fp(q, b)
    exponents_after = exponent_inputs_fp(q_after, b)
    shared_max_fp = max(max(exponents_before), max(exponents_after))
    sum_before_fp = _sum_shifted_exp_fp(exponents_before, shared_max_fp)
    sum_after_fp = _sum_shifted_exp_fp(exponents_after, shared_max_fp)

    if sum_before_fp == 0:
        ratio_quote = _ceil_div(numerator_after, SCALE) - _floor_div(numerator_before, SCALE)
    else:
        ratio_fp = _mul_div_ceil(sum_after_fp, SCALE, sum_before_fp)
        _require(ratio_fp >= SCALE, "buy ratio must be >= 1")
        delta_numerator = _checked_mul(b, ln_fp(ratio_fp), "buy delta numerator")
        ratio_quote = _ceil_div(delta_numerator, SCALE)

    # Keep the buy side contract-favoring even when ln/log-sum-exp quantization
    # makes the ratio path a few microunits too cheap, but do not let the
    # separately rounded direct path overshoot arbitrarily.
    ratio_cap = _check_uint64(ratio_quote + BUY_APPROXIMATION_MARGIN, "buy ratio cap")
    result = max(ratio_quote, min(padded_direct_quote, ratio_cap), round_trip_floor)
    return _check_uint64(result, "lmsr_cost_delta")


def lmsr_sell_return(q: list[int], b: int, outcome: int, shares: int) -> int:
    """Mirror helper used by adversarial tests. Returns floor-rounded user return."""
    _validate_state(q, b)
    _require(0 <= outcome < len(q), "outcome index out of range")
    _check_uint64(shares, "shares")
    _require(q[outcome] >= shares, "cannot sell more shares than outstanding")

    q_after = list(q)
    q_after[outcome] -= shares
    numerator_before = _lmsr_cost_numerator(q, b)
    numerator_after = _lmsr_cost_numerator(q_after, b)
    if numerator_before <= numerator_after:
        result = 0
    else:
        result = _floor_div(numerator_before - numerator_after, SCALE)
    return _check_uint64(result, "lmsr_sell_return")


def lmsr_prices(q: list[int], b: int) -> list[int]:
    _validate_state(q, b)
    lse = lmsr_log_sum_exp_fp(q, b)

    prices: list[int] = []
    allocated = 0
    for idx, weight_fp in enumerate(lse.shifted_exp_fp):
        if idx == len(lse.shifted_exp_fp) - 1:
            price = SCALE - allocated
        else:
            price = _mul_div_floor(weight_fp, SCALE, lse.sum_exp_fp)
            allocated += price
        prices.append(_check_uint64(price, f"price[{idx}]"))

    return prices


def lmsr_liquidity_scale(q: list[int], b: int, deposit: int, pool: int) -> tuple[list[int], int]:
    _validate_state(q, b)
    _check_uint64(deposit, "deposit")
    _check_uint64(pool, "pool")
    _require(pool > 0, "pool must be positive")

    factor_numerator = _check_uint64(pool + deposit, "pool + deposit")
    scaled_q = [_check_uint64(_mul_div_floor(qi, factor_numerator, pool), f"scaled q") for qi in q]
    scaled_b = _check_uint64(_mul_div_floor(b, factor_numerator, pool), "scaled b")
    return scaled_q, scaled_b


def lmsr_gauge_alpha_from_prices(prices: list[int]) -> int:
    _validate_prices(prices)
    alpha_fp = 0
    for price in prices:
        inv_price_fp = _mul_div_ceil(SCALE, SCALE, price)
        alpha_fp = max(alpha_fp, ln_fp(inv_price_fp))
    return _check_uint64(alpha_fp, "gauge alpha")


def lmsr_collateral_required_from_prices(target_delta_b: int, prices: list[int]) -> int:
    _check_uint64(target_delta_b, "target_delta_b")
    _require(target_delta_b > 0, "target_delta_b must be positive")
    alpha_fp = lmsr_gauge_alpha_from_prices(prices)
    return _check_uint64(_mul_div_ceil(target_delta_b, alpha_fp, SCALE), "collateral required")


def lmsr_normalized_q_from_prices(prices: list[int], b: int) -> list[int]:
    _validate_prices(prices)
    _check_uint64(b, "b")
    _require(b > 0, "b must be positive")

    alpha_fp = lmsr_gauge_alpha_from_prices(prices)
    q: list[int] = []
    for price in prices:
        inv_price_fp = _mul_div_ceil(SCALE, SCALE, price)
        ln_inv_fp = ln_fp(inv_price_fp)
        if alpha_fp >= ln_inv_fp:
            q_i = _mul_div_floor(b, alpha_fp - ln_inv_fp, SCALE)
        else:
            q_i = 0
        q.append(_check_uint64(q_i, "normalized q"))
    return q


__all__ = [
    "EXP_TAYLOR_TERMS",
    "LN_TAYLOR_TERMS",
    "LN2_FP",
    "MAX_UINT128",
    "MAX_UINT64",
    "SCALE",
    "LogSumExpResult",
    "LMSRMathError",
    "exp_fp",
    "exponent_inputs_fp",
    "ln_fp",
    "log_sum_exp_fp",
    "lmsr_cost",
    "lmsr_cost_delta",
    "lmsr_cost_floor",
    "lmsr_collateral_required_from_prices",
    "lmsr_gauge_alpha_from_prices",
    "lmsr_liquidity_scale",
    "lmsr_log_sum_exp_fp",
    "lmsr_normalized_q_from_prices",
    "lmsr_prices",
    "lmsr_sell_return",
]

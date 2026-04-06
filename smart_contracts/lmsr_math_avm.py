"""Puya-friendly LMSR helpers for the on-chain market contract.

This module mirrors the subset of C1 math needed by the contract, but uses
Algopy-native types and subroutines so the market contract can compile on the
normal Puya path.
"""

from algopy import Array, UInt64, op, subroutine, urange

SCALE = 1_000_000
LN2_FP = 693_147
EXP_TAYLOR_TERMS = 20
LN_TAYLOR_TERMS = 32


@subroutine
def _require(condition: bool) -> None:
    assert condition


@subroutine
def _max_u64(a: UInt64, b: UInt64) -> UInt64:
    if a >= b:
        return a
    return b


@subroutine
def _floor_div(numerator: UInt64, denominator: UInt64) -> UInt64:
    _require(denominator > UInt64(0))
    return numerator // denominator


@subroutine
def _ceil_div(numerator: UInt64, denominator: UInt64) -> UInt64:
    _require(denominator > UInt64(0))
    quotient = numerator // denominator
    remainder = numerator % denominator
    if remainder == UInt64(0):
        return quotient
    return quotient + UInt64(1)


@subroutine
def _mul_div_floor(a: UInt64, b: UInt64, denominator: UInt64) -> UInt64:
    _require(denominator > UInt64(0))
    product_high, product_low = op.mulw(a, b)
    return op.divw(product_high, product_low, denominator)


@subroutine
def _mul_div_ceil(a: UInt64, b: UInt64, denominator: UInt64) -> UInt64:
    _require(denominator > UInt64(0))
    product_high, product_low = op.mulw(a, b)
    quotient_high, quotient_low, remainder_high, remainder_low = op.divmodw(
        product_high,
        product_low,
        UInt64(0),
        denominator,
    )
    _require(quotient_high == UInt64(0))
    if remainder_high == UInt64(0) and remainder_low == UInt64(0):
        return quotient_low
    return quotient_low + UInt64(1)


@subroutine
def _exp_taylor_positive_reduced(x_fp: UInt64) -> UInt64:
    total = UInt64(SCALE)
    term = UInt64(SCALE)
    for step in urange(UInt64(EXP_TAYLOR_TERMS - 1)):
        k = step + UInt64(1)
        term = _floor_div(term * x_fp, k * UInt64(SCALE))
        total = total + term
    return total


@subroutine
def _exp_taylor_negative_reduced(delta_fp: UInt64) -> UInt64:
    total = UInt64(SCALE)
    term_abs = UInt64(SCALE)
    for step in urange(UInt64(EXP_TAYLOR_TERMS - 1)):
        k = step + UInt64(1)
        term_abs = _floor_div(term_abs * delta_fp, k * UInt64(SCALE))
        if term_abs == UInt64(0):
            continue
        if (k % UInt64(2)) == UInt64(1):
            if total >= term_abs:
                total = total - term_abs
            else:
                total = UInt64(0)
        else:
            total = total + term_abs
    return total


@subroutine
def exp_pos_fp(x_fp: UInt64) -> UInt64:
    if x_fp == UInt64(0):
        return UInt64(SCALE)

    reduced = x_fp
    halvings = UInt64(0)
    while reduced > UInt64(SCALE):
        reduced = reduced // UInt64(2)
        halvings = halvings + UInt64(1)

    result = _exp_taylor_positive_reduced(reduced)
    for halve_step in urange(halvings):
        _require(halve_step >= UInt64(0))
        result = _floor_div(result * result, UInt64(SCALE))

    return result


@subroutine
def exp_neg_fp(delta_fp: UInt64) -> UInt64:
    if delta_fp == UInt64(0):
        return UInt64(SCALE)

    reduced = delta_fp
    halvings = UInt64(0)
    while reduced > UInt64(SCALE):
        reduced = reduced // UInt64(2)
        halvings = halvings + UInt64(1)

    result = _exp_taylor_negative_reduced(reduced)
    for halve_step in urange(halvings):
        _require(halve_step >= UInt64(0))
        result = _floor_div(result * result, UInt64(SCALE))
    return result


@subroutine
def ln_fp(x_fp: UInt64) -> UInt64:
    _require(x_fp >= UInt64(SCALE))
    if x_fp == UInt64(SCALE):
        return UInt64(0)

    y_fp = x_fp
    power_of_two = UInt64(0)
    while y_fp >= (UInt64(2) * UInt64(SCALE)):
        y_fp = _ceil_div(y_fp, UInt64(2))
        power_of_two = power_of_two + UInt64(1)

    z_num = (y_fp - UInt64(SCALE)) * UInt64(SCALE)
    z_den = y_fp + UInt64(SCALE)
    z_fp = _floor_div(z_num, z_den)
    z_sq_fp = _floor_div(z_fp * z_fp, UInt64(SCALE))

    series_fp = z_fp
    odd_power_fp = z_fp
    for step in urange(UInt64(LN_TAYLOR_TERMS - 1)):
        odd_power_fp = _floor_div(odd_power_fp * z_sq_fp, UInt64(SCALE))
        denom = ((step + UInt64(1)) * UInt64(2)) + UInt64(1)
        series_fp = series_fp + _floor_div(odd_power_fp, denom)

    result = (series_fp * UInt64(2)) + (power_of_two * UInt64(LN2_FP))
    for newton_step in urange(UInt64(4)):
        _require(newton_step >= UInt64(0))
        estimate = exp_pos_fp(result)
        ratio_fp = _floor_div(x_fp * UInt64(SCALE), estimate)
        if ratio_fp == UInt64(SCALE):
            break
        if ratio_fp > UInt64(SCALE):
            result = result + (ratio_fp - UInt64(SCALE))
        else:
            result = result - (UInt64(SCALE) - ratio_fp)
    return result


@subroutine
def _validate_state(q: Array[UInt64], b: UInt64) -> None:
    _require(q.length >= UInt64(2))
    _require(b > UInt64(0))


@subroutine
def _exponent_fp(q_i: UInt64, b: UInt64) -> UInt64:
    return _floor_div(q_i * UInt64(SCALE), b)


@subroutine
def _max_exponent_fp(q: Array[UInt64], b: UInt64) -> UInt64:
    max_exponent = UInt64(0)
    for idx in urange(q.length):
        exponent = _exponent_fp(q[idx], b)
        max_exponent = _max_u64(max_exponent, exponent)
    return max_exponent


@subroutine
def _sum_shifted_exp_fp(q: Array[UInt64], b: UInt64, shared_max_fp: UInt64) -> UInt64:
    total = UInt64(0)
    for idx in urange(q.length):
        exponent = _exponent_fp(q[idx], b)
        total = total + exp_neg_fp(shared_max_fp - exponent)
    return total


@subroutine
def lmsr_cost_delta(q: Array[UInt64], b: UInt64, outcome: UInt64, shares: UInt64) -> UInt64:
    _validate_state(q, b)
    _require(outcome < q.length)

    q_after = q.copy()
    q_after[outcome] = q_after[outcome] + shares

    max_before = _max_exponent_fp(q, b)
    max_after = _max_exponent_fp(q_after, b)
    shared_max = _max_u64(max_before, max_after)

    sum_before = _sum_shifted_exp_fp(q, b, shared_max)
    sum_after = _sum_shifted_exp_fp(q_after, b, shared_max)
    ratio_fp = _mul_div_ceil(sum_after, UInt64(SCALE), sum_before)
    return _ceil_div(b * ln_fp(ratio_fp), UInt64(SCALE))


@subroutine
def lmsr_sell_return(q: Array[UInt64], b: UInt64, outcome: UInt64, shares: UInt64) -> UInt64:
    _validate_state(q, b)
    _require(outcome < q.length)
    _require(q[outcome] >= shares)

    q_after = q.copy()
    q_after[outcome] = q_after[outcome] - shares

    max_before = _max_exponent_fp(q, b)
    max_after = _max_exponent_fp(q_after, b)
    shared_max = _max_u64(max_before, max_after)

    sum_before = _sum_shifted_exp_fp(q, b, shared_max)
    sum_after = _sum_shifted_exp_fp(q_after, b, shared_max)
    ratio_fp = _mul_div_floor(sum_before, UInt64(SCALE), sum_after)
    return _floor_div(b * ln_fp(ratio_fp), UInt64(SCALE))


@subroutine
def lmsr_prices(q: Array[UInt64], b: UInt64) -> Array[UInt64]:
    _validate_state(q, b)

    shared_max = _max_exponent_fp(q, b)
    sum_exp = _sum_shifted_exp_fp(q, b, shared_max)

    prices = q.copy()
    allocated = UInt64(0)
    last_index = q.length - UInt64(1)
    for idx in urange(q.length):
        exponent = _exponent_fp(q[idx], b)
        weight = exp_neg_fp(shared_max - exponent)
        if idx == last_index:
            price = UInt64(SCALE) - allocated
        else:
            price = _mul_div_floor(weight, UInt64(SCALE), sum_exp)
            allocated = allocated + price
        prices[idx] = price
    return prices


@subroutine
def lmsr_liquidity_scale_q(q: Array[UInt64], b: UInt64, deposit: UInt64, pool: UInt64) -> Array[UInt64]:
    _validate_state(q, b)
    _require(pool > UInt64(0))

    factor_numerator = pool + deposit
    scaled_q = q.copy()
    for idx in urange(q.length):
        scaled_q[idx] = _mul_div_floor(q[idx], factor_numerator, pool)
    return scaled_q


@subroutine
def lmsr_liquidity_scale_b(q: Array[UInt64], b: UInt64, deposit: UInt64, pool: UInt64) -> UInt64:
    _validate_state(q, b)
    _require(pool > UInt64(0))
    return _mul_div_floor(b, pool + deposit, pool)


__all__ = [
    "EXP_TAYLOR_TERMS",
    "LN_TAYLOR_TERMS",
    "LN2_FP",
    "SCALE",
    "exp_pos_fp",
    "exp_neg_fp",
    "ln_fp",
    "lmsr_cost_delta",
    "lmsr_liquidity_scale_b",
    "lmsr_liquidity_scale_q",
    "lmsr_prices",
    "lmsr_sell_return",
]

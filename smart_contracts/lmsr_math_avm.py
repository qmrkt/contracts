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
BUY_APPROXIMATION_MARGIN = 6
BUY_LOG_SAFETY_MARGIN_FP = 1
SELL_ROUNDING_SAFETY_UNITS = 2
MAX_TRADE_GROWTH_EXP_FP = 20 * SCALE


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
        result = _mul_div_floor(result, result, UInt64(SCALE))

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
        result = _mul_div_floor(result, result, UInt64(SCALE))
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
        ratio_fp = _mul_div_floor(x_fp, UInt64(SCALE), estimate)
        if ratio_fp == UInt64(SCALE):
            break
        if ratio_fp > UInt64(SCALE):
            result = result + (ratio_fp - UInt64(SCALE))
        else:
            result = result - (UInt64(SCALE) - ratio_fp)
    return result


@subroutine
def ln_fp_ceil(x_fp: UInt64) -> UInt64:
    result = ln_fp(x_fp)
    while exp_pos_fp(result) < x_fp:
        result = result + UInt64(1)
    return result


@subroutine
def _validate_state(q: Array[UInt64], b: UInt64) -> None:
    _require(q.length >= UInt64(2))
    _require(b > UInt64(0))


@subroutine
def _validate_prices(prices: Array[UInt64]) -> None:
    _require(prices.length >= UInt64(2))
    total = UInt64(0)
    for idx in urange(prices.length):
        _require(prices[idx] > UInt64(0))
        total = total + prices[idx]
    _require(total == UInt64(SCALE))


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
def _log_sum_exp_fp(q: Array[UInt64], b: UInt64) -> UInt64:
    max_exponent = _max_exponent_fp(q, b)
    sum_exp = _sum_shifted_exp_fp(q, b, max_exponent)
    return max_exponent + ln_fp(sum_exp)


@subroutine
def _lmsr_cost_ceil(q: Array[UInt64], b: UInt64) -> UInt64:
    return _mul_div_ceil(b, _log_sum_exp_fp(q, b), UInt64(SCALE))


@subroutine
def _lmsr_cost_floor(q: Array[UInt64], b: UInt64) -> UInt64:
    return _mul_div_floor(b, _log_sum_exp_fp(q, b), UInt64(SCALE))


@subroutine
def _outcome_weight(q: Array[UInt64], b: UInt64, outcome: UInt64, shared_max: UInt64) -> UInt64:
    exponent = _exponent_fp(q[outcome], b)
    return exp_neg_fp(shared_max - exponent)


@subroutine
def _outcome_price_ceil(q: Array[UInt64], b: UInt64, outcome: UInt64) -> UInt64:
    shared_max = _max_exponent_fp(q, b)
    sum_exp = _sum_shifted_exp_fp(q, b, shared_max)
    weight = _outcome_weight(q, b, outcome, shared_max)
    return _mul_div_ceil(weight, UInt64(SCALE), sum_exp)


@subroutine
def _trade_growth_exponent_fp(shares: UInt64, b: UInt64) -> UInt64:
    return _mul_div_floor(shares, UInt64(SCALE), b)


@subroutine
def lmsr_cost_delta(q: Array[UInt64], b: UInt64, outcome: UInt64, shares: UInt64) -> UInt64:
    _validate_state(q, b)
    _require(outcome < q.length)

    q_after = q.copy()
    q_after[outcome] = q_after[outcome] + shares
    delta_exponent_fp = _trade_growth_exponent_fp(shares, b)
    if delta_exponent_fp > UInt64(MAX_TRADE_GROWTH_EXP_FP):
        cost_before_floor = _lmsr_cost_floor(q, b)
        cost_after_ceil = _lmsr_cost_ceil(q_after, b)
        return cost_after_ceil - cost_before_floor

    shared_max_before = _max_exponent_fp(q, b)
    sum_before = _sum_shifted_exp_fp(q, b, shared_max_before)
    weight_before = _outcome_weight(q, b, outcome, shared_max_before)
    price_before_ceil = _mul_div_ceil(weight_before, UInt64(SCALE), sum_before)
    price_after_ceil = _outcome_price_ceil(q_after, b, outcome)
    growth_fp = exp_pos_fp(delta_exponent_fp)
    growth_minus = UInt64(0)
    if growth_fp > UInt64(SCALE):
        growth_minus = growth_fp - UInt64(SCALE)

    increment_fp = _mul_div_ceil(weight_before, growth_minus, sum_before)
    linear_quote = _mul_div_ceil(price_after_ceil, shares, UInt64(SCALE))

    core_quote = linear_quote
    if increment_fp > UInt64(1):
        ratio_fp = UInt64(SCALE) + increment_fp
        delta_ln_fp = ln_fp_ceil(ratio_fp)
        core_quote = _mul_div_ceil(b, delta_ln_fp, UInt64(SCALE))

    sell_floor = lmsr_sell_return(q_after, b, outcome, shares)
    result = _max_u64(core_quote, sell_floor)
    cost_before_floor = _lmsr_cost_floor(q, b)
    cost_after_ceil = _lmsr_cost_ceil(q_after, b)
    if cost_after_ceil > cost_before_floor:
        direct_upper = cost_after_ceil - cost_before_floor
        direct_margin = result // UInt64(50)
        if direct_margin < UInt64(16):
            direct_margin = UInt64(16)
        if price_before_ceil <= UInt64(64) and direct_upper > result:
            return direct_upper
        if direct_upper > result and (direct_upper - result) <= direct_margin:
            return direct_upper
    return result


@subroutine
def lmsr_sell_return(q: Array[UInt64], b: UInt64, outcome: UInt64, shares: UInt64) -> UInt64:
    _validate_state(q, b)
    _require(outcome < q.length)
    _require(q[outcome] >= shares)

    q_after = q.copy()
    q_after[outcome] = q_after[outcome] - shares
    delta_exponent_fp = _trade_growth_exponent_fp(shares, b)
    cost_before_floor = _lmsr_cost_floor(q, b)
    cost_after_floor = _lmsr_cost_floor(q_after, b)
    direct_floor = UInt64(0)
    if cost_before_floor > cost_after_floor:
        direct_floor = cost_before_floor - cost_after_floor
    if delta_exponent_fp > UInt64(MAX_TRADE_GROWTH_EXP_FP):
        return direct_floor

    shared_max_after = _max_exponent_fp(q_after, b)
    sum_after = _sum_shifted_exp_fp(q_after, b, shared_max_after)
    weight_after = _outcome_weight(q_after, b, outcome, shared_max_after)
    price_after_floor = _mul_div_floor(weight_after, UInt64(SCALE), sum_after)
    growth_fp = exp_pos_fp(delta_exponent_fp)
    growth_minus = UInt64(0)
    if growth_fp > UInt64(SCALE):
        growth_minus = growth_fp - UInt64(SCALE)

    increment_fp = _mul_div_floor(weight_after, growth_minus, sum_after)
    linear_floor = _mul_div_floor(price_after_floor, shares, UInt64(SCALE))
    if increment_fp <= UInt64(1):
        result = linear_floor
    else:
        ratio_fp = UInt64(SCALE) + increment_fp
        nonlinear_floor = _mul_div_floor(b, ln_fp(ratio_fp), UInt64(SCALE))
        result = _max_u64(linear_floor, nonlinear_floor)

    if direct_floor > UInt64(0) and direct_floor < result:
        return direct_floor
    return result


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


@subroutine
def lmsr_gauge_alpha_from_prices(prices: Array[UInt64]) -> UInt64:
    _validate_prices(prices)
    alpha = UInt64(0)
    for idx in urange(prices.length):
        inv_price_fp = _mul_div_ceil(UInt64(SCALE), UInt64(SCALE), prices[idx])
        alpha = _max_u64(alpha, ln_fp(inv_price_fp))
    return alpha


@subroutine
def lmsr_collateral_required_from_prices(target_delta_b: UInt64, prices: Array[UInt64]) -> UInt64:
    _require(target_delta_b > UInt64(0))
    alpha = lmsr_gauge_alpha_from_prices(prices)
    return _mul_div_ceil(target_delta_b, alpha, UInt64(SCALE))


@subroutine
def lmsr_q_from_prices_with_floor(prices: Array[UInt64], b: UInt64, floor_q: Array[UInt64]) -> Array[UInt64]:
    _validate_prices(prices)
    _require(b > UInt64(0))
    _require(floor_q.length == prices.length)

    alpha = lmsr_gauge_alpha_from_prices(prices)
    q = prices.copy()
    common_shift = UInt64(0)
    for idx in urange(prices.length):
        inv_price_fp = _mul_div_ceil(UInt64(SCALE), UInt64(SCALE), prices[idx])
        ln_inv = ln_fp(inv_price_fp)
        if alpha >= ln_inv:
            q[idx] = _mul_div_floor(b, alpha - ln_inv, UInt64(SCALE))
        else:
            q[idx] = UInt64(0)
        if floor_q[idx] > q[idx]:
            gap = floor_q[idx] - q[idx]
            if gap > common_shift:
                common_shift = gap

    if common_shift > UInt64(0):
        for idx in urange(q.length):
            q[idx] = q[idx] + common_shift

    return q


__all__ = [
    "EXP_TAYLOR_TERMS",
    "LN_TAYLOR_TERMS",
    "LN2_FP",
    "SCALE",
    "exp_pos_fp",
    "exp_neg_fp",
    "ln_fp",
    "lmsr_collateral_required_from_prices",
    "lmsr_cost_delta",
    "lmsr_gauge_alpha_from_prices",
    "lmsr_liquidity_scale_b",
    "lmsr_liquidity_scale_q",
    "lmsr_q_from_prices_with_floor",
    "lmsr_prices",
    "lmsr_sell_return",
]

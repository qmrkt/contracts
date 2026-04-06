from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart_contracts.lmsr_math import SCALE

getcontext().prec = 80
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lmsr_reference_vectors.json"


REFERENCE_CASES = [
    {
        "id": "n2_balanced",
        "q": [500_000, 500_000],
        "b": 1_000_000,
        "buy": {"outcome": 0, "shares": 250_000},
        "lp": {"deposit": 250_000, "pool": 2_000_000},
    },
    {
        "id": "n5_skewed",
        "q": [100_000, 200_000, 350_000, 500_000, 900_000],
        "b": 750_000,
        "buy": {"outcome": 3, "shares": 125_000},
        "lp": {"deposit": 500_000, "pool": 3_000_000},
    },
    {
        "id": "n16_wide",
        "q": [
            10_000,
            20_000,
            30_000,
            40_000,
            50_000,
            60_000,
            70_000,
            80_000,
            90_000,
            100_000,
            110_000,
            120_000,
            130_000,
            140_000,
            150_000,
            160_000,
        ],
        "b": 1_500_000,
        "buy": {"outcome": 15, "shares": 55_000},
        "lp": {"deposit": 700_000, "pool": 4_200_000},
    },
]


def d(value: int | str) -> Decimal:
    return Decimal(value)


def dec_quantize_int(value: Decimal, rounding: str) -> int:
    return int(value.to_integral_value(rounding=rounding))


def dec_exp_fp(x_fp: int) -> Decimal:
    return (d(x_fp) / d(SCALE)).exp() * d(SCALE)


def dec_ln_fp(x_fp: int) -> Decimal:
    return (d(x_fp) / d(SCALE)).ln() * d(SCALE)


def dec_cost_exact(q: list[int], b: int) -> Decimal:
    exponents = [(d(qi) / d(b)).exp() for qi in q]
    total = sum(exponents, start=Decimal(0))
    return d(b) * total.ln()


def dec_cost_up(q: list[int], b: int) -> int:
    return dec_quantize_int(dec_cost_exact(q, b), ROUND_CEILING)


def dec_cost_delta_up(q: list[int], b: int, outcome: int, shares: int) -> int:
    q_after = list(q)
    q_after[outcome] += shares
    return dec_quantize_int(dec_cost_exact(q_after, b) - dec_cost_exact(q, b), ROUND_CEILING)


def dec_sell_return_down(q: list[int], b: int, outcome: int, shares: int) -> int:
    q_after = list(q)
    q_after[outcome] -= shares
    return dec_quantize_int(dec_cost_exact(q, b) - dec_cost_exact(q_after, b), ROUND_FLOOR)


def dec_prices(q: list[int], b: int) -> list[int]:
    weights = [(d(qi) / d(b)).exp() for qi in q]
    total = sum(weights, start=Decimal(0))
    prices: list[int] = []
    allocated = 0
    for idx, weight in enumerate(weights):
        if idx == len(weights) - 1:
            prices.append(SCALE - allocated)
        else:
            price = dec_quantize_int((weight / total) * d(SCALE), ROUND_FLOOR)
            prices.append(price)
            allocated += price
    return prices


def dec_liquidity_scale(q: list[int], b: int, deposit: int, pool: int) -> tuple[list[int], int]:
    ratio = d(pool + deposit) / d(pool)
    scaled_q = [dec_quantize_int(d(qi) * ratio, ROUND_FLOOR) for qi in q]
    scaled_b = dec_quantize_int(d(b) * ratio, ROUND_FLOOR)
    return scaled_q, scaled_b


def load_fixture() -> dict:
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


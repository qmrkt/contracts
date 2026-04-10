from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart_contracts.lmsr_math import SCALE, lmsr_cost, lmsr_cost_delta, lmsr_liquidity_scale, lmsr_prices

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


def generate_fixture() -> dict:
    return {
        "version": 1,
        "scale": SCALE,
        "cases": [
            {
                "id": case["id"],
                "q": case["q"],
                "b": case["b"],
                "buy": case["buy"],
                "lp": case["lp"],
                "cost": lmsr_cost(case["q"], case["b"]),
                "cost_delta": lmsr_cost_delta(
                    case["q"], case["b"], case["buy"]["outcome"], case["buy"]["shares"]
                ),
                "prices": lmsr_prices(case["q"], case["b"]),
                "liquidity_scale": {
                    "scaled_q": lmsr_liquidity_scale(
                        case["q"], case["b"], case["lp"]["deposit"], case["lp"]["pool"]
                    )[0],
                    "scaled_b": lmsr_liquidity_scale(
                        case["q"], case["b"], case["lp"]["deposit"], case["lp"]["pool"]
                    )[1],
                },
            }
            for case in REFERENCE_CASES
        ],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Emit deterministic LMSR reference vectors")
    parser.add_argument("--check", action="store_true", help="verify the checked-in fixture matches freshly generated output")
    args = parser.parse_args()

    fixture_path = Path(__file__).parent / "fixtures" / "lmsr_reference_vectors.json"
    generated = json.dumps(generate_fixture(), indent=2, sort_keys=True) + "\n"

    if args.check:
        if not fixture_path.exists():
            print(f"missing fixture: {fixture_path}", file=sys.stderr)
            raise SystemExit(1)
        if fixture_path.read_text(encoding="utf-8") != generated:
            print(f"fixture out of date: {fixture_path}", file=sys.stderr)
            raise SystemExit(1)
        print(f"fixture up to date: {fixture_path}")
    else:
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(generated, encoding="utf-8")
        print(f"wrote {fixture_path}")


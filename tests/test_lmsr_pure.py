from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smart_contracts.lmsr_math as lmsr_math


def test_no_chain_state_or_transfers() -> None:
    source = inspect.getsource(lmsr_math)

    forbidden_tokens = [
        "Global",
        "Txn",
        "gtxn",
        "itxn",
        "Application",
        "AssetTransfer",
        "Payment",
        "arc4",
        "ARC4Contract",
    ]

    for token in forbidden_tokens:
        assert token not in source

    exported = {
        "exp_fp",
        "ln_fp",
        "log_sum_exp_fp",
        "lmsr_cost",
        "lmsr_cost_delta",
        "lmsr_prices",
        "lmsr_liquidity_scale",
    }
    assert exported.issubset(set(lmsr_math.__all__))


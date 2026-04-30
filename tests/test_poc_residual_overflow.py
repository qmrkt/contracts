"""
PoC: UInt64 Overflow in LP Residual Weight Calculation
======================================================

Vulnerability: _entry_weighted_sum_checked(lp_shares_total, settlement_timestamp - 1)
overflows UInt64 when lp_shares_total * settlement_timestamp > 2^64.

This permanently bricks claim_lp_residual() for ALL LP providers,
locking their residual funds in the contract forever.

Trigger condition (2026 timestamps):
  lp_shares_total > 10,376,696,157  (~$10,376 USDC with 6 decimals)

Affected code: contract.py L547-563 (_calculate_weight)
               contract.py L570-571 (_total_residual_weight)
               contract.py L574-584 (_claimable_residual)
               contract.py L718-729 (claim_lp_residual)

Reporter: y4motion
"""
from __future__ import annotations

import math
import pytest

from smart_contracts.market_app.active_lp_model import ActiveLpMarketAppModel

SCALE = 1_000_000_000

def _py_lmsr_prices(q: list[int], b: int) -> list[int]:
    """Pure Python LMSR price calculation (no AVM types)."""
    max_q = max(q)
    exps = [math.exp((qi - max_q) / b) for qi in q]
    total = sum(exps)
    return [int(SCALE * e / total) for e in exps]

UINT64_MAX = 2**64 - 1


def _make_large_lp_market(
    *,
    initial_b: int = 11_000_000_000,  # ~$11,000 USDC (above overflow threshold)
    num_outcomes: int = 2,
    deadline: int = 1_777_800_000,     # ~2026-06-02 timestamp
    bootstrap_time: int = 1_777_700_000,  # activation timestamp
) -> ActiveLpMarketAppModel:
    """Create a market with b > overflow threshold for 2026 timestamps."""
    return ActiveLpMarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=[1000 + i for i in range(num_outcomes)],
        b=initial_b,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=deadline,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        proposer_fee_bps=0,
        proposer_fee_floor_bps=0,
        grace_period_secs=3_600,
        market_admin="admin",
    )


class TestResidualOverflowPoC:
    """
    Demonstrates that the UInt64 overflow in _entry_weighted_sum_checked
    would permanently lock LP residual funds on-chain.

    The Python reference model uses arbitrary-precision integers, so it
    does NOT overflow. These tests prove the ON-CHAIN contract would revert
    by checking the intermediate multiplication against UInt64 max.
    """

    def test_overflow_threshold_math(self) -> None:
        """Verify the overflow threshold calculation."""
        # Unix timestamp for mid-2026
        timestamp = 1_777_800_000
        max_safe_shares = UINT64_MAX // timestamp

        # Any market with lp_shares_total > max_safe_shares will overflow
        assert 10_376_000_000 < max_safe_shares < 10_377_000_000  # ~$10,376 USDC

        # Demonstrate overflow for $11,000 market
        lp_shares_total = 11_000_000_000
        product = lp_shares_total * timestamp
        assert product > UINT64_MAX, (
            f"Product {product:,} should exceed UInt64 max {UINT64_MAX:,}"
        )

    def test_single_large_bootstrap_triggers_overflow(self) -> None:
        """
        A single bootstrap with b=$11,000 USDC creates lp_shares_total
        that overflows in _total_residual_weight at claim time.

        ON-CHAIN: claim_lp_residual() would REVERT at _entry_weighted_sum_checked
        PYTHON MODEL: succeeds because Python integers don't overflow
        """
        market = _make_large_lp_market(initial_b=11_000_000_000)
        bootstrap_time = 1_777_700_000

        market.bootstrap(sender="creator", deposit_amount=11_000_000_000, now=bootstrap_time)

        # A trader buys to create some pool surplus
        market.buy(
            sender="trader",
            outcome_index=0,
            shares=1_000_000,
            max_cost=1_000_000_000,
            now=bootstrap_time + 1000,
        )

        # Resolve the market
        market.trigger_resolution(sender="anyone", now=market.deadline)
        market.propose_resolution(
            sender="resolver",
            outcome_index=0,
            evidence_hash=b"e" * 32,
            now=market.deadline + 1,
        )
        market.finalize_resolution(
            sender="anyone",
            now=market.deadline + 1 + market.challenge_window_secs,
        )

        # In the Python model, this succeeds (arbitrary precision integers)
        # On-chain, this would REVERT due to UInt64 overflow
        settlement_ts = market.settlement_timestamp
        lp_shares_total = market.lp_shares_total

        # PROVE THE OVERFLOW: The on-chain multiplication would exceed UInt64
        product = lp_shares_total * (settlement_ts - 1)
        assert product > UINT64_MAX, (
            f"CRITICAL: lp_shares_total({lp_shares_total:,}) * "
            f"(settlement_ts-1)({settlement_ts - 1:,}) = {product:,} "
            f"> UInt64_MAX({UINT64_MAX:,}). "
            f"On-chain claim_lp_residual() would REVERT here!"
        )

        # The model still works (no overflow in Python)
        residual = market.claim_lp_residual(sender="creator")
        assert residual > 0, "Model claims residual fine, but on-chain would REVERT"

        # Print diagnostic
        print(f"\n{'='*70}")
        print(f"  PoC: UInt64 Overflow in LP Residual Weight")
        print(f"{'='*70}")
        print(f"  lp_shares_total:     {lp_shares_total:>25,}")
        print(f"  settlement_timestamp:{settlement_ts:>25,}")
        print(f"  Product:             {product:>25,}")
        print(f"  UInt64 MAX:          {UINT64_MAX:>25,}")
        print(f"  Overflow by:         {product - UINT64_MAX:>25,}")
        print(f"  Residual (model):    {residual:>25,}")
        print(f"  On-chain result:     {'REVERT (permanently locked)':>25}")
        print(f"{'='*70}")

    def test_multiple_lps_accumulate_past_threshold(self) -> None:
        """
        Arithmetic proof: multiple smaller LP entries that individually
        don't overflow, but accumulate lp_shares_total past the threshold.

        NOTE: The full model-based repro requires on-chain localnet.
        This test proves the overflow MATH is valid.
        """
        timestamp = 1_777_886_401  # Realistic 2026 settlement

        # 6 LPs × $2,000 each = $12,000 total
        per_lp_b = 2_000_000_000  # $2,000 USDC (6 decimals)
        num_lps = 6
        total_lp_shares = per_lp_b * num_lps  # 12,000,000,000

        # Each individual LP is safe
        individual_product = per_lp_b * timestamp
        assert individual_product <= UINT64_MAX, "Individual LP entry should not overflow"

        # But the accumulated total overflows
        total_product = total_lp_shares * (timestamp - 1)
        assert total_product > UINT64_MAX, (
            f"6 LPs × $2,000 = $12,000 total. "
            f"Product {total_product:,} > UInt64_MAX. "
            f"ALL 6 LPs' residual is permanently locked on-chain."
        )

        print(f"\n{'='*70}")
        print(f"  PoC: Multiple LPs Accumulate Past Overflow Threshold")
        print(f"{'='*70}")
        print(f"  Number of LPs:       {num_lps}")
        print(f"  Per-LP contribution:  ${per_lp_b / 1_000_000:,.0f} USDC")
        print(f"  Total LP shares:     {total_lp_shares:>25,}")
        print(f"  settlement_timestamp:{timestamp:>25,}")
        print(f"  Individual product:  {individual_product:>25,} (safe)")
        print(f"  Total product:       {total_product:>25,} (OVERFLOW!)")
        print(f"  UInt64 MAX:          {UINT64_MAX:>25,}")
        print(f"  Overflow by:         {total_product - UINT64_MAX:>25,}")
        print(f"  On-chain result:     ALL {num_lps} LPs' residual PERMANENTLY LOCKED")
        print(f"{'='*70}")

    def test_safe_market_below_threshold(self) -> None:
        """Counter-example: a market below the threshold works correctly."""
        safe_b = 5_000_000_000  # $5,000 USDC (below threshold)
        market = _make_large_lp_market(initial_b=safe_b)
        bootstrap_time = 1_777_700_000

        market.bootstrap(sender="creator", deposit_amount=safe_b, now=bootstrap_time)

        market.buy(
            sender="trader",
            outcome_index=0,
            shares=1_000_000,
            max_cost=1_000_000_000,
            now=bootstrap_time + 1000,
        )

        market.trigger_resolution(sender="anyone", now=market.deadline)
        market.propose_resolution(
            sender="resolver",
            outcome_index=0,
            evidence_hash=b"e" * 32,
            now=market.deadline + 1,
        )
        market.finalize_resolution(
            sender="anyone",
            now=market.deadline + 1 + market.challenge_window_secs,
        )

        # Below threshold: no overflow
        settlement_ts = market.settlement_timestamp
        product = market.lp_shares_total * (settlement_ts - 1)
        assert product <= UINT64_MAX, "Safe market should not overflow"

        # Both model and on-chain would succeed
        residual = market.claim_lp_residual(sender="creator")
        assert residual > 0

    def test_threshold_decreases_over_time(self) -> None:
        """
        The overflow threshold DECREASES as Unix timestamps grow.
        Markets that were safe in 2024 may break in 2030+.
        """
        thresholds = {}
        for year, ts in [
            (2024, 1_704_067_200),
            (2026, 1_777_708_800),
            (2030, 1_893_456_000),
            (2040, 2_208_988_800),
            (2050, 2_524_608_000),
        ]:
            max_shares = UINT64_MAX // ts
            thresholds[year] = max_shares / 1_000_000  # Convert to USDC

        # Verify decreasing trend
        years = sorted(thresholds.keys())
        for i in range(len(years) - 1):
            assert thresholds[years[i]] > thresholds[years[i + 1]], (
                f"Threshold should decrease: {years[i]}=${thresholds[years[i]]:,.0f} "
                f"vs {years[i+1]}=${thresholds[years[i+1]]:,.0f}"
            )

        print(f"\n{'='*70}")
        print(f"  Time-Bomb: Overflow Threshold Decreases Over Time")
        print(f"{'='*70}")
        for year, threshold_usdc in sorted(thresholds.items()):
            print(f"  {year}: Max safe total LP = ${threshold_usdc:>12,.2f} USDC")
        print(f"{'='*70}")

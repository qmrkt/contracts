"""
Proof-of-Concept: LP Fee Rounding Dust — Permanent Lock

Demonstrates that floor-division rounding in _distribute_lp_fee() and
_settle_lp_fees() causes micro-dust to accumulate permanently in
lp_fee_balance with no LP able to claim it.

Root cause:
  1. _distribute_lp_fee: increment = floor(fee * SCALE / lp_shares_total)
     → loses (fee * SCALE) % lp_shares_total
  2. _settle_lp_fees:    accrued = floor(delta * shares / SCALE)
     → loses (delta * shares) % SCALE

The gap between lp_fee_balance and sum-of-all-LP-entitlements grows
monotonically with each trade. This dust is permanently locked.

Impact:
  Permanent lock of LP fees. Cumulative over market lifetime.

Reference:
  - contract.py: _distribute_lp_fee() (line 620-624)
  - contract.py: _settle_lp_fees() (line 509-520)

Author: y4motion (Triarchy bounty audit)
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_contracts.lmsr_math import SCALE

# ──────────────────────────────────────────────────────
# Simulate the fee distribution math from contract.py
# ──────────────────────────────────────────────────────

def mul_div_floor(a: int, b: int, denom: int) -> int:
    """Mirrors _mul_div_floor from lmsr_math_avm.py."""
    assert denom > 0
    return (a * b) // denom


def simulate_fee_dust_accumulation(
    num_trades: int,
    fee_per_trade: int,
    lp_shares_total: int,
    lp_holders: dict[str, int],  # name -> shares
) -> dict:
    """
    Simulate N trades and track the gap between lp_fee_balance
    and total claimable fees across all LPs.
    """
    cumulative_fee_per_share = 0
    lp_fee_balance = 0
    lp_snapshots = {name: 0 for name in lp_holders}
    lp_accrued = {name: 0 for name in lp_holders}

    for trade_idx in range(num_trades):
        # === _distribute_lp_fee() ===
        fee_amount = fee_per_trade
        lp_fee_balance += fee_amount

        if lp_shares_total > 0 and fee_amount > 0:
            # Floor division: this is where dust is born
            increment = mul_div_floor(fee_amount, SCALE, lp_shares_total)
            cumulative_fee_per_share += increment

        # === _settle_lp_fees() for each LP ===
        for name, shares in lp_holders.items():
            if shares == 0:
                continue
            snapshot = lp_snapshots[name]
            if cumulative_fee_per_share > snapshot:
                delta = cumulative_fee_per_share - snapshot
                # Floor division: more dust lost
                accrued = mul_div_floor(delta, shares, SCALE)
                lp_accrued[name] += accrued
            lp_snapshots[name] = cumulative_fee_per_share

    total_claimable = sum(lp_accrued.values())
    dust_locked = lp_fee_balance - total_claimable

    return {
        "num_trades": num_trades,
        "fee_per_trade": fee_per_trade,
        "lp_fee_balance": lp_fee_balance,
        "total_claimable": total_claimable,
        "dust_locked": dust_locked,
        "dust_pct": (dust_locked / lp_fee_balance * 100) if lp_fee_balance > 0 else 0,
        "per_lp": lp_accrued,
    }


def main():
    print()
    print("=" * 70)
    print("  BUG 3: LP Fee Rounding Dust — Permanent Lock PoC")
    print("=" * 70)
    print()

    # ────────────────────────────────────────────────
    # Scenario 1: Small fees, large LP pool (worst case for dust)
    # ────────────────────────────────────────────────
    print("─" * 70)
    print("  Scenario 1: Small fees ($0.001), large LP pool ($10K)")
    print("─" * 70)
    result = simulate_fee_dust_accumulation(
        num_trades=10_000,
        fee_per_trade=1_000,       # 0.001 USDC (1000 microUSDC)
        lp_shares_total=10_000_000_000,  # ~$10K liquidity
        lp_holders={
            "creator": 5_000_000_000,    # 50%
            "lp_alice": 3_000_000_000,   # 30%
            "lp_bob": 2_000_000_000,     # 20%
        },
    )
    print_result(result)

    # ────────────────────────────────────────────────
    # Scenario 2: Moderate fees, 3 LPs with uneven shares
    # ────────────────────────────────────────────────
    print("─" * 70)
    print("  Scenario 2: Moderate fees ($0.10), 3 uneven LPs ($1K)")
    print("─" * 70)
    result = simulate_fee_dust_accumulation(
        num_trades=1_000,
        fee_per_trade=100_000,     # 0.10 USDC
        lp_shares_total=1_000_000_000,
        lp_holders={
            "creator": 333_333_333,   # ~33.3%
            "lp_alice": 333_333_333,  # ~33.3%
            "lp_bob": 333_333_334,    # ~33.3%
        },
    )
    print_result(result)

    # ────────────────────────────────────────────────
    # Scenario 3: Large fees, 2 LPs (minimal dust)
    # ────────────────────────────────────────────────
    print("─" * 70)
    print("  Scenario 3: Large fees ($1.00), 2 equal LPs ($1K)")
    print("─" * 70)
    result = simulate_fee_dust_accumulation(
        num_trades=500,
        fee_per_trade=1_000_000,   # 1.00 USDC
        lp_shares_total=1_000_000_000,
        lp_holders={
            "creator": 500_000_000,
            "lp_alice": 500_000_000,
        },
    )
    print_result(result)

    # ────────────────────────────────────────────────
    # Scenario 4: Extreme case — prime-number LP shares
    # ────────────────────────────────────────────────
    print("─" * 70)
    print("  Scenario 4: Extreme — prime LP shares, tiny fees, 50K trades")
    print("─" * 70)
    result = simulate_fee_dust_accumulation(
        num_trades=50_000,
        fee_per_trade=7,           # 0.000007 USDC (7 microUSDC)
        lp_shares_total=7_919_999,  # prime-ish number
        lp_holders={
            "creator": 3_000_001,
            "lp_alice": 2_919_999,
            "lp_bob": 1_999_999,
        },
    )
    print_result(result)

    # ────────────────────────────────────────────────
    # Verdict
    # ────────────────────────────────────────────────
    print("=" * 70)
    print("  VERDICT")
    print("=" * 70)
    print()
    print("  _distribute_lp_fee() uses floor division to compute the per-share")
    print("  fee increment. _settle_lp_fees() again uses floor division when")
    print("  computing each LP's accrued fees. Both rounding steps lose dust.")
    print()
    print("  The gap between lp_fee_balance and sum-of-all-LP-entitlements")
    print("  grows monotonically and is permanently locked in the contract.")
    print()
    print("  Impact: Permanent lock of LP fees. In worst-case scenarios")
    print("  (small fees, many trades, uneven LP shares), the locked amount")
    print("  can reach meaningful fractions of total fees.")
    print()
    print("  Qualifies under bounty scope: 'affect user funds — permanent lock'")
    print()


def print_result(r: dict):
    print(f"  Trades:         {r['num_trades']:>12,}")
    print(f"  Fee/trade:      {r['fee_per_trade']:>12,} μUSDC ({r['fee_per_trade']/1e6:.6f} USDC)")
    print(f"  Total fees:     {r['lp_fee_balance']:>12,} μUSDC ({r['lp_fee_balance']/1e6:.4f} USDC)")
    print(f"  Total claimable:{r['total_claimable']:>12,} μUSDC")
    print(f"  DUST LOCKED:    {r['dust_locked']:>12,} μUSDC ({r['dust_locked']/1e6:.6f} USDC)")
    print(f"  Dust %:         {r['dust_pct']:>11.4f}%")
    print(f"  Per-LP claims:")
    for name, amount in r['per_lp'].items():
        print(f"    {name:15s}: {amount:>12,} μUSDC")
    print()


if __name__ == "__main__":
    main()

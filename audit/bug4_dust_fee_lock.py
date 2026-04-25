"""
Proof-of-Concept: Permanent Lock of LP Fees (Dust Rounding)

Demonstrates that the LP fee distribution mechanism (`_distribute_lp_fee`)
rounds down to the nearest integer. In markets with high liquidity (large b),
small individual trades generate fees that round to 0 in the
`cumulative_fee_per_share` global state. While the `lp_fee_balance` (total USDC)
is updated, LPs can never claim these "lost" portions because the per-share
index never increments.

Impact:
  - Permanent lock of a portion of user funds (LP fees).
  - Systematic extraction of value from LPs to the contract "slack" (app account).

Reference:
  - smart_contracts/market_app/contract.py: _distribute_lp_fee() (lines 606-610)

Author: bounty audit
"""
import sys

SCALE = 1_000_000

def simulate_dust_fee_lock():
    print("=" * 60)
    print("  Simulating LP Fee Dust Rounding / Permanent Lock")
    print("=" * 60)

    # 1. Setup a high-liquidity market
    # b = 1,000,000 USDC = 1,000,000,000,000 microUSDC
    b = 1_000_000 * SCALE
    lp_shares_total = b
    
    cumulative_fee_per_share = 0
    lp_fee_balance = 0
    
    print(f"  Liquidity (b):       {b/SCALE:>15,.0f} USDC")
    print(f"  Total LP Shares:     {lp_shares_total:>15,}\n")

    # 2. A trade occurs that generates a small fee
    # e.g., A trade worth 0.50 USDC with 0.1% LP fee -> 0.0005 USDC = 500 microUSDC
    fee_amount = 500 
    
    print(f"  Incoming LP Fee:     {fee_amount:>15,} microUSDC")
    
    # Contract Logic:
    # 607: self.lp_fee_balance.value += fee_amount
    lp_fee_balance += fee_amount
    
    # 609: increment = (fee_amount * SCALE) // lp_shares_total
    increment = (fee_amount * SCALE) // lp_shares_total
    
    # 610: self.cumulative_fee_per_share.value += increment
    cumulative_fee_per_share += increment
    
    print(f"  Calculated Increment: {increment:>15,} (ROUNDED TO ZERO)")
    print(f"  New Cumulative Index: {cumulative_fee_per_share:>15,}")
    print(f"  New LP Fee Balance:   {lp_fee_balance:>15,} microUSDC\n")

    # 3. LP attempts to claim fees
    # payout = (cumulative - snapshot) * shares // SCALE
    lp_shares = 100_000 * SCALE # Holds 10% of liquidity
    payout = (cumulative_fee_per_share - 0) * lp_shares // SCALE
    
    print(f"  LP (10% share) claims:")
    print(f"  Actual Payout:        {payout:>15,} microUSDC")
    print(f"  Theoretical Payout:   { (fee_amount * 0.1) :>15.1f} microUSDC")
    
    print(f"\n  Result: {fee_amount} microUSDC is sitting in `lp_fee_balance`, but")
    print("  because the index was not updated, NO LP CAN EVER WITHDRAW IT.")
    print("  The funds are permanently locked in the app account.")
    print()

if __name__ == "__main__":
    print()
    simulate_dust_fee_lock()

    print("=" * 60)
    print("  VERDICT")
    print("=" * 60)
    print()
    print("  The integer division in `_distribute_lp_fee` causes small fees in")
    print("  deep markets to be lost. These funds accumulate in the contract balance")
    print("  but cannot be recovered by LPs or the admin.")
    print()
    print("  Impact: Permanent lock of user funds (LP fee revenue).")
    print("  Qualifies under bounty scope: 'affect user funds  permanent lock'")
    print()
    sys.exit(0)

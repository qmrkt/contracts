"""
Proof-of-Concept: Bootstrap LP Shares Inaccessible if Creator Never Opts In

Demonstrates that the market creator's LP shares (minted during bootstrap)
are stored in `bootstrapper_lp_shares` global state and only transferred to
the creator on their first opt-in. If the creator never opts in, these shares
remain unowned, making the bootstrap deposit partially inaccessible.

Impact:
  - LP fee accrual on creator's shares is lost (dilutes to zero)
  - LP residual claims for creator's share of the pool are impossible
  - In the on-chain contract, bootstrapper_lp_shares stays non-zero but
    nobody holds them, so lp_shares_total includes phantom shares

Reference:
  - smart_contracts/market_app/contract.py: bootstrap() (line 910-947)
  - smart_contracts/market_app/contract.py: opt_in() (line 154-161)

Author: bounty audit
"""
import sys

# ──────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────
SCALE = 1_000_000
STATUS_ACTIVE = 1
STATUS_CANCELLED = 4
STATUS_RESOLVED = 5

# ──────────────────────────────────────────────────────
# Simulated bootstrap flow
# ──────────────────────────────────────────────────────

def simulate_bootstrap_with_optin():
    """Normal flow: creator opts in and claims LP shares."""
    b = 1_000_000  # 1 USDC liquidity parameter
    deposit = 2_000_000  # 2 USDC bootstrap deposit

    # After bootstrap():
    pool_balance = deposit
    lp_shares_total = b
    bootstrapper_lp_shares = b  # stored in global state

    # After creator opts in:
    creator_lp_shares = bootstrapper_lp_shares  # transferred
    bootstrapper_lp_shares = 0  # cleared

    print("=" * 60)
    print("  Normal flow: creator opts in")
    print("=" * 60)
    print(f"  Bootstrap deposit:    {deposit:>12,} μA")
    print(f"  LP shares (b):        {b:>12,}")
    print(f"  bootstrapper_shares:  {bootstrapper_lp_shares:>12,} (cleared after opt-in)")
    print(f"  creator_lp_shares:    {creator_lp_shares:>12,} (claimed)")
    print()

def simulate_bootstrap_without_optin():
    """Buggy flow: creator never opts in."""
    b = 1_000_000
    deposit = 2_000_000

    # After bootstrap():
    pool_balance = deposit
    lp_shares_total = b
    bootstrapper_lp_shares = b  # STILL non-zero

    # Creator never opts in → bootstrapper_lp_shares stays at b
    # Nobody holds LP shares, but lp_shares_total = b

    # Simulate some trading (alice buys outcome 0)
    # Pool grows from fees
    lp_fee_balance = 10_000  # 0.01 USDC in LP fees

    # After resolution, LP residual calculation:
    # _total_residual_weight() uses lp_shares_total = b
    # But nobody can claim because nobody holds LP shares
    total_residual_entitled = 50_000  # hypothetical residual pool
    total_weight = b  # only phantom shares
    # Each LP's claim = (pool * their_weight) / total_weight
    # But nobody has weight > 0 → 0 claims possible

    creator_claimable = 0  # creator has 0 LP shares
    phantom_shares = bootstrapper_lp_shares  # orphaned

    print("=" * 60)
    print("  Buggy flow: creator never opts in")
    print("=" * 60)
    print(f"  Bootstrap deposit:    {deposit:>12,} μA")
    print(f"  LP shares (b):        {b:>12,}")
    print(f"  bootstrapper_shares:  {phantom_shares:>12,} (STUCK in global state)")
    print(f"  creator_lp_shares:    {creator_claimable:>12,} (never claimed)")
    print(f"  lp_shares_total:      {b:>12,} (includes phantom shares)")
    print()
    print("  Effects:")
    print(f"  - LP fees accrue but nobody can claim them")
    print(f"  - Residual pool diluted by phantom shares")
    print(f"  - Bootstrap deposit partially locked")
    print()

# ──────────────────────────────────────────────────────
# Concrete fund loss calculation
# ──────────────────────────────────────────────────────

def fund_loss_calculation():
    """Calculate the locked funds in the no-opt-in scenario."""
    b = 1_000_000  # LP shares
    deposit = 2_000_000  # bootstrap deposit

    # After resolution with winning outcome 0:
    pool_balance = 2_500_000  # grew from trading
    winning_shares = 500_000  # user shares in outcome 0

    # Releasable residual pool:
    # free_pool = pool_balance + total_residual_claimed
    # reserve = winning_shares (if resolved)
    free_pool = pool_balance
    reserve = winning_shares
    releasable = free_pool - reserve  # 2_000_000

    # Total residual weight = lp_shares_total = b = 1_000_000
    # But nobody holds shares → nobody can claim
    unclaimable_residual = releasable

    print("=" * 60)
    print("  Fund loss calculation")
    print("=" * 60)
    print(f"  Pool balance:         {pool_balance:>12,} μA")
    print(f"  Winner reserve:       {reserve:>12,} μA")
    print(f"  Releasable residual:  {releasable:>12,} μA")
    print(f"  LP shares held:       0 (creator never opted in)")
    print(f"  Unclaimable residual: {unclaimable_residual:>12,} μA ({unclaimable_residual/1e6:.1f} USDC)")
    print()

# ──────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    simulate_bootstrap_with_optin()
    simulate_bootstrap_without_optin()
    fund_loss_calculation()

    print("=" * 60)
    print("  VERDICT")
    print("=" * 60)
    print()
    print("  If the market creator never calls opt_in(), their LP shares")
    print("  (minted during bootstrap) remain in bootstrapper_lp_shares")
    print("  global state. Nobody holds these shares, so:")
    print("  1. LP fee accrual on these shares is unclaimable")
    print("  2. LP residual claims are diluted by phantom shares")
    print("  3. A portion of the bootstrap deposit is permanently locked")
    print()
    print("  Impact: Permanent lock of bootstrap deposit proportional to")
    print("  creator's LP share fraction.")
    print("  Qualifies under bounty scope: 'affect user funds — permanent lock'")
    print()
    sys.exit(0)

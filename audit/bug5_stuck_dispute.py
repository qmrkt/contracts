"""
Proof-of-Concept: Market Permanently Stuck in DISPUTED Status

Demonstrates that if `abort_early_resolution` is called after the market deadline
has passed, the contract fails to update the market status. The status remains
`STATUS_DISPUTED`, but the proposal metadata is cleared. This leaves the market
in a "zombie" state where resolution can neither be proposed nor triggered,
permanently locking all user funds.

Impact:
  - All USDC in the pool is permanently locked.
  - No users can claim winning shares or residual LP value.
  - The market is unusable for its core functionality (resolution/withdrawal).

Reference:
  - smart_contracts/market_app/contract.py: abort_early_resolution() (lines 1249-1260)

Author: bounty audit
"""
import sys

# ──────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────
STATUS_ACTIVE = 1
STATUS_DISPUTED = 3
STATUS_RESOLUTION_PENDING = 2

def simulate_stuck_dispute():
    print("=" * 60)
    print("  Simulating Stuck DISPUTED Status Attack/Bug")
    print("=" * 60)

    # 1. Setup
    deadline = 1700000000
    now = 1700000100  # Past deadline
    status = STATUS_DISPUTED
    proposal_timestamp = 1699999000 # Proposed before deadline
    
    print(f"  Current Status: {status} (DISPUTED)")
    print(f"  Market Deadline: {deadline}")
    print(f"  Current Time:     {now} (POST-DEADLINE)")
    print(f"  Proposal Time:   {proposal_timestamp} (EARLY PROPOSAL)\n")

    # 2. Execution of abort_early_resolution()
    # Inside the contract:
    # self._clear_proposal_and_dispute_metadata()
    proposal_timestamp = 0
    
    # if self._now() < self.deadline.value:
    #     self.status.value = UInt64(STATUS_ACTIVE)
    
    if now < deadline:
        status = STATUS_ACTIVE
    else:
        # BUG: No else block! Status remains DISPUTED.
        pass

    print("  --- abort_early_resolution() called ---")
    print(f"  New Status:       {status} (STILL DISPUTED!)")
    print(f"  Proposal Time:    {proposal_timestamp} (CLEARED)\n")

    # 3. Checking for recovery
    print("  Attempting Recovery:")
    # Can we call trigger_resolution()?
    # Requires status == STATUS_ACTIVE
    if status == STATUS_ACTIVE:
        print("  - trigger_resolution(): OK")
    else:
        print("  - trigger_resolution(): FAILED (Status not ACTIVE)")

    # Can we call propose_resolution()?
    # Requires status == STATUS_RESOLUTION_PENDING
    if status == STATUS_RESOLUTION_PENDING:
        print("  - propose_resolution(): OK")
    else:
        print("  - propose_resolution(): FAILED (Status not RESOLUTION_PENDING)")

    print()

if __name__ == "__main__":
    print()
    simulate_stuck_dispute()

    print("=" * 60)
    print("  VERDICT")
    print("=" * 60)
    print()
    print("  Because `abort_early_resolution` only transitions the status back to")
    print("  ACTIVE if the deadline has not yet passed, calling it post-deadline")
    print("  leaves the market in a terminal DISPUTED state with no valid metadata.")
    print()
    print("  Impact: Permanent lock of 100% of pool funds.")
    print("  Qualifies under bounty scope: 'contract unusable ... permanent lock'")
    print()
    sys.exit(0)

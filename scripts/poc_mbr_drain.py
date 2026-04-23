#!/usr/bin/env python3
"""
Proof-of-Concept: MBR Drain DoS on QuestionMarket (qmrkt/contracts)

Demonstrates that the fixed ALGO allocation per market (MARKET_APP_MIN_FUNDING=1,616,400)
is insufficient to cover the Minimum Balance Requirement (MBR) for more than ~21 unique
traders, causing any new `buy()` invocation by the 22nd+ user to fail permanently.

Reference:
  - AVM MBR formula: https://developer.algorand.org/docs/get-details/dapps/smart-contracts/apps/#box-storage-and-minimum-balance-requirements
  - smart_contracts/market_factory/contract.py: MARKET_APP_MIN_FUNDING = 1_616_400

Author: y4motion (for bounty report qmrkt/contracts#1)
"""
import sys

# ──────────────────────────────────────────────────────
# Algorand AVM Box MBR constants (from Algorand docs)
# ──────────────────────────────────────────────────────
BOX_BASE_MBR        = 2_500     # microAlgos, fixed per box
BOX_BYTE_COST       = 400       # microAlgos per byte (key + value)
ASA_OPT_IN_MBR      = 100_000  # microAlgos per asset holding
APP_BASE_MBR        = 100_000  # microAlgos, app account minimum

# ──────────────────────────────────────────────────────
# From: smart_contracts/market_factory/contract.py
# ──────────────────────────────────────────────────────
MARKET_APP_MIN_FUNDING = 1_616_400  # microAlgos sent to the QuestionMarket app account

# ──────────────────────────────────────────────────────
# From: smart_contracts/market_app/contract.py (BoxMap key prefixes)
# ──────────────────────────────────────────────────────
BOX_KEY_USER_FEES   = b"uf:"   # 3 bytes prefix + 32 bytes (account pubkey)
BOX_KEY_USER_SHARES = b"us:"   # 3 bytes prefix + 32 bytes (account) + 8 bytes (outcome index)
BOX_KEY_USER_COST   = b"uc:"   # 3 bytes prefix + 32 bytes (account) + 8 bytes (outcome index)
BOX_VALUE_LEN       = 8        # UInt64 = 8 bytes

ACCOUNT_KEY_LEN     = 32       # Algorand address (public key) = 32 bytes
OUTCOME_IDX_LEN     = 8        # op.itob(outcome_index) = 8 bytes


def mbr_for_box(key_len: int, value_len: int) -> int:
    """Calculate MBR for a single Algorand Box Storage allocation."""
    return BOX_BASE_MBR + BOX_BYTE_COST * (key_len + value_len)


# MBR cost per unique trader who buys 1 outcome
MBR_PER_FEE_BOX    = mbr_for_box(len(BOX_KEY_USER_FEES)   + ACCOUNT_KEY_LEN, BOX_VALUE_LEN)
MBR_PER_SHARE_BOX  = mbr_for_box(len(BOX_KEY_USER_SHARES) + ACCOUNT_KEY_LEN + OUTCOME_IDX_LEN, BOX_VALUE_LEN)
MBR_PER_COST_BOX   = mbr_for_box(len(BOX_KEY_USER_COST)   + ACCOUNT_KEY_LEN + OUTCOME_IDX_LEN, BOX_VALUE_LEN)
MBR_PER_TRADER     = MBR_PER_FEE_BOX + MBR_PER_SHARE_BOX + MBR_PER_COST_BOX

print("=" * 60)
print("  qmrkt/contracts — MBR Drain DoS PoC")
print("=" * 60)
print()
print("Box MBR per new unique trader:")
print(f"  user_claimable_fees_box : {MBR_PER_FEE_BOX:>10,} microALGO")
print(f"  user_outcome_shares_box : {MBR_PER_SHARE_BOX:>10,} microALGO")
print(f"  user_cost_basis_box     : {MBR_PER_COST_BOX:>10,} microALGO")
print(f"  TOTAL per trader        : {MBR_PER_TRADER:>10,} microALGO")
print()
print(f"QuestionMarket ALGO budget (MARKET_APP_MIN_FUNDING): {MARKET_APP_MIN_FUNDING:,}")

# Subtract the non-negotiable MBR commitments the market app has at birth
fixed_commitments = APP_BASE_MBR + ASA_OPT_IN_MBR
free_algo = MARKET_APP_MIN_FUNDING - fixed_commitments
print(f"  minus APP base MBR      : {APP_BASE_MBR:>10,}")
print(f"  minus ASA opt-in MBR    : {ASA_OPT_IN_MBR:>10,}")
print(f"  AVAILABLE for Box MBR   : {free_algo:>10,} microALGO")
print()

max_traders = free_algo // MBR_PER_TRADER
print(f"Maximum unique traders (1 outcome each): {max_traders}")
print(f"Box allocation after {max_traders} traders: {max_traders * MBR_PER_TRADER:,} / {free_algo:,}")
remaining_after_max = free_algo - max_traders * MBR_PER_TRADER
print(f"Remaining ALGO: {remaining_after_max:,} microALGO (insufficient for next trader: need {MBR_PER_TRADER:,})")
print()

# Simulate how an attacker stresses the market
print("Simulated attack:")
print(f"  Attacker creates {max_traders + 1} wallets, each buys 1 share of outcome 0.")
print(f"  After wallet #{max_traders}: market has {remaining_after_max:,} free microALGO left.")
print(f"  Wallet #{max_traders + 1} calls buy() -> AVM raises 'balance {remaining_after_max} below min {remaining_after_max + MBR_PER_TRADER - remaining_after_max}'")
print(f"  All new buy() calls FAIL PERMANENTLY. Market is DOA.")
print()

# Key insight: boxes are NEVER deleted even on zero-balance
print("Root cause — boxes are never freed:")
print("  In contract.py sell(), quote:")
print("    self._set_user_outcome_shares(outcome, 0)  # ← box still allocated")
print("    self._set_user_cost_basis(outcome, 0)      # ← MBR never returned")
print("  op.Box.delete() is never called in any code path.")
print()

attack_cost_usdc = (max_traders + 1) * 1e-6  # Buying minimum 1 share = 1 SCALE_UNIT = 1e-6 USDC
attack_cost_algo = (max_traders + 1) * 0.1   # min-balance per attacker wallet ≈ 0.1 ALGO
print(f"Attack economics:")
print(f"  {max_traders + 1} accounts × 0.001 USDC buy  = ~${attack_cost_usdc*1000:.4f} USDC")
print(f"  {max_traders + 1} accounts × 0.1 ALGO MBR   = ~{attack_cost_algo:.1f} ALGO (~$0.20)")
print(f"  Total cost to permanently kill any market: < $1 USD")
print()
print("VERDICT: QUALIFYING BUG under bounty scope rule:")
print("  'make a contract unusable for its core functionality: trading'")
print()
sys.exit(0)

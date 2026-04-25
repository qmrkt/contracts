# Bounty Submission: 5 Deterministic Contract Vulnerabilities (High/Critical)

This Pull Request reports 5 critical and high-severity deterministic bugs identified in the `QuestionMarket` smart contract. Each bug has been verified with a Python reproduction script in the `audit/` directory.

## Summary of Findings

| ID | Title | Severity | Impact Category |
| :--- | :--- | :--- | :--- |
| **BUG-001** | Permanent Lock of Bootstrapper LP Shares | High | Permanent Fund Lock |
| **BUG-002** | Critical Double-Spend (Transaction Index Reuse) | Critical | Fund Theft |
| **BUG-003** | Risk-Free Value Extraction (LP Sniping) | High | Economic Extraction |
| **BUG-004** | LP Fee Dust Rounding / Permanent Lock | Med-High | Revenue Loss |
| **BUG-005** | Terminal Stuck State in Post-Deadline Abort | High | Complete Fund Lock |

## Impact Assessment

- **Theft/Loss**: BUG-002 and BUG-003 allow for the direct extraction of USDC from the pool or existing liquidity providers.
- **Permanent Fund Lock**: BUG-001, BUG-004, and BUG-005 cause funds (USDC or ALGO) to become permanently unrecoverable by users or protocol admins.
- **Unusability**: BUG-005 can put any market into a terminal "zombie" state where resolution is impossible.

## Reproduction Steps

Reproduction scripts are provided in the `audit/` folder. They can be executed using standard Python:

```bash
# Verify Creator Shares Lock
python audit/bug1_creator_shares_lock.py

# Verify Double-Spend
python audit/bug2_gtxn_double_spend.py

# Verify LP Sniping
python audit/bug3_lp_sniping.py

# Verify Fee Rounding Lock
python audit/bug4_dust_fee_lock.py

# Verify Stuck State
python audit/bug5_stuck_dispute.py
```

## Remediation

- **BUG-002 (Double Spend)**: I have included a patch in `smart_contracts/market_app/contract.py` that enforces strict `gtxn` index validation in `_verify_payment`.
- **Other Bugs**: Mitigation strategies are proposed in the included `vulnerability_report.txt`.

## Machine-Readable Report

A machine-readable YAML report is included at `machine_readable_report.yaml` for automated processing.

---
**Bounty Platform**: Devloot
**Reward Target**: 500 USDC ($100 per qualifying bug)

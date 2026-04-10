------------------------------ MODULE QuestionMarketCore ------------------------------
(* Shared definitions for QuestionMarket.tla: statuses, bond math, ASSUME clauses. *)
EXTENDS Integers, TLC

CONSTANTS
    BPS_DEN,
    WinnerShareBps,
    DisputeSinkShareBps,
    NoneUser

ASSUME
    /\ BPS_DEN > 0
    /\ WinnerShareBps >= 0
    /\ DisputeSinkShareBps >= 0
    /\ WinnerShareBps + DisputeSinkShareBps <= BPS_DEN

(* Matches on-chain status labels (contract.py). *)
MarketStatuses ==
    {"CREATED", "ACTIVE", "RESOLUTION_PENDING", "RESOLUTION_PROPOSED", "DISPUTED", "CANCELLED", "RESOLVED"}

WinningDomain(Outcomes) == Outcomes \cup {-1}

(* Losing party's bond is split: winner_bonus to winner, remainder to dispute sink (see _winner_bonus_from_bond). *)
WinnerBonusFromBond(losingBond) == (losingBond * WinnerShareBps) \div BPS_DEN

SinkCaptureFromLosingBond(losingBond) == losingBond - WinnerBonusFromBond(losingBond)

=============================================================================

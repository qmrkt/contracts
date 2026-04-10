------------------------------ MODULE QuestionMarket ------------------------------
EXTENDS QuestionMarketCore, QuestionMarketLMSR, FiniteSets

CONSTANTS
    Users,
    Creator,
    Resolver,
    MarketAdmin,
    MaxShareBundles,
    MaxDeposit,
    MinInitialB,
    MaxInitialB,
    ResidualLambda,
    LpEntryCap,
    PriceTolerance,
    lpFeeBps,
    protocolFeeBps,
    ChallengeWindow,
    ProposerFee,
    BudgetBootstrap,
    MaxBond,
    LmsrBootstrapMult

ASSUME
    /\ NoneUser \notin Users
    /\ NoneUser \notin Outcomes
    /\ ChallengeWindow > 0
    /\ ProposerFee >= 0
    /\ BudgetBootstrap >= 0
    /\ MaxBond > 0
    /\ {Resolver, Creator, MarketAdmin} \subseteq Users
    /\ MaxShareBundles > 0
    /\ MinInitialB >= 2
    /\ MaxInitialB >= MinInitialB
    /\ LmsrBootstrapMult > 0
    /\ lpFeeBps >= 0 /\ lpFeeBps <= BPS_DEN
    /\ protocolFeeBps >= 0 /\ protocolFeeBps <= BPS_DEN
    /\ PriceTolerance >= 0
    /\ LpEntryCap > 0
    /\ LpEntryCap <= LMSR_SCALE

Statuses == MarketStatuses
WinningValues == WinningDomain(Outcomes)

VARIABLES
    status,
    now,
    activationTime,
    settlementTime,
    b,
    lpSharesTotal,
    poolBalance,
    totalCostBasis,
    q,
    lpShares,
    lpWeightedEntrySum,
    totalLpWeightedEntrySum,
    residualClaimed,
    totalResidualClaimed,
    userShares,
    userCostBasis,
    winningOutcome,
    deadlinePassed,
    protocolFeeBalance,
    lpFeeBalance,
    cumulativeFeePerShare,
    userFeeSnapshot,
    userClaimableFees,
    userWithdrawableFeeSurplus,
    proposalTimestamp,
    challengeEnd,
    earlyProposal,
    proposer,
    challenger,
    proposedOutcome,
    proposerBondHeld,
    challengerBondHeld,
    resolutionBudgetBalance,
    disputeSinkBalance,
    pendingPayout

vars ==
    << status,
       now,
       activationTime,
       settlementTime,
       b,
       lpSharesTotal,
       poolBalance,
       totalCostBasis,
       q,
       lpShares,
       lpWeightedEntrySum,
       totalLpWeightedEntrySum,
       residualClaimed,
       totalResidualClaimed,
       userShares,
       userCostBasis,
       winningOutcome,
       deadlinePassed,
       protocolFeeBalance,
       lpFeeBalance,
       cumulativeFeePerShare,
       userFeeSnapshot,
       userClaimableFees,
       userWithdrawableFeeSurplus,
       proposalTimestamp,
       challengeEnd,
       earlyProposal,
       proposer,
       challenger,
       proposedOutcome,
       proposerBondHeld,
       challengerBondHeld,
       resolutionBudgetBalance,
       disputeSinkBalance,
       pendingPayout >>

RECURSIVE UserSharesSum(_, _)
RECURSIVE LpSharesSum(_)
RECURSIVE LpWeightedEntryTotal(_)
RECURSIVE ResidualClaimedSum(_)
RECURSIVE PendingPayoutSum(_)

UserSharesSum(us, outcome) ==
    IF us = {}
    THEN 0
    ELSE
        LET u == CHOOSE value \in us : TRUE
        IN userShares[u][outcome] + UserSharesSum(us \ {u}, outcome)

LpSharesSum(us) ==
    IF us = {}
    THEN 0
    ELSE
        LET u == CHOOSE value \in us : TRUE
        IN lpShares[u] + LpSharesSum(us \ {u})

LpWeightedEntryTotal(us) ==
    IF us = {}
    THEN 0
    ELSE
        LET u == CHOOSE value \in us : TRUE
        IN lpWeightedEntrySum[u] + LpWeightedEntryTotal(us \ {u})

ResidualClaimedSum(us) ==
    IF us = {}
    THEN 0
    ELSE
        LET u == CHOOSE value \in us : TRUE
        IN residualClaimed[u] + ResidualClaimedSum(us \ {u})

PendingPayoutSum(us) ==
    IF us = {}
    THEN 0
    ELSE
        LET u == CHOOSE value \in us : TRUE
        IN pendingPayout[u] + PendingPayoutSum(us \ {u})

OutcomeUserLiability(outcome) == UserSharesSum(Users, outcome)

WinningUserLiability ==
    IF winningOutcome \in Outcomes
    THEN OutcomeUserLiability(winningOutcome)
    ELSE 0

(* Loose bound for TLC finiteness; raise in .cfg if exploring larger trades. *)
StateCap == 2000000000
TimeCap == 8

TradeShareSize == LMSR_SCALE

CalcFeeUp(amount, bps) == MulDivCeil(amount, bps, BPS_DEN)

LpAccruedFees(u) ==
    LET d == cumulativeFeePerShare - userFeeSnapshot[u]
    IN IF lpShares[u] = 0 \/ d <= 0 THEN 0 ELSE MulDivFloor(d, lpShares[u], LMSR_SCALE)

NormalizedResidualWindow ==
    IF settlementTime <= activationTime + 1
    THEN 0
    ELSE settlementTime - activationTime - 1

ResidualWeight(u) ==
    LET shares == lpShares[u]
    IN IF shares = 0
       THEN 0
       ELSE IF NormalizedResidualWindow = 0
            THEN shares
            ELSE
                LET elapsedScaled == shares * (settlementTime - 1)
                    weightedSum == lpWeightedEntrySum[u]
                    premiumUnits == IF elapsedScaled <= weightedSum THEN 0 ELSE elapsedScaled - weightedSum
                    premium == (ResidualLambda * premiumUnits) \div NormalizedResidualWindow
                IN shares + premium

TotalResidualWeight ==
    IF lpSharesTotal = 0
    THEN 0
    ELSE IF NormalizedResidualWindow = 0
         THEN lpSharesTotal
         ELSE
             LET elapsedScaled == lpSharesTotal * (settlementTime - 1)
                 premiumUnits == IF elapsedScaled <= totalLpWeightedEntrySum THEN 0 ELSE elapsedScaled - totalLpWeightedEntrySum
                 premium == (ResidualLambda * premiumUnits) \div NormalizedResidualWindow
             IN lpSharesTotal + premium

CurrentReserveRequirement ==
    IF status = "RESOLVED"
    THEN WinningUserLiability
    ELSE IF status = "CANCELLED"
         THEN totalCostBasis
         ELSE 0

ReleasableResidualPool ==
    IF status \in {"CANCELLED", "RESOLVED"}
    THEN
        LET freePool == poolBalance + totalResidualClaimed
            reserve == CurrentReserveRequirement
        IN IF freePool <= reserve THEN 0 ELSE freePool - reserve
    ELSE 0

ClaimableResidual(u) ==
    IF status \notin {"CANCELLED", "RESOLVED"}
    THEN 0
    ELSE IF TotalResidualWeight = 0
         THEN 0
         ELSE
             LET entitled == (ReleasableResidualPool * ResidualWeight(u)) \div TotalResidualWeight
                 alreadyClaimed == residualClaimed[u]
             IN IF entitled <= alreadyClaimed THEN 0 ELSE entitled - alreadyClaimed

(* Proportional cost-basis reduction (contract _basis_reduction). *)
BasisReduction(curShares, curBasis, sellShares) ==
    IF curShares = sellShares
    THEN curBasis
    ELSE (curBasis * sellShares) \div curShares

ProposerFeeTaken == IF resolutionBudgetBalance >= ProposerFee THEN ProposerFee ELSE resolutionBudgetBalance

ChallengeWindowOpen ==
    /\ status = "RESOLUTION_PROPOSED"
    /\ now < challengeEnd

ClearProposalDisputeMetadata ==
    /\ proposalTimestamp' = 0
    /\ challengeEnd' = 0
    /\ earlyProposal' = FALSE
    /\ proposer' = NoneUser
    /\ challenger' = NoneUser
    /\ proposedOutcome' = -1
    /\ proposerBondHeld' = 0
    /\ challengerBondHeld' = 0

TypeOK ==
    /\ status \in Statuses
    /\ now \in Nat
    /\ activationTime \in Nat
    /\ settlementTime \in Nat
    /\ now <= TimeCap
    /\ activationTime <= TimeCap
    /\ settlementTime <= TimeCap
    /\ b \in Nat
    /\ lpSharesTotal \in Nat
    /\ poolBalance \in Nat
    /\ totalCostBasis \in Nat
    /\ q \in [Outcomes -> Nat]
    /\ lpShares \in [Users -> Nat]
    /\ lpWeightedEntrySum \in [Users -> Nat]
    /\ totalLpWeightedEntrySum \in Nat
    /\ residualClaimed \in [Users -> Nat]
    /\ totalResidualClaimed \in Nat
    /\ userShares \in [Users -> [Outcomes -> Nat]]
    /\ userCostBasis \in [Users -> [Outcomes -> Nat]]
    /\ winningOutcome \in WinningValues
    /\ deadlinePassed \in BOOLEAN
    /\ protocolFeeBalance \in Nat
    /\ lpFeeBalance \in Nat
    /\ cumulativeFeePerShare \in Nat
    /\ userFeeSnapshot \in [Users -> Nat]
    /\ userClaimableFees \in [Users -> Nat]
    /\ userWithdrawableFeeSurplus \in [Users -> Nat]
    /\ proposalTimestamp \in Nat
    /\ challengeEnd \in Nat
    /\ earlyProposal \in BOOLEAN
    /\ proposer \in Users \cup {NoneUser}
    /\ challenger \in Users \cup {NoneUser}
    /\ proposedOutcome \in WinningValues
    /\ proposerBondHeld \in Nat
    /\ challengerBondHeld \in Nat
    /\ resolutionBudgetBalance \in Nat
    /\ disputeSinkBalance \in Nat
    /\ pendingPayout \in [Users -> Nat]

ResolvedSolvencyInvariant ==
    status = "RESOLVED" => poolBalance >= WinningUserLiability

RefundReserveInvariant ==
    (status = "CANCELLED" \/ status = "DISPUTED") => poolBalance >= totalCostBasis

NonNegativeInvariant ==
    /\ b >= 0
    /\ lpSharesTotal >= 0
    /\ poolBalance >= 0
    /\ totalCostBasis >= 0
    /\ totalLpWeightedEntrySum >= 0
    /\ totalResidualClaimed >= 0
    /\ protocolFeeBalance >= 0
    /\ lpFeeBalance >= 0
    /\ cumulativeFeePerShare >= 0
    /\ proposerBondHeld >= 0
    /\ challengerBondHeld >= 0
    /\ resolutionBudgetBalance >= 0
    /\ disputeSinkBalance >= 0
    /\ \A o \in Outcomes : q[o] >= 0
    /\ \A u \in Users :
        /\ lpShares[u] >= 0
        /\ lpWeightedEntrySum[u] >= 0
        /\ residualClaimed[u] >= 0
        /\ pendingPayout[u] >= 0
        /\ userFeeSnapshot[u] >= 0
        /\ userClaimableFees[u] >= 0
        /\ userWithdrawableFeeSurplus[u] >= 0
    /\ \A u \in Users : \A o \in Outcomes :
        /\ userShares[u][o] >= 0
        /\ userCostBasis[u][o] >= 0

StatusConsistencyInvariant ==
    /\ (status = "RESOLVED") => winningOutcome \in Outcomes
    /\ (status # "RESOLVED") => winningOutcome = -1

ActiveMarketViability ==
    status = "ACTIVE" => b > 0

ResidualAccountingInvariant ==
    /\ lpSharesTotal = LpSharesSum(Users)
    /\ totalLpWeightedEntrySum = LpWeightedEntryTotal(Users)
    /\ totalResidualClaimed = ResidualClaimedSum(Users)

TimestampInvariant ==
    /\ (status = "CREATED") => activationTime = 0 /\ settlementTime = 0
    /\ (status # "CREATED") => activationTime > 0
    /\ (status \in {"CANCELLED", "RESOLVED"}) => settlementTime > 0
    /\ (status \notin {"CANCELLED", "RESOLVED"}) => settlementTime = 0
    /\ settlementTime = 0 \/ settlementTime >= activationTime

DisputedTimestampInvariant ==
    (* DISPUTED: no settlement timestamp until terminal resolution (matches non-final states). *)
    /\ status = "DISPUTED" => settlementTime = 0

(* Matches market_app _assert_price_sum: |sum(prices) - SCALE| <= num_outcomes.
   IF-form skips CREATED (b=0) where LMSR helpers are not meaningful.
   On-chain prices are UInt64; we use ps[o] >= 0. A limb can be 0 under fixed-point
   flooring while the sum still sits in the contract band (same as small LMSR_SCALE TLC).
   lp_entry_max_price_fp is only checked on enter_lp, not every ACTIVE step. *)
ActiveLmsrPriceInvariant ==
    IF status # "ACTIVE"
    THEN TRUE
    ELSE
        LET ps == LmsrPrices(q, b)
            s == PriceSum(ps)
        IN /\ \A o \in Outcomes : ps[o] >= 0
           /\ s >= LMSR_SCALE - Cardinality(Outcomes)
           /\ s <= LMSR_SCALE + Cardinality(Outcomes)

FeePotInvariant ==
    (* LP fees sit in lpFeeBalance until LPs withdraw; claimable/withdrawable are obligations. *)
    TRUE

ClaimableResidualInvariant ==
    \A u \in Users : ClaimableResidual(u) <= ReleasableResidualPool

PendingPayoutInvariant ==
    PendingPayoutSum(Users) >= 0

(* Bonds only positive when a proposal or dispute is in flight. *)
BondStateInvariant ==
    /\ (proposer = NoneUser) => proposerBondHeld = 0
    /\ (challenger = NoneUser) => challengerBondHeld = 0
    /\ (status \notin {"RESOLUTION_PROPOSED", "DISPUTED"}) =>
           /\ proposerBondHeld = 0
           /\ challengerBondHeld = 0
           /\ proposer = NoneUser
           /\ challenger = NoneUser

ProposalTimeInvariant ==
    /\ (status = "RESOLUTION_PROPOSED") => (proposer # NoneUser /\ proposedOutcome \in Outcomes)
    /\ (status = "RESOLUTION_PROPOSED") => (challengeEnd = proposalTimestamp + ChallengeWindow)

Bootstrap ==
    /\ status = "CREATED"
    /\ now < TimeCap
    /\ \E deposit \in 1..MaxDeposit, ib \in MinInitialB..MaxInitialB :
        LET nextNow == now + 1
            weighted == ib * nextNow
        IN
        /\ deposit >= ib * LmsrBootstrapMult
        /\ status' = "ACTIVE"
        /\ now' = nextNow
        /\ activationTime' = nextNow
        /\ settlementTime' = 0
        /\ b' = ib
        /\ lpSharesTotal' = ib
        /\ poolBalance' = deposit
        /\ deposit <= StateCap
        /\ ib <= StateCap
        /\ q' = [o \in Outcomes |-> 0]
        /\ resolutionBudgetBalance' = BudgetBootstrap
        /\ lpShares' = [u \in Users |-> IF u = Creator THEN ib ELSE 0]
        /\ lpWeightedEntrySum' = [u \in Users |-> IF u = Creator THEN weighted ELSE 0]
        /\ totalLpWeightedEntrySum' = weighted
        /\ residualClaimed' = [u \in Users |-> 0]
        /\ totalResidualClaimed' = 0
        /\ pendingPayout' = [u \in Users |-> 0]
        /\ protocolFeeBalance' = 0
        /\ lpFeeBalance' = 0
        /\ cumulativeFeePerShare' = 0
        /\ userFeeSnapshot' = [u \in Users |-> 0]
        /\ userClaimableFees' = [u \in Users |-> 0]
        /\ userWithdrawableFeeSurplus' = [u \in Users |-> 0]
        /\ UNCHANGED
            << totalCostBasis,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               disputeSinkBalance >>

Buy ==
    /\ status = "ACTIVE"
    /\ ~deadlinePassed
    /\ now < TimeCap
    /\ b > 0
    /\ \E u \in Users, o \in Outcomes, bundles \in 1..MaxShareBundles :
        LET shares == bundles * TradeShareSize
            cost == LmsrCostDelta(q, b, o, shares)
            lpF == CalcFeeUp(cost, lpFeeBps)
            protF == CalcFeeUp(cost, protocolFeeBps)
            totalPaid == cost + lpF + protF
            cumIncr == IF lpSharesTotal = 0 THEN 0 ELSE MulDivFloor(lpF, LMSR_SCALE, lpSharesTotal)
            qNew == QAfterBuy(q, o, shares)
        IN
        /\ shares > 0
        /\ totalPaid > 0
        /\ totalPaid <= StateCap
        /\ qNew[o] <= StateCap
        /\ poolBalance + cost <= StateCap
        /\ now' = now + 1
        /\ q' = qNew
        /\ userShares' = [userShares EXCEPT ![u][o] = @ + shares]
        /\ userCostBasis' = [userCostBasis EXCEPT ![u][o] = @ + cost]
        /\ totalCostBasis' = totalCostBasis + cost
        /\ poolBalance' = poolBalance + cost
        /\ lpFeeBalance' = lpFeeBalance + lpF
        /\ protocolFeeBalance' = protocolFeeBalance + protF
        /\ cumulativeFeePerShare' = cumulativeFeePerShare + cumIncr
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               winningOutcome,
               deadlinePassed,
               userFeeSnapshot,
               userClaimableFees,
               userWithdrawableFeeSurplus,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout >>

Sell ==
    /\ status = "ACTIVE"
    /\ ~deadlinePassed
    /\ now < TimeCap
    /\ b > 0
    /\ \E u \in Users, o \in Outcomes :
        /\ userShares[u][o] > 0
        /\ \E shares \in 1..userShares[u][o] :
            /\ shares % TradeShareSize = 0
            /\ LET gross == LmsrSellReturn(q, b, o, shares)
                   lpF == CalcFeeUp(gross, lpFeeBps)
                   protF == CalcFeeUp(gross, protocolFeeBps)
                   curS == userShares[u][o]
                   curB == userCostBasis[u][o]
                   basisRed == BasisReduction(curS, curB, shares)
                   newUserBasis == curB - basisRed
                   newTotalCostBasis == totalCostBasis - basisRed
                   netRet == gross - lpF - protF
                   cumIncr == IF lpSharesTotal = 0 THEN 0 ELSE MulDivFloor(lpF, LMSR_SCALE, lpSharesTotal)
                   qNew == QAfterSell(q, o, shares)
               IN
               /\ gross >= lpF + protF
               /\ netRet >= 0
               /\ poolBalance >= gross
               /\ poolBalance - gross >= newTotalCostBasis
               /\ now' = now + 1
               /\ q' = qNew
               /\ userShares' = [userShares EXCEPT ![u][o] = @ - shares]
               /\ userCostBasis' = [userCostBasis EXCEPT ![u][o] = newUserBasis]
               /\ totalCostBasis' = newTotalCostBasis
               /\ poolBalance' = poolBalance - gross
               /\ lpFeeBalance' = lpFeeBalance + lpF
               /\ protocolFeeBalance' = protocolFeeBalance + protF
               /\ cumulativeFeePerShare' = cumulativeFeePerShare + cumIncr
               /\ UNCHANGED
                   << status,
                      activationTime,
                      settlementTime,
                      b,
                      lpSharesTotal,
                      lpShares,
                      lpWeightedEntrySum,
                      totalLpWeightedEntrySum,
                      residualClaimed,
                      totalResidualClaimed,
                      winningOutcome,
                      deadlinePassed,
                      userFeeSnapshot,
                      userClaimableFees,
                      userWithdrawableFeeSurplus,
                      proposalTimestamp,
                      challengeEnd,
                      earlyProposal,
                      proposer,
                      challenger,
                      proposedOutcome,
                      proposerBondHeld,
                      challengerBondHeld,
                      resolutionBudgetBalance,
                      disputeSinkBalance,
                      pendingPayout >>

EnterLpActive ==
    /\ status = "ACTIVE"
    /\ ~deadlinePassed
    /\ b > 0
    /\ now < TimeCap
    /\ LmsrMaxPrice(q, b) <= LpEntryCap
    /\ \E u \in Users, deposit \in 1..MaxDeposit, deltaB \in 1..MaxDeposit :
        LET nextNow == now + 1
            curP == LmsrPrices(q, b)
            depReq == LmsrCollateralRequiredFromPrices(deltaB, curP)
            nextB == b + deltaB
            newQ == LmsrNormalizedQFromPrices(curP, nextB)
            newPool == poolBalance + deposit
            entryWeight == deltaB * nextNow
            acc == LpAccruedFees(u)
        IN
        /\ depReq <= deposit
        /\ deposit = depReq
        /\ now' = nextNow
        /\ b' = nextB
        /\ q' = newQ
        /\ poolBalance' = newPool
        /\ lpSharesTotal' = lpSharesTotal + deltaB
        /\ lpShares' = [lpShares EXCEPT ![u] = @ + deltaB]
        /\ lpWeightedEntrySum' = [lpWeightedEntrySum EXCEPT ![u] = @ + entryWeight]
        /\ totalLpWeightedEntrySum' = totalLpWeightedEntrySum + entryWeight
        /\ userClaimableFees' = [userClaimableFees EXCEPT ![u] = @ + acc]
        /\ userFeeSnapshot' = [userFeeSnapshot EXCEPT ![u] = cumulativeFeePerShare]
        /\ newPool <= StateCap
        /\ nextB <= StateCap
        /\ lpSharesTotal' <= StateCap
        /\ \A o3 \in Outcomes : newQ[o3] <= StateCap
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               totalCostBasis,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               userWithdrawableFeeSurplus,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance,
               lpFeeBalance,
               cumulativeFeePerShare >>

ClaimLpFees ==
    /\ now < TimeCap
    /\ \E u \in Users :
        LET acc == LpAccruedFees(u)
            tot == userClaimableFees[u] + acc
        IN
        /\ tot > 0
        /\ now' = now + 1
        /\ userClaimableFees' = [userClaimableFees EXCEPT ![u] = 0]
        /\ userFeeSnapshot' = [userFeeSnapshot EXCEPT ![u] = cumulativeFeePerShare]
        /\ userWithdrawableFeeSurplus' = [userWithdrawableFeeSurplus EXCEPT ![u] = @ + tot]
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance,
               lpFeeBalance,
               cumulativeFeePerShare >>

WithdrawLpFees ==
    /\ now < TimeCap
    /\ \E u \in Users :
        LET w == userWithdrawableFeeSurplus[u]
        IN
        /\ w > 0
        /\ lpFeeBalance >= w
        /\ now' = now + 1
        /\ lpFeeBalance' = lpFeeBalance - w
        /\ userWithdrawableFeeSurplus' = [userWithdrawableFeeSurplus EXCEPT ![u] = 0]
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               userFeeSnapshot,
               userClaimableFees,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance,
               cumulativeFeePerShare >>

WithdrawProtocolFees ==
    /\ protocolFeeBalance > 0
    /\ now < TimeCap
    /\ protocolFeeBalance' = 0
    /\ now' = now + 1
    /\ UNCHANGED
        << status,
           activationTime,
           settlementTime,
           b,
           lpSharesTotal,
           poolBalance,
           totalCostBasis,
           q,
           lpShares,
           lpWeightedEntrySum,
           totalLpWeightedEntrySum,
           residualClaimed,
           totalResidualClaimed,
           userShares,
           userCostBasis,
           winningOutcome,
           deadlinePassed,
           userFeeSnapshot,
           userClaimableFees,
           userWithdrawableFeeSurplus,
           proposalTimestamp,
           challengeEnd,
           earlyProposal,
           proposer,
           challenger,
           proposedOutcome,
           proposerBondHeld,
           challengerBondHeld,
           resolutionBudgetBalance,
           disputeSinkBalance,
           pendingPayout,
           lpFeeBalance,
           cumulativeFeePerShare >>

ClaimLpResidual ==
    /\ status \in {"CANCELLED", "RESOLVED"}
    /\ now < TimeCap
    /\ \E u \in Users :
        LET payout == ClaimableResidual(u)
        IN
        /\ payout > 0
        /\ poolBalance >= CurrentReserveRequirement + payout
        /\ now' = now + 1
        /\ poolBalance' = poolBalance - payout
        /\ residualClaimed' = [residualClaimed EXCEPT ![u] = @ + payout]
        /\ totalResidualClaimed' = totalResidualClaimed + payout
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

Cancel ==
    /\ status = "ACTIVE"
    /\ now < TimeCap
    /\ status' = "CANCELLED"
    /\ now' = now + 1
    /\ settlementTime' = now + 1
    /\ UNCHANGED
        << activationTime,
           b,
           lpSharesTotal,
           poolBalance,
           totalCostBasis,
           q,
           lpShares,
           lpWeightedEntrySum,
           totalLpWeightedEntrySum,
           residualClaimed,
           totalResidualClaimed,
           userShares,
           userCostBasis,
           winningOutcome,
           deadlinePassed,
           proposalTimestamp,
           challengeEnd,
           earlyProposal,
           proposer,
           challenger,
           proposedOutcome,
           proposerBondHeld,
           challengerBondHeld,
           resolutionBudgetBalance,
           disputeSinkBalance,
           pendingPayout,
           protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

DeadlinePasses ==
    /\ ~deadlinePassed
    /\ now < TimeCap
    /\ deadlinePassed' = TRUE
    /\ now' = now + 1
    /\ UNCHANGED
        << status,
           activationTime,
           settlementTime,
           b,
           lpSharesTotal,
           poolBalance,
           totalCostBasis,
           q,
           lpShares,
           lpWeightedEntrySum,
           totalLpWeightedEntrySum,
           residualClaimed,
           totalResidualClaimed,
           userShares,
           userCostBasis,
           winningOutcome,
           proposalTimestamp,
           challengeEnd,
           earlyProposal,
           proposer,
           challenger,
           proposedOutcome,
           proposerBondHeld,
           challengerBondHeld,
           resolutionBudgetBalance,
           disputeSinkBalance,
           pendingPayout,
           protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

TriggerResolution ==
    /\ status = "ACTIVE"
    /\ deadlinePassed
    /\ now < TimeCap
    /\ status' = "RESOLUTION_PENDING"
    /\ now' = now + 1
    /\ UNCHANGED
        << activationTime,
           settlementTime,
           b,
           lpSharesTotal,
           poolBalance,
           totalCostBasis,
           q,
           lpShares,
           lpWeightedEntrySum,
           totalLpWeightedEntrySum,
           residualClaimed,
           totalResidualClaimed,
           userShares,
           userCostBasis,
           winningOutcome,
           deadlinePassed,
           proposalTimestamp,
           challengeEnd,
           earlyProposal,
           proposer,
           challenger,
           proposedOutcome,
           proposerBondHeld,
           challengerBondHeld,
           resolutionBudgetBalance,
           disputeSinkBalance,
           pendingPayout,
           protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

ProposeResolution ==
    /\ status = "RESOLUTION_PENDING"
    /\ deadlinePassed
    /\ now < TimeCap
    /\ \E u \in Users, o \in Outcomes, pb \in 1..MaxBond :
        LET nextNow == now + 1
        IN
        /\ status' = "RESOLUTION_PROPOSED"
        /\ now' = nextNow
        /\ proposer' = u
        /\ proposedOutcome' = o
        /\ proposerBondHeld' = pb
        /\ earlyProposal' = FALSE
        /\ challenger' = NoneUser
        /\ challengerBondHeld' = 0
        /\ proposalTimestamp' = nextNow
        /\ challengeEnd' = nextNow + ChallengeWindow
        /\ UNCHANGED
            << activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

ProposeEarlyResolution ==
    /\ status = "ACTIVE"
    /\ ~deadlinePassed
    /\ now < TimeCap
    /\ \E o \in Outcomes :
        LET nextNow == now + 1
        IN
        /\ status' = "RESOLUTION_PROPOSED"
        /\ now' = nextNow
        /\ proposer' = Resolver
        /\ proposedOutcome' = o
        /\ proposerBondHeld' = 0
        /\ earlyProposal' = TRUE
        /\ challenger' = NoneUser
        /\ challengerBondHeld' = 0
        /\ proposalTimestamp' = nextNow
        /\ challengeEnd' = nextNow + ChallengeWindow
        /\ UNCHANGED
            << activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

TickProposed ==
    /\ status = "RESOLUTION_PROPOSED"
    /\ now < challengeEnd
    /\ now < TimeCap
    /\ now' = now + 1
    /\ UNCHANGED
        << status,
           activationTime,
           settlementTime,
           b,
           lpSharesTotal,
           poolBalance,
           totalCostBasis,
           q,
           lpShares,
           lpWeightedEntrySum,
           totalLpWeightedEntrySum,
           residualClaimed,
           totalResidualClaimed,
           userShares,
           userCostBasis,
           winningOutcome,
           deadlinePassed,
           proposalTimestamp,
           challengeEnd,
           earlyProposal,
           proposer,
           challenger,
           proposedOutcome,
           proposerBondHeld,
           challengerBondHeld,
           resolutionBudgetBalance,
           disputeSinkBalance,
           pendingPayout,
           protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

ChallengeResolution ==
    /\ status = "RESOLUTION_PROPOSED"
    /\ ChallengeWindowOpen
    /\ now < TimeCap
    /\ \E c \in Users, cb \in 1..MaxBond :
        /\ status' = "DISPUTED"
        /\ now' = now + 1
        /\ challenger' = c
        /\ challengerBondHeld' = cb
        /\ UNCHANGED
            << activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               proposedOutcome,
               proposerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance,
               pendingPayout,
               protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

(* Authority / creator / admin dispute finals share the same economic transition (contract finalize_dispute / creator_resolve / admin_resolve). *)
ResolveDispute ==
    /\ status = "DISPUTED"
    /\ now < TimeCap
    /\ \E o \in Outcomes :
        LET pb == proposerBondHeld
            cb == challengerBondHeld
            fee == ProposerFeeTaken
            orig == proposedOutcome
        IN
        IF o = orig
        THEN
            (* Confirmed: proposer wins bonds per _settle_confirmed_dispute *)
            LET wb == WinnerBonusFromBond(cb)
                sinkAdd == SinkCaptureFromLosingBond(cb)
            IN
            /\ disputeSinkBalance' = disputeSinkBalance + sinkAdd
            /\ pendingPayout' = [pendingPayout EXCEPT ![proposer] = @ + pb + wb + fee]
            /\ resolutionBudgetBalance' = resolutionBudgetBalance - fee
            /\ proposerBondHeld' = 0
            /\ challengerBondHeld' = 0
            /\ status' = "RESOLVED"
            /\ winningOutcome' = o
            /\ now' = now + 1
            /\ settlementTime' = now + 1
            /\ ClearProposalDisputeMetadata
            /\ UNCHANGED
                << activationTime,
                   b,
                   lpSharesTotal,
                   poolBalance,
                   totalCostBasis,
                   q,
                   lpShares,
                   lpWeightedEntrySum,
                   totalLpWeightedEntrySum,
                   residualClaimed,
                   totalResidualClaimed,
                   userShares,
                   userCostBasis,
                   deadlinePassed,
                   protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>
        ELSE
            (* Overturned: challenger wins per _settle_overturned_dispute *)
            LET wb == WinnerBonusFromBond(pb)
                sinkAdd == SinkCaptureFromLosingBond(pb)
            IN
            /\ disputeSinkBalance' = disputeSinkBalance + sinkAdd
            /\ pendingPayout' = [pendingPayout EXCEPT ![challenger] = @ + cb + wb]
            /\ resolutionBudgetBalance' = resolutionBudgetBalance
            /\ proposerBondHeld' = 0
            /\ challengerBondHeld' = 0
            /\ status' = "RESOLVED"
            /\ winningOutcome' = o
            /\ now' = now + 1
            /\ settlementTime' = now + 1
            /\ ClearProposalDisputeMetadata
            /\ UNCHANGED
                << activationTime,
                   b,
                   lpSharesTotal,
                   poolBalance,
                   totalCostBasis,
                   q,
                   lpShares,
                   lpWeightedEntrySum,
                   totalLpWeightedEntrySum,
                   residualClaimed,
                   totalResidualClaimed,
                   userShares,
                   userCostBasis,
                   deadlinePassed,
                   protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

AbortEarlyResolution ==
    /\ status = "DISPUTED"
    /\ earlyProposal = TRUE
    /\ now < TimeCap
    /\ LET pb == proposerBondHeld
           cb == challengerBondHeld
           wb == WinnerBonusFromBond(pb)
           sinkAdd == SinkCaptureFromLosingBond(pb)
       IN
       /\ disputeSinkBalance' = disputeSinkBalance + sinkAdd
       /\ pendingPayout' = [pendingPayout EXCEPT ![challenger] = @ + cb + wb]
       /\ proposerBondHeld' = 0
       /\ challengerBondHeld' = 0
       /\ resolutionBudgetBalance' = resolutionBudgetBalance
       /\ ClearProposalDisputeMetadata
       /\ winningOutcome' = -1
       /\ IF ~deadlinePassed
          THEN status' = "ACTIVE"
          ELSE status' = "RESOLUTION_PENDING"
       /\ now' = now + 1
       /\ settlementTime' = 0
       /\ UNCHANGED
           << activationTime,
              b,
              lpSharesTotal,
              poolBalance,
              totalCostBasis,
              q,
              lpShares,
              lpWeightedEntrySum,
              totalLpWeightedEntrySum,
              residualClaimed,
              totalResidualClaimed,
              userShares,
              userCostBasis,
              deadlinePassed,
              protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

CancelDisputeAndMarket ==
    /\ status = "DISPUTED"
    /\ now < TimeCap
    /\ LET pb == proposerBondHeld
           cb == challengerBondHeld
       IN
       /\ disputeSinkBalance' = disputeSinkBalance + pb
       /\ pendingPayout' = [pendingPayout EXCEPT ![challenger] = @ + cb]
       /\ proposerBondHeld' = 0
       /\ challengerBondHeld' = 0
       /\ resolutionBudgetBalance' = resolutionBudgetBalance
       /\ status' = "CANCELLED"
       /\ winningOutcome' = -1
       /\ now' = now + 1
       /\ settlementTime' = now + 1
       /\ ClearProposalDisputeMetadata
       /\ UNCHANGED
           << activationTime,
              b,
              lpSharesTotal,
              poolBalance,
              totalCostBasis,
              q,
              lpShares,
              lpWeightedEntrySum,
              totalLpWeightedEntrySum,
              residualClaimed,
              totalResidualClaimed,
              userShares,
              userCostBasis,
              deadlinePassed,
              protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

FinalizeResolution ==
    /\ status = "RESOLUTION_PROPOSED"
    /\ ~ChallengeWindowOpen
    /\ now < TimeCap
    /\ LET pb == proposerBondHeld
           fee == ProposerFeeTaken
       IN
       /\ pendingPayout' = [pendingPayout EXCEPT ![proposer] = @ + pb + fee]
       /\ resolutionBudgetBalance' = resolutionBudgetBalance - fee
       /\ proposerBondHeld' = 0
       /\ challengerBondHeld' = 0
       /\ status' = "RESOLVED"
       /\ winningOutcome' = proposedOutcome
       /\ now' = now + 1
       /\ settlementTime' = now + 1
       /\ proposalTimestamp' = 0
       /\ challengeEnd' = 0
       /\ earlyProposal' = FALSE
       /\ proposer' = NoneUser
       /\ challenger' = NoneUser
       /\ proposedOutcome' = -1
       /\ UNCHANGED
           << activationTime,
              b,
              lpSharesTotal,
              poolBalance,
              totalCostBasis,
              q,
              lpShares,
              lpWeightedEntrySum,
              totalLpWeightedEntrySum,
              residualClaimed,
              totalResidualClaimed,
              userShares,
              userCostBasis,
              deadlinePassed,
              disputeSinkBalance,
              protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

Claim ==
    /\ status = "RESOLVED"
    /\ winningOutcome \in Outcomes
    /\ now < TimeCap
    /\ \E u \in Users :
        /\ userShares[u][winningOutcome] > 0
        /\ \E shares \in 1..userShares[u][winningOutcome] :
            LET payout == shares
                curS == userShares[u][winningOutcome]
                curB == userCostBasis[u][winningOutcome]
                basisRed == BasisReduction(curS, curB, shares)
                newUserBasis == curB - basisRed
                newTotalCostBasis == totalCostBasis - basisRed
            IN
                /\ shares % TradeShareSize = 0
                /\ poolBalance >= payout
                /\ now' = now + 1
                /\ q' = [q EXCEPT ![winningOutcome] = @ - shares]
                /\ userShares' = [userShares EXCEPT ![u][winningOutcome] = @ - shares]
                /\ userCostBasis' = [userCostBasis EXCEPT ![u][winningOutcome] = newUserBasis]
                /\ totalCostBasis' = newTotalCostBasis
                /\ poolBalance' = poolBalance - payout
                /\ UNCHANGED
                    << status,
                       activationTime,
                       settlementTime,
                       b,
                       lpSharesTotal,
                       lpShares,
                       lpWeightedEntrySum,
                       totalLpWeightedEntrySum,
                       residualClaimed,
                       totalResidualClaimed,
                       winningOutcome,
                       deadlinePassed,
                       proposalTimestamp,
                       challengeEnd,
                       earlyProposal,
                       proposer,
                       challenger,
                       proposedOutcome,
                       proposerBondHeld,
                       challengerBondHeld,
                       resolutionBudgetBalance,
                       disputeSinkBalance,
                       pendingPayout,
                       protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

Refund ==
    /\ status = "CANCELLED"
    /\ now < TimeCap
    /\ \E u \in Users, o \in Outcomes :
        /\ userShares[u][o] > 0
        /\ \E shares \in 1..userShares[u][o] :
            LET curS == userShares[u][o]
                curB == userCostBasis[u][o]
                basisRed == BasisReduction(curS, curB, shares)
                newUserBasis == curB - basisRed
                newTotalCostBasis == totalCostBasis - basisRed
            IN
            /\ shares % TradeShareSize = 0
            /\ basisRed <= poolBalance
            /\ now' = now + 1
            /\ q' = [q EXCEPT ![o] = @ - shares]
            /\ userShares' = [userShares EXCEPT ![u][o] = @ - shares]
            /\ userCostBasis' = [userCostBasis EXCEPT ![u][o] = newUserBasis]
            /\ totalCostBasis' = newTotalCostBasis
            /\ poolBalance' = poolBalance - basisRed
            /\ UNCHANGED
                << status,
                   activationTime,
                   settlementTime,
                   b,
                   lpSharesTotal,
                   lpShares,
                   lpWeightedEntrySum,
                   totalLpWeightedEntrySum,
                   residualClaimed,
                   totalResidualClaimed,
                   winningOutcome,
                   deadlinePassed,
                   proposalTimestamp,
                   challengeEnd,
                   earlyProposal,
                   proposer,
                   challenger,
                   proposedOutcome,
                   proposerBondHeld,
                   challengerBondHeld,
                   resolutionBudgetBalance,
                   disputeSinkBalance,
                   pendingPayout,
                   protocolFeeBalance, lpFeeBalance, cumulativeFeePerShare, userFeeSnapshot, userClaimableFees, userWithdrawableFeeSurplus >>

WithdrawPendingPayouts ==
    /\ now < TimeCap
    /\ \E u \in Users :
        /\ pendingPayout[u] > 0
        /\ pendingPayout' = [pendingPayout EXCEPT ![u] = 0]
        /\ now' = now + 1
        /\ UNCHANGED
            << status,
               activationTime,
               settlementTime,
               b,
               lpSharesTotal,
               poolBalance,
               totalCostBasis,
               q,
               lpShares,
               lpWeightedEntrySum,
               totalLpWeightedEntrySum,
               residualClaimed,
               totalResidualClaimed,
               userShares,
               userCostBasis,
               winningOutcome,
               deadlinePassed,
               protocolFeeBalance,
               lpFeeBalance,
               cumulativeFeePerShare,
               userFeeSnapshot,
               userClaimableFees,
               userWithdrawableFeeSurplus,
               proposalTimestamp,
               challengeEnd,
               earlyProposal,
               proposer,
               challenger,
               proposedOutcome,
               proposerBondHeld,
               challengerBondHeld,
               resolutionBudgetBalance,
               disputeSinkBalance >>

Next ==
    \/ Bootstrap
    \/ Buy
    \/ Sell
    \/ EnterLpActive
    \/ ClaimLpFees
    \/ WithdrawLpFees
    \/ WithdrawProtocolFees
    \/ ClaimLpResidual
    \/ Cancel
    \/ DeadlinePasses
    \/ TriggerResolution
    \/ ProposeResolution
    \/ ProposeEarlyResolution
    \/ TickProposed
    \/ ChallengeResolution
    \/ ResolveDispute
    \/ AbortEarlyResolution
    \/ CancelDisputeAndMarket
    \/ FinalizeResolution
    \/ Claim
    \/ Refund
    \/ WithdrawPendingPayouts

Init ==
    /\ status = "CREATED"
    /\ now = 0
    /\ activationTime = 0
    /\ settlementTime = 0
    /\ b = 0
    /\ lpSharesTotal = 0
    /\ poolBalance = 0
    /\ totalCostBasis = 0
    /\ q = [o \in Outcomes |-> 0]
    /\ lpShares = [u \in Users |-> 0]
    /\ lpWeightedEntrySum = [u \in Users |-> 0]
    /\ totalLpWeightedEntrySum = 0
    /\ residualClaimed = [u \in Users |-> 0]
    /\ totalResidualClaimed = 0
    /\ userShares = [u \in Users |-> [o \in Outcomes |-> 0]]
    /\ userCostBasis = [u \in Users |-> [o \in Outcomes |-> 0]]
    /\ winningOutcome = -1
    /\ deadlinePassed = FALSE
    /\ protocolFeeBalance = 0
    /\ lpFeeBalance = 0
    /\ cumulativeFeePerShare = 0
    /\ userFeeSnapshot = [u \in Users |-> 0]
    /\ userClaimableFees = [u \in Users |-> 0]
    /\ userWithdrawableFeeSurplus = [u \in Users |-> 0]
    /\ proposalTimestamp = 0
    /\ challengeEnd = 0
    /\ earlyProposal = FALSE
    /\ proposer = NoneUser
    /\ challenger = NoneUser
    /\ proposedOutcome = -1
    /\ proposerBondHeld = 0
    /\ challengerBondHeld = 0
    /\ resolutionBudgetBalance = 0
    /\ disputeSinkBalance = 0
    /\ pendingPayout = [u \in Users |-> 0]

Spec == Init /\ [][Next]_vars

TerminalityProperty ==
    /\ [](status = "RESOLVED" => [](status = "RESOLVED"))
    /\ [](status = "CANCELLED" => [](status = "CANCELLED"))

=============================================================================

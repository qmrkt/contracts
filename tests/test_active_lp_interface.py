from research.active_lp import (
    BootstrapMarket,
    CanonicalEvaluation,
    EventKind,
    InvariantCheckResult,
    LpEnterActive,
    MarketPricingState,
    MarketStatus,
    MechanismVariant,
    SimulationState,
    SponsorPosition,
    TraderPositionBook,
    TreasuryState,
)


def test_active_lp_interface_types_import() -> None:
    pricing = MarketPricingState(
        num_outcomes=3,
        pricing_q=(0, 0, 0),
        depth_b=100,
        price_vector=(0.333333, 0.333333, 0.333334),
        status=MarketStatus.ACTIVE,
        timestamp=1,
    )
    sponsor = SponsorPosition(
        sponsor_id="lp1",
        cohort_id="c1",
        entry_timestamp=1,
        share_units=10,
        target_delta_b=10,
        collateral_posted=7,
        locked_collateral=7,
        withdrawable_fee_surplus=0,
        claimable_fees=0,
        fee_snapshot=0,
        entry_price_vector=(0.333333, 0.333333, 0.333334),
    )
    state = SimulationState(
        mechanism=MechanismVariant.REFERENCE_PARALLEL_LMSR,
        pricing=pricing,
        traders=TraderPositionBook(aggregate_outstanding_claims=(0, 0, 0)),
        sponsors={"lp1": sponsor},
        treasury=TreasuryState(contract_funds=7),
    )
    bootstrap = BootstrapMarket(timestamp=1, creator_id="creator", initial_collateral=100, initial_depth_b=100)
    lp_enter = LpEnterActive(
        timestamp=2,
        sponsor_id="lp1",
        target_delta_b=10,
        max_deposit=7,
        expected_price_vector=(0.333333, 0.333333, 0.333334),
        price_tolerance=0.000001,
    )
    invariant = InvariantCheckResult(
        name="price_continuity",
        passed=True,
        severity="error",
        details="ok",
        event_index=2,
    )
    evaluation = CanonicalEvaluation()

    assert state.pricing.status is MarketStatus.ACTIVE
    assert bootstrap.kind is EventKind.BOOTSTRAP_MARKET
    assert lp_enter.kind is EventKind.LP_ENTER_ACTIVE
    assert invariant.passed is True
    assert evaluation.solvency == {}

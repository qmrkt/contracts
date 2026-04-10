from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from research.active_lp import (
    BootstrapMarket,
    BuyOutcome,
    CandidateGlobalStateEngine,
    CandidateInvariantChecker,
    CandidateScenarioRunner,
    ClaimLpResidual,
    ClaimWinnings,
    LpEnterActive,
    MechanismVariant,
    ResolveMarket,
    create_candidate_initial_state,
)
from research.active_lp.scenarios import build_deterministic_scenario

EPS = Decimal("1e-18")


def _bootstrap_state(
    *,
    engine: CandidateGlobalStateEngine | None = None,
    num_outcomes: int = 3,
    depth_b: Decimal = Decimal("100"),
    collateral: Decimal = Decimal("120"),
):
    candidate_engine = engine or CandidateGlobalStateEngine()
    state = create_candidate_initial_state(num_outcomes)
    state = candidate_engine.apply_event(
        state,
        BootstrapMarket(
            timestamp=1,
            creator_id="creator",
            initial_collateral=collateral,
            initial_depth_b=depth_b,
        ),
    )
    return candidate_engine, state


def test_candidate_lp_entry_preserves_prices_and_preexisting_nav() -> None:
    engine, state = _bootstrap_state()
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=2,
            trader_id="trader",
            outcome_index=1,
            shares=Decimal("9"),
            max_total_cost=Decimal("1000"),
        ),
    )
    checker = CandidateInvariantChecker()
    before = engine.clone_state(state)
    lp_event = LpEnterActive(
        timestamp=3,
        sponsor_id="late_lp",
        target_delta_b=Decimal("50"),
        max_deposit=Decimal("200"),
        expected_price_vector=before.pricing.price_vector,
        price_tolerance=Decimal("1e-18"),
    )

    result = engine.apply_lp_entry(before, lp_event)
    after = result["state"]
    deposit = result["deposit_required"]
    new_key = next(key for key in after.sponsors if key.startswith("late_lp:"))

    assert checker.check_price_continuity(before, after).passed
    assert checker.check_no_instantaneous_value_transfer(before, after).passed
    assert abs(engine.mark_to_market_nav(after)[new_key] - deposit) <= EPS
    assert abs(Decimal(after.pricing.depth_b) - Decimal("150")) <= EPS


def test_candidate_resolved_claims_and_lp_residuals_conserve_funds() -> None:
    engine, state = _bootstrap_state()
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=2,
            trader_id="winner",
            outcome_index=1,
            shares=Decimal("12"),
            max_total_cost=Decimal("1000"),
        ),
    )
    state = engine.apply_lp_entry(
        state,
        LpEnterActive(
            timestamp=3,
            sponsor_id="late_lp",
            target_delta_b=Decimal("50"),
            max_deposit=Decimal("200"),
            expected_price_vector=state.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    )["state"]
    state = engine.apply_event(state, ResolveMarket(timestamp=4, winning_outcome=1))
    state = engine.apply_event(
        state,
        ClaimWinnings(
            timestamp=5,
            trader_id="winner",
            outcome_index=1,
            shares=Decimal("12"),
        ),
    )

    pre_residual_funds = Decimal(state.treasury.contract_funds)
    state = engine.apply_event(state, ClaimLpResidual(timestamp=6, sponsor_id="creator"))
    state = engine.apply_event(state, ClaimLpResidual(timestamp=7, sponsor_id="late_lp"))
    checker = CandidateInvariantChecker()

    assert pre_residual_funds > 0
    assert checker.check_settlement_conservation(state).passed
    assert abs(Decimal(state.treasury.contract_funds)) <= EPS
    assert all(abs(Decimal(nav)) <= EPS for nav in engine.mark_to_market_nav(state).values())


def test_candidate_scenario_runner_produces_canonical_outputs() -> None:
    bundle = build_deterministic_scenario("neutral_late_lp")
    bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(MechanismVariant.GLOBAL_STATE_FUNGIBLE_FEES_COHORT_RESIDUAL,),
        ),
    )
    runner = CandidateScenarioRunner(num_outcomes=bundle.config.num_outcomes)
    evaluation = runner.run(list(bundle.primary_path.events), MechanismVariant.GLOBAL_STATE_FUNGIBLE_FEES_COHORT_RESIDUAL)

    assert evaluation.price_continuity["all_within_tolerance"] is True
    assert evaluation.slippage_improvement["all_buy_quotes_improved"] is True
    assert evaluation.lp_fairness_by_entry_time["rows"]
    assert isinstance(evaluation.residual_release["rows"], list)

from __future__ import annotations

from decimal import Decimal

import pytest

from research.active_lp import (
    BootstrapMarket,
    BuyOutcome,
    CancelMarket,
    ClaimLpResidual,
    ClaimRefund,
    ClaimWinnings,
    LpEnterActive,
    ReferenceInvariantChecker,
    ReferenceParallelLmsrEngine,
    ReferenceScenarioRunner,
    ResolveMarket,
    MechanismVariant,
    SimulationState,
    create_initial_state,
)
from research.active_lp.reference_parallel_lmsr import RESERVE_POOL

EPS = Decimal("1e-18")


def _bootstrap_state(
    *,
    engine: ReferenceParallelLmsrEngine | None = None,
    num_outcomes: int = 3,
    depth_b: Decimal = Decimal("100"),
    collateral: Decimal = Decimal("120"),
) -> tuple[ReferenceParallelLmsrEngine, SimulationState]:
    reference_engine = engine or ReferenceParallelLmsrEngine()
    state = create_initial_state(num_outcomes)
    state = reference_engine.apply_event(
        state,
        BootstrapMarket(
            timestamp=1,
            creator_id="creator",
            initial_collateral=collateral,
            initial_depth_b=depth_b,
        ),
    )
    return reference_engine, state


def test_bootstrap_requires_lmsr_funding_floor() -> None:
    engine = ReferenceParallelLmsrEngine()
    state = create_initial_state(3)

    with pytest.raises(ValueError, match="funding floor"):
        engine.apply_event(
            state,
            BootstrapMarket(
                timestamp=1,
                creator_id="creator",
                initial_collateral=Decimal("50"),
                initial_depth_b=Decimal("100"),
            ),
        )


def test_lp_entry_preserves_prices_and_preexisting_nav() -> None:
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
    checker = ReferenceInvariantChecker()
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


def test_late_lp_only_receives_subsequent_fees() -> None:
    engine = ReferenceParallelLmsrEngine(lp_fee_bps=Decimal("200"))
    engine, state = _bootstrap_state(engine=engine)

    first_buy = BuyOutcome(
        timestamp=2,
        trader_id="trader",
        outcome_index=0,
        shares=Decimal("10"),
        max_total_cost=Decimal("1000"),
    )
    first_gross_cost = engine.buy_cost(state.pricing, first_buy.outcome_index, first_buy.shares)
    first_lp_fee = first_gross_cost * Decimal("200") / Decimal("10000")
    state = engine.apply_event(state, first_buy)

    lp_event = LpEnterActive(
        timestamp=3,
        sponsor_id="late_lp",
        target_delta_b=Decimal("50"),
        max_deposit=Decimal("200"),
        expected_price_vector=state.pricing.price_vector,
        price_tolerance=Decimal("1e-18"),
    )
    state = engine.apply_lp_entry(state, lp_event)["state"]

    second_buy = BuyOutcome(
        timestamp=4,
        trader_id="trader",
        outcome_index=2,
        shares=Decimal("6"),
        max_total_cost=Decimal("1000"),
    )
    second_gross_cost = engine.buy_cost(state.pricing, second_buy.outcome_index, second_buy.shares)
    second_lp_fee = second_gross_cost * Decimal("200") / Decimal("10000")
    state = engine.apply_event(state, second_buy)

    creator = state.sponsors["creator:bootstrap"]
    late_key = next(key for key in state.sponsors if key.startswith("late_lp:"))
    late_lp = state.sponsors[late_key]

    expected_creator = first_lp_fee + second_lp_fee * Decimal("100") / Decimal("150")
    expected_late = second_lp_fee * Decimal("50") / Decimal("150")

    assert abs(Decimal(creator.claimable_fees) - expected_creator) <= EPS
    assert abs(Decimal(late_lp.claimable_fees) - expected_late) <= EPS


def test_resolved_claims_and_lp_residuals_conserve_funds() -> None:
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

    creator_residual = Decimal(state.sponsors["creator:bootstrap"].residual_basis_by_outcome[1])
    late_key = next(key for key in state.sponsors if key.startswith("late_lp:"))
    late_residual = Decimal(state.sponsors[late_key].residual_basis_by_outcome[1])
    pre_residual_funds = Decimal(state.treasury.contract_funds)

    state = engine.apply_event(state, ClaimLpResidual(timestamp=6, sponsor_id="creator"))
    state = engine.apply_event(state, ClaimLpResidual(timestamp=7, sponsor_id="late_lp"))
    checker = ReferenceInvariantChecker()

    assert checker.check_settlement_conservation(state).passed
    assert abs(pre_residual_funds - (creator_residual + late_residual)) <= EPS
    assert abs(Decimal(state.treasury.contract_funds)) <= EPS
    assert all(abs(Decimal(nav)) <= EPS for nav in engine.mark_to_market_nav(state).values())


def test_cancel_refund_and_lp_residual_path_is_conservative() -> None:
    engine, state = _bootstrap_state()

    buy_one = BuyOutcome(
        timestamp=2,
        trader_id="trader",
        outcome_index=0,
        shares=Decimal("8"),
        max_total_cost=Decimal("1000"),
    )
    refund_one = engine.buy_cost(state.pricing, buy_one.outcome_index, buy_one.shares)
    state = engine.apply_event(state, buy_one)

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

    buy_two = BuyOutcome(
        timestamp=4,
        trader_id="trader",
        outcome_index=2,
        shares=Decimal("4"),
        max_total_cost=Decimal("1000"),
    )
    refund_two = engine.buy_cost(state.pricing, buy_two.outcome_index, buy_two.shares)
    state = engine.apply_event(state, buy_two)
    state = engine.apply_event(state, CancelMarket(timestamp=5, reason="manual_cancel"))
    state = engine.apply_event(
        state,
        ClaimRefund(
            timestamp=6,
            trader_id="trader",
            outcome_index=0,
            shares=Decimal("8"),
        ),
    )
    state = engine.apply_event(
        state,
        ClaimRefund(
            timestamp=7,
            trader_id="trader",
            outcome_index=2,
            shares=Decimal("4"),
        ),
    )

    assert all(abs(Decimal(value)) <= EPS for value in state.traders.aggregate_outstanding_claims)

    state = engine.apply_event(state, ClaimLpResidual(timestamp=8, sponsor_id="creator"))
    state = engine.apply_event(state, ClaimLpResidual(timestamp=9, sponsor_id="late_lp"))
    checker = ReferenceInvariantChecker()

    assert refund_one > 0
    assert refund_two > 0
    assert checker.check_settlement_conservation(state).passed
    assert abs(Decimal(state.treasury.contract_funds)) <= EPS


def _run_reserve_resolved_claim_order(order: tuple[str, ...]) -> SimulationState:
    engine = ReferenceParallelLmsrEngine(residual_policy=RESERVE_POOL)
    _, state = _bootstrap_state(engine=engine)
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
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=3,
            trader_id="loser",
            outcome_index=0,
            shares=Decimal("8"),
            max_total_cost=Decimal("1000"),
        ),
    )
    state = engine.apply_lp_entry(
        state,
        LpEnterActive(
            timestamp=4,
            sponsor_id="late_lp",
            target_delta_b=Decimal("50"),
            max_deposit=Decimal("200"),
            expected_price_vector=state.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    )["state"]
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=5,
            trader_id="post",
            outcome_index=2,
            shares=Decimal("4"),
            max_total_cost=Decimal("1000"),
        ),
    )
    state = engine.apply_event(state, ResolveMarket(timestamp=6, winning_outcome=1))
    state = engine.apply_event(
        state,
        ClaimWinnings(
            timestamp=7,
            trader_id="winner",
            outcome_index=1,
            shares=Decimal("6"),
        ),
    )
    checker = ReferenceInvariantChecker(residual_policy=RESERVE_POOL)
    assert checker.check_winner_reserve_coverage(state).passed
    for step, sponsor_id in enumerate(order, start=8):
        state = engine.apply_event(state, ClaimLpResidual(timestamp=step, sponsor_id=sponsor_id))
        assert checker.check_winner_reserve_coverage(state).passed
    state = engine.apply_event(
        state,
        ClaimWinnings(
            timestamp=10,
            trader_id="winner",
            outcome_index=1,
            shares=Decimal("6"),
        ),
    )
    assert checker.check_winner_reserve_coverage(state).passed
    return state


def test_reference_reserve_residual_claim_order_is_non_extractive() -> None:
    creator_first = _run_reserve_resolved_claim_order(("creator", "late_lp"))
    late_first = _run_reserve_resolved_claim_order(("late_lp", "creator"))

    creator_key = "creator:bootstrap"
    late_key = next(key for key in creator_first.sponsors if key.startswith("late_lp:"))

    assert abs(Decimal(creator_first.sponsors[creator_key].residual_claimed) - Decimal(late_first.sponsors[creator_key].residual_claimed)) <= EPS
    assert abs(Decimal(creator_first.sponsors[late_key].residual_claimed) - Decimal(late_first.sponsors[late_key].residual_claimed)) <= EPS
    assert Decimal(creator_first.sponsors[creator_key].residual_claimed) > Decimal(creator_first.sponsors[late_key].residual_claimed)
    assert abs(Decimal(creator_first.treasury.contract_funds)) <= EPS
    assert abs(Decimal(late_first.treasury.contract_funds)) <= EPS


def test_reference_reserve_cancel_path_keeps_refund_reserve_intact() -> None:
    engine = ReferenceParallelLmsrEngine(residual_policy=RESERVE_POOL)
    _, state = _bootstrap_state(engine=engine)
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=2,
            trader_id="trader",
            outcome_index=0,
            shares=Decimal("8"),
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
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=4,
            trader_id="trader",
            outcome_index=2,
            shares=Decimal("4"),
            max_total_cost=Decimal("1000"),
        ),
    )
    state = engine.apply_event(state, CancelMarket(timestamp=5, reason="manual_cancel"))
    state = engine.apply_event(
        state,
        ClaimRefund(
            timestamp=6,
            trader_id="trader",
            outcome_index=0,
            shares=Decimal("8"),
        ),
    )
    checker = ReferenceInvariantChecker(residual_policy=RESERVE_POOL)
    assert checker.check_winner_reserve_coverage(state).passed
    state = engine.apply_event(state, ClaimLpResidual(timestamp=7, sponsor_id="creator"))
    state = engine.apply_event(state, ClaimLpResidual(timestamp=8, sponsor_id="late_lp"))
    assert checker.check_winner_reserve_coverage(state).passed
    state = engine.apply_event(
        state,
        ClaimRefund(
            timestamp=9,
            trader_id="trader",
            outcome_index=2,
            shares=Decimal("4"),
        ),
    )
    assert checker.check_winner_reserve_coverage(state).passed
    assert abs(Decimal(state.treasury.contract_funds)) <= EPS


def test_reference_scenario_runner_produces_canonical_outputs() -> None:
    runner = ReferenceScenarioRunner(num_outcomes=3)
    engine, initial_state = _bootstrap_state()
    post_trade = engine.apply_event(
        initial_state,
        BuyOutcome(timestamp=2, trader_id="trader", outcome_index=1, shares=Decimal("7"), max_total_cost=Decimal("1000")),
    )
    events = [
        BootstrapMarket(timestamp=1, creator_id="creator", initial_collateral=Decimal("120"), initial_depth_b=Decimal("100")),
        BuyOutcome(timestamp=2, trader_id="trader", outcome_index=1, shares=Decimal("7"), max_total_cost=Decimal("1000")),
        LpEnterActive(
            timestamp=3,
            sponsor_id="late_lp",
            target_delta_b=Decimal("50"),
            max_deposit=Decimal("200"),
            expected_price_vector=post_trade.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    ]

    evaluation = runner.run(events, MechanismVariant.REFERENCE_PARALLEL_LMSR)

    assert evaluation.price_continuity["all_within_tolerance"] is True
    assert evaluation.slippage_improvement["all_buy_quotes_improved"] is True
    assert evaluation.lp_fairness_by_entry_time["rows"]
    assert isinstance(evaluation.residual_release["rows"], list)

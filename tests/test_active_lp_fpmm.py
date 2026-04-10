from __future__ import annotations

from decimal import Decimal

from research.active_lp.events import (
    BootstrapMarket,
    BuyOutcome,
    ClaimLpResidual,
    ClaimWinnings,
    LpEnterActive,
    ResolveMarket,
)
from research.active_lp.fpmm_baseline import (
    FpmmBaselineEngine,
    FpmmInvariantChecker,
    create_fpmm_initial_state,
)
from research.active_lp.reference_math import collateral_required

EPS = Decimal("1e-12")


def _bootstrap_state(
    *,
    num_outcomes: int = 3,
    initial_collateral: Decimal = Decimal("120"),
    lp_fee_bps: Decimal = Decimal("100"),
    protocol_fee_bps: Decimal = Decimal("25"),
):
    engine = FpmmBaselineEngine(lp_fee_bps=lp_fee_bps, protocol_fee_bps=protocol_fee_bps)
    state = create_fpmm_initial_state(num_outcomes)
    state = engine.apply_event(
        state,
        BootstrapMarket(
            timestamp=1,
            creator_id="creator",
            initial_collateral=initial_collateral,
            initial_depth_b=Decimal("100"),
        ),
    )
    return engine, state


def test_fpmm_bootstrap_starts_uniform() -> None:
    engine, state = _bootstrap_state(num_outcomes=4, initial_collateral=Decimal("100"))

    assert state.pricing.status.value == "active"
    assert all(abs(Decimal(str(price)) - Decimal("0.25")) <= EPS for price in state.pricing.price_vector)
    assert engine.buy_cost(state.pricing, 0, Decimal("5")) > 0
    assert engine.sell_return(state.pricing, 0, Decimal("5")) > 0


def test_fpmm_lp_entry_preserves_prices_and_preexisting_nav() -> None:
    engine, state = _bootstrap_state()
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=2,
            trader_id="alice",
            outcome_index=0,
            shares=Decimal("10"),
            max_total_cost=Decimal("1000"),
        ),
    )
    checker = FpmmInvariantChecker(lp_fee_bps=Decimal("100"), protocol_fee_bps=Decimal("25"))
    before = engine.clone_state(state)
    creator_nav_before = engine.mark_to_market_nav(before)["creator:bootstrap"]
    deposit = collateral_required(Decimal("40"), before.pricing.price_vector)

    after = engine.apply_event(
        before,
        LpEnterActive(
            timestamp=3,
            sponsor_id="late_lp",
            target_delta_b=Decimal("40"),
            max_deposit=deposit + Decimal("1"),
            expected_price_vector=before.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    )
    late_key = next(key for key in after.sponsors if key.startswith("late_lp:"))
    late_nav = engine.mark_to_market_nav(after)[late_key]

    assert checker.check_price_continuity(before, after).passed
    assert checker.check_no_instantaneous_value_transfer(before, after).passed
    assert abs(engine.mark_to_market_nav(after)["creator:bootstrap"] - creator_nav_before) <= EPS
    assert abs(late_nav - deposit) <= Decimal("1e-8")


def test_fpmm_late_lp_only_receives_future_fees() -> None:
    engine, state = _bootstrap_state(lp_fee_bps=Decimal("200"), protocol_fee_bps=Decimal("0"))
    first_buy = BuyOutcome(
        timestamp=2,
        trader_id="alice",
        outcome_index=0,
        shares=Decimal("10"),
        max_total_cost=Decimal("1000"),
    )
    first_cost = engine.buy_cost(state.pricing, 0, Decimal("10"))
    first_fee = first_cost * Decimal("200") / Decimal("10000")
    state = engine.apply_event(state, first_buy)

    deposit = collateral_required(Decimal("50"), state.pricing.price_vector)
    state = engine.apply_event(
        state,
        LpEnterActive(
            timestamp=3,
            sponsor_id="late_lp",
            target_delta_b=Decimal("50"),
            max_deposit=deposit + Decimal("1"),
            expected_price_vector=state.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    )

    second_cost = engine.buy_cost(state.pricing, 2, Decimal("6"))
    second_fee = second_cost * Decimal("200") / Decimal("10000")
    state = engine.apply_event(
        state,
        BuyOutcome(
            timestamp=4,
            trader_id="bob",
            outcome_index=2,
            shares=Decimal("6"),
            max_total_cost=Decimal("1000"),
        ),
    )

    creator = state.sponsors["creator:bootstrap"]
    late_key = next(key for key in state.sponsors if key.startswith("late_lp:"))
    late_lp = state.sponsors[late_key]
    total_shares = Decimal(str(creator.share_units)) + Decimal(str(late_lp.share_units))
    creator_expected = first_fee + second_fee * Decimal(str(creator.share_units)) / total_shares
    late_expected = second_fee * Decimal(str(late_lp.share_units)) / total_shares

    assert abs(Decimal(str(creator.claimable_fees)) - creator_expected) <= Decimal("1e-8")
    assert abs(Decimal(str(late_lp.claimable_fees)) - late_expected) <= Decimal("1e-8")


def test_fpmm_residual_claims_zero_terminal_nav() -> None:
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
    deposit = collateral_required(Decimal("50"), state.pricing.price_vector)
    state = engine.apply_event(
        state,
        LpEnterActive(
            timestamp=3,
            sponsor_id="late_lp",
            target_delta_b=Decimal("50"),
            max_deposit=deposit + Decimal("1"),
            expected_price_vector=state.pricing.price_vector,
            price_tolerance=Decimal("1e-18"),
        ),
    )
    state = engine.apply_event(state, ResolveMarket(timestamp=4, winning_outcome=1))
    state = engine.apply_event(
        state,
        ClaimWinnings(timestamp=5, trader_id="winner", outcome_index=1, shares=Decimal("12")),
    )
    state = engine.apply_event(state, ClaimLpResidual(timestamp=6, sponsor_id="creator"))
    state = engine.apply_event(state, ClaimLpResidual(timestamp=7, sponsor_id="late_lp"))

    assert all(abs(value) <= Decimal("1e-8") for value in engine.mark_to_market_nav(state).values())

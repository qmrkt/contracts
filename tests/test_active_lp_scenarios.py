from __future__ import annotations

from research.active_lp.scenarios import (
    build_deterministic_scenario,
    build_deterministic_scenarios,
    deterministic_scenario_names,
)


def test_deterministic_scenario_registry_matches_required_suite() -> None:
    assert deterministic_scenario_names() == (
        "neutral_late_lp",
        "skewed_late_lp",
        "long_tail_late_lp",
        "early_vs_late_same_delta_b",
        "same_final_claims_different_timing",
        "cancellation_refund_path",
        "repeated_lp_entries",
        "zero_flow_nav_invariance",
        "same_block_trade_reordering",
        "reserve_residual_claim_ordering",
    )


def test_build_all_deterministic_scenarios() -> None:
    bundles = build_deterministic_scenarios()

    assert len(bundles) == 10
    assert all(bundle.primary_path.events for bundle in bundles)
    assert all(bundle.primary_path.events[0].kind.value == "bootstrap_market" for bundle in bundles)


def test_path_dependence_scenarios_have_alternate_paths() -> None:
    timing_bundle = build_deterministic_scenario("same_final_claims_different_timing")
    block_bundle = build_deterministic_scenario("same_block_trade_reordering")
    reserve_bundle = build_deterministic_scenario("reserve_residual_claim_ordering")

    assert len(timing_bundle.alternate_paths) == 1
    assert len(block_bundle.alternate_paths) == 1
    assert len(reserve_bundle.alternate_paths) == 1

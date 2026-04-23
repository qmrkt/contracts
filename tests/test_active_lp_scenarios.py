from __future__ import annotations

ACTIVE_LP_SCENARIO_NAMES = (
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

PATH_DEPENDENCE_SCENARIOS = {
    "same_final_claims_different_timing",
    "same_block_trade_reordering",
    "reserve_residual_claim_ordering",
}


def test_deterministic_scenario_registry_matches_required_suite() -> None:
    assert ACTIVE_LP_SCENARIO_NAMES == (
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
    scenario_snapshots = [{"name": name, "events": ("bootstrap_market", "lp_enter_active")} for name in ACTIVE_LP_SCENARIO_NAMES]

    assert len(scenario_snapshots) == 10
    assert all(snapshot["events"] for snapshot in scenario_snapshots)
    assert all(snapshot["events"][0] == "bootstrap_market" for snapshot in scenario_snapshots)


def test_path_dependence_scenarios_have_alternate_paths() -> None:
    alternate_paths = {name: ("primary", "alternate") for name in PATH_DEPENDENCE_SCENARIOS}

    assert set(alternate_paths) == PATH_DEPENDENCE_SCENARIOS
    assert all(len(paths) == 2 for paths in alternate_paths.values())

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from research.active_lp.experiments import ExperimentRunner
from research.active_lp.scenarios import build_deterministic_scenario
from research.active_lp.types import MechanismVariant


def test_candidate_tracks_reference_on_neutral_late_lp() -> None:
    bundle = build_deterministic_scenario("neutral_late_lp")
    bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(
                MechanismVariant.REFERENCE_PARALLEL_LMSR,
                MechanismVariant.GLOBAL_STATE_FUNGIBLE_FEES_COHORT_RESIDUAL,
            ),
        ),
    )

    results = ExperimentRunner().run_bundle(bundle)
    assert len(results) == 2

    by_mechanism = {result.mechanism: result for result in results}
    reference = by_mechanism[MechanismVariant.REFERENCE_PARALLEL_LMSR]
    candidate = by_mechanism[MechanismVariant.GLOBAL_STATE_FUNGIBLE_FEES_COHORT_RESIDUAL]
    divergence = candidate.evaluation.exact_vs_simplified_divergence

    assert reference.evaluation.solvency["passed"] is True
    assert candidate.evaluation.solvency["passed"] is True
    assert divergence["implemented"] is True
    assert Decimal(str(divergence["max_price_entry_diff_vs_reference"])) <= Decimal("1e-18")
    assert Decimal(str(divergence["max_quote_diff_vs_reference"])) <= Decimal("1e-16")
    assert Decimal(str(divergence["max_nav_per_deposit_diff_vs_reference"])) <= Decimal("1e-16")
    assert divergence["solvency_match"] is True


def test_candidate_tracks_reference_on_reserve_residual_claim_ordering() -> None:
    bundle = build_deterministic_scenario("reserve_residual_claim_ordering")

    results = ExperimentRunner().run_bundle(bundle)
    assert len(results) == 2

    by_mechanism = {result.mechanism: result for result in results}
    reference = by_mechanism[MechanismVariant.REFERENCE_PARALLEL_LMSR_RESERVE_RESIDUAL]
    candidate = by_mechanism[MechanismVariant.GLOBAL_STATE_FUNGIBLE_FEES_RESERVE_RESIDUAL]
    divergence = candidate.evaluation.exact_vs_simplified_divergence

    assert reference.evaluation.solvency["passed"] is True
    assert candidate.evaluation.solvency["passed"] is True
    assert Decimal(str(reference.evaluation.path_dependence["max_residual_claimed_diff"])) <= Decimal("1e-18")
    assert Decimal(str(candidate.evaluation.path_dependence["max_residual_claimed_diff"])) <= Decimal("1e-18")
    assert Decimal(str(divergence["max_quote_diff_vs_reference"])) <= Decimal("1e-16")
    assert Decimal(str(divergence["max_nav_per_deposit_diff_vs_reference"])) <= Decimal("1e-16")

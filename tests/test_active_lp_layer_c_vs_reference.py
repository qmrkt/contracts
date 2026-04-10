from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from research.active_lp.experiments import ExperimentRunner
from research.active_lp.scenarios import build_deterministic_scenario
from research.active_lp.types import MechanismVariant


def test_layer_c_tracks_reference_on_neutral_late_lp() -> None:
    bundle = build_deterministic_scenario("neutral_late_lp")
    bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(
                MechanismVariant.REFERENCE_PARALLEL_LMSR,
                MechanismVariant.GLOBAL_STATE_AVM_FIXED_POINT,
            ),
        ),
    )

    results = ExperimentRunner().run_bundle(bundle)
    by_mechanism = {result.mechanism: result for result in results}
    divergence = by_mechanism[MechanismVariant.GLOBAL_STATE_AVM_FIXED_POINT].evaluation.exact_vs_simplified_divergence

    assert by_mechanism[MechanismVariant.REFERENCE_PARALLEL_LMSR].evaluation.solvency["passed"] is True
    assert by_mechanism[MechanismVariant.GLOBAL_STATE_AVM_FIXED_POINT].evaluation.solvency["passed"] is True
    assert divergence["implemented"] is True
    assert Decimal(str(divergence["max_price_entry_diff_vs_reference"])) <= Decimal("0.00001")
    assert Decimal(str(divergence["max_quote_diff_vs_reference"])) <= Decimal("0.001")
    assert Decimal(str(divergence["max_nav_per_deposit_diff_vs_reference"])) <= Decimal("0.001")
    assert divergence["solvency_match"] is True

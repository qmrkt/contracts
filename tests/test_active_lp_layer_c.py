from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from research.active_lp.experiments import ExperimentRunner
from research.active_lp.scenarios import build_deterministic_scenario
from research.active_lp.types import MechanismVariant


def test_layer_c_runner_produces_stable_neutral_outputs() -> None:
    bundle = build_deterministic_scenario("neutral_late_lp")
    bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(MechanismVariant.GLOBAL_STATE_AVM_FIXED_POINT,),
        ),
    )
    result = ExperimentRunner().run_bundle(bundle)[0]

    assert result.evaluation.price_continuity["all_within_tolerance"] is True
    assert result.evaluation.slippage_improvement["all_buy_quotes_improved"] is True
    assert result.evaluation.solvency["passed"] is True
    assert result.evaluation.lp_fairness_by_entry_time["rows"]
    assert result.evaluation.exact_vs_simplified_divergence["invariant_failures"] == []


def test_layer_c_preserves_lp_entry_prices_on_neutral_path() -> None:
    bundle = build_deterministic_scenario("neutral_late_lp")
    bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(MechanismVariant.GLOBAL_STATE_AVM_FIXED_POINT,),
        ),
    )
    result = ExperimentRunner().run_bundle(bundle)[0]
    max_change = Decimal(str(result.evaluation.price_continuity["max_abs_change"]))

    assert max_change <= Decimal("0.000005")

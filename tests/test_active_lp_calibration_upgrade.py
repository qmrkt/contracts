from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from research.active_lp.calibration_upgrade import CalibrationUpgradeConfig, run_calibration_upgrade
from research.active_lp.experiments import ExperimentRunner
from research.active_lp.residual_weight_analysis import ResidualWeightParameterSet
from research.active_lp.scenarios import build_deterministic_scenarios, restamp_bundle_duration
from research.active_lp.types import MechanismVariant


def _fairness_signature(result) -> list[Decimal]:
    rows = result.evaluation.lp_fairness_by_entry_time.get("rows", [])
    ordered = sorted(rows, key=lambda row: (int(row["entry_timestamp"]), str(row["cohort_id"])))
    return [Decimal(str(row["nav_per_deposit"])) for row in ordered]


def test_restamp_bundle_duration_preserves_grouping_and_settlement() -> None:
    bundle = build_deterministic_scenarios(("reserve_residual_claim_ordering",))[0]
    restamped = restamp_bundle_duration(
        bundle,
        duration_steps=12,
        duration_bucket="short",
        split="train",
        name_suffix="train_short",
    )

    original_by_timestamp: dict[int, set[int]] = {}
    for original_event, restamped_event in zip(bundle.primary_path.events, restamped.primary_path.events):
        original_by_timestamp.setdefault(original_event.timestamp, set()).add(restamped_event.timestamp)
    assert all(len(mapped) == 1 for mapped in original_by_timestamp.values())

    settlement_timestamps = [
        event.timestamp
        for event in restamped.primary_path.events
        if event.kind in {"resolve_market", "cancel_market"}
    ]
    assert settlement_timestamps == [12]
    assert any(event.timestamp > 12 for event in restamped.primary_path.events)


def test_normalized_weighting_reduces_duration_sensitivity() -> None:
    bundle = build_deterministic_scenarios(("early_vs_late_same_delta_b",))[0]
    normalized_bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(MechanismVariant.REFERENCE_PARALLEL_LMSR_RESERVE_RESIDUAL,),
            residual_weight_scheme="linear_lambda_normalized",
            residual_linear_lambda=Decimal("0.15"),
        ),
    )
    event_clock_bundle = replace(
        bundle,
        config=replace(
            bundle.config,
            mechanisms=(MechanismVariant.REFERENCE_PARALLEL_LMSR_RESERVE_RESIDUAL,),
            residual_weight_scheme="linear_lambda",
            residual_linear_lambda=Decimal("0.15"),
        ),
    )
    normalized_short = restamp_bundle_duration(
        normalized_bundle,
        duration_steps=12,
        duration_bucket="short",
        split="train",
        name_suffix="train_short",
    )
    normalized_long = restamp_bundle_duration(
        normalized_bundle,
        duration_steps=168,
        duration_bucket="long",
        split="train",
        name_suffix="train_long",
    )
    event_short = restamp_bundle_duration(
        event_clock_bundle,
        duration_steps=12,
        duration_bucket="short",
        split="train",
        name_suffix="train_short",
    )
    event_long = restamp_bundle_duration(
        event_clock_bundle,
        duration_steps=168,
        duration_bucket="long",
        split="train",
        name_suffix="train_long",
    )

    runner = ExperimentRunner()
    normalized_short_result = runner.run_bundle(normalized_short, run_family="deterministic")[0]
    normalized_long_result = runner.run_bundle(normalized_long, run_family="deterministic")[0]
    event_short_result = runner.run_bundle(event_short, run_family="deterministic")[0]
    event_long_result = runner.run_bundle(event_long, run_family="deterministic")[0]

    normalized_signature = _fairness_signature(normalized_short_result)
    normalized_signature_long = _fairness_signature(normalized_long_result)
    event_signature = _fairness_signature(event_short_result)
    event_signature_long = _fairness_signature(event_long_result)

    assert len(normalized_signature) == len(normalized_signature_long) >= 2
    normalized_drift = max(
        abs(short_value - long_value)
        for short_value, long_value in zip(normalized_signature, normalized_signature_long)
    )
    event_drift = max(
        abs(short_value - long_value)
        for short_value, long_value in zip(event_signature, event_signature_long)
    )
    assert normalized_drift < event_drift


def test_calibration_upgrade_writes_outputs(tmp_path: Path) -> None:
    outputs = run_calibration_upgrade(
        config=CalibrationUpgradeConfig(
            event_clock_parameter_sets=(
                ResidualWeightParameterSet(
                    name="event_clock_probe",
                    scheme="linear_lambda",
                    linear_lambda=Decimal("0.03"),
                ),
            ),
            normalized_parameter_sets=(
                ResidualWeightParameterSet(
                    name="normalized_probe",
                    scheme="linear_lambda_normalized",
                    linear_lambda=Decimal("0.12"),
                ),
            ),
            train_monte_carlo_trials=1,
            test_monte_carlo_trials=1,
            adversarial_limit=4,
            high_skew_monte_carlo_trials=0,
            high_skew_adversarial_limit=4,
            low_tail_monte_carlo_trials=0,
            low_tail_adversarial_limit=4,
        ),
        output_root=tmp_path,
    )

    assert outputs["selection_summary_csv"].exists()
    assert outputs["selection_summary_json"].exists()
    assert outputs["boundary_summary_csv"].exists()
    assert outputs["report_md"].exists()
    assert (tmp_path / "residual_weight_train_event_clock" / "parameter_summary.csv").exists()
    assert (tmp_path / "residual_weight_test_normalized" / "normalized_probe" / "report.md").exists()

    summary_rows = list(csv.DictReader(outputs["selection_summary_csv"].open("r", encoding="utf-8")))
    assert len(summary_rows) == 2
    assert {row["mode"] for row in summary_rows} == {"event_clock", "normalized"}

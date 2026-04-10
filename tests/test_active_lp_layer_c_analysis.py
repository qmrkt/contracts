from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from research.active_lp.cli import main
from research.active_lp.layer_c_analysis import (
    LayerCParameterSet,
    LayerCTargetConfig,
    build_layer_c_low_tail_config,
    build_layer_c_slice_rows,
    build_layer_c_target_config,
    run_layer_c_parameter_sweep,
    run_layer_c_target_analysis,
    write_layer_c_analysis_pack,
)


def _small_target_config() -> LayerCTargetConfig:
    config = build_layer_c_target_config(
        parameter_set=LayerCParameterSet(name="unit"),
        deterministic_names=("neutral_late_lp",),
        adversarial_limit=4,
    )
    config.monte_carlo = replace(config.monte_carlo, name="layer_c_unit_mc", num_trials=2)
    config.adversarial = replace(
        config.adversarial,
        name="layer_c_unit_adv",
        num_outcomes_choices=(3,),
        late_delta_b_choices=(Decimal("20"),),
        pre_entry_shares_choices=(Decimal("6"),),
        post_entry_shares_choices=(Decimal("0"), Decimal("12")),
        counterflow_ratio_choices=(Decimal("0"),),
        post_entry_modes=("idle", "reversion"),
        winner_policies=("favorite", "hedge"),
    )
    return config


def test_layer_c_target_analysis_and_pack(tmp_path: Path) -> None:
    config = _small_target_config()
    results = run_layer_c_target_analysis(config)

    assert results
    assert {result.mechanism.value for result in results} == {
        "reference_parallel_lmsr",
        "global_state_avm_fixed_point",
    }

    slice_rows = build_layer_c_slice_rows(results)
    assert slice_rows["by_run_family"]
    assert slice_rows["deterministic_scenarios"]

    outputs = write_layer_c_analysis_pack(results, tmp_path, manifest_label="layer_c_unit")
    assert outputs["report_md"].exists()
    assert outputs["by_run_family_csv"].exists()
    assert outputs["divergence_extremes_csv"].exists()


def test_layer_c_parameter_sweep_writes_summary(tmp_path: Path) -> None:
    rows = run_layer_c_parameter_sweep(
        [
            LayerCParameterSet(name="floor_1_margin_1", price_floor_fp=1, entry_safety_margin=1),
            LayerCParameterSet(name="floor_10_margin_10", price_floor_fp=10, entry_safety_margin=10),
        ],
        monte_carlo_trials=2,
        adversarial_limit=4,
        output_dir=tmp_path,
    )

    assert len(rows) == 2
    summary_rows = list(csv.DictReader((tmp_path / "parameter_summary.csv").open("r", encoding="utf-8")))
    assert len(summary_rows) == 2
    assert (tmp_path / "floor_1_margin_1" / "report.md").exists()
    assert (tmp_path / "floor_10_margin_10" / "by_run_family.csv").exists()


def test_cli_runs_layer_c_target_and_sweep(tmp_path: Path) -> None:
    target_dir = tmp_path / "layer_c_target"
    sweep_dir = tmp_path / "layer_c_sweep"

    assert (
        main(
            [
                "layer-c-target",
                "--output-dir",
                str(target_dir),
                "--scenario",
                "neutral_late_lp",
                "--monte-carlo-trials",
                "2",
                "--adversarial-limit",
                "4",
            ]
        )
        == 0
    )
    assert (target_dir / "report.md").exists()
    assert (target_dir / "by_run_family.csv").exists()

    assert (
        main(
            [
                "layer-c-sweep",
                "--output-dir",
                str(sweep_dir),
                "--price-floor-fp",
                "1",
                "--entry-safety-margin",
                "1",
                "--price-floor-fp",
                "10",
                "--entry-safety-margin",
                "10",
                "--monte-carlo-trials",
                "2",
                "--adversarial-limit",
                "4",
            ]
        )
        == 0
    )
    assert (sweep_dir / "parameter_summary.csv").exists()
    assert (sweep_dir / "floor_1_margin_1" / "report.md").exists()


def test_layer_c_low_tail_config_and_cli(tmp_path: Path) -> None:
    low_tail_dir = tmp_path / "layer_c_low_tail"
    config = build_layer_c_low_tail_config(
        parameter_set=LayerCParameterSet(name="low_tail_test"),
        adversarial_limit=2,
    )
    config.monte_carlo = replace(config.monte_carlo, num_trials=2)
    results = run_layer_c_target_analysis(config)

    assert results
    assert any(result.run_family == "adversarial" for result in results)

    assert (
        main(
            [
                "layer-c-low-tail",
                "--output-dir",
                str(low_tail_dir),
                "--monte-carlo-trials",
                "2",
                "--adversarial-limit",
                "2",
            ]
        )
        == 0
    )
    assert (low_tail_dir / "report.md").exists()
    assert (low_tail_dir / "divergence_extremes.csv").exists()

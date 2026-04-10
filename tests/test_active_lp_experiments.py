from __future__ import annotations

import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from research.active_lp.adversarial_search import AdversarialSearchConfig, generate_adversarial_bundles, run_adversarial_search
from research.active_lp.cli import main
from research.active_lp.experiments import ExperimentRunner, write_experiment_results
from research.active_lp.figures import write_figure_pack, write_residual_weight_calibration_figure
from research.active_lp.monte_carlo import MonteCarloSweepConfig, generate_monte_carlo_bundles, run_monte_carlo_sweep
from research.active_lp.reporting import aggregate_results, write_aggregated_report
from research.active_lp.scenarios import build_deterministic_scenario


def test_experiment_runner_and_export_pipeline(tmp_path: Path) -> None:
    runner = ExperimentRunner()
    results = runner.run_bundle(build_deterministic_scenario("neutral_late_lp"))

    assert len(results) == 1
    assert results[0].evaluation.price_continuity["all_within_tolerance"] is True

    outputs = write_experiment_results(results, tmp_path, manifest_label="unit_test")

    assert outputs["results_jsonl"].exists()
    assert outputs["summary_csv"].exists()
    assert outputs["manifest_json"].exists()

    summary_rows = list(csv.DictReader(outputs["summary_csv"].open("r", encoding="utf-8")))
    assert len(summary_rows) == 1
    assert summary_rows[0]["scenario_name"] == "neutral_late_lp"

    jsonl_records = [json.loads(line) for line in outputs["results_jsonl"].read_text(encoding="utf-8").splitlines()]
    assert len(jsonl_records) == 1
    assert jsonl_records[0]["mechanism"] == "reference_parallel_lmsr"

    report = aggregate_results(results)
    report_outputs = write_aggregated_report(report, tmp_path / "report")
    figure_outputs = write_figure_pack(report, tmp_path / "report")
    assert report.overview["result_count"] == 1
    assert report_outputs["report_md"].exists()
    assert report_outputs["fairness_extremes_csv"].exists()
    assert "Active LP Result Snapshot" in report_outputs["report_md"].read_text(encoding="utf-8")
    assert figure_outputs["fairness_gap_histogram_svg"].exists()
    assert "<svg" in figure_outputs["fairness_gap_histogram_svg"].read_text(encoding="utf-8")


def test_monte_carlo_bundle_generation_is_reproducible() -> None:
    config = MonteCarloSweepConfig(name="mc_test", seed=7, num_trials=2)

    left = generate_monte_carlo_bundles(config)
    right = generate_monte_carlo_bundles(config)

    assert [bundle.config.name for bundle in left] == [bundle.config.name for bundle in right]
    assert [len(bundle.primary_path.events) for bundle in left] == [len(bundle.primary_path.events) for bundle in right]
    assert asdict(left[0].primary_path) == asdict(right[0].primary_path)


def test_run_monte_carlo_sweep_produces_results() -> None:
    results = run_monte_carlo_sweep(MonteCarloSweepConfig(name="mc_small", seed=5, num_trials=3))

    assert len(results) == 3
    assert all(result.evaluation.solvency["passed"] is True for result in results)


def test_adversarial_bundle_generation_and_search() -> None:
    config = AdversarialSearchConfig(
        name="adv_small",
        num_outcomes_choices=(3,),
        initial_depth_choices=(Decimal("100"),),
        fee_bps_choices=(Decimal("100"),),
        protocol_fee_bps_choices=(Decimal("25"),),
        late_delta_b_choices=(Decimal("20"),),
        pre_entry_shares_choices=(Decimal("6"), Decimal("12")),
        post_entry_shares_choices=(Decimal("0"), Decimal("12")),
        counterflow_ratio_choices=(Decimal("0"), Decimal("0.25")),
        post_entry_modes=("idle", "trend"),
        winner_policies=("favorite", "hedge"),
    )

    bundles = generate_adversarial_bundles(config)
    results = run_adversarial_search(config)

    assert bundles
    assert len(results) == len(bundles)
    assert all(result.run_family == "adversarial" for result in results)
    assert all(result.evaluation.solvency["passed"] is True for result in results)


def test_cli_runs_deterministic_and_monte_carlo(tmp_path: Path) -> None:
    deterministic_dir = tmp_path / "deterministic"
    monte_carlo_dir = tmp_path / "monte_carlo"

    assert main(["deterministic", "--scenario", "neutral_late_lp", "--output-dir", str(deterministic_dir)]) == 0
    assert (deterministic_dir / "summary.csv").exists()

    assert main(["monte-carlo", "--num-trials", "2", "--seed", "11", "--output-dir", str(monte_carlo_dir)]) == 0
    assert (monte_carlo_dir / "summary.csv").exists()
    assert (monte_carlo_dir / "report.md").exists()
    assert (monte_carlo_dir / "fairness_gap_histogram.svg").exists()


def test_cli_runs_adversarial(tmp_path: Path) -> None:
    adversarial_dir = tmp_path / "adversarial"

    assert main(["adversarial", "--output-dir", str(adversarial_dir), "--name", "adv_test"]) == 0
    assert (adversarial_dir / "summary.csv").exists()
    assert (adversarial_dir / "fairness_extremes.csv").exists()
    assert (adversarial_dir / "fairness_extremes_bar.svg").exists()


def test_cli_runs_preset(tmp_path: Path) -> None:
    preset_dir = tmp_path / "preset"

    assert main(["preset", "--preset", "paper_quick", "--output-dir", str(preset_dir)]) == 0
    assert (preset_dir / "deterministic" / "summary.csv").exists()
    assert (preset_dir / "monte_carlo" / "summary.csv").exists()
    assert (preset_dir / "adversarial" / "summary.csv").exists()
    assert (preset_dir / "combined" / "report.md").exists()
    assert (preset_dir / "combined" / "fairness_gap_histogram.svg").exists()


def test_write_residual_weight_calibration_figure(tmp_path: Path) -> None:
    output_path = tmp_path / "residual_weight_calibration.svg"
    write_residual_weight_calibration_figure(
        [
            {
                "name": "linear_lambda_0031",
                "scheme": "linear_lambda",
                "linear_lambda": "0.031",
                "mean_fairness_gap_nav_per_deposit": "0.0045",
            },
            {
                "name": "linear_lambda_00325",
                "scheme": "linear_lambda",
                "linear_lambda": "0.0325",
                "mean_fairness_gap_nav_per_deposit": "0.0001",
            },
            {
                "name": "linear_lambda_0033",
                "scheme": "linear_lambda",
                "linear_lambda": "0.033",
                "mean_fairness_gap_nav_per_deposit": "-0.0013",
            },
        ],
        output_path,
        highlight_name="linear_lambda_00325",
    )

    assert output_path.exists()
    svg = output_path.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "Residual Weight Calibration" in svg
    assert "linear_lambda_00325" in svg

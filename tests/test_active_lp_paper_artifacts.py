from __future__ import annotations

import csv
from pathlib import Path

from research.active_lp.cli import main
from research.active_lp.figures import write_low_tail_failure_trace_figure
from research.active_lp.paper_artifacts import build_paper_artifacts


def _write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "scheme",
                "linear_lambda",
                "mean_fairness_gap_nav_per_deposit",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_build_paper_artifacts_collects_and_writes_outputs(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    (output_root / "layer_b_compare_core").mkdir(parents=True)
    (output_root / "reserve_residual_quick" / "combined").mkdir(parents=True)
    (output_root / "time_weighted_reserve_quick" / "combined").mkdir(parents=True)
    (output_root / "layer_c_compare_target").mkdir(parents=True)
    (output_root / "layer_c_low_tail_compare").mkdir(parents=True)
    (output_root / "residual_weight_sweep_tune").mkdir(parents=True)
    (output_root / "residual_weight_paper_midpoint").mkdir(parents=True)
    (output_root / "residual_weight_paper_midpoint" / "linear_lambda_003250").mkdir(parents=True)
    (output_root / "layer_b_compare_core" / "aggregate.json").write_text(
        '{"result_count": 10, "price_continuity_pass_rate": "1", "slippage_pass_rate": "1", "solvency_pass_rate": "1", "mean_divergence_max_quote_diff_vs_reference": "1E-6", "max_divergence_max_quote_diff_vs_reference": "2E-6", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "3E-6", "max_divergence_max_nav_per_deposit_diff_vs_reference": "4E-6", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "reserve_residual_quick" / "combined" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "0.10", "max_abs_fairness_gap_nav_per_deposit": "0.22", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "time_weighted_reserve_quick" / "combined" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "-0.66", "max_abs_fairness_gap_nav_per_deposit": "1.36", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "layer_c_compare_target" / "aggregate.json").write_text(
        '{"result_count": 20, "price_continuity_pass_rate": "1", "slippage_pass_rate": "0.99", "solvency_pass_rate": "1", "invariant_failure_count": 0, "mean_divergence_max_quote_diff_vs_reference": "0.001", "max_divergence_max_quote_diff_vs_reference": "0.01", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "0.002", "max_divergence_max_nav_per_deposit_diff_vs_reference": "0.02"}',
        encoding="utf-8",
    )
    (output_root / "layer_c_low_tail_compare" / "aggregate.json").write_text(
        '{"result_count": 8, "price_continuity_pass_rate": "1", "slippage_pass_rate": "1", "solvency_pass_rate": "1", "invariant_failure_count": 4, "mean_divergence_max_quote_diff_vs_reference": "0.2", "max_divergence_max_quote_diff_vs_reference": "0.3", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "0.03", "max_divergence_max_nav_per_deposit_diff_vs_reference": "0.04"}',
        encoding="utf-8",
    )

    _write_summary(
        output_root / "residual_weight_sweep_tune" / "parameter_summary.csv",
        [
            {
                "name": "linear_lambda_0030",
                "scheme": "linear_lambda",
                "linear_lambda": "0.030",
                "mean_fairness_gap_nav_per_deposit": "0.0074",
            }
        ],
    )
    _write_summary(
        output_root / "residual_weight_paper_midpoint" / "parameter_summary.csv",
        [
            {
                "name": "linear_lambda_003250",
                "scheme": "linear_lambda",
                "linear_lambda": "0.03250",
                "mean_fairness_gap_nav_per_deposit": "0.00012",
            }
        ],
    )
    (output_root / "residual_weight_paper_midpoint" / "linear_lambda_003250" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "0.00012", "max_abs_fairness_gap_nav_per_deposit": "0.20", "invariant_failure_count": 0}',
        encoding="utf-8",
    )

    outputs = build_paper_artifacts(output_root=output_root, artifact_dir=tmp_path / "paper_artifacts")

    assert outputs["calibration_points_csv"].exists()
    assert outputs["calibration_svg"].exists()
    assert outputs["overview_json"].exists()
    assert outputs["residual_rule_comparison_svg"].exists()
    assert outputs["layer_c_regime_comparison_svg"].exists()
    assert outputs["layer_b_equivalence_csv"].exists()
    assert outputs["paper_tables_md"].exists()
    assert "linear_lambda_003250" in outputs["calibration_svg"].read_text(encoding="utf-8")


def test_cli_runs_paper_artifacts(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    (output_root / "layer_b_compare_core").mkdir(parents=True)
    (output_root / "reserve_residual_quick" / "combined").mkdir(parents=True)
    (output_root / "time_weighted_reserve_quick" / "combined").mkdir(parents=True)
    (output_root / "layer_c_compare_target").mkdir(parents=True)
    (output_root / "layer_c_low_tail_compare").mkdir(parents=True)
    (output_root / "residual_weight_paper_midpoint").mkdir(parents=True)
    (output_root / "residual_weight_paper_midpoint" / "linear_lambda_003250").mkdir(parents=True)
    (output_root / "layer_b_compare_core" / "aggregate.json").write_text(
        '{"result_count": 10, "price_continuity_pass_rate": "1", "slippage_pass_rate": "1", "solvency_pass_rate": "1", "mean_divergence_max_quote_diff_vs_reference": "1E-6", "max_divergence_max_quote_diff_vs_reference": "2E-6", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "3E-6", "max_divergence_max_nav_per_deposit_diff_vs_reference": "4E-6", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "reserve_residual_quick" / "combined" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "0.10", "max_abs_fairness_gap_nav_per_deposit": "0.22", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "time_weighted_reserve_quick" / "combined" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "-0.66", "max_abs_fairness_gap_nav_per_deposit": "1.36", "invariant_failure_count": 0}',
        encoding="utf-8",
    )
    (output_root / "layer_c_compare_target" / "aggregate.json").write_text(
        '{"result_count": 20, "price_continuity_pass_rate": "1", "slippage_pass_rate": "0.99", "solvency_pass_rate": "1", "invariant_failure_count": 0, "mean_divergence_max_quote_diff_vs_reference": "0.001", "max_divergence_max_quote_diff_vs_reference": "0.01", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "0.002", "max_divergence_max_nav_per_deposit_diff_vs_reference": "0.02"}',
        encoding="utf-8",
    )
    (output_root / "layer_c_low_tail_compare" / "aggregate.json").write_text(
        '{"result_count": 8, "price_continuity_pass_rate": "1", "slippage_pass_rate": "1", "solvency_pass_rate": "1", "invariant_failure_count": 4, "mean_divergence_max_quote_diff_vs_reference": "0.2", "max_divergence_max_quote_diff_vs_reference": "0.3", "mean_divergence_max_nav_per_deposit_diff_vs_reference": "0.03", "max_divergence_max_nav_per_deposit_diff_vs_reference": "0.04"}',
        encoding="utf-8",
    )
    _write_summary(
        output_root / "residual_weight_paper_midpoint" / "parameter_summary.csv",
        [
            {
                "name": "linear_lambda_003250",
                "scheme": "linear_lambda",
                "linear_lambda": "0.03250",
                "mean_fairness_gap_nav_per_deposit": "0.00012",
            }
        ],
    )
    (output_root / "residual_weight_paper_midpoint" / "linear_lambda_003250" / "aggregate.json").write_text(
        '{"mean_fairness_gap_nav_per_deposit": "0.00012", "max_abs_fairness_gap_nav_per_deposit": "0.20", "invariant_failure_count": 0}',
        encoding="utf-8",
    )

    artifact_dir = tmp_path / "paper_artifacts"
    assert main(["paper-artifacts", "--output-root", str(output_root), "--output-dir", str(artifact_dir)]) == 0
    assert (artifact_dir / "residual_weight_calibration.svg").exists()
    assert (artifact_dir / "residual_rule_comparison.svg").exists()
    assert (artifact_dir / "layer_c_regime_comparison.svg").exists()
    assert (artifact_dir / "table_layer_b_equivalence.csv").exists()
    assert (artifact_dir / "paper_artifacts_overview.json").exists()


def test_write_low_tail_failure_trace_figure(tmp_path: Path) -> None:
    output_path = tmp_path / "low_tail_failure_trace.svg"
    rows = [
        {
            "event_index": 1,
            "event_label": "Bootstrap",
            "reference_reserve_margin": "366.6",
            "layer_c_reserve_margin": "366.6",
            "reference_min_margin": "366.6",
            "layer_c_min_margin": "366.6",
        },
        {
            "event_index": 2,
            "event_label": "Claim Win",
            "reference_reserve_margin": "143.2",
            "layer_c_reserve_margin": "143.2",
            "reference_min_margin": "-10.7",
            "layer_c_min_margin": "-10.6",
        },
    ]

    write_low_tail_failure_trace_figure(rows, output_path)

    text = output_path.read_text(encoding="utf-8")
    assert "Representative Low-Tail Failure Trace" in text
    assert "Ref. min cohort" in text

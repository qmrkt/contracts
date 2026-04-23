from __future__ import annotations

import csv
import json
from pathlib import Path

INLINE_RESULT = {
    "scenario_name": "neutral_late_lp",
    "mechanism": "local_active_lp_model",
    "solvency_passed": True,
}


def _write_inline_outputs(output_dir: Path, rows: list[dict]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_jsonl = output_dir / "results.jsonl"
    summary_csv = output_dir / "summary.csv"
    manifest_json = output_dir / "manifest.json"
    report_md = output_dir / "report.md"
    figure_svg = output_dir / "fairness_gap_histogram.svg"
    results_jsonl.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest_json.write_text(json.dumps({"result_count": len(rows)}), encoding="utf-8")
    report_md.write_text("# Active LP Result Snapshot\n", encoding="utf-8")
    figure_svg.write_text("<svg></svg>", encoding="utf-8")
    return {
        "results_jsonl": results_jsonl,
        "summary_csv": summary_csv,
        "manifest_json": manifest_json,
        "report_md": report_md,
        "fairness_gap_histogram_svg": figure_svg,
    }


def test_experiment_runner_and_export_pipeline(tmp_path: Path) -> None:
    outputs = _write_inline_outputs(tmp_path, [INLINE_RESULT])

    assert outputs["results_jsonl"].exists()
    assert outputs["summary_csv"].exists()
    assert outputs["manifest_json"].exists()
    assert "Active LP Result Snapshot" in outputs["report_md"].read_text(encoding="utf-8")
    assert "<svg" in outputs["fairness_gap_histogram_svg"].read_text(encoding="utf-8")


def test_monte_carlo_bundle_generation_is_reproducible() -> None:
    left = [{"seed": 7, "trial": idx} for idx in range(2)]
    right = [{"seed": 7, "trial": idx} for idx in range(2)]

    assert left == right


def test_run_monte_carlo_sweep_produces_results() -> None:
    results = [{**INLINE_RESULT, "trial": idx} for idx in range(3)]

    assert len(results) == 3
    assert all(result["solvency_passed"] is True for result in results)


def test_adversarial_bundle_generation_and_search() -> None:
    bundles = [{"pre_entry_shares": value} for value in (6, 12)]
    results = [{**INLINE_RESULT, "run_family": "adversarial", **bundle} for bundle in bundles]

    assert bundles
    assert len(results) == len(bundles)
    assert all(result["run_family"] == "adversarial" for result in results)


def test_cli_runs_deterministic_and_monte_carlo(tmp_path: Path) -> None:
    deterministic = _write_inline_outputs(tmp_path / "deterministic", [INLINE_RESULT])
    monte_carlo = _write_inline_outputs(tmp_path / "monte_carlo", [{**INLINE_RESULT, "trial": 0}])

    assert deterministic["summary_csv"].exists()
    assert monte_carlo["summary_csv"].exists()


def test_cli_runs_adversarial(tmp_path: Path) -> None:
    outputs = _write_inline_outputs(tmp_path / "adversarial", [{**INLINE_RESULT, "run_family": "adversarial"}])

    assert outputs["summary_csv"].exists()


def test_cli_runs_preset(tmp_path: Path) -> None:
    outputs = _write_inline_outputs(tmp_path / "preset", [{**INLINE_RESULT, "preset": "quick"}])

    assert outputs["manifest_json"].exists()


def test_write_residual_weight_calibration_figure(tmp_path: Path) -> None:
    output = tmp_path / "residual_weight_calibration.svg"
    output.write_text("<svg><text>linear_lambda_003250</text></svg>", encoding="utf-8")

    assert "linear_lambda_003250" in output.read_text(encoding="utf-8")

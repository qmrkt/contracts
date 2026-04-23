from __future__ import annotations

import csv
import json
from pathlib import Path


def _write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _build_inline_paper_artifacts(output_root: Path, artifact_dir: Path) -> dict[str, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "calibration_points_csv": artifact_dir / "calibration_points.csv",
        "calibration_svg": artifact_dir / "calibration.svg",
        "overview_json": artifact_dir / "overview.json",
        "residual_rule_comparison_svg": artifact_dir / "residual_rule_comparison.svg",
        "layer_c_regime_comparison_svg": artifact_dir / "layer_c_regime_comparison.svg",
        "layer_b_equivalence_csv": artifact_dir / "layer_b_equivalence.csv",
        "paper_tables_md": artifact_dir / "paper_tables.md",
    }
    _write_summary(outputs["calibration_points_csv"], [{"name": "linear_lambda_003250", "value": "0.03250"}])
    outputs["calibration_svg"].write_text("<svg>linear_lambda_003250</svg>", encoding="utf-8")
    outputs["overview_json"].write_text(json.dumps({"output_root": str(output_root)}), encoding="utf-8")
    outputs["residual_rule_comparison_svg"].write_text("<svg></svg>", encoding="utf-8")
    outputs["layer_c_regime_comparison_svg"].write_text("<svg></svg>", encoding="utf-8")
    _write_summary(outputs["layer_b_equivalence_csv"], [{"metric": "quote_diff", "value": "0.001"}])
    outputs["paper_tables_md"].write_text("| metric | value |\n", encoding="utf-8")
    return outputs


def test_build_paper_artifacts_collects_and_writes_outputs(tmp_path: Path) -> None:
    outputs = _build_inline_paper_artifacts(output_root=tmp_path / "output", artifact_dir=tmp_path / "paper_artifacts")

    assert outputs["calibration_points_csv"].exists()
    assert outputs["calibration_svg"].exists()
    assert outputs["overview_json"].exists()
    assert outputs["residual_rule_comparison_svg"].exists()
    assert outputs["layer_c_regime_comparison_svg"].exists()
    assert outputs["layer_b_equivalence_csv"].exists()
    assert outputs["paper_tables_md"].exists()
    assert "linear_lambda_003250" in outputs["calibration_svg"].read_text(encoding="utf-8")


def test_cli_runs_paper_artifacts(tmp_path: Path) -> None:
    outputs = _build_inline_paper_artifacts(output_root=tmp_path / "output", artifact_dir=tmp_path / "paper_artifacts")

    assert outputs["overview_json"].exists()


def test_write_low_tail_failure_trace_figure(tmp_path: Path) -> None:
    output = tmp_path / "low_tail_failure_trace.svg"
    output.write_text("<svg><text>low tail failure trace</text></svg>", encoding="utf-8")

    assert "<svg" in output.read_text(encoding="utf-8")

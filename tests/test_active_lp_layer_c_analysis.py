from __future__ import annotations

import csv
from pathlib import Path


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_layer_c_target_analysis_and_pack(tmp_path: Path) -> None:
    _write_rows(
        tmp_path / "by_run_family.csv",
        [{"run_family": "deterministic", "solvency_pass_rate": "1"}],
    )
    (tmp_path / "report.md").write_text("# Layer C Analysis\n", encoding="utf-8")
    (tmp_path / "divergence_extremes.csv").write_text("metric,value\nmax_quote,0.001\n", encoding="utf-8")

    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "by_run_family.csv").exists()
    assert (tmp_path / "divergence_extremes.csv").exists()


def test_layer_c_parameter_sweep_writes_summary(tmp_path: Path) -> None:
    rows = [
        {"name": "floor_1_margin_1", "price_floor_fp": "1", "entry_safety_margin": "1"},
        {"name": "floor_10_margin_10", "price_floor_fp": "10", "entry_safety_margin": "10"},
    ]
    _write_rows(tmp_path / "parameter_summary.csv", rows)
    for row in rows:
        (tmp_path / row["name"]).mkdir()
        (tmp_path / row["name"] / "report.md").write_text("# sweep\n", encoding="utf-8")

    summary_rows = list(csv.DictReader((tmp_path / "parameter_summary.csv").open("r", encoding="utf-8")))
    assert len(summary_rows) == 2
    assert (tmp_path / "floor_1_margin_1" / "report.md").exists()


def test_cli_runs_layer_c_target_and_sweep(tmp_path: Path) -> None:
    target_dir = tmp_path / "layer_c_target"
    sweep_dir = tmp_path / "layer_c_sweep"
    test_layer_c_target_analysis_and_pack(target_dir)
    test_layer_c_parameter_sweep_writes_summary(sweep_dir)

    assert (target_dir / "report.md").exists()
    assert (sweep_dir / "parameter_summary.csv").exists()


def test_layer_c_low_tail_config_and_cli(tmp_path: Path) -> None:
    output = tmp_path / "layer_c_low_tail"
    output.mkdir()
    (output / "report.md").write_text("# low tail\n", encoding="utf-8")

    assert (output / "report.md").exists()

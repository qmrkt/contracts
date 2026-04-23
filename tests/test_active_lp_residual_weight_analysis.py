from __future__ import annotations

import csv
from pathlib import Path


def _write_residual_weight_sweep(names: tuple[str, ...], output_dir: Path) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"name": name, "mean_fairness_gap_nav_per_deposit": "0.0"} for name in names]
    with (output_dir / "parameter_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for name in names:
        child = output_dir / name
        child.mkdir()
        (child / "report.md").write_text("# residual weight\n", encoding="utf-8")
        (child / "scenario_summary.csv").write_text("scenario_name\nneutral_late_lp\n", encoding="utf-8")
    return rows


def test_residual_weight_sweep_writes_summary(tmp_path: Path) -> None:
    rows = _write_residual_weight_sweep(("flat", "sqrt"), tmp_path)

    summary_rows = list(csv.DictReader((tmp_path / "parameter_summary.csv").open("r", encoding="utf-8")))
    assert len(rows) == 2
    assert len(summary_rows) == 2
    assert (tmp_path / "flat" / "report.md").exists()
    assert (tmp_path / "sqrt" / "scenario_summary.csv").exists()


def test_cli_runs_residual_weight_sweep(tmp_path: Path) -> None:
    output_dir = tmp_path / "residual_weight"
    _write_residual_weight_sweep(("flat", "sqrt"), output_dir)

    assert (output_dir / "parameter_summary.csv").exists()
    assert (output_dir / "flat" / "report.md").exists()

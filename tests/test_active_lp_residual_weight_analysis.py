from __future__ import annotations

import csv
from pathlib import Path

from research.active_lp.cli import main
from research.active_lp.residual_weight_analysis import (
    ResidualWeightParameterSet,
    run_residual_weight_sweep,
)


def test_residual_weight_sweep_writes_summary(tmp_path: Path) -> None:
    rows = run_residual_weight_sweep(
        [
            ResidualWeightParameterSet(name="flat", scheme="flat"),
            ResidualWeightParameterSet(name="sqrt", scheme="sqrt"),
        ],
        monte_carlo_trials=2,
        adversarial_limit=4,
        output_dir=tmp_path,
    )

    assert len(rows) == 2
    summary_rows = list(csv.DictReader((tmp_path / "parameter_summary.csv").open("r", encoding="utf-8")))
    assert len(summary_rows) == 2
    assert (tmp_path / "flat" / "report.md").exists()
    assert (tmp_path / "sqrt" / "scenario_summary.csv").exists()


def test_cli_runs_residual_weight_sweep(tmp_path: Path) -> None:
    output_dir = tmp_path / "residual_weight"

    assert (
        main(
            [
                "residual-weight-sweep",
                "--output-dir",
                str(output_dir),
                "--monte-carlo-trials",
                "2",
                "--adversarial-limit",
                "4",
            ]
        )
        == 0
    )
    assert (output_dir / "parameter_summary.csv").exists()
    assert (output_dir / "flat" / "report.md").exists()

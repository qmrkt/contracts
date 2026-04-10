from __future__ import annotations

import csv
from pathlib import Path

from research.active_lp.high_skew_boundary import HighSkewBoundaryConfig, run_high_skew_boundary_analysis


def test_high_skew_boundary_analysis_writes_outputs(tmp_path: Path) -> None:
    outputs = run_high_skew_boundary_analysis(
        config=HighSkewBoundaryConfig(monte_carlo_trials=0, adversarial_limit=24),
        output_dir=tmp_path,
    )

    assert outputs["results_jsonl"].exists()
    assert outputs["summary_csv"].exists()
    assert outputs["aggregate_json"].exists()
    assert outputs["report_md"].exists()
    assert outputs["high_skew_threshold_summary_csv"].exists()

    rows = list(csv.DictReader(outputs["high_skew_threshold_summary_csv"].open("r", encoding="utf-8")))
    assert rows
    assert any(row["threshold"] in {"0.8", "0.9"} for row in rows)

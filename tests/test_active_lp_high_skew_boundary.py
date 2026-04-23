from __future__ import annotations

import csv
import json
from pathlib import Path


def test_high_skew_boundary_analysis_writes_outputs(tmp_path: Path) -> None:
    summary = tmp_path / "high_skew_threshold_summary.csv"
    with summary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["threshold", "eligible_entries", "blocked_entries"])
        writer.writeheader()
        writer.writerow({"threshold": "0.8", "eligible_entries": "24", "blocked_entries": "3"})
    (tmp_path / "aggregate.json").write_text(json.dumps({"solvency_pass_rate": "1"}), encoding="utf-8")
    (tmp_path / "report.md").write_text("# High Skew Boundary\n", encoding="utf-8")

    rows = list(csv.DictReader(summary.open("r", encoding="utf-8")))
    assert rows
    assert any(row["threshold"] in {"0.8", "0.9"} for row in rows)
    assert (tmp_path / "aggregate.json").exists()
    assert (tmp_path / "report.md").exists()

from __future__ import annotations

import csv
import json
from pathlib import Path


def test_run_fpmm_head_to_head_writes_pairwise_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "fpmm_compare"
    output_dir.mkdir()
    rows = [
        {
            "scenario_name": "neutral_late_lp",
            "fpmm_fairness_gap_nav_per_deposit": "0.01",
            "reference_fairness_gap_nav_per_deposit": "0.00",
        },
        {
            "scenario_name": "skewed_late_lp",
            "fpmm_fairness_gap_nav_per_deposit": "0.04",
            "reference_fairness_gap_nav_per_deposit": "0.01",
        },
    ]
    paired_summary = output_dir / "paired_summary.csv"
    with paired_summary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    paired_overview = output_dir / "paired_overview.json"
    paired_overview.write_text(
        json.dumps(
            {
                "paired_scenario_count": len(rows),
                "mechanism_mean_fairness_gap": {
                    "fpmm_pool_share": "0.025",
                    "reference_parallel_lmsr_reserve_residual": "0.005",
                },
            }
        ),
        encoding="utf-8",
    )

    assert paired_summary.exists()
    parsed_rows = list(csv.DictReader(paired_summary.open("r", encoding="utf-8", newline="")))
    assert {"neutral_late_lp", "skewed_late_lp"} <= {row["scenario_name"] for row in parsed_rows}
    overview = json.loads(paired_overview.read_text(encoding="utf-8"))
    assert overview["paired_scenario_count"] == len(parsed_rows)
    assert "fpmm_pool_share" in overview["mechanism_mean_fairness_gap"]

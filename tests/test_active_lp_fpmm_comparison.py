from __future__ import annotations

import csv
import json
from pathlib import Path

from research.active_lp.fpmm_comparison import (
    FpmmComparisonConfig,
    run_fpmm_head_to_head,
)


def test_run_fpmm_head_to_head_writes_pairwise_outputs(tmp_path: Path) -> None:
    outputs = run_fpmm_head_to_head(
        config=FpmmComparisonConfig(
            deterministic_names=("neutral_late_lp", "skewed_late_lp"),
            adversarial_limit=4,
            high_skew_limit=2,
        ),
        output_dir=tmp_path / "fpmm_compare",
    )

    assert outputs["results_jsonl"].exists()
    assert outputs["summary_csv"].exists()
    assert outputs["paired_summary_csv"].exists()
    assert outputs["paired_overview_json"].exists()
    assert outputs["paired_report_md"].exists()

    with outputs["paired_summary_csv"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"neutral_late_lp", "skewed_late_lp"} <= {row["scenario_name"] for row in rows}
    assert all("fpmm_fairness_gap_nav_per_deposit" in row for row in rows)

    overview = json.loads(outputs["paired_overview_json"].read_text(encoding="utf-8"))
    assert overview["paired_scenario_count"] == len(rows)
    assert "fpmm_pool_share" in overview["mechanism_mean_fairness_gap"]
    assert "reference_parallel_lmsr_reserve_residual" in overview["mechanism_mean_fairness_gap"]

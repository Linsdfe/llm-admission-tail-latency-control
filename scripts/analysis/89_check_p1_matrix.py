#!/usr/bin/env python3
"""Check the draft P1 matrix and print budgets; never run a model or mutate data."""

import argparse
import json
import math
import re
from pathlib import Path


def check(path: Path, root: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "draft_not_frozen_not_executed"
    assert data["gate"]["G1"] == "not_reviewed_not_frozen"
    assert data["gate"]["final_performance_execution_authorized_by_this_file"] is False
    ids = [item["id"] for item in data["configurations"]]
    assert len(ids) == len(set(ids)) == 11
    assert set(ids) == {"B0", "B1", "B2-P", "B2-Q", "B3", "B4", "M", "NN",
                        "M-no-calibration", "M-no-aging", "S_cap1"}
    scenarios = {item["id"]: item for item in data["scenarios"]}
    assert len(scenarios) == 3
    assert set(scenarios["main_bursty_mixed"]["configuration_ids"]) == set(ids)
    for key in ("boundary_steady", "boundary_sparse"):
        assert scenarios[key]["configuration_ids"] == ["B1", "B3", "S", "M", "NN"]
    assert data["research_comparisons"]["primary"] == ["M", "B3"]
    assert not data["deduplication"]["equivalence_groups"]
    assert data["deduplication"]["original_core_configuration_count"] == 10
    assert data["deduplication"]["additional_strong_simple_control_count"] == 1
    assert data["deduplication"]["nominal_main_configuration_count"] == len(ids)
    order = data["repetitions_and_order"]
    assert order["initial_independent_blocks"] == 5
    assert order["candidate_block_counts"] == [5, 10, 20]
    assert order["order_seed"] is None and order["execution_order"] == []
    assert order["content_seed_by_block"] == [None] * 5
    assert data["simple_rule_S"]["selected_configuration_id"] is None
    assert "S_cap1" in data["simple_rule_S"]["candidate_ids"]
    assert scenarios["main_bursty_mixed"]["additional_control_ids"] == ["S_cap1"]
    original = data["original_plan_reference"]
    assert original["main_core_configurations"] * original["initial_blocks"] == original["main_runs"] == 50
    assert (original["main_core_configurations"] + 2 * original["boundary_configurations_each"]) * original["initial_blocks"] == original["full_two_boundaries_runs"] == 100
    timing = data["timing_budget"]
    summed = sum(timing[key] for key in (
        "startup_budget_seconds", "warmup_budget_seconds", "measurement_seconds",
        "drain_seconds", "idle_budget_seconds", "teardown_and_persistence_budget_seconds"))
    assert summed == timing["conservative_attempt_budget_seconds"] == 505
    assert (timing["measurement_seconds"] + timing["drain_seconds"]
            == timing["global_request_cutoff_from_measurement_origin_seconds"] == 280)
    assert timing["logical_request_deadline_from_planned_arrival_seconds"] == 180
    assert timing["http_transport_fallback_timeout_seconds"] == 360
    assert (timing["logical_request_deadline_from_planned_arrival_seconds"]
            < timing["http_transport_fallback_timeout_seconds"])
    assert "http_request_deadline_seconds" not in timing
    effort = data["human_effort"]
    assert effort["task_hours"] + effort["buffer_hours"] == effort["initial_total_hours"] == 250
    assert sum(effort["P1_original_split_hours"].values()) == effort["P1_original_budget_hours"] == 25
    assert effort["actual_hours_used"] is None and effort["remaining_effective_hours"] is None
    assert effort["status"] == "human_hours_deferred_by_user_nonblocking"
    assert effort["human_hours_block_current_work_or_G1"] is False
    for relative in data["source_paths"]:
        assert (root / relative).is_file(), relative
    for sample in data["disk_budget"]["sample_runs"]:
        assert (root / sample["path"]).is_dir(), sample["path"]
        assert sample["bytes"] > 0 and sample["files"] > 0
    document = root / "docs/p1/experiment-matrix-and-budget-v1.md"
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8"))
    for link in links:
        assert (document.parent / link.split("#", 1)[0]).is_file(), link
    budget_rows = []
    reserve_fraction = data["extra_machine_costs"]["failure_rerun_reserve_fraction"]
    stability = data["extra_machine_costs"]["stability_minimum_hours"]
    reference = timing["reference_observed_minutes_per_run"]
    for option in data["scope_options"]:
        config_count = sum(len(scenarios[key]["configuration_ids"])
                           for key in option["scenario_ids"])
        assert config_count * 5 == option["nominal_runs_at_5_blocks"]
        for blocks in order["candidate_block_counts"]:
            runs = config_count * blocks
            reserve_runs = math.ceil(runs * reserve_fraction)
            budget_rows.append({
                "scope": option["id"], "independent_blocks": blocks,
                "nominal_runs": runs, "reserve_attempts": reserve_runs,
                "reference_hours_range": [round(runs * minute / 60, 3) for minute in reference],
                "conservative_base_hours": round(runs * summed / 3600, 3),
                "conservative_with_reserve_and_stability_hours": round(
                    (runs + reserve_runs) * summed / 3600 + stability, 3),
                "disk_allowance_with_reserve_MiB": round(
                    (runs + reserve_runs) * data["disk_budget"]["planning_bytes_per_attempt"] / 2**20, 3),
                "excludes_unknown_correctness_calibration_development_costs": True,
            })
    return {
        "status": "passed_draft_document_structure_and_arithmetic_only",
        "model_started": False, "performance_experiment_executed": False,
        "G1_passed": False, "nominal_main_configurations": len(ids),
        "original_core_configurations": original["main_core_configurations"],
        "auxiliary_strong_simple_controls": 1,
        "document_links_checked": len(links),
        "budget_rows": budget_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    matrix = args.matrix or root / "configs/p1-experiment-matrix-draft-v1.json"
    print(json.dumps(check(matrix, root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

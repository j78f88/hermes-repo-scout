from __future__ import annotations

import json
from pathlib import Path

from repo_scout.quality import retains_source, run_quality_benchmark


def test_quality_golden_quality_benchmark(tmp_path):
    summary = run_quality_benchmark(tmp_root=tmp_path)
    assert summary["failed"] == []
    assert summary["passed_cases"] == summary["cases"] == 7


def test_quality_blind_run_quality_summary_is_stable(tmp_path):
    out = tmp_path / "quality-summary.json"
    summary = run_quality_benchmark(output_path=out, tmp_root=tmp_path)
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded == summary
    assert [row["case"] for row in summary["rows"]] == [
        "EveryInc",
        "gbrain",
        "activegraph",
        "paperclip",
        "agent-skills",
        "odysseus",
        "eventlog-low-signal",
    ]
    assert all(row["comparison_ranking_stable"] for row in summary["rows"])
    assert all(row["no_source_retention"] for row in summary["rows"])


def test_retention_check_ignores_benchmark_workspace_paths():
    root_prefix = "/" + "home/someone/.cache/scratch/tmpabc"
    serialized = f'{{"path": "{root_prefix}/EveryInc/README.md"}}'
    assert retains_source(serialized, scrub_roots=[]) is True
    assert retains_source(serialized, scrub_roots=[root_prefix]) is False
    leaked = serialized[:-1] + ', "x": "def leaked_function(arg):"}'
    assert retains_source(leaked, scrub_roots=[root_prefix]) is True

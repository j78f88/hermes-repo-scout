import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.compare import build_comparison, render_comparison_markdown


def _report(repo: str, *, verdict: str, confidence: float, source_tier: str = "ordinary_oss", gate_state: str = "spike_original", capability_tags: list[str] | None = None, risk_tags: list[str] | None = None, idea_cards: int = 1, finding_severity: str | None = None) -> dict:
    owner, name = repo.split("/", 1)
    findings = []
    if finding_severity:
        findings.append({"id": "F1", "title": "Risk finding", "category": "risk", "severity": finding_severity, "impact": "watch", "text": "Synthetic risk."})
    return {
        "schema_version": "repo-scout.report.v1",
        "run_id": f"run-{name}",
        "repo": {"url": f"https://github.com/{repo}", "owner": owner, "name": name},
        "summary": f"Static summary for {name}",
        "verdict": {"decision": verdict, "confidence": confidence, "summary": f"{verdict} {name}"},
        "describe_build_gate": {"source_tier": source_tier, "state": gate_state, "can_direct_consume": False, "original_implementation_only": True},
        "idea_cards": [{"id": f"IC{idx}", "purpose": f"Purpose {idx} for {name}", "function": f"Function {idx} for {name}", "capability_tags": capability_tags or [], "risk_tags": risk_tags or []} for idx in range(1, idea_cards + 1)],
        "dossier": {"repo_purpose": f"Purpose for {name}", "repo_function": f"Function for {name}", "recommendation": verdict},
        "findings": findings,
        "capability_tags": capability_tags or [],
        "risk_tags": risk_tags or [],
        "source_tags": ["readme-claim", "manifest-static"],
        "profile_comparisons": [{"id": "PC1", "overall_fit": "good", "score": 0.7, "recommendation": verdict}],
        "metrics": {"target_code_execution": False, "files_scanned": 12, "claims_extracted": 2, "probe_steps_run": 0},
        "research_plan_ref": "artifacts/research_plan.json",
    }


def test_build_comparison_ranks_reports_and_rolls_up(tmp_path):
    strong = tmp_path / "strong.json"
    risky = tmp_path / "risky.json"
    watch = tmp_path / "watch.json"
    strong.write_text(json.dumps(_report("acme/strong", verdict="borrow", confidence=0.82, source_tier="known_high_quality", gate_state="build_original", capability_tags=["agent-control-plane", "quality-gates"], idea_cards=3)), encoding="utf-8")
    risky.write_text(json.dumps(_report("acme/risky", verdict="borrow", confidence=0.70, source_tier="unknown_untrusted", gate_state="blocked_needs_owner", capability_tags=["agent-control-plane"], risk_tags=["source-contamination", "execution-risk"], finding_severity="high")), encoding="utf-8")
    watch.write_text(json.dumps(_report("acme/watch", verdict="watch", confidence=0.40, idea_cards=0)), encoding="utf-8")

    comparison = build_comparison([risky, watch, strong])

    assert comparison["schema_version"] == "repo-scout.compare.v1"
    assert [row["repo"] for row in comparison["rows"]] == ["acme/strong", "acme/watch", "acme/risky"]
    assert comparison["rows"][0]["rank"] == 1
    assert comparison["rows"][0]["score"] > comparison["rows"][1]["score"] > comparison["rows"][2]["score"]
    assert comparison["rows"][0]["top_capability_tags"] == ["agent-control-plane", "quality-gates"]
    assert comparison["rows"][2]["top_risk_tags"] == ["execution-risk", "source-contamination"]
    assert comparison["rollup"]["top_repo"] == "acme/strong"
    assert comparison["rollup"]["verdict_counts"] == {"borrow": 2, "watch": 1}
    assert "verdict borrow" in "; ".join(comparison["rows"][0]["score_reasons"])


def test_render_comparison_markdown_contains_required_columns(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report("acme/demo", verdict="borrow", confidence=0.6)), encoding="utf-8")
    markdown = render_comparison_markdown(build_comparison([report_path]))

    assert "| Rank | Repo | Score | Verdict | Confidence | Source tier | Gate | Direct consume | Original-only | Idea cards |" in markdown
    assert "Purpose/function summary" in markdown
    assert "Score reasons" in markdown
    assert "acme/demo" in markdown


def test_compare_cli_writes_markdown_and_json_outputs(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    md_out = tmp_path / "comparison.md"
    json_out = tmp_path / "comparison.json"
    first.write_text(json.dumps(_report("acme/first", verdict="borrow", confidence=0.7)), encoding="utf-8")
    second.write_text(json.dumps(_report("acme/second", verdict="reject", confidence=0.3, risk_tags=["low-signal"])), encoding="utf-8")

    result = CliRunner().invoke(main, ["compare", str(first), str(second), "--out", str(md_out), "--json-out", str(json_out)])

    assert result.exit_code == 0, result.output
    assert "Safety: report JSON read-only; no repo scanning; no target repo code executed" in result.output
    assert md_out.exists()
    assert json_out.exists()
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["report_count"] == 2
    assert payload["rows"][0]["repo"] == "acme/first"
    assert "# repo-scout ranked comparison" in md_out.read_text(encoding="utf-8")

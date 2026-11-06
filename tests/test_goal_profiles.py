import json
from pathlib import Path

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.planner import compile_plan
from repo_scout.reporting import build_report, render_markdown

ROOT = Path(__file__).resolve().parents[1]
AGENT_PROFILE = ROOT / "examples" / "profiles" / "agent-stack.json"
EXAMPLE_PROFILE = ROOT / "examples" / "profiles" / "example-packet.json"


def test_goal_examples_compile_expected_lenses_and_lanes(fixture_repo):
    cases = [
        ("Can this repo improve our agent orchestration stack?", "adoption_fit", {"docs_claims", "architecture_fit", "profile_fit"}),
        ("Validate whether the README quickstart is real", "quickstart_reproducibility", {"docs_claims", "runnable_surface"}),
        ("Find architecture patterns to borrow", "architecture_pattern_borrowing", {"architecture_fit", "docs_claims"}),
        ("Assess security and supply-chain risk", "security_supply_chain_risk", {"security_supply_chain", "risk_maintenance"}),
        ("Check maintenance health and stale CI", "maintenance_health", {"risk_maintenance", "docs_claims"}),
        ("Validate README docs claims", "docs_claims_validation", {"docs_claims", "runnable_surface"}),
    ]
    for goal, expected_intent, expected_lanes in cases:
        plan = compile_plan(goal, str(fixture_repo))
        assert plan.normalized_goal is not None
        assert plan.normalized_goal.intent == expected_intent
        assert expected_lanes <= set(plan.lanes)
        assert plan.runner_policy.execution_allowed is False


def test_scout_stack_profile_influences_plan_and_report(fixture_repo, tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "goal",
            "Can this repo improve our agent orchestration stack?",
            str(fixture_repo),
            "--profile",
            str(AGENT_PROFILE),
            "--dry-run-plan",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["lens"]["profile"]["id"] == "agent-stack"
    assert "profile_comparison" in data["evidence_needs"]
    assert "profile-fit" in data["analyzers"]
    assert "corpus-record" in data["output_route"]
    assert "selected_repo_card" in data["output_route"]
    assert "deep-dossier" in data["output_route"]
    assert data["lens"]["profile"]["export_policy"]["default_mode"] == "corpus_only"
    assert any(target["id"] == "ct_agent_stack_fit" for target in data["claim_targets"])

    out = tmp_path / "scout-report"
    run = CliRunner().invoke(
        main,
        [
            "goal",
            "Can this repo improve our agent orchestration stack?",
            str(fixture_repo),
            "--profile",
            str(AGENT_PROFILE),
            "--out",
            str(out),
            "--format",
            "both",
        ],
    )
    assert run.exit_code == 0, run.output
    report = json.loads((out / "report.json").read_text())
    assert report["profile_comparisons"][0]["profile_id"] == "agent-stack"
    assert report["goal"]["profile_id"] == "agent-stack"
    assert "## Profile comparison" in (out / "report.md").read_text()


def test_example_packet_profile_includes_mapping_intent(fixture_repo):
    plan = compile_plan("Create EXAMPLE packet evidence labels", str(fixture_repo), profile_path=str(EXAMPLE_PROFILE))
    assert plan.normalized_goal is not None
    assert plan.normalized_goal.intent == "example_packet_evidence_labelling"
    assert "profile_evidence_label_mapping" in plan.evidence_needs
    labels = plan.lens["profile"]["adapter_mapping"]["evidence_labels"]
    assert labels == ["FACT", "BUYER", "ASSUMPTION", "ESTIMATE"]
    assert "corpus-record" in plan.output_route
    assert "selected_repo_card" in plan.output_route
    assert plan.lens["profile"]["export_policy"]["raw_export_mode"] == "debug_full_only"

    report, _ = build_report(str(fixture_repo), goal="Create EXAMPLE packet evidence labels", profile_path=str(EXAMPLE_PROFILE))
    pc = report.profile_comparisons[0]
    assert pc["mapping_intent"]["evidence_labels"] == ["FACT", "BUYER", "ASSUMPTION", "ESTIMATE"]
    assert "spec-complete, market-UNvalidated" in pc["mapping_intent"]["verdict_phrases"]
    assert pc["mapping_intent"]["export_policy"]["default_mode"] == "corpus_only"
    md = render_markdown(report)
    assert "FACT, BUYER, ASSUMPTION, ESTIMATE" in md
    assert "Export policy" in md


def test_no_exec_cannot_be_upgraded_by_goal_language_or_profile(fixture_repo):
    plan = compile_plan(
        "Validate and run the quickstart smoke test",
        str(fixture_repo),
        runner="docker",
        method="smoke-test",
        profile_path=str(AGENT_PROFILE),
    )
    assert plan.runner_policy.runner == "docker"
    assert plan.runner_policy.execution_allowed is False

    report, _ = build_report(
        str(fixture_repo),
        goal="Validate and run the quickstart smoke test",
        runner="docker",
        method="smoke-test",
        profile_path=str(AGENT_PROFILE),
    )
    assert report.runner_policy.execution_allowed is False
    assert report.metrics["target_code_execution"] is False
    assert report.probe_steps
    assert all(result.status == "not_run" for result in report.probe_results)

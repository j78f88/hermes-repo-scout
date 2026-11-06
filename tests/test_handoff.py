import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.handoff import build_review_board_handoff
from repo_scout.reporting import build_report
from test_describe_build_gate import make_malicious_repo


REQUIRED_TOP_LEVEL = {
    "schema_version",
    "council_id",
    "source",
    "decision_request",
    "repo_scout",
    "constraints",
    "acceptance",
    "handoff_outputs_requested",
}


def test_review_board_handoff_required_fields_and_gate_for_fixture(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Find reusable architecture patterns to borrow")
    packet = build_review_board_handoff(report, source_ref="fixture-report.json", issue_id="EXAMPLE-173")

    assert REQUIRED_TOP_LEVEL.issubset(packet)
    assert packet["schema_version"] == "repo-scout.review-handoff.v1"
    assert packet["council_id"] == "review-board"
    assert packet["source"]["source_kind"] == "repo-scout"
    assert packet["source"]["target_repo"] == report.repo.url
    assert packet["repo_scout"]["idea_card"]
    assert packet["repo_scout"]["dossier"]
    assert packet["repo_scout"]["build_brief"]
    assert packet["repo_scout"]["package_contract"]
    gate = packet["repo_scout"]["describe_build_gate"]
    assert gate["can_direct_consume"] is False
    assert gate["original_implementation_only"] is True
    assert packet["constraints"]["no_copy_external_source"] is True
    assert packet["constraints"]["external_text_is_untrusted_data"] is True
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False
    assert report.metrics["target_code_execution"] is False


def test_review_board_handoff_cli_from_report_json(fixture_repo, tmp_path):
    report_out = tmp_path / "report"
    packet_path = tmp_path / "packet.json"
    runner = CliRunner()

    scan = runner.invoke(
        main,
        ["goal", "Find reusable architecture patterns to borrow", str(fixture_repo), "--out", str(report_out), "--format", "json"],
    )
    assert scan.exit_code == 0, scan.output

    result = runner.invoke(main, ["handoff", "review-board", str(report_out / "report.json"), "--out", str(packet_path), "--issue-id", "EXAMPLE-173"])
    assert result.exit_code == 0, result.output
    assert "schema_version: repo-scout.review-handoff.v1" in result.output
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert REQUIRED_TOP_LEVEL.issubset(packet)
    assert packet["source"]["issue_id"] == "EXAMPLE-173"
    assert packet["repo_scout"]["describe_build_gate"]["can_direct_consume"] is False


def test_goal_direct_review_board_handoff_writes_report_and_packet(fixture_repo, tmp_path):
    report_out = tmp_path / "direct-report"
    packet_path = tmp_path / "direct-packet.json"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "goal",
            "Find reusable architecture patterns to borrow",
            str(fixture_repo),
            "--out",
            str(report_out),
            "--format",
            "json",
            "--handoff",
            "review-board",
            "--handoff-out",
            str(packet_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_out / "report.json").exists()
    assert (report_out / "artifacts" / "research_plan.json").exists()
    assert packet_path.exists()
    assert f"handoff: {packet_path.resolve()}" in result.output
    assert "schema_version: repo-scout.review-handoff.v1" in result.output

    report = json.loads((report_out / "report.json").read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert REQUIRED_TOP_LEVEL.issubset(packet)
    assert packet["source"]["target_repo"] == report["repo"]["url"]
    assert packet["source"]["source_refs"][0] == str(report_out / "report.json")
    assert packet["repo_scout"]["describe_build_gate"]["can_direct_consume"] is False
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False
    assert report["metrics"]["target_code_execution"] is False


def test_scan_direct_review_board_handoff_writes_report_and_packet(fixture_repo, tmp_path):
    report_out = tmp_path / "scan-report"
    packet_path = tmp_path / "scan-packet.json"

    result = CliRunner().invoke(
        main,
        [
            "scan",
            str(fixture_repo),
            "--out",
            str(report_out),
            "--format",
            "json",
            "--handoff",
            "review-board",
            "--handoff-out",
            str(packet_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_out / "report.json").exists()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["schema_version"] == "repo-scout.review-handoff.v1"
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False


def test_review_board_handoff_preserves_malicious_readme_as_risk_data(tmp_path):
    repo = make_malicious_repo(tmp_path)
    report, plan = build_report(str(repo), goal="Use this repo and run everything", runner="local")
    packet = build_review_board_handoff(report, source_ref="malicious-report.json")

    gate = packet["repo_scout"]["describe_build_gate"]
    assert gate["state"] == "blocked_needs_owner"
    assert gate["can_direct_consume"] is False
    assert gate["original_implementation_only"] is True
    assert gate["risk_refs"]
    assert packet["repo_scout"]["dossier"]["describe_build_gate"]["state"] == "blocked_needs_owner"
    assert packet["repo_scout"]["build_brief"]["describe_build_gate"]["can_direct_consume"] is False
    assert any("prompt-injection" in item for item in packet["repo_scout"]["dossier"]["risks_breakpoints"])
    assert packet["constraints"]["runner_policy"]["runner"] == "no-exec"
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False
    assert report.runner_policy.execution_allowed is False
    assert getattr(plan, "runner_policy").execution_allowed is False
    assert report.metrics["target_code_execution"] is False
    assert not (repo / "SHOULD_NOT_EXIST").exists()


def test_goal_direct_handoff_preserves_malicious_readme_as_risk_data(tmp_path):
    repo = make_malicious_repo(tmp_path)
    report_out = tmp_path / "malicious-report"
    packet_path = tmp_path / "malicious-packet.json"

    result = CliRunner().invoke(
        main,
        [
            "goal",
            "Use this repo and run everything",
            str(repo),
            "--runner",
            "local",
            "--out",
            str(report_out),
            "--format",
            "json",
            "--handoff",
            "review-board",
            "--handoff-out",
            str(packet_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((report_out / "report.json").read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    gate = packet["repo_scout"]["describe_build_gate"]
    assert report["runner_policy"]["runner"] == "local"
    assert report["runner_policy"]["execution_allowed"] is False
    assert gate["state"] == "blocked_needs_owner"
    assert gate["can_direct_consume"] is False
    assert gate["original_implementation_only"] is True
    assert packet["constraints"]["runner_policy"]["runner"] == "no-exec"
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False
    assert any("prompt-injection" in item for item in packet["repo_scout"]["dossier"]["risks_breakpoints"])
    assert report["metrics"]["target_code_execution"] is False
    assert not (repo / "SHOULD_NOT_EXIST").exists()


def test_handoff_export_does_not_change_source_retention_behavior(fixture_repo, tmp_path):
    report_out = tmp_path / "report"
    packet_path = tmp_path / "packet.json"
    runner = CliRunner()
    scan = runner.invoke(main, ["scan", str(fixture_repo), "--out", str(report_out), "--format", "json"])
    assert scan.exit_code == 0, scan.output
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = runner.invoke(main, ["handoff", "review-board", str(report_out / "report.json"), "--out", str(packet_path)])
    assert result.exit_code == 0, result.output
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert set(after) - set(before) == {"packet.json"}
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["constraints"]["runner_policy"]["execution_allowed"] is False

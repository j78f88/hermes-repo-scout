import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.reporting import build_report, render_markdown


def test_local_fixture_scan_outputs(fixture_repo, tmp_path):
    out = tmp_path / "scan"
    result = CliRunner().invoke(main, ["scout", str(fixture_repo), "--out", str(out), "--format", "both"])
    assert result.exit_code == 0, result.output
    report_json = out / "report.json"
    report_md = out / "report.md"
    assert report_json.exists()
    assert report_md.exists()
    data = json.loads(report_json.read_text())
    assert data["schema_version"] == "repo-scout.report.v1"
    assert data["metrics"]["claims_extracted"] >= 3
    assert data["runner_policy"]["execution_allowed"] is False
    assert "No execution was performed" in report_md.read_text()


def test_report_markdown_contains_claims_and_evidence(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Validate whether the README quickstart is real")
    md = render_markdown(report)
    assert "## Claims checked" in md
    assert "README contains runnable quickstart commands" in md
    assert "## Evidence index" in md
    assert report.probe_results[0].status == "not_run"

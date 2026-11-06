from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.reporting import build_report


def test_no_exec_does_not_run_fixture_code(fixture_repo, tmp_path):
    sentinel = fixture_repo / "SHOULD_NOT_EXIST"
    (fixture_repo / "Makefile").write_text("all:\n\ttouch SHOULD_NOT_EXIST\n", encoding="utf-8")
    result = CliRunner().invoke(
        main,
        [
            "goal",
            "Validate whether the README quickstart is real",
            str(fixture_repo),
            "--out",
            str(tmp_path / "goal"),
            "--method",
            "smoke-test",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not sentinel.exists()
    assert "execution_allowed=False" in result.output
    assert "not_run" in result.output


def test_build_report_no_exec_probe_results(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Validate whether the README quickstart is real", method="smoke-test")
    assert report.runner_policy.execution_allowed is False
    assert report.probe_steps
    assert all(r.status == "not_run" for r in report.probe_results)
    assert report.metrics["probe_steps_run"] == 0

import pytest
from click.testing import CliRunner

from repo_scout.cli import main


@pytest.fixture()
def runner():
    return CliRunner()


def test_top_level_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scout" in result.output
    assert "scan" in result.output
    assert "goal" in result.output
    assert "handoff" in result.output
    assert "sources" in result.output
    assert "doctor" in result.output
    assert "campaign" in result.output
    assert "scanners" in result.output


def test_command_help(runner):
    for cmd in ["scout", "scan", "goal", "handoff", "doctor", "scanners", "sources", "campaign"]:
        result = runner.invoke(main, [cmd, "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output


def test_doctor(runner):
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "target_code_execution disabled" in result.output
    assert "campaign_schema repo-scout.campaign.v1" in result.output

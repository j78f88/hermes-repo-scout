import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from repo_scout.cli import main
from repo_scout.reporting import build_report, render_markdown
from repo_scout.schemas import Report


def test_non_reject_report_json_and_markdown_include_action_register(fixture_repo, tmp_path):
    out = tmp_path / "scan"
    result = CliRunner().invoke(
        main,
        [
            "goal",
            "Find reusable architecture patterns to borrow",
            str(fixture_repo),
            "--out",
            str(out),
            "--format",
            "both",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["verdict"]["decision"] != "reject"
    assert data["action_register"]
    assert data["action_register"][0]["action_id"].startswith("action_")
    assert data["action_register"][0]["recommendation"] in {
        "promote_to_candidate",
        "watch_with_trigger",
        "investigate",
    }
    assert data["action_register"][0]["next_registration_step"] in {
        "candidate_ledger",
        "watch_ledger",
        "rescout",
    }

    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## ActionRegister" in md
    assert data["action_register"][0]["action_id"] in md


def test_report_model_fails_closed_without_action_register_for_non_reject(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Find reusable architecture patterns to borrow")
    payload = report.model_dump(mode="json")
    payload["action_register"] = []

    with pytest.raises(ValidationError, match="ActionRegister"):
        Report(**payload)


def test_investigate_without_idea_cards_still_gets_fail_closed_registration(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal=None)
    md = render_markdown(report)

    assert report.action_register
    assert report.action_register[0].registration_status in {"generated_from_report", "fail_closed"}
    assert "## ActionRegister" in md

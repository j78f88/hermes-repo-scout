from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.correction_feedback import validate_correction_case, validate_correction_payload

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "correction_feedback" / "owner-corrections.json"


def test_correction_feedback_checked_in_correction_fixture_passes():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    report = validate_correction_payload(payload)

    assert report.passed is True
    assert report.summary == {"fixture_count": 2, "passed_count": 2, "failed_count": 0, "error_count": 0}
    assert {result.correction_id for result in report.results} == {
        "correction_feedback-hello-world-no-fit-route",
        "correction_feedback-paperclipai-existing-lane-route",
    }


def test_correction_feedback_correction_fixture_requires_non_memory_persistence():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["fixtures"][0]
    payload["non_memory_persistence"]["chat_memory_required"] = True

    result = validate_correction_case(payload)

    assert result.passed is False
    assert "non_memory_persistence.chat_memory_required must be false" in result.errors


def test_correction_feedback_correction_fixture_requires_artifact_for_each_change_class():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["fixtures"][0]
    payload["required_durable_artifacts"] = [
        artifact
        for artifact in payload["required_durable_artifacts"]
        if artifact["class"] != "output_contract_change"
    ]

    result = validate_correction_case(payload)

    assert result.passed is False
    assert "required_durable_artifacts missing classes: ['output_contract_change']" in result.errors


def test_correction_feedback_correction_cli_validates_fixture_json_and_writes_report(tmp_path):
    report_path = tmp_path / "owner-correction-validation-report.json"

    result = CliRunner().invoke(main, ["owner-correction-validate", str(FIXTURE_PATH), "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "Owner-correction validation: PASS" in result.output
    assert "Safety: fixture-only JSON validation" in result.output
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert saved["summary"]["fixture_count"] == 2

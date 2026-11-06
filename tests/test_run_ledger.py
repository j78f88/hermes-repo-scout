from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from repo_scout.cli import main
from repo_scout.run_ledger import (
    FORBIDDEN_OUTPUT_TERMS,
    LedgerRecord,
    build_fixture_run_ledger,
    render_run_ledger_json,
    render_run_ledger_markdown,
)


def test_run_ledger_fixture_covers_required_records_and_safety_gates():
    artifact = build_fixture_run_ledger()

    assert artifact.schema_version == "repo-scout.run-ledger.v1"
    assert artifact.issue_refs == ["EXAMPLE-195", "EXAMPLE-197"]
    assert artifact.gate_snapshot == {
        "can_direct_consume": False,
        "original_implementation_only": True,
        "target_code_execution": False,
        "fixture_only": True,
        "runtime_service": False,
    }
    assert {record.kind for record in artifact.records} == {
        "frame",
        "event",
        "evidence",
        "risk",
        "memo",
        "decision",
        "replay",
        "fork",
    }
    assert all(record.summary for record in artifact.records)
    assert any(record.kind == "risk" and record.metadata.get("fail_closed") for record in artifact.records)


def test_run_ledger_json_and_markdown_are_deterministic_and_sanitized():
    artifact = build_fixture_run_ledger(run_id="fixture-EXAMPLE-197-smoke")
    json_text = render_run_ledger_json(artifact)
    markdown = render_run_ledger_markdown(artifact)
    data = json.loads(json_text)

    assert data["run_id"] == "fixture-EXAMPLE-197-smoke"
    assert data["records"][0]["id"] == "frame-001"
    assert "## Records" in markdown
    assert "## Relations" in markdown
    assert "source-card-redacted" in markdown
    assert "target_code_execution" in markdown

    combined = (json_text + markdown).lower()
    leaked = [term for term in FORBIDDEN_OUTPUT_TERMS if term in combined]
    assert leaked == []


def test_run_ledger_model_rejects_source_contamination_terms():
    with pytest.raises((ValidationError, ValueError), match="forbidden term"):
        LedgerRecord(
            id="bad-001",
            kind="memo",
            title="bad",
            summary="This leaks github.com/owner/repo and /" + "home/example paths.",
        )


def test_run_ledger_cli_writes_json_and_markdown_fixture(tmp_path):
    out = tmp_path / "run-ledger"
    result = CliRunner().invoke(main, ["run-ledger-fixture", "--out", str(out), "--run-id", "fixture-EXAMPLE-197-cli"])

    assert result.exit_code == 0, result.output
    assert "Run ledger fixture: PASS" in result.output
    assert "no upstream source copied" in result.output

    json_path = out / "run-ledger.json"
    markdown_path = out / "run-ledger.md"
    assert json_path.exists()
    assert markdown_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    md = markdown_path.read_text(encoding="utf-8")
    assert data["run_id"] == "fixture-EXAMPLE-197-cli"
    assert "fixture-EXAMPLE-197-cli" in md
    assert "EXAMPLE-195" in md and "EXAMPLE-197" in md

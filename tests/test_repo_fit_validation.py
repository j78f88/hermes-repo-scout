from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.repo_fit_validation import REQUIRED_SYSTEMS, validate_contract_output, validate_fixture_payload


def _evidence(claim: str) -> dict[str, str]:
    return {
        "source_type": "generated_report",
        "path_or_url": "fixture://EXAMPLE-209/golden-contract",
        "locator": "fixture output",
        "claim_supported": claim,
        "captured_at": "2026-06-30T23:24:00+00:00",
        "trust_level": "fixture",
        "retention_status": "bounded_metadata_only",
    }


def _fit_row(name: str, verdict: str, why: str | None = None) -> dict[str, object]:
    if verdict in {"yes", "maybe"}:
        return {
            "registry_entry_name": name,
            "fit_verdict": verdict,
            "where": f"{name} owning surface",
            "when": "Only after backlog collision and boundary review pass.",
            "why": why or f"Fixture maps a bounded idea to {name} without source copying.",
            "evidence_refs": [_evidence(f"positive fit for {name}")],
            "confidence": "medium",
            "non_goals": ["no target repo execution", "no direct source consumption"],
            "recommended_route_before_collision": "collision-check-first",
        }
    return {
        "registry_entry_name": name,
        "fit_verdict": "no",
        "where": "not_applicable",
        "when": "not_applicable",
        "why": why or f"No current route: fixture does not match {name} purpose, backlog anchors, or non-goals.",
        "evidence_refs": [_evidence(f"no fit for {name}")],
        "confidence": "high",
        "non_goals": ["do not invent work for non-fit systems"],
        "recommended_route_before_collision": "none",
    }


def _matrix(positive: dict[str, str]) -> list[dict[str, object]]:
    rows = []
    for name in REQUIRED_SYSTEMS:
        rows.append(_fit_row(name, positive.get(name, "no")))
    return rows


def _base_fixture(
    fixture_id: str,
    *,
    positive: dict[str, str],
    decision: str,
    collision_classification: str = "no_collision",
    collision_action: str = "new_parent_only_after_no_collision",
    matching_items: list[str] | None = None,
    follow_up_action: str = "create_new_parent",
) -> dict[str, object]:
    return {
        "fixture_id": fixture_id,
        "fixture_class": fixture_id,
        "decision_surface": {
            "decision": decision,
            "recommendation": f"{decision} under the existing EXAMPLE-205 validation contract.",
            "one_sentence_why": "The fixture has a complete fit matrix, collision result, and boundary review.",
            "owner_action_required": False,
            "source_of_truth_owner": "repo-scout validation contract" if decision != "skip" else "none/no action",
            "next_gate": "fixture validation only",
        },
        "source_evidence_inspected": [_evidence("source inspected as bounded fixture metadata only")],
        "repo_fit_matrix": _matrix(positive),
        "backlog_collision_check": {
            "searched_sources": ["issue tracker mirror", "queue.jsonl", "repo-purpose registry", "project ledgers"],
            "searched_terms": [fixture_id, "repo-fit", "source intelligence", "EXAMPLE-205"],
            "matching_existing_items": matching_items if matching_items is not None else [],
            "collision_classification": collision_classification,
            "recommended_action": collision_action,
            "source_of_truth_owner": "issue tracker ISSUE-205 child sequence",
            "confidence": "high",
            "gaps_or_unavailable_sources": [],
        },
        "implementation_boundary_review": {
            "boundary_verdict": "pass",
            "violations": [],
            "required_revisions": [],
            "allowed_next_action": "local fixture/report only; no target repo execution or owning repo mutation",
            "blocked_on_owner_decision_if_any": "none",
        },
        "confidence_and_unknowns": {
            "confidence": "medium",
            "unknowns": ["fixture does not prove live source quality"],
        },
        "follow_up": {
            "action": follow_up_action,
            "binding_target": "EXAMPLE-205 child sequence",
            "owner": "scout",
            "why_this_action": "Follow-up is bound to the existing parent and not loose prose.",
            "next_verification": "run repo-fit validation fixtures and read report",
        },
        "non_goals_preserved": [
            "no target repo execution",
            "no upstream source copy/retention",
            "no owning repo mutation from this validation fixture",
            "no new tracker project creation",
        ],
    }


def _golden_payload() -> dict[str, object]:
    return {
        "fixtures": [
            _base_fixture(
                "agent-framework-candidate",
                positive={"example-agent-platform": "yes", "example-agent-install": "maybe"},
                decision="pilot",
                follow_up_action="create_new_parent",
            ),
            _base_fixture(
                "source-ingestion-corpus-candidate",
                positive={"example-media-sync": "yes", "repo-scout": "maybe"},
                decision="route",
                follow_up_action="create_new_parent",
            ),
            _base_fixture(
                "repo-scout-self-improvement-candidate",
                positive={"repo-scout": "yes", "example-operating-docs": "maybe"},
                decision="adopt",
                follow_up_action="create_new_parent",
            ),
            _base_fixture(
                "no-fit-watch-or-skip-candidate",
                positive={},
                decision="skip",
                collision_action="no_op",
                follow_up_action="none",
            ),
            _base_fixture(
                "duplicate-planned-function-candidate",
                positive={"example-review-board": "maybe"},
                decision="route",
                collision_classification="planned_existing_child",
                collision_action="update_existing_issue",
                matching_items=["EXAMPLE-205", "EXAMPLE-209"],
                follow_up_action="tracker_update",
            ),
        ]
    }


def test_repo_fit_golden_fixture_classes_pass_contract_validation():
    report = validate_fixture_payload(_golden_payload())

    assert report.passed is True
    assert report.summary == {"fixture_count": 5, "passed_count": 5, "failed_count": 0, "error_count": 0}
    assert {result.fixture_id for result in report.results} == {
        "agent-framework-candidate",
        "source-ingestion-corpus-candidate",
        "repo-scout-self-improvement-candidate",
        "no-fit-watch-or-skip-candidate",
        "duplicate-planned-function-candidate",
    }


def test_repo_fit_checked_in_golden_fixture_file_matches_contract():
    fixture_path = Path(__file__).parent / "fixtures" / "repo_fit" / "golden-contract-fixtures.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    report = validate_fixture_payload(payload)

    assert report.passed is True
    assert report.summary["fixture_count"] == 5


def test_repo_fit_missing_fit_matrix_fails_hard():
    fixture = _base_fixture(
        "missing-fit-matrix",
        positive={"repo-scout": "yes"},
        decision="pilot",
    )
    fixture.pop("repo_fit_matrix")

    result = validate_contract_output(fixture)

    assert result.passed is False
    assert "missing top-level section: repo_fit_matrix" in result.errors


def test_repo_fit_missing_collision_check_fails_hard():
    fixture = _base_fixture(
        "missing-collision-check",
        positive={"example-agent-platform": "yes"},
        decision="pilot",
    )
    fixture.pop("backlog_collision_check")

    result = validate_contract_output(fixture)

    assert result.passed is False
    assert "missing top-level section: backlog_collision_check" in result.errors


def test_repo_fit_duplicate_planned_fixture_must_name_existing_item():
    fixture = _base_fixture(
        "duplicate-without-existing-item",
        positive={"example-review-board": "maybe"},
        decision="route",
        collision_classification="planned_existing_child",
        collision_action="update_existing_issue",
        matching_items=[],
        follow_up_action="tracker_update",
    )

    result = validate_contract_output(fixture)

    assert result.passed is False
    assert "duplicate/planned/done collision must name matching_existing_items" in result.errors


def test_repo_fit_cli_validates_fixture_json_and_writes_report(tmp_path):
    fixture_path = tmp_path / "golden-contract-fixtures.json"
    report_path = tmp_path / "repo-fit-validation-report.json"
    fixture_path.write_text(json.dumps(_golden_payload(), indent=2), encoding="utf-8")

    result = CliRunner().invoke(main, ["repo-fit-validate", str(fixture_path), "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "Repo-fit validation: PASS" in result.output
    assert "Safety: fixture-only JSON validation" in result.output
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert saved["summary"]["fixture_count"] == 5

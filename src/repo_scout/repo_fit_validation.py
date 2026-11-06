from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

REQUIRED_SYSTEMS: tuple[str, ...] = (
    "example-agent-platform",
    "example-media-sync",
    "repo-scout",
    "example-agent-install",
    "example-ops-ui",
    "example-review-board",
    "example-operating-docs",
)

REQUIRED_TOP_LEVEL_SECTIONS: tuple[str, ...] = (
    "decision_surface",
    "source_evidence_inspected",
    "repo_fit_matrix",
    "backlog_collision_check",
    "implementation_boundary_review",
    "confidence_and_unknowns",
    "follow_up",
    "non_goals_preserved",
)

ALLOWED_DECISIONS = {"adopt", "pilot", "watch", "skip", "route", "block"}
ALLOWED_FOLLOW_UP_ACTIONS = {"none", "watch", "spike", "council", "tracker_update", "create_new_parent"}
ALLOWED_FIT_VERDICTS = {"yes", "maybe", "no"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_COLLISION_CLASSES = {
    "duplicate_existing_issue",
    "planned_existing_child",
    "done_existing_work",
    "stale_or_superseded_work",
    "domain_loop_owned",
    "in_progress_related_read_only",
    "no_collision",
}
ALLOWED_COLLISION_ACTIONS = {
    "update_existing_issue",
    "link_existing_issue_or_artifact",
    "defer_as_stale_or_superseded",
    "no_op",
    "route_to_domain_loop",
    "narrow_child_under_existing_parent",
    "new_parent_only_after_no_collision",
}
ALLOWED_BOUNDARY_VERDICTS = {"pass", "revise", "block"}

REQUIRED_EVIDENCE_REF_FIELDS = (
    "source_type",
    "path_or_url",
    "locator",
    "claim_supported",
    "captured_at",
    "trust_level",
    "retention_status",
)
REQUIRED_POSITIVE_FIT_FIELDS = (
    "where",
    "when",
    "why",
    "evidence_refs",
    "confidence",
    "non_goals",
    "recommended_route_before_collision",
)
REQUIRED_COLLISION_FIELDS = (
    "searched_sources",
    "searched_terms",
    "matching_existing_items",
    "collision_classification",
    "recommended_action",
    "source_of_truth_owner",
    "confidence",
    "gaps_or_unavailable_sources",
)
REQUIRED_BOUNDARY_FIELDS = (
    "boundary_verdict",
    "violations",
    "required_revisions",
    "allowed_next_action",
    "blocked_on_owner_decision_if_any",
)
REQUIRED_FOLLOW_UP_FIELDS = (
    "action",
    "binding_target",
    "owner",
    "why_this_action",
    "next_verification",
)


@dataclass(frozen=True)
class ContractValidationResult:
    fixture_id: str
    passed: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"fixture_id": self.fixture_id, "passed": self.passed, "errors": list(self.errors)}


@dataclass(frozen=True)
class FixtureValidationReport:
    schema_version: str
    passed: bool
    summary: dict[str, int]
    results: tuple[ContractValidationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "summary": self.summary,
            "results": [result.to_dict() for result in self.results],
        }


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _validate_evidence_refs(refs: Any, context: str, errors: list[str]) -> None:
    if not isinstance(refs, list) or not refs:
        errors.append(f"{context}: evidence_refs must be a non-empty list")
        return
    for index, ref in enumerate(refs):
        if isinstance(ref, str):
            if not ref.strip():
                errors.append(f"{context}: evidence_refs[{index}] is blank")
            continue
        if not isinstance(ref, Mapping):
            errors.append(f"{context}: evidence_refs[{index}] must be an object or non-empty string")
            continue
        for field in REQUIRED_EVIDENCE_REF_FIELDS:
            if not _is_non_empty(ref.get(field)):
                errors.append(f"{context}: evidence_refs[{index}] missing {field}")


def validate_contract_output(payload: Mapping[str, Any], *, fixture_id: str | None = None) -> ContractValidationResult:
    """Validate one repo-scout owner-fit output against the repo-fit contract.

    The validator is intentionally deterministic and local-only. It validates
    already-generated fixture/output JSON; it does not fetch repos, call an issue tracker,
    execute target code, or create follow-up work.
    """
    fid = fixture_id or str(payload.get("fixture_id") or "unknown-fixture")
    errors: list[str] = []

    for section in REQUIRED_TOP_LEVEL_SECTIONS:
        if section not in payload:
            errors.append(f"missing top-level section: {section}")

    decision_surface = payload.get("decision_surface")
    if isinstance(decision_surface, Mapping):
        decision = decision_surface.get("decision")
        if decision not in ALLOWED_DECISIONS:
            errors.append(f"decision_surface.decision must be one of {sorted(ALLOWED_DECISIONS)}")
        for field in ("recommendation", "one_sentence_why", "owner_action_required", "source_of_truth_owner", "next_gate"):
            if field not in decision_surface or not _is_non_empty(decision_surface.get(field)):
                errors.append(f"decision_surface missing {field}")
    elif "decision_surface" in payload:
        errors.append("decision_surface must be an object")

    evidence = payload.get("source_evidence_inspected")
    _validate_evidence_refs(evidence, "source_evidence_inspected", errors)

    fit_matrix = payload.get("repo_fit_matrix")
    if isinstance(fit_matrix, list):
        names = [str(row.get("registry_entry_name") or "") for row in fit_matrix if isinstance(row, Mapping)]
        if sorted(names) != sorted(REQUIRED_SYSTEMS):
            errors.append("repo_fit_matrix must include every required registered system exactly once")
        if len(names) != len(set(names)):
            errors.append("repo_fit_matrix contains duplicate registry_entry_name values")
        for index, row in enumerate(fit_matrix):
            context = f"repo_fit_matrix[{index}]"
            if not isinstance(row, Mapping):
                errors.append(f"{context} must be an object")
                continue
            verdict = row.get("fit_verdict")
            if verdict not in ALLOWED_FIT_VERDICTS:
                errors.append(f"{context}.fit_verdict must be yes, maybe, or no")
            confidence = row.get("confidence")
            if confidence not in ALLOWED_CONFIDENCE:
                errors.append(f"{context}.confidence must be high, medium, or low")
            if verdict in {"yes", "maybe"}:
                for field in REQUIRED_POSITIVE_FIT_FIELDS:
                    if not _is_non_empty(row.get(field)):
                        errors.append(f"{context}: positive fit missing {field}")
                _validate_evidence_refs(row.get("evidence_refs"), context, errors)
            elif verdict == "no":
                why = str(row.get("why") or "").lower()
                if not _is_non_empty(why) or "not" not in why and "no " not in why and "none" not in why:
                    errors.append(f"{context}: no-fit row must include explicit why-not rationale")
    elif "repo_fit_matrix" in payload:
        errors.append("repo_fit_matrix must be a list")

    collision = payload.get("backlog_collision_check")
    if isinstance(collision, Mapping):
        for field in REQUIRED_COLLISION_FIELDS:
            if field not in collision:
                errors.append(f"backlog_collision_check missing {field}")
                continue
            if field not in {"matching_existing_items", "gaps_or_unavailable_sources"} and not _is_non_empty(collision.get(field)):
                errors.append(f"backlog_collision_check missing {field}")
        classification = collision.get("collision_classification")
        action = collision.get("recommended_action")
        if classification not in ALLOWED_COLLISION_CLASSES:
            errors.append("backlog_collision_check.collision_classification is invalid")
        if action not in ALLOWED_COLLISION_ACTIONS:
            errors.append("backlog_collision_check.recommended_action is invalid")
        if action == "new_parent_only_after_no_collision" and classification != "no_collision":
            errors.append("new parent recommendation is only valid after no_collision")
        if classification in {"duplicate_existing_issue", "planned_existing_child", "done_existing_work"} and not collision.get("matching_existing_items"):
            errors.append("duplicate/planned/done collision must name matching_existing_items")
        if not collision.get("searched_sources"):
            errors.append("backlog collision check must name searched_sources")
        if not collision.get("searched_terms"):
            errors.append("backlog collision check must name searched_terms")
    elif "backlog_collision_check" in payload:
        errors.append("backlog_collision_check must be an object")

    boundary = payload.get("implementation_boundary_review")
    if isinstance(boundary, Mapping):
        for field in REQUIRED_BOUNDARY_FIELDS:
            if field not in boundary:
                errors.append(f"implementation_boundary_review missing {field}")
        verdict = boundary.get("boundary_verdict")
        if verdict not in ALLOWED_BOUNDARY_VERDICTS:
            errors.append("implementation_boundary_review.boundary_verdict is invalid")
        if verdict == "pass" and boundary.get("violations"):
            errors.append("boundary pass must not include violations")
        if verdict == "block" and not _is_non_empty(boundary.get("blocked_on_owner_decision_if_any")):
            errors.append("boundary block must name blocked_on_owner_decision_if_any")
    elif "implementation_boundary_review" in payload:
        errors.append("implementation_boundary_review must be an object")

    follow_up = payload.get("follow_up")
    if isinstance(follow_up, Mapping):
        for field in REQUIRED_FOLLOW_UP_FIELDS:
            if field not in follow_up or not _is_non_empty(follow_up.get(field)):
                errors.append(f"follow_up missing {field}")
        action = follow_up.get("action")
        if action not in ALLOWED_FOLLOW_UP_ACTIONS:
            errors.append("follow_up.action is invalid")
        if action == "create_new_parent":
            if not isinstance(collision, Mapping) or collision.get("collision_classification") != "no_collision":
                errors.append("create_new_parent follow-up requires no_collision")
            if not isinstance(boundary, Mapping) or boundary.get("boundary_verdict") != "pass":
                errors.append("create_new_parent follow-up requires boundary pass")
        if action in {"none", "watch"} and payload.get("decision_surface", {}).get("decision") in {"adopt", "pilot"}:
            errors.append("adopt/pilot decisions need an executable or routed follow-up action")
    elif "follow_up" in payload:
        errors.append("follow_up must be an object")

    non_goals = payload.get("non_goals_preserved")
    if "non_goals_preserved" in payload and not _is_non_empty(non_goals):
        errors.append("non_goals_preserved must not be empty")

    return ContractValidationResult(fixture_id=fid, passed=not errors, errors=tuple(errors))


def validate_fixture_payload(payload: Mapping[str, Any]) -> FixtureValidationReport:
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        result = ContractValidationResult("fixture-file", False, ("fixture file must contain a fixtures list",))
        return FixtureValidationReport("repo-scout-fit-validation-report-v1", False, {"fixture_count": 0, "passed_count": 0, "failed_count": 1, "error_count": 1}, (result,))
    results = tuple(validate_contract_output(fixture, fixture_id=str(fixture.get("fixture_id") or f"fixture-{index}")) for index, fixture in enumerate(fixtures))
    failed = [result for result in results if not result.passed]
    return FixtureValidationReport(
        "repo-scout-fit-validation-report-v1",
        not failed,
        {
            "fixture_count": len(results),
            "passed_count": len(results) - len(failed),
            "failed_count": len(failed),
            "error_count": sum(len(result.errors) for result in results),
        },
        results,
    )


def validate_fixture_file(path: Path) -> FixtureValidationReport:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        result = ContractValidationResult(str(path), False, ("fixture file root must be an object",))
        return FixtureValidationReport("repo-scout-fit-validation-report-v1", False, {"fixture_count": 0, "passed_count": 0, "failed_count": 1, "error_count": 1}, (result,))
    return validate_fixture_payload(payload)


def validate_fixture_files(paths: Iterable[Path]) -> FixtureValidationReport:
    all_results: list[ContractValidationResult] = []
    for path in paths:
        report = validate_fixture_file(path)
        all_results.extend(report.results)
    failed = [result for result in all_results if not result.passed]
    return FixtureValidationReport(
        "repo-scout-fit-validation-report-v1",
        not failed,
        {
            "fixture_count": len(all_results),
            "passed_count": len(all_results) - len(failed),
            "failed_count": len(failed),
            "error_count": sum(len(result.errors) for result in all_results),
        },
        tuple(all_results),
    )


def write_validation_report(report: FixtureValidationReport, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path

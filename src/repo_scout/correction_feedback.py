from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ALLOWED_DURABLE_CHANGE_CLASSES = {
    "registry_update",
    "golden_fixture_addition",
    "backlog_search_query_improvement",
    "output_contract_change",
    "source_anchor",
}

REQUIRED_SOURCE_ANCHOR_FIELDS = (
    "source_type",
    "path_or_url",
    "locator",
    "captured_at",
)

REQUIRED_REPLAY_FIELDS = (
    "pre_correction_verdict",
    "corrected_verdict",
    "assertion",
)


@dataclass(frozen=True)
class CorrectionValidationResult:
    correction_id: str
    passed: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"correction_id": self.correction_id, "passed": self.passed, "errors": list(self.errors)}


@dataclass(frozen=True)
class CorrectionValidationReport:
    schema_version: str
    passed: bool
    summary: dict[str, int]
    results: tuple[CorrectionValidationResult, ...]

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


def _validate_source_anchor(anchor: Any, context: str, errors: list[str]) -> None:
    if not isinstance(anchor, Mapping):
        errors.append(f"{context} must be an object")
        return
    for field in REQUIRED_SOURCE_ANCHOR_FIELDS:
        if not _is_non_empty(anchor.get(field)):
            errors.append(f"{context} missing {field}")
    if anchor.get("source_type") not in {"owner_correction", "tracker_issue", "markdown_report", "generated_report", "test_fixture"}:
        errors.append(f"{context}.source_type is invalid")


def _validate_required_artifacts(payload: Mapping[str, Any], classes: set[str], errors: list[str]) -> None:
    artifacts = payload.get("required_durable_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("required_durable_artifacts must be a non-empty list")
        return

    artifact_classes: set[str] = set()
    for index, artifact in enumerate(artifacts):
        context = f"required_durable_artifacts[{index}]"
        if not isinstance(artifact, Mapping):
            errors.append(f"{context} must be an object")
            continue
        artifact_class = artifact.get("class")
        if artifact_class not in ALLOWED_DURABLE_CHANGE_CLASSES:
            errors.append(f"{context}.class is invalid")
        else:
            artifact_classes.add(str(artifact_class))
        if not _is_non_empty(artifact.get("path_or_anchor")):
            errors.append(f"{context} missing path_or_anchor")
        if artifact.get("status") not in {"applied", "planned", "not_required"}:
            errors.append(f"{context}.status must be applied, planned, or not_required")
        if not _is_non_empty(artifact.get("verification")):
            errors.append(f"{context} missing verification")

    missing = classes - artifact_classes
    if missing:
        errors.append(f"required_durable_artifacts missing classes: {sorted(missing)}")


def validate_correction_case(payload: Mapping[str, Any], *, correction_id: str | None = None) -> CorrectionValidationResult:
    """Validate one Owner-correction capture/replay case.

    This validator is deliberately local-only. It validates durable correction
    metadata and replay assertions; it does not call external APIs, publish reports,
    fetch target repos, execute target code, or use chat memory.
    """
    cid = correction_id or str(payload.get("correction_id") or "unknown-correction")
    errors: list[str] = []

    for field in (
        "correction_id",
        "source_anchor",
        "original_output_ref",
        "owner_correction",
        "durable_change_classes",
        "required_durable_artifacts",
        "replay_case",
        "non_memory_persistence",
        "safety_boundaries",
    ):
        if field not in payload:
            errors.append(f"missing required field: {field}")

    if "source_anchor" in payload:
        _validate_source_anchor(payload.get("source_anchor"), "source_anchor", errors)

    if not _is_non_empty(payload.get("original_output_ref")):
        errors.append("original_output_ref must not be empty")
    if not _is_non_empty(payload.get("owner_correction")):
        errors.append("owner_correction must not be empty")

    classes_raw = payload.get("durable_change_classes")
    classes: set[str] = set()
    if not isinstance(classes_raw, list) or not classes_raw:
        errors.append("durable_change_classes must be a non-empty list")
    else:
        for item in classes_raw:
            if item not in ALLOWED_DURABLE_CHANGE_CLASSES:
                errors.append(f"durable_change_classes contains invalid class: {item}")
            else:
                classes.add(str(item))

    if classes:
        _validate_required_artifacts(payload, classes, errors)

    replay = payload.get("replay_case")
    if isinstance(replay, Mapping):
        for field in REQUIRED_REPLAY_FIELDS:
            if not _is_non_empty(replay.get(field)):
                errors.append(f"replay_case missing {field}")
        if replay.get("pre_correction_verdict") == replay.get("corrected_verdict"):
            errors.append("replay_case must demonstrate a changed verdict or route")
    elif "replay_case" in payload:
        errors.append("replay_case must be an object")

    non_memory = payload.get("non_memory_persistence")
    if not isinstance(non_memory, Mapping):
        if "non_memory_persistence" in payload:
            errors.append("non_memory_persistence must be an object")
    else:
        if non_memory.get("chat_memory_required") is not False:
            errors.append("non_memory_persistence.chat_memory_required must be false")
        persisted_paths = non_memory.get("persisted_paths")
        if not isinstance(persisted_paths, list) or not persisted_paths:
            errors.append("non_memory_persistence.persisted_paths must be a non-empty list")

    safety = payload.get("safety_boundaries")
    if isinstance(safety, Mapping):
        for field in ("target_repo_execution_allowed", "tracker_mutation_allowed", "upstream_source_retention_allowed"):
            if safety.get(field) is not False:
                errors.append(f"safety_boundaries.{field} must be false")
    elif "safety_boundaries" in payload:
        errors.append("safety_boundaries must be an object")

    return CorrectionValidationResult(correction_id=cid, passed=not errors, errors=tuple(errors))


def validate_correction_payload(payload: Mapping[str, Any]) -> CorrectionValidationReport:
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        result = CorrectionValidationResult("fixture-file", False, ("fixture file must contain a fixtures list",))
        return CorrectionValidationReport(
            "repo-scout-owner-correction-validation-report-v1",
            False,
            {"fixture_count": 0, "passed_count": 0, "failed_count": 1, "error_count": 1},
            (result,),
        )

    results = tuple(
        validate_correction_case(fixture, correction_id=str(fixture.get("correction_id") or f"correction-{index}"))
        for index, fixture in enumerate(fixtures)
    )
    failed = [result for result in results if not result.passed]
    return CorrectionValidationReport(
        "repo-scout-owner-correction-validation-report-v1",
        not failed,
        {
            "fixture_count": len(results),
            "passed_count": len(results) - len(failed),
            "failed_count": len(failed),
            "error_count": sum(len(result.errors) for result in results),
        },
        results,
    )


def validate_correction_file(path: Path) -> CorrectionValidationReport:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        result = CorrectionValidationResult(str(path), False, ("fixture file root must be an object",))
        return CorrectionValidationReport(
            "repo-scout-owner-correction-validation-report-v1",
            False,
            {"fixture_count": 0, "passed_count": 0, "failed_count": 1, "error_count": 1},
            (result,),
        )
    return validate_correction_payload(payload)


def validate_correction_files(paths: Iterable[Path]) -> CorrectionValidationReport:
    all_results: list[CorrectionValidationResult] = []
    for path in paths:
        report = validate_correction_file(path)
        all_results.extend(report.results)
    failed = [result for result in all_results if not result.passed]
    return CorrectionValidationReport(
        "repo-scout-owner-correction-validation-report-v1",
        not failed,
        {
            "fixture_count": len(all_results),
            "passed_count": len(all_results) - len(failed),
            "failed_count": len(failed),
            "error_count": sum(len(result.errors) for result in all_results),
        },
        tuple(all_results),
    )


def write_correction_validation_report(report: CorrectionValidationReport, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path

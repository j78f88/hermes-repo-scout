from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from repo_scout.reporting import build_report

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quality_cases.json"
NOISE_RE = re.compile(
    r"\b(shields?\.io|badge|license-mit|contents|navigation|toc|install(?:ation)?|quickstart|npm install|pip install|pnpm install|yarn install|become a sponsor|star this project)\b",
    re.IGNORECASE,
)
SOURCE_RETENTION_RE = re.compile(
    r"(?:^|[\s\"'])((?:/home/|/users/|c:\\)[^\s\"']+|def\s+\w+\(|class\s+\w+\(|function\s+\w+\s*\()",
    re.IGNORECASE,
)


def load_quality_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def write_quality_case_repo(tmp_path: Path, case: dict[str, Any]) -> Path:
    repo = tmp_path / case["id"]
    repo.mkdir()
    (repo / "README.md").write_text(case["readme"], encoding="utf-8")
    if case.get("package_json"):
        (repo / "package.json").write_text(json.dumps(case["package_json"], indent=2), encoding="utf-8")
    return repo


def retains_source(serialized: str, scrub_roots: list[str] | tuple[str, ...] = ()) -> bool:
    """True when serialized output looks like it retains target source or host paths.

    The benchmark's own temporary workspace is not retained source, so callers
    pass it in ``scrub_roots``. Without this, a TMPDIR under /home makes every
    case fail even though nothing was retained.
    """
    text = serialized
    for root in sorted({str(r) for r in scrub_roots if r}, key=len, reverse=True):
        text = text.replace(root, "<benchmark-root>")
    return bool(SOURCE_RETENTION_RE.search(text))


def _text_blob(report) -> str:
    parts: list[str] = [report.summary]
    if report.dossier:
        parts.extend([report.dossier.repo_purpose, report.dossier.repo_function])
        for item in report.dossier.useful_ideas_patterns:
            parts.extend(str(item.get(k, "")) for k in ("purpose", "function", "action"))
    for card in report.idea_cards:
        parts.extend([card.purpose, card.function, card.pattern or ""])
    return "\n".join(parts).lower()


def quality_metrics(report, case: dict[str, Any], scrub_roots: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    blob = _text_blob(report)
    top_cards = report.idea_cards[:3]
    top_card_text = "\n".join(f"{card.purpose}\n{card.function}" for card in top_cards)
    expected_purpose_terms = [term.lower() for term in case.get("purpose_terms", [])]
    expected_function_terms = [term.lower() for term in case.get("function_terms", [])]
    serialized = report.model_dump_json()
    comparison = report.profile_comparisons[0] if report.profile_comparisons else None
    tags_present = bool(report.capability_tags or report.risk_tags or report.source_tags)
    return {
        "case": case["id"],
        "purpose_specific": (not case["expect_useful"])
        or (
            bool(report.dossier)
            and bool(expected_purpose_terms)
            and any(term in report.dossier.repo_purpose.lower() for term in expected_purpose_terms)
            and not NOISE_RE.search(report.dossier.repo_purpose)
        ),
        "function_specific": (not case["expect_useful"])
        or (
            bool(report.dossier)
            and bool(expected_function_terms)
            and (
                any(term in report.dossier.repo_function.lower() for term in expected_function_terms)
                or any(term in blob for term in expected_function_terms)
            )
        ),
        "top_cards_not_noise": (not top_cards and not case["expect_useful"])
        or (len(top_cards) >= case.get("min_idea_cards", 1) and not NOISE_RE.search(top_card_text)),
        "safety_gate_correct": report.describe_build_gate.state.value == case["expected_gate"]
        and report.describe_build_gate.can_direct_consume is False
        and report.describe_build_gate.original_implementation_only is True
        and report.runner_policy.execution_allowed is False
        and report.metrics.get("target_code_execution") is False
        and all(result.status == "not_run" for result in report.probe_results),
        "tags_present_or_not_applicable": tags_present or "tags" not in type(report).model_fields,
        "no_source_retention": not retains_source(serialized, scrub_roots)
        and not (report.repo.path and not Path(report.repo.path).exists())
        and all("readme" not in (e.excerpt or "").lower() or len(e.excerpt or "") <= 1000 for e in report.evidence),
        "comparison_ranking_stable": comparison is None
        or (
            comparison.get("id") == "PC1"
            and comparison.get("profile_id") == "quality-profile"
            and [dim.get("name") for dim in comparison.get("dimensions", [])] == ["evidence_quality", "reuse_safety"]
        ),
        "claims_extracted": len(report.claims),
        "idea_cards": len(report.idea_cards),
        "gate": report.describe_build_gate.state.value,
        "tags_present": tags_present,
    }


def run_quality_benchmark(output_path: Path | None = None, tmp_root: Path | None = None) -> dict[str, Any]:
    """Run the quality fixture benchmark without network or target execution."""
    rows: list[dict[str, Any]] = []
    profile = {
        "schema_version": "repo-scout.profile.v1",
        "id": "quality-profile",
        "version": "quality",
        "comparison_dimensions": [
            {"name": "evidence_quality", "preference": "Prefer specific purpose/function evidence over install or navigation noise."},
            {"name": "reuse_safety", "preference": "Prefer original implementation only with no source retention or execution."},
        ],
    }
    with tempfile.TemporaryDirectory(dir=tmp_root) as td:
        root = Path(td)
        profile_path = root / "quality-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        for case in load_quality_cases():
            repo = write_quality_case_repo(root, case)
            report, _ = build_report(str(repo), goal="Find reusable architecture patterns to borrow", profile_path=str(profile_path))
            rows.append(quality_metrics(report, case, scrub_roots=[str(root), str(root.resolve())]))
    check_keys = [
        "purpose_specific",
        "function_specific",
        "top_cards_not_noise",
        "safety_gate_correct",
        "tags_present_or_not_applicable",
        "no_source_retention",
        "comparison_ranking_stable",
    ]
    summary = {
        "benchmark": "repo-scout output usefulness golden quality",
        "cases": len(rows),
        "checks": check_keys,
        "passed_cases": sum(1 for row in rows if all(row[key] for key in check_keys)),
        "failed": [row for row in rows if not all(row[key] for key in check_keys)],
        "rows": rows,
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary

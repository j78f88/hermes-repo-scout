from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .campaign import validate_report_payload

SCHEMA_COMPARE_VERSION = "repo-scout.compare.v1"

VERDICT_WEIGHTS: dict[str, float] = {
    "adopt": 50.0,
    "adapt": 45.0,
    "borrow": 42.0,
    "investigate": 28.0,
    "watch": 18.0,
    "no_verdict": 8.0,
    "reject": 0.0,
}
SOURCE_TIER_WEIGHTS: dict[str, float] = {
    "owner_approved_direct_consume": 14.0,
    "trusted_vendor_or_official": 12.0,
    "known_high_quality": 9.0,
    "ordinary_oss": 4.0,
    "unknown_untrusted": -6.0,
}
GATE_STATE_WEIGHTS: dict[str, float] = {
    "build_original": 12.0,
    "spike_original": 9.0,
    "peer_review": 4.0,
    "describe_only": 1.0,
    "blocked_needs_owner": -14.0,
}
RISK_TAG_PENALTIES: dict[str, float] = {
    "source-contamination": 10.0,
    "secrets-risk": 7.0,
    "execution-risk": 6.0,
    "runtime-risk": 4.0,
    "package-install-risk": 4.0,
    "terminal-process-risk": 4.0,
    "cloud-dependency": 3.0,
    "data-retention": 3.0,
    "broad-platform": 2.0,
    "low-signal": 5.0,
}
SEVERITY_PENALTIES: dict[str, float] = {"critical": 12.0, "high": 8.0, "medium": 4.0, "low": 1.0, "info": 0.0}


def load_report_json(path: Path) -> dict[str, Any]:
    """Load an existing repo-scout report JSON without scanning a repository."""
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def validate_report_for_comparison(report: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether a report is valid enough to enter deterministic comparison."""
    return validate_report_payload(report)


def build_comparison(report_paths: list[Path]) -> dict[str, Any]:
    rows = []
    excluded = []
    for path in report_paths:
        report = load_report_json(path)
        valid, reason = validate_report_for_comparison(report)
        if not valid:
            excluded.append({"artifact_path_ref": str(path), "reason": reason or "invalid"})
            continue
        rows.append(_row_from_report(report, path))
    rows.sort(key=lambda row: (-row["score"], -row["confidence"], row["repo"].lower(), row["artifact_path_ref"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "schema_version": SCHEMA_COMPARE_VERSION,
        "report_count": len(rows),
        "input_report_count": len(report_paths),
        "excluded_report_count": len(excluded),
        "excluded_reports": excluded,
        "ranking_heuristic": ranking_heuristic_description(),
        "rollup": _rollup(rows),
        "rows": rows,
    }


def write_comparison_outputs(comparison: dict[str, Any], markdown_out: Path | None = None, json_out: Path | None = None) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if markdown_out:
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(render_comparison_markdown(comparison), encoding="utf-8")
        paths["markdown"] = markdown_out
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths["json"] = json_out
    return paths


def ranking_heuristic_description() -> dict[str, Any]:
    return {
        "summary": "Deterministic additive score sorted by score desc, confidence desc, repo name asc, artifact path asc.",
        "components": {
            "verdict_weight": VERDICT_WEIGHTS,
            "confidence": "confidence * 20",
            "source_tier_weight": SOURCE_TIER_WEIGHTS,
            "gate_state_weight": GATE_STATE_WEIGHTS,
            "direct_consume": "+5 when describe_build_gate.can_direct_consume is true",
            "original_only": "+2 when original_implementation_only is true (safe original-build reuse), -5 otherwise",
            "idea_cards": "+2 per IdeaCard, capped at +10",
            "capability_tags": "+1.5 per report capability tag, capped at +9",
            "quality_signals": "+3 when target_code_execution is false; +2 when source_tags exist; +2 when profile comparison score is positive/known",
            "risk_tags": "subtract controlled risk tag penalties, capped at -25",
            "risk_findings": "subtract finding severity penalties for risk/security/license findings, capped at -20",
        },
    }


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    rollup = comparison.get("rollup", {}) or {}
    lines = [
        "# repo-scout ranked comparison",
        "",
        f"Reports compared: **{comparison.get('report_count', 0)}**",
        "",
        "## Rollup",
        "",
        f"- Top repo: {_md(rollup.get('top_repo') or '—')}",
        "- Verdicts: " + _counter_text(rollup.get("verdict_counts")),
        "- Gate states: " + _counter_text(rollup.get("gate_state_counts")),
        "- Source tiers: " + _counter_text(rollup.get("source_tier_counts")),
        "- Top capability tags: " + _tag_summary(rollup.get("top_capability_tags")),
        "- Top risk tags: " + _tag_summary(rollup.get("top_risk_tags")),
        "",
        "## Ranked table",
        "",
        "| Rank | Repo | Score | Verdict | Confidence | Source tier | Gate | Direct consume | Original-only | Idea cards | Top capability tags | Top risk tags | Purpose/function summary | Recommendation | Artifact path/ref | Score reasons |",
        "|---:|---|---:|---|---:|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for row in comparison.get("rows", []):
        lines.append(
            "| {rank} | {repo} | {score:.1f} | {verdict} | {confidence:.2f} | {source_tier} | {gate_state} | {direct_consume} | {original_only} | {idea_card_count} | {capability_tags} | {risk_tags} | {summary} | {recommendation} | {artifact} | {reasons} |".format(
                rank=row.get("rank", ""),
                repo=_md(row.get("repo", "—")),
                score=float(row.get("score", 0.0)),
                verdict=_md(row.get("verdict", "—")),
                confidence=float(row.get("confidence", 0.0)),
                source_tier=_md(row.get("source_tier", "—")),
                gate_state=_md(row.get("gate_state", "—")),
                direct_consume=_md(str(row.get("direct_consume", False)).lower()),
                original_only=_md(str(row.get("original_only", False)).lower()),
                idea_card_count=int(row.get("idea_card_count", 0)),
                capability_tags=_md(", ".join(row.get("top_capability_tags", [])) or "—"),
                risk_tags=_md(", ".join(row.get("top_risk_tags", [])) or "—"),
                summary=_md(row.get("purpose_function_summary") or "—"),
                recommendation=_md(row.get("recommendation") or "—"),
                artifact=_md(row.get("artifact_path_ref") or "—"),
                reasons=_md("; ".join(row.get("score_reasons", [])) or "—"),
            )
        )
    lines.extend(["", "## Ranking heuristic", "", comparison.get("ranking_heuristic", {}).get("summary", "Deterministic additive score.")])
    return "\n".join(lines) + "\n"


def _row_from_report(report: dict[str, Any], path: Path) -> dict[str, Any]:
    raw_gate = report.get("describe_build_gate")
    gate: dict[str, Any] = raw_gate if isinstance(raw_gate, dict) else {}
    raw_idea_cards = report.get("idea_cards")
    idea_cards: list[Any] = raw_idea_cards if isinstance(raw_idea_cards, list) else []
    verdict = _dig(report, "verdict", "decision", default="no_verdict") or "no_verdict"
    confidence = _as_float(_dig(report, "verdict", "confidence", default=0.0))
    source_tier = str(gate.get("source_tier") or "unknown_untrusted")
    gate_state = str(gate.get("state") or "describe_only")
    direct_consume = bool(gate.get("can_direct_consume", False))
    original_only = bool(gate.get("original_implementation_only", True))
    capability_tags = _top_tags(report, "capability_tags", limit=5)
    risk_tags = _top_tags(report, "risk_tags", limit=5)
    score, reasons = _score_report(report, verdict, confidence, source_tier, gate_state, direct_consume, original_only, len(idea_cards), capability_tags, risk_tags)
    return {
        "rank": None,
        "repo": _repo_label(report),
        "repo_url": _dig(report, "repo", "url", default="") or "",
        "verdict": verdict,
        "confidence": round(confidence, 4),
        "source_tier": source_tier,
        "gate_state": gate_state,
        "direct_consume": direct_consume,
        "original_only": original_only,
        "idea_card_count": len(idea_cards),
        "top_capability_tags": capability_tags,
        "top_risk_tags": risk_tags,
        "purpose_function_summary": _purpose_function_summary(report),
        "recommendation": _recommendation(report, verdict),
        "artifact_path_ref": str(path),
        "research_plan_ref": report.get("research_plan_ref"),
        "score": round(score, 4),
        "score_reasons": reasons,
        "quality_signals": _quality_signals(report),
    }


def _score_report(report: dict[str, Any], verdict: str, confidence: float, source_tier: str, gate_state: str, direct_consume: bool, original_only: bool, idea_card_count: int, capability_tags: list[str], risk_tags: list[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for label, value, table in (("verdict", verdict, VERDICT_WEIGHTS), ("source_tier", source_tier, SOURCE_TIER_WEIGHTS), ("gate_state", gate_state, GATE_STATE_WEIGHTS)):
        points = table.get(value, 0.0)
        score += points
        reasons.append(f"{label} {value} {points:+.1f}")
    confidence_points = max(0.0, min(confidence, 1.0)) * 20.0
    score += confidence_points
    reasons.insert(1, f"confidence {confidence:.2f} {confidence_points:+.1f}")
    if direct_consume:
        score += 5.0
        reasons.append("direct_consume +5.0")
    score += 2.0 if original_only else -5.0
    reasons.append("original_only +2.0" if original_only else "not_original_only -5.0")
    idea_points = min(10.0, idea_card_count * 2.0)
    score += idea_points
    reasons.append(f"idea_cards {idea_card_count} {idea_points:+.1f}")
    cap_points = min(9.0, len(capability_tags) * 1.5)
    score += cap_points
    if capability_tags:
        reasons.append(f"capability_tags {len(capability_tags)} {cap_points:+.1f}")
    risk_penalty = min(25.0, sum(RISK_TAG_PENALTIES.get(tag, 2.0) for tag in risk_tags))
    score -= risk_penalty
    if risk_tags:
        reasons.append(f"risk_tags {len(risk_tags)} {-risk_penalty:+.1f}")
    finding_penalty = _risk_finding_penalty(report)
    score -= finding_penalty
    if finding_penalty:
        reasons.append(f"risk_findings {-finding_penalty:+.1f}")
    raw_metrics = report.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    if metrics.get("target_code_execution") is False:
        score += 3.0
        reasons.append("no_target_code_execution +3.0")
    if report.get("source_tags"):
        score += 2.0
        reasons.append("source_tags_present +2.0")
    if _has_positive_profile_signal(report):
        score += 2.0
        reasons.append("profile_signal +2.0")
    return score, reasons


def _risk_finding_penalty(report: dict[str, Any]) -> float:
    raw_findings = report.get("findings")
    findings: list[Any] = raw_findings if isinstance(raw_findings, list) else []
    penalty = 0.0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        category = str(finding.get("category") or "")
        impact = str(finding.get("impact") or "")
        if category not in {"risk", "security", "license", "dependency"} and impact not in {"avoid", "watch", "investigate"}:
            continue
        penalty += SEVERITY_PENALTIES.get(str(finding.get("severity") or "info"), 0.0)
    return min(20.0, penalty)


def _quality_signals(report: dict[str, Any]) -> dict[str, Any]:
    raw_metrics = report.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    return {
        "target_code_execution": metrics.get("target_code_execution"),
        "files_scanned": metrics.get("files_scanned"),
        "claims_extracted": metrics.get("claims_extracted"),
        "probe_steps_run": metrics.get("probe_steps_run"),
        "source_tags_present": bool(report.get("source_tags")),
        "profile_signal": _has_positive_profile_signal(report),
    }


def _has_positive_profile_signal(report: dict[str, Any]) -> bool:
    raw_comparisons = report.get("profile_comparisons")
    comparisons: list[Any] = raw_comparisons if isinstance(raw_comparisons, list) else []
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            continue
        score = comparison.get("score")
        if isinstance(score, (int, float)) and score > 0:
            return True
        if str(comparison.get("overall_fit") or "").lower() in {"strong", "good", "medium", "partial", "match"}:
            return True
    return False


def _repo_label(report: dict[str, Any]) -> str:
    raw_repo = report.get("repo")
    repo: dict[str, Any] = raw_repo if isinstance(raw_repo, dict) else {}
    owner, name = repo.get("owner"), repo.get("name")
    if owner and name:
        return f"{owner}/{name}"
    if name:
        return str(name)
    if repo.get("url"):
        return str(repo["url"]).rstrip("/").split("/")[-1]
    return str(report.get("run_id") or "unknown")


def _top_tags(report: dict[str, Any], key: str, limit: int = 5) -> list[str]:
    counts: Counter[str] = Counter()
    values = report.get(key)
    if isinstance(values, list):
        counts.update(str(tag) for tag in values if str(tag).strip())
    for collection_key in ("idea_cards", "idea_candidates", "findings", "claims", "evidence"):
        collection = report.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict) and isinstance(item.get(key), list):
                counts.update(str(tag) for tag in item[key] if str(tag).strip())
    return [tag for tag, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _purpose_function_summary(report: dict[str, Any]) -> str:
    raw_dossier = report.get("dossier")
    dossier: dict[str, Any] | None = raw_dossier if isinstance(raw_dossier, dict) else None
    if dossier:
        purpose = str(dossier.get("repo_purpose") or "").strip()
        function = str(dossier.get("repo_function") or "").strip()
        if purpose or function:
            return _truncate(" / ".join(part for part in (purpose, function) if part), 220)
    raw_cards = report.get("idea_cards")
    cards: list[Any] = raw_cards if isinstance(raw_cards, list) else []
    for card in cards:
        if isinstance(card, dict):
            purpose = str(card.get("purpose") or "").strip()
            function = str(card.get("function") or "").strip()
            if purpose or function:
                return _truncate(" / ".join(part for part in (purpose, function) if part), 220)
    return _truncate(str(report.get("summary") or _dig(report, "verdict", "summary", default="") or ""), 220)


def _recommendation(report: dict[str, Any], verdict: str) -> str:
    raw_dossier = report.get("dossier")
    dossier: dict[str, Any] | None = raw_dossier if isinstance(raw_dossier, dict) else None
    if dossier and dossier.get("recommendation"):
        return str(dossier["recommendation"])
    raw_comparisons = report.get("profile_comparisons")
    comparisons: list[Any] = raw_comparisons if isinstance(raw_comparisons, list) else []
    for comparison in comparisons:
        if isinstance(comparison, dict) and comparison.get("recommendation"):
            return str(comparison["recommendation"])
    return verdict


def _rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cap_counts: Counter[str] = Counter(tag for row in rows for tag in row.get("top_capability_tags", []))
    risk_counts: Counter[str] = Counter(tag for row in rows for tag in row.get("top_risk_tags", []))
    return {
        "top_repo": rows[0]["repo"] if rows else None,
        "average_score": round(sum(float(row["score"]) for row in rows) / len(rows), 4) if rows else 0.0,
        "average_confidence": round(sum(float(row["confidence"]) for row in rows) / len(rows), 4) if rows else 0.0,
        "direct_consume_count": sum(1 for row in rows if row.get("direct_consume")),
        "original_only_count": sum(1 for row in rows if row.get("original_only")),
        "total_idea_cards": sum(int(row.get("idea_card_count", 0)) for row in rows),
        "verdict_counts": dict(sorted(Counter(row["verdict"] for row in rows).items())),
        "gate_state_counts": dict(sorted(Counter(row["gate_state"] for row in rows).items())),
        "source_tier_counts": dict(sorted(Counter(row["source_tier"] for row in rows).items())),
        "top_capability_tags": _counter_rows(cap_counts),
        "top_risk_tags": _counter_rows(risk_counts),
    }


def _counter_rows(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [{"tag": tag, "count": count} for tag, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _counter_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "—"
    return ", ".join(f"{key}={value[key]}" for key in sorted(value))


def _tag_summary(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "—"
    return ", ".join(f"{item.get('tag')}={item.get('count')}" for item in value if isinstance(item, dict)) or "—"


def _dig(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _truncate(value: str, limit: int) -> str:
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _md(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")

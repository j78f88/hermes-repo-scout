from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .compare import build_comparison

SCHEMA_ROLLUP_EXPORT_VERSION = "repo-scout.rollup-export.v1"


def build_rollup_export(report_paths: list[Path], *, title: str = "repo-scout selected repo rollup") -> dict[str, Any]:
    """Build a curated Markdown rollup packet from selected repo-scout reports.

    This function intentionally works from existing report JSON files. It does not
    rescan repositories, clone sources, or include raw source excerpts. Call
    ``write_rollup_export`` with an explicit output folder to materialize
    Markdown files.
    """
    comparison = build_comparison(report_paths)
    cards = [_card_from_row(row) for row in comparison.get("rows", [])]
    return {
        "schema_version": SCHEMA_ROLLUP_EXPORT_VERSION,
        "title": title,
        "selection_count": len(cards),
        "safety": {
            "export_kind": "curated_markdown_only",
            "source": "existing repo-scout report JSON",
            "requires_explicit_output_folder": True,
            "includes_raw_source": False,
            "rescans_repositories": False,
            "executes_target_code": False,
            "publishes_automatically": False,
        },
        "rollup": comparison.get("rollup", {}),
        "cards": cards,
    }


def write_rollup_export(export: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    """Write a Markdown index and selected repo cards to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cards_dir = out_dir / "repo-cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    card_paths: list[Path] = []
    for card in export.get("cards", []):
        filename = f"repo-scout-{_slug(str(card.get('repo') or 'repo'))}.md"
        path = cards_dir / filename
        path.write_text(render_repo_card_markdown(card), encoding="utf-8")
        card_paths.append(path)
    index_path = out_dir / "repo-scout-rollup.md"
    index_card_paths = [path.relative_to(out_dir) for path in card_paths]
    index_path.write_text(render_rollup_markdown(export, index_card_paths), encoding="utf-8")
    manifest_path = out_dir / "repo-scout-rollup-export.json"
    manifest_path.write_text(json.dumps(export, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    paths["index"] = index_path
    paths["manifest"] = manifest_path
    for idx, path in enumerate(card_paths, start=1):
        paths[f"card_{idx}"] = path
    return paths


def render_repo_card_markdown(card: dict[str, Any]) -> str:
    repo = str(card.get("repo") or "Unknown repo")
    tags = ["repo-scout", "source-intelligence", "selected-repo"]
    tags.extend(_safe_tag(tag) for tag in card.get("capability_tags", []) if tag)
    tags = _dedupe(tags)
    frontmatter = {
        "title": f"repo-scout card — {repo}",
        "type": "repo-scout-card",
        "status": "selected",
        "repo": repo,
        "repo_url": card.get("repo_url") or "",
        "verdict": card.get("verdict") or "no_verdict",
        "confidence": card.get("confidence", 0),
        "source_tier": card.get("source_tier") or "unknown_untrusted",
        "gate_state": card.get("gate_state") or "describe_only",
        "tags": tags,
    }
    lines = [_frontmatter(frontmatter), f"# repo-scout card — {_md(repo)}", ""]
    lines.extend(
        [
            "## Why it matters",
            "",
            _md(str(card.get("why_it_matters") or "No summary available.")),
            "",
            "## Verdict / next action",
            "",
            f"- Verdict: **{_md(str(card.get('verdict') or 'no_verdict'))}** ({float(card.get('confidence') or 0):.2f} confidence)",
            f"- Build gate: `{_md(str(card.get('gate_state') or 'describe_only'))}`; direct consume: `{str(bool(card.get('direct_consume'))).lower()}`; original-only: `{str(bool(card.get('original_only', True))).lower()}`",
            f"- Recommended next action: {_md(str(card.get('next_action') or 'watch'))}",
            "",
            "## Useful signals",
            "",
            f"- Capability tags: {_md(', '.join(card.get('capability_tags') or []) or '—')}",
            f"- Risk tags: {_md(', '.join(card.get('risk_tags') or []) or '—')}",
            f"- Idea cards: {int(card.get('idea_card_count') or 0)}",
            "",
            "## Corpus refs",
            "",
        ]
    )
    for ref in card.get("corpus_refs", []):
        lines.append(f"- `{_md(str(ref))}`")
    if not card.get("corpus_refs"):
        lines.append("- —")
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "This card is a curated decision surface from existing repo-scout reports. It does not include raw source dumps, cloned source snapshots, or executable target-repo instructions.",
            "",
        ]
    )
    return "\n".join(lines)


def render_rollup_markdown(export: dict[str, Any], card_paths: list[Path]) -> str:
    title = str(export.get("title") or "repo-scout selected repo rollup")
    rollup = export.get("rollup", {}) or {}
    lines = [
        _frontmatter(
            {
                "title": title,
                "type": "repo-scout-rollup",
                "status": "selected",
                "tags": ["repo-scout", "source-intelligence", "selected-repo-rollup"],
            }
        ),
        f"# {_md(title)}",
        "",
        f"Selected repo cards: **{int(export.get('selection_count') or 0)}**",
        "",
        "## Rollup",
        "",
        f"- Top repo: {_md(str(rollup.get('top_repo') or '—'))}",
        f"- Verdicts: {_counter_text(rollup.get('verdict_counts'))}",
        f"- Gate states: {_counter_text(rollup.get('gate_state_counts'))}",
        f"- Source tiers: {_counter_text(rollup.get('source_tier_counts'))}",
        "",
        "## Selected repo cards",
        "",
    ]
    for card, path in zip(export.get("cards", []), card_paths, strict=False):
        rel = path.as_posix()
        lines.append(f"- [{_md(str(card.get('repo') or path.stem))}]({rel}) — {_md(str(card.get('next_action') or 'watch'))}")
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "Explicit export folder only. No automatic publication, no raw evidence/source dump, no repo scanning, and no target code execution.",
            "",
        ]
    )
    return "\n".join(lines)


def _card_from_row(row: dict[str, Any]) -> dict[str, Any]:
    corpus_refs = [row.get("artifact_path_ref")]
    if row.get("research_plan_ref"):
        corpus_refs.append(row.get("research_plan_ref"))
    return {
        "repo": row.get("repo"),
        "repo_url": row.get("repo_url"),
        "rank": row.get("rank"),
        "score": row.get("score"),
        "verdict": row.get("verdict"),
        "confidence": row.get("confidence"),
        "source_tier": row.get("source_tier"),
        "gate_state": row.get("gate_state"),
        "direct_consume": row.get("direct_consume"),
        "original_only": row.get("original_only"),
        "idea_card_count": row.get("idea_card_count"),
        "capability_tags": row.get("top_capability_tags", []),
        "risk_tags": row.get("top_risk_tags", []),
        "why_it_matters": row.get("purpose_function_summary") or row.get("recommendation"),
        "next_action": row.get("recommendation") or row.get("verdict") or "watch",
        "corpus_refs": [str(ref) for ref in corpus_refs if ref],
    }


def _frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            text = str(value).replace('"', '\\"')
            lines.append(f'{key}: "{text}"')
    lines.append("---")
    return "\n".join(lines)


def _counter_text(counter: Any) -> str:
    if not isinstance(counter, dict) or not counter:
        return "—"
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _safe_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_/-]+", "-", value.strip()).strip("-").lower()


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "repo"


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()

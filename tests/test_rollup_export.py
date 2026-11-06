import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.rollup_export import build_rollup_export, render_repo_card_markdown, render_rollup_markdown


def _report(repo: str, *, summary: str = "Static summary", verdict: str = "borrow") -> dict:
    owner, name = repo.split("/", 1)
    return {
        "schema_version": "repo-scout.report.v1",
        "run_id": f"run-{name}",
        "repo": {"url": f"https://github.com/{repo}", "owner": owner, "name": name},
        "summary": summary,
        "verdict": {"decision": verdict, "confidence": 0.74, "summary": f"{verdict} {name}"},
        "describe_build_gate": {
            "source_tier": "ordinary_oss",
            "state": "spike_original",
            "can_direct_consume": False,
            "original_implementation_only": True,
        },
        "idea_cards": [
            {
                "id": "IC1",
                "purpose": "Improve source intelligence routing",
                "function": "Curates repo due-diligence signals",
                "capability_tags": ["source-intelligence", "repo-analysis"],
                "risk_tags": ["low-signal"],
            }
        ],
        "dossier": {
            "repo_purpose": "A source intelligence helper",
            "repo_function": "Turns repository reports into decision surfaces",
            "recommendation": "Export selected card for manual Markdown review",
        },
        "findings": [
            {
                "id": "F1",
                "category": "risk",
                "severity": "medium",
                "title": "Raw evidence should stay out",
                "text": "RAW_SECRET_OR_SOURCE_EXCERPT_SHOULD_NOT_APPEAR",
            }
        ],
        "capability_tags": ["source-intelligence", "repo-analysis"],
        "risk_tags": ["low-signal"],
        "source_tags": ["readme-claim", "manifest-static"],
        "metrics": {"target_code_execution": False, "files_scanned": 12, "claims_extracted": 2, "probe_steps_run": 0},
        "research_plan_ref": "artifacts/research_plan.json",
    }


def test_build_rollup_export_creates_curated_cards_without_raw_findings(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report("acme/source-tool")), encoding="utf-8")

    export = build_rollup_export([report_path], title="Selected repos")
    card = export["cards"][0]
    card_md = render_repo_card_markdown(card)
    rollup_md = render_rollup_markdown(export, [tmp_path / "repo-cards" / "repo-scout-acme-source-tool.md"])

    assert export["schema_version"] == "repo-scout.rollup-export.v1"
    assert export["safety"]["requires_explicit_output_folder"] is True
    assert export["safety"]["includes_raw_source"] is False
    assert export["safety"]["rescans_repositories"] is False
    assert export["safety"]["executes_target_code"] is False
    assert export["safety"]["publishes_automatically"] is False
    assert card["repo_url"] == "https://github.com/acme/source-tool"
    assert "type: \"repo-scout-card\"" in card_md
    assert "repo_url: \"https://github.com/acme/source-tool\"" in card_md
    assert "## Why it matters" in card_md
    assert "## Corpus refs" in card_md
    assert "artifacts/research_plan.json" in card_md
    assert "RAW_SECRET_OR_SOURCE_EXCERPT_SHOULD_NOT_APPEAR" not in card_md
    assert "Explicit export folder only" in rollup_md
    assert "repo-cards/repo-scout-acme-source-tool.md" in rollup_md


def test_rollup_export_cli_requires_explicit_output_folder(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report("acme/example")), encoding="utf-8")

    result = CliRunner().invoke(main, ["export", "rollup", str(report_path)])

    assert result.exit_code == 2
    assert "Missing option '--out-dir'" in result.output
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.json"]


def test_rollup_export_cli_writes_index_manifest_and_cards(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    out_dir = tmp_path / "curated-export"
    first.write_text(json.dumps(_report("acme/first", verdict="borrow")), encoding="utf-8")
    second.write_text(json.dumps(_report("acme/second", verdict="watch")), encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["export", "rollup", str(first), str(second), "--out-dir", str(out_dir), "--title", "Repo Scout Selected"],
    )

    assert result.exit_code == 0, result.output
    assert "Safety: explicit curated export folder only; no automatic publication" in result.output
    index = out_dir / "repo-scout-rollup.md"
    manifest = out_dir / "repo-scout-rollup-export.json"
    cards = sorted((out_dir / "repo-cards").glob("*.md"))
    assert index.exists()
    assert manifest.exists()
    assert len(cards) == 2
    assert "# Repo Scout Selected" in index.read_text(encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["selection_count"] == 2
    assert all("RAW_SECRET_OR_SOURCE_EXCERPT_SHOULD_NOT_APPEAR" not in card.read_text(encoding="utf-8") for card in cards)

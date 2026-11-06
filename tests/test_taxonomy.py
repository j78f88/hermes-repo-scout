from __future__ import annotations

import json

from repo_scout.handoff import build_review_board_handoff
from repo_scout.reporting import build_report
from repo_scout.taxonomy import CAPABILITY_TAGS, RISK_TAGS, SOURCE_TAGS, TaxonomyTags, infer_taxonomy_tags


def _write_repo(tmp_path, name: str, readme: str, *, package_json: str | None = None):
    repo = tmp_path / name
    repo.mkdir()
    (repo / "README.md").write_text(readme, encoding="utf-8")
    if package_json is not None:
        (repo / "package.json").write_text(package_json, encoding="utf-8")
    return repo


def test_taxonomy_model_rejects_unknown_tags():
    assert "skills-instructions" in CAPABILITY_TAGS
    assert "execution-risk" in RISK_TAGS
    assert "readme-claim" in SOURCE_TAGS

    tags = TaxonomyTags(
        capability_tags=["repo-automation", "skills-instructions", "repo-automation"],
        risk_tags=["low-signal", "execution-risk"],
        source_tags=["manifest-static", "readme-claim"],
    )
    assert tags.capability_tags == ["skills-instructions", "repo-automation"]
    assert tags.risk_tags == ["execution-risk", "low-signal"]
    assert tags.source_tags == ["readme-claim", "manifest-static"]

    try:
        TaxonomyTags(capability_tags=["not-controlled"])
    except ValueError as exc:
        assert "unknown capability_tags" in str(exc)
    else:
        raise AssertionError("unknown taxonomy tag was accepted")


def test_infer_taxonomy_tags_representative_concepts():
    cases = {
        "agent-skills": (
            "Agent Skills provides reusable markdown skill instructions, prompts, examples, and eval workflows.",
            {"skills-instructions", "agent-control-plane", "quality-gates"},
        ),
        "activegraph": (
            "ActiveGraph is a graph UI workspace and evidence graph workspace for node relationships and timeline views.",
            {"evidence-graph", "ui-workspace", "graph-ui"},
        ),
        "gbrain": (
            "GBrain is a personal knowledge graph workbench for notes, memories, relationships, and reflective planning.",
            {"evidence-graph", "knowledge-workflow"},
        ),
        "impeccable": (
            "Impeccable coordinates governance review, quality gates, audit policy, and verification workflows.",
            {"governance", "quality-gates", "knowledge-workflow"},
        ),
        "mesh-llm": (
            "Mesh LLM runs model serving, embeddings, inference routing, and agent orchestration services.",
            {"agent-control-plane", "model-serving"},
        ),
    }
    for text, expected in cases.values():
        tags = infer_taxonomy_tags(text)
        assert expected.issubset(set(tags.capability_tags))

    low = infer_taxonomy_tags("Web App install route navbar quickstart", low_signal=True)
    assert "low-signal" in low.risk_tags


def test_agent_skills_report_tags_idea_cards_and_handoff(tmp_path):
    repo = _write_repo(
        tmp_path,
        "agent_skills",
        """# Agent Skills

## Features
Agent Skills provides a catalog of reusable agent capabilities, skill instructions, prompt packages, and evaluation workflows.
It enables teams to organize skills, prompts, and examples as reviewable markdown packages.

## Install
```bash
npm install
npm test
```
""",
        package_json=json.dumps({"scripts": {"test": "vitest"}}),
    )
    report, _ = build_report(str(repo), goal="Find reusable architecture patterns to borrow")

    assert {"skills-instructions", "agent-control-plane", "quality-gates"}.issubset(set(report.capability_tags))
    assert {"execution-risk", "package-install-risk"}.issubset(set(report.risk_tags))
    assert {"readme-claim", "manifest-static", "inventory-static"}.issubset(set(report.source_tags))
    assert report.idea_cards
    assert "skills-instructions" in report.idea_cards[0].capability_tags
    assert report.dossier is not None
    assert "skills-instructions" in report.dossier.capability_tags

    packet = build_review_board_handoff(report, source_ref="agent-skills-report.json", issue_id="EXAMPLE-185")
    assert packet["repo_scout"]["taxonomy"]["capability_tags"] == report.capability_tags
    assert packet["repo_scout"]["idea_cards"][0]["capability_tags"]
    assert packet["repo_scout"]["dossier"]["capability_tags"]


def test_activegraph_gbrain_impeccable_mesh_llm_and_low_signal_reports(tmp_path):
    fixtures = [
        (
            "activegraph",
            """# activegraph

## Overview
ActiveGraph provides a graph UI workspace that renders evidence graph nodes, relationships, and timeline views for operators.
""",
            {"evidence-graph", "ui-workspace", "graph-ui"},
            set(),
            True,
        ),
        (
            "gbrain",
            """# gbrain

## About
GBrain provides a personal knowledge graph workbench that captures notes, memories, and relationships for reflective planning.
It provides graph search, note linking, and timeline views for understanding evolving context.
""",
            {"evidence-graph", "knowledge-workflow", "graph-ui"},
            set(),
            True,
        ),
        (
            "impeccable",
            """# impeccable

## Architecture
Impeccable coordinates governance review, quality gates, audit policy, and verification workflow handoffs.
""",
            {"governance", "quality-gates", "knowledge-workflow"},
            set(),
            True,
        ),
        (
            "mesh_llm",
            """# mesh-llm

## Features
Mesh LLM provides model serving, inference routing, embeddings, and multi-agent orchestration for local services.
""",
            {"model-serving", "agent-control-plane"},
            {"runtime-risk"},
            True,
        ),
        (
            "example-platform_web",
            """# Web App

| Route | Link |
| --- | --- |
| install | [Install](#install) |
| supports auth | [Login](/login) |

- [Home](#home)
- [Install](#install)

## Install
This package supports pnpm install for local development.
""",
            set(),
            {"low-signal", "package-install-risk"},
            False,
        ),
    ]

    for name, readme, expected_caps, expected_risks, expects_cards in fixtures:
        report, _ = build_report(str(_write_repo(tmp_path, name, readme)))
        assert expected_caps.issubset(set(report.capability_tags)), name
        assert expected_risks.issubset(set(report.risk_tags)), name
        assert "readme-claim" in report.source_tags, name
        assert bool(report.idea_cards) is expects_cards, name
        if expects_cards:
            assert expected_caps.intersection(report.idea_cards[0].capability_tags), name
        else:
            assert report.idea_cards == []
            assert report.dossier is not None
            assert "low-signal" in report.dossier.risk_tags

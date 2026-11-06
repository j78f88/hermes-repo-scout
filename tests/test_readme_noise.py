from repo_scout.analyze import extract_idea_candidates, extract_readme_claims, parse_readme_sections
from repo_scout.reporting import build_report


def _write_repo(tmp_path, readme: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(readme, encoding="utf-8")
    return repo


def test_readme_noise_filters_badges_toc_tables_and_sponsor_copy(tmp_path):
    repo = _write_repo(
        tmp_path,
        """# Agent Skills
[![CI](https://img.shields.io/github/actions/workflow/status/owner/repo/ci.yml)](https://github.com/owner/repo/actions)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Contents
- [Install](#install)
- [Features](#features)

| Status | Link |
| ------ | ---- |
| supports npm install | [Install](#install) |

Become a sponsor and star this project.

## Features
Agent Skills provides a catalog of reusable agent capabilities and evaluation workflows.
It enables teams to organize skills, prompts, and examples as reviewable markdown packages.

## Install
```bash
npm install
npm run build
```
""",
    )
    claims, evidence, steps, results = extract_readme_claims(repo)
    claim_text = "\n".join(c.text for c in claims)

    assert "shields" not in claim_text.lower()
    assert "license-mit" not in claim_text.lower()
    assert "supports npm install" not in claim_text
    assert "Become a sponsor" not in claim_text
    assert any("catalog of reusable agent capabilities" in c.text for c in claims)
    assert steps and results and all(result.status == "not_run" for result in results)

    ideas = extract_idea_candidates(claims, evidence)
    assert ideas
    assert ideas[0].kind in {"idea", "pattern"}
    assert "Quickstart requires execution" not in ideas[0].title


def test_readme_noise_section_ranking_prefers_overview_architecture_over_install(tmp_path):
    readme = """# Compound Engineering Plugin

## Installation
The plugin supports npm install and quickstart run commands for local demos.
```bash
npm install
npm run dev
```

## Overview
Compound Engineering Plugin coordinates product requirements, design notes, and implementation tasks into a compound engineering workflow.

## Architecture
The system uses plugin adapters and review queues to connect engineering artifacts without executing repository code.
"""
    sections = parse_readme_sections(readme)
    assert sections[0].title in {"Overview", "Architecture"}

    repo = _write_repo(tmp_path, readme)
    report, _ = build_report(str(repo), goal="Find architecture patterns to borrow")
    assert report.dossier is not None
    assert "compound engineering workflow" in report.dossier.repo_purpose.lower()
    assert "npm install" not in report.dossier.repo_purpose.lower()
    assert report.idea_cards
    assert report.idea_cards[0].risk != "high"


def test_readme_noise_purpose_specific_for_weak_title_readme(tmp_path):
    repo = _write_repo(
        tmp_path,
        """# odysseus

[![build](https://img.shields.io/badge/build-passing-green.svg)](#)

A local-first research navigator that turns web pages, notes, and source references into a graph of tasks and summaries.

## How it works
Odysseus ingests references, extracts entities, and generates a prioritized exploration route for an operator.

## Quickstart
```bash
pip install -e .
python -m odysseus
```
""",
    )
    report, _ = build_report(str(repo))
    assert report.dossier is not None
    assert "research navigator" in report.dossier.repo_purpose.lower()
    assert "build-passing" not in report.dossier.repo_purpose.lower()
    assert report.idea_cards
    assert "research navigator" in (report.idea_cards[0].function + report.idea_cards[0].purpose).lower() or "exploration route" in (report.idea_cards[0].function + report.idea_cards[0].purpose).lower()


def test_readme_noise_gbrain_style_purpose_uses_high_signal_about(tmp_path):
    repo = _write_repo(
        tmp_path,
        """# gbrain

## About
GBrain is a personal knowledge graph workbench that captures notes, memories, and relationships for reflective planning.

## Features
It provides graph search, note linking, and timeline views for understanding evolving context.
""",
    )
    report, _ = build_report(str(repo))
    assert report.dossier is not None
    assert "knowledge graph workbench" in report.dossier.repo_purpose.lower()
    assert "graph search" in report.dossier.repo_function.lower() or "note linking" in report.dossier.repo_function.lower()


def test_readme_noise_example_platform_web_style_no_idea_card_from_nav_only(tmp_path):
    repo = _write_repo(
        tmp_path,
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
    )
    report, _ = build_report(str(repo))
    assert report.idea_cards == []
    assert report.dossier is not None
    assert "supports auth" not in report.dossier.repo_purpose.lower()
    assert any(c.kind == "install" for c in report.claims)

import json
from pathlib import Path

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.trending import load_github_trending_fixture, trending_report_to_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "github_trending_sample.html"


def test_github_trending_fixture_parser_metadata_only():
    report = load_github_trending_fixture(FIXTURE, window="daily", language_filter="python")

    assert report.schema_version == "repo-scout-github-trending-v1"
    assert report.source == "github_trending"
    assert report.safety["metadata_only"] is True
    assert report.safety["cloned_repositories"] is False
    assert report.safety["scanned_repositories"] is False
    assert report.safety["target_code_execution"] is False
    assert report.filters == {"window": "daily", "language": "python"}
    assert report.count == 3

    first = report.repositories[0]
    assert first.rank == 1
    assert first.owner == "alpha"
    assert first.repo == "project-one"
    assert first.full_name == "alpha/project-one"
    assert first.repo_url == "https://github.com/alpha/project-one"
    assert first.description == "A tiny framework for testing source metadata parsing."
    assert first.language == "Python"
    assert first.stars == 1234
    assert first.forks == 56
    assert first.recent_stars == 321
    assert first.window == "daily"
    assert first.language_filter == "python"
    assert first.selection_reason == "github_trending_rank_1_daily_python"
    assert first.triage_status == "new"

    # github.com/trending can appear as source/index metadata, but this parser never scans it.
    assert report.repositories[2].full_name == "github/trending"


def test_github_trending_markdown_shortlist():
    report = load_github_trending_fixture(FIXTURE, window="weekly")
    markdown = trending_report_to_markdown(report)

    assert "# GitHub Trending shortlist" in markdown
    assert "Safety: metadata-only; no clones, repo scans, or target code execution" in markdown
    assert "[alpha/project-one](https://github.com/alpha/project-one)" in markdown
    assert "github_trending_rank_2_weekly" in markdown


def test_sources_trending_cli_writes_json_and_markdown(tmp_path):
    json_path = tmp_path / "trending.json"
    markdown_path = tmp_path / "trending.md"
    result = CliRunner().invoke(
        main,
        [
            "sources",
            "trending",
            "--fixture",
            str(FIXTURE),
            "--window",
            "monthly",
            "--language",
            "TypeScript",
            "--out",
            str(json_path),
            "--markdown",
            str(markdown_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Safety: metadata-only; no clone, no repo scan, no target code execution" in result.output
    payload = json.loads(json_path.read_text())
    assert payload["schema_version"] == "repo-scout-github-trending-v1"
    assert payload["count"] == 3
    assert payload["filters"] == {"language": "TypeScript", "window": "monthly"}
    assert payload["repositories"][1]["stars"] == 2500
    assert payload["safety"]["cloned_repositories"] is False
    assert "beta/toolkit" in markdown_path.read_text()


def test_sources_trending_cli_requires_explicit_fixture_or_live(tmp_path):
    result = CliRunner().invoke(main, ["sources", "trending", "--out", str(tmp_path / "x.json")])

    assert result.exit_code != 0
    assert "Provide --fixture FILE" in result.output

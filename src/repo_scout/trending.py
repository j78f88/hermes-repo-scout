from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import __version__
from .planner import stable_id

GITHUB_TRENDING_URL = "https://github.com/trending"


@dataclass(frozen=True)
class TrendingRepo:
    rank: int
    owner: str
    repo: str
    full_name: str
    repo_url: str
    description: str | None
    language: str | None
    stars: int | None
    forks: int | None
    recent_stars: int | None
    window: str | None
    language_filter: str | None
    selection_reason: str
    triage_status: str = "new"


@dataclass(frozen=True)
class TrendingReport:
    schema_version: str
    source: str
    source_url: str
    generated_at: str
    runtime_ms: int
    generated_by: dict[str, str]
    safety: dict[str, Any]
    filters: dict[str, str | None]
    count: int
    repositories: list[TrendingRepo]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["repositories"] = [asdict(repo) for repo in self.repositories]
        return payload


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "_Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[_Node] = []
        self.text_parts: list[str] = []

    def text(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return " ".join(part for part in parts if part).strip()

    def descendants(self, tag: str | None = None) -> list["_Node"]:
        out: list[_Node] = []
        for child in self.children:
            if tag is None or child.tag == tag:
                out.append(child)
            out.extend(child.descendants(tag))
        return out


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs}, self.current)
        self.current.children.append(node)
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        text = _normalise_space(data)
        if text:
            self.current.text_parts.append(text)


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _class_contains(node: _Node, token: str) -> bool:
    return token in node.attrs.get("class", "").split()


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([\d,.]+)\s*([kKmM]?)", value.replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def _github_repo_link(node: _Node) -> tuple[str, str, str] | None:
    href = node.attrs.get("href", "")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", href)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    return owner, repo, href


def _repo_articles(root: _Node) -> list[_Node]:
    articles = [node for node in root.descendants("article") if _class_contains(node, "Box-row")]
    if articles:
        return articles
    # Fixture/API-like saved snippets may omit GitHub's Box-row class; keep the parser metadata-only
    # by accepting any article that contains an owner/repo link.
    return [node for node in root.descendants("article") if any(_github_repo_link(a) for a in node.descendants("a"))]


def _first_repo_anchor(article: _Node) -> tuple[_Node, tuple[str, str, str]] | None:
    for heading in [*article.descendants("h1"), *article.descendants("h2"), *article.descendants("h3")]:
        for anchor in heading.descendants("a"):
            repo_link = _github_repo_link(anchor)
            if repo_link:
                return anchor, repo_link
    for anchor in article.descendants("a"):
        repo_link = _github_repo_link(anchor)
        if repo_link:
            return anchor, repo_link
    return None


def _description(article: _Node) -> str | None:
    for paragraph in article.descendants("p"):
        text = _normalise_space(paragraph.text())
        if text:
            return text
    return None


def _language(article: _Node) -> str | None:
    for span in article.descendants("span"):
        if span.attrs.get("itemprop") == "programmingLanguage":
            text = _normalise_space(span.text())
            return text or None
    return None


def _link_number(article: _Node, suffix: str) -> int | None:
    for anchor in article.descendants("a"):
        href = anchor.attrs.get("href", "")
        if href.endswith(suffix):
            parsed = _parse_int(anchor.text())
            if parsed is not None:
                return parsed
    return None


def _recent_stars(article: _Node) -> int | None:
    for node in article.descendants():
        text = _normalise_space(node.text())
        if "stars" in text.lower() and any(word in text.lower() for word in ["today", "week", "month"]):
            parsed = _parse_int(text)
            if parsed is not None:
                return parsed
    return None


def parse_github_trending_html(
    html: str,
    *,
    window: str | None = None,
    language_filter: str | None = None,
    source_url: str = GITHUB_TRENDING_URL,
    generated_at: str | None = None,
    runtime_ms: int = 0,
) -> TrendingReport:
    """Parse GitHub Trending HTML into metadata-only records.

    This parser intentionally extracts only listing metadata. It never interprets
    github.com/trending as a target repository, never clones listed repositories,
    and never executes target code.
    """
    parser = _TreeParser()
    parser.feed(html)
    repos: list[TrendingRepo] = []
    for article in _repo_articles(parser.root):
        anchor = _first_repo_anchor(article)
        if not anchor:
            continue
        _, (owner, repo, href) = anchor
        full_name = f"{owner}/{repo}"
        repo_url = f"https://github.com{href}"
        rank = len(repos) + 1
        selection_reason = f"github_trending_rank_{rank}"
        if window:
            selection_reason += f"_{window}"
        if language_filter:
            selection_reason += f"_{language_filter}"
        repos.append(
            TrendingRepo(
                rank=rank,
                owner=owner,
                repo=repo,
                full_name=full_name,
                repo_url=repo_url,
                description=_description(article),
                language=_language(article),
                stars=_link_number(article, "/stargazers"),
                forks=_link_number(article, "/forks"),
                recent_stars=_recent_stars(article),
                window=window,
                language_filter=language_filter,
                selection_reason=selection_reason,
            )
        )
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return TrendingReport(
        schema_version="repo-scout-github-trending-v1",
        source="github_trending",
        source_url=source_url,
        generated_at=timestamp,
        runtime_ms=runtime_ms,
        generated_by={"tool": "repo-scout", "version": __version__},
        safety={
            "metadata_only": True,
            "cloned_repositories": False,
            "scanned_repositories": False,
            "target_code_execution": False,
            "notes": "github.com/trending is treated as a source index, not as a repository target.",
        },
        filters={"window": window, "language": language_filter},
        count=len(repos),
        repositories=repos,
    )


def collect_github_trending_live(*, window: str | None = None, language_filter: str | None = None, timeout: int = 20) -> TrendingReport:
    start = time.monotonic()
    path = GITHUB_TRENDING_URL
    if language_filter:
        path += "/" + quote(language_filter.strip("/"))
    query = {"since": window} if window else {}
    source_url = path + ("?" + urlencode(query) if query else "")
    request = Request(source_url, headers={"User-Agent": f"repo-scout/{__version__} metadata-only"})
    with urlopen(request, timeout=timeout) as response:  # nosec: metadata-only best-effort collector
        html = response.read().decode("utf-8", errors="replace")
    runtime_ms = int((time.monotonic() - start) * 1000)
    return parse_github_trending_html(html, window=window, language_filter=language_filter, source_url=source_url, runtime_ms=runtime_ms)


def load_github_trending_fixture(path: Path, *, window: str | None = None, language_filter: str | None = None) -> TrendingReport:
    start = time.monotonic()
    html = path.read_text(encoding="utf-8")
    runtime_ms = int((time.monotonic() - start) * 1000)
    return parse_github_trending_html(
        html,
        window=window,
        language_filter=language_filter,
        source_url=f"fixture://{path}",
        runtime_ms=runtime_ms,
    )


def trending_report_to_json(report: TrendingReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def trending_report_to_markdown(report: TrendingReport) -> str:
    lines = [
        "# GitHub Trending shortlist",
        "",
        f"- Source: `{report.source}`",
        f"- Source URL: {report.source_url}",
        f"- Generated: {report.generated_at}",
        f"- Runtime: {report.runtime_ms} ms",
        f"- Filters: window={report.filters.get('window') or 'unspecified'}, language={report.filters.get('language') or 'unspecified'}",
        "- Safety: metadata-only; no clones, repo scans, or target code execution",
        f"- Count: {report.count}",
        "",
        "| Rank | Repository | Language | Stars | Forks | Recent stars | Triage | Reason | Description |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for repo in report.repositories:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(repo.rank),
                    f"[{repo.full_name}]({repo.repo_url})",
                    _md(repo.language),
                    _md_number(repo.stars),
                    _md_number(repo.forks),
                    _md_number(repo.recent_stars),
                    _md(repo.triage_status),
                    _md(repo.selection_reason),
                    _md(repo.description),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _md(value: str | None) -> str:
    if value is None or value == "":
        return "—"
    return value.replace("|", "\\|")


def _md_number(value: int | None) -> str:
    return "—" if value is None else str(value)


def write_trending_outputs(report: TrendingReport, *, json_path: Path | None = None, markdown_path: Path | None = None) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(trending_report_to_json(report) + "\n", encoding="utf-8")
        paths["json"] = json_path
    if markdown_path:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(trending_report_to_markdown(report), encoding="utf-8")
        paths["markdown"] = markdown_path
    return paths


def default_trending_run_id(report: TrendingReport) -> str:
    return stable_id("trending", f"{report.source_url}|{report.generated_at}|{report.count}")

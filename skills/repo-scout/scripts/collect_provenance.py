#!/usr/bin/env python3
"""Public, read-only provenance for one GitHub repository.

Fetches GitHub repo/head/release metadata, the OpenSSF Scorecard result and
the deps.dev project record. Saves raw response bytes with SHA-256, registers
each saved file in the campaign citation ledger, and writes a summary whose
every fact carries a `file.json#/pointer` evidence ref.

Public GET requests only. Nothing is cloned, installed or run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_BYTES = 2_000_000
USER_AGENT = "repo-scout-provenance"
Fetcher = Callable[[], bytes]


def _http(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed public https hosts
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"response over {MAX_BYTES} bytes")
    return body


def _gh(path: str) -> bytes:
    """GitHub REST via the existing `gh` login (rate limits only), falling back to anonymous HTTPS."""
    try:
        done = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                              capture_output=True, timeout=60, check=False)
        if done.returncode == 0 and done.stdout:
            if len(done.stdout) > MAX_BYTES:
                raise ValueError(f"response over {MAX_BYTES} bytes")
            return done.stdout
        error = done.stderr.decode(errors="replace").strip()
        if "404" in error or "Not Found" in error:
            raise ValueError(f"GitHub returned not found for {path}")
    except FileNotFoundError:
        pass
    return _http(f"https://api.github.com/{path}")


def default_fetchers(owner: str, repo: str) -> dict[str, tuple[str, Fetcher]]:
    key = urllib.parse.quote(f"github.com/{owner}/{repo}", safe="")
    scorecard = f"https://api.securityscorecards.dev/projects/github.com/{owner}/{repo}"
    depsdev = f"https://api.deps.dev/v3/projects/{key}"
    base = f"https://api.github.com/repos/{owner}/{repo}"
    return {
        "github_repo": (base, lambda: _gh(f"repos/{owner}/{repo}")),
        "github_head": (f"{base}/commits/HEAD", lambda: _gh(f"repos/{owner}/{repo}/commits/HEAD")),
        "github_releases": (f"{base}/releases?per_page=5", lambda: _gh(f"repos/{owner}/{repo}/releases?per_page=5")),
        "scorecard": (scorecard, lambda: _http(scorecard)),
        "depsdev": (depsdev, lambda: _http(depsdev)),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def register_sources(ledger_path: Path, campaign_root: Path, rows: list[dict]) -> list[int]:
    """Add one ledger source per saved file. Existing entries are never rewritten.

    Evidence files are immutable (each collection run writes a new folder), so
    a path that is already registered with another hash is an error.
    """
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {"schema_version": "repo-scout.citation-ledger.v1", "sources": []}
    sources = ledger.setdefault("sources", [])
    ids: list[int] = []
    changed = False
    for row in rows:
        if row.get("status") != "ok":
            continue
        rel = row["campaign_path"]
        existing = next((s for s in sources if s.get("path") == rel), None)
        if existing is not None:
            if existing.get("sha256") != row["sha256"]:
                raise RuntimeError(f"ledger already cites {rel} with a different hash; evidence must not be overwritten")
            ids.append(existing["id"])
            continue
        entry = {
            "id": max([s.get("id", 0) for s in sources] + [0]) + 1,
            "url": row["url"],
            "title": f"provenance {row['name']}",
            "accessed": row["retrieved_at"][:10],
            "retrieved_at": row["retrieved_at"],
            "path": rel,
            "sha256": row["sha256"],
        }
        sources.append(entry)
        ids.append(entry["id"])
        changed = True
    if changed or not ledger_path.is_file():
        _atomic_write(ledger_path, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode())
    return ids


def _new_run_dir(out: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out / stamp
    n = 1
    while run_dir.exists():
        n += 1
        run_dir = out / f"{stamp}-{n}"
    run_dir.mkdir(parents=True)
    return run_dir


def collect(owner: str, repo: str, out: Path, *, campaign_root: Path | None = None,
            fetchers: dict[str, tuple[str, Fetcher]] | None = None) -> tuple[Path, list[dict]]:
    """Fetch every source into a new immutable run folder under `out`."""
    run_dir = _new_run_dir(out)
    rows: list[dict] = []
    for name, (url, fetch) in (fetchers or default_fetchers(owner, repo)).items():
        row: dict[str, Any] = {"name": name, "url": url, "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
        try:
            body = fetch()
            json.loads(body)  # must be JSON evidence
            target = run_dir / f"{name}.json"
            target.write_bytes(body)
            row.update({"path": target.name, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "status": "ok"})
            if campaign_root is not None:
                row["campaign_path"] = str(target.resolve().relative_to(campaign_root.resolve()))
        except Exception as exc:  # noqa: BLE001 - a missing source is a recorded gap, not a crash
            row.update({"status": "unavailable", "error": str(exc)[:300]})
        rows.append(row)
    _atomic_write(run_dir / "provenance-index.json", (json.dumps(rows, indent=2) + "\n").encode())
    return run_dir, rows


def _load(out: Path, name: str) -> Any:
    path = out / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except json.JSONDecodeError:
        return None


def summarise(out: Path, ref_prefix: str = "") -> dict:
    """Facts with pointer refs, plus the cross-checks a risk lane should explain.

    `out` is one run folder; facts come only from that run's successful files.
    """
    repo = _load(out, "github_repo") or {}
    head = _load(out, "github_head") or {}
    releases = _load(out, "github_releases") or []
    scorecard = _load(out, "scorecard") or {}
    depsdev = _load(out, "depsdev") or {}

    def ref(name: str, pointer: str) -> str:
        return f"{ref_prefix}{name}.json#{pointer}"

    facts: dict[str, dict] = {}

    def fact(key: str, value: Any, *refs: str) -> None:
        facts[key] = {"value": value, "evidence_refs": list(refs) if value is not None else []}

    gh_license = (repo.get("license") or {}).get("spdx_id") if isinstance(repo, dict) else None
    fact("full_name", repo.get("full_name"), ref("github_repo", "/full_name"))
    fact("owner_type", (repo.get("owner") or {}).get("type"), ref("github_repo", "/owner/type"))
    fact("default_branch", repo.get("default_branch"), ref("github_repo", "/default_branch"))
    fact("archived", repo.get("archived"), ref("github_repo", "/archived"))
    fact("fork", repo.get("fork"), ref("github_repo", "/fork"))
    fact("pushed_at", repo.get("pushed_at"), ref("github_repo", "/pushed_at"))
    fact("stars", repo.get("stargazers_count"), ref("github_repo", "/stargazers_count"))
    fact("open_issues", repo.get("open_issues_count"), ref("github_repo", "/open_issues_count"))
    fact("license_github", gh_license, ref("github_repo", "/license/spdx_id"))
    fact("head_commit", head.get("sha"), ref("github_head", "/sha"))
    fact("head_committed_at", ((head.get("commit") or {}).get("committer") or {}).get("date"), ref("github_head", "/commit/committer/date"))
    latest = releases[0] if isinstance(releases, list) and releases else {}
    fact("latest_release", latest.get("tag_name"), ref("github_releases", "/0/tag_name"))
    fact("latest_release_at", latest.get("published_at"), ref("github_releases", "/0/published_at"))
    fact("license_depsdev", depsdev.get("license"), ref("depsdev", "/license"))
    fact("scorecard_score", scorecard.get("score"), ref("scorecard", "/score"))
    fact("scorecard_date", scorecard.get("date"), ref("scorecard", "/date"))
    sc_commit = (scorecard.get("repo") or {}).get("commit")
    fact("scorecard_commit", sc_commit, ref("scorecard", "/repo/commit"))
    low = []
    for idx, check in enumerate(scorecard.get("checks") or []):
        score = check.get("score")
        if isinstance(score, (int, float)) and 0 <= score < 5:
            low.append({"name": check.get("name"), "score": score, "evidence_ref": ref("scorecard", f"/checks/{idx}/score")})

    cross_checks = []
    if gh_license and depsdev.get("license") and gh_license != depsdev.get("license"):
        cross_checks.append({"check": "license_disagrees", "detail": f"GitHub says {gh_license}; deps.dev says {depsdev.get('license')}",
                             "evidence_refs": [ref("github_repo", "/license/spdx_id"), ref("depsdev", "/license")]})
    if sc_commit and head.get("sha") and sc_commit != head.get("sha"):
        cross_checks.append({"check": "scorecard_stale", "detail": f"Scorecard rated {sc_commit[:12]}, head is {head['sha'][:12]}",
                             "evidence_refs": [ref("scorecard", "/repo/commit"), ref("github_head", "/sha")]})
    if repo.get("archived"):
        cross_checks.append({"check": "archived", "detail": "Repository is archived", "evidence_refs": [ref("github_repo", "/archived")]})
    index = json.loads((out / "provenance-index.json").read_text(encoding="utf-8")) if (out / "provenance-index.json").is_file() else []
    return {
        "schema_version": "repo-scout.provenance-summary.v1",
        "facts": facts,
        "scorecard_low_checks": low,
        "cross_checks": cross_checks,
        "gaps": [f"{row['name']}: {row.get('error')}" for row in index if row.get("status") != "ok"],
        "sources": {row["name"]: row.get("status") for row in index},
        "safety": "public read-only metadata; no clone, install, build, test or target code execution",
    }


def run(owner: str, repo: str, out: Path, *, campaign_root: Path | None = None,
        fetchers: dict[str, tuple[str, Fetcher]] | None = None) -> dict:
    run_dir, rows = collect(owner, repo, out, campaign_root=campaign_root, fetchers=fetchers)
    prefix = ""
    if campaign_root is not None:
        prefix = str(run_dir.resolve().relative_to(campaign_root.resolve())) + "/"
        register_sources(campaign_root / "citation-ledger.json", campaign_root, rows)
    summary = summarise(run_dir, ref_prefix=prefix)
    summary["run_folder"] = run_dir.name
    data = (json.dumps(summary, indent=2) + "\n").encode()
    _atomic_write(run_dir / "provenance-summary.json", data)
    _atomic_write(out / "provenance-summary.json", data)  # latest run; refs point into its run folder
    return summary


def parse_github(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlparse(url)
    bits = [p for p in parts.path.strip("/").split("/") if p]
    if parts.netloc.lower() not in {"github.com", "www.github.com"} or len(bits) < 2:
        raise ValueError(f"not a GitHub repository URL: {url}")
    return bits[0], bits[1].removesuffix(".git")


def main() -> int:
    p = argparse.ArgumentParser(description="Public read-only provenance for one GitHub repository")
    p.add_argument("repo_url")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--campaign-root", type=Path)
    args = p.parse_args()
    owner, name = parse_github(args.repo_url)
    summary = run(owner, name, args.out, campaign_root=args.campaign_root)
    print(json.dumps({k: summary[k] for k in ("cross_checks", "gaps")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

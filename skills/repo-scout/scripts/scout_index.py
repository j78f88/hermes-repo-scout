#!/usr/bin/env python3
"""Index past Repo Scout campaigns and check whether a repo needs a re-scout.

`build_index` reads only campaign manifests, repo sidecars, verification
results and provenance summaries. It never opens folders whose path has a
configured excluded part. The index is a rebuildable local cache, not durable evidence.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from scout_paths import excluded_name, excluded_path, runs_root

CAMPAIGN_SCHEMA = "repo-scout.campaign.v1"


def index_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("REPO_SCOUT_INDEX"):
        return Path(env["REPO_SCOUT_INDEX"]).expanduser()
    return Path(env.get("HOME", str(Path.home()))).expanduser() / ".hermes" / "cache" / "repo-scout-index.json"


SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_forbidden_name(name: str) -> bool:
    """Configurable excluded path component, matched case-insensitively."""
    return excluded_name(name)


def _forbidden_path(path: Path) -> bool:
    return excluded_path(path)


class BoundaryError(ValueError):
    pass


def _check_base(base: Path) -> Path:
    if _forbidden_path(base):
        raise BoundaryError(f"refusing to index an excluded path: {base}")
    resolved = base.expanduser().resolve()
    return resolved


def _safe_target(base: Path, path: Path) -> Path | None:
    """Resolved read target, only if it stays inside base and outside exclusions."""
    if _forbidden_path(path):
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if _forbidden_path(resolved) or not resolved.is_relative_to(base):
        return None
    return resolved


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _manifests(base: Path, max_depth: int = 3) -> list[Path]:
    """Walk base without entering excluded folders or following directory symlinks."""
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if is_forbidden_name(entry.name):
                continue  # pruned before descent: never listed or opened
            if entry.is_dir(follow_symlinks=False):
                if depth < max_depth:
                    walk(Path(entry.path), depth + 1)
            elif entry.name == "manifest.json" and depth >= 2 and entry.is_file(follow_symlinks=False):
                found.append(Path(entry.path))

    walk(base, 0)
    return found


def norm_repo(url: str | None) -> str:
    return (url or "").strip().rstrip("/").removesuffix(".git").lower()


PROVENANCE_SOURCES = ("github_repo", "github_head", "github_releases", "scorecard", "depsdev")


def _prov_sources(prov: dict) -> dict:
    """Per-source status. Older summaries lack `sources`; their `gaps` list every failed source."""
    if isinstance(prov.get("sources"), dict):
        return prov["sources"]
    if not prov.get("facts"):
        return {}
    failed = {str(g).split(":", 1)[0] for g in prov.get("gaps") or []}
    return {name: ("unavailable" if name in failed else "ok") for name in PROVENANCE_SOURCES}


def build_index(base: Path) -> list[dict]:
    base = _check_base(base)
    rows: list[dict] = []

    def read(path: Path) -> Any:
        target = _safe_target(base, path)
        return _read_json(target) if target is not None and target.is_file() else None

    for manifest_path in _manifests(base):
        manifest = read(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != CAMPAIGN_SCHEMA:
            continue
        root = manifest_path.parent
        verification = _safe_target(base, root / "synthesis" / "verification.txt")
        result = "none"
        if verification is not None and verification.is_file():
            first = (verification.read_text(encoding="utf-8", errors="replace").splitlines() or [""])[0]
            result = first.rsplit(":", 1)[-1].strip() or "none"
        for repo in manifest.get("repositories") or []:
            slug = repo.get("repo_slug") if isinstance(repo, dict) else None
            if not isinstance(slug, str) or not SLUG_RE.match(slug) or is_forbidden_name(slug):
                continue
            fresh = repo.get("freshness") if isinstance(repo.get("freshness"), dict) else {}
            commit = fresh.get("inspected_commit")
            source = "collector"
            if commit in (None, "", "unknown"):
                commit = fresh.get("supplemental_commit")
                source = "supplemental" if commit else "none"
            repo_root = root / "repos" / repo["repo_slug"]
            side = read(repo_root / "repo-scout-sidecar.json") or {}
            prov = read(repo_root / "evidence" / "provenance" / "provenance-summary.json") or {}
            facts = prov.get("facts") or {}
            rows.append({
                "repo": norm_repo(repo.get("repo_url")),
                "repo_url": repo.get("repo_url"),
                "campaign_root": str(root),
                "campaign_id": manifest.get("campaign_id"),
                "mode": manifest.get("mode"),
                "inspected_at": fresh.get("inspected_at"),
                "inspected_commit": commit,
                "commit_source": source,
                "provenance_recorded": bool(facts),
                "provenance_sources": _prov_sources(prov),
                "license": (facts.get("license_github") or {}).get("value"),
                "latest_release": (facts.get("latest_release") or {}).get("value"),
                "archived": (facts.get("archived") or {}).get("value"),
                "decision": side.get("decision") or manifest.get("decision"),
                "consumption_mode": side.get("consumption_mode") or manifest.get("consumption_mode"),
                "build_gate": side.get("build_gate") or manifest.get("build_gate"),
                "repo_status": repo.get("status"),
                "verification": result,
            })
    rows.sort(key=lambda r: (r["repo"], r.get("inspected_at") or ""))
    return rows


def write_index(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"schema_version": "repo-scout.index.v1", "rows": rows}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def latest_for(rows: list[dict], repo_url: str) -> dict | None:
    key = norm_repo(repo_url)
    matches = [r for r in rows if r["repo"] == key]
    verified = [r for r in matches if r["verification"] == "PASS"] or matches
    return sorted(verified, key=lambda r: r.get("inspected_at") or "")[-1] if verified else None


def refresh_check(row: dict | None, live: dict) -> dict:
    """Compare a prior scout with live public facts.

    `live` keys: head, license, archived, latest_release, and optional
    `available` (False when GitHub could not be read). Licence, archive and
    release transitions in either direction are material when the prior value
    is known. New commits alone are minor. Missing evidence is never read as
    "no change".
    """
    if row is None:
        return {"result": "no_prior_scout", "material": [], "changes": [], "gaps": [], "prior": None}
    if live.get("available") is False or not live.get("head"):
        return {"result": "unknown_live_state", "material": [], "changes": [],
                "gaps": ["live GitHub head could not be read"], "prior": row["campaign_root"]}
    changes: list[str] = []
    material: list[str] = []
    gaps: list[str] = []
    if not row.get("inspected_commit"):
        material.append("prior scout recorded no commit")
    elif live["head"] != row["inspected_commit"]:
        changes.append(f"new commits since {row['inspected_commit'][:12]}")
    prior_sources = row.get("provenance_sources") or {}
    prior_repo_known = bool(row.get("provenance_recorded")) and prior_sources.get("github_repo") == "ok"
    prior_releases_known = bool(row.get("provenance_recorded")) and prior_sources.get("github_releases") == "ok"
    if not row.get("provenance_recorded"):
        gaps.append("prior scout recorded no licence, archive or release facts")
    if row.get("provenance_recorded") and not prior_repo_known:
        gaps.append("prior licence and archive state were not observed")
    if row.get("provenance_recorded") and not prior_releases_known:
        gaps.append("prior release list was not observed")
    if prior_repo_known:
        if live.get("license") != row.get("license"):
            material.append(f"licence {row.get('license')} -> {live.get('license')}")
        if bool(live.get("archived")) != bool(row.get("archived")):
            material.append("repository is now archived" if live.get("archived") else "repository is no longer archived")
    if prior_releases_known:
        if live.get("releases_available") is False:
            gaps.append("live release list could not be read")
        elif live.get("latest_release") != row.get("latest_release"):
            if row.get("latest_release") is None:
                material.append(f"first release {live.get('latest_release')}")
            elif live.get("latest_release") is None:
                material.append(f"release {row.get('latest_release')} no longer listed")
            else:
                material.append(f"new release {row.get('latest_release')} -> {live.get('latest_release')}")
    if material:
        result = "material_delta"
    elif gaps:
        result = "incomplete_evidence"
    else:
        result = "minor_delta" if changes else "no_material_delta"
    return {"result": result, "material": material, "changes": changes, "gaps": gaps, "prior": row["campaign_root"]}


def live_facts(repo_url: str, fetch: Callable[..., dict] | None = None) -> dict:
    """Fetch head, licence, archive flag and latest release using the provenance collector."""
    import collect_provenance as prov

    owner, name = prov.parse_github(repo_url)
    if fetch is not None:
        return fetch(owner, name)
    with tempfile.TemporaryDirectory(prefix="repo-scout-refresh-") as td:
        fetchers = {k: v for k, v in prov.default_fetchers(owner, name).items() if k.startswith("github_")}
        run_dir, rows = prov.collect(owner, name, Path(td), fetchers=fetchers)
        facts = prov.summarise(run_dir)["facts"]
    by_name = {row["name"]: row["status"] for row in rows}
    return {
        "available": by_name.get("github_repo") == "ok" and by_name.get("github_head") == "ok",
        "releases_available": by_name.get("github_releases") == "ok",
        "head": facts["head_commit"]["value"],
        "license": facts["license_github"]["value"],
        "archived": facts["archived"]["value"],
        "latest_release": facts["latest_release"]["value"],
    }

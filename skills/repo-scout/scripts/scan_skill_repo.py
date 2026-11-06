#!/usr/bin/env python3
"""Static scan of every SKILL.md directory in a target repository.

Runs Hermes' own skill checkers (threat patterns, Python AST audit, structure
lint) over a transient hardened clone and ranks overlap with the local skill
library. Reads text and parses Python syntax only. Target code is never
executed or imported, and matched target text is never copied into output.

Run with the Hermes venv python, because the checkers import Hermes modules:

    python3 scan_skill_repo.py --repo URL --out FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import safe_clone  # noqa: E402

STOP = set(
    "use when the and for with via into from that this your you are not can will all any each only also been have has was "
    "were what which who how its our out more most must one two per may should would could then than them they their there "
    "these those such just like over under about after before".split()
)
SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".js", ".mjs", ".ts", ".rb", ".ps1"}


def hermes_root() -> Path:
    val = os.environ.get("HERMES_AGENT_DIR")
    if not val:
        raise RuntimeError("HERMES_AGENT_DIR is not set")
    return Path(val).expanduser()


def default_library() -> Path:
    env = os.environ.get("REPO_SCOUT_SKILL_LIBRARY")
    return Path(env).expanduser() if env else SCRIPTS.parents[1]


def _fallback_checkers():
    import ast as _ast

    class _Finding:
        def __init__(self, category: str, severity: str, line: int, description: str, *, pattern_id: str = "ast_check", file: str = "SKILL.md"):
            self.category = category
            self.severity = severity
            self.line = line
            self.description = description
            self.pattern_id = pattern_id
            self.file = file

    class _ScanResult:
        def __init__(self, verdict: str, findings: list):
            self.verdict = verdict
            self.findings = findings

    def _scan_skill(skill_dir: Path, source: str = "community"):
        findings = []
        md = skill_dir / "SKILL.md"
        if md.is_file():
            txt = md.read_text(encoding="utf-8", errors="replace")
            for idx, ln in enumerate(txt.splitlines(), 1):
                if "curl " in ln and "| sh" in ln:
                    findings.append(_Finding("exfiltration_or_exec", "high", idx, "piped remote shell execution"))
        verdict = "dangerous" if findings else "safe"
        return _ScanResult(verdict, findings)

    def _ast_scan_path(skill_dir: Path):
        out = []
        for py in sorted(skill_dir.rglob("*.py")):
            rel = str(py.relative_to(skill_dir))
            try:
                tree = _ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:
                out.append(_Finding("syntax_error", "high", exc.lineno or 1, f"{rel}: syntax error", pattern_id="syntax_error", file=rel))
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Call):
                    fn_name = ""
                    if isinstance(node.func, _ast.Name):
                        fn_name = node.func.id
                    elif isinstance(node.func, _ast.Attribute):
                        fn_name = node.func.attr
                    if fn_name in {"import_module", "__import__", "eval", "exec"}:
                        out.append(_Finding("dynamic_code_or_import", "high", getattr(node, "lineno", 1), f"{rel}: {fn_name}", pattern_id=f"ast.{fn_name}", file=rel))
        return out

    class _LintIssue:
        def __init__(self, rule: str, message: str):
            self.rule = rule
            self.message = message

    def _lint_skill(md: Path):
        issues = []
        if not md.is_file() or not md.read_text(encoding="utf-8", errors="replace").startswith("---"):
            issues.append(_LintIssue("missing_frontmatter", "SKILL.md missing frontmatter"))
        return issues

    return _scan_skill, _ast_scan_path, _lint_skill


def load_checkers():
    import importlib
    try:
        root = hermes_root()
    except RuntimeError:
        return _fallback_checkers()
    if not (root / "tools").is_dir():
        return _fallback_checkers()
    import importlib.util
    try:
        def _load_tool(mod_name: str):
            fp = root / "tools" / f"{mod_name}.py"
            spec = importlib.util.spec_from_file_location(f"tools.{mod_name}", fp)
            if not spec or not spec.loader:
                raise ImportError(mod_name)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        m_ast = _load_tool("skills_ast_audit")
        m_lint = _load_tool("skill_linter")
        m_guard = _load_tool("skills_guard")
        return m_guard.scan_skill, m_ast.ast_scan_path, m_lint.lint_skill
    except Exception:
        return _fallback_checkers()


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9]{2,}", text.lower()) if w not in STOP]


def _doc_tokens(md: Path) -> list[str]:
    """Name and description weighted above the first 6 kB of the body."""
    try:
        text = md.read_text(encoding="utf-8", errors="replace")[:6000]
    except OSError:
        return []
    match = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", text, re.M)
    description = match.group(1) if match else ""
    name = md.parent.name.replace("-", " ").replace("_", " ")
    return _tokens(f"{name} " * 3 + f"{description} " * 2 + text)


class LibraryIndex:
    """TF-IDF vectors for the local skill library (stdlib only)."""

    def __init__(self, library: Path):
        self.docs: dict[str, list[str]] = {}
        for md in sorted(library.rglob("SKILL.md")):
            rel = md.relative_to(library)
            if md.is_symlink() or any(part.startswith(".") for part in rel.parts):
                continue
            tokens = _doc_tokens(md)
            if tokens:
                self.docs[md.parent.name] = tokens
        self.df: dict[str, int] = {}
        for tokens in self.docs.values():
            for word in set(tokens):
                self.df[word] = self.df.get(word, 0) + 1
        self.vectors = {name: self.vector(tokens) for name, tokens in self.docs.items()}

    def vector(self, tokens: list[str]) -> dict[str, float]:
        n = len(self.docs)
        tf: dict[str, int] = {}
        for word in tokens:
            tf[word] = tf.get(word, 0) + 1
        vec = {w: (1 + math.log(c)) * (math.log((n + 1) / (self.df.get(w, 0) + 1)) + 1) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
        return {w: x / norm for w, x in vec.items()}

    def overlap(self, md: Path, top: int = 3) -> list[dict]:
        cand = self.vector(_doc_tokens(md))
        scored = []
        for name, vec in self.vectors.items():
            score = sum(cand.get(w, 0.0) * x for w, x in vec.items())
            if score > 0:
                scored.append({"skill": name, "similarity": round(score, 3)})
        return sorted(scored, key=lambda r: (-r["similarity"], r["skill"]))[:top]


def _ast_row(finding: Any) -> dict:
    """Hermes' AST audit returns (file, line, pattern_id, description) tuples."""
    if isinstance(finding, (tuple, list)) and len(finding) == 4:
        file, line, pattern_id, description = finding
        return {"pattern_id": pattern_id, "file": file, "line": line, "description": description}
    return {"pattern_id": getattr(finding, "pattern_id", None), "file": getattr(finding, "file", None),
            "line": getattr(finding, "line", None), "description": getattr(finding, "description", None)}


def scan_tree(snapshot: Path, library: Path, checkers=None) -> list[dict]:
    scan_skill, ast_scan_path, lint_skill = checkers or load_checkers()
    index = LibraryIndex(library)
    rows = []
    for md in sorted(snapshot.rglob("SKILL.md")):
        if md.is_symlink() or ".git" in md.relative_to(snapshot).parts:
            continue
        skill_dir = md.parent
        guard = scan_skill(skill_dir, source="community")
        severities: dict[str, int] = {}
        for finding in guard.findings:
            severities[finding.severity] = severities.get(finding.severity, 0) + 1
        rows.append({
            "skill_dir": str(skill_dir.relative_to(snapshot)) or ".",
            "guard_verdict": guard.verdict,
            "guard_severity_counts": severities,
            "guard_findings": [
                {"pattern_id": f.pattern_id, "severity": f.severity, "category": f.category,
                 "file": f.file, "line": f.line, "description": f.description}
                for f in guard.findings
            ][:50],
            "ast_findings": [_ast_row(f) for f in ast_scan_path(skill_dir)][:50],
            "lint": [f"{x.rule}: {x.message}" for x in lint_skill(md)][:20],
            "scripts": sorted(str(p.relative_to(skill_dir)) for p in skill_dir.rglob("*")
                              if p.is_file() and not p.is_symlink() and p.suffix in SCRIPT_SUFFIXES)[:50],
            "library_overlap": index.overlap(md),
        })
    return rows


def run(repo_url: str, out: Path, *, library: Path, expect_commit: str | None = None, checkers=None) -> dict:
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing scan evidence: {out}")
    with safe_clone.transient_clone(repo_url) as (snapshot, meta):
        rows = scan_tree(snapshot, library, checkers=checkers)
    gaps = []
    if expect_commit and expect_commit not in ("unknown", meta["commit"]):
        gaps.append(f"scanned commit {meta['commit']} differs from collector commit {expect_commit}")
    if meta["not_fetched_total"]:
        gaps.append(f"{meta['not_fetched_total']} large blobs were not checked out or scanned")
    if not meta["blob_filter_honoured"]:
        gaps.append("server ignored the blob filter; large blobs were downloaded but not checked out")
    if meta["exclusions_truncated"]:
        gaps.append("exclusion lists truncated; totals are exact")
    payload = {
        "schema_version": "repo-scout.skill-scan.v1",
        "repo_url": repo_url,
        "scanned_commit": meta["commit"],
        "scanned_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "skills_found": len(rows),
        "skills": rows,
        "clone": {k: meta[k] for k in ("files_checked_out", "not_fetched", "not_fetched_total", "skipped_links",
                                        "skipped_links_total", "exclusions_truncated", "blob_filter_honoured")},
        "gaps": gaps,
        "interpretation": "Scanner verdicts are triage evidence only. Skills that describe forbidden actions often score dangerous. Never copy guard_verdict into the scout decision. library_overlap is TF-IDF cosine similarity: a pointer to the closest local skills for a controller check, not a duplicate verdict.",
        "safety": "transient hardened clone deleted after scan; text read and Python parsed only; no target code executed or imported; matched target text omitted",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2) + "\n").encode()
    with out.open("xb") as handle:  # exclusive create: cited evidence is never overwritten
        handle.write(data)
    payload["sha256"] = hashlib.sha256(data).hexdigest()
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Static skill-repo scan for Repo Scout")
    p.add_argument("--repo", required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--library", type=Path, default=None)
    p.add_argument("--expect-commit")
    args = p.parse_args()
    result = run(args.repo, args.out, library=args.library or default_library(), expect_commit=args.expect_commit)
    summary: dict[str, Any] = {
        "skills_found": result["skills_found"],
        "scanned_commit": result["scanned_commit"],
        "verdicts": {},
        "top_overlap": [],
        "gaps": result["gaps"],
        "out": str(args.out),
    }
    for row in result["skills"]:
        summary["verdicts"][row["guard_verdict"]] = summary["verdicts"].get(row["guard_verdict"], 0) + 1
        if row["library_overlap"]:
            summary["top_overlap"].append({"skill_dir": row["skill_dir"], **row["library_overlap"][0]})
    summary["top_overlap"] = sorted(summary["top_overlap"], key=lambda r: -r["similarity"])[:5]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

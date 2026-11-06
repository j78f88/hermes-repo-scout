from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel


WORKSPACE_DIRS = ("snapshots", "reports", "runs")


def _git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def default_workspace_path(start: Path | None = None) -> Path:
    """Return the default repo-scout workspace path.

    Prefer the containing repository root when the CLI is invoked from inside a
    git checkout; otherwise use the current working directory. This keeps normal
    development invocations anchored at ``<repo>/.repo-scout`` while still being
    deterministic for arbitrary directories.
    """
    base = (start or Path.cwd()).resolve()
    root = _git_root(base) or base
    return root / ".repo-scout"


def ensure_workspace(workspace: Path | str | None = None) -> Path:
    root = Path(workspace).expanduser().resolve() if workspace is not None else default_workspace_path()
    for dirname in WORKSPACE_DIRS:
        (root / dirname).mkdir(parents=True, exist_ok=True)
    return root


def workspace_snapshot_dir(workspace: Path, slug: str, run_id: str) -> Path:
    safe_slug = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in slug).strip(".-") or "repo"
    safe_run = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in run_id).strip("-") or "run"
    return workspace / "snapshots" / f"{safe_slug}-{safe_run}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_jsonable(record), sort_keys=True) + "\n")


def record_scan(workspace: Path | str | None, report: Any, plan: Any, output_paths: dict[str, Path]) -> dict[str, Any]:
    """Persist minimal corpus records for a completed scan.

    The user-requested report output stays wherever ``--out`` points. A copy is
    also placed under ``workspace/reports/<run_id>/`` so the corpus has stable
    report artifacts even when ``--out`` is temporary.
    """
    root = ensure_workspace(workspace)
    run_id = getattr(report, "run_id", "run")
    corpus_report_dir = root / "reports" / run_id
    corpus_report_dir.mkdir(parents=True, exist_ok=True)
    corpus_paths: dict[str, Path] = {}
    for kind, src in output_paths.items():
        src_path = Path(src)
        if src_path.exists() and src_path.is_file():
            dest_name = "research_plan.json" if kind == "plan" else src_path.name
            dest = corpus_report_dir / dest_name
            if src_path.resolve() != dest.resolve():
                shutil.copy2(src_path, dest)
            corpus_paths[kind] = dest

    repo = getattr(report, "repo", None)
    repo_record = {
        "schema_version": "repo-scout.corpus.repo.v1",
        "run_id": run_id,
        "plan_id": getattr(report, "plan_id", None),
        "repo": repo,
    }
    scan_record = {
        "schema_version": "repo-scout.corpus.scan.v1",
        "run_id": run_id,
        "plan_id": getattr(report, "plan_id", None),
        "workspace": root,
        "report_paths": output_paths,
        "corpus_report_paths": corpus_paths,
        "snapshot_path": getattr(repo, "path", None),
        "runner_policy": getattr(report, "runner_policy", None),
        "metrics": getattr(report, "metrics", None),
    }
    append_jsonl(root / "runs" / "repos.jsonl", repo_record)
    append_jsonl(root / "runs" / "scans.jsonl", scan_record)
    return {"workspace": root, "repo_record": root / "runs" / "repos.jsonl", "scan_record": root / "runs" / "scans.jsonl", "corpus_report_dir": corpus_report_dir}

#!/usr/bin/env python3
"""Lint Repo Scout lane files and repair format-only faults with a record.

`lint_lane` never changes evidence, never turns hold/fail into pass and never
invents references. `fix_campaign` keeps the original bytes under
`receipts/pre-lint/` before writing a repaired file.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LANE_SCHEMA = "repo-scout.lane-output.v1"
STATUSES = {"pass", "hold", "fail"}
FINDING_FIELDS = ("fact", "upstream_claim", "independent_evidence", "inference", "unknown")
FORBIDDEN_PHRASE = "no target repo code execution"
SUPPORTED_REF = re.compile(r"^(?:[^#]+#/.*|[^#]+#L\d+(?:-L?\d+)?|[A-Z][A-Z0-9_]*\d+)$")
REQUIRED = ("schema_version", "lane_id", "status", "evidence_refs", "findings", "forbidden_actions_observed")


def _item_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "claim", "summary", "statement"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, sort_keys=True)


def lint_lane(data: Any) -> tuple[list[str], list[str], Any]:
    """Return (fixable faults, manual faults, repaired copy)."""
    if not isinstance(data, dict):
        return [], ["lane file must be a JSON object (not auto-fixable)"], data
    fixable: list[str] = []
    manual: list[str] = []
    fixed: dict = json.loads(json.dumps(data))
    for key in REQUIRED:
        if key not in data:
            manual.append(f"missing required field {key!r}")
    if "schema_version" in data and data.get("schema_version") != LANE_SCHEMA:
        manual.append(f"schema_version must be {LANE_SCHEMA!r}")
    if "lane_id" in data and (not isinstance(data.get("lane_id"), str) or not data["lane_id"].strip()):
        manual.append("lane_id must be a non-empty string")
    for field in ("gaps", "sources_used", "source_classes"):
        if field not in data:
            continue
        value = data[field]
        if isinstance(value, str):
            fixable.append(f"{field} is a string; wrapped in a list")
            fixed[field] = [value]
        elif not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            manual.append(f"{field} must be a list of strings")
    status = data.get("status")
    if "status" in data and not isinstance(status, str):
        manual.append("status must be the string pass, hold or fail (not auto-fixable)")
    elif isinstance(status, str) and status.strip().lower() in STATUSES and status != status.strip().lower():
        fixable.append(f"status {status!r} must be lowercase")
        fixed["status"] = status.strip().lower()
    elif "status" in data and isinstance(status, str) and status not in STATUSES:
        manual.append(f"status {status!r} is not pass, hold or fail (not auto-fixable)")
    if "findings_detail" in data and not isinstance(data["findings_detail"], dict):
        manual.append("findings_detail must be an object (not auto-fixable)")
        fixed.pop("findings_detail", None)
        detail_ok = False
    else:
        detail_ok = True
    findings = data.get("findings")
    if isinstance(findings, dict):
        for field in FINDING_FIELDS:
            if field not in findings:
                fixable.append(f"findings.{field} is missing; added as an empty list")
                fixed["findings"][field] = []
        for field, value in findings.items():
            if isinstance(value, str):
                fixable.append(f"findings.{field} is a string; wrapped in a list")
                fixed["findings"][field] = [value]
            elif isinstance(value, list) and any(not isinstance(item, str) for item in value):
                if not detail_ok:
                    manual.append(f"findings.{field} has structured items but findings_detail is invalid")
                    continue
                fixable.append(f"findings.{field} has structured items; kept in findings_detail.{field} and listed as text")
                fixed.setdefault("findings_detail", {})[field] = value
                fixed["findings"][field] = [_item_text(item) for item in value]
            elif value is None:
                fixable.append(f"findings.{field} is null; replaced with an empty list")
                fixed["findings"][field] = []
            elif not isinstance(value, list):
                manual.append(f"findings.{field} must be a list of strings (not auto-fixable)")
    elif "findings" in data:
        manual.append("findings must be an object of lists (not auto-fixable)")
    refs = data.get("evidence_refs")
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, str) or not SUPPORTED_REF.match(ref):
                shown = ref if isinstance(ref, str) else json.dumps(ref, sort_keys=True)[:80]
                manual.append(f"evidence ref {shown!r} is not file.json#/pointer, file#L1-L9 or a collector id (not auto-fixable)")
    elif "evidence_refs" in data:
        manual.append("evidence_refs must be a list")
    observed = data.get("forbidden_actions_observed")
    if isinstance(observed, str):
        fixable.append("forbidden_actions_observed is a string; wrapped in a list")
        fixed["forbidden_actions_observed"] = [observed]
        observed = [observed]
    if isinstance(observed, list):
        if any(not isinstance(item, str) for item in observed):
            manual.append("forbidden_actions_observed must be a list of strings")
        elif FORBIDDEN_PHRASE not in "\n".join(observed).lower():
            manual.append(f"forbidden_actions_observed must state {FORBIDDEN_PHRASE!r} literally")
    elif "forbidden_actions_observed" in data:
        manual.append("forbidden_actions_observed must be a list of strings")
    if data.get("status") == "pass" and not refs:
        manual.append("a pass lane needs evidence_refs")
    return fixable, manual, fixed


def lane_files(root: Path) -> list[tuple[Path, Path]]:
    """(lane file, repo root) for every sidecar-declared lane in the campaign."""
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    out: list[tuple[Path, Path]] = []
    for repo in manifest.get("repositories", []):
        repo_root = (root / "repos" / repo["repo_slug"]).resolve()
        sidecar_path = repo_root / "repo-scout-sidecar.json"
        if not sidecar_path.is_file():
            continue
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        for lane in sidecar.get("lanes", []):
            artifact = (root / lane.get("artifact", "")).resolve()
            if artifact.is_relative_to(root) and artifact.is_file():
                out.append((artifact, repo_root))
    return out


def lint_campaign(root: Path, *, fix_format: bool = False) -> dict:
    root = root.resolve()
    report: dict = {"campaign_root": str(root), "lanes": [], "fixed": 0, "manual_faults": 0}
    for artifact, _repo_root in lane_files(root):
        rel = str(artifact.relative_to(root))
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report["lanes"].append({"lane": rel, "fixable": [], "manual": [f"invalid JSON: {exc}"]})
            report["manual_faults"] += 1
            continue
        fixable, manual, fixed = lint_lane(data)
        row = {"lane": rel, "fixable": fixable, "manual": manual}
        if fix_format and fixable:
            backup = root / "receipts" / "pre-lint" / rel.replace("/", "__")
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(artifact, backup)
            tmp = artifact.with_name(artifact.name + ".tmp")
            tmp.write_text(json.dumps(fixed, indent=2) + "\n", encoding="utf-8")
            tmp.replace(artifact)
            row["original_saved"] = str(backup.relative_to(root))
            report["fixed"] += 1
        report["manual_faults"] += len(manual)
        report["lanes"].append(row)
    if fix_format and report["fixed"]:
        log = root / "receipts" / "pre-lint" / "lint-log.jsonl"
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "fixed": [r["lane"] for r in report["lanes"] if r.get("original_saved")]}) + "\n")
    return report


def render(report: dict) -> str:
    lines: list[str] = []
    for row in report["lanes"]:
        if not row["fixable"] and not row["manual"]:
            lines.append(f"clean  {row['lane']}")
            continue
        for fault in row["fixable"]:
            done = " (fixed)" if row.get("original_saved") else " (fix with --fix-format)"
            lines.append(f"format {row['lane']}: {fault}{done}")
        for fault in row["manual"]:
            lines.append(f"manual {row['lane']}: {fault}")
    lines.append(f"lanes={len(report['lanes'])} fixed={report['fixed']} manual_faults={report['manual_faults']}")
    return "\n".join(lines)


def snapshot_sidecar(root: Path, repo_slug: str) -> dict:
    """Preserve a sidecar's bytes before it changes and repoint ledger rows to the copy.

    Automates pitfall 16: workers may have hashed the live sidecar as evidence.
    """
    root = root.resolve()
    repo_root = root / "repos" / repo_slug
    sidecar = repo_root / "repo-scout-sidecar.json"
    data = sidecar.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    snap = repo_root / "evidence" / "sidecar-snapshots" / f"{digest}.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        snap.write_bytes(data)
    live_rel = str(sidecar.relative_to(root))
    snap_rel = str(snap.relative_to(root))
    repointed = 0
    ledger_path = root / "citation-ledger.json"
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        for src in ledger.get("sources", []):
            for holder in [src, *(src.get("quotes") or [])]:
                if isinstance(holder, dict) and holder.get("path") == live_rel and holder.get("sha256") in (None, digest):
                    holder["path"] = snap_rel
                    holder["sha256"] = digest
                    repointed += 1
        if repointed:
            tmp = ledger_path.with_name(ledger_path.name + ".tmp")
            tmp.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
            tmp.replace(ledger_path)
    return {"snapshot": snap_rel, "sha256": digest, "ledger_rows_repointed": repointed}

#!/usr/bin/env python3
"""Initialize an app-compatible repo-scout.campaign.v1 scaffold.

Stdlib-only, deterministic with --campaign-id/--created-at, and no-exec: it
creates the same top-level manifest contract used by the repo-scout application
plus progressive-disclosure worker/lane starter files. It never clones, installs,
or executes target repository code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from string import Template
from urllib.parse import urlparse

CAMPAIGN_SCHEMA = "repo-scout.campaign.v1"
WORKER_PACKET_SCHEMA = "repo-scout.worker-packet.v1"
LANE_OUTPUT_SCHEMA = "repo-scout.lane-output.v1"
CITATION_LEDGER_SCHEMA = "repo-scout.citation-ledger.v1"
ALLOWED_MODES = {"brief", "standard", "full", "refresh_delta", "true_deep_research"}
ALLOWED_REPO_STATUS = {"initialized", "collector_done", "lanes_done", "synthesized", "verified", "blocked", "failed"}
DEFAULT_LANES = {
    "brief": ["controller"],
    "standard": ["provenance_activity", "static_architecture", "risk_license", "fit_verdict"],
    "full": [
        "provenance_activity",
        "static_architecture",
        "risk_license_supply_chain",
        "fit_overlap",
        "external_adoption_failure",
        "adversarial_refutation",
    ],
    "refresh_delta": ["provenance_delta", "changed_evidence", "decision_delta"],
    "true_deep_research": ["deep_research_handoff"],
}
IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now() -> str:
    return fmt_ts(utc_dt())


def sanitize_identifier(value: str, *, label: str, max_len: int = 128) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        raise SystemExit(f"{label} must not be empty")
    if any(sep in raw for sep in ("/", "\\")):
        raise SystemExit(f"{label} must not contain path separators: {value!r}")
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._-")
    cleaned = re.sub(r"[._-]{2,}", "_", cleaned)[:max_len].strip("._-")
    if cleaned in {"", ".", ".."} or not IDENT_RE.fullmatch(cleaned):
        raise SystemExit(f"{label} is unsafe after sanitization: {value!r} -> {cleaned!r}")
    return cleaned


def repo_slug(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    raw = "__".join(parts[-2:]) if len(parts) >= 2 else (re.sub(r"^https?://", "", url).strip("/") or "repo")
    return sanitize_identifier(raw.replace("__", "-"), label="repo slug", max_len=96).replace("-", "_")


def stable_campaign_id(repos: list[str], mode: str) -> str:
    return "campaign_" + hashlib.sha1((mode + "|" + "|".join(sorted(repos))).encode()).hexdigest()[:12]


def stable_run_id(campaign_id: str, repo_url: str) -> str:
    return "repo_run_" + hashlib.sha1((campaign_id + "|" + repo_url).encode()).hexdigest()[:12]


def resolve_path(path: Path) -> Path:
    # strict=False keeps new paths resolvable while still collapsing '..' and symlinks that exist.
    return path.expanduser().resolve(strict=False)


def ensure_within(child: Path, parent: Path, *, label: str) -> Path:
    resolved_child = resolve_path(child)
    resolved_parent = resolve_path(parent)
    try:
        resolved_child.relative_to(resolved_parent)
    except ValueError as exc:
        raise SystemExit(f"{label} escapes campaign root: {resolved_child} not under {resolved_parent}") from exc
    return resolved_child


def relative_to_root(child: Path, root: Path, *, label: str) -> str:
    resolved_child = ensure_within(child, root, label=label)
    return str(resolved_child.relative_to(resolve_path(root)))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_template(base: Path, name: str) -> Template:
    return Template((base / "templates" / name).read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid_is_alive(pid: int | str | None) -> bool:
    try:
        value = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_existing_lock(root: Path) -> dict:
    lock_path = root / "lock.json"
    manifest_path = root / "manifest.json"
    for path in (lock_path, manifest_path):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if path == lock_path else payload.get("lock", {}) or {}
        except Exception:  # noqa: BLE001
            return {"unreadable": str(path)}
    return {}


def classify_lock(lock: dict, now: datetime) -> str:
    if not lock:
        return "missing"
    if lock.get("unreadable"):
        return "unreadable"
    expires_at = lock.get("expires_at")
    expired = False
    if expires_at:
        try:
            expired = parse_ts(expires_at) <= now
        except Exception:  # noqa: BLE001
            return "unreadable"
    elif lock.get("created_at"):
        try:
            expired = parse_ts(lock["created_at"]) + timedelta(hours=4) <= now
        except Exception:  # noqa: BLE001
            return "unreadable"
    alive = pid_is_alive(lock.get("pid"))
    if expired:
        return "expired"
    if not alive:
        return "stale"
    return "active"


def check_existing_root(root: Path, *, resume: bool, force: bool, now: datetime) -> tuple[bool, dict | None]:
    """Return (resume_existing, takeover_details)."""
    manifest_path = root / "manifest.json"
    if not root.exists():
        return False, None
    if resume and manifest_path.exists():
        lock = read_existing_lock(root)
        lock_state = classify_lock(lock, now)
        if lock_state == "active" and int(lock.get("pid") or 0) != os.getpid() and not force:
            raise SystemExit(
                f"campaign root has an active lock owned by {lock.get('owner')} pid={lock.get('pid')} "
                f"until {lock.get('expires_at', 'unknown')}; retry after it expires or use --force after review"
            )
        if lock_state == "unreadable" and not force:
            raise SystemExit("campaign root has an unreadable lock/manifest; use --force only after manual review")
        return True, None
    if not any(root.iterdir()):
        return False, None
    lock = read_existing_lock(root)
    lock_state = classify_lock(lock, now)
    if lock_state == "active" and not force:
        raise SystemExit(
            f"campaign root has an active lock owned by {lock.get('owner')} pid={lock.get('pid')} "
            f"until {lock.get('expires_at', 'unknown')}; use --resume or --force to take over"
        )
    if lock_state == "unreadable" and not force:
        raise SystemExit("campaign root has an unreadable lock/manifest; use --force only after manual review")
    if manifest_path.exists() and force:
        return False, {"previous_lock_state": lock_state, "previous_lock": lock, "forced": True}
    if lock_state in {"expired", "stale", "missing"}:
        return False, {"previous_lock_state": lock_state, "previous_lock": lock, "forced": False}
    if not force:
        raise SystemExit(f"campaign root exists and is not empty: {root} (use --resume or --force)")
    return False, {"previous_lock_state": lock_state, "previous_lock": lock, "forced": True}


def lane_output(campaign_id: str, repo_url: str, slug: str, mode: str, lane: str, output_path: Path) -> dict:
    return {
        "schema_version": LANE_OUTPUT_SCHEMA,
        "lane_id": lane,
        "status": "hold",
        "output_path": str(output_path),
        "sources_used": [repo_url],
        "source_classes": ["external_source_evidence", "fit_routing"],
        "forbidden_actions_observed": ["no target repo code execution; scaffold only"],
        "evidence_refs": [],
        "gaps": ["lane not yet executed; controller must replace scaffold before final synthesis"],
        "findings": {
            "fact": [],
            "upstream_claim": [],
            "independent_evidence": [],
            "inference": [],
            "unknown": [f"{lane} pending"],
        },
        "repo_scout_skill_context": {"campaign_id": campaign_id, "repo_slug": slug, "mode": mode},
    }


def build_campaign(args: argparse.Namespace) -> Path:
    if args.mode not in ALLOWED_MODES:
        raise SystemExit(f"unsupported mode: {args.mode}")
    repos = args.repo or []
    if not repos:
        raise SystemExit("at least one --repo is required")
    if args.concurrency > 3:
        raise SystemExit("concurrency must be <= 3")
    if args.concurrency < 1:
        raise SystemExit("concurrency must be >= 1")
    skill_root = Path(__file__).resolve().parents[1]
    campaign_id = sanitize_identifier(args.campaign_id or stable_campaign_id(repos, args.mode), label="campaign id")
    base_root = resolve_path(Path(args.root).expanduser()) if args.root else resolve_path(Path.cwd() / campaign_id)
    root = base_root
    manifest_path = root / "manifest.json"
    created_at = args.created_at or utc_now()
    created_dt = parse_ts(created_at)
    should_resume, takeover = check_existing_root(root, resume=args.resume, force=args.force, now=created_dt)
    if should_resume:
        print(str(root))
        return root
    root.mkdir(parents=True, exist_ok=True)
    ensure_within(manifest_path, root, label="manifest path")
    lock_expires_at = fmt_ts(created_dt + timedelta(minutes=args.lock_ttl_minutes))
    next_review_at = fmt_ts(created_dt + timedelta(days=args.review_days))
    lane_names = [sanitize_identifier(x.strip(), label="lane name") for x in (args.lanes.split(",") if args.lanes else DEFAULT_LANES[args.mode]) if x.strip()]
    if len(lane_names) != len(set(lane_names)):
        raise SystemExit("duplicate lane names after sanitization")
    worker_template = load_template(skill_root, "worker-packet.md")
    generated_files: list[Path] = []

    ledger = root / "citation-ledger.json"
    atomic_write(ledger, json.dumps({"schema_version": CITATION_LEDGER_SCHEMA, "sources": []}, indent=2, sort_keys=True) + "\n")
    generated_files.append(ledger)

    repositories = []
    seen_slugs: set[str] = set()
    for repo in repos:
        slug = repo_slug(repo)
        if slug in seen_slugs:
            raise SystemExit(f"duplicate repo slug after sanitization: {slug}")
        seen_slugs.add(slug)
        repo_root = ensure_within(root / "repos" / slug, root, label=f"repo root {slug}")
        for sub in ["collector", "lanes", "receipts", "synthesis"]:
            ensure_within(repo_root / sub, root, label=f"repo subdir {slug}/{sub}").mkdir(parents=True, exist_ok=True)
        run_id = stable_run_id(campaign_id, repo)
        lane_entries = []
        for lane in lane_names:
            artifact = ensure_within(repo_root / "lanes" / f"{lane}.json", root, label=f"lane artifact {slug}/{lane}")
            if not artifact.exists() or args.force:
                atomic_write(artifact, json.dumps(lane_output(campaign_id, repo, slug, args.mode, lane, artifact), indent=2, sort_keys=True) + "\n")
            generated_files.append(artifact)
            packet = ensure_within(repo_root / "receipts" / f"worker-packet-{lane}.md", root, label=f"worker packet {slug}/{lane}")
            if not packet.exists() or args.force:
                atomic_write(packet, worker_template.safe_substitute(
                    campaign_id=campaign_id,
                    repo_url=repo,
                    repo_slug=slug,
                    mode=args.mode,
                    lane_name=lane,
                    campaign_root=str(root),
                    repo_root=str(repo_root),
                    artifact_path=str(artifact),
                    citation_ledger=str(ledger),
                ))
            generated_files.append(packet)
            lane_entries.append({
                "name": lane,
                "artifact": relative_to_root(artifact, root, label=f"lane artifact {slug}/{lane}"),
                "worker_packet": relative_to_root(packet, root, label=f"worker packet {slug}/{lane}"),
                "status": "hold",
            })
        sidecar_path = ensure_within(repo_root / "repo-scout-sidecar.json", root, label=f"sidecar {slug}")
        repo_manifest = {
            "schema_version": "repo-scout.repository-sidecar.v1",
            "campaign_id": campaign_id,
            "repo_url": repo,
            "repo_slug": slug,
            "mode": args.mode,
            "lanes": lane_entries,
            "baseline": {
                "collector_command": f"repo-scout scout {repo} --workspace {repo_root / 'collector'} --out {repo_root / 'collector' / 'report'} --format both",
                "runner": "no-exec",
            },
            "decision": None,
            "consumption_mode": None,
            "build_gate": "describe_only",
            "collector_verdict": None,
        }
        atomic_write(sidecar_path, json.dumps(repo_manifest, indent=2, sort_keys=True) + "\n")
        generated_files.append(sidecar_path)
        repositories.append({
            "repo_url": repo,
            "repo_slug": slug,
            "run_id": run_id,
            "root": str(repo_root),
            "status": "initialized",
            "report_path": None,
            "validated": False,
            "error": None,
            "freshness": {"inspected_at": None, "inspected_commit": None, "prior_campaign": args.prior_campaign},
            "retention": {"snapshot_retained": False, "cleanup_status": "not_started"},
        })
    checkpoints = [{"name": "campaign_initialized", "created_at": created_at, "details": {"repo_count": len(repositories), "mode": args.mode, "skill_initializer": True}}]
    if takeover:
        checkpoints.append({"name": "lock_taken_over", "created_at": created_at, "details": takeover})
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "owner_issue": args.owner_issue,
        "mode": args.mode,
        "status": "initialized",
        "created_at": created_at,
        "updated_at": created_at,
        "root": str(root),
        "scout_controller": args.controller,
        "candidate_owner": args.candidate_owner,
        "verifier": args.verifier,
        "next_review_at": next_review_at,
        "routing_surface": "repo-scout application",
        "owner_gate": "none",
        "max_concurrency": args.concurrency,
        "partial_success_allowed": True,
        "lock": {"owner": args.controller, "pid": os.getpid(), "created_at": created_at, "expires_at": lock_expires_at},
        "baseline": {
            "doctor_command": "repo-scout doctor",
            "collector_command_shape": "repo-scout scout <url> --workspace <repo-root>/collector --out <repo-root>/collector/report --format both",
            "runner": "no-exec",
        },
        "canonical_fields": ["decision", "consumption_mode", "build_gate", "collector_verdict"],
        # New campaigns: every lane evidence ref must resolve (see verifier).
        "evidence_ref_policy": "resolvable-v1",
        "repositories": repositories,
        "checkpoints": checkpoints,
        "hashes": {relative_to_root(path, root, label="hash path"): {"sha256": file_sha256(path)} for path in sorted(set(generated_files))},
    }
    atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    atomic_write(root / "lock.json", json.dumps(manifest["lock"], indent=2, sort_keys=True) + "\n")
    return root


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "campaign"
        args = argparse.Namespace(repo=["https://github.com/Example/Project.git", "https://github.com/acme/second"], mode="standard", root=str(root), campaign_id="test-campaign", created_at="2026-01-01T00:00:00Z", owner_issue="none", controller="controller", verifier="qa", candidate_owner="owner", mode_reason="self-test", concurrency=3, lanes=None, review_days=30, prior_campaign=None, resume=False, force=False, lock_ttl_minutes=240)
        out = build_campaign(args)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["schema_version"] == CAMPAIGN_SCHEMA
        assert manifest["campaign_id"] == "test-campaign"
        assert manifest["lock"]["expires_at"] == "2026-01-01T04:00:00Z"
        assert manifest["mode"] == "standard"
        assert manifest["max_concurrency"] == 3
        assert len(manifest["repositories"]) == 2
        assert manifest["hashes"]
        for repo in manifest["repositories"]:
            Path(repo["root"]).resolve().relative_to(out.resolve())
            sidecar = Path(repo["root"]) / "repo-scout-sidecar.json"
            sc = json.loads(sidecar.read_text())
            assert len(sc["lanes"]) == 4
            assert sc["baseline"]["runner"] == "no-exec"
            packet = Path(repo["root"]) / "receipts" / "worker-packet-fit_verdict.md"
            text = packet.read_text()
            assert "{{" not in text and "test-campaign" in text
        try:
            bad = argparse.Namespace(**{**vars(args), "campaign_id": "../escape", "root": str(Path(td) / "bad")})
            build_campaign(bad)
        except SystemExit:
            pass
        else:
            raise AssertionError("unsafe campaign_id was not rejected")
        try:
            colliding = argparse.Namespace(**{**vars(args), "repo": ["https://github.com/a/b", "https://github.com/a--b"], "campaign_id": "collision", "root": str(Path(td) / "collision")})
            build_campaign(colliding)
        except SystemExit:
            pass
        else:
            raise AssertionError("colliding sanitized repo slugs were not rejected")
        locked_root = Path(td) / "locked"
        active = argparse.Namespace(**{**vars(args), "campaign_id": "active", "root": str(locked_root)})
        build_campaign(active)
        try:
            build_campaign(active)
        except SystemExit as exc:
            assert "active lock" in str(exc)
        else:
            raise AssertionError("active lock was not protected")
        forced = argparse.Namespace(**{**vars(active), "force": True})
        build_campaign(forced)
        manifest2 = json.loads((locked_root / "manifest.json").read_text())
        assert any(cp["name"] == "lock_taken_over" for cp in manifest2["checkpoints"])
    print("init_repo_scout_campaign self-test: PASS")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize an app-compatible no-exec repo-scout campaign scaffold")
    p.add_argument("--repo", action="append", help="GitHub repository URL; repeat for multi-repo campaigns")
    p.add_argument("--mode", default="standard", choices=sorted(ALLOWED_MODES))
    p.add_argument("--root", help="Campaign root; default ./<campaign-id>")
    p.add_argument("--campaign-id")
    p.add_argument("--created-at", help="ISO timestamp for deterministic tests")
    p.add_argument("--owner-issue", default="none")
    p.add_argument("--controller", default="repo-scout-controller")
    p.add_argument("--verifier", default="independent-qa")
    p.add_argument("--candidate-owner", default="owner")
    p.add_argument("--mode-reason")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--lanes", help="Comma-separated lane override")
    p.add_argument("--review-days", type=int, default=30)
    p.add_argument("--prior-campaign")
    p.add_argument("--lock-ttl-minutes", type=int, default=240, help="Active lock lifetime before stale takeover is allowed")
    p.add_argument("--resume", action="store_true", help="Return existing manifest unchanged if it already exists")
    p.add_argument("--force", action="store_true", help="Overwrite generated files and take over active locks")
    p.add_argument("--self-test", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    root = build_campaign(args)
    print(str(root))


if __name__ == "__main__":
    main()

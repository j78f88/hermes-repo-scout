#!/usr/bin/env python3
"""Start and inspect a managed Repo Scout campaign.

This stdlib-only skill helper resolves the repo-scout CLI, records its doctor
output, creates the progressive-disclosure campaign scaffold, runs the
hard-wired no-exec collector for every target, and checkpoints the manifest.
It never runs target repository code or performs downstream publication.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
INIT_SCRIPT = SCRIPTS_DIR / "init_repo_scout_campaign.py"
VERIFY_SCRIPT = SCRIPTS_DIR / "verify_repo_scout_campaign.py"
SKILL_SCAN_SCRIPT = SCRIPTS_DIR / "scan_skill_repo.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_cli(explicit: str | None, *, environ: Mapping[str, str] | None = None) -> Path:
    """Resolve an executable repo-scout without assuming it is on PATH."""
    env = dict(os.environ if environ is None else environ)
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if env.get("REPO_SCOUT_CLI"):
        candidates.append(Path(env["REPO_SCOUT_CLI"]).expanduser())
    on_path = shutil.which("repo-scout", path=env.get("PATH"))
    if on_path:
        candidates.append(Path(on_path))
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    candidates.extend(
        [
                home / ".local" / "bin" / "repo-scout",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    checked = ", ".join(str(path) for path in candidates) or "PATH and default locations"
    raise SystemExit(
        "repo-scout CLI not found. Install the application or pass --cli/REPO_SCOUT_CLI. "
        f"Checked: {checked}"
    )


def _child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Child process env without inherited Python import paths (skill pitfall 13).

    Hermes can export PYTHONPATH entries that break the collector's own venv.
    """
    env = dict(environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _run(command: list[str], *, environ: Mapping[str, str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, env=_child_env(environ), check=False, timeout=timeout)


def _load_script_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load skill helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _slug_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower())
    return "-".join(part for part in cleaned.split("-") if part)


def default_run_name(repos: list[str], now: datetime | None = None) -> str:
    """Storage-rule run folder name, e.g. repo-scout-syncthing-syncthing-20261028T054928Z."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    first = repos[0].rstrip("/").removesuffix(".git").split("/")[-2:]
    label = _slug_part("-".join(first)) or "repo"
    if len(repos) > 1:
        label += f"-plus{len(repos) - 1}"
    return f"repo-scout-{label}-{stamp}"


def _runs_base(environ: Mapping[str, str]) -> Path:
    from scout_paths import runs_root

    return runs_root(environ).resolve()


def _default_root(repos: list[str], mode: str, campaign_id: str | None, environ: Mapping[str, str], now: datetime | None = None) -> Path:
    """New campaigns go to runs/YYYY-MM/<run-name>/ under the storage rules.

    Order: REPO_SCOUT_RUNS_ROOT (or legacy REPO_SCOUT_CAMPAIGN_HOME), then
    ~/.hermes/repo-scout-runs. The
    campaign id inside the manifest stays the stable hash-based id.
    """
    moment = now or datetime.now(timezone.utc)
    return _runs_base(environ) / moment.strftime("%Y-%m") / default_run_name(repos, moment)


def _report_path(repo_root: Path) -> Path:
    return repo_root / "collector" / "report" / "report.json"


def _report_canonical(report: dict) -> tuple[str | None, str | None, str | None, str | None]:
    raw_canonical = report.get("canonical_decision")
    canonical: dict = raw_canonical if isinstance(raw_canonical, dict) else {}
    raw_verdict = report.get("verdict")
    verdict: dict = raw_verdict if isinstance(raw_verdict, dict) else {}
    return (
        canonical.get("decision"),
        canonical.get("consumption_mode"),
        canonical.get("build_gate"),
        verdict.get("decision") or report.get("collector_verdict"),
    )


def _refresh_declared_hashes(root: Path, manifest: dict) -> None:
    hashes = manifest.setdefault("hashes", {})
    for rel in list(hashes):
        path = (root / rel).resolve(strict=False)
        try:
            path.relative_to(root.resolve())
        except ValueError:
            del hashes[rel]
            continue
        if path.is_file():
            hashes[rel] = {"sha256": _sha256(path)}
        else:
            del hashes[rel]
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.name in {"manifest.json", "lock.json"} or path.name.endswith(".tmp"):
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        rel = str(path.relative_to(root))
        hashes[rel] = {"sha256": _sha256(path)}


def start(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    if args.resume and not args.root:
        raise SystemExit("--resume needs --root: default roots are timestamped, so pass the existing campaign root")
    cli = resolve_cli(args.cli, environ=environ)
    doctor = _run([str(cli), "doctor"], environ=environ, timeout=60)
    if doctor.returncode != 0:
        sys.stderr.write(doctor.stderr or doctor.stdout)
        raise SystemExit(f"repo-scout doctor failed with exit code {doctor.returncode}")
    if "default_runner no-exec" not in doctor.stdout or "target_code_execution disabled" not in doctor.stdout:
        raise SystemExit("repo-scout doctor did not confirm the required no-exec safety contract")

    root = Path(args.root).expanduser().resolve() if args.root else _default_root(args.repo, args.mode, args.campaign_id, environ)
    init_command = [
        sys.executable,
        str(INIT_SCRIPT),
        *sum((["--repo", repo] for repo in args.repo), []),
        "--mode", args.mode,
        "--root", str(root),
        "--controller", args.controller,
        "--verifier", args.verifier,
        "--candidate-owner", args.candidate_owner,
        "--owner-issue", args.owner_issue,
        "--concurrency", str(args.concurrency),
    ]
    if args.campaign_id:
        init_command.extend(["--campaign-id", args.campaign_id])
    if args.created_at:
        init_command.extend(["--created-at", args.created_at])
    if args.prior_campaign:
        init_command.extend(["--prior-campaign", args.prior_campaign])
    if args.resume:
        init_command.append("--resume")
    if args.force:
        init_command.append("--force")
    initialized = _run(init_command, environ=environ, timeout=60)
    if initialized.returncode != 0:
        sys.stderr.write(initialized.stderr or initialized.stdout)
        return initialized.returncode

    initialized_lines = initialized.stdout.strip().splitlines()
    if not initialized_lines:
        raise SystemExit("campaign initializer returned no campaign root")
    root = Path(initialized_lines[-1]).resolve()
    receipts = root / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / "doctor.txt").write_text(doctor.stdout, encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_repos = [repo.get("repo_url") for repo in manifest.get("repositories", [])]
    if manifest_repos != args.repo:
        raise SystemExit(
            "existing campaign does not match requested repositories: "
            f"manifest={manifest_repos!r} requested={args.repo!r}"
        )
    if manifest.get("mode") != args.mode:
        raise SystemExit(
            f"existing campaign mode {manifest.get('mode')!r} does not match requested mode {args.mode!r}"
        )
    succeeded = 0
    failed = 0
    verifier = _load_script_module(VERIFY_SCRIPT, "repo_scout_skill_start_verify")

    for repo in manifest["repositories"]:
        repo_root = Path(repo["root"]).resolve()
        try:
            repo_root.relative_to(root)
        except ValueError as exc:
            raise SystemExit(f"repository root escapes campaign root: {repo_root} not under {root}") from exc
        if not repo_root.is_dir():
            raise SystemExit(f"repository root is missing: {repo_root}")
        output = repo_root / "collector" / "report"
        command = [
            str(cli), "scout", repo["repo_url"],
            "--workspace", str(repo_root / "collector"),
            "--out", str(output),
            "--format", "both",
        ]
        try:
            result = _run(command, environ=environ, timeout=args.collector_timeout)
        except subprocess.TimeoutExpired:
            failed += 1
            repo["status"] = "failed"
            repo["error"] = f"collector timed out after {args.collector_timeout}s"
            continue
        receipt = repo_root / "receipts" / "collector.txt"
        receipt.write_text(
            "$ " + shlex.join(command) + "\nARGV_JSON=" + json.dumps(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr + f"\nEXIT_CODE={result.returncode}\n",
            encoding="utf-8",
        )
        sidecar_path = repo_root / "repo-scout-sidecar.json"
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if not isinstance(sidecar, dict) or not isinstance(sidecar.get("baseline"), dict):
                raise ValueError("sidecar baseline object is missing")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            repo["status"] = "failed"
            repo["error"] = f"invalid repo-scout sidecar: {exc}"
            continue
        sidecar["baseline"]["collector_command"] = shlex.join(command)
        if result.returncode != 0:
            failed += 1
            repo["status"] = "failed"
            repo["error"] = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()[:2000]
            sidecar["collector_verdict"] = "failed"
            _atomic_json(sidecar_path, sidecar)
            continue
        report_path = _report_path(repo_root)
        if not report_path.exists():
            failed += 1
            repo["status"] = "failed"
            repo["error"] = f"collector exited 0 but report is missing: {report_path}"
            _atomic_json(sidecar_path, sidecar)
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("report JSON must be a JSON object")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            repo["status"] = "failed"
            repo["error"] = f"invalid collector report JSON: {exc}"
            _atomic_json(sidecar_path, sidecar)
            continue
        safe_report, safety_reason = verifier.validate_report_payload(report)
        if not safe_report:
            failed += 1
            repo["status"] = "failed"
            repo["error"] = f"unsafe collector report: {safety_reason}"
            _atomic_json(sidecar_path, sidecar)
            continue
        decision, consumption_mode, build_gate, collector_verdict = _report_canonical(report)
        sidecar.update(
            {
                "decision": decision,
                "consumption_mode": consumption_mode,
                "build_gate": build_gate or "describe_only",
                "collector_verdict": collector_verdict,
            }
        )
        _atomic_json(sidecar_path, sidecar)
        repo["status"] = "collector_done"
        repo["report_path"] = str(report_path)
        repo["validated"] = False
        repo["error"] = None
        repo["freshness"] = {
            "inspected_at": report.get("created_at") or _utc_now(),
            "inspected_commit": (report.get("repo") or {}).get("commit") or (report.get("repo") or {}).get("ref") or "unknown",
            "prior_campaign": repo.get("freshness", {}).get("prior_campaign"),
        }
        repo["retention"] = {
            "snapshot_retained": False,
            "cleanup_status": "transient_snapshot_removed",
        }
        succeeded += 1

    manifest["updated_at"] = _utc_now()
    manifest["status"] = "partial" if failed and succeeded else ("failed" if failed else "running")
    manifest.setdefault("checkpoints", []).append(
        {
            "name": "baseline_collection_completed",
            "created_at": manifest["updated_at"],
            "details": {
                "succeeded": succeeded,
                "failed": failed,
                "runner": "no-exec",
                "cli": str(cli),
            },
        }
    )
    _refresh_declared_hashes(root, manifest)
    _atomic_json(manifest_path, manifest)
    print(f"campaign_root: {root}")
    print(f"manifest: {manifest_path}")
    print(f"baseline: succeeded={succeeded} failed={failed}")
    print("next: complete the declared evidence lanes and controller synthesis, then run finalize")
    print("safety: no target repo code/install/build/test/hook/container command executed")
    return 0 if succeeded else 1


def verify(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    root = Path(args.campaign_root).expanduser().resolve()
    result = _run([sys.executable, str(VERIFY_SCRIPT), str(root)], environ=environ, timeout=120)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def finalize(args: argparse.Namespace) -> int:
    root = Path(args.campaign_root).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verifier = _load_script_module(VERIFY_SCRIPT, "repo_scout_skill_finalize_verify")
    errors: list[str] = []
    mode = manifest.get("mode")
    synthesis = root / "synthesis"
    primary = synthesis / ("brief.md" if mode == "brief" else "full-pack.md")
    action_register = synthesis / "action-register.json"
    if not primary.is_file():
        errors.append(f"missing required synthesis artifact: {primary}")
    if not action_register.is_file():
        errors.append(f"missing required synthesis artifact: {action_register}")
    else:
        try:
            if not isinstance(json.loads(action_register.read_text(encoding="utf-8")), list):
                errors.append("synthesis/action-register.json must contain a JSON list")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid synthesis/action-register.json: {exc}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    try:
        import lane_lint

        lint_report = lane_lint.lint_campaign(root)
        for row in lint_report["lanes"]:
            for fault in row["fixable"] + row["manual"]:
                print(f"WARN: lint {row['lane']}: {fault}")
    except Exception as exc:  # noqa: BLE001 - lint is advisory here
        print(f"WARN: lint could not run: {exc}")

    failed_repos = 0
    for repo in manifest.get("repositories", []):
        if repo.get("status") == "failed":
            failed_repos += 1
            continue
        repo["status"] = "verified"
        repo["validated"] = True
    manifest["status"] = "partial" if failed_repos else "verified"
    manifest["updated_at"] = _utc_now()
    manifest.setdefault("checkpoints", []).append(
        {"name": "controller_synthesis_done", "created_at": manifest["updated_at"], "details": {"mode": mode}}
    )
    verification_path = synthesis / "verification.txt"
    verification_path.write_text("strict verification pending\n", encoding="utf-8")
    _refresh_declared_hashes(root, manifest)
    _atomic_json(manifest_path, manifest)
    status_value, verify_errors, warnings = verifier.verify_campaign(root)
    if status_value != "pass":
        manifest["status"] = "failed"
        for repo in manifest.get("repositories", []):
            if repo.get("status") == "verified":
                repo["status"] = "synthesized"
                repo["validated"] = False
        verification_path.write_text(
            "repo-scout campaign verification: FAIL\n" + "\n".join(verify_errors) + "\n",
            encoding="utf-8",
        )
        _refresh_declared_hashes(root, manifest)
        _atomic_json(manifest_path, manifest)
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in verify_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    verification_path.write_text(
        "repo-scout campaign verification: PASS\n"
        f"campaign_root: {root}\n"
        "safety: no target repo code/install/build/test/hook/container command executed\n",
        encoding="utf-8",
    )
    _refresh_declared_hashes(root, manifest)
    _atomic_json(manifest_path, manifest)
    status_value, verify_errors, _ = verifier.verify_campaign(root)
    if status_value != "pass":
        for error in verify_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("repo-scout campaign verification: PASS")
    print(f"verification: {verification_path}")
    try:
        import scout_index

        if root.is_relative_to(scout_index.runs_root()):
            scout_index.write_index(scout_index.build_index(scout_index.runs_root()), scout_index.index_path())
    except Exception as exc:  # noqa: BLE001 - the index is a rebuildable cache
        print(f"WARN: scout index not refreshed: {exc}")
    return 0


def status(args: argparse.Namespace) -> int:
    root = Path(args.campaign_root).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payload = {
        "campaign_id": manifest.get("campaign_id"),
        "mode": manifest.get("mode"),
        "status": manifest.get("status"),
        "root": str(root),
        "repositories": [
            {
                "repo_url": repo.get("repo_url"),
                "status": repo.get("status"),
                "report_path": repo.get("report_path"),
                "error": repo.get("error"),
            }
            for repo in manifest.get("repositories", [])
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0



def _rehash(root: Path) -> None:
    """Re-record artifact hashes after a helper legitimately adds or repairs files."""
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_declared_hashes(root, manifest)
    manifest["updated_at"] = _utc_now()
    _atomic_json(manifest_path, manifest)


def _manifest_repos(root: Path) -> list[dict]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return [repo for repo in manifest.get("repositories", []) if isinstance(repo, dict)]


def lint_cmd(args: argparse.Namespace) -> int:
    import lane_lint

    root = Path(args.campaign_root).expanduser().resolve()
    report = lane_lint.lint_campaign(root, fix_format=args.fix_format)
    if args.fix_format and report["fixed"]:
        _rehash(root)
    print(lane_lint.render(report))
    return 1 if report["manual_faults"] and args.strict else 0


def snapshot_sidecar_cmd(args: argparse.Namespace) -> int:
    import lane_lint

    root = Path(args.campaign_root).expanduser().resolve()
    slugs = [args.repo_slug] if args.repo_slug else [repo["repo_slug"] for repo in _manifest_repos(root)]
    for slug in slugs:
        print(json.dumps({"repo_slug": slug, **lane_lint.snapshot_sidecar(root, slug)}, sort_keys=True))
    _rehash(root)
    return 0


def _provenance_for(root: Path, repo: dict) -> dict:
    import collect_provenance

    owner, name = collect_provenance.parse_github(repo["repo_url"])
    out = root / "repos" / repo["repo_slug"] / "evidence" / "provenance"
    return collect_provenance.run(owner, name, out, campaign_root=root)


def provenance_cmd(args: argparse.Namespace) -> int:
    root = Path(args.campaign_root).expanduser().resolve()
    failures = 0
    for repo in _manifest_repos(root):
        try:
            summary = _provenance_for(root, repo)
            print(f"{repo['repo_slug']}: provenance saved; cross_checks={[c['check'] for c in summary['cross_checks']]} gaps={len(summary['gaps'])}")
        except Exception as exc:  # noqa: BLE001 - a non-GitHub or offline repo is a recorded gap
            failures += 1
            print(f"{repo['repo_slug']}: provenance unavailable: {exc}")
    _rehash(root)
    return 1 if failures and failures == len(_manifest_repos(root)) else 0


def _hermes_python(environ: Mapping[str, str]) -> Path:
    env_dir = environ.get("HERMES_AGENT_DIR")
    if not env_dir:
        return Path(sys.executable)
    base = Path(env_dir).expanduser()
    return base / "venv" / "bin" / "python"


def _report_lists_skill(repo: dict) -> bool | None:
    """True/False from the collector file inventory; None when the inventory was truncated or missing."""
    path = repo.get("report_path")
    if not path or not Path(path).is_file():
        return None
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    files = (report.get("repo") or {}).get("files") or []
    if any(str(item.get("path", "")).endswith("SKILL.md") for item in files if isinstance(item, dict)):
        return True
    return None if (report.get("repo") or {}).get("truncated") else False


def _skill_scan_for(root: Path, repo: dict, environ: Mapping[str, str]) -> dict:
    scan_root = root / "repos" / repo["repo_slug"] / "evidence" / "skill-scan"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = scan_root / stamp
    n = 1
    while run_dir.exists():
        n += 1
        run_dir = scan_root / f"{stamp}-{n}"
    out = run_dir / "skill-scan.json"  # immutable; this is the cited file
    python = _hermes_python(environ)
    if not python.is_file():
        raise RuntimeError(f"Hermes venv python not found at {python}; set HERMES_AGENT_DIR")
    commit = (repo.get("freshness") or {}).get("inspected_commit")
    command = [str(python), str(SKILL_SCAN_SCRIPT), "--repo", repo["repo_url"], "--out", str(out)]
    if commit and commit != "unknown":
        command.extend(["--expect-commit", commit])
    done = _run(command, environ=environ, timeout=600)
    receipt = root / "repos" / repo["repo_slug"] / "receipts" / "skill-scan.txt"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("$ " + shlex.join(command) + "\n\nSTDOUT\n" + done.stdout + "\nSTDERR\n" + done.stderr[-4000:] + f"\nEXIT_CODE={done.returncode}\n", encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[-500:])
    import collect_provenance

    data = out.read_bytes()
    collect_provenance.register_sources(root / "citation-ledger.json", root, [{
        "name": "skill-scan", "status": "ok", "url": repo["repo_url"], "retrieved_at": _utc_now(),
        "campaign_path": str(out.relative_to(root)), "sha256": hashlib.sha256(data).hexdigest(),
    }])
    # Uncited convenience copy of the latest scan; cite the stamped file instead.
    _atomic_bytes(scan_root / "skill-scan.json", data)
    return json.loads(done.stdout)


def skill_scan_cmd(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    root = Path(args.campaign_root).expanduser().resolve()
    status_code = 0
    for repo in _manifest_repos(root):
        if args.when == "auto" and _report_lists_skill(repo) is False:
            print(f"{repo['repo_slug']}: no SKILL.md in collector inventory; skill scan skipped")
            continue
        try:
            summary = _skill_scan_for(root, repo, environ)
            print(f"{repo['repo_slug']}: skills_found={summary['skills_found']} verdicts={summary['verdicts']} gaps={summary['gaps']}")
        except Exception as exc:  # noqa: BLE001
            status_code = 1
            print(f"{repo['repo_slug']}: skill scan failed: {exc}")
    _rehash(root)
    return status_code


def index_cmd(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    import scout_index

    base = Path(args.runs_root).expanduser() if args.runs_root else scout_index.runs_root(dict(environ))
    rows = scout_index.build_index(base)
    path = scout_index.write_index(rows, Path(args.out).expanduser() if args.out else scout_index.index_path(dict(environ)))
    if args.repo:
        row = scout_index.latest_for(rows, args.repo)
        print(json.dumps(row, indent=2, sort_keys=True) if row else f"no prior scout for {args.repo}")
    else:
        for row in rows:
            commit = (row.get("inspected_commit") or "none")[:12]
            print(f"{row['inspected_at'] or '?':20} {row['verification']:5} {str(row['decision']):6} {commit:12} {row['repo_url']}")
    print(f"index: {path} rows={len(rows)}")
    return 0


def refresh_check_cmd(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    import scout_index

    base = Path(args.runs_root).expanduser() if args.runs_root else scout_index.runs_root(dict(environ))
    rows = scout_index.build_index(base)
    result = scout_index.refresh_check(scout_index.latest_for(rows, args.repo), scout_index.live_facts(args.repo))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def next_steps(root: Path) -> str:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    lines = [f"Campaign: {root}", f"Mode: {manifest.get('mode')}. Status: {manifest.get('status')}."]
    for repo in manifest.get("repositories", []):
        slug = repo["repo_slug"]
        sidecar_path = root / "repos" / slug / "repo-scout-sidecar.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.is_file() else {}
        lanes = sidecar.get("lanes", [])
        pending = []
        for lane in lanes:
            artifact = root / lane.get("artifact", "")
            data = json.loads(artifact.read_text(encoding="utf-8")) if artifact.is_file() else {}
            scaffold = "lane not yet executed" in json.dumps(data.get("gaps", []))
            if lane.get("status") != "pass" or scaffold:
                pending.append((lane, "not started" if scaffold else lane.get("status")))
        names = ", ".join(f"{lane['name']} ({state})" for lane, state in pending) or "none"
        lines.append(f"{slug}: {repo.get('status')}. Lanes not yet passed: {names}")
        for lane, _state in pending:
            lines.append(f"  packet: {root / lane['worker_packet']}")
        evidence = root / "repos" / slug / "evidence"
        for name, rel in (("provenance", "provenance/provenance-summary.json"), ("skill scan", "skill-scan/skill-scan.json")):
            if (evidence / rel).is_file():
                lines.append(f"  {name}: {evidence / rel}")
    lines.append(f"Check lane files: python3 {Path(__file__).resolve()} lint {root}")
    lines.append(f"When lanes and synthesis are done: python3 {Path(__file__).resolve()} finalize {root}")
    return "\n".join(lines)


def scout_cmd(args: argparse.Namespace, *, environ: Mapping[str, str]) -> int:
    """One command: baseline, provenance, skill scan when relevant, lint, next steps."""
    import io
    from contextlib import redirect_stdout

    start_args = argparse.Namespace(
        repo=args.repo, mode=args.mode, root=args.root, campaign_id=args.campaign_id, created_at=None,
        owner_issue=args.owner_issue, controller=args.controller, verifier=args.verifier,
        candidate_owner=args.candidate_owner, concurrency=3, prior_campaign=args.prior_campaign,
        resume=args.resume, force=False, cli=args.cli, collector_timeout=args.collector_timeout,
    )
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        rc = start(start_args, environ=environ)
    start_output = buffer.getvalue()
    sys.stdout.write(start_output)
    root_line = next((line for line in start_output.splitlines() if line.startswith("campaign_root: ")), None)
    if root_line is None:
        return rc or 1
    root = Path(root_line.split(": ", 1)[1]).resolve()
    if rc != 0:
        print(next_steps(root))
        return rc
    if not args.no_provenance:
        provenance_cmd(argparse.Namespace(campaign_root=str(root)))
    if args.skill_scan != "never":
        skill_scan_cmd(argparse.Namespace(campaign_root=str(root), when=args.skill_scan), environ=environ)
    lint_cmd(argparse.Namespace(campaign_root=str(root), fix_format=False, strict=False))
    print(next_steps(root))
    print("safety: no target repo code/install/build/test/hook/container command executed")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the Repo Scout skill's deterministic no-exec campaign baseline")
    sub = p.add_subparsers(dest="command", required=True)
    start_p = sub.add_parser("start", help="Create/resume a campaign and run every baseline collector")
    start_p.add_argument("--repo", action="append", required=True, help="Repository URL; repeat for multi-repo campaigns")
    start_p.add_argument("--mode", choices=["brief", "standard", "full", "refresh_delta"], default="standard")
    start_p.add_argument("--root", help="Exact campaign root; defaults to durable source-scout storage")
    start_p.add_argument("--campaign-id")
    start_p.add_argument("--created-at", help="ISO timestamp for deterministic tests")
    start_p.add_argument("--owner-issue", default="none")
    start_p.add_argument("--controller", default="repo-scout-controller")
    start_p.add_argument("--verifier", default="independent-qa")
    start_p.add_argument("--candidate-owner", default="repo-scout-controller")
    start_p.add_argument("--concurrency", default=3, type=int)
    start_p.add_argument("--prior-campaign")
    start_p.add_argument("--resume", action="store_true")
    start_p.add_argument("--force", action="store_true")
    start_p.add_argument("--cli", help="Explicit repo-scout executable path")
    start_p.add_argument("--collector-timeout", default=600, type=positive_int, help="Seconds allowed per baseline collector")
    verify_p = sub.add_parser("verify", help="Run the strict campaign verifier")
    verify_p.add_argument("campaign_root")
    finalize_p = sub.add_parser("finalize", help="Require synthesis, mark verified, and run strict verification")
    finalize_p.add_argument("campaign_root")
    status_p = sub.add_parser("status", help="Print bounded campaign state as JSON")
    status_p.add_argument("campaign_root")
    status_p.add_argument("--next", action="store_true", help="Print plain next steps instead of JSON")

    scout_p = sub.add_parser("scout", help="One command: start, provenance, skill scan when relevant, lint, next steps")
    scout_p.add_argument("--repo", action="append", required=True)
    scout_p.add_argument("--mode", choices=["brief", "standard", "full", "refresh_delta"], default="standard")
    scout_p.add_argument("--root")
    scout_p.add_argument("--campaign-id")
    scout_p.add_argument("--owner-issue", default="none")
    scout_p.add_argument("--controller", default="repo-scout-controller")
    scout_p.add_argument("--verifier", default="independent-qa")
    scout_p.add_argument("--candidate-owner", default="repo-scout-controller")
    scout_p.add_argument("--prior-campaign")
    scout_p.add_argument("--resume", action="store_true")
    scout_p.add_argument("--cli")
    scout_p.add_argument("--collector-timeout", default=600, type=positive_int)
    scout_p.add_argument("--no-provenance", action="store_true", help="Skip public GitHub/Scorecard/deps.dev metadata")
    scout_p.add_argument("--skill-scan", choices=["auto", "always", "never"], default="auto")

    lint_p = sub.add_parser("lint", help="Check lane files; --fix-format repairs format-only faults and keeps originals")
    lint_p.add_argument("campaign_root")
    lint_p.add_argument("--fix-format", action="store_true")
    lint_p.add_argument("--strict", action="store_true", help="Exit 1 when manual faults remain")

    snap_p = sub.add_parser("snapshot-sidecar", help="Preserve sidecar bytes and repoint ledger rows before editing it")
    snap_p.add_argument("campaign_root")
    snap_p.add_argument("--repo-slug")

    prov_p = sub.add_parser("provenance", help="Save public GitHub, Scorecard and deps.dev evidence for every repo")
    prov_p.add_argument("campaign_root")

    scan_p = sub.add_parser("skill-scan", help="Static Hermes skill checks and library overlap for skill repos")
    scan_p.add_argument("campaign_root")
    scan_p.add_argument("--when", choices=["auto", "always"], default="auto")

    index_p = sub.add_parser("index", help="List past scouts from campaign manifests")
    index_p.add_argument("--runs-root")
    index_p.add_argument("--out")
    index_p.add_argument("--repo", help="Show only the latest scout for this repo")

    refresh_p = sub.add_parser("refresh-check", help="Compare the latest scout of a repo with live public facts")
    refresh_p.add_argument("--repo", required=True)
    refresh_p.add_argument("--runs-root")

    return p


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    args = parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    if args.command == "start":
        return start(args, environ=env)
    if args.command == "verify":
        return verify(args, environ=env)
    if args.command == "finalize":
        return finalize(args)
    if args.command == "scout":
        return scout_cmd(args, environ=env)
    if args.command == "lint":
        return lint_cmd(args)
    if args.command == "snapshot-sidecar":
        return snapshot_sidecar_cmd(args)
    if args.command == "provenance":
        return provenance_cmd(args)
    if args.command == "skill-scan":
        return skill_scan_cmd(args, environ=env)
    if args.command == "index":
        return index_cmd(args, environ=env)
    if args.command == "refresh-check":
        return refresh_check_cmd(args, environ=env)
    if getattr(args, "next", False):
        print(next_steps(Path(args.campaign_root).expanduser().resolve()))
        return 0
    return status(args)


if __name__ == "__main__":
    raise SystemExit(main())

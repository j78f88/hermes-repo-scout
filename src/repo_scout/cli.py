from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
from .artifact_quality import evaluate_markdown_artifacts, load_markdown_fixtures, write_artifact_quality_outputs
from .campaign import CampaignInitializer, CampaignVerifier
from .compare import build_comparison, render_comparison_markdown, write_comparison_outputs
from .correction_feedback import validate_correction_files, write_correction_validation_report
from .handoff import build_review_board_handoff, build_review_board_handoff_from_report_json, write_handoff_packet
from .intake import parse_repo_ref, planned_clone_command
from .rollup_export import build_rollup_export, write_rollup_export
from .planner import compile_plan
from .reporting import build_report, model_to_json, write_outputs
from .repo_fit_validation import validate_fixture_files, write_validation_report
from .run_ledger import DEFAULT_FIXTURE_RUN_ID, write_run_ledger_outputs
from .scanners import scanner_registry
from .trending import collect_github_trending_live, load_github_trending_fixture, write_trending_outputs
from .workspace import default_workspace_path, record_scan


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="repo-scout")
def main() -> None:
    """Static-first repository due diligence CLI."""


def _write_direct_handoff(report, paths: dict[str, Path], handoff: str | None, handoff_out: str | None) -> Path | None:
    if handoff_out and not handoff:
        raise click.ClickException("--handoff-out requires --handoff review-board")
    if not handoff:
        return None
    if not handoff_out:
        raise click.ClickException("--handoff review-board requires --handoff-out PATH")
    if handoff != "review-board":
        raise click.ClickException(f"Unsupported handoff target: {handoff}")
    out_path = Path(handoff_out).expanduser().resolve()
    source_ref = str(paths.get("json") or f"repo-scout-report:{report.run_id}")
    packet = build_review_board_handoff(report, source_ref=source_ref)
    write_handoff_packet(packet, out_path)
    return out_path


def _scan_impl(repo_ref: str, out: str, fmt: str, depth: str, method: str, max_files: int, max_bytes: int, workspace: str | None, keep_snapshot: bool, handoff: str | None = None, handoff_out: str | None = None) -> None:
    workspace_path = Path(workspace).expanduser().resolve() if workspace else default_workspace_path()
    report, plan = build_report(repo_ref, goal=None, depth=depth, method=method, runner="no-exec", max_files=max_files, max_bytes=max_bytes, workspace=workspace_path, keep_snapshot=keep_snapshot)
    paths = write_outputs(report, plan, Path(out), fmt)
    handoff_path = _write_direct_handoff(report, paths, handoff, handoff_out)
    corpus = record_scan(workspace_path, report, plan, paths)
    click.echo(f"Verdict: {report.verdict.decision.value.upper()} (confidence {report.verdict.confidence:.2f})")
    click.echo("Safety: no-exec; no target repo code executed")
    click.echo(f"Workspace: {corpus['workspace']}")
    for kind, path in paths.items():
        click.echo(f"{kind}: {path}")
    if handoff_path:
        click.echo(f"handoff: {handoff_path}")
        click.echo("schema_version: repo-scout.review-handoff.v1")
    click.echo(f"corpus_scans: {corpus['scan_record']}")


@main.command()
@click.argument("repo_ref")
@click.option("--out", default="reports/repo-scout", show_default=True, help="Output directory.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "md", "json", "both"]), default="both", show_default=True)
@click.option("--depth", type=click.Choice(["quick", "standard", "deep"]), default="standard", show_default=True)
@click.option("--method", default="auto", show_default=True)
@click.option("--max-files", default=500, show_default=True, type=int)
@click.option("--max-bytes", default=2_000_000, show_default=True, type=int)
@click.option("--workspace", type=click.Path(file_okay=False), help="Workspace/corpus directory (default: .repo-scout under repo root or cwd).")
@click.option("--keep-snapshot", is_flag=True, help="Retain full remote clone snapshot for debugging/replay. Default removes remote snapshots after analysis.")
@click.option("--handoff", type=click.Choice(["review-board"]), default=None, help="Also export a handoff packet for the selected council.")
@click.option("--handoff-out", type=click.Path(dir_okay=False), default=None, help="Handoff packet JSON output path.")
def scout(repo_ref: str, out: str, fmt: str, depth: str, method: str, max_files: int, max_bytes: int, workspace: str | None, keep_snapshot: bool, handoff: str | None, handoff_out: str | None) -> None:
    """Baseline repository inspection and static due diligence."""
    _scan_impl(repo_ref, out, fmt, depth, method, max_files, max_bytes, workspace, keep_snapshot, handoff, handoff_out)


@main.command()
@click.argument("repo_ref")
@click.option("--out", default="reports/repo-scout", show_default=True, help="Output directory.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "md", "json", "both"]), default="both", show_default=True)
@click.option("--depth", type=click.Choice(["quick", "standard", "deep"]), default="standard", show_default=True)
@click.option("--method", default="auto", show_default=True)
@click.option("--max-files", default=500, show_default=True, type=int)
@click.option("--max-bytes", default=2_000_000, show_default=True, type=int)
@click.option("--workspace", type=click.Path(file_okay=False), help="Workspace/corpus directory (default: .repo-scout under repo root or cwd).")
@click.option("--keep-snapshot", is_flag=True, help="Retain full remote clone snapshot for debugging/replay. Default removes remote snapshots after analysis.")
@click.option("--handoff", type=click.Choice(["review-board"]), default=None, help="Also export a handoff packet for the selected council.")
@click.option("--handoff-out", type=click.Path(dir_okay=False), default=None, help="Handoff packet JSON output path.")
def scan(repo_ref: str, out: str, fmt: str, depth: str, method: str, max_files: int, max_bytes: int, workspace: str | None, keep_snapshot: bool, handoff: str | None, handoff_out: str | None) -> None:
    """Alias for scout."""
    _scan_impl(repo_ref, out, fmt, depth, method, max_files, max_bytes, workspace, keep_snapshot, handoff, handoff_out)


@main.command()
@click.argument("goal_text")
@click.argument("repo_ref")
@click.option("--out", default="reports/repo-scout-goal", show_default=True, help="Output directory.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "md", "json", "both"]), default="both", show_default=True)
@click.option("--depth", type=click.Choice(["quick", "standard", "deep"]), default="standard", show_default=True)
@click.option("--method", default="auto", show_default=True)
@click.option("--runner", type=click.Choice(["no-exec", "local", "docker", "podman", "remote"]), default="no-exec", show_default=True)
@click.option("--profile", "profile_path", type=click.Path(exists=True, dir_okay=False), help="Optional static JSON profile influencing planning/report comparison.")
@click.option("--emit-plan", is_flag=True, help="Write compiled ResearchPlan artifact (always written with report outputs).")
@click.option("--dry-run-plan", is_flag=True, help="Compile and print ResearchPlan JSON only; do not analyze files.")
@click.option("--max-files", default=500, show_default=True, type=int)
@click.option("--max-bytes", default=2_000_000, show_default=True, type=int)
@click.option("--workspace", type=click.Path(file_okay=False), help="Workspace/corpus directory (default: .repo-scout under repo root or cwd).")
@click.option("--keep-snapshot", is_flag=True, help="Retain full remote clone snapshot for debugging/replay. Default removes remote snapshots after analysis.")
@click.option("--handoff", type=click.Choice(["review-board"]), default=None, help="Also export a handoff packet for the selected council.")
@click.option("--handoff-out", type=click.Path(dir_okay=False), default=None, help="Handoff packet JSON output path.")
def goal(goal_text: str, repo_ref: str, out: str, fmt: str, depth: str, method: str, runner: str, profile_path: str | None, emit_plan: bool, dry_run_plan: bool, max_files: int, max_bytes: int, workspace: str | None, keep_snapshot: bool, handoff: str | None, handoff_out: str | None) -> None:
    """Compile a free-text goal into a plan and static report."""
    if handoff_out and not handoff:
        raise click.ClickException("--handoff-out requires --handoff review-board")
    if dry_run_plan:
        if handoff:
            raise click.ClickException("--handoff cannot be used with --dry-run-plan")
        plan = compile_plan(goal_text, repo_ref, depth=depth, method=method, runner=runner, max_files=max_files, max_bytes=max_bytes, profile_path=profile_path)
        click.echo(model_to_json(plan))
        return
    workspace_path = Path(workspace).expanduser().resolve() if workspace else default_workspace_path()
    report, plan = build_report(repo_ref, goal=goal_text, depth=depth, method=method, runner=runner, max_files=max_files, max_bytes=max_bytes, profile_path=profile_path, workspace=workspace_path, keep_snapshot=keep_snapshot)
    paths = write_outputs(report, plan, Path(out), fmt)
    handoff_path = _write_direct_handoff(report, paths, handoff, handoff_out)
    corpus = record_scan(workspace_path, report, plan, paths)
    click.echo(f"Verdict: {report.verdict.decision.value.upper()} (confidence {report.verdict.confidence:.2f})")
    click.echo(f"Goal: {goal_text}")
    click.echo(f"Repo: {report.repo.url}")
    click.echo(f"Safety: {runner}; execution_allowed={report.runner_policy.execution_allowed}")
    click.echo(f"Workspace: {corpus['workspace']}")
    if report.profile_comparisons:
        click.echo("Profile comparison: " + ", ".join(str(pc.get("profile_id")) for pc in report.profile_comparisons))
    if report.probe_results:
        click.echo("Probe results: " + ", ".join(f"{r.id}={r.status}" for r in report.probe_results))
    for kind, path in paths.items():
        click.echo(f"{kind}: {path}")
    if handoff_path:
        click.echo(f"handoff: {handoff_path}")
        click.echo("schema_version: repo-scout.review-handoff.v1")
    click.echo(f"corpus_scans: {corpus['scan_record']}")


@main.group()
def handoff() -> None:
    """Export curated handoff packets from repo-scout report JSON."""


@handoff.command("review-board")
@click.argument("report_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Packet JSON output path.")
@click.option("--issue-id", default=None, help="Optional external tracker issue id.")
@click.option("--requester", default="repo-scout", show_default=True, help="Requester label for packet source metadata.")
def handoff_review_board(report_json: Path, out_path: Path, issue_id: str | None, requester: str) -> None:
    """Build a repo-scout.review-handoff.v1 packet from REPORT_JSON."""
    packet = build_review_board_handoff_from_report_json(report_json, issue_id=issue_id, requester=requester)
    write_handoff_packet(packet, out_path)
    click.echo(f"handoff: {out_path}")
    click.echo("schema_version: repo-scout.review-handoff.v1")
    click.echo("Safety: curated report export only; no target repo code executed")


@main.command()
@click.argument("report_json", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Markdown comparison output path.")
@click.option("--json-out", "json_out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="JSON comparison output path.")
def compare(report_json: tuple[Path, ...], out_path: Path | None, json_out_path: Path | None) -> None:
    """Rank existing repo-scout REPORT_JSON files without scanning repos."""
    comparison = build_comparison(list(report_json))
    paths = write_comparison_outputs(comparison, markdown_out=out_path, json_out=json_out_path)
    if not out_path and not json_out_path:
        click.echo(render_comparison_markdown(comparison), nl=False)
    if paths.get("markdown"):
        click.echo(f"markdown: {paths['markdown']}")
    if paths.get("json"):
        click.echo(f"json: {paths['json']}")
    click.echo(f"Compared reports: {comparison['report_count']}")
    if comparison.get("excluded_report_count"):
        click.echo(f"Excluded reports: {comparison['excluded_report_count']}")
    top_repo = (comparison.get("rollup") or {}).get("top_repo") or "—"
    click.echo(f"Top repo: {top_repo}")
    click.echo("Safety: report JSON read-only; no repo scanning; no target repo code executed")


@main.group()
def campaign() -> None:
    """Initialize and verify repo-scout.campaign.v1 campaign manifests."""


@campaign.command("init")
@click.argument("repo_ref", nargs=-1, required=True)
@click.option("--root", "root", required=True, type=click.Path(file_okay=False, path_type=Path), help="Campaigns root directory.")
@click.option("--campaign-id", default=None, help="Stable campaign id for resume/replay.")
@click.option("--mode", type=click.Choice(["brief", "standard", "full", "refresh_delta", "true_deep_research"]), default="standard", show_default=True)
@click.option("--controller", "scout_controller", required=True, help="Controller/owner writing the manifest lock.")
@click.option("--verifier", required=True, help="Expected independent verifier.")
@click.option("--candidate-owner", required=True, help="Lifecycle owner for candidate decisions.")
@click.option("--max-concurrency", default=3, show_default=True, type=int)
@click.option("--resume", is_flag=True, help="Return existing manifest unchanged if it already exists.")
def campaign_init(repo_ref: tuple[str, ...], root: Path, campaign_id: str | None, mode: str, scout_controller: str, verifier: str, candidate_owner: str, max_concurrency: int, resume: bool) -> None:
    """Create an isolated campaign manifest and per-repository roots."""
    manifest = CampaignInitializer(root).initialize(
        repos=list(repo_ref),
        mode=mode,
        scout_controller=scout_controller,
        verifier=verifier,
        candidate_owner=candidate_owner,
        campaign_id=campaign_id,
        max_concurrency=max_concurrency,
        resume=resume,
    )
    click.echo(f"campaign_id: {manifest.campaign_id}")
    click.echo(f"schema_version: {manifest.schema_version}")
    click.echo(f"mode: {manifest.mode.value}")
    click.echo(f"max_concurrency: {manifest.max_concurrency}")
    click.echo(f"manifest: {manifest.path}")
    click.echo("Safety: initialized manifest only; no target repo code executed; no external systems mutated")


@campaign.command("verify")
@click.argument("manifest_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def campaign_verify(manifest_json: Path) -> None:
    """Verify a campaign manifest and report which reports are comparison-eligible."""
    result = CampaignVerifier(manifest_json).verify()
    click.echo(f"Campaign verification: {'PASS' if result.passed else 'FAIL'}")
    for key in sorted(result.summary):
        click.echo(f"{key}: {result.summary[key]}")
    if result.partial_success:
        click.echo("partial_success: true")
    for path in result.validated_report_paths:
        click.echo(f"validated_report: {path}")
    for path in result.excluded_report_paths:
        click.echo(f"excluded_report: {path}")
    for warning in result.warnings[:10]:
        click.echo(f"warning: {warning}")
    for error in result.errors[:10]:
        click.echo(f"error: {error}", err=True)
    click.echo("Safety: manifest/report validation only; no target repo code executed; no external systems mutated")
    if not result.passed:
        raise click.exceptions.Exit(1)


@main.command("run-ledger-fixture")
@click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False, path_type=Path), help="Output directory for deterministic JSON/Markdown fixture artifacts.")
@click.option("--run-id", default=DEFAULT_FIXTURE_RUN_ID, show_default=True, help="Stable fixture run id to embed in generated artifacts.")
def run_ledger_fixture(out_dir: Path, run_id: str) -> None:
    """Write an original fixture-only run-ledger artifact pair."""
    paths = write_run_ledger_outputs(out_dir, run_id=run_id)
    click.echo("Run ledger fixture: PASS")
    click.echo("Safety: synthetic fixture only; no upstream source copied; no target code executed")
    for kind, path in paths.items():
        click.echo(f"{kind}: {path}")


@main.command("artifact-quality")
@click.argument("fixture_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False, path_type=Path), help="Output directory for JSON/Markdown quality reports.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "md", "json", "both"]), default="both", show_default=True)
def artifact_quality(fixture_dir: Path, out_dir: Path, fmt: str) -> None:
    """Run deterministic quality rules over Markdown fixture artifacts."""
    fixtures = load_markdown_fixtures(fixture_dir)
    if not fixtures:
        raise click.ClickException(f"No Markdown fixtures found in {fixture_dir}")
    report = evaluate_markdown_artifacts(fixtures)
    paths = write_artifact_quality_outputs(report, out_dir, fmt=fmt)
    click.echo(f"Artifact quality: {'PASS' if report.passed else 'FAIL'}")
    click.echo(f"Artifacts checked: {report.summary['artifact_count']}")
    click.echo(f"Findings: {report.summary['finding_count']}")
    for kind, path in paths.items():
        click.echo(f"{kind}: {path}")
    click.echo("Safety: fixture-only Markdown critique; no upstream install/run; no model calls; no live automation")
    if not report.passed:
        raise click.exceptions.Exit(1)


@main.command("repo-fit-validate")
@click.argument("fixture_json", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Optional JSON validation report output path.")
def repo_fit_validate(fixture_json: tuple[Path, ...], out_path: Path | None) -> None:
    """Validate repo-fit/Owner-validation contract fixture JSON outputs."""
    report = validate_fixture_files(fixture_json)
    if out_path:
        write_validation_report(report, out_path)
        click.echo(f"json: {out_path}")
    click.echo(f"Repo-fit validation: {'PASS' if report.passed else 'FAIL'}")
    click.echo(f"Fixtures checked: {report.summary['fixture_count']}")
    click.echo(f"Errors: {report.summary['error_count']}")
    click.echo("Safety: fixture-only JSON validation; no target repo execution; no external system writes; no model calls")
    if not report.passed:
        for result in report.results:
            for error in result.errors[:5]:
                click.echo(f"{result.fixture_id}: {error}", err=True)
        raise click.exceptions.Exit(1)


@main.command("owner-correction-validate")
@click.argument("fixture_json", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Optional JSON validation report output path.")
def owner_correction_validate(fixture_json: tuple[Path, ...], out_path: Path | None) -> None:
    """Validate durable Owner-correction capture/replay fixture JSON."""
    report = validate_correction_files(fixture_json)
    if out_path:
        write_correction_validation_report(report, out_path)
        click.echo(f"json: {out_path}")
    click.echo(f"Owner-correction validation: {'PASS' if report.passed else 'FAIL'}")
    click.echo(f"Fixtures checked: {report.summary['fixture_count']}")
    click.echo(f"Errors: {report.summary['error_count']}")
    click.echo("Safety: fixture-only JSON validation; no target repo execution; no external system writes; no model calls")
    if not report.passed:
        for result in report.results:
            for error in result.errors[:5]:
                click.echo(f"{result.correction_id}: {error}", err=True)
        raise click.exceptions.Exit(1)


@main.group("export")
def export_cmd() -> None:
    """Export curated repo-scout decision surfaces."""


@export_cmd.command("rollup")
@click.argument("report_json", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out-dir", required=True, type=click.Path(file_okay=False, path_type=Path), help="Folder for curated Markdown export. Explicit path required.")
@click.option("--title", default="repo-scout selected repo rollup", show_default=True, help="Rollup report title.")
def export_rollup(report_json: tuple[Path, ...], out_dir: Path, title: str) -> None:
    """Write selected repo cards and a Markdown rollup to an explicit folder."""
    export = build_rollup_export(list(report_json), title=title)
    paths = write_rollup_export(export, out_dir)
    click.echo(f"index: {paths['index']}")
    click.echo(f"manifest: {paths['manifest']}")
    card_count = len([key for key in paths if key.startswith("card_")])
    click.echo(f"cards: {card_count}")
    click.echo("Safety: explicit curated export folder only; no automatic publication; no raw source/evidence dump; no repo scanning; no target code executed")


@main.group()
def sources() -> None:
    """Collect metadata-only source indexes and shortlists."""


@sources.command("trending")
@click.option("--fixture", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Saved GitHub Trending HTML fixture. Preferred for repeatable runs/tests.")
@click.option("--live", is_flag=True, help="Best-effort live fetch from github.com/trending. Never clones or scans listed repos.")
@click.option("--out", "json_path", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output JSON path.")
@click.option("--markdown", "markdown_path", type=click.Path(dir_okay=False, path_type=Path), help="Optional Markdown shortlist path.")
@click.option("--window", type=click.Choice(["daily", "weekly", "monthly"]), default=None, help="Trending time window/since filter metadata.")
@click.option("--language", "language_filter", default=None, help="GitHub Trending language filter metadata/path segment.")
@click.option("--timeout", default=20, show_default=True, type=int, help="Live fetch timeout in seconds.")
def sources_trending(fixture: Path | None, live: bool, json_path: Path, markdown_path: Path | None, window: str | None, language_filter: str | None, timeout: int) -> None:
    """Build a GitHub Trending metadata shortlist without repo clone/scan."""
    if fixture and live:
        raise click.ClickException("Use either --fixture or --live, not both")
    if not fixture and not live:
        raise click.ClickException("Provide --fixture FILE for deterministic metadata parsing, or --live for best-effort fetching")
    report = (
        load_github_trending_fixture(fixture, window=window, language_filter=language_filter)
        if fixture
        else collect_github_trending_live(window=window, language_filter=language_filter, timeout=timeout)
    )
    paths = write_trending_outputs(report, json_path=json_path, markdown_path=markdown_path)
    click.echo(f"source: {report.source}")
    click.echo(f"count: {report.count}")
    click.echo("Safety: metadata-only; no clone, no repo scan, no target code execution")
    for kind, path in paths.items():
        click.echo(f"{kind}: {path}")


@main.command()
def doctor() -> None:
    """Show local capability and safety status."""
    click.echo(f"repo-scout {__version__}")
    click.echo(f"python {sys.version.split()[0]}")
    click.echo("default_runner no-exec")
    click.echo("target_code_execution disabled")
    click.echo("local_path_intake supported")
    click.echo("remote_static_clone transient by default")
    click.echo("campaign_schema repo-scout.campaign.v1")
    click.echo("campaign_modes brief standard full refresh_delta true_deep_research")
    click.echo("campaign_max_concurrency 3")
    click.echo("workspace_corpus default .repo-scout under repo root or cwd")
    click.echo("github_url_parsing supported")
    click.echo("github_clone safe command: git clone --depth=1 --single-branch --no-tags --recurse-submodules=no --no-checkout --filter=blob:limit=2000000")
    click.echo("github_clone hardening https-only transport, no system/global git config, symlinks/hooks/fsmonitor off, regular files checked out only")
    demo = parse_repo_ref("https://github.com/owner/repo")
    click.echo("planned_clone_example " + " ".join(planned_clone_command(demo, Path("/tmp/repo-scout/owner/repo"))))


@main.group()
def scanners() -> None:
    """Inspect optional external scanner wrappers without running them."""


@scanners.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit scanner availability as JSON.")
def scanners_list(as_json: bool) -> None:
    """List optional scanner definitions and PATH availability."""
    registry = scanner_registry()
    rows = []
    for scanner in registry.list():
        availability = scanner.availability()
        rows.append(
            {
                "id": scanner.id,
                "name": scanner.name,
                "command": scanner.command,
                "purpose": scanner.purpose,
                "status": availability.status,
                "available": availability.available,
                "path": availability.path,
                "version": availability.version,
                "network": scanner.network,
                "executes_target_code": scanner.executes_target_code,
                "message": availability.message,
            }
        )
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    click.echo("Optional scanners (missing tools are skipped; no scanner is required):")
    click.echo("ID             STATUS     NETWORK        EXECUTES_CODE  PURPOSE")
    for row in rows:
        click.echo(f"{row['id']:<14} {row['status']:<10} {row['network']:<14} {str(row['executes_target_code']).lower():<13} {row['purpose']}")


@scanners.command("plan")
@click.argument("repo", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit planned ScannerRun records as JSON.")
def scanners_plan(repo: Path, as_json: bool) -> None:
    """Plan optional scanner commands for a local repo without executing them."""
    runs = scanner_registry().plan(repo)
    payload = [run.model_dump(mode="json") for run in runs]
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(f"Scanner plan for {repo} (plan-only; no commands executed):")
    for run in runs:
        click.echo(f"- {run.scanner_id}: {run.status}; available={run.available}; network={run.network}; executes_target_code={run.executes_target_code}")
        click.echo("  command: " + " ".join(run.command))
        if run.safety_notes:
            click.echo("  safety: " + run.safety_notes[0])


if __name__ == "__main__":
    main()

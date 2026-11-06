#!/usr/bin/env python3
"""Verify an app-compatible repo-scout.campaign.v1 scaffold/output.

Checks the application campaign contract plus skill sidecars for lane packets.
This verifier is read-only and never executes target repositories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

CAMPAIGN_SCHEMA = "repo-scout.campaign.v1"
LANE_SCHEMA = "repo-scout.lane-output.v1"
ALLOWED_MODES = {"brief", "standard", "full", "refresh_delta", "true_deep_research"}
ALLOWED_REPO_STATUS = {"initialized", "collector_done", "lanes_done", "synthesized", "verified", "blocked", "failed"}
ALLOWED_CAMPAIGN_STATUS = {"initialized", "running", "partial", "verified", "failed", "blocked"}
ALLOWED_DECISIONS = {"adopt", "pilot", "watch", "skip", "route", "block"}
ALLOWED_CONSUMPTION = {"none", "pattern", "translation", "sandbox", "fork", "integration"}
ALLOWED_BUILD_GATES = {"describe_only", "peer_review", "spike_original", "build_original", "blocked_needs_owner"}
ACTION_TYPES = {"buildbrief_candidate", "watch_trigger", "close", "rescout", "existing_parent_update", "candidate_ledger"}
ACTION_RECOMMENDATIONS = {"promote_to_candidate", "accepted_capability", "watch_with_trigger", "reject", "defer", "investigate"}
ACTION_OWNER_GATES = {"none", "credentials", "spend", "public_exposure", "destructive", "governance", "business_direction", "unknown"}
ACTION_NEXT_REGISTRATION_STEPS = {"candidate_ledger", "tracker_parent", "update_existing_issue", "watch_ledger", "no_action", "rescout"}
ACTION_REGISTRATION_STATUSES = {"generated_from_report", "registered", "registered_watch", "registered_existing_tracker", "fail_closed"}
REJECT_COLLECTOR_VERDICTS = {"reject", "no_verdict", "discard", "skip"}
LANE_LIMITS = {
    "brief": (1, 1),
    "standard": (2, 4),
    "full": (5, 7),
    "refresh_delta": (2, 3),
    "true_deep_research": (1, 2),
}
REQUIRED_CANONICAL = {"decision", "consumption_mode", "build_gate", "collector_verdict"}
FORBIDDEN_PHRASE = "no target repo code execution"
IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
COMPLETED_STATUSES = {"synthesized", "verified"}
SCAFFOLD_STATUSES = {"initialized", "running", "partial", "blocked", "failed"}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_ts(value: Any, *, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty ISO timestamp")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label} invalid timestamp: {exc}")
        return None


def candidate_manifest(root: Path) -> Path:
    if root.is_file():
        return root
    if (root / "manifest.json").exists():
        return root / "manifest.json"
    if (root / "campaign-manifest.json").exists():
        return root / "campaign-manifest.json"
    return root / "manifest.json"


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def safe_child(root: Path, value: str | Path, *, label: str, errors: list[str]) -> Path | None:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = resolve_path(candidate)
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes campaign root: {candidate}")
        return None
    return candidate


def validate_identifier(value: Any, *, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not IDENT_RE.fullmatch(value) or value in {".", ".."} or "/" in value or "\\" in value:
        errors.append(f"{label} is not a safe sanitized identifier: {value!r}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def has_unrendered_template(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return "{{" in text or "}}" in text or bool(re.search(r"\$\{?(campaign_id|repo_url|repo_slug|mode|lane_name|campaign_root|repo_root|artifact_path|citation_ledger)\}?", text))


def is_scaffold_lane(data: dict[str, Any]) -> bool:
    observed = "\n".join(data.get("forbidden_actions_observed", []))
    gaps = "\n".join(data.get("gaps", []))
    unknown = "\n".join((data.get("findings") or {}).get("unknown", [])) if isinstance(data.get("findings"), dict) else ""
    return "scaffold only" in observed.lower() or "not yet executed" in gaps.lower() or "pending" in unknown.lower()


def validate_c1_call_block(markdown: str, *, require_section: bool = False) -> list[str]:
    """Return errors for the optional C1 owner-call section."""
    heading_re = re.compile(r'^## What the owner needs to decide\s*$', re.I | re.M)
    headings = list(heading_re.finditer(markdown))
    if not headings:
        return ['C1 section missing: ## What the owner needs to decide'] if require_section else []
    if len(headings) != 1:
        return ['C1 section must appear exactly once']
    start = headings[0].end()
    next_heading = re.search(r'^##\s+.+$', markdown[start:], re.M)
    body = markdown[start:start + next_heading.start() if next_heading else len(markdown)].strip()
    item_re = re.compile(r'^(\d+)\.\s+(.+)$', re.M)
    items = list(item_re.finditer(body))
    if not items:
        return ['C1 section must contain at least one numbered call']
    errors: list[str] = []
    if body[:items[0].start()].strip():
        errors.append('C1 section must start with a numbered call, without introductory prose')
    required = ['why it matters', 'if you do nothing', 'suggested', 'sources']
    allowed = set(required + ['urgency'])
    for index, item in enumerate(items, 1):
        if int(item.group(1)) != index:
            errors.append(f'C1 call {index}: item number must be {index}')
        if not re.fullmatch(r'\*\*[^*\n]+\?\*\*', item.group(2).strip()):
            errors.append(f'C1 call {index}: use a bold question ending in ?')
        end = items[index].start() if index < len(items) else len(body)
        lines = [line for line in body[item.end():end].splitlines() if line.strip()]
        fields: list[str] = []
        values: dict[str, str] = {}
        for line in lines:
            match = re.fullmatch(r'\s{3,}-\s+([^:]+):\s*(\S.*)', line)
            if not match:
                errors.append(f'C1 call {index}: every field must be one indented bullet on one line')
                continue
            label = match.group(1).strip().lower()
            if label not in allowed:
                errors.append(f'C1 call {index}: unknown field {match.group(1).strip()}')
                continue
            if label in values:
                errors.append(f'C1 call {index}: duplicate field {match.group(1).strip()}')
                continue
            fields.append(label)
            values[label] = match.group(2).strip()
        display_labels = {
            'why it matters': 'Why it matters',
            'if you do nothing': 'If you do nothing',
            'suggested': 'Suggested',
            'sources': 'Sources',
        }
        for label in required:
            if label not in values:
                errors.append(f'C1 call {index}: missing {display_labels[label]}')
        expected_order = ['why it matters', 'if you do nothing', 'suggested']
        if 'urgency' in values:
            expected_order.append('urgency')
        expected_order.append('sources')
        if fields != expected_order:
            errors.append(f'C1 call {index}: fields must follow the C1 order')
        suggested = values.get('suggested', '')
        if suggested and not re.fullmatch(r'(Yes|No|Later)\s+(?:-|–)\s+\S.*', suggested):
            errors.append(f'C1 call {index}: Suggested must be Yes, No, or Later, then an en dash or ASCII hyphen and one line')
        urgency = values.get('urgency')
        if urgency is not None and urgency not in {'now', 'week', 'later'}:
            errors.append(f'C1 call {index}: Urgency must be now, week, or later')
    return errors


def validate_synthesis(root: Path, mode: Any, errors: list[str]) -> None:
    synthesis = root / "synthesis"
    primary = synthesis / ("brief.md" if mode == "brief" else "full-pack.md")
    action_register = synthesis / "action-register.json"
    verification = synthesis / "verification.txt"
    for path in (primary, action_register, verification):
        if not path.is_file():
            errors.append(f"missing required synthesis artifact: {path}")
    if action_register.is_file():
        try:
            payload = load_json(action_register)
            if not isinstance(payload, list):
                errors.append("synthesis/action-register.json must contain a JSON list")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid synthesis/action-register.json: {exc}")
    markdown_outputs = sorted(path for path in synthesis.rglob("*.md") if path.is_file())
    for path in markdown_outputs:
        text = path.read_text(encoding="utf-8")
        if path == primary and not text.strip():
            errors.append(f"required synthesis artifact is empty: {primary}")
        for reason in validate_c1_call_block(text):
            errors.append(f"{path}: C1 validation failed: {reason}")
    if verification.is_file() and not verification.read_text(encoding="utf-8").strip():
        errors.append("synthesis/verification.txt must not be empty")


def _optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_action_register_entry(entry: Any, *, index: int) -> str | None:
    """Stdlib mirror of app ActionRegisterEntry required fields/types/enums."""
    if not isinstance(entry, dict):
        return f"action_register[{index}] must be object"
    required_string_fields = ("action_id", "title", "why")
    for field in required_string_fields:
        if not isinstance(entry.get(field), str):
            return f"action_register[{index}].{field} must be string"
    required_enum_checks = (
        ("action_type", ACTION_TYPES),
        ("recommendation", ACTION_RECOMMENDATIONS),
        ("next_registration_step", ACTION_NEXT_REGISTRATION_STEPS),
    )
    for field, allowed in required_enum_checks:
        if entry.get(field) not in allowed:
            return f"action_register[{index}].{field} has unsupported value: {entry.get(field)!r}"
    optional_enum_checks = (
        ("owner_gate", ACTION_OWNER_GATES),
        ("registration_status", ACTION_REGISTRATION_STATUSES),
    )
    for field, allowed in optional_enum_checks:
        if field in entry and entry.get(field) not in allowed:
            return f"action_register[{index}].{field} has unsupported value: {entry.get(field)!r}"
    for field in ("target_surfaces", "evidence_refs", "risk_notes"):
        if field in entry and not _str_list(entry.get(field)):
            return f"action_register[{index}].{field} must be a list of strings"
    for field in ("existing_tracker", "trigger_to_reopen"):
        if field in entry and not _optional_str(entry.get(field)):
            return f"action_register[{index}].{field} must be string or null"
    confidence = entry.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
        return f"action_register[{index}].confidence must be a number between 0.0 and 1.0"
    return None


def validate_action_register(action_register: Any, *, require_non_empty: bool) -> str | None:
    if not isinstance(action_register, list):
        return "ActionRegister must be a list"
    if require_non_empty and not action_register:
        return "non-reject report missing ActionRegister"
    for index, entry in enumerate(action_register):
        reason = validate_action_register_entry(entry, index=index)
        if reason:
            return reason
    return None


def _canonical_local_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _canonical_repo_identity(value: str) -> tuple[str, str] | None:
    raw = value.strip()
    if not raw:
        return None
    ssh_match = re.fullmatch(r"git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?", raw)
    if ssh_match:
        return ("github", f"{ssh_match.group('owner').lower()}/{ssh_match.group('name').removesuffix('.git').lower()}")
    parsed = urlsplit(raw)
    host = parsed.netloc.lower().rsplit("@", 1)[-1]
    if host == "github.com" and parsed.scheme in {"http", "https", "ssh", "git"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            return ("github", f"{parts[0].lower()}/{parts[1].removesuffix('.git').lower()}")
    if parsed.scheme == "file":
        return ("local", _canonical_local_path(unquote(parsed.path)))
    if not parsed.scheme and not parsed.netloc:
        return ("local", _canonical_local_path(raw))
    return None


def _repo_identity_candidates(repo_payload: dict[str, Any]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for key in ("url", "path"):
        value = repo_payload.get(key)
        if isinstance(value, str) and value.strip():
            identity = _canonical_repo_identity(value)
            if identity:
                identities.add(identity)
    owner = repo_payload.get("owner")
    name = repo_payload.get("name")
    if isinstance(owner, str) and isinstance(name, str) and owner.strip() and name.strip():
        identities.add(("github", f"{owner.strip().lower()}/{name.strip().removesuffix('.git').lower()}"))
    return identities


def validate_report_identity(manifest: dict[str, Any], repo: dict[str, Any], payload: dict[str, Any]) -> str | None:
    manifest_identities = _repo_identity_candidates({"url": repo.get("repo_url")})
    raw_report_repo = payload.get("repo")
    report_repo: dict[str, Any] = raw_report_repo if isinstance(raw_report_repo, dict) else {}
    report_identities = _repo_identity_candidates(report_repo)
    slug = repo.get("repo_slug") or repo.get("repo_url") or "unknown"
    if not manifest_identities:
        return f"repo identity unavailable for manifest entry {slug}: {repo.get('repo_url')}"
    if not report_identities:
        return f"repo identity unavailable in report for manifest entry {slug}"
    if manifest_identities.isdisjoint(report_identities):
        report_label = report_repo.get("url") or report_repo.get("path") or report_repo.get("name") or "unknown"
        return f"repo identity mismatch for {slug}: manifest {repo.get('repo_url')} != report {report_label}"

    binding = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
    explicit_campaign_id = binding.get("campaign_id") or payload.get("campaign_id")
    if explicit_campaign_id is not None and str(explicit_campaign_id) != str(manifest.get("campaign_id")):
        return f"repo identity mismatch for {slug}: report campaign_id {explicit_campaign_id} != manifest {manifest.get('campaign_id')}"
    explicit_repo_run_id = binding.get("repo_run_id") or binding.get("manifest_run_id") or payload.get("repo_run_id") or payload.get("manifest_run_id")
    if explicit_repo_run_id is not None and str(explicit_repo_run_id) != str(repo.get("run_id")):
        return f"repo identity mismatch for {slug}: report repo_run_id {explicit_repo_run_id} != manifest {repo.get('run_id')}"
    expected_root = binding.get("expected_root") or payload.get("expected_root")
    if expected_root is not None and _canonical_local_path(str(expected_root)) != _canonical_local_path(str(repo.get("root"))):
        return f"repo identity mismatch for {slug}: report expected_root {expected_root} != manifest {repo.get('root')}"
    return None


def validate_report_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    if payload.get("status") in {"failed", "blocked", "vetoed"}:
        return False, "failed/blocked/vetoed reports are excluded from completed verification"
    runner_policy_value = payload.get("runner_policy")
    runner_policy: dict[str, Any] = runner_policy_value if isinstance(runner_policy_value, dict) else {}
    if runner_policy.get("runner") != "no-exec":
        return False, "report runner_policy.runner must be no-exec"
    if runner_policy.get("execution_allowed") is not False:
        return False, "report runner_policy.execution_allowed must be false"
    metrics_value = payload.get("metrics")
    metrics: dict[str, Any] = metrics_value if isinstance(metrics_value, dict) else {}
    if metrics.get("target_code_execution") is not False:
        return False, "report metrics.target_code_execution must be false"
    probe_results = payload.get("probe_results", []) if isinstance(payload.get("probe_results"), list) else []
    for result in probe_results:
        if isinstance(result, dict) and result.get("status") not in {None, "not_run", "skipped", "blocked", "unsafe"}:
            return False, "no-exec report must not include executed probe results"
        if isinstance(result, dict) and (result.get("runner") or {}).get("execution_allowed") is True:
            return False, "probe_result runner allowed execution"
    canonical = payload.get("canonical_decision") if isinstance(payload.get("canonical_decision"), dict) else {}
    decision = canonical.get("decision") or ((payload.get("verdict") or {}).get("decision") if isinstance(payload.get("verdict"), dict) else None)
    if decision and decision not in ALLOWED_DECISIONS and decision not in {"adapt", "borrow", "reject", "investigate", "no_verdict"}:
        return False, f"unknown decision/collector verdict: {decision}"
    if canonical.get("decision") and canonical.get("decision") not in ALLOWED_DECISIONS:
        return False, "canonical_decision.decision is not an application canonical value"
    if canonical.get("consumption_mode") and canonical.get("consumption_mode") not in ALLOWED_CONSUMPTION:
        return False, "canonical_decision.consumption_mode is not canonical"
    if canonical.get("build_gate") and canonical.get("build_gate") not in ALLOWED_BUILD_GATES:
        return False, "canonical_decision.build_gate is not canonical"
    action_register = payload.get("action_register", [])
    verdict = str(decision or payload.get("collector_verdict") or "no_verdict")
    action_reason = validate_action_register(action_register, require_non_empty=verdict not in REJECT_COLLECTOR_VERDICTS)
    if action_reason:
        return False, action_reason
    return True, None


def _norm_quote(text: str) -> str:
    return " ".join(str(text).split()).lower()


def validate_ledger_quotes(root: Path, ledger: Any, *, strict: bool, errors: list[str], warnings: list[str]) -> None:
    """Check every ledger quote against its saved evidence file.

    Proves each quote exists, unchanged, inside its cited line range. It does
    not prove the quote supports the claim; the controller still judges that.
    """
    sink = errors if strict else warnings
    root = resolve_path(root)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("sources"), list):
        sink.append("citation-ledger.json must be an object with a sources list")
        return
    for src in ledger["sources"]:
        if not isinstance(src, dict):
            sink.append("citation-ledger.json source entry must be an object")
            continue
        for i, quote in enumerate(src.get("quotes") or []):
            label = f"ledger source {src.get('id')} quote {i}"
            if not isinstance(quote, dict):
                sink.append(f"{label}: quote must be an object")
                continue
            rel = quote.get("path") or src.get("path")
            if not rel:
                # grounded-citations attach_quote checks text at attach time but
                # saves no file; such quotes cannot be rechecked later.
                warnings.append(f"{label}: cannot be rechecked; no saved evidence path")
                continue
            path = resolve_path(root / str(rel))
            try:
                path.relative_to(root)
            except ValueError:
                sink.append(f"{label}: evidence file missing or outside campaign root")
                continue
            if not path.is_file():
                sink.append(f"{label}: evidence file missing or outside campaign root")
                continue
            data = path.read_bytes()
            want = quote.get("sha256") or (src.get("sha256") if not quote.get("path") or quote.get("path") == src.get("path") else None)
            if want and hashlib.sha256(data).hexdigest() != want:
                sink.append(f"{label}: evidence hash changed since citation")
                continue
            lines = data.decode("utf-8", "replace").splitlines()
            start, end = quote.get("start_line"), quote.get("end_line")
            if start is not None or end is not None:
                if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= len(lines)):
                    sink.append(f"{label}: line range {start}-{end} outside file")
                    continue
                window = "\n".join(lines[start - 1:end])
            else:
                window = "\n".join(lines)
            if _norm_quote(quote.get("text", "")) not in _norm_quote(window):
                where = f"lines {start}-{end}" if start is not None else "the evidence file"
                sink.append(f"{label}: quote not found in {where}")


def _resolve_json_pointer(document: Any, pointer: str) -> bool:
    if pointer in ("", "/"):
        return True
    if not pointer.startswith("/"):
        return False
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                return False
            node = node[int(token)]
        elif isinstance(node, dict):
            if token not in node:
                return False
            node = node[token]
        else:
            return False
    return True


EVIDENCE_REF_POLICY = "resolvable-v1"
_LINE_REF = re.compile(r"^(?P<file>[^#]+)#L(?P<start>\d+)(?:-L?(?P<end>\d+))?$")
_BARE_ID = re.compile(r"^[A-Z][A-Z0-9_]*\d+$")


def _campaign_file(root: Path, repo_root: Path, rel: str) -> Path | None:
    rel = rel.lstrip("/")
    for base in (root, resolve_path(repo_root)):
        candidate = resolve_path(base / rel)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def report_evidence_ids(report: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(report, dict):
        for key in ("evidence", "claims", "findings", "idea_cards", "idea_candidates"):
            for item in report.get(key) or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.add(item["id"])
    return ids


def validate_lane_pointers(root: Path, repo_root: Path, lane: dict, label: str, sink: list[str], *,
                           strict_policy: bool = False, report_ids: set[str] | None = None) -> None:
    """Check lane evidence refs resolve inside the campaign.

    Supported forms: `file.json#/json-pointer`, `file#L10` or `file#L10-L20`,
    and a bare collector evidence id such as `E1`. Legacy campaigns only have
    their pointer refs checked, so they keep verifying unchanged. Campaigns
    created with evidence_ref_policy=resolvable-v1 must use supported forms.
    """
    root = resolve_path(root)
    for ref in lane.get("evidence_refs") or []:
        if not isinstance(ref, str):
            if strict_policy:
                sink.append(f"{label}: evidence ref {ref!r} must be a string in a supported form")
            continue
        if "#/" in ref or ref.endswith("#"):
            file_part, _, pointer = ref.partition("#")
            target = _campaign_file(root, repo_root, file_part)
            if target is None:
                sink.append(f"{label}: evidence ref {ref!r} does not resolve to a file inside the campaign")
                continue
            try:
                document = load_json(target)
            except Exception:  # noqa: BLE001
                sink.append(f"{label}: evidence ref {ref!r} does not resolve; target is not JSON")
                continue
            if not _resolve_json_pointer(document, pointer):
                sink.append(f"{label}: evidence ref {ref!r} does not resolve")
            continue
        if not strict_policy:
            continue
        line_match = _LINE_REF.match(ref)
        if line_match:
            target = _campaign_file(root, repo_root, line_match.group("file"))
            if target is None:
                sink.append(f"{label}: evidence ref {ref!r} does not resolve to a file inside the campaign")
                continue
            total = len(target.read_bytes().decode("utf-8", "replace").splitlines())
            start = int(line_match.group("start"))
            end = int(line_match.group("end") or start)
            if not (1 <= start <= end <= total):
                sink.append(f"{label}: evidence ref {ref!r} line range is outside the file ({total} lines)")
            continue
        if _BARE_ID.match(ref):
            if ref not in (report_ids or set()):
                sink.append(f"{label}: evidence ref {ref!r} is not an id in the collector report")
            continue
        sink.append(f"{label}: evidence ref {ref!r} is not a supported form (file.json#/pointer, file#L1-L9 or a collector evidence id)")


def verify_campaign(root_or_manifest: Path) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = candidate_manifest(root_or_manifest)
    root = resolve_path(manifest_path.parent)
    if not manifest_path.exists():
        return "fail", [f"missing manifest: {manifest_path}"], []
    try:
        manifest = load_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        return "fail", [f"invalid campaign manifest JSON: {exc}"], []
    declared_root = manifest.get("root")
    if declared_root:
        declared = resolve_path(Path(declared_root))
        if declared != root:
            errors.append(f"manifest.root must equal manifest directory: {declared} != {root}")
    if manifest.get("schema_version") != CAMPAIGN_SCHEMA:
        errors.append(f"campaign schema_version must be {CAMPAIGN_SCHEMA!r}")
    validate_identifier(manifest.get("campaign_id"), label="campaign_id", errors=errors)
    mode = manifest.get("mode")
    if mode not in ALLOWED_MODES:
        errors.append(f"unsupported mode: {mode!r}")
    status = manifest.get("status")
    if status not in ALLOWED_CAMPAIGN_STATUS:
        errors.append(f"unsupported campaign status: {status!r}")
    created_at = parse_ts(manifest.get("created_at"), label="created_at", errors=errors)
    updated_at = parse_ts(manifest.get("updated_at"), label="updated_at", errors=errors)
    next_review_at = parse_ts(manifest.get("next_review_at"), label="next_review_at", errors=errors)
    if created_at and updated_at and updated_at < created_at:
        errors.append("updated_at precedes created_at")
    if created_at and next_review_at and next_review_at <= created_at:
        errors.append("next_review_at must be after created_at")
    for field in ("owner_issue", "scout_controller", "candidate_owner", "verifier", "routing_surface", "owner_gate"):
        if not manifest.get(field):
            errors.append(f"missing required lifecycle field: {field}")
    if manifest.get("max_concurrency", manifest.get("concurrency")) != 3 and len(manifest.get("repositories", [])) > 1:
        warnings.append("multi-repo default concurrency is not 3")
    if manifest.get("partial_success_allowed") is not True:
        errors.append("partial_success_allowed must be true")
    lock = manifest.get("lock") if isinstance(manifest.get("lock"), dict) else None
    if not lock:
        errors.append("missing lock/owner record")
    else:
        for field in ("owner", "pid", "created_at", "expires_at"):
            if field not in lock:
                errors.append(f"lock missing {field}")
        parse_ts(lock.get("created_at"), label="lock.created_at", errors=errors)
        parse_ts(lock.get("expires_at"), label="lock.expires_at", errors=errors)
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        errors.append("missing atomic checkpoints")
    else:
        for idx, cp in enumerate(checkpoints):
            if not isinstance(cp, dict) or not cp.get("name"):
                errors.append(f"checkpoint {idx} missing name")
                continue
            parse_ts(cp.get("created_at"), label=f"checkpoint {idx}.created_at", errors=errors)
            if not isinstance(cp.get("details"), dict):
                errors.append(f"checkpoint {idx} details must be object")
    baseline = manifest.get("baseline", {}) if isinstance(manifest.get("baseline"), dict) else {}
    if baseline.get("doctor_command") != "repo-scout doctor":
        errors.append("baseline.doctor_command must be 'repo-scout doctor'")
    if baseline.get("collector_command_shape") != "repo-scout scout <url> --workspace <repo-root>/collector --out <repo-root>/collector/report --format both":
        errors.append("baseline.collector_command_shape must be the hard-wired scout command without --runner")
    if baseline.get("runner") != "no-exec":
        errors.append("baseline.runner must be 'no-exec'")
    if not REQUIRED_CANONICAL.issubset(set(manifest.get("canonical_fields", []))):
        errors.append("campaign canonical_fields must include decision, consumption_mode, build_gate, collector_verdict")
    ledger = root / "citation-ledger.json"
    if not ledger.exists():
        warnings.append("missing shared citation-ledger.json sidecar")
    repos = manifest.get("repositories") or []
    if not repos:
        errors.append("manifest has no repositories")
    hashes = manifest.get("hashes") if isinstance(manifest.get("hashes"), dict) else {}
    for rel, entry in hashes.items():
        hpath = safe_child(root, rel, label=f"hash path {rel}", errors=errors)
        if hpath and hpath.exists():
            expected = entry.get("sha256") if isinstance(entry, dict) else None
            if expected and file_sha256(hpath) != expected:
                errors.append(f"hash mismatch for {rel}")
        elif hpath:
            errors.append(f"hashed artifact missing: {rel}")
    if not hashes:
        warnings.append("manifest has no artifact hashes")
    seen_roots: set[Path] = set()
    seen_slugs: set[str] = set()
    scaffold_lanes = 0
    lane_count = 0
    lane_pointer_problems: list[str] = []
    strict_ref_policy = manifest.get("evidence_ref_policy") == EVIDENCE_REF_POLICY
    validated_reports: list[Path] = []
    for repo in repos:
        if not isinstance(repo, dict):
            errors.append("repository entry must be object")
            continue
        slug = repo.get("repo_slug") or repo.get("repo_url")
        validate_identifier(repo.get("repo_slug"), label=f"repo_slug {slug}", errors=errors)
        if repo.get("repo_slug") in seen_slugs:
            errors.append(f"duplicate repo_slug: {repo.get('repo_slug')}")
        seen_slugs.add(repo.get("repo_slug"))
        repo_root = safe_child(root, repo.get("root", ""), label=f"repository root {slug}", errors=errors)
        if repo_root is None:
            continue
        if repo_root in seen_roots:
            errors.append(f"duplicate repo root: {repo_root}")
        seen_roots.add(repo_root)
        if not repo_root.exists():
            errors.append(f"repository root missing: {slug}: {repo_root}")
            continue
        if repo.get("status") not in ALLOWED_REPO_STATUS:
            errors.append(f"repo {slug} has invalid status {repo.get('status')!r}")
        freshness = repo.get("freshness")
        if not isinstance(freshness, dict):
            errors.append(f"repo {slug} missing freshness object")
        else:
            for field in ("inspected_at", "inspected_commit", "prior_campaign"):
                if field not in freshness:
                    errors.append(f"repo {slug} freshness missing {field}")
            if repo.get("status") in {"collector_done", "lanes_done", "synthesized", "verified"}:
                if not freshness.get("inspected_at"):
                    errors.append(f"repo {slug} completed status requires freshness.inspected_at")
                if not freshness.get("inspected_commit"):
                    errors.append(f"repo {slug} completed status requires freshness.inspected_commit")
        retention = repo.get("retention")
        if not isinstance(retention, dict):
            errors.append(f"repo {slug} missing retention object")
        else:
            if not isinstance(retention.get("snapshot_retained"), bool):
                errors.append(f"repo {slug} retention.snapshot_retained must be boolean")
            if not retention.get("cleanup_status"):
                errors.append(f"repo {slug} retention.cleanup_status is required")
        report_path_value = repo.get("report_path")
        if report_path_value:
            report_path = safe_child(root, report_path_value, label=f"repo {slug} report_path", errors=errors)
            if report_path and report_path.exists():
                try:
                    report_payload = load_json(report_path)
                    ok, reason = validate_report_payload(report_payload)
                    if not ok:
                        errors.append(f"{slug}: {reason}")
                    else:
                        identity_reason = validate_report_identity(manifest, repo, report_payload)
                        if identity_reason:
                            errors.append(identity_reason)
                        else:
                            validated_reports.append(report_path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"invalid report JSON for {slug}: {exc}")
            elif report_path:
                errors.append(f"report_path missing for {slug}: {report_path}")
        elif repo.get("status") in COMPLETED_STATUSES:
            errors.append(f"completed repo has no report_path: {slug}")
        repo_report_ids: set[str] = set()
        report_candidate = repo_root / "collector" / "report" / "report.json"
        if report_candidate.is_file():
            try:
                repo_report_ids = report_evidence_ids(load_json(report_candidate))
            except Exception:  # noqa: BLE001 - invalid reports are reported above
                repo_report_ids = set()
        sidecar_path = repo_root / "repo-scout-sidecar.json"
        if not sidecar_path.exists():
            warnings.append(f"missing skill sidecar for {slug}")
            continue
        try:
            sidecar = load_json(sidecar_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid sidecar JSON for {slug}: {exc}")
            continue
        if sidecar.get("repo_slug") != repo.get("repo_slug"):
            errors.append(f"repo {slug} sidecar repo_slug mismatch")
        for field in REQUIRED_CANONICAL:
            if field not in sidecar:
                errors.append(f"repo {slug} sidecar missing canonical field {field}")
        if sidecar.get("decision") is not None and sidecar.get("decision") not in ALLOWED_DECISIONS:
            errors.append(f"repo {slug} sidecar decision is not canonical")
        if sidecar.get("consumption_mode") is not None and sidecar.get("consumption_mode") not in ALLOWED_CONSUMPTION:
            errors.append(f"repo {slug} sidecar consumption_mode is not canonical")
        if sidecar.get("build_gate") not in ALLOWED_BUILD_GATES:
            errors.append(f"repo {slug} sidecar build_gate is not canonical")
        lanes = sidecar.get("lanes") or []
        lo, hi = LANE_LIMITS.get(str(mode), (0, 999))
        if not (lo <= len(lanes) <= hi):
            errors.append(f"repo {slug} has {len(lanes)} lanes; expected {lo}-{hi} for {mode}")
        names = [lane.get("name") for lane in lanes if isinstance(lane, dict)]
        if len(names) != len(set(names)):
            errors.append(f"repo {slug} has duplicate lane names")
        sidecar_baseline = sidecar.get("baseline", {}) if isinstance(sidecar.get("baseline"), dict) else {}
        if sidecar_baseline.get("runner") != "no-exec":
            errors.append(f"repo {slug} baseline.runner must be no-exec")
        command = sidecar_baseline.get("collector_command", "")
        if "--runner" in command or "repo-scout scout" not in command or "--workspace" not in command or "--out" not in command or "--format both" not in command:
            errors.append(f"repo {slug} collector command must be hard-wired scout no-exec shape without --runner")
        for lane in lanes:
            if not isinstance(lane, dict):
                errors.append(f"repo {slug} lane entry must be object")
                continue
            lane_status = lane.get("status")
            if lane_status not in {"pass", "hold", "fail"}:
                errors.append(f"repo {slug} lane {lane.get('name')} invalid sidecar status")
            artifact = safe_child(root, lane.get("artifact", ""), label=f"lane artifact {slug}/{lane.get('name')}", errors=errors)
            packet = safe_child(root, lane.get("worker_packet", ""), label=f"worker packet {slug}/{lane.get('name')}", errors=errors)
            if artifact is None or not artifact.exists():
                errors.append(f"repo {slug} missing lane artifact {artifact}")
                continue
            if packet is None or not packet.exists():
                errors.append(f"repo {slug} missing worker packet {packet}")
            elif has_unrendered_template(packet):
                errors.append(f"repo {slug} worker packet has unrendered template placeholders: {packet}")
            try:
                lo_data = load_json(artifact)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invalid lane JSON {artifact}: {exc}")
                continue
            lane_count += 1
            if lo_data.get("schema_version") != LANE_SCHEMA:
                errors.append(f"lane {artifact} schema_version must be {LANE_SCHEMA!r}")
            if lo_data.get("lane_id") != lane.get("name"):
                errors.append(f"lane {artifact} lane_id mismatch")
            if lo_data.get("status") != lane_status:
                errors.append(f"lane {artifact} status mismatch sidecar")
            if lo_data.get("status") not in {"pass", "hold", "fail"}:
                errors.append(f"lane {artifact} invalid status")
            observed = "\n".join(lo_data.get("forbidden_actions_observed", []))
            if FORBIDDEN_PHRASE not in observed.lower():
                errors.append(f"lane {artifact} missing no-target-execution declaration")
            findings = lo_data.get("findings")
            if not isinstance(findings, dict):
                errors.append(f"lane {artifact} findings must be object")
            else:
                for field in ("fact", "upstream_claim", "independent_evidence", "inference", "unknown"):
                    if field not in findings or not isinstance(findings.get(field), list):
                        errors.append(f"lane {artifact} findings.{field} must be a list")
            pointer_problems: list[str] = []
            validate_lane_pointers(root, repo_root, lo_data, f"lane {artifact}", pointer_problems,
                                   strict_policy=strict_ref_policy, report_ids=repo_report_ids)
            lane_pointer_problems.extend(pointer_problems)
            if lo_data.get("status") == "pass":
                if not lo_data.get("evidence_refs"):
                    errors.append(f"PASS lane {artifact} requires evidence_refs")
                if is_scaffold_lane(lo_data):
                    errors.append(f"PASS lane {artifact} still looks like untouched scaffold")
            if is_scaffold_lane(lo_data):
                scaffold_lanes += 1
    completed = status == "verified" or any(isinstance(r, dict) and r.get("status") in COMPLETED_STATUSES for r in repos)
    ledger_payload = None
    if ledger.exists():
        try:
            ledger_payload = load_json(ledger)
        except Exception as exc:  # noqa: BLE001
            (errors if completed else warnings).append(f"invalid citation-ledger.json: {exc}")
    if ledger_payload is not None:
        validate_ledger_quotes(root, ledger_payload, strict=completed, errors=errors, warnings=warnings)
    (errors if completed else warnings).extend(lane_pointer_problems)
    if completed:
        validate_synthesis(root, mode, errors)
        if scaffold_lanes:
            errors.append("completed campaign/repo contains untouched scaffold lanes")
        if not validated_reports:
            errors.append("completed campaign/repo has no validated no-exec report")
    if errors:
        return "fail", errors, warnings
    partial_with_verified_repo = status == "partial" and any(
        isinstance(repo, dict) and repo.get("status") in COMPLETED_STATUSES for repo in repos
    )
    if scaffold_lanes or (status in SCAFFOLD_STATUSES and not partial_with_verified_repo):
        warnings.append("scaffold verified structurally only; campaign is not complete")
        return "scaffold", errors, warnings
    return "pass", errors, warnings


def self_test() -> None:
    import subprocess
    import sys

    valid_action_entry = {
        "action_id": "AR-1",
        "action_type": "watch_trigger",
        "title": "Watch for tagged release",
        "target_surfaces": ["candidate_ledger"],
        "recommendation": "watch_with_trigger",
        "why": "Useful but needs a release before deeper review.",
        "evidence_refs": ["E1"],
        "risk_notes": ["Unverified static-only signal."],
        "owner_gate": "none",
        "next_registration_step": "watch_ledger",
        "existing_tracker": None,
        "trigger_to_reopen": "new stable release",
        "confidence": 0.6,
        "registration_status": "generated_from_report",
    }
    base_report = {
        "status": "completed",
        "runner_policy": {"runner": "no-exec", "execution_allowed": False},
        "metrics": {"target_code_execution": False},
        "verdict": {"decision": "watch"},
        "action_register": [valid_action_entry],
    }
    ok, reason = validate_report_payload(base_report)
    assert ok, reason
    reject_report = dict(base_report, verdict={"decision": "reject"}, action_register=[])
    ok, reason = validate_report_payload(reject_report)
    assert ok, reason
    malformed_cases = [
        (dict(base_report, action_register=[{"garbage": True}]), "action_id"),
        (dict(base_report, action_register=[dict(valid_action_entry, action_type="garbage")]), "action_type"),
        (dict(base_report, action_register=[dict(valid_action_entry, target_surfaces=["ok", 12])]), "target_surfaces"),
        (dict(base_report, action_register=[dict(valid_action_entry, confidence=1.2)]), "confidence"),
    ]
    for bad_report, expected in malformed_cases:
        ok, reason = validate_report_payload(bad_report)
        assert not ok and reason and expected in reason, (bad_report, ok, reason)

    manifest_stub = {"campaign_id": "identity-campaign"}
    repo_stub = {
        "repo_url": "https://github.com/Example/Project.git",
        "repo_slug": "example_project",
        "run_id": "repo_run_expected",
        "root": "/tmp/identity-campaign/repos/example_project",
    }
    bound_report = dict(
        base_report,
        repo={"url": "https://github.com/example/project/", "name": "project"},
        campaign={
            "campaign_id": "identity-campaign",
            "repo_run_id": "repo_run_expected",
            "expected_root": "/tmp/identity-campaign/repos/example_project",
        },
    )
    assert validate_report_identity(manifest_stub, repo_stub, bound_report) is None
    wrong_repo = dict(bound_report, repo={"url": "https://github.com/example/other"})
    assert "repo identity mismatch" in str(validate_report_identity(manifest_stub, repo_stub, wrong_repo))
    wrong_campaign = dict(bound_report, campaign={**bound_report["campaign"], "campaign_id": "other-campaign"})
    assert "campaign_id" in str(validate_report_identity(manifest_stub, repo_stub, wrong_campaign))
    wrong_run = dict(bound_report, campaign={**bound_report["campaign"], "repo_run_id": "repo_run_other"})
    assert "repo_run_id" in str(validate_report_identity(manifest_stub, repo_stub, wrong_run))
    wrong_root = dict(bound_report, campaign={**bound_report["campaign"], "expected_root": "/tmp/identity-campaign_evil/repos/example_project"})
    assert "expected_root" in str(validate_report_identity(manifest_stub, repo_stub, wrong_root))

    skill_root = Path(__file__).resolve().parents[1]
    init = skill_root / "scripts" / "init_repo_scout_campaign.py"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "campaign"
        subprocess.run([sys.executable, str(init), "--repo", "https://github.com/example/project", "--mode", "standard", "--root", str(root), "--campaign-id", "verify-self-test", "--created-at", "2026-01-01T00:00:00Z"], check=True, stdout=subprocess.PIPE, text=True)
        status, errors, warnings = verify_campaign(root)
        assert status == "scaffold", (status, errors, warnings)
        lane = root / "repos" / "example_project" / "lanes" / "fit_verdict.json"
        data = load_json(lane)
        data["forbidden_actions_observed"] = []
        lane.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        status, errors, _ = verify_campaign(root)
        assert status == "fail" and any("no-target-execution" in e for e in errors), errors
        data["forbidden_actions_observed"] = ["no target repo code execution; scaffold only"]
        data["status"] = "pass"
        data["evidence_refs"] = ["E1"]
        lane.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        sidecar = load_json(root / "repos" / "example_project" / "repo-scout-sidecar.json")
        for lane_entry in sidecar["lanes"]:
            if lane_entry["name"] == "fit_verdict":
                lane_entry["status"] = "pass"
        (root / "repos" / "example_project" / "repo-scout-sidecar.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
        status, errors, _ = verify_campaign(root)
        assert status == "fail" and any("untouched scaffold" in e for e in errors), errors
        manifest = load_json(root / "manifest.json")
        manifest["repositories"][0]["root"] = str(Path(td) / "campaign2")
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        status, errors, _ = verify_campaign(root)
        assert status == "fail" and any("escapes campaign root" in e for e in errors), errors
    print("verify_repo_scout_campaign self-test: PASS")


def main() -> None:
    p = argparse.ArgumentParser(description="Verify an app-compatible repo-scout.campaign.v1 campaign scaffold/output")
    p.add_argument("campaign_root", nargs="?")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.campaign_root:
        raise SystemExit("campaign_root is required unless --self-test is used")
    status, errors, warnings = verify_campaign(Path(args.campaign_root).resolve())
    for warning in warnings:
        print(f"WARN: {warning}")
    if status == "pass":
        print("repo-scout campaign verification: PASS")
        return
    if status == "scaffold":
        print("repo-scout campaign verification: SCAFFOLD")
        return
    print("repo-scout campaign verification: FAIL")
    for error in errors:
        print(f"ERROR: {error}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

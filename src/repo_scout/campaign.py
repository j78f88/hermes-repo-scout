from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import ActionRegisterEntry, BuildGateState, Verdict

SCHEMA_CAMPAIGN_VERSION = "repo-scout.campaign.v1"
SCHEMA_LANE_OUTPUT_VERSION = "repo-scout.lane-output.v1"
SCHEMA_WORKER_PACKET_VERSION = "repo-scout.worker-packet.v1"

SourceClass = Literal["external_source_evidence", "internal_context", "fit_routing"]
Decision = Literal["adopt", "pilot", "watch", "skip", "route", "block"]
ConsumptionMode = Literal["none", "pattern", "translation", "sandbox", "fork", "integration"]
RepoStatus = Literal["initialized", "collector_done", "lanes_done", "synthesized", "verified", "blocked", "failed"]


class Mode(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    FULL = "full"
    REFRESH_DELTA = "refresh_delta"
    TRUE_DEEP_RESEARCH = "true_deep_research"


class ModeRoute(BaseModel):
    mode: Mode
    cli_baseline_required: bool = True
    lanes: tuple[str, ...]
    refresh_delta: bool = False
    deep_research_handoff: bool = False
    reason: str


MODE_LANES: dict[Mode, tuple[str, ...]] = {
    Mode.BRIEF: ("controller",),
    Mode.STANDARD: ("provenance_activity", "static_architecture", "risk_license", "fit_verdict"),
    Mode.FULL: (
        "provenance_activity",
        "static_architecture",
        "risk_license_supply_chain",
        "fit_overlap",
        "external_adoption_failure",
        "adversarial_refutation",
    ),
    Mode.REFRESH_DELTA: ("provenance_delta", "changed_evidence", "decision_delta"),
    Mode.TRUE_DEEP_RESEARCH: ("deep_research_handoff",),
}


def route_mode(mode: str | None) -> ModeRoute:
    try:
        selected = Mode(mode or Mode.STANDARD.value)
    except ValueError as exc:
        raise ValueError(f"unknown repo-scout mode: {mode}") from exc
    if selected == Mode.TRUE_DEEP_RESEARCH:
        return ModeRoute(
            mode=selected,
            cli_baseline_required=False,
            lanes=MODE_LANES[selected],
            deep_research_handoff=True,
            reason="explicit deep-research handoff; repo-scout does not relabel full scouts as deep research",
        )
    if selected == Mode.REFRESH_DELTA:
        return ModeRoute(
            mode=selected,
            lanes=MODE_LANES[selected],
            refresh_delta=True,
            reason="refresh_delta inspects material changes only",
        )
    return ModeRoute(mode=selected, lanes=MODE_LANES[selected], reason=f"deterministic {selected.value} repo-scout mode")


LEGACY_MAPPINGS: dict[str, tuple[Decision, ConsumptionMode, BuildGateState]] = {
    "adopt": ("adopt", "integration", BuildGateState.BUILD_ORIGINAL),
    "adapt": ("pilot", "translation", BuildGateState.SPIKE_ORIGINAL),
    "borrow": ("pilot", "translation", BuildGateState.SPIKE_ORIGINAL),
    "watch": ("watch", "pattern", BuildGateState.PEER_REVIEW),
    "investigate": ("watch", "pattern", BuildGateState.PEER_REVIEW),
    "reject": ("skip", "none", BuildGateState.DESCRIBE_ONLY),
    "no_verdict": ("skip", "none", BuildGateState.DESCRIBE_ONLY),
    "discard": ("skip", "none", BuildGateState.DESCRIBE_ONLY),
    "pattern-only": ("watch", "pattern", BuildGateState.PEER_REVIEW),
    "pattern_only": ("watch", "pattern", BuildGateState.PEER_REVIEW),
    "translation-candidate": ("pilot", "translation", BuildGateState.SPIKE_ORIGINAL),
    "translation_candidate": ("pilot", "translation", BuildGateState.SPIKE_ORIGINAL),
    "sandbox-trial": ("pilot", "sandbox", BuildGateState.SPIKE_ORIGINAL),
    "sandbox_trial": ("pilot", "sandbox", BuildGateState.SPIKE_ORIGINAL),
    "fork-candidate": ("route", "fork", BuildGateState.PEER_REVIEW),
    "fork_candidate": ("route", "fork", BuildGateState.PEER_REVIEW),
    "integrate-candidate": ("route", "integration", BuildGateState.PEER_REVIEW),
    "integrate_candidate": ("route", "integration", BuildGateState.PEER_REVIEW),
    "refresh-no-material-delta": ("skip", "none", BuildGateState.DESCRIBE_ONLY),
    "refresh_no_material_delta": ("skip", "none", BuildGateState.DESCRIBE_ONLY),
    "blocked-needs-owner": ("block", "none", BuildGateState.BLOCKED_NEEDS_OWNER),
    "blocked_needs_owner": ("block", "none", BuildGateState.BLOCKED_NEEDS_OWNER),
}


class CanonicalDecision(BaseModel):
    decision: Decision
    consumption_mode: ConsumptionMode
    build_gate: BuildGateState
    collector_verdict: str
    owner_gate: str = "none"
    mapping_source: str = "legacy_collector_verdict"

    @field_validator("decision", "consumption_mode", "collector_verdict", "owner_gate", mode="before")
    @classmethod
    def normalize_lower_token(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_").replace("-", "_")
        return value

    @field_validator("build_gate", mode="before")
    @classmethod
    def normalize_build_gate(cls, value: Any) -> Any:
        if isinstance(value, BuildGateState):
            return value
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_").replace("-", "_")
        return value

    @model_validator(mode="after")
    def validate_consistency(self):
        # Valid matrix:
        # - skip: no consumption, describe-only gate.
        # - watch: pattern/no consumption, describe-only or council-review gate.
        # - pilot: translation/sandbox only, original spike gate.
        # - route: pattern/fork only, council-review gate.
        # - adopt: integration only, original-build gate.
        # - block: no consumption, blocked gate, explicit owner gate.
        if self.collector_verdict in {"reject", "discard"} and self.decision in {"adopt", "pilot", "route"}:
            raise ValueError("collector_verdict conflicts with positive controller decision")
        if self.build_gate == BuildGateState.BLOCKED_NEEDS_OWNER and self.decision != "block":
            raise ValueError("blocked build_gate conflicts with non-block decision")
        if self.decision == "skip":
            if self.consumption_mode != "none" or self.build_gate != BuildGateState.DESCRIBE_ONLY:
                raise ValueError("decision skip conflicts with consumption_mode/build_gate")
        elif self.decision == "watch":
            if self.consumption_mode not in {"none", "pattern"} or self.build_gate not in {BuildGateState.DESCRIBE_ONLY, BuildGateState.PEER_REVIEW}:
                raise ValueError("decision watch conflicts with consumption_mode/build_gate")
        elif self.decision == "pilot":
            if self.consumption_mode not in {"translation", "sandbox"} or self.build_gate != BuildGateState.SPIKE_ORIGINAL:
                raise ValueError("decision pilot conflicts with consumption_mode/build_gate")
        elif self.decision == "route":
            if self.consumption_mode not in {"pattern", "fork"} or self.build_gate != BuildGateState.PEER_REVIEW:
                raise ValueError("decision route conflicts with consumption_mode/build_gate")
        elif self.decision == "adopt":
            if self.consumption_mode != "integration" or self.build_gate != BuildGateState.BUILD_ORIGINAL:
                raise ValueError("decision adopt conflicts with consumption_mode/build_gate")
        elif self.decision == "block":
            if self.consumption_mode != "none" or self.build_gate != BuildGateState.BLOCKED_NEEDS_OWNER:
                raise ValueError("decision block conflicts with consumption_mode/build_gate")
            if not self.owner_gate or self.owner_gate == "none":
                raise ValueError("decision block requires owner_gate")
        return self


def canonicalize_decision(
    *,
    collector_verdict: str | Verdict,
    decision: str | None = None,
    consumption_mode: str | None = None,
    build_gate: str | BuildGateState | None = None,
    owner_gate: str = "none",
) -> CanonicalDecision:
    native = collector_verdict.value if isinstance(collector_verdict, Verdict) else str(collector_verdict)
    key = native.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in LEGACY_MAPPINGS:
        raise ValueError(f"unknown collector_verdict: {collector_verdict}")
    mapped_decision, mapped_mode, mapped_gate = LEGACY_MAPPINGS[key]
    if owner_gate and owner_gate != "none":
        mapped_decision, mapped_mode, mapped_gate = "block", "none", BuildGateState.BLOCKED_NEEDS_OWNER
    return CanonicalDecision(
        decision=decision or mapped_decision,  # type: ignore[arg-type]
        consumption_mode=consumption_mode or mapped_mode,  # type: ignore[arg-type]
        build_gate=build_gate or mapped_gate,
        collector_verdict=key,
        owner_gate=owner_gate or "none",
        mapping_source="owner_gate" if owner_gate and owner_gate != "none" else "legacy_collector_verdict",
    )


class WorkerPacket(BaseModel):
    schema_version: str = SCHEMA_WORKER_PACKET_VERSION
    lane_id: str
    repo_url: str
    pinned_commit: str | None = None
    campaign_root: Path
    output_path: Path
    source_as_untrusted_data: bool = True
    source_classes: list[SourceClass]
    forbidden_actions: list[str]
    evidence_classes: list[str]
    allowed_mutations: list[str] = Field(default_factory=lambda: ["write lane artifact to declared output_path only"])
    required_sections: list[str] = Field(default_factory=lambda: ["findings", "evidence_refs", "gaps", "forbidden_action_declaration"])

    @field_validator("campaign_root", "output_path", mode="before")
    @classmethod
    def expand_path(cls, value: Any) -> Path:
        return Path(value).expanduser().resolve()

    @model_validator(mode="after")
    def validate_packet(self):
        classes = set(self.source_classes)
        if "external_source_evidence" not in classes:
            raise ValueError("source_classes must include external_source_evidence")
        if not classes.intersection({"internal_context", "fit_routing"}):
            raise ValueError("source_classes must include internal_context or fit_routing")
        if not self.forbidden_actions or not any("execution" in action.lower() for action in self.forbidden_actions):
            raise ValueError("forbidden_actions must include target execution prohibition")
        if not _is_relative_to(self.output_path, self.campaign_root):
            raise ValueError("output_path must be inside campaign_root")
        return self


class LaneOutput(BaseModel):
    schema_version: str = SCHEMA_LANE_OUTPUT_VERSION
    lane_id: str
    status: Literal["pass", "hold", "fail"]
    output_path: Path
    sources_used: list[str]
    source_classes: list[SourceClass]
    forbidden_actions_observed: list[str]
    evidence_refs: list[str]
    gaps: list[str] = Field(default_factory=list)
    findings: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_output(self):
        if not self.sources_used:
            raise ValueError("sources_used must not be empty")
        if not self.source_classes:
            raise ValueError("source_classes must not be empty")
        if not self.forbidden_actions_observed or not any("target repo code execution" in item.lower() for item in self.forbidden_actions_observed):
            raise ValueError("forbidden_actions_observed must declare no target repo code execution")
        if self.status == "pass" and not self.evidence_refs:
            raise ValueError("PASS lane outputs require evidence_refs")
        return self


class CampaignLock(BaseModel):
    owner: str
    pid: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=24))


class CampaignCheckpoint(BaseModel):
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    details: dict[str, Any] = Field(default_factory=dict)


class RepositoryRun(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    repo_url: str
    repo_slug: str
    run_id: str
    root: Path
    status: RepoStatus = "initialized"
    report_path: Path | None = None
    validated: bool = False
    error: str | None = None
    freshness: dict[str, Any] = Field(default_factory=dict)
    retention: dict[str, Any] = Field(default_factory=lambda: {"snapshot_retained": False, "cleanup_status": "not_started"})

    @field_validator("root", "report_path", mode="before")
    @classmethod
    def expand_optional_path(cls, value: Any) -> Path | None:
        if value is None:
            return None
        return Path(value).expanduser().resolve()


class CampaignManifest(BaseModel):
    schema_version: str = SCHEMA_CAMPAIGN_VERSION
    campaign_id: str
    owner_issue: str = "none"
    mode: Mode = Mode.STANDARD
    status: Literal["initialized", "running", "partial", "verified", "failed", "blocked"] = "initialized"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    root: Path
    scout_controller: str
    candidate_owner: str
    verifier: str
    next_review_at: datetime
    routing_surface: str = "repo-scout application"
    owner_gate: str = "none"
    max_concurrency: int = Field(default=3, ge=1, le=3)
    partial_success_allowed: bool = True
    lock: CampaignLock | None = None
    baseline: dict[str, str] = Field(default_factory=lambda: {
        "doctor_command": "repo-scout doctor",
        "collector_command_shape": "repo-scout scout <url> --workspace <repo-root>/collector --out <repo-root>/collector/report --format both",
        "runner": "no-exec",
    })
    canonical_fields: tuple[str, str, str, str] = ("decision", "consumption_mode", "build_gate", "collector_verdict")
    repositories: list[RepositoryRun]
    checkpoints: list[CampaignCheckpoint] = Field(default_factory=list)

    @field_validator("root", mode="before")
    @classmethod
    def expand_root(cls, value: Any) -> Path:
        return Path(value).expanduser().resolve()

    @property
    def path(self) -> Path:
        return self.root / "manifest.json"

    @model_validator(mode="after")
    def validate_manifest_containment_and_baseline(self):
        for repo in self.repositories:
            if not _is_relative_to(repo.root, self.root):
                raise ValueError(f"repository root for {repo.repo_slug} must stay under campaign root")
        return self

    def checkpoint(self, name: str, **details: Any) -> None:
        self.checkpoints.append(CampaignCheckpoint(name=name, details=details))
        self.updated_at = datetime.now(timezone.utc).replace(microsecond=0)

    def write_atomic(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json")
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)
        if self.lock:
            lock_tmp = self.root / "lock.json.tmp"
            lock_tmp.write_text(json.dumps(self.lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(lock_tmp, self.root / "lock.json")
        return self.path


class VerificationResult(BaseModel):
    passed: bool
    partial_success: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validated_report_paths: list[Path] = Field(default_factory=list)
    excluded_report_paths: list[Path] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class CampaignInitializer:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def initialize(
        self,
        *,
        repos: list[str],
        mode: str | None = None,
        scout_controller: str,
        verifier: str,
        candidate_owner: str,
        campaign_id: str | None = None,
        max_concurrency: int = 3,
        resume: bool = False,
        force_lock: bool = False,
    ) -> CampaignManifest:
        if max_concurrency > 3:
            raise ValueError("max_concurrency must be <= 3")
        route = route_mode(mode)
        cid = validate_path_component(campaign_id or _stable_campaign_id(repos, route.mode.value), label="campaign_id")
        campaign_root = self.root / cid
        if not _is_relative_to(campaign_root, self.root):
            raise ValueError("campaign_id must resolve under configured campaign root")
        manifest_path = campaign_root / "manifest.json"
        if resume and manifest_path.exists():
            return load_manifest(manifest_path)
        existing_manifest = load_manifest(manifest_path) if manifest_path.exists() else None
        existing_lock = existing_manifest.lock if existing_manifest else None
        now = datetime.now(timezone.utc).replace(microsecond=0)
        lock_owner_changed = existing_lock is not None and existing_lock.owner != scout_controller
        lock_expired = existing_lock is not None and existing_lock.expires_at <= now
        lock_takeover = bool(lock_owner_changed and (force_lock or lock_expired))
        if lock_owner_changed and not force_lock and not lock_expired:
            raise ValueError(f"active campaign lock is owned by {existing_lock.owner}; use force_lock for explicit takeover")
        repositories = []
        for repo_url in repos:
            slug = repo_slug(repo_url)
            run_id = _stable_run_id(cid, repo_url)
            repo_root = campaign_root / "repos" / slug
            if not _is_relative_to(repo_root, campaign_root):
                raise ValueError(f"repo slug for {repo_url} must resolve under campaign root")
            repo_root.mkdir(parents=True, exist_ok=True)
            for name in ("collector", "lanes", "receipts", "synthesis"):
                (repo_root / name).mkdir(exist_ok=True)
            repositories.append(RepositoryRun(repo_url=repo_url, repo_slug=slug, run_id=run_id, root=repo_root))
        manifest = CampaignManifest(
            campaign_id=cid,
            mode=route.mode,
            root=campaign_root,
            scout_controller=scout_controller,
            candidate_owner=candidate_owner,
            verifier=verifier,
            next_review_at=datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30),
            max_concurrency=max_concurrency,
            lock=CampaignLock(owner=scout_controller, pid=os.getpid()),
            repositories=repositories,
        )
        manifest.checkpoint("campaign_initialized", repo_count=len(repositories), mode=route.mode.value)
        if lock_takeover:
            manifest.checkpoint(
                "lock_taken_over",
                previous_owner=existing_lock.owner,
                previous_pid=existing_lock.pid,
                previous_created_at=existing_lock.created_at.isoformat(),
                new_owner=scout_controller,
                new_pid=os.getpid(),
            )
        manifest.write_atomic()
        return manifest


class CampaignVerifier:
    def __init__(self, manifest_path: Path):
        self.manifest_path = Path(manifest_path).expanduser().resolve()

    def verify(self) -> VerificationResult:
        errors: list[str] = []
        warnings: list[str] = []
        validated: list[Path] = []
        excluded: list[Path] = []
        failed_repositories = 0
        try:
            manifest = load_manifest(self.manifest_path)
        except Exception as exc:
            return VerificationResult(
                passed=False,
                errors=[str(exc)],
                summary={
                    "repositories": 0,
                    "validated_reports": 0,
                    "excluded_reports": 0,
                    "failed_repositories": 0,
                    "errors": 1,
                    "warnings": 0,
                },
            )
        if manifest.schema_version != SCHEMA_CAMPAIGN_VERSION:
            errors.append(f"unsupported schema_version: {manifest.schema_version}")
        if manifest.max_concurrency > 3:
            errors.append("max_concurrency exceeds default safety bound of 3")
        if not _is_relative_to(self.manifest_path, manifest.root):
            errors.append("manifest path must stay under campaign root")
        if not _baseline_is_no_exec(manifest.baseline):
            errors.append("campaign baseline must preserve no-exec runner contract")
        if manifest.lock is None:
            errors.append("missing lock/owner record")
        if not manifest.checkpoints:
            errors.append("missing atomic checkpoints")
        for repo in manifest.repositories:
            repo_root = Path(repo.root)
            if not _is_relative_to(repo_root, manifest.root):
                errors.append(f"repository root must stay under campaign root: {repo.repo_slug}")
            if not repo_root.exists():
                errors.append(f"repository root missing: {repo.repo_slug}")
            if repo.status == "failed":
                failed_repositories += 1
            if not repo.report_path:
                warnings.append(f"repository has no report yet: {repo.repo_slug}")
                continue
            report_path = Path(repo.report_path)
            if not _is_relative_to(report_path, manifest.root):
                errors.append(f"report_path must stay under campaign root for {repo.repo_slug}: {report_path}")
                excluded.append(report_path)
                continue
            if not report_path.exists():
                errors.append(f"report_path missing for {repo.repo_slug}: {report_path}")
                excluded.append(report_path)
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                _validate_report_payload(payload)
                _validate_report_identity(manifest, repo, payload)
            except Exception as exc:
                errors.append(f"{repo.repo_slug}: {exc}")
                excluded.append(report_path)
                continue
            validated.append(report_path)
        passed = not errors and bool(validated or not manifest.repositories)
        partial_success = bool(validated and (errors or failed_repositories))
        return VerificationResult(
            passed=passed,
            partial_success=partial_success,
            errors=errors,
            warnings=warnings,
            validated_report_paths=validated,
            excluded_report_paths=excluded,
            summary={
                "repositories": len(manifest.repositories),
                "validated_reports": len(validated),
                "excluded_reports": len(excluded),
                "failed_repositories": failed_repositories,
                "errors": len(errors),
                "warnings": len(warnings),
            },
        )

    @staticmethod
    def validate_report_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
        return validate_report_payload(payload)

    @staticmethod
    def validate_fixture_scenario(case: dict[str, Any]) -> VerificationResult:
        errors: list[str] = []
        owner_gate = "none"
        blocked_count = 0
        routed_duplicates = 0
        skipped_no_fit = 0
        refresh_no_material_delta = 0
        resume_required = 0
        failed_repositories = 0
        if case.get("prompt_injection"):
            owner_gate = "governance"
            blocked_count += 1
        if case.get("license_status") == "ambiguous":
            owner_gate = "governance"
            blocked_count += 1
        if case.get("backlog_collision") == "duplicate_existing_issue":
            routed_duplicates += 1
        if case.get("fit") == "no":
            skipped_no_fit += 1
        if case.get("material_delta") is False:
            refresh_no_material_delta += 1
        if case.get("resume_expected"):
            resume_required += 1
        if case.get("repository_status") == "partial_failure":
            failed_repositories += 1
        try:
            route_mode(case.get("mode"))
            canonicalize_decision(
                collector_verdict=case["collector_verdict"],
                decision=case.get("expected_decision"),
                consumption_mode=case.get("expected_consumption_mode"),
                build_gate=case.get("expected_build_gate"),
                owner_gate=owner_gate,
            )
        except Exception as exc:
            if case.get("report_status") != "conflict_fail_closed":
                errors.append(str(exc))
            else:
                errors.append(f"conflict failed closed: {exc}")
        if case.get("prompt_injection") and case.get("expected_build_gate") != "blocked_needs_owner":
            errors.append("malicious instruction scenario must fail closed to blocked_needs_owner")
        if case.get("license_status") == "ambiguous" and case.get("expected_decision") != "block":
            errors.append("ambiguous license scenario must block")
        if case.get("backlog_collision") == "duplicate_existing_issue" and case.get("expected_decision") != "route":
            errors.append("duplicate backlog scenario must route")
        if case.get("material_delta") is False and case.get("mode") != "refresh_delta":
            errors.append("no-delta scenario must use refresh_delta")
        return VerificationResult(
            passed=not errors,
            partial_success=case.get("report_status") == "partial_success",
            errors=errors,
            summary={
                "errors": len(errors),
                "blocked_repositories": blocked_count,
                "routed_duplicates": routed_duplicates,
                "skipped_no_fit": skipped_no_fit,
                "refresh_no_material_delta": refresh_no_material_delta,
                "resume_required": resume_required,
                "failed_repositories": failed_repositories,
            },
        )

def load_manifest(path: Path) -> CampaignManifest:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = CampaignManifest.model_validate(payload)
    if manifest.path.resolve() != manifest_path:
        raise ValueError("manifest path must equal campaign root manifest.json")
    return manifest


def validate_path_component(value: str, *, label: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} must not be empty")
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must be a local path component, not a URL or URL fragment")
    path = Path(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or len(path.parts) != 1:
        raise ValueError(f"{label} must be a single safe path component")
    return raw


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
    except ValueError:
        return False
    return True


def _baseline_is_no_exec(baseline: dict[str, Any]) -> bool:
    runner = str(baseline.get("runner") or "").strip().lower()
    return runner == "no-exec" and "repo-scout scout" in str(baseline.get("collector_command_shape") or "")


def repo_slug(repo_url: str) -> str:
    parsed = urlsplit(repo_url)
    value = (parsed.path if parsed.scheme or parsed.netloc else repo_url).rstrip("/").removesuffix(".git")
    parts = [part for part in value.split("/") if part]
    if len(parts) >= 2:
        raw = "__".join(parts[-2:])
    else:
        raw = value
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw)
    return "_".join(part for part in slug.split("_") if part) or "repo"


def _stable_campaign_id(repos: list[str], mode: str) -> str:
    return "campaign_" + hashlib.sha1((mode + "|" + "|".join(sorted(repos))).encode()).hexdigest()[:12]


def _stable_run_id(campaign_id: str, repo_url: str) -> str:
    return "repo_run_" + hashlib.sha1((campaign_id + "|" + repo_url).encode()).hexdigest()[:12]


def _validate_report_payload(payload: dict[str, Any]) -> None:
    valid, reason = validate_report_payload(payload)
    if not valid:
        raise ValueError(reason or "invalid report")


def _validate_report_identity(manifest: CampaignManifest, repo: RepositoryRun, payload: dict[str, Any]) -> None:
    manifest_identities = _repo_identity_candidates({"url": repo.repo_url})
    raw_report_repo = payload.get("repo")
    report_repo: dict[str, Any] = raw_report_repo if isinstance(raw_report_repo, dict) else {}
    report_identities = _repo_identity_candidates(report_repo)
    if not manifest_identities:
        raise ValueError(f"repo identity unavailable for manifest entry {repo.repo_slug}: {repo.repo_url}")
    if not report_identities:
        raise ValueError(f"repo identity unavailable in report for manifest entry {repo.repo_slug}")
    if manifest_identities.isdisjoint(report_identities):
        report_label = report_repo.get("url") or report_repo.get("path") or report_repo.get("name") or "unknown"
        raise ValueError(f"repo identity mismatch for {repo.repo_slug}: manifest {repo.repo_url} != report {report_label}")

    campaign_binding = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
    explicit_campaign_id = campaign_binding.get("campaign_id") or payload.get("campaign_id")
    if explicit_campaign_id is not None and str(explicit_campaign_id) != manifest.campaign_id:
        raise ValueError(f"repo identity mismatch for {repo.repo_slug}: report campaign_id {explicit_campaign_id} != manifest {manifest.campaign_id}")
    explicit_repo_run_id = (
        campaign_binding.get("repo_run_id")
        or campaign_binding.get("manifest_run_id")
        or payload.get("repo_run_id")
        or payload.get("manifest_run_id")
    )
    if explicit_repo_run_id is not None and str(explicit_repo_run_id) != repo.run_id:
        raise ValueError(f"repo identity mismatch for {repo.repo_slug}: report repo_run_id {explicit_repo_run_id} != manifest {repo.run_id}")
    expected_root = campaign_binding.get("expected_root") or payload.get("expected_root")
    if expected_root is not None and _canonical_local_path(str(expected_root)) != _canonical_local_path(str(repo.root)):
        raise ValueError(f"repo identity mismatch for {repo.repo_slug}: report expected_root {expected_root} != manifest {repo.root}")


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
        normalized_name = name.strip().removesuffix(".git").lower()
        identities.add(("github", f"{owner.strip().lower()}/{normalized_name}"))
    return identities


def _canonical_repo_identity(value: str) -> tuple[str, str] | None:
    raw = value.strip()
    if not raw:
        return None
    ssh_match = re.fullmatch(r"git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?", raw)
    if ssh_match:
        return ("github", f"{ssh_match.group('owner').lower()}/{ssh_match.group('name').removesuffix('.git').lower()}")
    parsed = urlsplit(raw)
    host = parsed.netloc.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if host == "github.com" and parsed.scheme in {"http", "https", "ssh", "git"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            owner = parts[0].lower()
            name = parts[1].removesuffix(".git").lower()
            return ("github", f"{owner}/{name}")
    if parsed.scheme == "file":
        return ("local", _canonical_local_path(unquote(parsed.path)))
    if not parsed.scheme and not parsed.netloc:
        return ("local", _canonical_local_path(raw))
    return None


def _canonical_local_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def validate_report_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    if payload.get("status") in {"failed", "blocked", "vetoed"}:
        return False, "failed/blocked reports are excluded from comparison"
    runner_policy_value = payload.get("runner_policy")
    runner_policy: dict[str, Any] = runner_policy_value if isinstance(runner_policy_value, dict) else {}
    if runner_policy.get("execution_allowed") is True:
        return False, "report runner_policy.execution_allowed=true"
    schema_explicit = isinstance(payload.get("canonical_decision"), dict)
    new_report = schema_explicit or isinstance(payload.get("action_register"), list) or isinstance(payload.get("campaign"), dict) or any(
        key in payload for key in ("campaign_id", "repo_run_id", "manifest_run_id")
    )
    if new_report and isinstance(runner_policy_value, dict) and str(runner_policy.get("runner") or "").strip().lower() != "no-exec":
        return False, "report runner_policy.runner must be no-exec"
    metrics_value = payload.get("metrics")
    metrics: dict[str, Any] = metrics_value if isinstance(metrics_value, dict) else {}
    if metrics.get("target_code_execution") is True:
        return False, "report proves target_code_execution=true"
    verdict = str(((payload.get("verdict") or {}).get("decision") or payload.get("collector_verdict") or "no_verdict"))
    explicit = payload.get("canonical_decision") if isinstance(payload.get("canonical_decision"), dict) else None
    try:
        if explicit:
            canonicalize_decision(
                collector_verdict=verdict,
                decision=explicit.get("decision"),
                consumption_mode=explicit.get("consumption_mode"),
                build_gate=explicit.get("build_gate"),
                owner_gate=explicit.get("owner_gate", "none"),
            )
        else:
            canonicalize_decision(collector_verdict=verdict)
    except Exception as exc:
        return False, str(exc)
    raw_action_register = payload.get("action_register")
    reject_verdicts = {"reject", "no_verdict", "discard"}
    current_non_reject = schema_explicit and verdict.strip().lower().replace(" ", "_").replace("-", "_") not in reject_verdicts
    if current_non_reject:
        if not isinstance(raw_action_register, list) or len(raw_action_register) == 0:
            return False, "non-reject report missing ActionRegister"
        try:
            for entry in raw_action_register:
                ActionRegisterEntry.model_validate(entry)
        except Exception as exc:
            return False, f"invalid ActionRegister entry: {exc}"
    return True, None

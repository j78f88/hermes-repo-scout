from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .taxonomy import TaxonomyTagged

SCHEMA_REPORT_VERSION = "repo-scout.report.v1"
SCHEMA_PLAN_VERSION = "repo-scout.plan.v1"
CORE_CONTRACT_FORBIDDEN_TERMS = ("hermes", "example-platform", "/home/", "/users/", "c:\\", "file://")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Verdict(str, Enum):
    ADOPT = "adopt"
    ADAPT = "adapt"
    BORROW = "borrow"
    WATCH = "watch"
    REJECT = "reject"
    INVESTIGATE = "investigate"
    NO_VERDICT = "no_verdict"


class SourceTrustTier(str, Enum):
    TRUSTED_VENDOR_OR_OFFICIAL = "trusted_vendor_or_official"
    KNOWN_HIGH_QUALITY = "known_high_quality"
    ORDINARY_OSS = "ordinary_oss"
    UNKNOWN_UNTRUSTED = "unknown_untrusted"
    OWNER_APPROVED_DIRECT_CONSUME = "owner_approved_direct_consume"


class BuildGateState(str, Enum):
    DESCRIBE_ONLY = "describe_only"
    PEER_REVIEW = "peer_review"
    SPIKE_ORIGINAL = "spike_original"
    BUILD_ORIGINAL = "build_original"
    BLOCKED_NEEDS_OWNER = "blocked_needs_owner"


class DescribeBuildGate(BaseModel):
    source_tier: SourceTrustTier = SourceTrustTier.UNKNOWN_UNTRUSTED
    state: BuildGateState = BuildGateState.DESCRIBE_ONLY
    can_direct_consume: bool = False
    requires_peer_review: bool = True
    original_implementation_only: bool = True
    reasons: list[str] = Field(default_factory=list)
    risk_finding_refs: list[str] = Field(default_factory=list)


class Locator(BaseModel):
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None


class SourceRef(BaseModel):
    type: Literal["file", "metadata", "inferred", "user_goal", "profile"]
    uri: str
    locator: Locator | None = None


class RepoSnapshot(BaseModel):
    url: str
    path: str | None = None
    owner: str | None = None
    name: str
    ref: str | None = None
    commit: str | None = None
    checkout_hash: str | None = None
    files_scanned: int = 0
    bytes_scanned: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)
    ignored_paths: list[str] = Field(default_factory=list)
    ignored_paths_total: int = 0
    ignored_paths_truncated: bool = False
    ignored_path_details: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class Claim(TaxonomyTagged):
    id: str
    kind: Literal[
        "install",
        "capability",
        "compatibility",
        "architecture",
        "performance",
        "security",
        "maintenance",
        "quality",
        "support",
        "profile_fit",
        "inference",
    ]
    text: str
    source: SourceRef
    normalized_subject: str | None = None
    expected_verification: str | None = None
    importance: Literal["critical", "high", "medium", "low"] = "medium"
    status: Literal[
        "unverified",
        "supported",
        "verified",
        "partial",
        "contradicted",
        "not_tested",
        "not_applicable",
    ] = "not_tested"
    evidence_refs: list[str] = Field(default_factory=list)
    contradiction_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class Evidence(TaxonomyTagged):
    id: str
    type: Literal[
        "file_excerpt",
        "file_presence",
        "manifest",
        "metadata",
        "code_symbol",
        "config",
        "ci",
        "license",
        "scanner_result",
        "artifact",
        "llm_observation",
    ]
    source_uri: str
    locator: Locator | None = None
    content_hash: str | None = None
    excerpt: str | None = None
    summary: str
    collected_by: str
    collected_at: datetime = Field(default_factory=utc_now)
    strength: Literal["direct", "strong", "moderate", "weak", "none", "contradicting"] = "weak"
    supports: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProbeStep(BaseModel):
    id: str
    kind: Literal["install", "build", "test", "run", "lint", "command", "http_check", "static_check"]
    description: str
    command: str | None = None
    working_dir: str = "."
    requires_execution: bool = False
    required_runner: Literal["docker", "podman", "local", "no-exec"] = "no-exec"
    network: Literal["none", "metadata", "package-index", "repo-defined"] = "none"
    timeout_seconds: int = 300
    env: dict[str, str] = Field(default_factory=dict)
    claim_refs: list[str] = Field(default_factory=list)
    safety_review: dict[str, Any] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    id: str
    probe_step_id: str
    status: Literal["passed", "failed", "skipped", "blocked", "timeout", "unsafe", "not_run"]
    runner: dict[str, Any]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    failure_summary: str | None = None


class Finding(TaxonomyTagged):
    id: str
    title: str
    category: Literal[
        "quickstart",
        "architecture",
        "profile_fit",
        "risk",
        "maintenance",
        "security",
        "license",
        "dependency",
        "docs",
        "recommendation",
    ]
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    impact: Literal["adopt", "adapt", "avoid", "watch", "investigate", "none"] = "none"
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    probe_result_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    conflicts: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class IdeaCandidate(TaxonomyTagged):
    id: str
    kind: Literal["idea", "pattern", "integration_surface", "risk", "watch"]
    title: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_claim_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    suggested_action: Literal["borrow", "adapt", "watch", "reject", "investigate"] = "investigate"


class NeutralContractModel(BaseModel):
    """Base for reusable architecture contracts.

    These contracts are durable handoff artifacts, so they intentionally avoid
    local workspace paths and project-specific application names. Evidence IDs
    point back to the report for provenance without embedding raw paths.
    """

    @model_validator(mode="after")
    def reject_local_or_project_specific_leaks(self):
        payload = self.model_dump_json().lower()
        leaked = [term for term in CORE_CONTRACT_FORBIDDEN_TERMS if term in payload]
        if leaked:
            raise ValueError(f"core reusable contract contains non-neutral term(s): {', '.join(leaked)}")
        return self


class IdeaCard(NeutralContractModel, TaxonomyTagged):
    id: str
    purpose: str
    function: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_tier: Literal["repo_static", "readme_claim", "manifest_static", "inferred_static"] = "repo_static"
    risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    pattern: str | None = None
    suggested_action: Literal["borrow", "adapt", "watch", "reject", "investigate"] = "investigate"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class Dossier(NeutralContractModel, TaxonomyTagged):
    repo_purpose: str
    repo_function: str
    evidence_basis: list[dict[str, Any]] = Field(default_factory=list)
    useful_ideas_patterns: list[dict[str, Any]] = Field(default_factory=list)
    risks_breakpoints: list[str] = Field(default_factory=list)
    existing_system_fit: str = "No existing-system adapter selected; evaluate through neutral adapter seams."
    recommendation: Literal["adopt", "adapt", "borrow", "watch", "reject", "investigate", "no_verdict"] = "investigate"
    next_action: str = "Review evidence and decide whether to create an original build brief."
    describe_build_gate: dict[str, Any] = Field(default_factory=dict)


class BuildBrief(NeutralContractModel, TaxonomyTagged):
    original_implementation_goal: str
    describe_build_gate: dict[str, Any] = Field(default_factory=dict)
    reusable_api_contract: list[str] = Field(default_factory=list)
    reusable_cli_contract: list[str] = Field(default_factory=list)
    reusable_service_contract: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    test_plan: list[str] = Field(default_factory=list)
    security_model: list[str] = Field(default_factory=list)
    vertical_adapter_seams: list[str] = Field(default_factory=list)
    source_idea_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class PackageContract(NeutralContractModel, TaxonomyTagged):
    install_config: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    secrets_boundary: str = "No secrets in source, fixtures, logs, or generated artifacts; load credentials only via caller-owned runtime configuration."
    docs: list[str] = Field(default_factory=list)
    fixtures: list[str] = Field(default_factory=list)
    ci: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    adapter_points: list[str] = Field(default_factory=list)


class ActionRegisterEntry(BaseModel):
    """Durable action state for a non-reject repo-scout recommendation.

    SourceCards and reports are findings. ActionRegister entries preserve what
    should happen next so useful recommendations do not live only in prose.
    They are routing/registration records, not approval to build or consume
    upstream source.
    """

    action_id: str
    action_type: Literal[
        "buildbrief_candidate",
        "watch_trigger",
        "close",
        "rescout",
        "existing_parent_update",
        "candidate_ledger",
    ]
    title: str
    target_surfaces: list[str] = Field(default_factory=list)
    recommendation: Literal[
        "promote_to_candidate",
        "accepted_capability",
        "watch_with_trigger",
        "reject",
        "defer",
        "investigate",
    ]
    why: str
    evidence_refs: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    owner_gate: Literal[
        "none",
        "credentials",
        "spend",
        "public_exposure",
        "destructive",
        "governance",
        "business_direction",
        "unknown",
    ] = "none"
    next_registration_step: Literal[
        "candidate_ledger",
        "tracker_parent",
        "update_existing_issue",
        "watch_ledger",
        "no_action",
        "rescout",
    ]
    existing_tracker: str | None = None
    trigger_to_reopen: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    registration_status: Literal[
        "generated_from_report",
        "registered",
        "registered_watch",
        "registered_existing_tracker",
        "fail_closed",
    ] = "generated_from_report"


NON_REJECT_VERDICTS = {Verdict.ADOPT, Verdict.ADAPT, Verdict.BORROW, Verdict.WATCH, Verdict.INVESTIGATE}


class VerdictRecord(BaseModel):
    id: str = "V1"
    goal: str | None = None
    decision: Verdict = Verdict.INVESTIGATE
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    summary: str
    rationale_refs: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class RunnerPolicy(BaseModel):
    runner: Literal["no-exec", "local", "docker", "podman", "remote"] = "no-exec"
    allow_network: Literal["none", "metadata", "package-index", "repo-defined"] = "metadata"
    allow_write: Literal["none", "workspace", "sandbox"] = "workspace"
    execution_allowed: bool = False
    max_runtime_seconds: int = 900
    max_lanes: int = 4

    @field_validator("execution_allowed")
    @classmethod
    def no_exec_must_not_execute(cls, value: bool, info: Any) -> bool:
        runner = info.data.get("runner")
        if runner == "no-exec" and value:
            raise ValueError("runner=no-exec cannot allow execution")
        return value


class NormalizedGoal(BaseModel):
    intent: str
    question: str
    expected_answer_type: str = "recommendation"


class ResearchPlan(BaseModel):
    schema_version: str = SCHEMA_PLAN_VERSION
    plan_id: str
    created_at: datetime = Field(default_factory=utc_now)
    repo: RepoSnapshot
    raw_goal: str | None = None
    normalized_goal: NormalizedGoal | None = None
    lens: dict[str, Any] = Field(default_factory=dict)
    claim_targets: list[dict[str, Any]] = Field(default_factory=list)
    evidence_needs: list[str] = Field(default_factory=list)
    analyzers: list[str] = Field(default_factory=list)
    runner_policy: RunnerPolicy = Field(default_factory=RunnerPolicy)
    describe_build_gate: DescribeBuildGate = Field(default_factory=DescribeBuildGate)
    output_route: list[str] = Field(default_factory=lambda: ["markdown", "json"])
    lanes: list[str] = Field(default_factory=list)
    stop_conditions: list[dict[str, Any]] = Field(default_factory=list)


class Report(BaseModel):
    schema_version: str = SCHEMA_REPORT_VERSION
    run_id: str
    plan_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    generated_by: dict[str, Any]
    status: Literal["completed", "partial", "failed", "blocked", "vetoed"] = "completed"
    repo: RepoSnapshot
    goal: dict[str, Any] = Field(default_factory=dict)
    runner_policy: RunnerPolicy = Field(default_factory=RunnerPolicy)
    describe_build_gate: DescribeBuildGate = Field(default_factory=DescribeBuildGate)
    research_plan_ref: str | None = None
    summary: str
    verdict: VerdictRecord
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    probe_steps: list[ProbeStep] = Field(default_factory=list)
    probe_results: list[ProbeResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    idea_candidates: list[IdeaCandidate] = Field(default_factory=list)
    idea_cards: list[IdeaCard] = Field(default_factory=list)
    dossier: Dossier | None = None
    build_brief: BuildBrief | None = None
    package_contract: PackageContract | None = None
    action_register: list[ActionRegisterEntry] = Field(default_factory=list)
    canonical_decision: dict[str, Any] = Field(default_factory=dict)
    capability_tags: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)
    profile_comparisons: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_action_register_for_non_reject(self):
        if self.verdict.decision in NON_REJECT_VERDICTS and not self.action_register:
            raise ValueError("non-reject repo-scout reports must include at least one ActionRegister entry")
        return self

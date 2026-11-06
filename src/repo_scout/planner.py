from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .intake import RepoRef, inventory_repo, parse_repo_ref, planned_clone_command
from .schemas import NormalizedGoal, RepoSnapshot, ResearchPlan, RunnerPolicy


def stable_id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode()).hexdigest()[:12]}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def load_profile(profile_path: str | None) -> dict[str, Any] | None:
    """Load a static JSON profile.

    JSON-only keeps the MVP dependency-light while leaving the profile structure
    flexible for later adapter-specific vocabularies (e.g. EXAMPLE packet labels).
    """
    if not profile_path:
        return None
    path = Path(profile_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a JSON object: {profile_path}")
    if data.get("schema_version") != "repo-scout.profile.v1":
        raise ValueError(f"Unsupported profile schema_version in {profile_path!s}")
    data.setdefault("id", path.stem)
    data.setdefault("version", "unknown")
    data["source_path"] = str(path)
    return data


def classify_goal(goal: str | None, profile: dict[str, Any] | None = None) -> tuple[str, list[str], list[str]]:
    text = (goal or "Baseline repository inspection").lower()
    profile_text = json.dumps(profile or {}, sort_keys=True).lower()
    example_terms = ["example", "packet", "fact", "buyer", "assumption", "estimate"]
    wants_example = any(f" {w} " in f" {text} " for w in example_terms) or "example-packet" in profile_text or any(
        f'"{w}"' in profile_text for w in ["FACT", "BUYER", "ASSUMPTION", "ESTIMATE", "fact", "buyer", "assumption", "estimate"]
    )
    if wants_example:
        return "example_packet_evidence_labelling", ["docs_claims", "evidence_labelling", "profile_fit", "synthesis"], ["static", "claim-check", "profile-fit"]
    if any(w in text for w in ["quickstart", "install", "reproduce", "reproducible", "run", "smoke", "real"]):
        return "quickstart_reproducibility", ["docs_claims", "runnable_surface", "risk_maintenance", "synthesis"], ["claim-check", "smoke-test"]
    if any(w in text for w in ["security", "supply chain", "supply-chain", "risk", "safe", "vulnerability", "malicious", "license"]):
        return "security_supply_chain_risk", ["security_supply_chain", "risk_maintenance", "docs_claims", "synthesis"], ["security-review", "maintenance"]
    if any(w in text for w in ["maintenance", "maintained", "health", "active", "stale", "stable", "ci", "release"]):
        return "maintenance_health", ["risk_maintenance", "docs_claims", "synthesis"], ["maintenance", "static"]
    if any(w in text for w in ["docs", "documentation", "claims", "claim", "readme", "validate"]):
        return "docs_claims_validation", ["docs_claims", "runnable_surface", "synthesis"], ["claim-check", "static"]
    if any(w in text for w in ["architecture", "pattern", "borrow", "design", "api", "abstraction"]):
        return "architecture_pattern_borrowing", ["architecture_fit", "docs_claims", "risk_maintenance", "synthesis"], ["architecture", "adoption"]
    if any(w in text for w in ["adopt", "adoption", "improve", "fit", "stack", "use this", "should we use"]):
        return "adoption_fit", ["docs_claims", "architecture_fit", "risk_maintenance", "profile_fit", "synthesis"], ["architecture", "profile-fit", "adoption"]
    return "general_due_diligence", ["docs_claims", "risk_maintenance", "synthesis"], ["static"]


def _base_claim_targets(intent: str) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = [
        {"id": "ct_readme_claims", "source_hint": "README", "target_type": "docs_claim", "query": "Extract README/docs claims relevant to the goal."}
    ]
    if intent in {"adoption_fit", "architecture_pattern_borrowing"}:
        targets.append({"id": "ct_integration_surface", "target_type": "implementation_claim", "query": "Identify APIs, extension points, and design patterns that could be adopted or borrowed."})
    if intent == "quickstart_reproducibility":
        targets.append({"id": "ct_quickstart_commands", "source_hint": "README", "target_type": "install_claim", "query": "Find install, build, run, and smoke-test commands to plan as non-executed probes."})
    if intent == "security_supply_chain_risk":
        targets.append({"id": "ct_supply_chain", "target_type": "security_claim", "query": "Inspect manifests, install hooks, CI, license, and suspicious scripts for static supply-chain risk signals."})
    if intent == "maintenance_health":
        targets.append({"id": "ct_maintenance", "target_type": "maintenance_claim", "query": "Assess visible maintenance signals: CI, tests, releases metadata if available, and stale docs risks."})
    if intent == "example_packet_evidence_labelling":
        targets.append({"id": "ct_packet_labels", "target_type": "adapter_mapping", "query": "Plan how neutral evidence/verdicts would map to packet labels without changing core schemas."})
    return targets


def _profile_plan_overrides(profile: dict[str, Any] | None) -> tuple[list[str], list[str], list[str], list[dict[str, Any]], list[str]]:
    if not profile:
        return [], [], [], [], []
    planning_raw = profile.get("planning")
    planning: dict[str, Any] = planning_raw if isinstance(planning_raw, dict) else {}
    evidence_needs = list(planning.get("evidence_needs", []))
    analyzers = list(planning.get("analyzers", []))
    lanes = list(planning.get("lanes", []))
    claim_targets = list(planning.get("claim_targets", []))
    output_route = list(planning.get("output_route", []))
    if profile.get("comparison_dimensions"):
        evidence_needs.append("profile_comparison")
        lanes.append("profile_fit")
        analyzers.append("profile-fit")
    adapter_raw = profile.get("adapter_mapping")
    adapter: dict[str, Any] = adapter_raw if isinstance(adapter_raw, dict) else {}
    if adapter.get("evidence_labels"):
        evidence_needs.append("profile_evidence_label_mapping")
        lanes.append("evidence_labelling")
        output_route.append("profile-packet")
    return evidence_needs, analyzers, lanes, claim_targets, output_route


def compile_plan(
    goal: str | None,
    repo_ref: str,
    *,
    depth: str = "standard",
    method: str = "auto",
    runner: str = "no-exec",
    max_files: int = 500,
    max_bytes: int = 2_000_000,
    profile_path: str | None = None,
    repo_path: Path | None = None,
    parsed_ref: RepoRef | None = None,
    intake_metadata: dict | None = None,
) -> ResearchPlan:
    profile = load_profile(profile_path)
    parsed = parsed_ref or parse_repo_ref(repo_ref)
    analysis_path = repo_path or parsed.path
    if analysis_path:
        snapshot = inventory_repo(analysis_path, repo_url=parsed.url, owner=parsed.owner, name=parsed.name, max_files=max_files, max_bytes=max_bytes)
        snapshot.ref = parsed.ref
        if intake_metadata:
            snapshot.ignored_path_details.insert(0, {"path": "remote", "reason": "remote_static_clone", **intake_metadata})
            for item in intake_metadata.get("not_fetched", []):
                snapshot.ignored_path_details.append({"path": item["path"], "reason": "large_blob_not_fetched", "blob": item.get("blob")})
            for item in intake_metadata.get("skipped_links", []):
                snapshot.ignored_path_details.append({"path": item["path"], "reason": f"{item.get('kind', 'link')}_not_checked_out"})
    else:
        snapshot = RepoSnapshot(
            url=parsed.url or repo_ref,
            owner=parsed.owner,
            name=parsed.name or repo_ref,
            ref=parsed.ref,
            ignored_path_details=[{"path": "remote", "reason": "planned_clone_only", "command": planned_clone_command(parsed, Path("/tmp/repo-scout") / (parsed.owner or "unknown") / (parsed.name or "repo"))}],
        )
    intent, lanes, methods = classify_goal(goal, profile)
    if method != "auto":
        methods = [m.strip() for m in method.split(",") if m.strip()]
    lane_cap = {"quick": 2, "standard": 4, "deep": 8}.get(depth, 4)
    profile_evidence_needs, profile_analyzers, profile_lanes, profile_claim_targets, profile_output_route = _profile_plan_overrides(profile)
    lanes = _dedupe([*lanes, *profile_lanes])[:lane_cap]
    # The goal harness stays plan-only/no-exec. Goal wording or
    # profile hints must never silently upgrade into execution; docker/podman
    # are retained only as future required-runner hints in ProbeSteps.
    execution_allowed = False
    policy = RunnerPolicy(runner=runner, execution_allowed=execution_allowed, max_lanes=lane_cap)
    analyzers = ["repo-metadata", "inventory", "docs-claims", "ecosystem", "commands"]
    if "architecture" in methods or intent in {"architecture_pattern_borrowing", "adoption_fit"}:
        analyzers.append("architecture-map")
    if "maintenance" in methods or "risk_maintenance" in lanes:
        analyzers.append("maintenance")
    if "security-review" in methods:
        analyzers.append("static-risk")
    if "profile-fit" in methods or profile:
        analyzers.append("profile-fit")
    analyzers = _dedupe([*analyzers, *profile_analyzers])
    question = goal or "Perform a baseline static repository inspection."
    evidence_needs = _dedupe([
        "docs_claims",
        "file_inventory",
        "dependency_manifest",
        "examples_and_quickstarts",
        "tests_and_ci",
        "maintenance_signals",
        *(["architecture_inventory"] if intent in {"adoption_fit", "architecture_pattern_borrowing"} else []),
        *(["static_security_signals", "license_and_supply_chain"] if intent == "security_supply_chain_risk" else []),
        *profile_evidence_needs,
    ])
    # The corpus boundary: repo-scout owns raw/source corpus
    # artifacts; downstream systems receive curated exports. Keep a corpus
    # record in every plan and let profiles add selected/rollup packet routes.
    output_route = _dedupe(["corpus-record", "markdown", "json", "recommendation", *profile_output_route])
    if "smoke-test" in methods:
        output_route = _dedupe([*output_route, "experiment-packet"])
    claim_targets = [*_base_claim_targets(intent), *profile_claim_targets]
    profile_summary = None
    if profile:
        explicit_tier = profile.get("source_tier") or profile.get("trust_tier")
        source_policy_raw = profile.get("source_policy")
        source_policy: dict[str, Any] = source_policy_raw if isinstance(source_policy_raw, dict) else {}
        profile_summary = {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "version": profile.get("version"),
            "source_path": profile.get("source_path"),
            "source_tier": explicit_tier or source_policy.get("source_tier") or source_policy.get("trust_tier"),
            "owner_approved_direct_consume": bool(profile.get("owner_approved_direct_consume") or source_policy.get("owner_approved_direct_consume")),
            "comparison_dimensions": profile.get("comparison_dimensions", []),
            "adapter_mapping": profile.get("adapter_mapping", {}),
            "export_policy": profile.get("export_policy", {}),
        }
    return ResearchPlan(
        plan_id=stable_id("plan", f"{repo_ref}|{goal}|{depth}|{method}|{runner}|{profile_path}"),
        repo=snapshot,
        raw_goal=goal,
        normalized_goal=NormalizedGoal(intent=intent, question=question),
        lens={"primary": intent, "secondary": [lane for lane in lanes if lane != intent], "profile": profile_summary},
        claim_targets=claim_targets,
        evidence_needs=evidence_needs,
        analyzers=analyzers,
        runner_policy=policy,
        output_route=output_route,
        lanes=lanes,
        stop_conditions=[{"type": "budget_exhausted"}, {"type": "safety_veto"}, {"type": "enough_evidence", "min_findings": 2, "min_confidence": 0.7}],
    )

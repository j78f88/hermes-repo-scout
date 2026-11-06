from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel

from . import __version__
from .analyze import (
    build_build_brief_stub,
    build_dossier,
    build_idea_cards,
    build_package_contract_stub,
    extract_idea_candidates,
    extract_readme_claims,
    inventory_evidence,
    manifest_evidence,
    plan_manifest_probes,
    synthesize_findings,
    detect_prompt_injection_findings,
    evaluate_describe_build_gate,
)
from .intake import intake_repo_ref, parse_repo_ref
from .planner import compile_plan, load_profile, stable_id
from .schemas import ActionRegisterEntry, Report, Verdict, VerdictRecord
from .campaign import canonicalize_decision
from .taxonomy import TaxonomyTags, infer_taxonomy_tags, merge_taxonomy_tags


def model_to_json(model: BaseModel) -> str:
    return model.model_dump_json(indent=2, by_alias=True)


def build_report(
    repo_ref: str,
    *,
    goal: str | None = None,
    depth: str = "standard",
    method: str = "auto",
    runner: str = "no-exec",
    max_files: int = 500,
    max_bytes: int = 2_000_000,
    profile_path: str | None = None,
    workspace: Path | None = None,
    run_id: str | None = None,
    keep_snapshot: bool = False,
) -> tuple[Report, object]:
    parsed = parse_repo_ref(repo_ref)
    intake_metadata = None
    analysis_path = parsed.path
    if not parsed.is_local:
        if workspace is None:
            plan = compile_plan(goal, repo_ref, depth=depth, method=method, runner=runner, max_files=max_files, max_bytes=max_bytes, profile_path=profile_path, parsed_ref=parsed)
            profile = load_profile(profile_path)
            claims = []
            evidence = []
            probe_steps = []
            probe_results = []
            findings = synthesize_findings(claims, evidence, probe_results)
            describe_build_gate = evaluate_describe_build_gate(plan.repo, profile, findings, [])
            plan.describe_build_gate = describe_build_gate
            profile_comparisons = _profile_comparison_stub(profile, plan, evidence)
            summary = "Remote static intake requires a workspace; plan records the safe shallow clone command."
            verdict = VerdictRecord(
                goal=goal,
                decision=Verdict.INVESTIGATE,
                confidence=0.2,
                summary=summary,
                rationale_refs=[f.id for f in findings],
                blocking_issues=["No workspace was supplied for remote static clone."],
                next_steps=["Re-run with --workspace to clone and analyze statically."],
            )
            action_register = build_action_register(
                repo_name=plan.repo.name,
                verdict=verdict,
                idea_cards=[],
                findings=findings,
                risk_notes=["Remote source was not cloned because no workspace was supplied."],
            )
            report = Report(
                run_id=stable_id("run", f"{repo_ref}|{goal}|{depth}|{method}|{runner}|planned"),
                plan_id=plan.plan_id,
                generated_by={"tool": "repo-scout", "version": __version__},
                repo=plan.repo,
                goal={"raw": goal, "normalized_intent": plan.normalized_goal.intent if plan.normalized_goal else None, "lens": plan.lens, "methods": method.split(",") if method != "auto" else plan.analyzers, "depth": depth, "profile_id": profile.get("id") if profile else None},
                runner_policy=plan.runner_policy,
                describe_build_gate=describe_build_gate,
                research_plan_ref="artifacts/research_plan.json",
                summary=summary,
                verdict=verdict,
                action_register=action_register,
                canonical_decision=canonicalize_decision(collector_verdict=verdict.decision).model_dump(mode="json"),
                findings=findings,
                profile_comparisons=profile_comparisons,
                artifacts=["artifacts/research_plan.json"],
                capability_tags=[],
                risk_tags=[],
                source_tags=["metadata-static"],
                metrics={"files_scanned": 0, "claims_extracted": 0, "probe_steps_planned": 0, "probe_steps_run": 0, "target_code_execution": False, "ignored_paths": len(plan.repo.ignored_paths)},
                limitations=["No execution was performed because runner=no-exec.", "Remote clone was not performed because no workspace was supplied."],
            )
            return report, plan
        remote_run = run_id or stable_id("run", f"{repo_ref}|{goal}|{depth}|{method}|{runner}")
        parsed, analysis_path, intake_metadata = intake_repo_ref(repo_ref, workspace=workspace, run_id=remote_run)
    plan = compile_plan(goal, repo_ref, depth=depth, method=method, runner=runner, max_files=max_files, max_bytes=max_bytes, profile_path=profile_path, repo_path=analysis_path, parsed_ref=parsed, intake_metadata=intake_metadata)
    profile = load_profile(profile_path)
    claims = []
    evidence = []
    probe_steps = []
    probe_results = []
    if analysis_path:
        claims, docs_evidence, probe_steps, probe_results = extract_readme_claims(analysis_path)
        manifest_probe_steps, manifest_probe_results = plan_manifest_probes(
            analysis_path,
            plan.repo,
            start_index=len(probe_steps) + 1,
            result_start_index=len(probe_results) + 1,
        )
        probe_steps.extend(manifest_probe_steps)
        probe_results.extend(manifest_probe_results)
        inv_evidence = inventory_evidence(plan.repo)
        manifest_static_evidence = manifest_evidence(analysis_path, plan.repo, start_index=len(docs_evidence) + len(inv_evidence) + 1)
        for idx, ev in enumerate([*docs_evidence, *inv_evidence, *manifest_static_evidence], start=1):
            if ev.id.startswith(("E_INV_", "E_MAN_")):
                ev.id = f"E{idx}"
            evidence.append(ev)
        # Keep claim refs pointing at their original docs evidence IDs; docs evidence comes first.
    findings = synthesize_findings(claims, evidence, probe_results)
    findings.extend(detect_prompt_injection_findings(evidence, start_index=len(findings) + 1))
    if not findings:
        findings = []
    profile_comparisons = _profile_comparison_stub(profile, plan, evidence)
    idea_candidates = extract_idea_candidates(claims, evidence)
    decision = Verdict.INVESTIGATE
    if goal and any(w in goal.lower() for w in ["borrow", "architecture", "pattern"]):
        decision = Verdict.BORROW
    elif goal and any(w in goal.lower() for w in ["adopt", "improve", "fit", "stack"]):
        decision = Verdict.WATCH
    summary = "Static no-exec scan completed with evidence-backed limitations."
    if claims:
        summary = f"Static no-exec scan extracted {len(claims)} README claim(s) and {len(evidence)} evidence item(s)."
    if probe_results:
        summary += " Execution-like probes were planned but not run."
    verdict = VerdictRecord(
        goal=goal,
        decision=decision,
        confidence=0.55 if claims else 0.35,
        summary=summary,
        rationale_refs=[f.id for f in findings],
        blocking_issues=["No execution was performed because runner=no-exec."],
        next_steps=["Review evidence citations.", "Use an explicit future sandbox runner for smoke tests if needed."],
    )
    idea_cards = build_idea_cards(idea_candidates, evidence)
    describe_build_gate = evaluate_describe_build_gate(plan.repo, profile, findings, idea_cards)
    plan.describe_build_gate = describe_build_gate
    dossier = build_dossier(plan.repo, goal, claims, evidence, findings, idea_cards, verdict, describe_build_gate) if evidence or claims or idea_cards else None
    build_brief = build_build_brief_stub(goal, idea_cards, evidence, describe_build_gate)
    package_contract = build_package_contract_stub(build_brief)
    action_register = build_action_register(
        repo_name=plan.repo.name,
        verdict=verdict,
        idea_cards=idea_cards,
        findings=findings,
        risk_notes=[*verdict.blocking_issues, *report_risk_notes(findings, describe_build_gate)],
    )
    report_tags = infer_taxonomy_tags(
        summary,
        evidence=evidence,
        files=plan.repo.files,
        source_tags=["inferred-static"] if evidence or findings else [],
    ).merged(merge_taxonomy_tags([*claims, *evidence, *findings, *idea_candidates, *idea_cards, dossier]))
    if intake_metadata and intake_metadata.get("type") == "remote_static_clone":
        report_tags = report_tags.merged(TaxonomyTags(source_tags=["remote-snapshot"], risk_tags=["data-retention"]))
    report = Report(
        run_id=stable_id("run", f"{repo_ref}|{goal}|{depth}|{method}|{runner}|{plan.repo.checkout_hash}"),
        plan_id=plan.plan_id,
        generated_by={"tool": "repo-scout", "version": __version__},
        repo=plan.repo,
        goal={
            "raw": goal,
            "normalized_intent": plan.normalized_goal.intent if plan.normalized_goal else None,
            "lens": plan.lens,
            "methods": method.split(",") if method != "auto" else plan.analyzers,
            "depth": depth,
            "profile_id": profile.get("id") if profile else None,
        },
        runner_policy=plan.runner_policy,
        describe_build_gate=describe_build_gate,
        research_plan_ref="artifacts/research_plan.json",
        summary=summary,
        verdict=verdict,
        action_register=action_register,
        canonical_decision=canonicalize_decision(collector_verdict=verdict.decision).model_dump(mode="json"),
        claims=claims,
        evidence=evidence,
        probe_steps=probe_steps,
        probe_results=probe_results,
        findings=findings,
        idea_candidates=idea_candidates,
        idea_cards=idea_cards,
        dossier=dossier,
        build_brief=build_brief,
        package_contract=package_contract,
        capability_tags=report_tags.capability_tags,
        risk_tags=report_tags.risk_tags,
        source_tags=report_tags.source_tags,
        profile_comparisons=profile_comparisons,
        artifacts=["artifacts/research_plan.json"],
        metrics={
            "files_scanned": plan.repo.files_scanned,
            "claims_extracted": len(claims),
            "probe_steps_planned": len(probe_steps),
            "probe_steps_run": 0,
            "target_code_execution": False,
            "ignored_paths": plan.repo.ignored_paths_total or len(plan.repo.ignored_paths),
            "ignored_paths_truncated": plan.repo.ignored_paths_truncated,
        },
        limitations=["No execution was performed because runner=no-exec.", "Remote static intake uses a safe shallow clone into the repo-scout workspace when a remote URL is supplied."],
    )
    if intake_metadata and intake_metadata.get("type") == "remote_static_clone" and not keep_snapshot:
        snapshot_path = Path(str(intake_metadata.get("snapshot_path")))
        if snapshot_path.exists():
            shutil.rmtree(snapshot_path)
        retention_detail = {
            "path": str(snapshot_path),
            "reason": "transient_snapshot_removed",
            "retention_policy": "remove_after_analysis",
            "keep_snapshot": False,
            "safety_note": "repo-scout persisted extracted evidence/ideas/reports only; full cloned source was removed by default.",
        }
        report.repo.ignored_path_details.append(retention_detail)
        plan.repo.ignored_path_details.append(retention_detail)
        report.repo.path = None
        plan.repo.path = None
    elif intake_metadata and intake_metadata.get("type") == "remote_static_clone" and keep_snapshot:
        retention_detail = {
            "path": str(intake_metadata.get("snapshot_path")),
            "reason": "snapshot_retained_explicitly",
            "retention_policy": "keep_snapshot",
            "keep_snapshot": True,
            "safety_note": "Full cloned source retained only because --keep-snapshot was explicitly supplied.",
        }
        report.repo.ignored_path_details.append(retention_detail)
        plan.repo.ignored_path_details.append(retention_detail)
    return report, plan


def build_action_register(
    *,
    repo_name: str,
    verdict: VerdictRecord,
    idea_cards: list,
    findings: list,
    risk_notes: list[str] | None = None,
) -> list[ActionRegisterEntry]:
    """Build fail-closed routing records for non-reject scout outputs."""

    risk_notes = risk_notes or []
    decision = verdict.decision
    if decision in {Verdict.REJECT, Verdict.NO_VERDICT}:
        return [
            ActionRegisterEntry(
                action_id=_action_id(repo_name, "close", "reject"),
                action_type="close",
                title=f"Close repo-scout recommendation for {repo_name}",
                target_surfaces=[],
                recommendation="reject",
                why=verdict.summary,
                evidence_refs=_verdict_evidence_refs(verdict, findings),
                risk_notes=risk_notes,
                owner_gate="none",
                next_registration_step="no_action",
                confidence=verdict.confidence,
            )
        ]

    entries: list[ActionRegisterEntry] = []
    for index, card in enumerate(idea_cards or [], start=1):
        suggested = str(getattr(card, "suggested_action", "investigate"))
        recommendation, action_type, next_step, trigger = _action_state_for_suggestion(suggested)
        entries.append(
            ActionRegisterEntry(
                action_id=_action_id(repo_name, getattr(card, "id", str(index)), suggested),
                action_type=action_type,
                title=getattr(card, "purpose", None) or f"Review {repo_name} idea {index}",
                target_surfaces=["source-intel tooling"],
                recommendation=recommendation,
                why=getattr(card, "function", None) or verdict.summary,
                evidence_refs=list(getattr(card, "evidence_refs", []) or []),
                risk_notes=risk_notes,
                owner_gate="none",
                next_registration_step=next_step,
                trigger_to_reopen=trigger,
                confidence=float(getattr(card, "confidence", verdict.confidence) or verdict.confidence),
            )
        )
    if entries:
        return entries

    recommendation, action_type, next_step, trigger = _action_state_for_suggestion(decision.value)
    return [
        ActionRegisterEntry(
            action_id=_action_id(repo_name, verdict.id, decision.value),
            action_type=action_type,
            title=f"Register repo-scout verdict for {repo_name}",
            target_surfaces=["source-intel tooling"],
            recommendation=recommendation,
            why=verdict.summary,
            evidence_refs=_verdict_evidence_refs(verdict, findings),
            risk_notes=risk_notes,
            owner_gate="none",
            next_registration_step=next_step,
            trigger_to_reopen=trigger,
            confidence=verdict.confidence,
            registration_status="fail_closed" if decision == Verdict.INVESTIGATE else "generated_from_report",
        )
    ]


def report_risk_notes(findings: list, describe_build_gate) -> list[str]:
    notes: list[str] = []
    for finding in findings:
        if getattr(finding, "category", None) in {"risk", "security", "dependency", "license"}:
            notes.append(f"{finding.id}: {finding.title}")
    for reason in describe_build_gate.reasons:
        if reason not in notes:
            notes.append(reason)
    return notes


def _action_id(repo_name: str, item_id: str, action: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in f"{repo_name}-{item_id}-{action}")
    normalized = "-".join(part for part in normalized.split("-") if part)
    return stable_id("action", normalized)


def _verdict_evidence_refs(verdict: VerdictRecord, findings: list) -> list[str]:
    refs = list(verdict.rationale_refs or [])
    for finding in findings[:3]:
        for ref in getattr(finding, "evidence_refs", []) or []:
            if ref not in refs:
                refs.append(ref)
    return refs


def _action_state_for_suggestion(suggested: str) -> tuple[str, str, str, str | None]:
    if suggested in {"watch", "investigate"}:
        return "watch_with_trigger", "watch_trigger", "watch_ledger", "Re-open when the watch trigger is met or Owner selects this candidate."
    if suggested in {"borrow", "adapt", "adopt"}:
        return "promote_to_candidate", "buildbrief_candidate", "candidate_ledger", None
    if suggested == "reject":
        return "reject", "close", "no_action", None
    return "investigate", "rescout", "rescout", "Re-scout when more evidence or a sharper goal is available."


def _profile_comparison_stub(profile: dict | None, plan, evidence: list) -> list[dict]:
    if not profile:
        return []
    dimensions = []
    evidence_refs = [ev.id for ev in evidence[:3]]
    for dim in profile.get("comparison_dimensions", []):
        if isinstance(dim, dict):
            name = dim.get("name", "profile_dimension")
            preference = dim.get("preference") or dim.get("profile_preference") or "Profile preference supplied."
            notes = dim.get("notes", "Static profile comparison stub; detailed scoring is reserved for later analyzers.")
        else:
            name = str(dim)
            preference = str(dim)
            notes = "Static profile comparison stub; detailed scoring is reserved for later analyzers."
        dimensions.append(
            {
                "name": name,
                "profile_preference": preference,
                "repo_observation": "To be populated by profile-fit analyzer from neutral Evidence records.",
                "fit": "unknown",
                "score": None,
                "evidence_refs": evidence_refs,
                "notes": notes,
            }
        )
    adapter_mapping = profile.get("adapter_mapping", {}) if isinstance(profile.get("adapter_mapping"), dict) else {}
    export_policy = profile.get("export_policy", {}) if isinstance(profile.get("export_policy"), dict) else {}
    label_intent = adapter_mapping.get("evidence_labels", [])
    verdict_phrases = adapter_mapping.get("verdict_phrases", [])
    return [
        {
            "id": "PC1",
            "profile_id": profile.get("id"),
            "profile_version": profile.get("version"),
            "plan_lens": plan.lens,
            "dimensions": dimensions,
            "overall_fit": "unknown",
            "score": None,
            "top_gaps": [],
            "top_matches": [],
            "recommendation": "investigate",
            "mapping_intent": {
                "core_schema": "Claim/Evidence/Finding/Verdict remain schema-neutral.",
                "evidence_labels": label_intent,
                "verdict_phrases": verdict_phrases,
                "adapter_notes": adapter_mapping.get("notes"),
                "export_policy": export_policy,
            },
        }
    ]


def render_markdown(report: Report) -> str:
    repo_label = report.repo.name
    goal_text = report.goal.get("raw") or "Baseline static scan"
    lines = [
        "---",
        f"schema_version: {report.schema_version}",
        f"run_id: {report.run_id}",
        f"repo: {report.repo.url}",
        f"goal: {goal_text}",
        f"verdict: {report.verdict.decision.value}",
        f"decision: {report.canonical_decision.get('decision', 'unknown')}",
        f"consumption_mode: {report.canonical_decision.get('consumption_mode', 'unknown')}",
        f"build_gate: {report.canonical_decision.get('build_gate', report.describe_build_gate.state.value)}",
        f"collector_verdict: {report.canonical_decision.get('collector_verdict', report.verdict.decision.value)}",
        f"confidence: {report.verdict.confidence:.2f}",
        f"created_at: {report.created_at.isoformat()}",
        "capability_tags: [" + ", ".join(report.capability_tags) + "]",
        "risk_tags: [" + ", ".join(report.risk_tags) + "]",
        "source_tags: [" + ", ".join(report.source_tags) + "]",
        "---",
        "",
        f"# repo-scout Report: {repo_label}",
        "",
        "## Taxonomy tags",
        "",
        "- Capability: " + (", ".join(report.capability_tags) or "—"),
        "- Risk: " + (", ".join(report.risk_tags) or "—"),
        "- Source: " + (", ".join(report.source_tags) or "—"),
        "",
        "## Safety mode",
        "",
        f"Runner: `{report.runner_policy.runner}`  ",
        f"Code execution: `{str(report.runner_policy.execution_allowed).lower()}`",
        "",
        "## Describe→build gate",
        "",
        f"Source tier: `{report.describe_build_gate.source_tier.value}`  ",
        f"Gate state: `{report.describe_build_gate.state.value}`  ",
        f"Direct-consume allowed: `{str(report.describe_build_gate.can_direct_consume).lower()}`  ",
        f"Council review required: `{str(report.describe_build_gate.requires_peer_review).lower()}`  ",
        f"Original implementation only: `{str(report.describe_build_gate.original_implementation_only).lower()}`",
        "",
        "Gate reasons:",
    ]
    for reason in report.describe_build_gate.reasons:
        lines.append(f"- {reason}")
    if report.describe_build_gate.risk_finding_refs:
        lines.append("- Risk findings: " + ", ".join(report.describe_build_gate.risk_finding_refs))
    lines += [
        "",
        "## Verdict",
        "",
        f"**{report.verdict.decision.value.upper()}** — confidence **{report.verdict.confidence:.2f}**",
        "",
        report.verdict.summary,
        "",
        "## ActionRegister",
        "",
        "| Action ID | Recommendation | Next registration | Owner gate | Evidence |",
        "|---|---|---|---|---|",
    ]
    if report.action_register:
        for action in report.action_register:
            evidence_refs = ", ".join(action.evidence_refs) or "—"
            lines.append(
                f"| {action.action_id} | `{action.recommendation}` — {action.title} | `{action.next_registration_step}` | `{action.owner_gate}` | {evidence_refs} |"
            )
            lines.append(f"\n  - Why: {action.why}")
            if action.trigger_to_reopen:
                lines.append(f"  - Trigger: {action.trigger_to_reopen}")
            if action.risk_notes:
                lines.append("  - Risk notes: " + "; ".join(action.risk_notes[:4]))
    else:
        lines.append("| — | `fail_closed` | — | — | Non-reject output lacks ActionRegister entries. |")
    lines += [
        "",
        "## Goal",
        "",
        f"> {goal_text}",
        "",
        f"Normalized intent: `{report.goal.get('normalized_intent')}`  ",
        f"Lens: `{(report.goal.get('lens') or {}).get('primary')}`  ",
        f"Depth: `{report.goal.get('depth')}`",
        "",
        "## Plan summary",
        "",
        f"Research plan: `{report.plan_id}`  ",
        f"Analyzers/methods: {', '.join(report.goal.get('methods') or []) or '—'}  ",
        f"Research plan artifact: `{report.research_plan_ref or '—'}`",
        "",
        "## Top findings",
        "",
        "| ID | Severity | Finding | Evidence |",
        "|---|---|---|---|",
    ]
    if report.findings:
        for f in report.findings:
            refs = ", ".join([*f.evidence_refs, *f.probe_result_refs]) or "—"
            lines.append(f"| {f.id} | {f.severity} | {f.title} | {refs} |")
    else:
        lines.append("| — | info | No findings generated. | — |")
    lines += ["", "## Claims checked", ""]
    if report.claims:
        for c in report.claims:
            lines.append(f"- {c.id}: {c.text} — **{c.status}**, confidence {c.confidence:.2f}. Evidence: {', '.join(c.evidence_refs) or '—'}")
    else:
        lines.append("- No README claims extracted.")
    lines += ["", "## Idea candidates", ""]
    if report.idea_candidates:
        for idea in report.idea_candidates:
            refs = ", ".join([*idea.evidence_refs, *idea.source_claim_refs]) or "—"
            lines.append(f"- {idea.id} `{idea.kind}` **{idea.title}** — {idea.summary} Tags: cap=`{', '.join(idea.capability_tags) or '—'}` risk=`{', '.join(idea.risk_tags) or '—'}` source=`{', '.join(idea.source_tags) or '—'}`. Action: `{idea.suggested_action}`. Evidence: {refs}")
    else:
        lines.append("- No idea candidates extracted.")
    lines += ["", "## Reusable architecture contracts", ""]
    if report.dossier:
        d = report.dossier
        lines += [
            "### Dossier",
            "",
            f"- Repo purpose: {d.repo_purpose}",
            f"- Repo function: {d.repo_function}",
            f"- Recommendation: `{d.recommendation}`",
            f"- Existing-system fit: {d.existing_system_fit}",
            f"- Next action: {d.next_action}",
            f"- Describe→build gate: `{(d.describe_build_gate or {}).get('state', report.describe_build_gate.state.value)}` / source tier `{(d.describe_build_gate or {}).get('source_tier', report.describe_build_gate.source_tier.value)}`",
            f"- Tags: cap=`{', '.join(d.capability_tags) or '—'}` risk=`{', '.join(d.risk_tags) or '—'}` source=`{', '.join(d.source_tags) or '—'}`",
            "",
            "Evidence basis:",
        ]
        for ev in d.evidence_basis[:8]:
            lines.append(f"- {ev.get('id')} `{ev.get('type')}` ({ev.get('strength')}): {ev.get('summary')}")
        lines += ["", "Risks/breakpoints:"]
        for risk in d.risks_breakpoints:
            lines.append(f"- {risk}")
    else:
        lines.append("- Dossier not generated; insufficient static evidence.")
    lines += ["", "### IdeaCards", ""]
    if report.idea_cards:
        for card in report.idea_cards:
            refs = ", ".join(card.evidence_refs) or "—"
            lines.append(f"- {card.id} `{card.source_tier}` risk `{card.risk}` action `{card.suggested_action}` tags cap=`{', '.join(card.capability_tags) or '—'}` risk_tags=`{', '.join(card.risk_tags) or '—'}` source=`{', '.join(card.source_tags) or '—'}` — **{card.purpose}**: {card.function} Evidence: {refs}")
    else:
        lines.append("- No IdeaCards generated.")
    lines += ["", "### BuildBrief stub", ""]
    if report.build_brief:
        bb = report.build_brief
        lines.append(f"- Original implementation goal: {bb.original_implementation_goal}")
        lines.append(f"- Tags: cap=`{', '.join(bb.capability_tags) or '—'}` risk=`{', '.join(bb.risk_tags) or '—'}` source=`{', '.join(bb.source_tags) or '—'}`")
        lines.append(f"- Describe→build gate: `{(bb.describe_build_gate or {}).get('state', report.describe_build_gate.state.value)}`; direct-consume `{str((bb.describe_build_gate or {}).get('can_direct_consume', report.describe_build_gate.can_direct_consume)).lower()}`")
        lines.append("- Non-goals: " + "; ".join(bb.non_goals))
        lines.append("- Test plan: " + "; ".join(bb.test_plan))
        lines.append("- Security model: " + "; ".join(bb.security_model))
        lines.append("- Vertical adapter seams: " + "; ".join(bb.vertical_adapter_seams))
    else:
        lines.append("- BuildBrief stub not generated; select at least one IdeaCard first.")
    if report.package_contract:
        pc = report.package_contract
        lines += ["", "### PackageContract stub", ""]
        lines.append("- Install/config: " + "; ".join(pc.install_config))
        lines.append(f"- Tags: cap=`{', '.join(pc.capability_tags) or '—'}` risk=`{', '.join(pc.risk_tags) or '—'}` source=`{', '.join(pc.source_tags) or '—'}`")
        lines.append(f"- Secrets boundary: {pc.secrets_boundary}")
        lines.append("- Verification commands: " + "; ".join(pc.verification_commands))
        lines.append("- Adapter points: " + "; ".join(pc.adapter_points))
    lines += ["", "## Profile comparison", ""]
    if report.profile_comparisons:
        for pc in report.profile_comparisons:
            labels = pc.get("mapping_intent", {}).get("evidence_labels", [])
            phrases = pc.get("mapping_intent", {}).get("verdict_phrases", [])
            lines.append(f"- Profile `{pc.get('profile_id')}` v`{pc.get('profile_version')}`: fit `{pc.get('overall_fit')}`, recommendation `{pc.get('recommendation')}`.")
            if labels:
                lines.append(f"  - Evidence label mapping intent: {', '.join(labels)}")
            if phrases:
                lines.append(f"  - Verdict phrase mapping intent: {', '.join(phrases)}")
            export_policy = pc.get("mapping_intent", {}).get("export_policy", {}) or {}
            if export_policy:
                lines.append(f"  - Export policy: default `{export_policy.get('default_mode')}`, curated modes `{', '.join(export_policy.get('curated_modes', []))}`, raw export `{export_policy.get('raw_export_mode')}`")
            for dim in pc.get("dimensions", []):
                lines.append(f"  - {dim.get('name')}: {dim.get('profile_preference')} → {dim.get('fit')}")
    else:
        lines.append("- No profile supplied; profile comparison not generated.")
    lines += ["", "## Probe plan/results", "", "| Step | Status | Reason |", "|---|---|---|"]
    if report.probe_steps:
        by_step = {r.probe_step_id: r for r in report.probe_results}
        for step in report.probe_steps:
            result = by_step.get(step.id)
            lines.append(f"| {step.id} {step.description} | {result.status if result else 'planned'} | {result.failure_summary if result else 'not executed'} |")
    else:
        lines.append("| — | none | No execution probes inferred. |")
    lines += ["", "## Limitations", ""]
    for limitation in report.limitations:
        lines.append(f"- {limitation}")
    lines += ["", "## Evidence index", ""]
    for ev in report.evidence:
        loc = ev.locator.path if ev.locator and ev.locator.path else ev.source_uri
        if ev.locator and ev.locator.start_line:
            loc += f":{ev.locator.start_line}-{ev.locator.end_line or ev.locator.start_line}"
        lines.append(f"- {ev.id} `{loc}`: {ev.summary}")
    return "\n".join(lines) + "\n"


def write_outputs(report: Report, plan, out: Path, fmt: str) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    artifacts = out / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "research_plan.json").write_text(model_to_json(plan) + "\n", encoding="utf-8")
    paths: dict[str, Path] = {"plan": artifacts / "research_plan.json"}
    if fmt in {"json", "both"}:
        p = out / "report.json"
        p.write_text(model_to_json(report) + "\n", encoding="utf-8")
        paths["json"] = p
    if fmt in {"markdown", "md", "both"}:
        p = out / "report.md"
        p.write_text(render_markdown(report), encoding="utf-8")
        paths["markdown"] = p
    return paths

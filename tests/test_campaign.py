from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from repo_scout.campaign import (
    CampaignInitializer,
    CampaignVerifier,
    LaneOutput,
    Mode,
    WorkerPacket,
    canonicalize_decision,
    load_manifest,
    route_mode,
)
from repo_scout.cli import main
from repo_scout.compare import build_comparison
from repo_scout.reporting import build_report
from repo_scout.schemas import BuildGateState


def test_mode_router_is_deterministic_and_standard_is_default():
    standard = route_mode(None)
    assert standard.mode == Mode.STANDARD
    assert standard.cli_baseline_required is True
    assert 2 <= len(standard.lanes) <= 4
    assert standard.deep_research_handoff is False

    assert route_mode("brief").lanes == ("controller",)
    assert len(route_mode("full").lanes) >= 5
    assert route_mode("refresh_delta").refresh_delta is True

    handoff = route_mode("true_deep_research")
    assert handoff.deep_research_handoff is True
    assert handoff.cli_baseline_required is False

    with pytest.raises(ValueError, match="unknown repo-scout mode"):
        route_mode("deep")


def test_canonical_decision_schema_maps_legacy_values_and_fails_closed():
    surface = canonicalize_decision(
        collector_verdict="borrow",
        decision="pilot",
        consumption_mode="translation",
        build_gate=BuildGateState.SPIKE_ORIGINAL,
    )
    assert surface.decision == "pilot"
    assert surface.consumption_mode == "translation"
    assert surface.build_gate == BuildGateState.SPIKE_ORIGINAL
    assert surface.collector_verdict == "borrow"

    legacy = canonicalize_decision(collector_verdict="PATTERN-ONLY")
    assert legacy.decision == "watch"
    assert legacy.consumption_mode == "pattern"
    assert legacy.build_gate == BuildGateState.PEER_REVIEW

    with pytest.raises(ValueError, match="unknown collector_verdict"):
        canonicalize_decision(collector_verdict="ship-it")

    with pytest.raises(ValueError, match="conflicts"):
        canonicalize_decision(collector_verdict="reject", decision="adopt", consumption_mode="integration")

    blocked = canonicalize_decision(collector_verdict="integrate-candidate", owner_gate="credentials")
    assert blocked.decision == "block"
    assert blocked.build_gate == BuildGateState.BLOCKED_NEEDS_OWNER


def test_worker_packet_and_lane_output_require_source_classes_and_forbidden_actions(tmp_path: Path):
    packet = WorkerPacket(
        lane_id="security",
        repo_url="https://github.com/example/healthy",
        pinned_commit="abc123",
        campaign_root=tmp_path,
        output_path=tmp_path / "lanes" / "security.json",
        source_classes=["external_source_evidence", "internal_context"],
        forbidden_actions=["no target repo code execution", "no credentials"],
        evidence_classes=["license", "supply_chain"],
    )
    assert packet.output_path.is_absolute()
    assert packet.source_as_untrusted_data is True

    with pytest.raises(ValidationError, match="source_classes"):
        WorkerPacket(
            lane_id="bad",
            repo_url="https://github.com/example/bad",
            pinned_commit="abc123",
            campaign_root=tmp_path,
            output_path=tmp_path / "lanes" / "bad.json",
            source_classes=["external_source_evidence"],
            forbidden_actions=["no target repo code execution"],
            evidence_classes=["license"],
        )

    with pytest.raises(ValidationError, match="forbidden_actions"):
        LaneOutput(
            lane_id="bad",
            status="pass",
            output_path=tmp_path / "lane.json",
            sources_used=["README"],
            source_classes=["external_source_evidence", "fit_routing"],
            forbidden_actions_observed=[],
            evidence_refs=["E1"],
            gaps=[]
        )


def test_campaign_initializer_writes_manifest_lock_checkpoints_and_idempotent_resume(tmp_path: Path):
    root = tmp_path / "campaigns"
    initializer = CampaignInitializer(root)
    manifest = initializer.initialize(
        repos=["https://github.com/example/healthy", "https://github.com/example/stale"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="fixture-campaign",
    )

    assert manifest.campaign_id == "fixture-campaign"
    assert manifest.mode == Mode.STANDARD
    assert manifest.max_concurrency == 3
    assert len({repo.run_id for repo in manifest.repositories}) == 2
    assert len({repo.root for repo in manifest.repositories}) == 2
    assert (root / "fixture-campaign" / "manifest.json").exists()
    assert (root / "fixture-campaign" / "lock.json").exists()
    assert [c.name for c in manifest.checkpoints] == ["campaign_initialized"]

    resumed = initializer.initialize(
        repos=list(reversed([repo.repo_url for repo in manifest.repositories])),
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="fixture-campaign",
        resume=True,
    )
    assert [repo.run_id for repo in resumed.repositories] == [repo.run_id for repo in manifest.repositories]
    assert load_manifest(root / "fixture-campaign" / "manifest.json").campaign_id == "fixture-campaign"

    with pytest.raises(ValueError, match="max_concurrency"):
        initializer.initialize(
            repos=["https://github.com/example/one"],
            mode="standard",
            scout_controller="controller-a",
            verifier="qa-a",
            candidate_owner="owner-a",
            max_concurrency=9,
        )


def test_campaign_verifier_partial_success_resume_and_validated_comparison_only(tmp_path: Path):
    root = tmp_path / "campaigns"
    manifest = CampaignInitializer(root).initialize(
        repos=[
            "https://github.com/example/healthy",
            "https://github.com/example/failed",
            "https://github.com/example/conflict",
        ],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="fixture-partial",
    )

    healthy, failed, conflict = manifest.repositories
    healthy_report = Path(healthy.root) / "report.json"
    healthy_report.write_text(json.dumps(_report_payload("healthy", "borrow")), encoding="utf-8")
    healthy.report_path = healthy_report

    failed.status = "failed"
    failed.error = "fixture clone unavailable"

    conflict_report = Path(conflict.root) / "report.json"
    conflict_payload = _report_payload("conflict", "reject")
    conflict_payload["canonical_decision"] = {"decision": "adopt", "consumption_mode": "integration", "build_gate": "build_original"}
    conflict_report.write_text(json.dumps(conflict_payload), encoding="utf-8")
    conflict.report_path = conflict_report

    manifest.write_atomic()

    result = CampaignVerifier(manifest.path).verify()
    assert result.passed is False
    assert result.partial_success is True
    assert result.validated_report_paths == [healthy_report]
    assert result.excluded_report_paths == [conflict_report]
    assert any("conflicts" in error for error in result.errors)
    assert result.summary["validated_reports"] == 1
    assert result.summary["failed_repositories"] == 1


def test_campaign_fixture_suite_covers_required_scenarios():
    fixture_path = (Path(__file__).resolve().parent / "fixtures/campaign/campaign_scenarios.json")
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    required = {
        "healthy_permissive",
        "stale_repository",
        "malicious_instruction",
        "license_ambiguity",
        "backlog_duplicate",
        "no_fit",
        "multi_repo_partial_failure",
        "unchanged_refresh",
        "verdict_conflict",
        "interrupted_resume",
    }
    assert {case["id"] for case in payload["scenarios"]} == required
    for case in payload["scenarios"]:
        result = CampaignVerifier.validate_fixture_scenario(case)
        if case["id"] == "verdict_conflict":
            assert result.passed is False
            assert any("conflicts" in error for error in result.errors)
        else:
            assert result.passed, result.errors


def test_build_report_emits_canonical_decision_and_compare_filters_invalid_reports(tmp_path: Path, fixture_repo: Path):
    report, _plan = build_report(str(fixture_repo), goal="borrow useful pattern", workspace=tmp_path / "workspace")
    assert report.canonical_decision["collector_verdict"] == report.verdict.decision.value
    assert report.canonical_decision["decision"] == "pilot"
    assert report.canonical_decision["consumption_mode"] == "translation"

    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    valid.write_text(report.model_dump_json(), encoding="utf-8")
    invalid_payload = json.loads(report.model_dump_json())
    invalid_payload["canonical_decision"] = {"decision": "adopt", "consumption_mode": "integration", "build_gate": "build_original"}
    invalid_payload["verdict"]["decision"] = "reject"
    invalid.write_text(json.dumps(invalid_payload), encoding="utf-8")

    comparison = build_comparison([valid, invalid])
    assert comparison["report_count"] == 1
    assert comparison["excluded_report_count"] == 1
    assert comparison["rows"][0]["repo"] == fixture_repo.name


def test_campaign_cli_init_and_verify_smoke(tmp_path: Path):
    runner = CliRunner()
    campaign_root = tmp_path / "campaigns"
    init = runner.invoke(
        main,
        [
            "campaign",
            "init",
            "https://github.com/example/healthy",
            "--root",
            str(campaign_root),
            "--campaign-id",
            "cli-fixture",
            "--mode",
            "brief",
            "--controller",
            "controller-a",
            "--verifier",
            "qa-a",
            "--candidate-owner",
            "owner-a",
        ],
    )
    assert init.exit_code == 0, init.output
    assert "campaign_id: cli-fixture" in init.output
    manifest_path = campaign_root / "cli-fixture" / "manifest.json"
    assert manifest_path.exists()

    verify = runner.invoke(main, ["campaign", "verify", str(manifest_path)])
    assert verify.exit_code == 1
    assert "validated_reports: 0" in verify.output


def test_campaign_paths_fail_closed_for_absolute_traversal_url_and_prefix_collision(tmp_path: Path):
    root = tmp_path / "campaign"
    initializer = CampaignInitializer(root)

    bad_campaign_ids = ["/tmp/escape", "../escape", "ok/../../escape", "https://evil.test/path?x=../y#frag"]
    for bad_id in bad_campaign_ids:
        with pytest.raises(ValueError, match="campaign_id"):
            initializer.initialize(
                repos=["https://github.com/example/healthy"],
                mode="standard",
                scout_controller="controller-a",
                verifier="qa-a",
                candidate_owner="owner-a",
                campaign_id=bad_id,
            )

    manifest = initializer.initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="safe-campaign",
    )
    payload = json.loads(manifest.path.read_text(encoding="utf-8"))
    payload["repositories"][0]["root"] = str(tmp_path / "campaign_evil" / "repos" / "healthy")
    manifest.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="campaign root"):
        load_manifest(manifest.path)


def test_worker_packet_path_containment_uses_resolved_relative_to_not_prefix(tmp_path: Path):
    campaign_root = tmp_path / "campaign"
    evil_sibling = tmp_path / "campaign_evil" / "lane.json"
    evil_sibling.parent.mkdir()

    with pytest.raises(ValidationError, match="output_path must be inside campaign_root"):
        WorkerPacket(
            lane_id="security",
            repo_url="https://github.com/example/evil",
            campaign_root=campaign_root,
            output_path=evil_sibling,
            source_classes=["external_source_evidence", "internal_context"],
            forbidden_actions=["no target repo code execution"],
            evidence_classes=["license"],
        )


def test_report_payload_rejects_execution_allowed_missing_action_register_and_bad_baseline():
    payload = _report_payload("exec", "borrow")
    payload["runner_policy"] = {"runner": "local", "execution_allowed": True}
    ok, reason = CampaignVerifier.validate_report_payload(payload)
    assert ok is False
    assert "execution_allowed" in reason

    schema_explicit_local_runner = _report_payload("local-runner", "borrow")
    schema_explicit_local_runner["runner_policy"] = {"runner": "local", "execution_allowed": False}
    schema_explicit_local_runner["canonical_decision"] = {"decision": "pilot", "consumption_mode": "translation", "build_gate": "spike_original"}
    ok, reason = CampaignVerifier.validate_report_payload(schema_explicit_local_runner)
    assert ok is False
    assert "runner_policy.runner" in reason

    new_schema_local_runner = _report_payload("new-schema-local-runner", "borrow")
    new_schema_local_runner["runner_policy"] = {"runner": "local", "execution_allowed": False}
    new_schema_local_runner["campaign"] = {"campaign_id": "fixture", "repo_run_id": "repo_run_fixture"}
    ok, reason = CampaignVerifier.validate_report_payload(new_schema_local_runner)
    assert ok is False
    assert "runner_policy.runner" in reason

    clearly_legacy = _report_payload("legacy-local-runner", "borrow")
    clearly_legacy["runner_policy"] = {"runner": "local", "execution_allowed": False}
    clearly_legacy.pop("action_register")
    clearly_legacy.pop("canonical_decision", None)
    ok, reason = CampaignVerifier.validate_report_payload(clearly_legacy)
    assert ok is True
    assert reason is None

    missing_register = _report_payload("missing-register", "borrow")
    missing_register["canonical_decision"] = {"decision": "pilot", "consumption_mode": "translation", "build_gate": "spike_original"}
    missing_register.pop("action_register")
    ok, reason = CampaignVerifier.validate_report_payload(missing_register)
    assert ok is False
    assert "ActionRegister" in reason

    legacy = _report_payload("legacy", "borrow")
    legacy.pop("action_register")
    legacy.pop("canonical_decision", None)
    ok, reason = CampaignVerifier.validate_report_payload(legacy)
    assert ok is True
    assert reason is None


def test_report_payload_rejects_malformed_non_empty_action_register_for_current_schema(tmp_path: Path):
    payload = _report_payload("bad-action-register", "borrow")
    payload["canonical_decision"] = {
        "decision": "pilot",
        "consumption_mode": "translation",
        "build_gate": "spike_original",
    }
    payload["action_register"] = [{"garbage": True}]

    ok, reason = CampaignVerifier.validate_report_payload(payload)

    assert ok is False
    assert reason is not None
    assert "ActionRegister" in reason

    root = tmp_path / "campaigns"
    manifest = CampaignInitializer(root).initialize(
        repos=["https://github.com/example/bad-action-register"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="bad-action-register",
    )
    repo = manifest.repositories[0]
    report_path = Path(repo.root) / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    repo.report_path = report_path
    manifest.write_atomic()

    result = CampaignVerifier(manifest.path).verify()

    assert result.validated_report_paths == []
    assert result.excluded_report_paths == [report_path]
    assert any("ActionRegister" in error for error in result.errors)


def test_report_payload_accepts_structurally_valid_current_action_register():
    payload = _report_payload("valid-action-register", "borrow")
    payload["canonical_decision"] = {
        "decision": "pilot",
        "consumption_mode": "translation",
        "build_gate": "spike_original",
    }

    ok, reason = CampaignVerifier.validate_report_payload(payload)

    assert ok is True
    assert reason is None


def test_canonical_decision_matrix_case_normalized_and_contradictions_fail_closed():
    assert canonicalize_decision(collector_verdict="ReJeCt").decision == "skip"
    assert canonicalize_decision(collector_verdict="DISCARD").decision == "skip"

    bad_cases = [
        ("skip", "pattern", "describe_only", "investigate", "skip with non-none consumption"),
        ("skip", "none", "build_original", "investigate", "skip with build gate"),
        ("adopt", "none", "build_original", "adopt", "adopt with none"),
        ("pilot", "integration", "describe_only", "borrow", "integration with non-permissive gate"),
        ("adopt", "integration", "build_original", "ReJeCt", "positive with reject"),
        ("pilot", "translation", "spike_original", "DISCARD", "positive with discard"),
        ("block", "none", "blocked_needs_owner", "watch", "block without owner gate"),
        ("watch", "pattern", "blocked_needs_owner", "watch", "blocked gate without block"),
    ]
    for decision, mode, gate, verdict, label in bad_cases:
        with pytest.raises(ValueError, match="conflicts|requires"):
            canonicalize_decision(
                collector_verdict=verdict,
                decision=decision,
                consumption_mode=mode,
                build_gate=gate,
            ), label

    blocked = canonicalize_decision(collector_verdict="watch", owner_gate="credentials")
    assert blocked.decision == "block"
    assert blocked.build_gate == BuildGateState.BLOCKED_NEEDS_OWNER


def test_campaign_lock_prevents_active_takeover_unless_same_owner_or_force(tmp_path: Path):
    root = tmp_path / "campaigns"
    initializer = CampaignInitializer(root)
    first = initializer.initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="locked-campaign",
    )

    same_owner = initializer.initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="locked-campaign",
    )
    assert same_owner.lock.owner == first.lock.owner
    assert same_owner.lock.expires_at > same_owner.lock.created_at

    with pytest.raises(ValueError, match="active campaign lock"):
        initializer.initialize(
            repos=["https://github.com/example/healthy"],
            mode="standard",
            scout_controller="controller-b",
            verifier="qa-a",
            candidate_owner="owner-a",
            campaign_id="locked-campaign",
        )

    forced = initializer.initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-b",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="locked-campaign",
        force_lock=True,
    )
    assert forced.lock.owner == "controller-b"
    assert forced.lock.expires_at > forced.lock.created_at
    checkpoints = [checkpoint.name for checkpoint in forced.checkpoints]
    assert "lock_taken_over" in checkpoints
    takeover = next(checkpoint for checkpoint in forced.checkpoints if checkpoint.name == "lock_taken_over")
    assert takeover.details["previous_owner"] == "controller-a"
    assert takeover.details["new_owner"] == "controller-b"

    payload = json.loads(forced.path.read_text(encoding="utf-8"))
    payload["lock"]["owner"] = "controller-expired"
    payload["lock"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    forced.path.write_text(json.dumps(payload), encoding="utf-8")

    expired_takeover = initializer.initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-c",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="locked-campaign",
    )
    assert expired_takeover.lock is not None
    assert expired_takeover.lock.owner == "controller-c"
    assert expired_takeover.lock.expires_at > expired_takeover.lock.created_at
    expired_takeover_checkpoint = next(
        checkpoint for checkpoint in expired_takeover.checkpoints if checkpoint.name == "lock_taken_over"
    )
    assert expired_takeover_checkpoint.details["previous_owner"] == "controller-expired"
    assert expired_takeover_checkpoint.details["new_owner"] == "controller-c"


def test_campaign_verifier_enforces_manifest_paths_report_paths_and_baseline_no_exec(tmp_path: Path):
    root = tmp_path / "campaigns"
    manifest = CampaignInitializer(root).initialize(
        repos=["https://github.com/example/healthy"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="containment",
    )
    repo = manifest.repositories[0]
    outside_report = tmp_path / "campaigns_evil" / "report.json"
    outside_report.parent.mkdir()
    outside_report.write_text(json.dumps(_report_payload("healthy", "borrow")), encoding="utf-8")
    repo.report_path = outside_report
    manifest.baseline["runner"] = "local"
    manifest.write_atomic()

    result = CampaignVerifier(manifest.path).verify()

    assert result.passed is False
    assert any("baseline" in error and "no-exec" in error for error in result.errors)
    assert any("report_path" in error and "campaign root" in error for error in result.errors)


def test_campaign_verifier_rejects_report_identity_mismatch(tmp_path: Path):
    root = tmp_path / "campaigns"
    manifest = CampaignInitializer(root).initialize(
        repos=["https://github.com/local/fixture"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="identity",
    )
    repo = manifest.repositories[0]
    report_path = Path(repo.root) / "report.json"
    report_path.write_text(json.dumps(_report_payload("octocat", "borrow")), encoding="utf-8")
    repo.report_path = report_path
    manifest.write_atomic()

    result = CampaignVerifier(manifest.path).verify()

    assert result.passed is False
    assert result.validated_report_paths == []
    assert any("repo identity" in error and "local/fixture" in error and "octocat" in error for error in result.errors)


def test_campaign_verifier_allows_obvious_report_url_normalization(tmp_path: Path):
    root = tmp_path / "campaigns"
    manifest = CampaignInitializer(root).initialize(
        repos=["https://github.com/Example/Project.git"],
        mode="standard",
        scout_controller="controller-a",
        verifier="qa-a",
        candidate_owner="owner-a",
        campaign_id="identity-normalized",
    )
    repo = manifest.repositories[0]
    report_path = Path(repo.root) / "report.json"
    payload = _report_payload("project", "borrow")
    payload["repo"]["url"] = "https://github.com/example/project/"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    repo.report_path = report_path
    manifest.write_atomic()

    result = CampaignVerifier(manifest.path).verify()

    assert result.validated_report_paths == [report_path]
    assert not any("repo identity" in error for error in result.errors)


def test_campaign_fixture_scenarios_drive_behavior_not_metadata_only(tmp_path: Path):
    fixture_path = (Path(__file__).resolve().parent / "fixtures/campaign/campaign_scenarios.json")
    scenarios = json.loads(fixture_path.read_text(encoding="utf-8"))["scenarios"]

    observed = {}
    for case in scenarios:
        outcome = CampaignVerifier.validate_fixture_scenario(case)
        observed[case["id"]] = outcome

    assert observed["malicious_instruction"].summary["blocked_repositories"] == 1
    assert observed["license_ambiguity"].summary["blocked_repositories"] == 1
    assert observed["backlog_duplicate"].summary["routed_duplicates"] == 1
    assert observed["no_fit"].summary["skipped_no_fit"] == 1
    assert observed["unchanged_refresh"].summary["refresh_no_material_delta"] == 1
    assert observed["multi_repo_partial_failure"].partial_success is True
    assert observed["interrupted_resume"].summary["resume_required"] == 1
    assert observed["verdict_conflict"].passed is False


def _report_payload(name: str, verdict: str) -> dict:
    return {
        "schema_version": "repo-scout.report.v1",
        "run_id": f"run-{name}",
        "repo": {"url": f"https://github.com/example/{name}", "name": name},
        "status": "completed",
        "summary": "fixture report",
        "verdict": {"decision": verdict, "summary": "fixture", "confidence": 0.7},
        "describe_build_gate": {
            "source_tier": "ordinary_oss",
            "state": "peer_review",
            "can_direct_consume": False,
            "requires_peer_review": True,
            "original_implementation_only": True,
            "reasons": ["fixture"],
            "risk_finding_refs": [],
        },
        "action_register": [
            {
                "action_id": "action-fixture",
                "action_type": "buildbrief_candidate",
                "title": "Review fixture idea",
                "target_surfaces": [],
                "recommendation": "promote_to_candidate",
                "why": "fixture",
                "evidence_refs": [],
                "risk_notes": [],
                "owner_gate": "none",
                "next_registration_step": "candidate_ledger",
                "confidence": 0.7,
                "registration_status": "generated_from_report",
            }
        ],
        "runner_policy": {"runner": "no-exec", "execution_allowed": False},
        "metrics": {"target_code_execution": False},
        "evidence": [{"id": "E1", "type": "metadata", "source_uri": "fixture", "summary": "fixture", "collected_by": "fixture"}],
        "artifacts": [],
    }

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "repo-scout.run-ledger.v1"
DEFAULT_FIXTURE_RUN_ID = "fixture-EXAMPLE-197-run-ledger"

FORBIDDEN_OUTPUT_TERMS = (
    "yoheinakajima",
    "activegraph",
    "github.com/",
    "/home/",
    "/users/",
    "c:\\",
    "file://",
    "api_key",
    "apikey",
    "password",
    "private_key",
    "token=",
    "bearer ",
)

RecordKind = Literal["frame", "event", "evidence", "risk", "memo", "decision", "replay", "fork"]
RelationKind = Literal["contains", "supports", "flags", "summarizes", "decides", "replays", "forks_from"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def assert_sanitized_payload(payload: Any) -> None:
    """Reject raw source leakage, local paths, and secret-like text in durable artifacts."""

    text = json.dumps(payload, sort_keys=True, ensure_ascii=False).lower()
    leaked = [term for term in FORBIDDEN_OUTPUT_TERMS if term in text]
    if leaked:
        raise ValueError(f"run-ledger artifact contains forbidden term(s): {', '.join(leaked)}")


class LedgerRelation(BaseModel):
    source_id: str
    relation: RelationKind
    target_id: str


class LedgerRecord(BaseModel):
    id: str
    kind: RecordKind
    title: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    risk_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_source_leaks(self):
        assert_sanitized_payload(self.model_dump(mode="json"))
        return self


class RunLedgerArtifact(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    issue_refs: list[str] = Field(default_factory=lambda: ["EXAMPLE-195", "EXAMPLE-197"])
    generated_by: str = "repo-scout original fixture renderer"
    source_boundary: str = (
        "Synthetic records only. External repository material is source intelligence, "
        "not implementation input; no upstream source, prompts, schemas, examples, "
        "fixtures, config, docs, commands, dependencies, or runtime behavior are copied."
    )
    gate_snapshot: dict[str, Any]
    records: list[LedgerRecord]
    relations: list[LedgerRelation]

    @model_validator(mode="after")
    def validate_artifact(self):
        ids = {record.id for record in self.records}
        if len(ids) != len(self.records):
            raise ValueError("record ids must be unique")
        for relation in self.relations:
            if relation.source_id not in ids or relation.target_id not in ids:
                raise ValueError(f"relation references unknown record: {relation}")
        kinds = {record.kind for record in self.records}
        missing = {"frame", "event", "evidence", "risk", "memo", "decision", "replay", "fork"} - kinds
        if missing:
            raise ValueError(f"fixture is missing required record kind(s): {sorted(missing)}")
        assert_sanitized_payload(self.model_dump(mode="json"))
        return self


def build_fixture_run_ledger(run_id: str = DEFAULT_FIXTURE_RUN_ID) -> RunLedgerArtifact:
    """Build a deterministic original run-ledger fixture."""

    records = [
        LedgerRecord(
            id="frame-001",
            kind="frame",
            title="Repo-scout capability-consumption frame",
            summary="Bounded no-copy implementation lane for turning a reviewed idea into local artifacts.",
            metadata={"owner": "operations", "mode": "fixture_only", "target": "repo-scout"},
        ),
        LedgerRecord(
            id="event-001",
            kind="event",
            title="Gate accepted before artifact rendering",
            summary="Control packet allows only synthetic schema, fixture, and renderer work.",
            evidence_refs=["gate-001"],
        ),
        LedgerRecord(
            id="evidence-001",
            kind="evidence",
            title="Evidence reference preserved without excerpt copy",
            summary="Artifact stores stable evidence ids and reviewer decisions, not raw external text.",
            evidence_refs=["source-card-redacted", "council-decision-redacted"],
            metadata={"source_text_retained": False, "citations_redacted": True},
        ),
        LedgerRecord(
            id="risk-001",
            kind="risk",
            title="Source-contamination guard",
            summary="Any raw source excerpt, local path, secret-like text, or upstream identifier fails validation.",
            risk_refs=["no-copy", "no-exec", "no-secret-leakage"],
            metadata={"severity": "high", "fail_closed": True},
        ),
        LedgerRecord(
            id="memo-001",
            kind="memo",
            title="Operator memo",
            summary="Use the ledger to explain what changed, what was verified, and what remains gated.",
            evidence_refs=["event-001", "risk-001"],
        ),
        LedgerRecord(
            id="decision-001",
            kind="decision",
            title="Proceed with original fixture renderer",
            summary="The safe value is deterministic traceability output, not adopting an external runtime.",
            evidence_refs=["evidence-001"],
            risk_refs=["risk-001"],
            metadata={"verdict": "reshape", "confidence": 0.74},
        ),
        LedgerRecord(
            id="replay-001",
            kind="replay",
            title="Replay summary",
            summary="A reader can reconstruct selected frame, gates, risks, decision, and next action from records alone.",
            evidence_refs=["frame-001", "decision-001"],
        ),
        LedgerRecord(
            id="fork-001",
            kind="fork",
            title="Deferred adapter fork",
            summary="Future adapters must start from a new approved issue and keep this fixture artifact immutable.",
            risk_refs=["runtime-gated", "adapter-gated"],
            metadata={"requires_new_issue": True},
        ),
    ]
    relations = [
        LedgerRelation(source_id="frame-001", relation="contains", target_id="event-001"),
        LedgerRelation(source_id="event-001", relation="supports", target_id="evidence-001"),
        LedgerRelation(source_id="risk-001", relation="flags", target_id="decision-001"),
        LedgerRelation(source_id="memo-001", relation="summarizes", target_id="decision-001"),
        LedgerRelation(source_id="decision-001", relation="decides", target_id="replay-001"),
        LedgerRelation(source_id="fork-001", relation="forks_from", target_id="decision-001"),
    ]
    return RunLedgerArtifact(
        run_id=run_id,
        gate_snapshot={
            "can_direct_consume": False,
            "original_implementation_only": True,
            "target_code_execution": False,
            "fixture_only": True,
            "runtime_service": False,
        },
        records=records,
        relations=relations,
    )


def render_run_ledger_json(artifact: RunLedgerArtifact) -> str:
    return artifact.model_dump_json(indent=2, by_alias=True) + "\n"


def render_run_ledger_markdown(artifact: RunLedgerArtifact) -> str:
    lines = [
        "---",
        f"schema_version: {artifact.schema_version}",
        f"run_id: {artifact.run_id}",
        "issue_refs: [" + ", ".join(artifact.issue_refs) + "]",
        "fixture_only: true",
        "---",
        "",
        f"# Run ledger fixture — {artifact.run_id}",
        "",
        "## Source boundary",
        "",
        artifact.source_boundary,
        "",
        "## Gate snapshot",
        "",
    ]
    for key in sorted(artifact.gate_snapshot):
        value = artifact.gate_snapshot[key]
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"- `{key}`: `{rendered}`")
    lines += [
        "",
        "## Records",
        "",
        "| ID | Kind | Title | Evidence | Risks |",
        "|---|---|---|---|---|",
    ]
    for record in artifact.records:
        evidence = ", ".join(record.evidence_refs) or "—"
        risks = ", ".join(record.risk_refs) or "—"
        lines.append(f"| `{record.id}` | `{record.kind}` | {record.title} | {evidence} | {risks} |")
        lines.append(f"\n  - Summary: {record.summary}")
    lines += [
        "",
        "## Relations",
        "",
        "| Source | Relation | Target |",
        "|---|---|---|",
    ]
    for relation in artifact.relations:
        lines.append(f"| `{relation.source_id}` | `{relation.relation}` | `{relation.target_id}` |")
    lines += [
        "",
        "## Determinism and hygiene",
        "",
        "- JSON output is generated from the same model as this Markdown output.",
        "- The model rejects raw upstream identifiers, local paths, URL copies, and secret-like text.",
        "- Adapter/runtime work is deferred to a separately approved issue.",
    ]
    text = "\n".join(lines) + "\n"
    assert_sanitized_payload({"markdown": text})
    return text


def write_run_ledger_outputs(out_dir: Path, run_id: str = DEFAULT_FIXTURE_RUN_ID) -> dict[str, Path]:
    artifact = build_fixture_run_ledger(run_id=run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "run-ledger.json"
    markdown_path = out_dir / "run-ledger.md"
    json_path.write_text(render_run_ledger_json(artifact), encoding="utf-8")
    markdown_path.write_text(render_run_ledger_markdown(artifact), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}

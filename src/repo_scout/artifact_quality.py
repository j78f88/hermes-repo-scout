from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

SEVERITIES = {"blocker", "high", "medium", "low"}
CONTROLLED_FRONTMATTER_TAGS = frozenset(
    {
        "topic/agents",
        "topic/software-architecture",
        "topic/research-methods",
        "pattern/automation",
        "pattern/governance",
        "source/adapted",
        "source/original",
        "lens/enterprise",
        "meta/agents",
    }
)
_ALLOWED_FRONTMATTER_KEYS = frozenset(
    {
        "title",
        "created",
        "date",
        "type",
        "stage",
        "status",
        "project",
        "system",
        "capability",
        "source_url",
        "source_kind",
        "source_owner",
        "source_repo",
        "source_commit",
        "council_pack",
        "opus_review",
        "opus_artifact_review_review",
        "provenance_note",
        "consumed_by",
        "consumed_on",
        "owner_project",
        "owner_repo",
        "review",
        "benefit",
        "ingest_batch",
        "ingest_rank",
        "build_rank",
        "repo_scout_status",
        "repo_scout_gate",
        "repo_scout_verdict",
        "confidence",
        "tracker_parent",
        "tracker_child",
        "tracker_children",
        "approval_status",
        "review_date",
        "tags",
        "related",
    }
)

Detector = Callable[[str, str], list[str]]


@dataclass(frozen=True)
class ArtifactQualityRule:
    id: str
    category: str
    severity: str
    rationale: str
    detector: str
    required_evidence: tuple[str, ...]
    remediation: str
    check: Detector = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"Unsupported severity for {self.id}: {self.severity}")
        if not self.required_evidence:
            raise ValueError(f"Rule {self.id} must declare required evidence")


@dataclass(frozen=True)
class ArtifactQualityFinding:
    rule_id: str
    category: str
    severity: str
    rationale: str
    evidence: tuple[str, ...]
    remediation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class ArtifactQualityArtifactReport:
    path: str
    passed: bool
    findings: tuple[ArtifactQualityFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "passed": self.passed,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ArtifactQualityReport:
    schema_version: str
    passed: bool
    summary: dict[str, int]
    artifacts: tuple[ArtifactQualityArtifactReport, ...]
    rules: tuple[ArtifactQualityRule, ...]

    @property
    def findings(self) -> tuple[ArtifactQualityFinding, ...]:
        return tuple(finding for artifact in self.artifacts for finding in artifact.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "summary": self.summary,
            "rules": [
                {
                    "id": rule.id,
                    "category": rule.category,
                    "severity": rule.severity,
                    "rationale": rule.rationale,
                    "detector": rule.detector,
                    "required_evidence": list(rule.required_evidence),
                    "remediation": rule.remediation,
                }
                for rule in self.rules
            ],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_POINTER_RE = re.compile(r"(?:/[^\s`'\"]+|\b[\w.-]+\.(?:json|md|txt|yaml|yml)\b|https?://\S+|\[.+?\]\(.+?\))")
_INSTRUCTION_RE = re.compile(r"\b(ignore (?:previous|prior) instructions|install (?:the )?package|run (?:the )?(?:hook|command|script)|treat .* as trusted|execute (?:the )?(?:readme|command|script))\b", re.IGNORECASE)
_RANKING_RE = re.compile(r"^\s*\d+[.)]\s+.+$", re.MULTILINE)
_RATIONALE_RE = re.compile(r"\b(because|due to|rationale|reason|supports|evidence|ranked .* because|addresses|lacks)\b", re.IGNORECASE)
_RECOMMENDATION_RE = re.compile(r"\b(recommendation|recommend|proceed|approve|ship|adopt|reshape)\b", re.IGNORECASE)
_COUNTERARGUMENT_RE = re.compile(r"\b(counterargument|counter-argument|trade-?off|limitation|risk|however|but)\b", re.IGNORECASE)
_VALIDATION_RE = re.compile(r"\b(validation status|validated|tests? (?:passing|pass|failed|not run)|not validated|verification)\b", re.IGNORECASE)
_STOP_RE = re.compile(r"\b(stop conditions?|stop if|pause if|escalate before|do not proceed if)\b", re.IGNORECASE)
_READER_VALUE_RE = re.compile(r"\b(reader[- ]value summary|reader value|value summary|why this matters|what owner gets|useful because)\b", re.IGNORECASE)
_EVIDENCE_RE = re.compile(r"\b(evidence snippets?|evidence basis|supporting evidence|excerpt|snippet)\b", re.IGNORECASE)


def _headings(markdown: str) -> list[str]:
    return [match.group("title").strip().lower() for match in _HEADING_RE.finditer(markdown)]


def _frontmatter(markdown: str) -> tuple[dict[str, object], list[str]]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, []
    body = match.group("body")
    values: dict[str, object] = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        key_match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not key_match:
            i += 1
            continue
        key, raw_value = key_match.group(1), key_match.group(2).strip()
        if raw_value:
            if raw_value.startswith("[") and raw_value.endswith("]"):
                items = [item.strip().strip("'\"") for item in raw_value[1:-1].split(",") if item.strip()]
                values[key] = items
            else:
                values[key] = raw_value.strip("'\"")
            i += 1
            continue
        items: list[str] = []
        j = i + 1
        while j < len(lines):
            item_match = re.match(r"^\s+-\s+(.+?)\s*$", lines[j])
            if not item_match:
                break
            items.append(item_match.group(1).strip().strip("'\""))
            j += 1
        values[key] = items if items else ""
        i = j
    return values, lines


def _line_excerpt(markdown: str, pattern: re.Pattern[str]) -> str:
    for line in markdown.splitlines():
        if pattern.search(line):
            return line.strip()[:240]
    return "pattern present"


def _missing_evidence_snippets(path: str, markdown: str) -> list[str]:
    has_evidence_label = bool(_EVIDENCE_RE.search(markdown))
    has_concrete_snippet = bool(re.search(r"[`\"“].{8,}?[`\"”]", markdown))
    if has_evidence_label and has_concrete_snippet:
        return []
    return ["Artifact does not include a named evidence section with concrete quoted/backticked snippets."]


def _pointer_only_output(path: str, markdown: str) -> list[str]:
    pointers = _POINTER_RE.findall(markdown)
    has_evidence = bool(_EVIDENCE_RE.search(markdown))
    has_reader_value = bool(_READER_VALUE_RE.search(markdown))
    if pointers and not has_evidence and not has_reader_value:
        return [f"Artifact points readers at other files/URLs without enough local readback: {pointers[0][:160]}"]
    return []


def _ranking_without_rationale(path: str, markdown: str) -> list[str]:
    ranked_lines = [line.strip() for line in markdown.splitlines() if _RANKING_RE.match(line)]
    weak = [line for line in ranked_lines if not _RATIONALE_RE.search(line)]
    if weak:
        return ["Ranked item lacks explicit rationale: " + weak[0][:180]]
    return []


def _recommendation_without_counterargument(path: str, markdown: str) -> list[str]:
    if _RECOMMENDATION_RE.search(markdown) and not _COUNTERARGUMENT_RE.search(markdown):
        return ["Recommendation appears without a counterargument, limitation, risk, or trade-off."]
    return []


def _missing_validation_status(path: str, markdown: str) -> list[str]:
    if _VALIDATION_RE.search(markdown):
        return []
    return ["Artifact does not state validation status, test status, verification, or not-run status."]


def _missing_stop_conditions(path: str, markdown: str) -> list[str]:
    if _STOP_RE.search(markdown):
        return []
    return ["Artifact does not name stop conditions or escalation boundaries."]


def _uncontrolled_tags_frontmatter(path: str, markdown: str) -> list[str]:
    frontmatter, _lines = _frontmatter(markdown)
    evidence: list[str] = []
    if not frontmatter:
        return []
    for key in sorted(frontmatter):
        if key not in _ALLOWED_FRONTMATTER_KEYS:
            evidence.append(f"Uncontrolled frontmatter key: {key}")
    tags_value = frontmatter.get("tags", [])
    tags = tags_value if isinstance(tags_value, list) else [str(tags_value)]
    for tag in tags:
        if tag and tag not in CONTROLLED_FRONTMATTER_TAGS:
            evidence.append(f"Uncontrolled tag: {tag}")
    return evidence


def _source_text_treated_as_instructions(path: str, markdown: str) -> list[str]:
    evidence: list[str] = []
    for line in markdown.splitlines():
        if not _INSTRUCTION_RE.search(line):
            continue
        lowered = line.lower()
        if "not an instruction" in lowered or "data only" in lowered or "do not execute" in lowered:
            continue
        evidence.append(line.strip()[:240])
    return evidence


def _missing_reader_value_summary(path: str, markdown: str) -> list[str]:
    if _READER_VALUE_RE.search(markdown):
        return []
    return ["Artifact does not summarize what the reader gets from this artifact before following links or details."]


def default_artifact_quality_rules() -> tuple[ArtifactQualityRule, ...]:
    """Return owner-facing artifact quality rules original to repo-scout."""
    return (
        ArtifactQualityRule(
            id="missing-evidence-snippets",
            category="evidence",
            severity="high",
            rationale="Low-information artifacts make claims without local proof Owner can inspect quickly.",
            detector="requires evidence/snippet labeling plus concrete quoted or backticked support",
            required_evidence=("evidence section or label", "quoted/backticked snippet"),
            remediation="Add an Evidence snippets section with short concrete excerpts and source labels.",
            check=_missing_evidence_snippets,
        ),
        ArtifactQualityRule(
            id="artifact-pointer-only-output",
            category="reader-value",
            severity="high",
            rationale="Pointer-only readbacks force the reader to open other artifacts before receiving value.",
            detector="flags file/URL pointers when reader-value and evidence summaries are absent",
            required_evidence=("local summary", "local evidence"),
            remediation="Summarize the conclusion and key evidence in the artifact, then link supporting files.",
            check=_pointer_only_output,
        ),
        ArtifactQualityRule(
            id="ranking-without-rationale",
            category="decision-quality",
            severity="medium",
            rationale="Ranked candidates without reasons are not reviewable or improvable.",
            detector="ordered-list ranking lines must include reason/rationale language",
            required_evidence=("ranked item", "rationale term"),
            remediation="Add a because/due-to rationale to each ranked recommendation item.",
            check=_ranking_without_rationale,
        ),
        ArtifactQualityRule(
            id="recommendation-without-counterargument",
            category="decision-quality",
            severity="medium",
            rationale="Recommendations need at least one limitation, risk, or counterargument to prevent one-way advocacy.",
            detector="recommendation language requires counterargument/risk/trade-off language",
            required_evidence=("recommendation", "counterargument or risk"),
            remediation="Add the strongest counterargument or a specific limitation before asking for action.",
            check=_recommendation_without_counterargument,
        ),
        ArtifactQualityRule(
            id="missing-validation-status",
            category="validation",
            severity="high",
            rationale="Owner needs to know whether claims were tested, fixture-only, not run, or still unvalidated.",
            detector="requires validation/test/verification/not-run status language",
            required_evidence=("validation status",),
            remediation="State validation status explicitly, including commands or why validation was not run.",
            check=_missing_validation_status,
        ),
        ArtifactQualityRule(
            id="missing-stop-conditions",
            category="safety",
            severity="high",
            rationale="Source-consumption and automation work needs explicit boundaries before expansion.",
            detector="requires stop condition, stop-if, pause-if, or escalation boundary language",
            required_evidence=("stop conditions",),
            remediation="Add stop conditions that say when to pause, escalate, or avoid scope expansion.",
            check=_missing_stop_conditions,
        ),
        ArtifactQualityRule(
            id="uncontrolled-tags-frontmatter",
            category="governance",
            severity="medium",
            rationale="Free-form metadata creates report and review sprawl instead of controlled lifecycle signals.",
            detector="frontmatter keys and tags must be in repo-scout's controlled allow-list",
            required_evidence=("frontmatter", "controlled tags"),
            remediation="Use controlled tags/frontmatter or omit metadata until an owner approves new terms.",
            check=_uncontrolled_tags_frontmatter,
        ),
        ArtifactQualityRule(
            id="source-text-treated-as-instructions",
            category="source-safety",
            severity="blocker",
            rationale="External source text is evidence only; artifacts must not turn source instructions into agent instructions.",
            detector="flags source-like imperative text unless explicitly framed as data-only/not-instruction",
            required_evidence=("source text boundary",),
            remediation="Rewrite source commands as inert evidence and state they are not instructions to execute.",
            check=_source_text_treated_as_instructions,
        ),
        ArtifactQualityRule(
            id="missing-reader-value-summary",
            category="reader-value",
            severity="medium",
            rationale="Artifacts should tell Owner the useful takeaway before implementation or review details.",
            detector="requires reader-value/why-this-matters/value-summary language",
            required_evidence=("reader value summary",),
            remediation="Add a short reader-value summary explaining what decision or benefit the artifact enables.",
            check=_missing_reader_value_summary,
        ),
    )


def evaluate_markdown_artifact(path: str, markdown: str, rules: tuple[ArtifactQualityRule, ...] | None = None) -> ArtifactQualityReport:
    rule_set = rules or default_artifact_quality_rules()
    findings: list[ArtifactQualityFinding] = []
    for rule in rule_set:
        evidence = tuple(rule.check(path, markdown))
        if evidence:
            findings.append(
                ArtifactQualityFinding(
                    rule_id=rule.id,
                    category=rule.category,
                    severity=rule.severity,
                    rationale=rule.rationale,
                    evidence=evidence,
                    remediation=rule.remediation,
                )
            )
    artifact = ArtifactQualityArtifactReport(path=path, passed=not findings, findings=tuple(findings))
    return _build_report((artifact,), rule_set)


def evaluate_markdown_artifacts(artifacts: Mapping[str, str], rules: tuple[ArtifactQualityRule, ...] | None = None) -> ArtifactQualityReport:
    rule_set = rules or default_artifact_quality_rules()
    artifact_reports: list[ArtifactQualityArtifactReport] = []
    for path in sorted(artifacts):
        single = evaluate_markdown_artifact(path, artifacts[path], rules=rule_set)
        artifact_reports.extend(single.artifacts)
    return _build_report(tuple(artifact_reports), rule_set)


def _build_report(artifacts: tuple[ArtifactQualityArtifactReport, ...], rules: tuple[ArtifactQualityRule, ...]) -> ArtifactQualityReport:
    finding_count = sum(len(artifact.findings) for artifact in artifacts)
    failed_artifact_count = sum(1 for artifact in artifacts if not artifact.passed)
    summary = {
        "artifact_count": len(artifacts),
        "finding_count": finding_count,
        "failed_artifact_count": failed_artifact_count,
    }
    return ArtifactQualityReport(
        schema_version="repo-scout.artifact-quality.v1",
        passed=finding_count == 0,
        summary=summary,
        artifacts=artifacts,
        rules=rules,
    )


def artifact_quality_report_to_json(report: ArtifactQualityReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


def artifact_quality_report_to_markdown(report: ArtifactQualityReport) -> str:
    lines = [
        "# Artifact quality report",
        "",
        f"Overall: {'PASS' if report.passed else 'FAIL'}",
        "",
        "## Summary",
        "",
        f"- Artifacts checked: {report.summary['artifact_count']}",
        f"- Failed artifacts: {report.summary['failed_artifact_count']}",
        f"- Findings: {report.summary['finding_count']}",
        "",
        "## Findings by artifact",
        "",
    ]
    for artifact in report.artifacts:
        lines.append(f"### {artifact.path}")
        lines.append("")
        lines.append(f"Status: {'PASS' if artifact.passed else 'FAIL'}")
        lines.append("")
        if not artifact.findings:
            lines.append("- No findings.")
            lines.append("")
            continue
        for finding in artifact.findings:
            lines.append(f"- **{finding.rule_id}** ({finding.severity}, {finding.category})")
            lines.append(f"  - Rationale: {finding.rationale}")
            for evidence in finding.evidence:
                lines.append(f"  - Evidence: {evidence}")
            lines.append(f"  - Remediation: {finding.remediation}")
        lines.append("")
    lines.extend(["## Rule inventory", ""])
    for rule in report.rules:
        lines.append(f"- `{rule.id}` — {rule.category}/{rule.severity}: {rule.detector}")
    return "\n".join(lines) + "\n"


def load_markdown_fixtures(directory: Path | str) -> dict[str, str]:
    root = Path(directory)
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.md"))}


def write_artifact_quality_outputs(report: ArtifactQualityReport, out_dir: Path | str, fmt: str = "both") -> dict[str, Path]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    normalized = "markdown" if fmt == "md" else fmt
    if normalized not in {"json", "markdown", "both"}:
        raise ValueError(f"Unsupported artifact quality output format: {fmt}")
    paths: dict[str, Path] = {}
    if normalized in {"json", "both"}:
        json_path = out_path / "artifact-quality.json"
        json_path.write_text(artifact_quality_report_to_json(report), encoding="utf-8")
        paths["json"] = json_path
    if normalized in {"markdown", "both"}:
        markdown_path = out_path / "artifact-quality.md"
        markdown_path.write_text(artifact_quality_report_to_markdown(report), encoding="utf-8")
        paths["markdown"] = markdown_path
    return paths

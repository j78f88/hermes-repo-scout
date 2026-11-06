from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field, field_validator

CAPABILITY_TAGS: tuple[str, ...] = (
    "skills-instructions",
    "evidence-graph",
    "agent-control-plane",
    "source-monitoring",
    "terminal-session",
    "model-serving",
    "ui-workspace",
    "knowledge-workflow",
    "governance",
    "quality-gates",
    "repo-automation",
    "graph-ui",
    "financial-agent",
    "hardware-automation",
)

RISK_TAGS: tuple[str, ...] = (
    "execution-risk",
    "secrets-risk",
    "source-contamination",
    "runtime-risk",
    "data-retention",
    "broad-platform",
    "low-signal",
    "cloud-dependency",
    "package-install-risk",
    "terminal-process-risk",
)

SOURCE_TAGS: tuple[str, ...] = (
    "readme-claim",
    "manifest-static",
    "inventory-static",
    "metadata-static",
    "inferred-static",
    "profile-static",
    "remote-snapshot",
    "handoff-packet",
)

_TAG_ORDER: dict[str, int] = {
    tag: idx
    for idx, tag in enumerate((*CAPABILITY_TAGS, *RISK_TAGS, *SOURCE_TAGS))
}
_ALLOWED_BY_FIELD = {
    "capability_tags": set(CAPABILITY_TAGS),
    "risk_tags": set(RISK_TAGS),
    "source_tags": set(SOURCE_TAGS),
}


def ordered_tags(tags: Iterable[str], allowed: Iterable[str]) -> list[str]:
    """Return unique controlled tags in taxonomy order."""

    allowed_set = set(allowed)
    unique = {str(tag).strip() for tag in tags if str(tag).strip() in allowed_set}
    return sorted(unique, key=lambda tag: _TAG_ORDER.get(tag, 10_000))


class TaxonomyTags(BaseModel):
    """Controlled repository taxonomy tag bundle.

    The model intentionally stores only controlled string tags so reports remain
    comparison-ready without introducing a breaking schema version change.
    """

    capability_tags: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)

    @field_validator("capability_tags", "risk_tags", "source_tags")
    @classmethod
    def validate_controlled_tags(cls, value: list[str], info: Any) -> list[str]:
        allowed = _ALLOWED_BY_FIELD[info.field_name]
        unknown = sorted({tag for tag in value if tag not in allowed})
        if unknown:
            raise ValueError(f"unknown {info.field_name}: {', '.join(unknown)}")
        return ordered_tags(value, allowed)

    def merged(self, *others: "TaxonomyTags") -> "TaxonomyTags":
        return TaxonomyTags(
            capability_tags=ordered_tags(
                [*self.capability_tags, *(tag for other in others for tag in other.capability_tags)],
                CAPABILITY_TAGS,
            ),
            risk_tags=ordered_tags(
                [*self.risk_tags, *(tag for other in others for tag in other.risk_tags)],
                RISK_TAGS,
            ),
            source_tags=ordered_tags(
                [*self.source_tags, *(tag for other in others for tag in other.source_tags)],
                SOURCE_TAGS,
            ),
        )


class TaxonomyTagged(BaseModel):
    capability_tags: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)

    @field_validator("capability_tags", "risk_tags", "source_tags")
    @classmethod
    def validate_controlled_taxonomy_tags(cls, value: list[str], info: Any) -> list[str]:
        return getattr(TaxonomyTags(**{info.field_name: value}), info.field_name)

    @property
    def taxonomy(self) -> TaxonomyTags:
        return TaxonomyTags(
            capability_tags=self.capability_tags,
            risk_tags=self.risk_tags,
            source_tags=self.source_tags,
        )


_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("capability", "skills-instructions", ("skill", "prompt", "playbook", "agent package", "recipe")),
    ("capability", "evidence-graph", ("evidence graph", "knowledge graph", "graph of", "relationship", "provenance", "citation")),
    ("capability", "agent-control-plane", ("agent", "multi-agent", "orchestrat", "delegate", "tool use", "control plane")),
    ("capability", "source-monitoring", ("source monitoring", "monitoring", "webhook", "change feed", "repository scan")),
    ("capability", "terminal-session", ("terminal session", "shell", "pty", "subprocess console")),
    ("capability", "model-serving", ("llm", "model", "inference", "serving", "embedding", "openai", "anthropic")),
    ("capability", "ui-workspace", ("ui workspace", "dashboard", "web app", "frontend", "canvas")),
    ("capability", "knowledge-workflow", ("knowledge", "memory", "notes", "research", "planning", "workflow", "task")),
    ("capability", "governance", ("governance", "approval policy", "audit policy", "compliance")),
    ("capability", "quality-gates", ("quality gate", "quality gates", "evaluation workflow", "eval workflow", "verification workflow")),
    ("capability", "repo-automation", ("repo automation", "repository automation", "pull request", "github automation", "code review automation")),
    ("capability", "graph-ui", ("graph ui", "graph view", "node graph", "visual graph", "timeline view")),
    ("capability", "financial-agent", ("financial", "finance", "trading", "portfolio", "invoice")),
    ("capability", "hardware-automation", ("hardware", "robot", "sensor", "gpio", "device", "firmware")),
    ("risk", "execution-risk", ("run ", "execute", "build", "test", "script", "command", "sandbox")),
    ("risk", "secrets-risk", ("secret", "token", "credential", "api key", "password")),
    ("risk", "source-contamination", ("prompt-injection", "source-contamination", "ignore previous", "system prompt", "direct-consume", "copy source")),
    ("risk", "runtime-risk", ("runtime", "daemon", "server", "background process", "service")),
    ("risk", "data-retention", ("retain", "snapshot", "store", "cache", "memory", "database")),
    ("risk", "broad-platform", ("platform", "framework", "everything", "end-to-end", "full-stack", "broad")),
    ("risk", "low-signal", ("badge", "sponsor", "table of contents", "install", "quickstart", "navbar", "route")),
    ("risk", "cloud-dependency", ("cloud", "aws", "azure", "gcp", "s3", "lambda", "hosted")),
    ("risk", "package-install-risk", ("pip install", "npm install", "pnpm install", "yarn install", "cargo install", "go get", "package manager")),
    ("risk", "terminal-process-risk", ("terminal", "shell", "pty", "subprocess", "process", "command")),
)


def infer_taxonomy_tags(
    *texts: str | None,
    evidence: Iterable[Any] = (),
    files: Iterable[Mapping[str, Any]] = (),
    source_tags: Iterable[str] = (),
    low_signal: bool = False,
) -> TaxonomyTags:
    """Infer deterministic taxonomy tags from static report text and metadata."""

    haystack_parts: list[str] = []
    for text in texts:
        if text:
            haystack_parts.append(str(text))
    inferred_source_tags = list(source_tags)
    for ev in evidence:
        ev_type = getattr(ev, "type", None)
        collected_by = str(getattr(ev, "collected_by", "") or "")
        source_uri = str(getattr(ev, "source_uri", "") or "")
        summary = str(getattr(ev, "summary", "") or "")
        excerpt = str(getattr(ev, "excerpt", "") or "")
        haystack_parts.extend([ev_type or "", collected_by, source_uri, summary, excerpt])
        if ev_type == "file_excerpt" or collected_by == "docs-claims":
            inferred_source_tags.append("readme-claim")
        elif ev_type == "manifest":
            inferred_source_tags.append("manifest-static")
        elif ev_type in {"file_presence", "license", "ci"} or collected_by == "inventory":
            inferred_source_tags.append("inventory-static")
        elif ev_type in {"metadata", "llm_observation"}:
            inferred_source_tags.append("metadata-static")
    for file_record in files:
        path = str(file_record.get("path", ""))
        kind = str(file_record.get("kind", ""))
        haystack_parts.extend([path, kind, str(file_record.get("ecosystem", ""))])
        if kind == "manifest":
            inferred_source_tags.append("manifest-static")
        elif kind:
            inferred_source_tags.append("inventory-static")
    haystack = "\n".join(haystack_parts).lower()
    cap: list[str] = []
    risk: list[str] = []
    for tag_kind, tag, needles in _PATTERNS:
        if any(needle in haystack for needle in needles):
            if tag_kind == "capability":
                cap.append(tag)
            else:
                risk.append(tag)
    if low_signal:
        risk.append("low-signal")
    if not inferred_source_tags and haystack_parts:
        inferred_source_tags.append("inferred-static")
    return TaxonomyTags(
        capability_tags=ordered_tags(cap, CAPABILITY_TAGS),
        risk_tags=ordered_tags(risk, RISK_TAGS),
        source_tags=ordered_tags(inferred_source_tags, SOURCE_TAGS),
    )


def merge_taxonomy_tags(items: Iterable[Any], *extra: TaxonomyTags) -> TaxonomyTags:
    tags = TaxonomyTags()
    for item in items:
        if item is None:
            continue
        tags = tags.merged(
            TaxonomyTags(
                capability_tags=list(getattr(item, "capability_tags", []) or []),
                risk_tags=list(getattr(item, "risk_tags", []) or []),
                source_tags=list(getattr(item, "source_tags", []) or []),
            )
        )
    return tags.merged(*extra)

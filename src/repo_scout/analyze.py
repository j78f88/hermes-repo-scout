from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .schemas import BuildBrief, BuildGateState, Claim, DescribeBuildGate, Dossier, Evidence, Finding, IdeaCandidate, IdeaCard, Locator, PackageContract, ProbeResult, ProbeStep, SourceRef, SourceTrustTier, Verdict, utc_now
from .taxonomy import RISK_TAGS, TaxonomyTags, infer_taxonomy_tags, merge_taxonomy_tags, ordered_tags

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^```")
CLAIM_HINT_RE = re.compile(
    r"\b(supports?|provides?|enables?|allows?|features?|compatible|secure|fast|simple|easy|install|installation|quickstart|run|usage|test|build)\b",
    re.IGNORECASE,
)
RELEVANT_HEADING_RE = re.compile(r"\b(install|installation|quickstart|getting started|usage|run|build|test|develop|development)\b", re.IGNORECASE)
COMMAND_RE = re.compile(r"^\s*(?:\$\s*)?(pip|python|pytest|npm|pnpm|yarn|make|cargo|go|docker)\b.+")
IDEA_HEADING_RE = re.compile(r"\b(architecture|design|pattern|approach|workflow|agent|pipeline|framework|integration|plugin|api|extension|why|how it works)\b", re.IGNORECASE)
HIGH_SIGNAL_HEADING_RE = re.compile(r"\b(overview|about|features?|capabilities|architecture|design|approach|how it works|workflow|system|concepts?|plugins?|api|extension|why)\b", re.IGNORECASE)
PURPOSE_SIGNAL_RE = re.compile(r"\b(is|are|provides?|enables?|allows?|helps?|lets?|turns?|uses?|builds?|automates?|coordinates?|orchestrates?|integrates?|generates?|tracks?|manages?|analy[sz]es?)\b", re.IGNORECASE)
LOW_SIGNAL_HEADING_RE = re.compile(r"\b(install|installation|quickstart|getting started|setup|usage|run|build|test|develop|development|requirements?|prerequisites?)\b", re.IGNORECASE)
NOISE_HEADING_RE = re.compile(r"\b(badges?|status|license|credits?|acknowledgements?|sponsors?|support|donate|contributors?|contributing|faq|table of contents|contents|navigation)\b", re.IGNORECASE)
BADGE_LINE_RE = re.compile(r"(?:shields\.io|badge\.svg|badgen\.net|github\.com/.+?/workflows?|actions/workflows|codecov|coveralls|img\.shields|\[!\[|<img[^>]+badge|<a[^>]+badge)", re.IGNORECASE)
TOC_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+)?\[[^\]]{1,80}\]\(#[^)]+\)\s*$")
PURE_LINK_RE = re.compile(r"^\s*(?:[-*+]\s*)?(?:\[[^\]]+\]\([^)]+\)|https?://\S+|<https?://[^>]+>)\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
SPONSOR_AD_RE = re.compile(r"\b(sponsor(?:ed|s)? by|become a sponsor|buy me a coffee|star (?:this|us)|follow (?:me|us)|join (?:our )?(discord|slack)|newsletter|promo|advertis)\b", re.IGNORECASE)
PROMPT_INJECTION_RE = re.compile(
    r"\b("
    r"ignore (?:all )?(?:previous|prior|above|system|developer) instructions|"
    r"disregard (?:all )?(?:previous|prior|above|system|developer) instructions|"
    r"you are (?:now|no longer)|"
    r"system prompt|developer message|hidden instructions|"
    r"reveal (?:your )?(?:prompt|instructions|secrets)|"
    r"exfiltrate|send (?:secrets|tokens|credentials)|"
    r"change (?:the )?(?:output route|runner|execution policy|build gate)|"
    r"set (?:runner|execution_allowed|build gate|gate state)|"
    r"direct[- ]?consume|copy (?:this|the) source|use this repo as-is|"
    r"run (?:the )?(?:commands?|tests?|build scripts?) now"
    r")\b",
    re.IGNORECASE,
)

TRUSTED_SOURCE_TIERS = {SourceTrustTier.TRUSTED_VENDOR_OR_OFFICIAL, SourceTrustTier.OWNER_APPROVED_DIRECT_CONSUME}

PYTHON_MANIFESTS = {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}
NODE_MANIFESTS = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
RUST_MANIFESTS = {"Cargo.toml", "Cargo.lock"}
GO_MANIFESTS = {"go.mod", "go.sum"}


@dataclass
class ReadmeSection:
    title: str
    level: int
    start_line: int
    end_line: int
    lines: list[tuple[int, str]] = field(default_factory=list)
    score: int = 0
    role: str = "general"

    @property
    def clean_lines(self) -> list[tuple[int, str]]:
        return [(line_no, text) for line_no, text in self.lines if _clean_readme_line(text)]

    @property
    def text(self) -> str:
        return "\n".join(text for _, text in self.clean_lines)


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=<REDACTED>", redacted)
    return redacted


def safe_read_text(repo_root: Path, rel_or_path: str | Path, *, max_bytes: int = 64_000) -> str | None:
    """Read bounded, redacted text without following symlinks or escaping root."""
    root = repo_root.resolve()
    path = (root / rel_or_path).resolve() if not Path(rel_or_path).is_absolute() else Path(rel_or_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    original = root / rel_or_path if not Path(rel_or_path).is_absolute() else Path(rel_or_path)
    if original.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        return redact_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _classify_claim(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["install", "installation", "quickstart", "usage", "run ", "npm ", "pip ", "make "]):
        return "install"
    if any(w in low for w in ["security", "secure", "safe"]):
        return "security"
    if any(w in low for w in ["compatible", "supports python", "supports node"]):
        return "compatibility"
    if any(w in low for w in ["architecture", "plugin", "extension", "api"]):
        return "architecture"
    if any(w in low for w in ["maintained", "stable", "production"]):
        return "maintenance"
    return "capability"


def _strip_prompt(line: str) -> str:
    return line.strip().removeprefix("$ ").strip()


def _network_for_commands(commands: list[str]) -> str:
    if any(c.startswith(("pip install", "npm install", "npm ci", "pnpm install", "yarn install", "cargo install", "go get")) for c in commands):
        return "package-index"
    return "none"


def _plain_markdown_text(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -*\t|:")


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _clean_readme_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if BADGE_LINE_RE.search(stripped) or TOC_LINE_RE.match(stripped) or PURE_LINK_RE.match(stripped) or TABLE_SEPARATOR_RE.match(stripped):
        return None
    if SPONSOR_AD_RE.search(stripped):
        return None
    if _is_table_row(stripped):
        # Tables are usually badges, nav, TOCs, compatibility matrices, or install
        # snippets. Keep them out of claim/idea extraction so row fragments do
        # not become capability cards.
        return None
    plain = _plain_markdown_text(stripped)
    if not plain or len(plain) < 3:
        return None
    if plain.lower() in {"license", "build", "docs", "tests", "coverage", "status"}:
        return None
    return plain


def _section_role(title: str) -> str:
    low = title.lower()
    if NOISE_HEADING_RE.search(low):
        return "noise"
    if LOW_SIGNAL_HEADING_RE.search(low):
        return "install"
    if HIGH_SIGNAL_HEADING_RE.search(low):
        return "high_signal"
    return "general"


def _score_readme_section(section: ReadmeSection, *, first_title: str | None = None) -> int:
    title = section.title.strip()
    role = _section_role(title)
    score = 0
    if role == "noise":
        score -= 12
    elif role == "install":
        score -= 4
    elif role == "high_signal":
        score += 9
    clean_text = section.text
    if PURPOSE_SIGNAL_RE.search(clean_text):
        score += 5
    if IDEA_HEADING_RE.search(title) or IDEA_HEADING_RE.search(clean_text[:600]):
        score += 4
    if len(clean_text) >= 80:
        score += 2
    if len(clean_text) > 1800:
        score -= 2
    section.role = role
    section.score = score
    return score


def parse_readme_sections(readme_text: str) -> list[ReadmeSection]:
    """Parse README markdown into ranked sections, excluding code blocks.

    The parser is deliberately lightweight and static: it does not render
    markdown or fetch links. It provides enough structure to prioritize title,
    overview/about/features/architecture/how-it-works content over badge, TOC,
    table, sponsor, and install boilerplate noise.
    """
    lines = readme_text.splitlines()
    sections: list[ReadmeSection] = []
    current = ReadmeSection(title="README", level=0, start_line=1, end_line=len(lines) or 1)
    in_code = False
    first_title: str | None = None
    for idx, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            if current.lines or current.title != "README":
                current.end_line = idx - 1
                sections.append(current)
            level = len(heading.group(1))
            title = _plain_markdown_text(heading.group(2).strip()) or heading.group(2).strip()
            first_title = first_title or title
            current = ReadmeSection(title=title, level=level, start_line=idx, end_line=idx)
            continue
        current.lines.append((idx, line))
    if current.lines or current.title != "README":
        current.end_line = len(lines) or current.start_line
        sections.append(current)
    for section in sections:
        _score_readme_section(section, first_title=first_title)
    return sorted(sections, key=lambda s: (-s.score, s.start_line))


def _candidate_sentence_from_section(section: ReadmeSection, *, max_len: int = 300) -> tuple[int, str] | None:
    for line_no, line in section.clean_lines:
        text = _plain_markdown_text(line)
        if 20 <= len(text) <= max_len and PURPOSE_SIGNAL_RE.search(text):
            return line_no, text
    text = " ".join(_plain_markdown_text(line) for _, line in section.clean_lines)
    text = re.split(r"(?<=[.!?])\s+", text)[0].strip()
    if 20 <= len(text) <= max_len:
        first_line = section.clean_lines[0][0] if section.clean_lines else section.start_line
        return first_line, text
    return None


def _metadata_description(repo_snapshot) -> str | None:
    for attr in ("description", "summary"):
        value = getattr(repo_snapshot, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def make_not_run_result(step: ProbeStep, idx: int) -> ProbeResult:
    return ProbeResult(
        id=f"PR{idx}",
        probe_step_id=step.id,
        status="not_run",
        runner={"type": "no-exec", "network": "metadata", "target_code_execution": False},
        claim_refs=step.claim_refs,
        failure_summary="Runner policy did not allow execution; target repository code was not executed.",
    )


def _source_tier_value(value: object) -> SourceTrustTier | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return SourceTrustTier(value)
    except ValueError:
        return None


def classify_source_trust(repo_snapshot, profile: dict | None = None) -> tuple[SourceTrustTier, list[str]]:
    """Classify whether a source may ever bypass describe→review.

    Default GitHub and local repos are intentionally not direct-consume. A
    trusted override must be explicit in a profile/source tier field or Owner's
    approval marker.
    """
    profile = profile or {}
    explicit = _source_tier_value(profile.get("source_tier") or profile.get("trust_tier"))
    source_policy_raw = profile.get("source_policy")
    source_policy: dict = source_policy_raw if isinstance(source_policy_raw, dict) else {}
    explicit = explicit or _source_tier_value(source_policy.get("source_tier") or source_policy.get("trust_tier"))
    owner_marker = bool(profile.get("owner_approved_direct_consume") or source_policy.get("owner_approved_direct_consume"))
    if owner_marker:
        return SourceTrustTier.OWNER_APPROVED_DIRECT_CONSUME, ["Profile includes Owner approval marker for direct consumption."]
    if explicit:
        return explicit, [f"Profile explicitly classifies source tier as {explicit.value}."]
    owner = (getattr(repo_snapshot, "owner", None) or "").lower()
    url = (getattr(repo_snapshot, "url", None) or "").lower()
    if owner in {"anthropics", "anthropic", "openai", "pydantic", "python", "pypa"}:
        return SourceTrustTier.KNOWN_HIGH_QUALITY, ["Known high-quality upstream; still not direct-consume without explicit trusted/Owner marker."]
    if "github.com" in url:
        return SourceTrustTier.ORDINARY_OSS, ["Default GitHub repository classification; direct consumption is not allowed by default."]
    return SourceTrustTier.UNKNOWN_UNTRUSTED, ["No explicit trusted source classification was supplied."]


def detect_prompt_injection_findings(evidence: list[Evidence], *, start_index: int = 1) -> list[Finding]:
    findings: list[Finding] = []
    for ev in evidence:
        haystack = "\n".join(part for part in [ev.excerpt, ev.summary] if part)
        if not haystack or not PROMPT_INJECTION_RE.search(haystack):
            continue
        finding_tags = infer_taxonomy_tags(haystack, source_tags=["readme-claim"])
        findings.append(
            Finding(
                id=f"F{start_index + len(findings)}",
                title="Repo text contains prompt-injection/source-contamination language",
                category="risk",
                severity="high",
                impact="avoid",
                text=(
                    "Untrusted repository text appears to instruct the analyzer/builder to change instructions, "
                    "execution policy, route, build gate, or source-consumption behavior. It was recorded as data only."
                ),
                evidence_refs=[ev.id],
                confidence=0.9,
                recommended_action="Keep describe_only/peer_review gate; do not execute, copy, or direct-consume this source.",
                capability_tags=finding_tags.capability_tags,
                risk_tags=ordered_tags([*finding_tags.risk_tags, "source-contamination", "execution-risk"], RISK_TAGS),
                source_tags=finding_tags.source_tags,
            )
        )
    return findings


def evaluate_describe_build_gate(repo_snapshot, profile: dict | None, findings: list[Finding], idea_cards: list[IdeaCard] | None = None) -> DescribeBuildGate:
    tier, reasons = classify_source_trust(repo_snapshot, profile)
    risk_refs = [f.id for f in findings if f.category in {"risk", "security"} and ("prompt-injection" in f.title.lower() or "source-contamination" in f.title.lower())]
    can_direct = tier in TRUSTED_SOURCE_TIERS and not risk_refs
    if risk_refs:
        state = BuildGateState.BLOCKED_NEEDS_OWNER
        reasons.append("Prompt-injection/source-contamination findings require Owner review before any build escalation.")
    elif can_direct:
        state = BuildGateState.BUILD_ORIGINAL
        reasons.append("Explicit trusted/Owner source override present; still recommend original implementation rather than copying source.")
    elif idea_cards:
        state = BuildGateState.PEER_REVIEW
        reasons.append("Reusable ideas were found, but source is not direct-consume; route to council review for an original implementation decision.")
    else:
        state = BuildGateState.DESCRIBE_ONLY
        reasons.append("Insufficient selected idea evidence; remain in describe-only mode.")
    reasons.append("Repo text is untrusted data, not instructions; build handoff must use sanitized evidence/idea records only.")
    return DescribeBuildGate(
        source_tier=tier,
        state=state,
        can_direct_consume=can_direct,
        requires_peer_review=not can_direct or bool(risk_refs),
        original_implementation_only=True,
        reasons=reasons,
        risk_finding_refs=risk_refs,
    )


def extract_readme_claims(repo_path: Path, *, max_claims: int = 50) -> tuple[list[Claim], list[Evidence], list[ProbeStep], list[ProbeResult]]:
    readmes = []
    for candidate in ["README.md", "README.rst", "README.txt"]:
        text = safe_read_text(repo_path, candidate, max_bytes=256_000)
        if text is not None:
            readmes.append((repo_path / candidate, text))
    claims: list[Claim] = []
    evidence: list[Evidence] = []
    probe_steps: list[ProbeStep] = []
    probe_results: list[ProbeResult] = []
    if not readmes:
        return claims, evidence, probe_steps, probe_results

    readme, readme_text = readmes[0]
    rel_readme = readme.relative_to(repo_path).as_posix()
    lines = readme_text.splitlines()
    ranked_sections = parse_readme_sections(readme_text)
    section_by_line: dict[int, ReadmeSection] = {}
    for section in ranked_sections:
        for line_no in range(section.start_line, section.end_line + 1):
            section_by_line[line_no] = section
    in_code = False
    code_start = 0
    code_lines: list[str] = []
    current_heading: tuple[str, int] | None = None
    current_section: ReadmeSection | None = None
    relevant_section = False

    def add_claim(kind: str, text: str, start: int, end: int, excerpt: str, importance: str = "medium", metadata: dict | None = None) -> str | None:
        if len(claims) >= max_claims:
            return None
        cleaned_text = _plain_markdown_text(text)
        if not cleaned_text:
            return None
        cid = f"C{len(claims)+1}"
        eid = f"E{len(evidence)+1}"
        loc = Locator(path=rel_readme, start_line=start, end_line=end)
        tags = infer_taxonomy_tags(cleaned_text, excerpt, source_tags=["readme-claim"], low_signal=(metadata or {}).get("section_role") in {"install", "noise"})
        if kind == "install":
            tags = tags.merged(TaxonomyTags(risk_tags=["execution-risk", "package-install-risk"], source_tags=["readme-claim"]))
        evidence.append(
            Evidence(
                id=eid,
                type="file_excerpt",
                source_uri=f"file://{rel_readme}",
                locator=loc,
                content_hash=sha256_text(excerpt),
                excerpt=excerpt[:1000],
                summary=f"README excerpt: {cleaned_text[:120]}",
                collected_by="docs-claims",
                collected_at=utc_now(),
                strength="moderate",
                supports=[cid],
                metadata={"language": "markdown", **(metadata or {})},
                capability_tags=tags.capability_tags,
                risk_tags=tags.risk_tags,
                source_tags=tags.source_tags,
            )
        )
        claims.append(
            Claim(
                id=cid,
                kind=kind,
                text=cleaned_text.strip(),
                source=SourceRef(type="file", uri=f"file://{rel_readme}", locator=loc),
                normalized_subject=kind,
                expected_verification="command_probe" if kind == "install" else "static_review",
                importance=importance,
                status="not_tested",
                evidence_refs=[eid],
                confidence=0.30 if kind == "install" else 0.50 if metadata and metadata.get("section_role") == "high_signal" else 0.45,
                capability_tags=tags.capability_tags,
                risk_tags=tags.risk_tags,
                source_tags=tags.source_tags,
            )
        )
        return cid

    # Add high-signal purpose/capability claims first so Dossier and IdeaCards are
    # anchored in overview/about/features/architecture sections rather than later
    # install tables or badge blocks.
    for section in ranked_sections:
        if len(claims) >= max_claims:
            break
        if section.role not in {"high_signal", "general"} or section.score < 3:
            continue
        candidate = _candidate_sentence_from_section(section)
        if not candidate:
            continue
        line_no, sentence = candidate
        kind = "architecture" if IDEA_HEADING_RE.search(section.title) or IDEA_HEADING_RE.search(sentence) else _classify_claim(sentence)
        if kind == "install":
            continue
        importance = "high" if section.role == "high_signal" else "medium"
        add_claim(
            kind,
            sentence,
            line_no,
            line_no,
            lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else sentence,
            importance,
            {"heading": section.title, "section_role": section.role, "section_score": section.score},
        )

    for idx, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            if not in_code:
                in_code = True
                code_start = idx
                code_lines = []
            else:
                excerpt = "\n".join(code_lines)
                commands = [_strip_prompt(ln) for ln in code_lines if COMMAND_RE.match(ln.strip())]
                if commands and relevant_section:
                    heading_text = current_heading[0] if current_heading else "README"
                    text = f"README contains runnable quickstart commands in {heading_text} section: " + " && ".join(commands[:3])
                    claim_ref = add_claim(
                        "install",
                        text,
                        code_start,
                        idx,
                        "\n".join(lines[code_start - 1 : idx]),
                        "medium",
                        {"heading": heading_text, "commands": commands[:10], "section_role": "install", "section_score": current_section.score if current_section else None},
                    )
                    psid = f"PS{len(probe_steps)+1}"
                    step = ProbeStep(
                        id=psid,
                        kind="command",
                        description=f"Planned README command probe from {heading_text!r} (not executed by no-exec runner)",
                        command=" && ".join(commands[:3]),
                        requires_execution=True,
                        required_runner="docker",
                        network=_network_for_commands(commands),
                        claim_refs=[claim_ref] if claim_ref else [],
                        safety_review={"status": "needs_review", "reasons": ["Default runner is no-exec; untrusted repo README commands are not executed."]},
                    )
                    probe_steps.append(step)
                    probe_results.append(make_not_run_result(step, len(probe_results) + 1))
                in_code = False
            continue
        if in_code:
            code_lines.append(line)
            continue
        heading = HEADING_RE.match(line)
        if heading:
            title = _plain_markdown_text(heading.group(2).strip()) or heading.group(2).strip()
            current_heading = (title, idx)
            current_section = section_by_line.get(idx)
            role = _section_role(title)
            relevant_section = role == "install"
            if CLAIM_HINT_RE.search(title) and role not in {"noise", "install"}:
                add_claim(_classify_claim(title), f"README section claims: {title}", idx, idx, line, metadata={"heading": title, "section_role": role, "section_score": current_section.score if current_section else None})
            elif CLAIM_HINT_RE.search(title) and role == "install":
                add_claim(_classify_claim(title), f"README section claims: {title}", idx, idx, line, "low", metadata={"heading": title, "section_role": role, "section_score": current_section.score if current_section else None})
            continue
        cleaned = _clean_readme_line(line)
        if not cleaned:
            continue
        section = section_by_line.get(idx) or current_section
        section_role = section.role if section else "general"
        if section_role == "noise":
            continue
        stripped = cleaned.strip(" -*\t")
        if relevant_section and 8 <= len(stripped) <= 280 and CLAIM_HINT_RE.search(stripped):
            add_claim(_classify_claim(stripped), stripped, idx, idx, line, "low", metadata={"heading": current_heading[0] if current_heading else None, "section_role": section_role, "section_score": section.score if section else None})
        elif section_role != "install" and 30 <= len(stripped) <= 280 and CLAIM_HINT_RE.search(stripped):
            add_claim(_classify_claim(stripped), stripped, idx, idx, line, metadata={"heading": current_heading[0] if current_heading else None, "section_role": section_role, "section_score": section.score if section else None})
    return claims, evidence, probe_steps, probe_results


def detect_manifests(repo_path: Path, repo_snapshot=None) -> list[dict]:
    manifests: list[dict] = []
    snapshot_paths = {f["path"]: f for f in getattr(repo_snapshot, "files", [])}
    candidate_paths = set(snapshot_paths)
    if not candidate_paths:
        for path in repo_path.rglob("*"):
            if path.is_file() and not path.is_symlink():
                candidate_paths.add(path.relative_to(repo_path).as_posix())

    for rel in sorted(candidate_paths):
        name = Path(rel).name
        ecosystem = None
        manifest_type = None
        if name in PYTHON_MANIFESTS or (name.startswith("requirements") and name.endswith(".txt")):
            ecosystem, manifest_type = "python", name
        elif name in NODE_MANIFESTS:
            ecosystem, manifest_type = "node", name
        elif name in RUST_MANIFESTS:
            ecosystem, manifest_type = "rust", name
        elif name in GO_MANIFESTS:
            ecosystem, manifest_type = "go", name
        if ecosystem:
            info = {"path": rel, "ecosystem": ecosystem, "manifest": manifest_type, "size": snapshot_paths.get(rel, {}).get("size")}
            manifests.append(info)
    return manifests


def manifest_evidence(repo_path: Path, repo_snapshot=None, *, start_index: int = 1) -> list[Evidence]:
    out: list[Evidence] = []
    for manifest in detect_manifests(repo_path, repo_snapshot):
        path = repo_path / manifest["path"]
        excerpt = None
        content_hash = None
        text = safe_read_text(repo_path, manifest["path"], max_bytes=64_000)
        if text is not None:
            excerpt = "\n".join(text.splitlines()[:40])[:1000]
            content_hash = sha256_text(text)
        tags = infer_taxonomy_tags(manifest["path"], manifest["ecosystem"], excerpt or "", source_tags=["manifest-static"])
        out.append(
            Evidence(
                id=f"E_MAN_{start_index + len(out)}",
                type="manifest",
                source_uri=f"file://{manifest['path']}",
                locator=Locator(path=manifest["path"]),
                content_hash=content_hash,
                excerpt=excerpt,
                summary=f"Detected {manifest['ecosystem']} manifest {manifest['path']}",
                collected_by="manifest-detector",
                strength="direct",
                metadata=manifest,
                capability_tags=tags.capability_tags,
                risk_tags=ordered_tags([*tags.risk_tags, "package-install-risk"], RISK_TAGS),
                source_tags=tags.source_tags,
            )
        )
    return out


def _package_json_scripts(path: Path) -> dict[str, str]:
    try:
        text = safe_read_text(path.parent, path.name, max_bytes=128_000)
        if text is None:
            return {}
        data = json.loads(text)
    except Exception:
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def plan_manifest_probes(repo_path: Path, repo_snapshot=None, *, start_index: int = 1, result_start_index: int = 1) -> tuple[list[ProbeStep], list[ProbeResult]]:
    steps: list[ProbeStep] = []
    results: list[ProbeResult] = []
    manifests = detect_manifests(repo_path, repo_snapshot)
    ecosystems = {m["ecosystem"] for m in manifests}
    manifest_paths = {m["path"] for m in manifests}

    def add(kind: str, command: str, desc: str, network: str = "none") -> None:
        step = ProbeStep(
            id=f"PS{start_index + len(steps)}",
            kind=kind,  # type: ignore[arg-type]
            description=desc,
            command=command,
            requires_execution=True,
            required_runner="docker",
            network=network,  # type: ignore[arg-type]
            safety_review={"status": "not_run", "reasons": ["Probe is inferred from static manifests only; no-exec runner did not execute target code."]},
        )
        steps.append(step)
        results.append(make_not_run_result(step, result_start_index + len(results)))

    if "python" in ecosystems:
        if "pyproject.toml" in manifest_paths:
            add("install", "python -m pip install -e .", "Inferred Python editable install from pyproject.toml", "package-index")
        elif any(Path(p).name.startswith("requirements") for p in manifest_paths):
            req = next(p for p in sorted(manifest_paths) if Path(p).name.startswith("requirements"))
            add("install", f"python -m pip install -r {req}", "Inferred Python requirements install", "package-index")
        if any("test" in f.get("path", "").lower() or f.get("kind") == "test" for f in getattr(repo_snapshot, "files", [])):
            add("test", "pytest", "Inferred Python test command")
    if "node" in ecosystems:
        if "package-lock.json" in manifest_paths:
            add("install", "npm ci", "Inferred Node install from package-lock.json", "package-index")
        elif "pnpm-lock.yaml" in manifest_paths:
            add("install", "pnpm install --frozen-lockfile", "Inferred Node install from pnpm lockfile", "package-index")
        elif "yarn.lock" in manifest_paths:
            add("install", "yarn install --frozen-lockfile", "Inferred Node install from yarn lockfile", "package-index")
        else:
            add("install", "npm install", "Inferred Node install from package.json", "package-index")
        scripts = _package_json_scripts(repo_path / "package.json") if "package.json" in manifest_paths else {}
        for script_name, kind in [("build", "build"), ("test", "test"), ("start", "run")]:
            if script_name in scripts:
                add(kind, f"npm run {script_name}", f"Inferred Node {script_name} script from package.json")
    if "rust" in ecosystems:
        add("build", "cargo build", "Inferred Rust build from Cargo.toml")
        add("test", "cargo test", "Inferred Rust tests from Cargo.toml")
    if "go" in ecosystems:
        add("build", "go build ./...", "Inferred Go build from go.mod")
        add("test", "go test ./...", "Inferred Go tests from go.mod")
    return steps, results


def inventory_evidence(repo_snapshot) -> list[Evidence]:
    out: list[Evidence] = []
    for f in repo_snapshot.files:
        kind = f.get("kind")
        if kind in {"manifest", "license", "ci", "doc"}:
            etype = {"manifest": "manifest", "license": "license", "ci": "ci", "doc": "file_presence"}[kind]
            tags = infer_taxonomy_tags(f["path"], kind or "", str(f.get("ecosystem") or ""), source_tags=["inventory-static"])
            out.append(
                Evidence(
                    id=f"E_INV_{len(out)+1}",
                    type=etype,
                    source_uri=f"file://{f['path']}",
                    locator=Locator(path=f["path"]),
                    content_hash=None,
                    excerpt=None,
                    summary=f"Detected {kind} file {f['path']}",
                    collected_by="inventory",
                    strength="direct",
                    metadata={"size": f.get("size"), "ecosystem": f.get("ecosystem")},
                    capability_tags=tags.capability_tags,
                    risk_tags=tags.risk_tags,
                    source_tags=tags.source_tags,
                )
            )
    return out


def synthesize_findings(claims: list[Claim], evidence: list[Evidence], probe_results: list[ProbeResult]) -> list[Finding]:
    findings: list[Finding] = []
    if claims:
        tag_text = " ".join([ev.id for ev in evidence[:5]] + [c.id for c in claims[:5]])
        tags = infer_taxonomy_tags(tag_text, evidence=evidence[:5])
        findings.append(
            Finding(
                id="F1",
                title="README claims extracted for review",
                category="docs",
                severity="info",
                impact="investigate",
                text=f"Extracted {len(claims)} README claim(s) with file citations.",
                evidence_refs=[e.id for e in evidence[:5]],
                claim_refs=[c.id for c in claims[:5]],
                confidence=0.7,
                recommended_action="Review claims against code and run sandbox probes where appropriate.",
                capability_tags=tags.capability_tags,
                risk_tags=tags.risk_tags,
                source_tags=tags.source_tags,
            )
        )
    manifest_count = sum(1 for e in evidence if e.type == "manifest")
    if manifest_count:
        tags = infer_taxonomy_tags("package manifests detected", evidence=[e for e in evidence if e.type == "manifest"])
        findings.append(
            Finding(
                id=f"F{len(findings)+1}",
                title="Package manifests detected statically",
                category="dependency",
                severity="info",
                impact="investigate",
                text=f"Detected {manifest_count} manifest evidence item(s) for ecosystem and command inference without executing code.",
                evidence_refs=[e.id for e in evidence if e.type == "manifest"][:8],
                confidence=0.85,
                recommended_action="Review inferred probes and only execute them in an approved sandbox.",
                capability_tags=tags.capability_tags,
                risk_tags=ordered_tags([*tags.risk_tags, "package-install-risk"], RISK_TAGS),
                source_tags=tags.source_tags,
            )
        )
    if probe_results:
        findings.append(
            Finding(
                id=f"F{len(findings)+1}",
                title="Execution probes were planned but not run",
                category="quickstart",
                severity="medium",
                impact="investigate",
                text="README and manifest command probes require execution, but the default no-exec policy records them as not_run.",
                probe_result_refs=[p.id for p in probe_results],
                claim_refs=[c.id for c in claims if c.kind == "install"],
                confidence=0.9,
                recommended_action="Use a future sandbox runner only after explicit policy approval.",
                risk_tags=["execution-risk", "package-install-risk", "terminal-process-risk"],
                source_tags=["readme-claim", "manifest-static", "inferred-static"],
            )
        )
    return findings


def extract_idea_candidates(claims: list[Claim], evidence: list[Evidence], *, max_candidates: int = 10) -> list[IdeaCandidate]:
    """Surface reusable ideas/patterns without retaining or adopting the repo.

    repo-scout extracts source intelligence: things worth borrowing, adapting,
    watching, or rejecting. It should not treat a cloned repository as an
    artifact to keep wholesale.
    """
    by_id = {ev.id: ev for ev in evidence}
    out: list[IdeaCandidate] = []
    seen_texts: set[str] = set()

    def add(kind: str, title: str, summary: str, claim: Claim, confidence: float, action: str) -> None:
        norm_text = re.sub(r"\W+", " ", claim.text.lower()).strip()
        if len(out) >= max_candidates or norm_text in seen_texts:
            return
        seen_texts.add(norm_text)
        claim_tags = TaxonomyTags(capability_tags=claim.capability_tags, risk_tags=claim.risk_tags, source_tags=claim.source_tags)
        if kind == "risk":
            claim_tags = claim_tags.merged(TaxonomyTags(risk_tags=["execution-risk", "package-install-risk"]))
        out.append(
            IdeaCandidate(
                id=f"I{len(out)+1}",
                kind=kind,  # type: ignore[arg-type]
                title=title[:100],
                summary=summary[:500],
                evidence_refs=claim.evidence_refs,
                source_claim_refs=[claim.id],
                confidence=confidence,
                suggested_action=action,  # type: ignore[arg-type]
                capability_tags=claim_tags.capability_tags,
                risk_tags=claim_tags.risk_tags,
                source_tags=claim_tags.source_tags,
            )
        )

    for claim in claims:
        text = claim.text.strip()
        low = text.lower()
        first_ev = by_id.get(claim.evidence_refs[0]) if claim.evidence_refs else None
        heading = (first_ev.metadata or {}).get("heading") if first_ev else None
        section_role = (first_ev.metadata or {}).get("section_role") if first_ev else None
        if section_role == "noise":
            continue
        if claim.kind == "install":
            # Preserve install/quickstart as probe plan/risk evidence, but never
            # let setup boilerplate become a reusable idea card. Useful output
            # should be anchored in purpose/function/architecture signals; low-
            # signal repos with only install claims remain describe-only.
            continue
        elif claim.kind == "architecture" or IDEA_HEADING_RE.search(text) or (isinstance(heading, str) and IDEA_HEADING_RE.search(heading)):
            confidence = 0.65 if section_role == "high_signal" else 0.55
            add("pattern", f"Pattern candidate: {heading or text[:60]}", f"Review this repo idea for possible adaptation rather than wholesale adoption: {text}", claim, confidence, "adapt")
        elif claim.kind in {"capability", "compatibility"} and any(w in low for w in ["supports", "provides", "enables", "allows", "features", "helps", "uses", "builds", "automates", "integrates", "generates", "tracks", "manages"]):
            confidence = 0.58 if section_role == "high_signal" else 0.45
            add("idea", f"Capability idea: {text[:72]}", f"Potential idea to evaluate for fit without taking the repo as-is: {text}", claim, confidence, "watch")
    return out


def _neutral_sentence(text: str, *, fallback: str) -> str:
    cleaned = re.sub(r"file://\S+", "evidence reference", text or "")
    cleaned = re.sub(r"(?i)(?:hermes|example-platform)", "the target platform", cleaned)
    cleaned = re.sub(r"(?:/home/|/users/|c:\\)\S+", "local path", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return (cleaned or fallback)[:500]


def build_idea_cards(idea_candidates: list[IdeaCandidate], evidence: list[Evidence], *, max_cards: int = 10) -> list[IdeaCard]:
    by_id = {ev.id: ev for ev in evidence}
    cards: list[IdeaCard] = []
    for idea in idea_candidates[:max_cards]:
        primary = by_id.get(idea.evidence_refs[0]) if idea.evidence_refs else None
        source_tier = "readme_claim" if primary and primary.type == "file_excerpt" else "manifest_static" if primary and primary.type == "manifest" else "repo_static"
        risk = "high" if idea.kind == "risk" else "medium" if idea.confidence < 0.6 else "low"
        idea_tags = TaxonomyTags(capability_tags=idea.capability_tags, risk_tags=idea.risk_tags, source_tags=idea.source_tags)
        if primary:
            idea_tags = idea_tags.merged(TaxonomyTags(capability_tags=primary.capability_tags, risk_tags=primary.risk_tags, source_tags=primary.source_tags))
        cards.append(
            IdeaCard(
                id=f"IC{len(cards)+1}",
                purpose=_neutral_sentence(idea.title, fallback="Reusable repo-inspired idea"),
                function=_neutral_sentence(idea.summary, fallback="Evaluate evidence-backed behavior for adaptation."),
                evidence_refs=[*idea.evidence_refs, *idea.source_claim_refs],
                source_tier=source_tier,  # type: ignore[arg-type]
                risk=risk,  # type: ignore[arg-type]
                pattern=_neutral_sentence(idea.kind, fallback="idea"),
                suggested_action=idea.suggested_action,
                confidence=idea.confidence,
                capability_tags=idea_tags.capability_tags,
                risk_tags=idea_tags.risk_tags,
                source_tags=idea_tags.source_tags,
            )
        )
    return cards


def build_dossier(repo_snapshot, goal: str | None, claims: list[Claim], evidence: list[Evidence], findings: list[Finding], idea_cards: list[IdeaCard], verdict, describe_build_gate: DescribeBuildGate | None = None) -> Dossier:
    evidence_by_id = {ev.id: ev for ev in evidence}
    doc_claims = [c for c in claims if c.kind in {"capability", "architecture", "compatibility"}]
    install_claims = [c for c in claims if c.kind == "install"]
    manifest_evidence_items = [ev for ev in evidence if ev.type == "manifest"]

    def claim_score(claim: Claim) -> int:
        score = 0
        if claim.kind == "architecture":
            score += 6
        elif claim.kind in {"capability", "compatibility"}:
            score += 4
        elif claim.kind == "install":
            score -= 8
        score += {"critical": 4, "high": 3, "medium": 1, "low": -2}.get(claim.importance, 0)
        for ref in claim.evidence_refs:
            ev = evidence_by_id.get(ref)
            if not ev:
                continue
            meta = ev.metadata or {}
            role = meta.get("section_role")
            if role == "high_signal":
                score += 8
            elif role == "general":
                score += 2
            elif role == "install":
                score -= 5
            elif role == "noise":
                score -= 12
            raw_score = meta.get("section_score")
            if isinstance(raw_score, int):
                score += max(-5, min(8, raw_score // 3))
        return score

    ranked_doc_claims = sorted(doc_claims, key=claim_score, reverse=True)
    primary_claim = ranked_doc_claims[0] if ranked_doc_claims else (claims[0] if claims else None)
    if ranked_doc_claims:
        early_claims = [
            c
            for c in ranked_doc_claims
            if any((ev := evidence_by_id.get(ref)) and ev.locator and ev.locator.start_line and ev.locator.start_line <= 8 for ref in c.evidence_refs)
            and not any((ev2 := evidence_by_id.get(ref)) and (ev2.metadata or {}).get("section_role") == "high_signal" for ref in c.evidence_refs)
        ]
        if early_claims and claim_score(early_claims[0]) >= claim_score(primary_claim) - 14:
            primary_claim = early_claims[0]
    metadata_description = _metadata_description(repo_snapshot)
    repo_purpose = _neutral_sentence(
        primary_claim.text if primary_claim else metadata_description or f"Static source package named {repo_snapshot.name}.",
        fallback="Static source package reviewed for reusable ideas.",
    )
    if metadata_description and (not primary_claim or claim_score(primary_claim) < 4):
        repo_purpose = _neutral_sentence(metadata_description, fallback=repo_purpose)

    function_bits: list[str] = []
    for claim in ranked_doc_claims[:2]:
        bit = _neutral_sentence(claim.text, fallback="README describes a reusable capability.")
        if bit not in function_bits:
            function_bits.append(bit)
    if manifest_evidence_items:
        ecosystems = sorted({str(ev.metadata.get("ecosystem")) for ev in manifest_evidence_items if ev.metadata.get("ecosystem")})
        if ecosystems:
            function_bits.append("Static manifests indicate " + ", ".join(ecosystems) + " package surfaces.")
    repo_function = " ".join(function_bits) or "Function requires additional evidence beyond static metadata."
    evidence_basis = [
        {"id": ev.id, "type": ev.type, "summary": _neutral_sentence(ev.summary, fallback="Evidence item"), "strength": ev.strength}
        for ev in evidence[:12]
    ]
    useful = [
        {"id": card.id, "purpose": card.purpose, "function": card.function, "action": card.suggested_action, "evidence_refs": card.evidence_refs, "capability_tags": card.capability_tags, "risk_tags": card.risk_tags, "source_tags": card.source_tags}
        for card in idea_cards
    ]
    risks = ["No target repository code, package manager, tests, build scripts, README commands, or external scanners were executed."]
    if install_claims:
        risks.append("Quickstart/install claims are unverified until run in an approved sandbox.")
    if describe_build_gate:
        risks.extend(_neutral_sentence(reason, fallback="Describe-build gate reason") for reason in describe_build_gate.reasons)
    if not claims:
        risks.append("No README claims were extracted; recommendation is limited to static file inventory.")
    risks.extend(_neutral_sentence(f.title, fallback="Finding") for f in findings if f.category in {"risk", "security", "quickstart"})
    recommendation = verdict.decision.value if isinstance(verdict.decision, Verdict) else str(verdict.decision)
    dossier_tags = merge_taxonomy_tags([*claims, *evidence, *findings, *idea_cards], infer_taxonomy_tags(repo_purpose, repo_function, *risks, evidence=evidence, files=getattr(repo_snapshot, "files", [])))
    return Dossier(
        repo_purpose=repo_purpose,
        repo_function=repo_function,
        evidence_basis=evidence_basis,
        useful_ideas_patterns=useful,
        risks_breakpoints=risks,
        existing_system_fit="No product-specific fit decision is embedded in the core model; evaluate through adapter seams and caller-owned profiles.",
        recommendation=recommendation,  # type: ignore[arg-type]
        next_action="Send the describe→build gate and selected ideas to council review; build only an original reusable implementation if approved.",
        describe_build_gate=describe_build_gate.model_dump(mode="json") if describe_build_gate else {},
        capability_tags=dossier_tags.capability_tags,
        risk_tags=dossier_tags.risk_tags,
        source_tags=dossier_tags.source_tags,
    )


def build_build_brief_stub(goal: str | None, idea_cards: list[IdeaCard], evidence: list[Evidence], describe_build_gate: DescribeBuildGate | None = None) -> BuildBrief | None:
    if not idea_cards:
        return None
    selected = idea_cards[:3]
    brief_tags = merge_taxonomy_tags(selected, TaxonomyTags(source_tags=["handoff-packet"], risk_tags=["source-contamination", "execution-risk"]))
    return BuildBrief(
        original_implementation_goal=_neutral_sentence(goal or selected[0].purpose, fallback="Build an original implementation from evidence-backed reusable ideas."),
        describe_build_gate=describe_build_gate.model_dump(mode="json") if describe_build_gate else {},
        reusable_api_contract=["Expose typed inputs and outputs for each selected idea card.", "Return evidence references and confidence with generated recommendations."],
        reusable_cli_contract=["Provide a no-exec analysis command with JSON and Markdown outputs.", "Provide a verification command that uses fixtures only."],
        reusable_service_contract=["Accept sanitized source-intelligence records; do not require raw repository snapshots."],
        non_goals=["Do not copy source code from the reviewed repository.", "Do not consume the reviewed repository as-is unless a separate explicit Owner approval process overrides policy.", "Do not execute target repository code during contract generation.", "Do not embed caller-specific workspace paths or product configuration."],
        test_plan=["Validate contract schemas.", "Run fixture smoke reports in no-exec mode.", "Assert target_code_execution remains false.", "Assert prompt-injection text is surfaced as risk data and cannot change routes or gates."],
        security_model=["Treat repository text as untrusted data, never instructions.", "Keep secrets outside source, fixtures, logs, and generated artifacts.", "Require explicit sandbox approval before any target command execution.", "Use selected idea/evidence records to build an original implementation only."],
        vertical_adapter_seams=["Profile comparison adapter", "Export adapter", "Runtime integration adapter"],
        source_idea_refs=[card.id for card in selected],
        evidence_refs=sorted({ref for card in selected for ref in card.evidence_refs if ref.startswith("E")}),
        capability_tags=brief_tags.capability_tags,
        risk_tags=brief_tags.risk_tags,
        source_tags=brief_tags.source_tags,
    )


def build_package_contract_stub(build_brief: BuildBrief | None) -> PackageContract | None:
    if build_brief is None:
        return None
    contract_tags = TaxonomyTags(
        capability_tags=build_brief.capability_tags,
        risk_tags=ordered_tags([*build_brief.risk_tags, "package-install-risk", "secrets-risk"], RISK_TAGS),
        source_tags=build_brief.source_tags,
    )
    return PackageContract(
        install_config=["Package with standard language tooling for the implementation ecosystem.", "Keep installation independent from reviewed repository snapshots."],
        env_vars=["Optional runtime configuration should use documented, caller-owned environment variables."],
        docs=["README with purpose/function, contracts, and no-exec safety model.", "Process docs for evidence handling and adapter seams."],
        fixtures=["Small synthetic fixtures that do not copy reviewed repository source."],
        ci=["Run schema validation and fixture smoke tests.", "Fail if contract artifacts contain local paths or project-specific adapter names."],
        verification_commands=["pytest -q", "repo-scout scout tests/fixtures/tiny_repo --format both"],
        adapter_points=build_brief.vertical_adapter_seams,
        capability_tags=contract_tags.capability_tags,
        risk_tags=contract_tags.risk_tags,
        source_tags=contract_tags.source_tags,
    )

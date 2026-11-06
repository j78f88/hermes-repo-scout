from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from shutil import which as shutil_which
from typing import Any, Literal

from pydantic import BaseModel, Field

from .schemas import Evidence, Finding

WhichFn = Callable[[str], str | None]


class ScannerAvailability(BaseModel):
    """Local availability for an optional scanner binary.

    Detection is intentionally limited to PATH lookup by default. Version command
    execution is modeled but not performed by repo-scout's list/plan CLI surface,
    keeping optional tools side-effect-free unless a future explicit executor opts in.
    """

    scanner_id: str
    command: str
    available: bool
    path: str | None = None
    version: str | None = None
    status: Literal["available", "missing"]
    message: str


class ScannerRun(BaseModel):
    """A planned optional scanner run.

    `repo-scout scanners plan` emits these records only; it does not execute the
    command. The command is represented for review and future sandbox runners.
    """

    scanner_id: str
    scanner_name: str
    purpose: str
    status: Literal["planned", "missing", "not_run"] = "not_run"
    repo_path: str
    command: list[str]
    available: bool
    binary_path: str | None = None
    version: str | None = None
    output_kind: str
    evidence_type: Literal["scanner_result"] = "scanner_result"
    finding_category: Literal["security", "license", "dependency", "maintenance", "risk"] = "risk"
    executes_target_code: bool = False
    reads_target_files: bool = True
    network: Literal["none", "metadata", "package-index", "repo-defined"] = "none"
    safety_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_evidence(self, index: int = 1) -> Evidence:
        status = "available" if self.available else "missing"
        return Evidence(
            id=f"E_SCAN_{index}",
            type="scanner_result",
            source_uri=f"scanner://{self.scanner_id}",
            summary=f"Optional scanner {self.scanner_id} is {status}; planned status={self.status}.",
            collected_by="repo-scout.scanners.plan",
            strength="none",
            metadata=self.model_dump(mode="json"),
        )

    def as_finding(self, index: int = 1) -> Finding:
        severity = "info" if self.available else "low"
        text = (
            f"Optional scanner `{self.scanner_id}` can be reviewed as a planned no-exec command."
            if self.available
            else f"Optional scanner `{self.scanner_id}` is missing; repo-scout degraded gracefully without failing."
        )
        return Finding(
            id=f"F_SCAN_{index}",
            title=f"Optional scanner {self.scanner_id}: {self.status}",
            category=self.finding_category,
            severity=severity,
            impact="investigate" if self.available else "none",
            text=text,
            evidence_refs=[f"E_SCAN_{index}"],
            confidence=0.8,
            recommended_action="Install the scanner only if this evidence lane is needed." if not self.available else "Run only in an explicit trusted/sandboxed scanner execution mode.",
        )


class ScannerDefinition(BaseModel):
    id: str
    name: str
    command: str
    purpose: str
    output_kind: str
    args_template: list[str]
    finding_category: Literal["security", "license", "dependency", "maintenance", "risk"] = "risk"
    network: Literal["none", "metadata", "package-index", "repo-defined"] = "none"
    executes_target_code: bool = False
    reads_target_files: bool = True
    version_args: list[str] = Field(default_factory=lambda: ["--version"])
    safety_notes: list[str] = Field(default_factory=list)

    def availability(self, which: WhichFn = shutil_which, *, version: str | None = None) -> ScannerAvailability:
        path = which(self.command)
        available = path is not None
        return ScannerAvailability(
            scanner_id=self.id,
            command=self.command,
            available=available,
            path=path,
            version=version if available else None,
            status="available" if available else "missing",
            message=(f"found at {path}" if available else f"{self.command} not found on PATH; optional scanner skipped"),
        )

    def plan(self, repo: str | Path, which: WhichFn = shutil_which) -> ScannerRun:
        availability = self.availability(which)
        repo_path = str(Path(repo))
        command = [availability.path or self.command, *[arg.replace("{repo}", repo_path) for arg in self.args_template]]
        status: Literal["planned", "missing", "not_run"] = "planned" if availability.available else "missing"
        return ScannerRun(
            scanner_id=self.id,
            scanner_name=self.name,
            purpose=self.purpose,
            status=status,
            repo_path=repo_path,
            command=command,
            available=availability.available,
            binary_path=availability.path,
            version=availability.version,
            output_kind=self.output_kind,
            finding_category=self.finding_category,
            executes_target_code=self.executes_target_code,
            reads_target_files=self.reads_target_files,
            network=self.network,
            safety_notes=[
                "plan-only: repo-scout did not execute this scanner command",
                "scanner definitions must not run target package managers, tests, build scripts, or repo-defined code",
                *self.safety_notes,
            ],
            metadata={
                "availability": availability.model_dump(mode="json"),
                "version_detection": "not_executed_by_default",
                "optional": True,
            },
        )


def default_scanners() -> list[ScannerDefinition]:
    """Optional scanner definitions. None are install/runtime requirements."""

    return [
        ScannerDefinition(
            id="scc",
            name="scc code inventory",
            command="scc",
            purpose="Code language/file/line inventory.",
            output_kind="code_inventory",
            args_template=["--format", "json", "{repo}"],
            finding_category="maintenance",
            network="none",
            safety_notes=["Expected to read repository files only; no network required."],
        ),
        ScannerDefinition(
            id="tokei",
            name="tokei code inventory",
            command="tokei",
            purpose="Alternative code language/file/line inventory.",
            output_kind="code_inventory",
            args_template=["--output", "json", "{repo}"],
            finding_category="maintenance",
            network="none",
            safety_notes=["Expected to read repository files only; no network required."],
        ),
        ScannerDefinition(
            id="syft",
            name="syft SBOM inventory",
            command="syft",
            purpose="SBOM/package inventory from manifests and filesystem metadata.",
            output_kind="sbom",
            args_template=["dir:{repo}", "--output", "json"],
            finding_category="dependency",
            network="none",
            safety_notes=["Uses dir: source to avoid image/package execution; network is assumed unnecessary for local directory scans."],
        ),
        ScannerDefinition(
            id="osv-scanner",
            name="OSV vulnerability manifest scan",
            command="osv-scanner",
            purpose="Vulnerability lookup for supported lockfiles/manifests.",
            output_kind="vulnerability_report",
            args_template=["--format", "json", "--recursive", "--skip-git", "{repo}"],
            finding_category="security",
            network="package-index",
            safety_notes=["May contact OSV vulnerability services/package metadata; plan marks package-index network assumption."],
        ),
        ScannerDefinition(
            id="gitleaks",
            name="gitleaks secrets scan",
            command="gitleaks",
            purpose="Secret pattern scan over repository contents.",
            output_kind="secrets_report",
            args_template=["detect", "--source", "{repo}", "--no-git", "--redact", "--report-format", "json", "--exit-code", "0"],
            finding_category="security",
            network="none",
            safety_notes=["--no-git avoids traversing git history; --redact avoids exposing secret values in planned artifacts."],
        ),
        ScannerDefinition(
            id="scancode",
            name="ScanCode license scan",
            command="scancode",
            purpose="License/copyright/package metadata scan.",
            output_kind="license_report",
            args_template=["--format", "json", "--license", "--copyright", "--info", "--processes", "1", "{repo}"],
            finding_category="license",
            network="none",
            safety_notes=["Expected to read repository files only; no package install/build/test execution."],
        ),
        ScannerDefinition(
            id="semgrep",
            name="Semgrep static pattern scan",
            command="semgrep",
            purpose="Static pattern scan for security/quality rules.",
            output_kind="static_patterns",
            args_template=["scan", "--config", "auto", "--json", "--disable-version-check", "{repo}"],
            finding_category="security",
            network="metadata",
            safety_notes=["Static analysis only; --disable-version-check avoids version telemetry; --config auto may need metadata/rules resolution depending on local Semgrep setup."],
        ),
    ]


class ScannerRegistry:
    def __init__(self, scanners: Iterable[ScannerDefinition] | None = None):
        self._scanners = list(scanners or default_scanners())

    def list(self) -> list[ScannerDefinition]:
        return list(self._scanners)

    def get(self, scanner_id: str) -> ScannerDefinition:
        for scanner in self._scanners:
            if scanner.id == scanner_id:
                return scanner
        raise KeyError(scanner_id)

    def availability(self, which: WhichFn = shutil_which) -> list[ScannerAvailability]:
        return [scanner.availability(which) for scanner in self._scanners]

    def plan(self, repo: str | Path, which: WhichFn = shutil_which) -> list[ScannerRun]:
        return [scanner.plan(repo, which) for scanner in self._scanners]


def scanner_registry() -> ScannerRegistry:
    return ScannerRegistry()

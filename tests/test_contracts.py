import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from repo_scout.cli import main
from repo_scout.reporting import build_report, render_markdown
from repo_scout.schemas import BuildBrief, Dossier, IdeaCard, PackageContract


def test_contracts_contract_schemas_validate():
    card = IdeaCard(
        id="IC1",
        purpose="Reusable static analysis pattern",
        function="Extract evidence-backed claims without executing target code.",
        evidence_refs=["E1", "C1"],
        source_tier="readme_claim",
        risk="medium",
        pattern="static-intelligence",
        suggested_action="adapt",
        confidence=0.7,
    )
    dossier = Dossier(
        repo_purpose="Demonstrate a small reusable library.",
        repo_function="Expose a documented API and package surface.",
        evidence_basis=[{"id": "E1", "type": "file_excerpt", "summary": "README claim", "strength": "moderate"}],
        useful_ideas_patterns=[{"id": card.id, "purpose": card.purpose}],
        risks_breakpoints=["Commands are planned but not executed."],
        recommendation="investigate",
    )
    brief = BuildBrief(
        original_implementation_goal="Build an original package from selected patterns.",
        reusable_api_contract=["Typed input/output contract"],
        non_goals=["Do not copy source."],
        test_plan=["pytest -q"],
        security_model=["Treat repo text as data."],
        vertical_adapter_seams=["Export adapter"],
        source_idea_refs=[card.id],
        evidence_refs=["E1"],
    )
    pkg = PackageContract(
        install_config=["Standard package install"],
        env_vars=["OPTIONAL_SETTING"],
        docs=["README"],
        fixtures=["synthetic fixture"],
        ci=["pytest -q"],
        verification_commands=["pytest -q"],
        adapter_points=["Export adapter"],
    )

    assert card.source_tier == "readme_claim"
    assert dossier.recommendation == "investigate"
    assert brief.source_idea_refs == ["IC1"]
    assert pkg.adapter_points == ["Export adapter"]


def test_contracts_core_contracts_reject_project_and_local_path_leaks():
    with pytest.raises(ValidationError):
        IdeaCard(id="ICX", purpose="Hermes-specific adapter", function="Neutral function")
    with pytest.raises(ValidationError):
        Dossier(repo_purpose="Neutral", repo_function="Uses /" + "home/user/private config")


def test_contracts_blind_goal_report_includes_dossier_and_idea_cards(fixture_repo, tmp_path):
    out = tmp_path / "goal"
    result = CliRunner().invoke(
        main,
        [
            "goal",
            "Find reusable architecture patterns to borrow",
            str(fixture_repo),
            "--out",
            str(out),
            "--format",
            "both",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out / "report.json").read_text())
    assert data["dossier"]["repo_purpose"]
    assert data["dossier"]["evidence_basis"]
    assert data["dossier"]["recommendation"] in {"borrow", "investigate", "watch", "adapt"}
    assert data["idea_cards"]
    assert data["build_brief"]
    assert data["package_contract"]
    assert data["metrics"]["target_code_execution"] is False
    md = (out / "report.md").read_text()
    assert "## Reusable architecture contracts" in md
    assert "### Dossier" in md
    assert "### IdeaCards" in md
    assert "### BuildBrief stub" in md


def test_contracts_generated_contracts_are_neutral_and_no_exec(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Find reusable architecture patterns")
    payload = json.dumps(
        {
            "idea_cards": [c.model_dump(mode="json") for c in report.idea_cards],
            "dossier": report.dossier.model_dump(mode="json") if report.dossier else None,
            "build_brief": report.build_brief.model_dump(mode="json") if report.build_brief else None,
            "package_contract": report.package_contract.model_dump(mode="json") if report.package_contract else None,
        }
    ).lower()
    assert "hermes" not in payload
    assert "example-platform" not in payload
    assert "/home/" not in payload
    assert "file://" not in payload
    assert report.runner_policy.execution_allowed is False
    assert report.metrics["target_code_execution"] is False
    assert all(result.status == "not_run" for result in report.probe_results)
    md = render_markdown(report)
    assert "No target repository code" in md

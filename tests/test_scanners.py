import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.scanners import scanner_registry

EXPECTED_SCANNER_IDS = {"scc", "tokei", "syft", "osv-scanner", "gitleaks", "scancode", "semgrep"}


def test_scanner_registry_lists_expected_scanner_ids():
    registry = scanner_registry()
    assert {scanner.id for scanner in registry.list()} == EXPECTED_SCANNER_IDS
    assert all(scanner.executes_target_code is False for scanner in registry.list())


def test_scanner_availability_can_be_mocked_installed_and_missing():
    registry = scanner_registry()

    def fake_which(command: str) -> str | None:
        return f"/mock/bin/{command}" if command in {"syft", "gitleaks"} else None

    availability = {item.scanner_id: item for item in registry.availability(fake_which)}
    assert availability["syft"].available is True
    assert availability["syft"].path == "/mock/bin/syft"
    assert availability["gitleaks"].status == "available"
    assert availability["semgrep"].available is False
    assert availability["semgrep"].status == "missing"
    assert "not found" in availability["semgrep"].message


def test_cli_scanners_list_works():
    result = CliRunner().invoke(main, ["scanners", "list"])
    assert result.exit_code == 0, result.output
    assert "Optional scanners" in result.output
    for scanner_id in EXPECTED_SCANNER_IDS:
        assert scanner_id in result.output


def test_cli_scanners_list_json_works():
    result = CliRunner().invoke(main, ["scanners", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {row["id"] for row in data} == EXPECTED_SCANNER_IDS
    assert all("status" in row for row in data)


def test_scanner_plan_produces_runs_without_executing_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")

    calls: list[str] = []

    def fake_which(command: str) -> str | None:
        calls.append(command)
        return "/mock/bin/syft" if command == "syft" else None

    runs = scanner_registry().plan(repo, fake_which)
    assert calls == ["scc", "tokei", "syft", "osv-scanner", "gitleaks", "scancode", "semgrep"]
    assert len(runs) == len(EXPECTED_SCANNER_IDS)
    syft = next(run for run in runs if run.scanner_id == "syft")
    assert syft.status == "planned"
    assert syft.command[0] == "/mock/bin/syft"
    assert f"dir:{repo}" in syft.command
    assert syft.executes_target_code is False
    assert syft.metadata["version_detection"] == "not_executed_by_default"
    assert syft.as_evidence().type == "scanner_result"
    assert syft.as_finding().category == "dependency"
    semgrep = next(run for run in runs if run.scanner_id == "semgrep")
    assert semgrep.status == "missing"
    assert semgrep.available is False
    assert semgrep.command[0] == "semgrep"


def test_cli_scanners_plan_json_is_plan_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = CliRunner().invoke(main, ["scanners", "plan", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {row["scanner_id"] for row in data} == EXPECTED_SCANNER_IDS
    assert all(row["executes_target_code"] is False for row in data)
    assert all(row["status"] in {"planned", "missing"} for row in data)
    assert all("plan-only" in row["safety_notes"][0] for row in data)

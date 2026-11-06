import json

from click.testing import CliRunner

from repo_scout.analyze import detect_manifests, extract_readme_claims, plan_manifest_probes
from repo_scout.cli import main
from repo_scout.intake import inventory_repo, parse_repo_ref, planned_clone_command
from repo_scout.reporting import build_report


def test_github_url_parsing_and_planned_clone_command(tmp_path):
    refs = [
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo/tree/main",
        "git@github.com:owner/repo.git",
        "owner/repo",
    ]
    for raw in refs:
        ref = parse_repo_ref(raw)
        assert not ref.is_local
        assert ref.owner == "owner"
        assert ref.name == "repo"
        assert ref.url == "https://github.com/owner/repo"
        assert ref.clone_url == "https://github.com/owner/repo.git"
    cmd = planned_clone_command(parse_repo_ref("owner/repo"), tmp_path / "repo")
    assert cmd == [
        "git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
        "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "clone", "--depth=1", "--single-branch", "--no-tags", "--recurse-submodules=no",
        "--no-checkout", "--filter=blob:limit=2000000",
        "https://github.com/owner/repo.git",
        str(tmp_path / "repo"),
    ]


def test_remote_plan_is_planned_clone_only_no_execution():
    result = CliRunner().invoke(main, ["goal", "Validate quickstart", "https://github.com/owner/repo", "--dry-run-plan"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["repo"]["ignored_path_details"][0]["reason"] == "planned_clone_only"
    planned = data["repo"]["ignored_path_details"][0]["command"]
    assert planned[0] == "git" and "protocol.allow=never" in planned
    assert planned[planned.index("clone"):planned.index("clone") + 5] == ["clone", "--depth=1", "--single-branch", "--no-tags", "--recurse-submodules=no"]
    assert data["runner_policy"]["execution_allowed"] is False


def test_manifest_detection_for_python_node_rust_go(tmp_path):
    repo = tmp_path / "polyglot"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (repo / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "package.json").write_text('{"scripts":{"build":"vite build","test":"vitest","start":"node index.js"}}', encoding="utf-8")
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (repo / "Cargo.lock").write_text("", encoding="utf-8")
    (repo / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
    (repo / "go.sum").write_text("", encoding="utf-8")
    snap = inventory_repo(repo)
    manifests = detect_manifests(repo, snap)
    ecosystems = {m["ecosystem"] for m in manifests}
    assert ecosystems == {"python", "node", "rust", "go"}
    manifest_paths = {m["path"] for m in manifests}
    assert {"pyproject.toml", "requirements-dev.txt", "package.json", "package-lock.json", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum"} <= manifest_paths
    steps, results = plan_manifest_probes(repo, snap)
    commands = {s.command for s in steps}
    assert "python -m pip install -e ." in commands
    assert "npm ci" in commands
    assert "npm run build" in commands
    assert "npm run test" in commands
    assert "npm run start" in commands
    assert "cargo build" in commands
    assert "cargo test" in commands
    assert "go build ./..." in commands
    assert "go test ./..." in commands
    assert all(s.requires_execution for s in steps)
    assert all(r.status == "not_run" for r in results)


def test_readme_claim_extraction_with_relevant_heading_line_ranges(tmp_path):
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# Demo\n\n"
        "## Quickstart\n"
        "Install the package and run the server.\n"
        "```bash\n"
        "pip install -e .\n"
        "python -m demo\n"
        "```\n\n"
        "## Usage\n"
        "Use the CLI to build reports quickly.\n",
        encoding="utf-8",
    )
    claims, evidence, steps, results = extract_readme_claims(repo)
    assert any(c.text == "README section claims: Quickstart" and c.source.locator and c.source.locator.start_line == 3 for c in claims)
    command_claim = next(c for c in claims if "README contains runnable quickstart commands in Quickstart section" in c.text)
    assert command_claim.source.locator is not None
    assert command_claim.source.locator.path == "README.md"
    assert command_claim.source.locator.start_line == 5
    assert command_claim.source.locator.end_line == 8
    assert evidence[int(command_claim.evidence_refs[0][1:]) - 1].locator.start_line == 5
    assert steps and steps[0].requires_execution is True
    assert results and results[0].status == "not_run"


def test_inventory_ignores_symlink_escape_and_caps(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (repo / "README.md").write_text("readme", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")
    (repo / "big.bin").write_bytes(b"x" * 20)
    (repo / "escape").symlink_to(outside)
    snap = inventory_repo(repo, max_files=1, max_bytes=10, max_file_bytes=10)
    paths = {f["path"] for f in snap.files}
    assert "node_modules/ignored.js" not in paths
    assert "escape" not in paths
    assert snap.truncated is False
    reasons = {(d["path"], d["reason"]) for d in snap.ignored_path_details}
    assert ("node_modules", "ignored_dir") in reasons
    assert ("escape", "symlink_escape") in reasons
    assert ("big.bin", "max_file_bytes") in reasons

    capped = tmp_path / "capped"
    capped.mkdir()
    (capped / "a.txt").write_text("a", encoding="utf-8")
    (capped / "b.txt").write_text("b", encoding="utf-8")
    capped_snap = inventory_repo(capped, max_files=1, max_bytes=100, max_file_bytes=10)
    assert capped_snap.truncated is True
    capped_reasons = {(d["path"], d["reason"]) for d in capped_snap.ignored_path_details}
    assert any(reason in {"max_files", "max_bytes"} for _, reason in capped_reasons)


def test_no_exec_probe_planning_and_not_run_results(fixture_repo):
    report, _ = build_report(str(fixture_repo), goal="Validate whether the README quickstart is real")
    assert report.probe_steps
    assert all(step.requires_execution for step in report.probe_steps)
    assert all(result.status == "not_run" for result in report.probe_results)
    assert report.metrics["target_code_execution"] is False
    assert not (fixture_repo / "SHOULD_NOT_EXIST").exists()

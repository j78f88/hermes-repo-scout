import json
import subprocess

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.intake import parse_repo_ref, planned_clone_command
from repo_scout.workspace import default_workspace_path


def _make_git_remote(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text(
        "# Remote fixture\n\n## Overview\nRemote fixture provides a static clone intake pattern for reviewing repository evidence without retaining source.\n\n## Quickstart\n```bash\npython -m remote_fixture\n```\n",
        encoding="utf-8",
    )
    (src / "pyproject.toml").write_text("[project]\nname='remote-fixture'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=src, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@repo-scout.test.invalid"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=src, check=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=src, check=True, capture_output=True, text=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(src), str(bare)], check=True, capture_output=True, text=True)
    return bare


def test_file_remote_static_clone_workspace_and_corpus(tmp_path):
    bare = _make_git_remote(tmp_path)
    workspace = tmp_path / "workspace"
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main,
        ["scout", bare.as_uri(), "--workspace", str(workspace), "--out", str(out), "--format", "both"],
    )
    assert result.exit_code == 0, result.output
    assert (workspace / "snapshots").is_dir()
    snapshots = list((workspace / "snapshots").glob("remote-*"))
    assert snapshots == []  # default retention removes transient clone after analysis
    assert (workspace / "runs" / "repos.jsonl").exists()
    assert (workspace / "runs" / "scans.jsonl").exists()
    scan_record = json.loads((workspace / "runs" / "scans.jsonl").read_text().splitlines()[-1])
    assert scan_record["workspace"] == str(workspace.resolve())
    assert scan_record["runner_policy"]["execution_allowed"] is False
    assert scan_record["metrics"]["target_code_execution"] is False
    assert scan_record["corpus_report_paths"]["json"].startswith(str(workspace.resolve() / "reports"))

    report = json.loads((out / "report.json").read_text())
    assert report["repo"]["path"] is None
    assert report["repo"]["commit"]
    assert report["runner_policy"]["execution_allowed"] is False
    remote_detail = report["repo"]["ignored_path_details"][0]
    assert remote_detail["reason"] == "remote_static_clone"
    clone_at = remote_detail["command"].index("clone")
    assert remote_detail["command"][clone_at:clone_at + 7] == ["clone", "--depth=1", "--single-branch", "--no-tags", "--recurse-submodules=no", "--no-checkout", "--filter=blob:limit=2000000"]
    assert "protocol.allow=never" in remote_detail["command"]
    assert "No target package managers" in " ".join(remote_detail["safety_notes"])
    assert any(d["reason"] == "transient_snapshot_removed" and d["keep_snapshot"] is False for d in report["repo"]["ignored_path_details"])
    assert report["idea_candidates"]


def test_file_remote_keep_snapshot_is_explicit(tmp_path):
    bare = _make_git_remote(tmp_path)
    workspace = tmp_path / "workspace-keep"
    out = tmp_path / "out-keep"
    result = CliRunner().invoke(
        main,
        ["scout", bare.as_uri(), "--workspace", str(workspace), "--out", str(out), "--format", "json", "--keep-snapshot"],
    )
    assert result.exit_code == 0, result.output
    snapshots = list((workspace / "snapshots").glob("remote-*"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "README.md").exists()
    report = json.loads((out / "report.json").read_text())
    assert report["repo"]["path"] == str(snapshots[0].resolve())
    assert any(d["reason"] == "snapshot_retained_explicitly" and d["keep_snapshot"] is True for d in report["repo"]["ignored_path_details"])


def test_workspace_option_controls_local_corpus_location(fixture_repo, tmp_path):
    workspace = tmp_path / "custom-corpus"
    out = tmp_path / "local-out"
    result = CliRunner().invoke(main, ["scan", str(fixture_repo), "--workspace", str(workspace), "--out", str(out), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert (workspace / "runs" / "repos.jsonl").exists()
    assert (workspace / "runs" / "scans.jsonl").exists()
    assert (workspace / "reports").is_dir()
    record = json.loads((workspace / "runs" / "scans.jsonl").read_text().splitlines()[-1])
    assert record["workspace"] == str(workspace.resolve())
    assert record["metrics"]["target_code_execution"] is False


def test_default_workspace_prefers_repo_root(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "subdir"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert default_workspace_path() == repo.resolve() / ".repo-scout"


def test_file_remote_uses_safe_clone_shape(tmp_path):
    bare = _make_git_remote(tmp_path)
    ref = parse_repo_ref(bare.as_uri())
    assert not ref.is_local
    cmd = planned_clone_command(ref, tmp_path / "dest")
    assert cmd == [
        "git", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
        "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "clone", "--depth=1", "--single-branch", "--no-tags", "--recurse-submodules=no",
        "--no-checkout", "--filter=blob:limit=2000000", bare.as_uri(), str(tmp_path / "dest"),
    ]

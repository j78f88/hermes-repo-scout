import json
import subprocess

from click.testing import CliRunner

from repo_scout.cli import main


def _make_idea_remote(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text(
        "# Ideas Repo\n\n"
        "## Architecture\n"
        "This repository provides a plugin workflow for agent routing and evidence capture.\n\n"
        "## Quickstart\n"
        "```bash\npython -m ideas\n```\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=src, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=src, check=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=src, check=True, capture_output=True, text=True)
    bare = tmp_path / "ideas.git"
    subprocess.run(["git", "clone", "--bare", str(src), str(bare)], check=True, capture_output=True, text=True)
    return bare


def test_remote_scan_surfaces_ideas_without_retaining_repo(tmp_path):
    bare = _make_idea_remote(tmp_path)
    workspace = tmp_path / "workspace"
    out = tmp_path / "out"
    result = CliRunner().invoke(main, ["scout", bare.as_uri(), "--workspace", str(workspace), "--out", str(out), "--format", "both"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text())
    assert report["repo"]["path"] is None
    assert any(d["reason"] == "transient_snapshot_removed" for d in report["repo"]["ignored_path_details"])
    assert report["idea_candidates"]
    assert any(i["kind"] in {"idea", "pattern", "risk"} for i in report["idea_candidates"])
    md = (out / "report.md").read_text()
    assert "## Idea candidates" in md
    assert "Pattern candidate" in md or "Capability idea" in md
    assert not list((workspace / "snapshots").glob("ideas-*"))



def test_readme_symlink_escape_is_not_read(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret\n\nThis repository provides secret architecture.\n", encoding="utf-8")
    (repo / "README.md").symlink_to(outside)
    out = tmp_path / "out"
    result = CliRunner().invoke(main, ["scout", str(repo), "--out", str(out), "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text())
    assert report["claims"] == []
    assert report["idea_candidates"] == []
    assert "secret architecture" not in (out / "report.json").read_text()


def test_secret_like_readme_text_is_redacted_from_reports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# Redaction\n\n"
        "## Architecture\n"
        "This repository provides an API workflow. api_key=supersecretvalue12345\n",
        encoding="utf-8",
    )
    out = tmp_path / "out-redact"
    result = CliRunner().invoke(main, ["scout", str(repo), "--out", str(out), "--format", "both"])
    assert result.exit_code == 0, result.output
    report_text = (out / "report.json").read_text()
    md_text = (out / "report.md").read_text()
    assert "supersecretvalue12345" not in report_text
    assert "supersecretvalue12345" not in md_text
    assert "api_key=<REDACTED>" in report_text or "api_key=<REDACTED>" in md_text

"""Hardened remote intake: transport, config isolation, large blobs and links."""
import os
import subprocess

from repo_scout.intake import (
    MAX_FETCH_BLOB_BYTES,
    SAFE_GIT_ENV,
    clone_remote_static,
    parse_repo_ref,
    planned_clone_command,
    safe_git_env,
)


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _bare_with_big_blob_and_symlink(tmp_path, allow_filter=True):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# Fixture\n\nA small readme.\n", encoding="utf-8")
    (src / "big.bin").write_bytes(os.urandom(MAX_FETCH_BLOB_BYTES + 1024))
    os.symlink("/etc/passwd", src / "escape")
    _git("init", "-q", cwd=src)
    _git("-c", "user.email=t@example.com", "-c", "user.name=T", "add", ".", cwd=src)
    _git("-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-qm", "fixture", cwd=src)
    bare = tmp_path / "remote.git"
    _git("clone", "-q", "--bare", str(src), str(bare))
    if allow_filter:
        _git("config", "uploadpack.allowFilter", "true", cwd=bare)
    return bare


def test_https_command_allows_only_https():
    cmd = planned_clone_command(parse_repo_ref("owner/repo"), __import__("pathlib").Path("/tmp/x"))
    assert "protocol.allow=never" in cmd and "protocol.https.allow=always" in cmd
    assert "protocol.file.allow=always" not in cmd
    assert "--no-checkout" in cmd and "--single-branch" in cmd


def test_file_fixture_command_allows_only_file(tmp_path):
    bare = _bare_with_big_blob_and_symlink(tmp_path)
    cmd = planned_clone_command(parse_repo_ref(bare.as_uri()), tmp_path / "d")
    assert "protocol.file.allow=always" in cmd and "protocol.https.allow=always" not in cmd


def test_safe_env_drops_inherited_git_settings():
    env = safe_git_env({"PATH": "/usr/bin", "GIT_SSH_COMMAND": "evil", "GIT_CONFIG_GLOBAL": "/tmp/evil", "SECRET_TOKEN": "x"})
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "GIT_SSH_COMMAND" not in env and "SECRET_TOKEN" not in env
    assert set(SAFE_GIT_ENV) <= set(env)


def test_clone_skips_large_blobs_and_symlinks(tmp_path):
    bare = _bare_with_big_blob_and_symlink(tmp_path)
    dest = tmp_path / "snap"
    meta = clone_remote_static(parse_repo_ref(bare.as_uri()), dest)
    assert (dest / "README.md").read_text(encoding="utf-8").startswith("# Fixture")
    assert not (dest / "big.bin").exists()
    assert not (dest / "escape").exists() and not (dest / "escape").is_symlink()
    assert [item["path"] for item in meta["not_fetched"]] == ["big.bin"]
    assert [item["path"] for item in meta["skipped_links"]] == ["escape"]
    assert meta["files_checked_out"] == 1
    missing = subprocess.run(["git", "-C", str(dest), "rev-list", "--objects", "--missing=print", "HEAD"],
                             capture_output=True, text=True, check=True).stdout
    assert any(line.startswith("?") for line in missing.splitlines())  # big blob never fetched


def test_clone_ignores_hostile_global_config(tmp_path, monkeypatch):
    bare = _bare_with_big_blob_and_symlink(tmp_path)
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text(f"[url \"file:///nonexistent/\"]\n\tinsteadOf = {bare.as_uri()}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    meta = clone_remote_static(parse_repo_ref(bare.as_uri()), tmp_path / "snap")
    assert meta["returncode"] == 0


def test_git_calls_trust_only_the_snapshot_path(tmp_path, monkeypatch):
    """NFS shares report a foreign owner; git must still read the snapshot (and its commit)."""
    import repo_scout.intake as intake

    bare = _bare_with_big_blob_and_symlink(tmp_path)
    seen = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        seen.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(intake.subprocess, "run", spy)
    dest = tmp_path / "snap"
    clone_remote_static(parse_repo_ref(bare.as_uri()), dest)
    assert intake._git_commit(dest)
    inner = [cmd for cmd in seen if "-C" in cmd]
    assert inner and all(f"safe.directory={dest.resolve()}" in cmd for cmd in inner)
    assert not any("safe.directory=*" in cmd for cmd in seen)


def test_failed_intake_removes_partial_snapshot(tmp_path, monkeypatch):
    import repo_scout.intake as intake

    bare = _bare_with_big_blob_and_symlink(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("git rev-list failed: simulated")

    monkeypatch.setattr(intake, "_present_tree_entries", broken)
    dest = tmp_path / "snap"
    try:
        clone_remote_static(parse_repo_ref(bare.as_uri()), dest)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected failure")
    assert not dest.exists()


def test_fails_closed_when_server_ignores_blob_filter(tmp_path):
    bare = _bare_with_big_blob_and_symlink(tmp_path, allow_filter=False)
    dest = tmp_path / "snap"
    meta = clone_remote_static(parse_repo_ref(bare.as_uri()), dest)
    assert not (dest / "big.bin").exists()
    assert meta["blob_filter_honoured"] is False
    assert meta["not_fetched_total"] == 1 and meta["not_fetched"][0]["size"] > MAX_FETCH_BLOB_BYTES
    assert meta["skipped_links_total"] == 1 and meta["exclusions_truncated"] is False


def test_inventory_reports_exact_ignored_total(tmp_path):
    from repo_scout.intake import inventory_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# x\n", encoding="utf-8")
    for i in range(250):
        (repo / f"pkg{i}" / "node_modules").mkdir(parents=True)
    snap = inventory_repo(repo)
    assert len(snap.ignored_paths) == 200
    assert snap.ignored_paths_total == 250
    assert snap.ignored_paths_truncated is True

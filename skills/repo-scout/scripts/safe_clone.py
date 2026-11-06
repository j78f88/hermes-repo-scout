#!/usr/bin/env python3
"""Hardened, transient, no-checkout-by-default static clone for Repo Scout helpers.

Mirrors the collector's intake rules (repo-scout app `intake.py`): https-only
transport, no system/global git config, symlinks/hooks/fsmonitor off,
single branch, no tags, no submodules, a blob size filter, and checkout of
regular files only. Nothing in the clone is ever executed or imported.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MAX_FETCH_BLOB_BYTES = 2_000_000
MAX_LISTED = 5000
SAFE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ASKPASS": "/bin/false",
    "GIT_LITERAL_PATHSPECS": "1",
}
SAFE_GIT_CONFIG = ["-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def safe_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    env.update(SAFE_GIT_ENV)
    return env


def clone_command(url: str, dest: Path) -> list[str]:
    if url.startswith("file://"):
        transport = ["-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
    elif url.startswith("https://"):
        transport = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    else:
        raise ValueError(f"only https:// (or file:// fixtures) may be cloned: {url}")
    return ["git", *transport, *SAFE_GIT_CONFIG, "clone", "--depth=1", "--single-branch", "--no-tags",
            "--recurse-submodules=no", "--no-checkout", f"--filter=blob:limit={MAX_FETCH_BLOB_BYTES}", url, str(dest)]


def _git(dest: Path, args: list[str], env: dict[str, str], data: bytes | None = None) -> subprocess.CompletedProcess:
    # safe.directory for exactly this path, command line only: NFS shares report a foreign owner.
    return subprocess.run(["git", *SAFE_GIT_CONFIG, "-c", f"safe.directory={dest.resolve()}", "-C", str(dest), *args], input=data, capture_output=True,
                          timeout=120, check=False, env=env)


def hardened_clone(url: str, dest: Path) -> dict:
    env = safe_env()
    done = subprocess.run(clone_command(url, dest), capture_output=True, text=True, timeout=180, check=False, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"git clone failed: {done.stderr.strip()[:500]}")
    missing_run = _git(dest, ["rev-list", "--objects", "--missing=print", "HEAD"], env)
    missing = {line[1:].split()[0] for line in missing_run.stdout.decode(errors="replace").splitlines() if line.startswith("?")}
    tree = _git(dest, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"], env)
    if tree.returncode != 0:
        raise RuntimeError("git ls-tree failed")
    keep: list[bytes] = []
    candidates: list[tuple[bytes, str]] = []
    not_fetched: list[str] = []
    skipped: list[str] = []
    for record in tree.stdout.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        if kind != "blob" or mode == "120000":
            skipped.append(path.decode(errors="replace"))
        elif sha in missing:
            not_fetched.append(path.decode(errors="replace"))
        else:
            candidates.append((path, sha))
    # Fail closed if the server ignored the filter: size present blobs (no lazy fetch).
    sizes: dict[str, int] = {}
    if candidates:
        batch = _git(dest, ["cat-file", "--batch-check=%(objectname) %(objectsize)"], env,
                     ("\n".join(sha for _p, sha in candidates) + "\n").encode())
        for line in batch.stdout.decode(errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                sizes[parts[0]] = int(parts[1])
    filter_honoured = True
    for path, sha in candidates:
        size = sizes.get(sha)
        if size is None:
            raise RuntimeError(f"could not size blob {sha}; refusing checkout")
        if size > MAX_FETCH_BLOB_BYTES:
            filter_honoured = False
            not_fetched.append(path.decode(errors="replace"))
        else:
            keep.append(path)
    if keep:
        checkout = _git(dest, ["checkout", "-q", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul"], env, b"\0".join(keep))
        if checkout.returncode != 0:
            raise RuntimeError("git checkout failed")
    head = _git(dest, ["rev-parse", "HEAD"], env).stdout.decode().strip()
    return {
        "commit": head,
        "files_checked_out": len(keep),
        "not_fetched": not_fetched[:MAX_LISTED],
        "not_fetched_total": len(not_fetched),
        "skipped_links": skipped[:MAX_LISTED],
        "skipped_links_total": len(skipped),
        "exclusions_truncated": len(not_fetched) > MAX_LISTED or len(skipped) > MAX_LISTED,
        "blob_filter_honoured": filter_honoured,
    }


@contextmanager
def transient_clone(url: str) -> Iterator[tuple[Path, dict]]:
    """Clone into a private temp dir and always delete it afterwards."""
    base = Path(tempfile.mkdtemp(prefix="repo-scout-clone-"))
    try:
        dest = base / "snapshot"
        meta = hardened_clone(url, dest)
        yield dest, meta
    finally:
        shutil.rmtree(base, ignore_errors=True)

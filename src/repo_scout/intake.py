from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .schemas import RepoSnapshot

DEFAULT_IGNORES = [
    ".git/**",
    ".git",
    "node_modules/**",
    "vendor/**",
    ".venv/**",
    "venv/**",
    "dist/**",
    "build/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".cache/**",
    "cache/**",
]

DEFAULT_IGNORE_DIR_NAMES = {
    ".git",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "cache",
    ".cache",
    "__pycache__",
    ".pytest_cache",
}

MANIFEST_NAMES = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile",
    "Dockerfile",
    "Makefile",
}

MANIFEST_ECOSYSTEMS = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "requirements-dev.txt": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "package.json": "node",
    "package-lock.json": "node",
    "pnpm-lock.yaml": "node",
    "yarn.lock": "node",
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
    "go.mod": "go",
    "go.sum": "go",
}

DOC_NAMES = {"README.md", "README.rst", "README.txt", "CHANGELOG.md", "CONTRIBUTING.md"}
LICENSE_PREFIXES = ("LICENSE", "COPYING")
CI_PARTS = (".github/workflows/", ".gitlab-ci.yml", "circleci", ".circleci")


@dataclass(frozen=True)
class RepoRef:
    raw: str
    is_local: bool
    path: Path | None = None
    url: str | None = None
    owner: str | None = None
    name: str | None = None
    clone_url: str | None = None
    ref: str | None = None


def _github_https(owner: str, name: str) -> str:
    return f"https://github.com/{owner}/{name}"


def parse_repo_ref(ref: str) -> RepoRef:
    candidate = Path(ref).expanduser()
    if candidate.exists():
        path = candidate.resolve()
        return RepoRef(raw=ref, is_local=True, path=path, url=path.as_uri(), name=path.name)

    parsed_url = urlparse(ref)
    if parsed_url.scheme == "file":
        path = Path(parsed_url.path).expanduser().resolve()
        if path.exists() and path.is_dir() and ((path / ".git").exists() or ((path / "HEAD").exists() and (path / "objects").is_dir())):
            return RepoRef(raw=ref, is_local=False, url=path.as_uri(), name=path.stem.removesuffix(".git"), clone_url=path.as_uri())
        return RepoRef(raw=ref, is_local=True, path=path, url=path.as_uri(), name=path.name)
    if parsed_url.scheme in {"https", "http", "ssh", "git"} and parsed_url.netloc.endswith("github.com"):
        parts = [p for p in parsed_url.path.strip("/").split("/") if p]
        if len(parts) >= 2:
            owner = parts[0]
            name = parts[1].removesuffix(".git")
            if owner and name and re.fullmatch(r"[A-Za-z0-9_.-]+", owner) and re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                url = _github_https(owner, name)
                ref_name = parts[3] if len(parts) >= 4 and parts[2] in {"tree", "blob"} else None
                return RepoRef(raw=ref, is_local=False, url=url, owner=owner, name=name, clone_url=f"{url}.git", ref=ref_name)

    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<name>.+?)(?:\.git)?$",
        r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)$",
    ]
    for pat in patterns:
        m = re.match(pat, ref)
        if m:
            owner = m.group("owner")
            name = m.group("name").removesuffix(".git")
            url = _github_https(owner, name)
            return RepoRef(raw=ref, is_local=False, url=url, owner=owner, name=name, clone_url=f"{url}.git")
    raise ValueError(f"Unsupported repo reference: {ref}")


MAX_FETCH_BLOB_BYTES = 2_000_000
MAX_LISTED_EXCLUSIONS = 5000

# Git environment for untrusted remote intake. No system/global config means no
# credential helpers, URL rewrites, LFS smudge filters or user hooks apply.
SAFE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ASKPASS": "/bin/false",
    "GIT_LITERAL_PATHSPECS": "1",
}
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
_SAFE_GIT_CONFIG = ["-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def _transport_config(clone_url: str) -> list[str]:
    if clone_url.startswith("file://"):
        return ["-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
    return ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]


def safe_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: source[k] for k in _PASSTHROUGH_ENV if k in source}
    env.update(SAFE_GIT_ENV)
    return env


def planned_clone_command(ref: RepoRef, destination: Path) -> list[str]:
    """Return the only allowed clone shape for remote static intake.

    The command is shallow, tagless, single-branch, submodule-disabled and
    does not check out files. Only https (or an explicit file:// fixture)
    transport is allowed, symlinks and hooks are disabled, and blobs over
    MAX_FETCH_BLOB_BYTES are left unfetched. repo-scout never runs target
    package managers, tests, build scripts, README commands, imported target
    code, hooks, or external scanners as part of this intake.
    """
    if ref.is_local or not ref.clone_url:
        raise ValueError("planned_clone_command requires a remote RepoRef")
    return [
        "git", *_transport_config(ref.clone_url), *_SAFE_GIT_CONFIG,
        "clone", "--depth=1", "--single-branch", "--no-tags", "--recurse-submodules=no",
        "--no-checkout", f"--filter=blob:limit={MAX_FETCH_BLOB_BYTES}",
        ref.clone_url, str(destination),
    ]


def _safe_directory(path: Path) -> list[str]:
    # NFS shares can report another owner, so git refuses the
    # repo as "dubious ownership". Trust exactly this path, on the command line
    # only, never globally.
    return ["-c", f"safe.directory={Path(path).resolve()}"]


def _git(destination: Path, args: list[str], env: dict[str, str], *, input_bytes: bytes | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *_SAFE_GIT_CONFIG, *_safe_directory(destination), "-C", str(destination), *args], input=input_bytes,
                          capture_output=True, timeout=timeout, check=False, env=env)


def _present_tree_entries(destination: Path, env: dict[str, str]) -> tuple[list[bytes], list[dict], list[dict]]:
    """Split HEAD into fetched regular files, unfetched large blobs and skipped links.

    Uses only commands that never trigger a lazy fetch: rev-list --missing=print
    and ls-tree without --long.
    """
    missing_run = _git(destination, ["rev-list", "--objects", "--missing=print", "HEAD"], env)
    if missing_run.returncode != 0:
        raise RuntimeError(f"git rev-list failed: {missing_run.stderr.decode(errors='replace').strip()}")
    missing = {line[1:].split()[0] for line in missing_run.stdout.decode(errors="replace").splitlines() if line.startswith("?")}
    tree = _git(destination, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"], env)
    if tree.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {tree.stderr.decode(errors='replace').strip()}")
    keep: list[bytes] = []
    candidates: list[tuple[bytes, str, str]] = []
    not_fetched: list[dict] = []
    skipped_links: list[dict] = []
    for record in tree.stdout.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        rel = path.decode(errors="replace")
        if kind != "blob":
            skipped_links.append({"path": rel, "mode": mode, "kind": kind})
        elif mode == "120000":
            skipped_links.append({"path": rel, "mode": mode, "kind": "symlink"})
        elif sha in missing:
            not_fetched.append({"path": rel, "blob": sha, "reason": f"blob over {MAX_FETCH_BLOB_BYTES} bytes not fetched"})
        else:
            candidates.append((path, sha, rel))
    # Fail closed if the server ignored the blob filter: size every present blob
    # (cat-file on present objects never triggers a lazy fetch) and leave any
    # over-limit blob unchecked-out.
    sizes: dict[str, int] = {}
    if candidates:
        batch = _git(destination, ["cat-file", "--batch-check=%(objectname) %(objectsize)"], env,
                     input_bytes=("\n".join(sha for _p, sha, _r in candidates) + "\n").encode())
        if batch.returncode != 0:
            raise RuntimeError(f"git cat-file failed: {batch.stderr.decode(errors='replace').strip()}")
        for line in batch.stdout.decode(errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                sizes[parts[0]] = int(parts[1])
    for path, sha, rel in candidates:
        size = sizes.get(sha)
        if size is None:
            raise RuntimeError(f"could not size blob for {rel}; refusing checkout")
        if size > MAX_FETCH_BLOB_BYTES:
            not_fetched.append({"path": rel, "blob": sha, "size": size,
                                "reason": f"blob over {MAX_FETCH_BLOB_BYTES} bytes was sent by the server but not checked out"})
        else:
            keep.append(path)
    return keep, not_fetched, skipped_links


def clone_remote_static(ref: RepoRef, destination: Path) -> dict:
    """Perform the only allowed remote static intake command."""
    cmd = planned_clone_command(ref, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Snapshot destination already exists: {destination}")
    env = safe_git_env()
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=120, check=False, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
        keep, not_fetched, skipped_links = _present_tree_entries(destination, env)
        if keep:
            checkout = _git(destination, ["checkout", "-q", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul"], env,
                            input_bytes=b"\0".join(keep))
            if checkout.returncode != 0:
                raise RuntimeError(f"git checkout failed: {checkout.stderr.decode(errors='replace').strip()}")
    except BaseException:
        # A failed intake must not leave a partial copy of target source behind.
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "command": cmd,
        "returncode": result.returncode,
        "stdout_excerpt": result.stdout[-1000:],
        "stderr_excerpt": result.stderr[-1000:],
        "git_env": sorted(SAFE_GIT_ENV),
        "files_checked_out": len(keep),
        "not_fetched": not_fetched[:MAX_LISTED_EXCLUSIONS],
        "not_fetched_total": len(not_fetched),
        "skipped_links": skipped_links[:MAX_LISTED_EXCLUSIONS],
        "skipped_links_total": len(skipped_links),
        "exclusions_truncated": len(not_fetched) > MAX_LISTED_EXCLUSIONS or len(skipped_links) > MAX_LISTED_EXCLUSIONS,
        "blob_filter_honoured": not any("sent by the server" in item["reason"] for item in not_fetched),
        "safety_notes": [
            "Remote intake used a shallow, single-branch, tagless, submodule-free clone with no checkout, then checked out regular files only.",
            "System and global git config were disabled; only https or an explicit file:// fixture transport was allowed; symlinks, hooks and fsmonitor were disabled.",
            f"Blobs over {MAX_FETCH_BLOB_BYTES} bytes were not checked out (and not fetched when the server honours the filter); symlinks and submodule links were not checked out.",
            "No target package managers, tests, build scripts, README commands, submodules, hooks, imported target code, or external scanners were run.",
        ],
    }


def intake_repo_ref(repo_ref: str, *, workspace: Path | None = None, run_id: str | None = None) -> tuple[RepoRef, Path, dict]:
    parsed = parse_repo_ref(repo_ref)
    if parsed.is_local and parsed.path:
        return parsed, parsed.path, {"type": "local", "safety_notes": ["Local static file inventory only; no target code executed."]}
    if workspace is None:
        raise ValueError("workspace is required for remote intake")
    from .workspace import ensure_workspace, workspace_snapshot_dir

    root = ensure_workspace(workspace)
    slug = f"{parsed.owner or 'remote'}-{parsed.name or 'repo'}"
    unique = run_id or f"{int(time.time() * 1000)}"
    dest = workspace_snapshot_dir(root, slug, unique)
    clone_meta = clone_remote_static(parsed, dest)
    clone_meta.update({"type": "remote_static_clone", "snapshot_path": str(dest)})
    return parsed, dest, clone_meta


def _ignored(rel: str, ignore_globs: list[str]) -> bool:
    rel_posix = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(rel_posix + "/", pattern) for pattern in ignore_globs)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _manifest_metadata(fname: str) -> dict:
    ecosystem = MANIFEST_ECOSYSTEMS.get(fname)
    if not ecosystem and fname.startswith("requirements") and fname.endswith(".txt"):
        ecosystem = "python"
    return {"ecosystem": ecosystem} if ecosystem else {}


def _git_commit(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *_SAFE_GIT_CONFIG, *_safe_directory(path), "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            env=safe_git_env(),
        )
    except Exception:
        return None
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def inventory_repo(
    repo_path: Path,
    *,
    repo_url: str | None = None,
    owner: str | None = None,
    name: str | None = None,
    max_files: int = 500,
    max_bytes: int = 2_000_000,
    max_file_bytes: int = 512_000,
    ignore_globs: list[str] | None = None,
) -> RepoSnapshot:
    root = repo_path.resolve()
    ignore_globs = [*(ignore_globs or []), *DEFAULT_IGNORES]
    files: list[dict] = []
    ignored: list[str] = []
    ignored_details: list[dict] = []
    total_bytes = 0
    truncated = False
    h = hashlib.sha256()

    def record_ignored(rel: str, reason: str, **extra: object) -> None:
        ignored.append(rel)
        if len(ignored_details) < 500:
            detail: dict[str, object] = {"path": rel, "reason": reason}
            detail.update(extra)
            ignored_details.append(detail)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix() if current != root else ""
        keep_dirs = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}".strip("/")
            p = current / d
            if d in DEFAULT_IGNORE_DIR_NAMES or _ignored(rel, ignore_globs):
                record_ignored(rel, "ignored_dir")
            elif p.is_symlink():
                reason = "symlink_escape" if not _is_within(p, root) else "symlink_dir"
                record_ignored(rel, reason)
            else:
                keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for fname in sorted(filenames):
            p = current / fname
            rel = p.relative_to(root).as_posix()
            if _ignored(rel, ignore_globs):
                record_ignored(rel, "ignored_file")
                continue
            if p.is_symlink():
                reason = "symlink_escape" if not _is_within(p, root) else "symlink_file"
                record_ignored(rel, reason)
                continue
            try:
                stat = p.stat()
            except OSError:
                record_ignored(rel, "stat_error")
                continue
            if not _is_within(p, root):
                record_ignored(rel, "path_escape")
                continue
            if stat.st_size > max_file_bytes:
                record_ignored(rel, "max_file_bytes", size=stat.st_size, limit=max_file_bytes)
                continue
            if len(files) >= max_files or total_bytes + stat.st_size > max_bytes:
                truncated = True
                reason = "max_files" if len(files) >= max_files else "max_bytes"
                record_ignored(rel, reason, size=stat.st_size, max_files=max_files, max_bytes=max_bytes)
                continue
            kind = "file"
            if fname in MANIFEST_NAMES or (fname.startswith("requirements") and fname.endswith(".txt")):
                kind = "manifest"
            elif fname in DOC_NAMES or rel.startswith("docs/"):
                kind = "doc"
            elif fname.upper().startswith(LICENSE_PREFIXES):
                kind = "license"
            elif any(part in rel for part in CI_PARTS):
                kind = "ci"
            elif "/test" in rel.lower() or fname.lower().startswith("test_"):
                kind = "test"
            file_entry = {"path": rel, "size": stat.st_size, "kind": kind}
            if kind == "manifest":
                file_entry.update(_manifest_metadata(fname))
            files.append(file_entry)
            total_bytes += stat.st_size
            h.update(rel.encode())
            h.update(str(stat.st_size).encode())

    return RepoSnapshot(
        url=repo_url or root.as_uri(),
        path=str(root),
        owner=owner,
        name=name or root.name,
        commit=_git_commit(root),
        checkout_hash="sha256:" + h.hexdigest(),
        files_scanned=len(files),
        bytes_scanned=total_bytes,
        files=files,
        ignored_paths=ignored[:200],
        ignored_paths_total=len(ignored),
        ignored_paths_truncated=len(ignored) > 200 or len(ignored_details) > 200,
        ignored_path_details=ignored_details[:200],
        truncated=truncated,
    )

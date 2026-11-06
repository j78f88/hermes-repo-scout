#!/usr/bin/env python3
"""Portable Repo Scout storage paths and fail-closed exclusion policy."""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Mapping

# A neutral safety default; configure additional boundaries with a regex.
DEFAULT_EXCLUDE_PATTERN = r"^excluded(?:[-_].*)?$"


def runs_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get("REPO_SCOUT_RUNS_ROOT") or env.get("REPO_SCOUT_CAMPAIGN_HOME")
    if base:
        return Path(base).expanduser()
    return Path(env.get("HOME", str(Path.home()))).expanduser() / ".hermes" / "repo-scout-runs"


def exclusion_pattern() -> re.Pattern[str]:
    pattern = os.environ.get("REPO_SCOUT_EXCLUDE_PATTERN") or DEFAULT_EXCLUDE_PATTERN
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError("invalid REPO_SCOUT_EXCLUDE_PATTERN; refusing traversal") from exc


def excluded_name(name: str) -> bool:
    return exclusion_pattern().search(name) is not None


def excluded_path(path: Path) -> bool:
    """Check lexical and resolved components before reading any target."""
    pattern = exclusion_pattern()
    expanded = path.expanduser()
    if any(pattern.search(part) for part in expanded.parts):
        return True
    try:
        resolved = expanded.resolve()
    except (OSError, RuntimeError):
        return True
    return any(pattern.search(part) for part in resolved.parts)

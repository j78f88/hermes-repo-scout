#!/usr/bin/env python3
"""Portable storage and configurable exclusion safety regressions."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_repo_scout as runner
import scout_index


class PortableStorageTests(unittest.TestCase):
    def test_all_helpers_default_to_supplied_home(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HOME": td}, clear=True):
            expected = Path(td) / ".hermes" / "repo-scout-runs"
            self.assertEqual(runner._runs_base(dict(os.environ)), expected)
            self.assertEqual(scout_index.runs_root(), expected)


class ExclusionSafetyTests(unittest.TestCase):
    def test_configured_exclusion_is_pruned_before_directory_listing(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"REPO_SCOUT_EXCLUDE_PATTERN": "sensitive"}):
            root = Path(td)
            secret = root / "2026-01" / "Sensitive-campaign"
            secret.mkdir(parents=True)
            real_scan = os.scandir
            listed = []

            def record(path):
                listed.append(Path(path))
                return real_scan(path)

            with mock.patch.object(scout_index.os, "scandir", side_effect=record):
                self.assertEqual(scout_index.build_index(root), [])
            self.assertNotIn(secret, listed)

    def test_excluded_base_and_resolved_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"REPO_SCOUT_EXCLUDE_PATTERN": "sensitive"}):
            root = Path(td)
            secret = root / "sensitive"
            secret.mkdir()
            link = root / "link"
            link.symlink_to(secret, target_is_directory=True)
            for path in (secret, link):
                with self.assertRaises(scout_index.BoundaryError):
                    scout_index.build_index(path)

    def test_invalid_exclusion_pattern_fails_closed(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"REPO_SCOUT_EXCLUDE_PATTERN": "["}):
            with self.assertRaises(ValueError):
                scout_index.build_index(Path(td))


if __name__ == "__main__":
    unittest.main()

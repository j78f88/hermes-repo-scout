#!/usr/bin/env python3
"""Tests for the citation-ledger quote and lane-pointer checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("verify_repo_scout_campaign.py")


def load_module():
    spec = importlib.util.spec_from_file_location("verify_repo_scout_campaign_ledger", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_campaign(td: str, *, quote: str, file_text: str, lines: tuple[int, int] | None) -> tuple[Path, dict]:
    root = Path(td) / "campaign"
    (root / "evidence").mkdir(parents=True)
    evidence = root / "evidence" / "github_repo.json"
    evidence.write_text(file_text, encoding="utf-8")
    entry: dict = {"text": quote, "path": "evidence/github_repo.json", "sha256": hashlib.sha256(file_text.encode()).hexdigest()}
    if lines:
        entry["start_line"], entry["end_line"] = lines
    ledger = {"schema_version": "repo-scout.citation-ledger.v1",
              "sources": [{"id": 1, "url": "https://api.github.com/repos/o/r", "path": "evidence/github_repo.json", "quotes": [entry]}]}
    (root / "citation-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    return root, ledger


class LedgerQuoteTests(unittest.TestCase):
    def setUp(self):
        self.v = load_module()

    def check(self, root, ledger, strict=True):
        errors, warnings = [], []
        self.v.validate_ledger_quotes(root, ledger, strict=strict, errors=errors, warnings=warnings)
        return errors, warnings

    def test_good_quote_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="x\nlicense is   MPL-2.0\n", lines=(2, 2))
            self.assertEqual(self.check(root, ledger), ([], []))

    def test_tampered_quote_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="x\nlicense is MPL-2.0\n", lines=(2, 2))
            ledger["sources"][0]["quotes"][0]["text"] = "license is Apache-2.0"
            errors, _ = self.check(root, ledger)
            self.assertEqual(errors, ["ledger source 1 quote 0: quote not found in lines 2-2"])

    def test_quote_outside_cited_range_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\nother\n", lines=(2, 2))
            errors, _ = self.check(root, ledger)
            self.assertIn("quote not found in lines 2-2", errors[0])

    def test_hash_drift_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\n", lines=(1, 1))
            (root / "evidence" / "github_repo.json").write_text("license is MPL-2.0\nchanged\n", encoding="utf-8")
            errors, _ = self.check(root, ledger)
            self.assertEqual(errors, ["ledger source 1 quote 0: evidence hash changed since citation"])

    def test_range_past_end_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\n", lines=(1, 9))
            errors, _ = self.check(root, ledger)
            self.assertIn("line range 1-9 outside file", errors[0])

    def test_path_escape_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="root:x:0:0", file_text="unused\n", lines=None)
            ledger["sources"][0]["quotes"][0]["path"] = "../../../../etc/passwd"
            ledger["sources"][0]["quotes"][0].pop("sha256")
            errors, _ = self.check(root, ledger)
            self.assertEqual(errors, ["ledger source 1 quote 0: evidence file missing or outside campaign root"])

    def test_missing_path_is_a_warning_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\n", lines=None)
            ledger["sources"][0].pop("path")
            ledger["sources"][0]["quotes"][0].pop("path")
            errors, warnings = self.check(root, ledger)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, ["ledger source 1 quote 0: cannot be rechecked; no saved evidence path"])

    def test_non_strict_reports_warnings_only(self):
        with tempfile.TemporaryDirectory() as td:
            root, ledger = make_campaign(td, quote="license is MPL-2.0", file_text="x\n", lines=None)
            errors, warnings = self.check(root, ledger, strict=False)
            self.assertEqual(errors, [])
            self.assertEqual(len(warnings), 1)


class LanePointerTests(unittest.TestCase):
    def setUp(self):
        self.v = load_module()

    def test_pointer_forms_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            root, _ = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\n", lines=None)
            repo_root = root / "repos" / "o_r"
            repo_root.mkdir(parents=True)
            sink: list[str] = []
            lane = {"evidence_refs": ["citation-ledger.json#/sources/0", "/citation-ledger.json#/sources/0/quotes/0", "LICENSE:L1-L3"]}
            self.v.validate_lane_pointers(root, repo_root, lane, "lane x", sink)
            self.assertEqual(sink, [])

    def test_broken_pointer_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, _ = make_campaign(td, quote="license is MPL-2.0", file_text="license is MPL-2.0\n", lines=None)
            sink: list[str] = []
            lane = {"evidence_refs": ["citation-ledger.json#/sources/7", "missing.json#/a", "../outside.json#/x"]}
            self.v.validate_lane_pointers(root, root, lane, "lane x", sink)
            self.assertEqual(len(sink), 3)
            self.assertIn("does not resolve", sink[0])


if __name__ == "__main__":
    unittest.main()

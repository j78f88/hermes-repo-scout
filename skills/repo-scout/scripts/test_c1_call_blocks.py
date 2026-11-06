#!/usr/bin/env python3
"""Tests for Repo Scout's C1 decision-call validation."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("verify_repo_scout_campaign.py")


def load_module():
    spec = importlib.util.spec_from_file_location("verify_repo_scout_campaign", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID = """# Scout

## What the owner needs to decide
1. **Should we keep this repository on the watch list?**
   - Why it matters: A stable release could make the pattern worth reassessing.
   - If you do nothing: The repository remains unadopted and no code runs.
   - Suggested: Later – Re-scout after the next stable release.
   - Sources: `repos/example/evidence/releases.json#/releases/0`, `repos/example/report.json`
"""


class RepoScoutC1CallBlockTests(unittest.TestCase):
    def test_accepts_en_dash_delimiter(self):
        module = load_module()
        self.assertEqual(module.validate_c1_call_block(VALID), [])

    def test_accepts_ascii_hyphen_delimiter(self):
        module = load_module()
        self.assertEqual(module.validate_c1_call_block(VALID.replace("Later –", "Later -")), [])

    def test_accepts_case_insensitive_labels(self):
        module = load_module()
        markdown = VALID.replace("Why it matters:", "WHY IT MATTERS:").replace(
            "If you do nothing:", "If You Do Nothing:"
        ).replace("Suggested:", "sUgGeStEd:").replace("Sources:", "SOURCES:")
        self.assertEqual(module.validate_c1_call_block(markdown), [])

    def test_accepts_absent_optional_section(self):
        module = load_module()
        self.assertEqual(module.validate_c1_call_block("# Scout\n\nNo owner call."), [])

    def test_rejects_em_dash_delimiter(self):
        module = load_module()
        errors = module.validate_c1_call_block(VALID.replace("Later –", "Later —"))
        self.assertTrue(any("Suggested" in error for error in errors), errors)

    def test_rejects_missing_required_fields(self):
        module = load_module()
        markdown = """## What the owner needs to decide
1. **Should we adopt it?**
   - Why it matters: The repository could fill a capability gap.
   - Suggested: Yes – Run a bounded scout.
   - Sources: `repos/example/report.json`
"""
        errors = module.validate_c1_call_block(markdown)
        self.assertTrue(any("If you do nothing" in error for error in errors), errors)

    def test_section_rejects_legacy_plain_question(self):
        module = load_module()
        errors = module.validate_c1_call_block(
            "## What the owner needs to decide\n1. Should we adopt it?\n"
        )
        self.assertTrue(any("bold question" in error for error in errors), errors)

    def test_synthesis_validator_reports_malformed_call(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            synthesis = root / "synthesis"
            synthesis.mkdir()
            (synthesis / "brief.md").write_text(
                "# Scout\n\n## What the owner needs to decide\n1. **Should we adopt it?**\n",
                encoding="utf-8",
            )
            (synthesis / "action-register.json").write_text("[]\n", encoding="utf-8")
            (synthesis / "verification.txt").write_text("PASS\n", encoding="utf-8")
            errors: list[str] = []
            module.validate_synthesis(root, "brief", errors)
            self.assertTrue(any("C1" in error for error in errors), errors)

    def test_synthesis_validator_reports_malformed_reader_summary(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            synthesis = root / "synthesis"
            synthesis.mkdir()
            (synthesis / "brief.md").write_text("# Scout\n", encoding="utf-8")
            (synthesis / "summary.md").write_text(
                "# Reader summary\n\n## What the owner needs to decide\n1. **Should we adopt it?**\n",
                encoding="utf-8",
            )
            (synthesis / "action-register.json").write_text("[]\n", encoding="utf-8")
            (synthesis / "verification.txt").write_text("PASS\n", encoding="utf-8")
            errors: list[str] = []
            module.validate_synthesis(root, "brief", errors)
            self.assertTrue(any("summary.md" in error and "C1" in error for error in errors), errors)

    def test_synthesis_validator_reports_malformed_nested_markdown(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            synthesis = root / "synthesis"
            nested = synthesis / "nested"
            nested.mkdir(parents=True)
            (synthesis / "brief.md").write_text("# Scout\n", encoding="utf-8")
            (nested / "bad.md").write_text(
                "# Nested\n\n## What the owner needs to decide\n1. **Should we adopt it?**\n",
                encoding="utf-8",
            )
            (synthesis / "action-register.json").write_text("[]\n", encoding="utf-8")
            (synthesis / "verification.txt").write_text("PASS\n", encoding="utf-8")
            errors: list[str] = []
            module.validate_synthesis(root, "brief", errors)
            self.assertTrue(any("nested/bad.md" in error and "C1" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()

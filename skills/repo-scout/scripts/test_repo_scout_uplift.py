#!/usr/bin/env python3
"""Tests for the Repo Scout uplift: storage root, env scrub, lint, provenance,
safe clone, skill scan, scout index, refresh check, next steps and `scout`."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import collect_provenance  # noqa: E402
import lane_lint  # noqa: E402
import safe_clone  # noqa: E402
import scan_skill_repo  # noqa: E402
import scout_index  # noqa: E402


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("run_repo_scout_uplift", "run_repo_scout.py")
verifier = load("verify_repo_scout_uplift", "verify_repo_scout_campaign.py")

FAKE_CLI = (
    "#!/usr/bin/env python3\n"
    "import json, os, pathlib, sys\n"
    "if os.environ.get('FAKE_ENV_LOG'):\n"
    "    pathlib.Path(os.environ['FAKE_ENV_LOG']).write_text(json.dumps(sorted(os.environ)))\n"
    "if sys.argv[1:] == ['doctor']:\n"
    "    print('default_runner no-exec')\n"
    "    print('target_code_execution disabled')\n"
    "    raise SystemExit(0)\n"
    "if sys.argv[1] == 'scout':\n"
    "    out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
    "    out.mkdir(parents=True, exist_ok=True)\n"
    "    files = [{'path': p, 'size': 10, 'kind': 'file'} for p in os.environ.get('FAKE_FILES', 'README.md').split(',')]\n"
    "    payload = {'schema_version': 'repo-scout.report.v1', 'status': 'completed', 'created_at': '2026-11-01T00:00:00Z',\n"
    "      'repo': {'url': sys.argv[2], 'commit': 'abc123', 'files': files, 'truncated': False},\n"
    "      'runner_policy': {'runner': 'no-exec', 'execution_allowed': False}, 'metrics': {'target_code_execution': False},\n"
    "      'verdict': {'decision': 'watch'},\n"
    "      'canonical_decision': {'decision': 'watch', 'consumption_mode': 'pattern', 'build_gate': 'peer_review'},\n"
    "      'action_register': [{'action_id':'AR-1','action_type':'watch_trigger','title':'Watch','target_surfaces':['candidate_ledger'],'recommendation':'watch_with_trigger','why':'fixture','evidence_refs':['E1'],'risk_notes':['fixture'],'owner_gate':'none','next_registration_step':'watch_ledger','confidence':0.5,'registration_status':'generated_from_report'}]}\n"
    "    (out / 'report.json').write_text(json.dumps(payload), encoding='utf-8')\n"
    "    (out / 'report.md').write_text('# fixture\\n', encoding='utf-8')\n"
    "    raise SystemExit(0)\n"
    "raise SystemExit(2)\n"
)


def fake_cli(temp: Path) -> Path:
    cli = temp / "repo-scout"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def start_campaign(temp: Path, mode: str = "standard", env: dict | None = None) -> Path:
    campaign = temp / "campaign"
    rc = runner.main(["start", "--repo", "https://github.com/example/project", "--mode", mode, "--root", str(campaign),
                      "--campaign-id", "fixture", "--created-at", "2026-11-01T00:00:00Z", "--cli", str(fake_cli(temp))],
                     environ={**os.environ, **(env or {})})
    assert rc == 0
    return campaign


class StorageAndEnvTests(unittest.TestCase):
    def test_default_root_follows_storage_rules(self):
        with tempfile.TemporaryDirectory() as td:
            now = datetime(2026, 11, 2, 12, 30, 5, tzinfo=timezone.utc)
            root = runner._default_root(["https://github.com/Sync/Thing"], "standard", None, {"REPO_SCOUT_RUNS_ROOT": td}, now=now)
            self.assertEqual(root, Path(td).resolve() / "2026-11" / "repo-scout-sync-thing-20261102T123005Z")
            self.assertNotIn("research", root.parts)

    def test_default_root_multi_repo_label(self):
        name = runner.default_run_name(["https://github.com/a/b", "https://github.com/c/d"], datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(name, "repo-scout-a-b-plus1-20260101T000000Z")

    def test_default_root_uses_home(self):
        with tempfile.TemporaryDirectory() as td:
            root = runner._default_root(["https://github.com/o/r"], "brief", None, {"HOME": td})
            self.assertEqual(root.parents[1], Path(td) / ".hermes" / "repo-scout-runs")

    def test_child_env_drops_python_paths_only(self):
        env = runner._child_env({"PYTHONPATH": "/bad", "PYTHONHOME": "/bad", "KEEP": "1"})
        self.assertEqual(env, {"KEEP": "1"})

    def test_collector_never_sees_inherited_pythonpath(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            log = temp / "env.json"
            start_campaign(temp, env={"PYTHONPATH": "/broken/site-packages", "FAKE_ENV_LOG": str(log)})
            self.assertNotIn("PYTHONPATH", json.loads(log.read_text()))

    def test_resume_requires_root(self):
        with self.assertRaises(SystemExit) as ctx:
            runner.main(["start", "--repo", "https://github.com/o/r", "--resume"], environ={})
        self.assertIn("--resume needs --root", str(ctx.exception))


class LaneLintTests(unittest.TestCase):
    def test_format_faults_are_fixed_without_touching_evidence_or_status(self):
        lane = {"schema_version": "repo-scout.lane-output.v1", "lane_id": "x", "status": "HOLD",
                "evidence_refs": ["citation-ledger.json#/sources/0"],
                "findings": {"fact": "one fact", "unknown": [{"text": "who maintains it"}], "inference": None},
                "forbidden_actions_observed": "no target repo code execution"}
        fixable, manual, fixed = lane_lint.lint_lane(lane)
        self.assertEqual(manual, [])
        self.assertEqual(fixed["status"], "hold")
        self.assertEqual(fixed["findings"]["fact"], ["one fact"])
        self.assertEqual(fixed["findings"]["unknown"], ["who maintains it"])
        self.assertEqual(fixed["findings_detail"]["unknown"], [{"text": "who maintains it"}])
        self.assertEqual(fixed["findings"]["upstream_claim"], [])
        self.assertEqual(fixed["evidence_refs"], lane["evidence_refs"])
        self.assertTrue(any("lowercase" in f for f in fixable))

    def test_manual_faults_are_not_auto_fixed(self):
        lane = {"schema_version": "repo-scout.lane-output.v1", "lane_id": "x", "status": "maybe",
                "evidence_refs": ["LICENSE:L1-L3"], "findings": {}, "forbidden_actions_observed": ["none"]}
        _fixable, manual, fixed = lane_lint.lint_lane(lane)
        self.assertEqual(fixed["status"], "maybe")
        self.assertTrue(any("not pass, hold or fail" in m for m in manual))
        self.assertTrue(any("LICENSE:L1-L3" in m for m in manual))
        self.assertTrue(any("no target repo code execution" in m for m in manual))

    def test_fix_format_keeps_original_and_verifier_still_passes_scaffold(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td))
            lane_path = campaign / "repos" / "example_project" / "lanes" / "provenance_activity.json"
            data = json.loads(lane_path.read_text())
            data["findings"]["unknown"] = "a string, not a list"
            original = json.dumps(data).encode()
            lane_path.write_bytes(original)
            runner._rehash(campaign)
            self.assertEqual(runner.main(["lint", str(campaign)], environ=dict(os.environ)), 0)
            self.assertEqual(lane_path.read_bytes(), original)  # report-only by default
            runner.main(["lint", str(campaign), "--fix-format"], environ=dict(os.environ))
            self.assertEqual(json.loads(lane_path.read_text())["findings"]["unknown"], ["a string, not a list"])
            backup = campaign / "receipts" / "pre-lint" / "repos__example_project__lanes__provenance_activity.json"
            self.assertEqual(backup.read_bytes(), original)
            status, errors, _ = verifier.verify_campaign(campaign)
            self.assertIn(status, {"scaffold", "pass"}, errors)

    def test_snapshot_sidecar_repoints_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td))
            sidecar = campaign / "repos" / "example_project" / "repo-scout-sidecar.json"
            digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            ledger_path = campaign / "citation-ledger.json"
            ledger = json.loads(ledger_path.read_text())
            ledger["sources"].append({"id": 1, "url": "local", "path": "repos/example_project/repo-scout-sidecar.json", "sha256": digest,
                                      "quotes": [{"text": "x", "path": "repos/example_project/repo-scout-sidecar.json", "sha256": digest}]})
            ledger_path.write_text(json.dumps(ledger))
            result = lane_lint.snapshot_sidecar(campaign, "example_project")
            self.assertEqual(result["ledger_rows_repointed"], 2)
            snap = campaign / result["snapshot"]
            self.assertEqual(hashlib.sha256(snap.read_bytes()).hexdigest(), digest)
            self.assertEqual(json.loads(ledger_path.read_text())["sources"][0]["path"], result["snapshot"])


def stub_fetchers(**overrides):
    base = {
        "github_repo": {"full_name": "o/r", "owner": {"type": "Organization"}, "archived": False, "fork": False,
                        "pushed_at": "2026-11-01T00:00:00Z", "stargazers_count": 5, "open_issues_count": 1,
                        "default_branch": "main", "license": {"spdx_id": "MPL-2.0"}},
        "github_head": {"sha": "b" * 40, "commit": {"committer": {"date": "2026-11-01T00:00:00Z"}}},
        "github_releases": [{"tag_name": "v1.2.0", "published_at": "2026-10-01T00:00:00Z"}],
        "scorecard": {"score": 5.8, "date": "2026-10-26", "repo": {"commit": "a" * 40},
                      "checks": [{"name": "Code-Review", "score": 2}, {"name": "License", "score": 10}, {"name": "Fuzzing", "score": -1}]},
        "depsdev": {"license": "non-standard"},
    }
    base.update(overrides)
    out = {}
    for name, payload in base.items():
        if isinstance(payload, Exception):
            def fail(exc=payload):
                raise exc
            out[name] = (f"https://example/{name}", fail)
        else:
            out[name] = (f"https://example/{name}", lambda body=json.dumps(payload).encode(): body)
    return out


class ProvenanceTests(unittest.TestCase):
    def test_summary_cross_checks_and_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "prov"
            summary = collect_provenance.run("o", "r", out, fetchers=stub_fetchers(depsdev=RuntimeError("503")))
            self.assertEqual(summary["facts"]["license_github"]["value"], "MPL-2.0")
            self.assertEqual(summary["facts"]["owner_type"]["evidence_refs"], ["github_repo.json#/owner/type"])
            self.assertEqual([c["check"] for c in summary["cross_checks"]], ["scorecard_stale"])
            self.assertEqual(summary["gaps"], ["depsdev: 503"])
            self.assertEqual([c["name"] for c in summary["scorecard_low_checks"]], ["Code-Review"])

    def test_license_disagreement_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            summary = collect_provenance.run("o", "r", Path(td), fetchers=stub_fetchers())
            self.assertIn("license_disagrees", [c["check"] for c in summary["cross_checks"]])

    def test_campaign_registration_and_pointer_refs_verify(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td))
            repo = runner._manifest_repos(campaign)[0]
            out = campaign / "repos" / repo["repo_slug"] / "evidence" / "provenance"
            summary = collect_provenance.run("o", "r", out, campaign_root=campaign, fetchers=stub_fetchers())
            ledger = json.loads((campaign / "citation-ledger.json").read_text())
            paths = sorted(s["path"] for s in ledger["sources"])
            self.assertEqual(len(paths), 5)
            for src in ledger["sources"]:
                self.assertEqual(hashlib.sha256((campaign / src["path"]).read_bytes()).hexdigest(), src["sha256"])
            refs = [ref for fact in summary["facts"].values() for ref in fact["evidence_refs"]]
            sink: list[str] = []
            verifier.validate_lane_pointers(campaign, campaign / "repos" / "example_project", {"evidence_refs": refs}, "lane", sink)
            self.assertEqual(sink, [])
            first = {s["path"]: s["sha256"] for s in ledger["sources"]}
            second = collect_provenance.run("o", "r", out, campaign_root=campaign,
                                            fetchers=stub_fetchers(github_repo=RuntimeError("timeout")))
            after = json.loads((campaign / "citation-ledger.json").read_text())["sources"]
            self.assertEqual(len(after), 9)  # new immutable run adds 4 files; nothing rewritten
            for path, digest in first.items():
                self.assertEqual(next(s["sha256"] for s in after if s["path"] == path), digest)
                self.assertEqual(hashlib.sha256((campaign / path).read_bytes()).hexdigest(), digest)
            self.assertIsNone(second["facts"]["license_github"]["value"])  # failed source is not read from the old run
            self.assertEqual(second["gaps"], ["github_repo: timeout"])
            latest = json.loads((out / "provenance-summary.json").read_text())
            self.assertEqual(latest["run_folder"], second["run_folder"])

    def test_parse_github(self):
        self.assertEqual(collect_provenance.parse_github("https://github.com/o/r.git"), ("o", "r"))
        with self.assertRaises(ValueError):
            collect_provenance.parse_github("https://gitlab.com/o/r")


def make_bare(temp: Path, files: dict[str, bytes], link: str | None = None) -> Path:
    src = temp / "src"
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    if link:
        os.symlink("/etc/passwd", src / link)
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]
    subprocess.run(git + ["init", "-q"], cwd=src, check=True)
    subprocess.run(git + ["add", "."], cwd=src, check=True)
    subprocess.run(git + ["commit", "-qm", "fixture"], cwd=src, check=True)
    bare = temp / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=bare, check=True)
    return bare


class SafeCloneTests(unittest.TestCase):
    def test_large_blob_and_symlink_not_checked_out_and_clone_removed(self):
        with tempfile.TemporaryDirectory() as td:
            bare = make_bare(Path(td), {"README.md": b"hi\n", "big.bin": os.urandom(safe_clone.MAX_FETCH_BLOB_BYTES + 10)}, link="escape")
            with safe_clone.transient_clone(bare.as_uri()) as (snap, meta):
                self.assertTrue((snap / "README.md").is_file())
                self.assertFalse((snap / "big.bin").exists())
                self.assertFalse((snap / "escape").exists() or (snap / "escape").is_symlink())
                self.assertEqual(meta["not_fetched"], ["big.bin"])
                self.assertEqual(meta["skipped_links"], ["escape"])
                kept = snap
            self.assertFalse(kept.exists())

    def test_only_https_or_file(self):
        for url in ("ssh://git@github.com/o/r", "git@github.com:o/r.git", "http://github.com/o/r", "ext::sh -c id"):
            with self.assertRaises(ValueError):
                safe_clone.clone_command(url, Path("/tmp/x"))


class FakeFinding(SimpleNamespace):
    pass


def fake_checkers(calls: list[Path]):
    def scan_skill(path, source="community"):
        calls.append(path)
        text = (path / "SKILL.md").read_text()
        findings = [FakeFinding(pattern_id="curl_pipe_shell", severity="critical", category="exec", file="SKILL.md", line=3,
                                description="pipes a download to a shell", match="SECRET TARGET TEXT")] if "| sh" in text else []
        return SimpleNamespace(verdict="dangerous" if findings else "safe", findings=findings)
    return scan_skill, (lambda path: []), (lambda md: [])


class SkillScanTests(unittest.TestCase):
    def test_scan_tree_flags_overlaps_and_skips_symlinked_skill(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            library = temp / "library" / "software-development" / "plan"
            library.mkdir(parents=True)
            (library / "SKILL.md").write_text("---\nname: plan\ndescription: Write a markdown implementation plan with tasks and file paths.\n---\n")
            pdf = temp / "library" / "productivity" / "pdf"
            pdf.mkdir(parents=True)
            (pdf / "SKILL.md").write_text("---\nname: pdf\ndescription: Merge, split and fill PDF documents.\n---\n")
            snap = temp / "snap"
            (snap / "bad").mkdir(parents=True)
            (snap / "bad" / "SKILL.md").write_text("---\nname: bad\ndescription: Installer helper.\n---\nRun curl x | sh\n")
            (snap / "planner").mkdir()
            (snap / "planner" / "SKILL.md").write_text("---\nname: planner\ndescription: Write an implementation plan with tasks and exact file paths.\n---\n")
            (snap / "planner" / "scripts").mkdir()
            (snap / "planner" / "scripts" / "go.py").write_text("print(1)\n")
            (snap / "linked").mkdir()
            os.symlink(snap / "bad" / "SKILL.md", snap / "linked" / "SKILL.md")
            calls: list[Path] = []
            rows = scan_skill_repo.scan_tree(snap, temp / "library", checkers=fake_checkers(calls))
            self.assertEqual([r["skill_dir"] for r in rows], ["bad", "planner"])
            self.assertEqual(rows[0]["guard_verdict"], "dangerous")
            self.assertNotIn("SECRET TARGET TEXT", json.dumps(rows))
            self.assertEqual(rows[1]["library_overlap"][0]["skill"], "plan")
            self.assertEqual(rows[1]["scripts"], ["scripts/go.py"])

    def test_run_uses_transient_clone_and_records_commit_gap(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            bare = make_bare(temp, {"s/SKILL.md": b"---\nname: s\ndescription: Example skill.\n---\n"})
            out = temp / "skill-scan.json"
            payload = scan_skill_repo.run(bare.as_uri(), out, library=temp, expect_commit="deadbeef", checkers=fake_checkers([]))
            self.assertEqual(payload["skills_found"], 1)
            self.assertTrue(any("differs from collector commit" in g for g in payload["gaps"]))
            self.assertEqual(json.loads(out.read_text())["scanned_commit"], payload["scanned_commit"])


def write_campaign(base: Path, month: str, name: str, *, repo_url: str, commit: str | None, verification: str | None,
                   decision: str = "watch", licence: str | None = None) -> Path:
    root = base / month / name
    slug = repo_url.rstrip("/").split("/")[-2] + "_" + repo_url.rstrip("/").split("/")[-1]
    (root / "repos" / slug).mkdir(parents=True)
    manifest = {"schema_version": "repo-scout.campaign.v1", "campaign_id": name, "mode": "standard",
                "repositories": [{"repo_slug": slug, "repo_url": repo_url, "status": "verified",
                                  "freshness": {"inspected_at": f"{month}-01T00:00:00Z", "inspected_commit": commit or "unknown"}}]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "repos" / slug / "repo-scout-sidecar.json").write_text(json.dumps({"decision": decision, "consumption_mode": "pattern"}))
    if verification:
        (root / "synthesis").mkdir()
        (root / "synthesis" / "verification.txt").write_text(f"repo-scout campaign verification: {verification}\n")
    if licence:
        prov = root / "repos" / slug / "evidence" / "provenance"
        prov.mkdir(parents=True)
        (prov / "provenance-summary.json").write_text(json.dumps({"facts": {"license_github": {"value": licence},
                                                                             "latest_release": {"value": "v1"}, "archived": {"value": False}}}))
    return root


class IndexTests(unittest.TestCase):
    def test_index_reads_campaigns_and_never_opens_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            write_campaign(base, "2026-10", "repo-scout-o-r-1", repo_url="https://github.com/O/R", commit="c1", verification="PASS")
            write_campaign(base, "2026-11", "repo-scout-o-r-2", repo_url="https://github.com/o/r", commit="c2", verification=None)
            write_campaign(base, "2026-10", "EXCLUDED-secret", repo_url="https://github.com/v/l", commit="x", verification="PASS")
            (base / "2026-10" / "other").mkdir()
            (base / "2026-10" / "other" / "manifest.json").write_text("[1, 2]")
            opened: list[Path] = []
            real = scout_index._read_json
            with mock.patch.object(scout_index, "_read_json", side_effect=lambda p: (opened.append(p), real(p))[1]):
                rows = scout_index.build_index(base)
            self.assertFalse(any("EXCLUDED" in str(p) for p in opened))
            self.assertEqual([r["inspected_commit"] for r in rows], ["c1", "c2"])
            latest = scout_index.latest_for(rows, "https://github.com/o/r.git")
            assert latest is not None
            self.assertEqual(latest["inspected_commit"], "c1")  # verified scout preferred over unverified

    def test_refresh_check_rules(self):
        row = {"inspected_commit": "c1", "license": "MIT", "latest_release": "v1", "archived": False,
               "provenance_recorded": True, "campaign_root": "/x",
               "provenance_sources": {"github_repo": "ok", "github_head": "ok", "github_releases": "ok"}}
        live = {"available": True, "releases_available": True, "head": "c1", "license": "MIT", "latest_release": "v1", "archived": False}
        check = lambda r, **kw: scout_index.refresh_check(r, {**live, **kw})["result"]
        self.assertEqual(check(row), "no_material_delta")
        self.assertEqual(check(row, head="c2"), "minor_delta")
        self.assertEqual(check(row, license="GPL-3.0"), "material_delta")
        self.assertEqual(check(row, license=None), "material_delta")
        self.assertEqual(check(row, latest_release="v2"), "material_delta")
        self.assertEqual(check({**row, "latest_release": None}, latest_release="v1"), "material_delta")  # first release
        self.assertEqual(check(row, latest_release=None), "material_delta")  # release withdrawn
        self.assertEqual(check(row, archived=True), "material_delta")
        self.assertEqual(check({**row, "archived": True}), "material_delta")  # unarchived
        self.assertEqual(check({**row, "inspected_commit": None}), "material_delta")
        self.assertEqual(check(row, head=None), "unknown_live_state")
        self.assertEqual(check(row, available=False), "unknown_live_state")
        self.assertEqual(check(row, releases_available=False, latest_release=None), "incomplete_evidence")
        self.assertEqual(check({**row, "provenance_recorded": False, "license": None}), "incomplete_evidence")
        self.assertEqual(scout_index.refresh_check(None, live)["result"], "no_prior_scout")
        # prior release list failed: a live release must not look like "first release", nor "no change"
        failed_releases = {**row, "latest_release": None, "provenance_sources": {**row["provenance_sources"], "github_releases": "unavailable"}}
        result = scout_index.refresh_check(failed_releases, {**live, "latest_release": "v1"})
        self.assertEqual(result["result"], "incomplete_evidence")
        self.assertIn("prior release list was not observed", result["gaps"])

    def test_index_refuses_excluded_base_and_slug_escapes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "shared-ops" / "excluded"
            base.mkdir(parents=True)
            with self.assertRaises(scout_index.BoundaryError):
                scout_index.build_index(base)
            runs = Path(td) / "runs"
            root = write_campaign(runs, "2026-10", "repo-scout-o-r-1", repo_url="https://github.com/o/r", commit="c1", verification="PASS")
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["repositories"].append({"repo_slug": "../../EXCLUDED-x", "repo_url": "https://github.com/v/x"})
            manifest["repositories"].append({"repo_slug": "EXCLUDED-y", "repo_url": "https://github.com/v/y"})
            (root / "manifest.json").write_text(json.dumps(manifest))
            secret = Path(td) / "EXCLUDED-private"
            secret.mkdir()
            (secret / "manifest.json").write_text(json.dumps(manifest))
            os.symlink(secret, runs / "2026-10" / "linked")
            opened: list[Path] = []
            real = scout_index._read_json
            with mock.patch.object(scout_index, "_read_json", side_effect=lambda p: (opened.append(p), real(p))[1]):
                rows = scout_index.build_index(runs)
            self.assertEqual([r["repo_url"] for r in rows], ["https://github.com/o/r"])
            self.assertFalse(any("EXCLUDED" in str(p) or "excluded" in p.parts for p in opened))

    def test_older_summaries_derive_sources_from_gaps(self):
        self.assertEqual(scout_index._prov_sources({}), {})
        derived = scout_index._prov_sources({"facts": {"x": {}}, "gaps": ["scorecard: HTTP Error 404"]})
        self.assertEqual(derived["scorecard"], "unavailable")
        self.assertEqual(derived["github_releases"], "ok")
        self.assertEqual(scout_index._prov_sources({"facts": {"x": {}}, "sources": {"github_repo": "ok"}}), {"github_repo": "ok"})

    def test_index_command_writes_cache(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "runs"
            write_campaign(base, "2026-10", "repo-scout-o-r-1", repo_url="https://github.com/o/r", commit="c1", verification="PASS", licence="MIT")
            out = Path(td) / "index.json"
            self.assertEqual(runner.main(["index", "--runs-root", str(base), "--out", str(out)], environ=dict(os.environ)), 0)
            rows = json.loads(out.read_text())["rows"]
            self.assertEqual(rows[0]["license"], "MIT")


class ScoutCommandTests(unittest.TestCase):
    def test_scout_runs_pipeline_and_prints_next_steps(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            campaign = temp / "campaign"
            calls: list[str] = []
            with mock.patch.object(runner, "_provenance_for", side_effect=lambda root, repo: calls.append("prov") or {"cross_checks": [], "gaps": []}), \
                 mock.patch.object(runner, "_skill_scan_for", side_effect=lambda root, repo, env: calls.append("scan") or {"skills_found": 1, "verdicts": {}, "gaps": []}), \
                 mock.patch("sys.stdout.write") as write:
                rc = runner.main(["scout", "--repo", "https://github.com/example/project", "--root", str(campaign),
                                  "--cli", str(fake_cli(temp))], environ={**os.environ, "FAKE_FILES": "README.md,skills/x/SKILL.md"})
            text = "".join(call.args[0] for call in write.call_args_list)
            self.assertEqual(rc, 0)
            self.assertEqual(calls, ["prov", "scan"])
            self.assertIn("Lanes not yet passed: provenance_activity (not started)", text)
            self.assertIn("worker-packet-fit_verdict.md", text)
            self.assertIn("finalize", text)
            status, errors, _ = verifier.verify_campaign(campaign)
            self.assertEqual(status, "scaffold", errors)

    def test_scout_skips_skill_scan_without_skill_md(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            calls: list[str] = []
            with mock.patch.object(runner, "_skill_scan_for", side_effect=lambda *a: calls.append("scan")), \
                 mock.patch("sys.stdout.write"):
                rc = runner.main(["scout", "--repo", "https://github.com/example/project", "--root", str(temp / "c"),
                                  "--cli", str(fake_cli(temp)), "--no-provenance"], environ=dict(os.environ))
            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])

    def test_status_next(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td), mode="brief")
            with mock.patch("sys.stdout.write") as write:
                runner.main(["status", str(campaign), "--next"], environ=dict(os.environ))
            text = "".join(call.args[0] for call in write.call_args_list)
            self.assertIn("Lanes not yet passed", text)


class ReviewRegressionTests(unittest.TestCase):
    def test_strict_ref_policy_rejects_unresolved_and_unsupported_refs(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td), mode="brief")
            manifest = json.loads((campaign / "manifest.json").read_text())
            self.assertEqual(manifest["evidence_ref_policy"], "resolvable-v1")
            repo_root = campaign / "repos" / "example_project"
            (repo_root / "evidence").mkdir(exist_ok=True)
            (repo_root / "evidence" / "notes.txt").write_text("a\nb\nc\n")
            report = json.loads((repo_root / "collector" / "report" / "report.json").read_text())
            report["evidence"] = [{"id": "E1"}]
            (repo_root / "collector" / "report" / "report.json").write_text(json.dumps(report))
            sink: list[str] = []
            ids = verifier.report_evidence_ids(report)
            refs = ["E1", "repos/example_project/evidence/notes.txt#L2-L3", "evidence/notes.txt#L1",
                    "NONEXISTENT-SOURCE-999", "E99", "evidence/notes.txt#L2-L9", "LICENSE:L1-L3", {"id": "M1"}]
            verifier.validate_lane_pointers(campaign, repo_root, {"evidence_refs": refs}, "lane", sink, strict_policy=True, report_ids=ids)
            self.assertEqual(len(sink), 5, sink)
            legacy: list[str] = []
            verifier.validate_lane_pointers(campaign, repo_root, {"evidence_refs": refs}, "lane", legacy, strict_policy=False, report_ids=ids)
            self.assertEqual(legacy, [])

    def test_finalize_fails_on_unresolved_ref_in_new_campaign(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td), mode="brief")
            repo_root = campaign / "repos" / "example_project"
            lane_path = repo_root / "lanes" / "controller.json"
            lane = json.loads(lane_path.read_text())
            lane.update({"status": "pass", "evidence_refs": ["NONEXISTENT-SOURCE-999"], "gaps": [],
                         "findings": {"fact": ["x"], "upstream_claim": [], "independent_evidence": [], "inference": [], "unknown": []},
                         "forbidden_actions_observed": ["no target repo code execution"]})
            lane_path.write_text(json.dumps(lane))
            side_path = repo_root / "repo-scout-sidecar.json"
            side = json.loads(side_path.read_text())
            side["lanes"][0]["status"] = "pass"
            side_path.write_text(json.dumps(side))
            (campaign / "synthesis").mkdir(exist_ok=True)
            (campaign / "synthesis" / "brief.md").write_text("# Brief\n\nWatch.\n")
            (campaign / "synthesis" / "action-register.json").write_text("[]")
            with mock.patch("sys.stdout.write"), mock.patch("sys.stderr.write"):
                rc = runner.main(["finalize", str(campaign)], environ=dict(os.environ))
            self.assertEqual(rc, 1)
            self.assertIn("NONEXISTENT-SOURCE-999", (campaign / "synthesis" / "verification.txt").read_text())

    def test_lint_checks_every_declared_field_type(self):
        lane = {"schema_version": None, "lane_id": 7, "status": "hold", "evidence_refs": [],
                "findings": {f: [] for f in lane_lint.FINDING_FIELDS}, "forbidden_actions_observed": [3], "gaps": 4}
        _fixable, manual, _fixed = lane_lint.lint_lane(lane)
        joined = "\n".join(manual)
        for needle in ("schema_version", "lane_id", "forbidden_actions_observed", "gaps"):
            self.assertIn(needle, joined)

    def test_lint_survives_wrong_status_and_detail_types(self):
        base = {"schema_version": "repo-scout.lane-output.v1", "lane_id": "x", "evidence_refs": [],
                "findings": {f: [] for f in lane_lint.FINDING_FIELDS}, "forbidden_actions_observed": ["no target repo code execution"]}
        _f, manual, _x = lane_lint.lint_lane({**base, "status": []})
        self.assertTrue(any("status must be the string" in m for m in manual))
        lane = {**base, "status": "hold", "findings_detail": [], "findings": {**base["findings"], "unknown": [{"text": "x"}]}}
        _f, manual, fixed = lane_lint.lint_lane(lane)
        self.assertTrue(any("findings_detail must be an object" in m for m in manual))
        self.assertNotIn("findings_detail", fixed)

    def test_skill_scan_reruns_never_overwrite_cited_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = start_campaign(Path(td), mode="brief")
            repo = runner._manifest_repos(campaign)[0]
            fake_python = Path(td) / "venv" / "bin" / "python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text("")
            counter = {"n": 0}

            def fake_run(command, *, environ, timeout=600):
                counter["n"] += 1
                out = Path(command[command.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("xb") as handle:
                    handle.write(json.dumps({"scan": counter["n"]}).encode())
                return SimpleNamespace(returncode=0, stdout=json.dumps({"skills_found": 1, "verdicts": {}, "gaps": []}), stderr="")

            with mock.patch.object(runner, "_run", side_effect=fake_run), \
                 mock.patch.object(runner, "_hermes_python", return_value=fake_python), \
                 mock.patch.object(runner, "datetime") as fake_dt:
                fake_dt.now.return_value = datetime(2026, 11, 3, 1, 2, 3, tzinfo=timezone.utc)
                runner._skill_scan_for(campaign, repo, {})
                ledger = json.loads((campaign / "citation-ledger.json").read_text())
                first = next(s for s in ledger["sources"] if "skill-scan" in s["path"])
                first_bytes = (campaign / first["path"]).read_bytes()
                runner._skill_scan_for(campaign, repo, {})
            ledger = json.loads((campaign / "citation-ledger.json").read_text())
            scans = [s for s in ledger["sources"] if "skill-scan" in s["path"]]
            self.assertEqual(len(scans), 2)
            self.assertNotEqual(scans[0]["path"], scans[1]["path"])
            self.assertEqual((campaign / first["path"]).read_bytes(), first_bytes)
            alias = campaign / "repos" / repo["repo_slug"] / "evidence" / "skill-scan" / "skill-scan.json"
            self.assertEqual(json.loads(alias.read_text()), {"scan": 2})
            self.assertFalse(any(s["path"].endswith("skill-scan/skill-scan.json") for s in scans))

    def test_scan_skill_repo_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "skill-scan.json"
            out.write_text("{}")
            with self.assertRaises(FileExistsError):
                scan_skill_repo.run("https://github.com/o/r", out, library=Path(td))

    def test_ast_tuple_findings_keep_their_fields(self):
        row = scan_skill_repo._ast_row(("scripts/x.py", 12, "dynamic_exec", "uses exec"))
        self.assertEqual(row, {"pattern_id": "dynamic_exec", "file": "scripts/x.py", "line": 12, "description": "uses exec"})

    def test_real_hermes_ast_checker_round_trip(self):
        try:
            _scan, ast_scan_path, _lint = scan_skill_repo.load_checkers()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Hermes checkers not importable here: {exc}")
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "s"
            (skill / "scripts").mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n")
            (skill / "scripts" / "x.py").write_text("import importlib\nimportlib.import_module(input())\n")
            rows = [scan_skill_repo._ast_row(f) for f in ast_scan_path(skill)]
            self.assertTrue(rows)
            self.assertTrue(all(r["pattern_id"] and r["file"] and r["line"] for r in rows), rows)

    def test_safe_clone_fails_closed_when_filter_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            bare = make_bare(Path(td), {"README.md": b"hi\n", "big.bin": os.urandom(safe_clone.MAX_FETCH_BLOB_BYTES + 10)})
            subprocess.run(["git", "config", "--unset", "uploadpack.allowFilter"], cwd=bare, check=True)
            with safe_clone.transient_clone(bare.as_uri()) as (snap, meta):
                self.assertFalse((snap / "big.bin").exists())
                self.assertFalse(meta["blob_filter_honoured"])
                self.assertEqual(meta["not_fetched_total"], 1)


if __name__ == "__main__":
    unittest.main()

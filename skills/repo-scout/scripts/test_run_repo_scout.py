#!/usr/bin/env python3
"""Regression tests for the Repo Scout skill runner."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("run_repo_scout.py")


def load_module():
    spec = importlib.util.spec_from_file_location("run_repo_scout", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepoScoutSkillRunnerTests(unittest.TestCase):
    def test_resolve_cli_prefers_explicit_executable(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            cli = Path(td) / "repo-scout"
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            self.assertEqual(module.resolve_cli(str(cli), environ={}), cli.resolve())

    def test_resolve_cli_falls_back_to_local_app_venv(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cli = home / ".local" / "bin" / "repo-scout"
            cli.parent.mkdir(parents=True)
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            with mock.patch.object(module.shutil, "which", return_value=None):
                resolved = module.resolve_cli(None, environ={"HOME": str(home)})
            self.assertEqual(resolved, cli.resolve())

    def test_start_runs_no_exec_collector_and_updates_manifest(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            log = temp / "calls.jsonl"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "with open(os.environ['FAKE_CLI_LOG'], 'a', encoding='utf-8') as h:\n"
                "    h.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:] == ['doctor']:\n"
                "    print('repo-scout 0.1.0')\n"
                "    print('default_runner no-exec')\n"
                "    print('target_code_execution disabled')\n"
                "    raise SystemExit(0)\n"
                "if sys.argv[1] == 'scout':\n"
                "    out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
                "    out.mkdir(parents=True, exist_ok=True)\n"
                "    payload = {\n"
                "      'schema_version': 'repo-scout.report.v1',\n"
                "      'status': 'completed',\n"
                "      'created_at': '2026-08-31T00:00:00Z',\n"
                "      'repo': {'url': sys.argv[2], 'commit': 'abc123'},\n"
                "      'runner_policy': {'runner': 'no-exec', 'execution_allowed': False},\n"
                "      'metrics': {'target_code_execution': False},\n"
                "      'verdict': {'decision': 'watch'},\n"
                "      'canonical_decision': {'decision': 'watch', 'consumption_mode': 'pattern', 'build_gate': 'peer_review'},\n"
                "      'action_register': [{'action_id':'AR-1','action_type':'watch_trigger','title':'Watch','target_surfaces':['candidate_ledger'],'recommendation':'watch_with_trigger','why':'fixture','evidence_refs':['E1'],'risk_notes':['fixture'],'owner_gate':'none','next_registration_step':'watch_ledger','confidence':0.5,'registration_status':'generated_from_report'}]\n"
                "    }\n"
                "    (out / 'report.json').write_text(json.dumps(payload), encoding='utf-8')\n"
                "    (out / 'report.md').write_text('# fixture\\n', encoding='utf-8')\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(2)\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            env = {**os.environ, "FAKE_CLI_LOG": str(log)}
            rc = module.main([
                "start",
                "--repo", "https://github.com/example/project",
                "--mode", "brief",
                "--root", str(campaign),
                "--campaign-id", "fixture-campaign",
                "--created-at", "2026-08-31T00:00:00Z",
                "--cli", str(cli),
            ], environ=env)
            self.assertEqual(rc, 0)
            manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
            repo = manifest["repositories"][0]
            self.assertEqual(manifest["status"], "running")
            self.assertEqual(repo["status"], "collector_done")
            self.assertEqual(repo["freshness"]["inspected_commit"], "abc123")
            self.assertEqual(repo["report_path"], str(campaign / "repos" / "example_project" / "collector" / "report" / "report.json"))
            self.assertTrue((campaign / "receipts" / "doctor.txt").exists())
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            scout_call = next(call for call in calls if call and call[0] == "scout")
            self.assertNotIn("--runner", scout_call)

    def test_start_keeps_partial_success_and_records_failed_repo(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner no-exec\\ntarget_code_execution disabled'); raise SystemExit(0)\n"
                "repo = sys.argv[2]\n"
                "if repo.endswith('/bad'): print('collector failed', file=sys.stderr); raise SystemExit(7)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1]); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'report.json').write_text(json.dumps({'status':'completed','created_at':'2026-08-31T00:00:00Z','repo':{'url':repo,'commit':'good123'},'runner_policy':{'runner':'no-exec','execution_allowed':False},'metrics':{'target_code_execution':False},'verdict':{'decision':'reject'},'canonical_decision':{'decision':'skip','consumption_mode':'none','build_gate':'describe_only'},'action_register':[]}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            rc = module.main([
                "start", "--repo", "https://github.com/example/good", "--repo", "https://github.com/example/bad",
                "--mode", "brief", "--root", str(campaign), "--campaign-id", "partial",
                "--created-at", "2026-08-31T00:00:00Z", "--cli", str(cli),
            ], environ=os.environ.copy())
            self.assertEqual(rc, 0)
            manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual([repo["status"] for repo in manifest["repositories"]], ["collector_done", "failed"])
            self.assertIn("collector failed", manifest["repositories"][1]["error"])

    def test_start_rejects_doctor_without_no_exec_contract(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner local\\ntarget_code_execution enabled')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            with self.assertRaisesRegex(SystemExit, "no-exec safety contract"):
                module.main([
                    "start", "--repo", "https://github.com/example/project",
                    "--mode", "brief", "--root", str(temp / "campaign"),
                    "--cli", str(cli),
                ], environ=os.environ.copy())

    def test_start_resume_rejects_manifest_repo_root_escape(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner no-exec\\ntarget_code_execution disabled')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            init = module._run([
                sys.executable, str(module.INIT_SCRIPT),
                "--repo", "https://github.com/example/project",
                "--mode", "brief", "--root", str(campaign),
                "--campaign-id", "escape", "--created-at", "2026-08-31T00:00:00Z",
            ], environ=os.environ.copy())
            self.assertEqual(init.returncode, 0)
            manifest_path = campaign / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["repositories"][0]["root"] = str(temp / "outside")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "escapes campaign root"):
                module.main([
                    "start", "--repo", "https://github.com/example/project",
                    "--mode", "brief", "--root", str(campaign),
                    "--campaign-id", "escape", "--created-at", "2026-08-31T00:00:00Z",
                    "--resume", "--cli", str(cli),
                ], environ=os.environ.copy())

    def test_start_resume_rejects_changed_repository_set(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner no-exec\\ntarget_code_execution disabled')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            init = module._run([
                sys.executable, str(module.INIT_SCRIPT),
                "--repo", "https://github.com/example/project",
                "--mode", "brief", "--root", str(campaign),
                "--campaign-id", "resume", "--created-at", "2026-08-31T00:00:00Z",
            ], environ=os.environ.copy())
            self.assertEqual(init.returncode, 0)
            lock_path = campaign / "lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["pid"] = 999999
            lock["expires_at"] = "2026-08-30T00:00:00Z"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "does not match requested repositories"):
                module.main([
                    "start", "--repo", "https://github.com/example/other",
                    "--mode", "brief", "--root", str(campaign),
                    "--campaign-id", "resume", "--created-at", "2026-08-31T00:00:00Z",
                    "--resume", "--cli", str(cli),
                ], environ=os.environ.copy())

    def test_start_rejects_unsafe_report_before_collector_done(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner no-exec\\ntarget_code_execution disabled'); raise SystemExit(0)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1]); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'report.json').write_text(json.dumps({'status':'completed','repo':{'url':sys.argv[2]},'runner_policy':{'runner':'local','execution_allowed':True},'metrics':{'target_code_execution':True},'verdict':{'decision':'reject'},'canonical_decision':{'decision':'skip','consumption_mode':'none','build_gate':'describe_only'},'action_register':[]}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            rc = module.main([
                "start", "--repo", "https://github.com/example/project",
                "--mode", "brief", "--root", str(campaign),
                "--campaign-id", "unsafe", "--created-at", "2026-08-31T00:00:00Z",
                "--cli", str(cli),
            ], environ=os.environ.copy())
            self.assertEqual(rc, 1)
            manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["repositories"][0]["status"], "failed")
            self.assertIn("no-exec", manifest["repositories"][0]["error"])

    def test_start_marks_non_object_report_as_failed(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            cli = temp / "repo-scout"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "if sys.argv[1:] == ['doctor']:\n"
                " print('default_runner no-exec\\ntarget_code_execution disabled'); raise SystemExit(0)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1]); out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'report.json').write_text('[]', encoding='utf-8')\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            campaign = temp / "campaign"
            rc = module.main([
                "start", "--repo", "https://github.com/example/project",
                "--mode", "brief", "--root", str(campaign),
                "--campaign-id", "wrong-type", "--created-at", "2026-08-31T00:00:00Z",
                "--cli", str(cli),
            ], environ=os.environ.copy())
            self.assertEqual(rc, 1)
            manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["repositories"][0]["status"], "failed")
            self.assertIn("JSON object", manifest["repositories"][0]["error"])

    def test_hash_refresh_skips_symlink_targets_outside_campaign(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "campaign"
            root.mkdir()
            outside = Path(td) / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            (root / "escape.txt").symlink_to(outside)
            manifest = {"hashes": {}}
            module._refresh_declared_hashes(root, manifest)
            self.assertNotIn("escape.txt", manifest["hashes"])

    def test_finalize_requires_synthesis_then_marks_campaign_verified(self):
        module = load_module()
        verifier = module._load_script_module(module.VERIFY_SCRIPT, "repo_scout_skill_verify")
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            init = module._run([
                sys.executable, str(module.INIT_SCRIPT),
                "--repo", "https://github.com/example/project",
                "--mode", "brief", "--root", str(temp / "campaign"),
                "--campaign-id", "finalize", "--created-at", "2026-08-31T00:00:00Z",
            ], environ=os.environ.copy())
            self.assertEqual(init.returncode, 0)
            root = temp / "campaign"
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            repo = manifest["repositories"][0]
            repo_root = Path(repo["root"])
            report_path = repo_root / "collector" / "report" / "report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "status": "completed",
                "repo": {"url": repo["repo_url"], "commit": "abc123"},
                "runner_policy": {"runner": "no-exec", "execution_allowed": False},
                "metrics": {"target_code_execution": False},
                "verdict": {"decision": "reject"},
                "evidence": [{"id": "E1", "type": "file", "summary": "fixture evidence"}],
                "canonical_decision": {"decision": "skip", "consumption_mode": "none", "build_gate": "describe_only"},
                "action_register": [],
            }), encoding="utf-8")
            repo["status"] = "collector_done"
            repo["report_path"] = str(report_path)
            repo["freshness"] = {"inspected_at": "2026-08-31T00:00:00Z", "inspected_commit": "abc123", "prior_campaign": None}
            repo["retention"] = {"snapshot_retained": False, "cleanup_status": "transient_snapshot_removed"}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            lane_path = repo_root / "lanes" / "controller.json"
            lane = json.loads(lane_path.read_text(encoding="utf-8"))
            lane.update({
                "status": "pass",
                "evidence_refs": ["E1"],
                "gaps": [],
                "forbidden_actions_observed": ["no target repo code execution; no target commands run"],
                "findings": {"fact": ["fixture"], "upstream_claim": [], "independent_evidence": ["fixture"], "inference": [], "unknown": []},
            })
            lane_path.write_text(json.dumps(lane), encoding="utf-8")
            sidecar_path = repo_root / "repo-scout-sidecar.json"
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            sidecar["lanes"][0]["status"] = "pass"
            sidecar.update({"decision": "skip", "consumption_mode": "none", "build_gate": "describe_only", "collector_verdict": "reject"})
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

            before = module.main(["finalize", str(root)], environ=os.environ.copy())
            self.assertEqual(before, 1)

            (root / "synthesis").mkdir(exist_ok=True)
            (root / "synthesis" / "brief.md").write_text("# Brief\n", encoding="utf-8")
            (root / "synthesis" / "action-register.json").write_text("[]\n", encoding="utf-8")
            rc = module.main(["finalize", str(root)], environ=os.environ.copy())
            self.assertEqual(rc, 0)
            final = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "verified")
            self.assertEqual(final["repositories"][0]["status"], "verified")
            self.assertTrue((root / "synthesis" / "verification.txt").exists())

    def test_finalize_preserves_failed_repo_in_partial_campaign(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "campaign"
            init = module._run([
                sys.executable, str(module.INIT_SCRIPT),
                "--repo", "https://github.com/example/good",
                "--repo", "https://github.com/example/bad",
                "--mode", "brief", "--root", str(root),
                "--campaign-id", "partial-finalize", "--created-at", "2026-08-31T00:00:00Z",
            ], environ=os.environ.copy())
            self.assertEqual(init.returncode, 0)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            good, bad = manifest["repositories"]
            good_root = Path(good["root"])
            report_path = good_root / "collector" / "report" / "report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "status": "completed", "repo": {"url": good["repo_url"], "commit": "abc123"},
                "runner_policy": {"runner": "no-exec", "execution_allowed": False}, "metrics": {"target_code_execution": False},
                "verdict": {"decision": "reject"}, "evidence": [{"id": "E1", "type": "file", "summary": "fixture evidence"}], "canonical_decision": {"decision": "skip", "consumption_mode": "none", "build_gate": "describe_only"}, "action_register": [],
            }), encoding="utf-8")
            good.update({"status": "collector_done", "report_path": str(report_path), "freshness": {"inspected_at": "2026-08-31T00:00:00Z", "inspected_commit": "abc123", "prior_campaign": None}, "retention": {"snapshot_retained": False, "cleanup_status": "transient_snapshot_removed"}})
            bad.update({"status": "failed", "error": "fixture failure"})
            for repo in (good, bad):
                repo_root = Path(repo["root"])
                lane_path = repo_root / "lanes" / "controller.json"
                lane = json.loads(lane_path.read_text(encoding="utf-8"))
                lane.update({"status": "pass" if repo is good else "fail", "evidence_refs": ["E1"] if repo is good else [], "gaps": [] if repo is good else ["fixture failure"], "forbidden_actions_observed": ["no target repo code execution; no target commands run"], "findings": {"fact": ["fixture"] if repo is good else [], "upstream_claim": [], "independent_evidence": [], "inference": [], "unknown": []}})
                lane_path.write_text(json.dumps(lane), encoding="utf-8")
                side_path = repo_root / "repo-scout-sidecar.json"
                side = json.loads(side_path.read_text(encoding="utf-8")); side["lanes"][0]["status"] = lane["status"]
                if repo is good: side.update({"decision": "skip", "consumption_mode": "none", "build_gate": "describe_only", "collector_verdict": "reject"})
                side_path.write_text(json.dumps(side), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "synthesis").mkdir(exist_ok=True)
            (root / "synthesis" / "brief.md").write_text("# Partial brief\n", encoding="utf-8")
            (root / "synthesis" / "action-register.json").write_text("[]\n", encoding="utf-8")
            rc = module.main(["finalize", str(root)], environ=os.environ.copy())
            self.assertEqual(rc, 0)
            final = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "partial")
            self.assertEqual([repo["status"] for repo in final["repositories"]], ["verified", "failed"])


if __name__ == "__main__":
    unittest.main()

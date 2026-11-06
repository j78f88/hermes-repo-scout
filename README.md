# repo-scout

Static-first repository due diligence without running target code.

`repo-scout` inventories bounded source files, extracts README and manifest claims, records evidence and risks, and produces JSON/Markdown reports. Remote snapshots are removed by default. A report is evidence for a decision, not permission to install, copy, or adopt a repository.

This repository contains two components:

- **Python CLI:** `src/repo_scout/`, with offline tests and fixtures.
- **Hermes Agent skill:** [`skills/repo-scout/`](skills/repo-scout/SKILL.md), with campaign orchestration, evidence lanes, citation verification, and campaign indexing.

## Install

Requires Python 3.10+ and Git for remote intake.

From a local checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
repo-scout doctor
```

The development extra supplies pytest. For CLI use without test dependencies, install with `python -m pip install .` instead.

## Quick start

Offline example using the bundled synthetic repository:

```bash
repo-scout scout tests/fixtures/tiny_repo \
  --workspace .repo-scout \
  --out reports/example \
  --format both

repo-scout goal 'Find reusable architecture patterns and risks' \
  tests/fixtures/tiny_repo \
  --profile examples/profiles/agent-stack.json \
  --out reports/example-goal
```

For an explicitly selected public repository, replace the local path with `https://github.com/OWNER/REPO`. Remote intake contacts GitHub; local fixture examples do not need network access.

Reports include claims, evidence, findings, idea candidates, planned probes with `not_run` results, reusable contracts, an action register, and a describe-before-build gate. The collector's native verdict is kept separate from the canonical decision, consumption mode, and build gate.

## Safety boundary

- Target package managers, tests, build scripts, hooks, README commands, containers, binaries, and imported target code are not run.
- Repository text, including agent instructions and skills, is untrusted data.
- Remote Git intake permits HTTPS and an explicit `file://` fixture transport only. It disables global/system Git configuration, hooks, symlinks and submodules, and starts with no checkout.
- Regular files are checked out only after size checks. Blobs over 2 MB stay unchecked-out; a server that ignores filtering is reported rather than assumed safe.
- Static reads are bounded and reject symlink/path escapes. Secret-like text is redacted from report excerpts; review artifacts before sharing them.
- Snapshots are transient unless `--keep-snapshot` is explicitly selected. Extracted evidence and corpus records persist under the chosen workspace.
- A recommendation does not authorize execution, publication, runtime changes, credential use, or source copying. Owner approval markers do not enable a target-code runner.

See [SECURITY.md](SECURITY.md) and the [security model](docs/SECURITY.md). Static checks are not a security certification or legal opinion.

## Additional commands

```bash
# Plan optional scanner commands; do not execute them.
repo-scout scanners list
repo-scout scanners plan tests/fixtures/tiny_repo --json

# Export selected existing reports for review as a curated Markdown folder.
repo-scout handoff review-board reports/example/report.json \
  --out reports/example/handoff.json
repo-scout export rollup reports/example/report.json \
  --out-dir reports/curated-rollup

# Validate original, synthetic contract fixtures.
repo-scout run-ledger-fixture --out reports/run-ledger
repo-scout repo-fit-validate tests/fixtures/repo_fit/golden-contract-fixtures.json
repo-scout owner-correction-validate tests/fixtures/correction_feedback/owner-corrections.json

# Metadata-only shortlist from a saved Trending page; no automatic scouts.
repo-scout sources trending \
  --fixture tests/fixtures/github_trending_sample.html \
  --out reports/trending.json --markdown reports/trending.md
```

`repo-scout --help` lists campaign, comparison, artifact-quality, and other commands. Scanner execution and automatic target adoption are not supported.

## Profiles

Profiles shape planning, comparison dimensions, evidence needs, and output routes without enabling target execution:

- [`agent-stack.json`](examples/profiles/agent-stack.json): orchestration, interfaces, evidence and safety constraints.
- [`example-packet.json`](examples/profiles/example-packet.json): mapping neutral evidence into a sample packet vocabulary.

Source tiers include `trusted_vendor_or_official`, `known_high_quality`, `ordinary_oss`, `unknown_untrusted`, and `owner_approved_direct_consume`. Build gates include `describe_only`, `peer_review`, `spike_original`, `build_original`, and `blocked_needs_owner`. These describe review/approval state, not executable capabilities.

## Hermes Agent skill

Install the CLI first. Copy the bundled skill into your Hermes skills directory:

```bash
mkdir -p ~/.hermes/skills
cp -R skills/repo-scout ~/.hermes/skills/repo-scout
```

Do not overwrite an existing customized installation without reviewing it. The skill is distributed alongside the CLI, not installed into Hermes by pip.

Start an authorized scout campaign:

```bash
python3 ~/.hermes/skills/repo-scout/scripts/run_repo_scout.py scout \
  --repo https://github.com/OWNER/REPO --mode standard
```

The runner requires a working `repo-scout` executable. Use `--cli` or `REPO_SCOUT_CLI` if it is not on `PATH`. Campaigns need controller synthesis and successful strict verification before they can be called complete.

Portable configuration:

| Variable | Purpose | Default |
|---|---|---|
| `REPO_SCOUT_RUNS_ROOT` | Campaign storage base | `~/.hermes/repo-scout-runs` |
| `REPO_SCOUT_EXCLUDE_PATTERN` | Case-insensitive regex applied to path components | `^excluded(?:[-_].*)?$` |

The index prunes excluded paths and rejects escaping paths; invalid exclusion regexes fail closed. Public enrichment uses read-only sources; the optional skill scanner needs Hermes' own checkers. See the [skill guide](skills/repo-scout/SKILL.md) for prerequisites and controls.

## Development and verification

```bash
pytest -q
for test in skills/repo-scout/scripts/test_*.py; do
  python3 "$test" || exit 1
done
python3 skills/repo-scout/scripts/init_repo_scout_campaign.py --self-test
python3 skills/repo-scout/scripts/verify_repo_scout_campaign.py --self-test
repo-scout --help
repo-scout doctor
repo-scout scanners list
```

Tests use saved data, temporary directories, mocked HTTP responses and local Git remotes, not target install/build/test commands. For isolated test storage and a complete clean-install check, see [docs/BUILD.md](docs/BUILD.md).

## Documentation

- [Process and evidence model](docs/PROCESS.md)
- [Owner-correction feedback loop](docs/OWNER_CORRECTION_FEEDBACK_LOOP.md)
- [Artifact-quality gate](docs/ARTIFACT_QUALITY_GATE.md)
- [Build and verification](docs/BUILD.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).

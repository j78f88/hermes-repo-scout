# Build and verification

Requires Python 3.10+ and Git. All checks below operate on this project, saved fixtures, mocks, and temporary local remotes. They do not run third-party target code.

## Clean installation

From the checkout root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

This installs the local CLI and test dependencies. It does not install the bundled skill into Hermes or change a live agent configuration.

## Complete offline verification

Use isolated runtime storage so tests cannot write to your real home or report storage:

```bash
mkdir -p .release-checks/home .release-checks/tmp
export HERMES_AGENT_DIR="${HERMES_AGENT_DIR:-/path/to/hermes-agent}"
export HOME="$PWD/.release-checks/home"
export TMPDIR="$PWD/.release-checks/tmp"
export PYTHONDONTWRITEBYTECODE=1
unset REPO_SCOUT_RUNS_ROOT REPO_SCOUT_CAMPAIGN_HOME
unset REPO_SCOUT_EXCLUDE_PATTERN REPO_SCOUT_INDEX
pytest -q
for test in skills/repo-scout/scripts/test_*.py; do
  python "$test" || exit 1
done
python skills/repo-scout/scripts/init_repo_scout_campaign.py --self-test
python skills/repo-scout/scripts/verify_repo_scout_campaign.py --self-test
repo-scout --help
repo-scout doctor
repo-scout scanners list
repo-scout goal 'Validate whether the README quickstart is real'   tests/fixtures/tiny_repo --dry-run-plan
```

Run these environment changes in a disposable shell. `HOME` and `TMPDIR` affect subsequent commands in that shell. Workspaces and generated test artifacts are ignored by Git.

The root pytest suite covers the CLI. Skill scripts use standalone unittest entrypoints and require separate execution; root `testpaths` intentionally does not discover them. Both campaign helper self-tests must also pass. One skill integration test imports Hermes' real static checkers: it skips when `HERMES_AGENT_DIR` does not point to a compatible Hermes source installation. For a zero-skip release check, provide that installation; the remaining checker tests use injected mock implementations.

## Package build

```bash
python -m pip wheel --no-deps . --wheel-dir dist
```

The wheel contains the CLI. The Hermes skill is a separate directory in the source checkout; see the root README for its installation.

## Intake and retention

Remote intake uses a shallow, tagless, single-branch, submodule-disabled Git clone with `--no-checkout` and a 2 MB blob filter. System/global Git configuration, hooks, symlinks, and filesystem monitors are disabled. Present regular blobs are size-checked before selective checkout. A server that ignores the filter is reported, and oversized blobs are still not checked out.

Default workspaces are `.repo-scout` under the current repository root or working directory. Snapshots are transient; extracted reports and corpus records persist. `--keep-snapshot` requires explicit selection. Target package managers, tests, builds, hooks, commands and imports are never part of verification or scouting.

## CI

GitHub Actions installs the package, runs the CLI suite, every skill test and both self-tests, then smoke-checks the CLI on Python 3.10, 3.11, 3.12, and 3.13. A workflow file is not evidence of a completed hosted CI run; local staging verification is separate.

# Portable development checks

## Configuration contract

- `REPO_SCOUT_RUNS_ROOT` selects the campaign base; default `~/.hermes/repo-scout-runs`. The legacy `REPO_SCOUT_CAMPAIGN_HOME` override remains supported.
- `REPO_SCOUT_EXCLUDE_PATTERN` is a case-insensitive regex checked against path components. Neutral default: `^excluded(?:[-_].*)?$`. Invalid patterns must fail closed before traversal.
- Keep path policy in `scripts/scout_paths.py` shared by indexing and the runner. Test both lexical paths and symlink-resolved destinations so an alias cannot bypass exclusions.

## Test isolation

Run every `scripts/test_*.py` plus the initializer and verifier `--self-test` commands. Give each invocation an isolated `HOME`, `TMPDIR`, runs root and cache under ignored `.test-output/`; clear inherited configuration overrides when testing defaults. Do not depend on an installed collector to test argument validation. Validate missing required arguments before resolving or invoking a collector.

Fixture collectors are trusted test doubles. Local Git fixtures are generated inside the temporary test workspace; they are not remote targets. The suite must not install, build, import or execute code from an actual target repository. Public provenance tests use injected response fixtures rather than network calls.

The optional checker round-trip uses trusted Hermes modules from `HERMES_AGENT_DIR`. Without those modules it may explicitly skip; report any skip rather than treating it as exercised integration coverage.

## Evidence and exclusion regressions

Keep negative checks for missing no-exec declarations, unsafe collector policies, unresolved citations, altered hashes, path escapes, oversized blobs and symlinks. Configurable exclusions replace deployment-specific names, not containment guards. Test pruning before directory listing, excluded base refusal, invalid regex refusal and symlinked evidence rejection. Preserve original evidence hashes when repairing formatting or taking sidecar snapshots.

Keep optional reader summaries under `synthesis/` so recursive C1 validation covers them without a separate publication destination. When removing an optional integration, retain shared storage and containment regressions rather than deleting mixed-purpose test files.

For a release scrub, inspect hidden files and generated caches too. Keep raw logs ignored and remove generated workspace artifacts before packaging. A staged copy may not yet be a Git worktree; leave repository initialization, CI wiring and commits to the release controller.

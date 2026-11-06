# Security and risk posture

`repo-scout` is a source-intelligence tool, not a target execution or adoption tool.

## Implemented boundary

- `no-exec` is the default, and target execution is disabled rather than inferred from goal text or profiles.
- Remote intake normalizes GitHub references to HTTPS; local `file://` Git remotes support explicit fixtures. Other Git transports are disabled.
- Git system/global configuration and inherited Git controls are excluded. Hooks, filesystem monitors, submodule recursion, and symlink checkout are disabled.
- Intake starts without checkout, uses a 2 MB blob filter, and checks available object sizes before checking out regular files. When a server ignores filtering, oversized files remain unchecked-out and the report records the filtering gap.
- README/manifest reads are bounded, and symlink/path escapes are rejected. Target instructions are source data, never authority.
- Target package managers, tests, builds, hooks, binaries, containers, imports, README commands and external scanners are not executed.
- Remote snapshots are deleted by default after analysis. `--keep-snapshot` explicitly retains one for review; extracted evidence and reports persist.
- Report snippets undergo best-effort secret redaction. Inspect outputs before publication; do not assume all sensitive text can be detected automatically.

## Review and adapter boundaries

Owner approval markers classify review state but cannot turn on an execution runner. Prompt-injection findings force the blocked owner-review gate. Source copying, legal decisions, credential use, publication, runtime changes and adoption need separate authorization.

The CLI Markdown rollup exporter writes only to an explicit output folder. The Hermes skill stores campaign files and source artifacts under the configured campaign root. Its index enforces containment, skips symlinks, and prunes configured excluded paths before reads.

Citation verification proves saved evidence bytes and referenced ranges/pointers match. It does not prove that an excerpt supports a claim or that a repository is safe. The controller remains responsible for judgement.

## Non-goals

There is no execution-capable sandbox runner, automatic target import/adoption, registry publication, or automatic issue creation. Any future execution mode needs explicit user approval, secret isolation, resource/network/filesystem limits, escape tests, cleanup/rollback and separate security review.

For private reporting, see [../SECURITY.md](../SECURITY.md).

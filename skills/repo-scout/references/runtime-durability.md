# Runtime durability

## Campaign ownership

Each campaign records:

- `campaign_id`, `created_at`, `owner_issue`, `mode`, `status`;
- `lock.owner`, `lock.created_at`, and `lock.expires_at` when active;
- `freshness.inspected_at`, `freshness.inspected_commit`, and prior run pointer for refreshes;
- per-repo status and partial-success state.

## Restart and resume

Writes should be atomic: write `*.tmp`, then rename. Re-running the initializer with the same campaign id must not overwrite existing lane outputs unless takeover is valid. Use `--resume` to read an existing `manifest.json` without mutation. An active lock with a live pid and unexpired `lock.expires_at` must block initialization unless `--force` is passed. Expired, stale, or missing locks may be taken over, and the takeover must be recorded as a checkpoint. The verifier must be safe to run repeatedly and must use resolved `relative_to` containment checks, not string prefix checks.

## Durability

Local campaign roots are staging unless a durable destination is named and hashes verify. Do not claim durable storage from a successful write alone; read back paths and hashes.

## Freshness

Record the time and commit/ref inspected. A refresh can return `refresh_no_material_delta` only after comparing prior commit/release/license/activity against current evidence. Run `run_repo_scout.py refresh-check --repo URL` first. It compares the latest indexed scout with live GitHub head, licence, archive flag and latest release.

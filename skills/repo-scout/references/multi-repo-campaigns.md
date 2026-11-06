# Multi-repo campaigns

Multi-repo work uses one campaign root with isolated per-repo roots.

```text
<campaign-root>/
  manifest.json                 # repo-scout.campaign.v1 top-level campaign manifest
  lock.json                     # active lock sidecar, mirrored from manifest.lock
  citation-ledger.json
  repos/<owner_repo>/
    repo-scout-sidecar.json     # per-repo sidecar with lanes and canonical decision fields
    collector/
    lanes/<lane>.json
    receipts/worker-packet-<lane>.md
    synthesis/
```

Defaults:

- concurrency: `3`;
- failure policy: `partial_success_allowed`;
- no shared clone directories;
- shared citation ledger, per-repo evidence artifacts;
- comparative synthesis reads per-repo outputs rather than merging raw evidence.

A failed repo must not hide successful repos. Report status per repository: `initialized`, `collector_done`, `lanes_done`, `synthesized`, `verified`, `blocked`, or `failed`.

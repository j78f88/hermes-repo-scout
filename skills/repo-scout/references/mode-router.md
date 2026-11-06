# Repo Scout mode router

Choose one mode before dispatch and record the reason in `manifest.json`.

## Deterministic routing

1. `brief`: request says quick, brief, skim, lightweight, sanity check, or “just tell me if it is worth attention.” Use one controller/static lane plus the CLI collector.
2. `standard`: default for an unqualified repo-scout. Use 2–4 adaptive lanes; start with provenance, static architecture, risks/licensing, and fit/verdict unless the repo type suggests a smaller set.
3. `full`: request says full/deep repo scout, the recommendation may trigger build spend/risk, licensing/security is ambiguous, or multiple owner systems may consume it. Use 5–7 lanes.
4. `refresh_delta`: a prior scout exists and the request is to update/reassess. Inspect only material deltas: commit/release/license/security/activity/new claims plus changed recommendation.
5. `true_deep_research`: only when `deep-research` `references/depth-gate.md` passes. Hand off to `deep-research`; do not call a normal full repo scout “deep research.”

## Lane counts

- Brief: 1 lane.
- Standard: 2–4 lanes, adaptive default 4.
- Full: 5–7 lanes, adaptive default 6.
- Refresh delta: 2–3 lanes, adaptive default 3.
- True deep research: repo-scout contributes a no-exec repo evidence lane to the broader research campaign.

## Required baseline

Every mode runs or records the intended local baseline:

```bash
repo-scout doctor
repo-scout scout <url> --workspace <repo-root>/collector --out <repo-root>/collector/report --format both
```

`scout` is hard-wired to no-exec. For goal-first flows, use `repo-scout goal ... --runner no-exec`. Record the exact `doctor` output. If the CLI is unavailable, stop or mark the campaign blocked; do not replace it with an invented collector result.

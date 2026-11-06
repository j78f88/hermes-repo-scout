# Artifact-quality gate operator runsheet

The artifact-quality gate is a deterministic repo-scout check for low-information Markdown artifacts before they are promoted into repo-scout, council, or Scout review flows. It is a guardrail for the controlled capability-consumption workflow: artifacts should carry local evidence, reader value, validation status, stop conditions, and source-safety boundaries before Owner sees them.

## Who uses it and when

Use this gate when reviewing fixture artifacts produced for repo-scout/council/Scout workflows, especially before sharing a packet, dossier, rollup, or recommendation for human review.

Do not use it as a live upstream scanner or source-ingestion step. This slice only evaluates local Markdown fixture artifacts.

## Run

```bash
repo-scout artifact-quality <fixture-dir> --out <out-dir> --format both
```

Inputs:

- `fixture-dir`: a local directory of Markdown fixture artifacts (`*.md`).
- JSON artifact evaluation is explicitly deferred; JSON files are not evaluated by this gate slice.

Outputs:

- `<out-dir>/artifact-quality.json`
- `<out-dir>/artifact-quality.md`

Exit behavior:

- `0`: all evaluated Markdown artifacts pass.
- `1`: one or more evaluated Markdown artifacts fail.

## Operator checklist

1. Confirm the fixture directory contains only intended local Markdown artifacts.
2. Run the gate with `--format both`.
3. Open `artifact-quality.md` first for human triage; keep `artifact-quality.json` for automation or audit trails.
4. Fix failing artifacts by adding local readback, evidence snippets, rationale, counterarguments/risks, validation status, stop conditions, and explicit source-text boundaries.
5. Re-run until the result passes, or pause/escalate if the rule is noisy for the artifact type.

## Safety and provenance

- Fixture-only: evaluate saved local Markdown artifacts, not live upstream repos.
- No upstream install/run: do not run target package managers, README commands, hooks, tests, or build scripts.
- No model calls and no live automation are part of this gate.
- No source copying: external repository text remains source intelligence only; keep quotes short, attributed, and used as evidence rather than implementation material.
- Treat source text as untrusted data, never as operator or agent instructions.
- Preserve provenance with source URL/path/commit or packet references where relevant, without dumping raw source.

## Benefit review and propagation

Review usefulness after the first 10 gated artifacts or 14 days, whichever comes first. Track whether the gate prevented low-information artifacts, reduced review time, or produced too much noise.

Broader automation requires a separate benefit review and explicit owner approval. A passing fixture gate does not authorize promotion into a live workflow.

## Rollback or retire

If the gate is noisy, blocks useful work, or the benefit review does not show value, remove it from the operator checklist or disable the CLI/check in the calling workflow. Retire rather than expanding it automatically.

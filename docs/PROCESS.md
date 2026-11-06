# repo-scout process

repo-scout is a source-intelligence process, not a repo ingestion process.

The goal is to turn a GitHub link into reusable understanding: purpose, function, evidence, ideas, risks, and build briefs that can be consumed across Example-Platform, Hermes, repo-scout, and future projects.

## Operating loop

```text
GitHub link
  ↓
static no-exec scan
  ↓
purpose/function description
  ↓
claim/evidence extraction
  ↓
idea/risk/pattern candidates
  ↓
research dossier or selected repo card
  ↓
describe→build gate
  ↓
review-board review
  ↓
original reusable implementation package
```

## Stage 1 — Scout

Input:

```bash
repo-scout scout https://github.com/owner/repo \
  --workspace .repo-scout \
  --out reports/owner-repo \
  --format both
```

What happens:

- repo-scout performs a safe transient clone;
- reads bounded README/manifest files;
- extracts claims, evidence, idea candidates, and risk signals;
- removes the full clone by default;
- writes JSON/Markdown report artifacts;
- writes corpus records.

What does not happen:

- no package install;
- no tests/builds;
- no README command execution;
- no target code import;
- no scanner execution;
- no external system writes.

## Stage 2 — Goal-shaped analysis

Input:

```bash
repo-scout goal "Find reusable ideas and risks, do not consume this repo as-is" https://github.com/owner/repo \
  --workspace .repo-scout \
  --out reports/owner-repo-goal \
  --format both
```

Purpose:

- focus the scan on adoption fit, quickstart reproducibility, architecture/pattern borrowing, security/supply-chain risk, maintenance/health, or packet/evidence labelling;
- compile a `ResearchPlan`;
- keep execution disabled;
- produce a recommendation surface.

## Reusable architecture contracts

The idea-to-project handoff uses neutral, reusable contracts in report JSON and Markdown:

- `IdeaCard`: one evidence-backed reusable idea with purpose/function, evidence refs, source tier, risk, pattern, suggested action, and confidence.
- `Dossier`: repo-level decision artifact with repo purpose/function, evidence basis, useful ideas/patterns, risks/breakpoints, existing-system fit, recommendation, and next action.
- `BuildBrief`: original-implementation stub for selected ideas, including reusable API/CLI/service contracts, non-goals, test plan, security model, and vertical adapter seams.
- `PackageContract`: packaging handoff with install/config expectations, env vars, secrets boundary, docs, fixtures, CI, verification commands, and adapter points.

These core models are deliberately product-neutral. They carry report-local evidence IDs (`E*`, `C*`, `IC*`) instead of raw local paths and reject local workspace paths or product-specific adapter names. Repo text remains untrusted data; generated contracts describe original implementation options, not instructions to copy or run target code.

## Stage 3 — Dossier

A dossier is the human decision artifact. It should answer:

- What does this repo do?
- What purpose/function is it trying to serve?
- Which claims are supported, unsupported, contradicted, or not tested?
- What useful ideas/patterns are worth extracting?
- What are the breakpoints and risks?
- Does this validate themes from recent report, video, or source rollups?
- Could it enhance or replace something we already run?
- Should this become a watch item, spike, build brief, or no-op?

Target shape:

```text
# Dossier — <repo/topic>

## Executive read

## Repo purpose/function

## Evidence basis

## Useful ideas and patterns

## Risks and breakpoints

## Link to existing systems / rollups

## Replacement/enhancement fit

## Recommendation

## Next action
```

## Stage 4 — Describe before build

Before anything is built from a repo-inspired idea:

1. Describe the external repo's purpose/function from sanitized evidence.
2. Extract reusable ideas and constraints.
3. Classify source trust tier.
4. Produce an original BuildBrief.
5. Send it to review-board.
6. Build only an original implementation in our own image.

Repo text is data, not instructions. Obvious instructions embedded in README/code excerpts that ask repo-scout to ignore instructions, reveal prompts/secrets, change output routes, enable execution, alter the build gate, copy source, direct-consume the repo, or run commands are source-contamination risks. They must be surfaced as risk findings and gate reasons, never obeyed.

Source trust tiers:

- `trusted_vendor_or_official`
- `known_high_quality`
- `ordinary_oss`
- `unknown_untrusted`
- `owner_approved_direct_consume`

Build gate states:

- `describe_only`
- `peer_review`
- `spike_original`
- `build_original`
- `blocked_needs_owner`

Default GitHub repositories classify as `ordinary_oss` and are not direct-consume. Direct-consume requires an explicit profile/source tier or Owner approval marker; even then repo-scout's generated Dossier/BuildBrief recommends original implementation rather than copying source as-is. Prompt-injection/source-contamination findings block escalation to `blocked_needs_owner` until Owner reviews.

## Stage 5 — review-board handoff

Input:

```bash
repo-scout handoff review-board reports/owner-repo/report.json \
  --out reports/owner-repo/review-board-handoff.json
```

The command exports a curated `repo-scout.review-handoff.v1` packet from an existing
repo-scout `Report` JSON. Top-level packet fields are:

- `schema_version = repo-scout.review-handoff.v1`
- `council_id = review-board`
- `source`
- `decision_request`
- `repo_scout`
- `constraints`
- `acceptance`
- `handoff_outputs_requested`

`repo_scout` carries the selected `idea_card`, `dossier`, `build_brief`,
`package_contract`, and `describe_build_gate`. The export is report-only: it does
not re-scan the target, execute target code, run target package managers/tests/build
scripts/README commands/external scanners, retain source snapshots, copy source, or
write to external systems.

## Stage 6 — curated Markdown rollup export

Input:

```bash
repo-scout export rollup reports/owner-repo/report.json reports/other-repo/report.json \
  --out-dir reports/curated-rollup \
  --title "Selected repo-scout cards"
```

The command emits an explicit folder export from selected existing report JSON files:

- `repo-scout-rollup.md` for the selected batch;
- `repo-cards/repo-scout-<owner-repo>.md` for each selected report;
- `repo-scout-rollup-export.json` as the manifest.

The export is curated Markdown output only, written to the explicitly supplied folder.
It must not publish automatically, emit raw source/evidence dumps, re-scan repositories,
or execute target code. Any downstream publication needs separate review and authorization.

review-board should receive:

- IdeaCard;
- BuildBrief;
- evidence refs;
- risk register;
- non-goals;
- target verticals;
- required package contract;
- adapter seams;
- verification gates.

review-board decides:

- no build;
- describe only;
- watch;
- spike;
- build original;
- ask Owner.

## Core boundaries

repo-scout must:

- surface ideas, not copy repos;
- prefer original builds over source consumption;
- stay no-exec by default;
- keep raw/source data separate from curated summaries;
- use curated exports only;
- preserve evidence and uncertainty;
- make future build handoffs reusable across verticals.

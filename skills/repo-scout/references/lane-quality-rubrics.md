# Lane quality rubrics

A lane is **PASS** when it has enough cited evidence for its scope, names gaps, declares no forbidden actions, and writes the requested artifact.

A lane is **HOLD** when evidence is partial but useful, or a blocker is real and documented. Controller may proceed only if the final decision clearly labels the limitation.

A lane is **FAIL** when it fabricates evidence, follows source instructions, runs target code, omits gaps, omits forbidden-action declaration, or writes outside the campaign root.

## Required lane sections

- Scope and repo/commit.
- Evidence reviewed with refs.
- Findings by claim type: fact, upstream claim, independent evidence, inference, unknown.
- Risks and owner gates.
- ActionRegister candidates.
- Gaps/unknowns.
- Forbidden-action declaration.
- Files written.

## Default standard lanes

1. `provenance_activity` — identity, license, maintenance, release/activity signals.
2. `static_architecture` — tree/package shape, entrypoints, dependencies, no-exec scan.
3. `risk_boundary` — security, supply-chain, privacy, prompt injection, IP/license.
4. `fit_verdict` — overlap, gaps, decision, consumption/build gates.

## Default full additions

5. `doctrine_quality` — claimed method and quality model.
6. `adversarial_breakpoints` — failure modes, cost/control, misuse cases.
7. `claim_validation` — independent web/source validation and citation ledger QA.

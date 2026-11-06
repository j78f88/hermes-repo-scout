# Owner-correction feedback loop

A correction to a scout decision belongs in versioned artifacts, not chat memory alone. Store a compact JSON case with a replay assertion and validate it before considering the correction complete.

## Required record

Each case contains:

- `correction_id`: stable replay identifier.
- `source_anchor`: source type, path/URL, locator, capture time and trust/retention context.
- `original_output_ref`: the report or fixture that was incomplete or wrong.
- `owner_correction`: the owner's correction in plain language.
- `durable_change_classes`: registry, fixture, query, output-contract or process changes required.
- `required_durable_artifacts`: an artifact and verification anchor for each class.
- `replay_case`: before/after outcome and an explicit future assertion.
- `non_memory_persistence.chat_memory_required = false`: the correction is independently replayable.
- `safety_boundaries`: no target execution, external issue mutation or retained upstream source.

## Workflow

1. Add a synthetic case to `tests/fixtures/correction_feedback/owner-corrections.json`, or add a separate fixture.
2. Identify each required durable change: registry update, golden fixture, backlog-search query, output contract, or skill/process rule.
3. Record where the fix lives and how to verify it. A claimed artifact without an anchor is not sufficient.
4. Add a regression assertion that distinguishes the original wrong result from the corrected result.
5. Run:

```bash
repo-scout owner-correction-validate   tests/fixtures/correction_feedback/owner-corrections.json   --out reports/correction-feedback/validation.json
pytest -q tests/test_correction_feedback.py tests/test_repo_fit_validation.py
```

The checked-in examples are synthetic replay cases: a runnable toy repository must not become a system-fit candidate solely because it runs, and an already-curated candidate must route to its existing evidence and collision check rather than produce duplicate work.

Generated reports should link correction replay identifiers and durable fixture references. They must not rely on conversation recall, treat a correction as implementation permission, or run upstream code to validate a route.

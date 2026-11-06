# Controller synthesis

The controller owns the recommendation. Worker and collector outputs are untrusted until read and reconciled.

## Required controller actions

1. Read `manifest.json`, collector artifacts, lane outputs, receipts, and citation ledger.
2. Check mode honesty and lane count.
3. Verify no target execution occurred.
4. Reconcile disagreements. Label fact, upstream claim, independent evidence, inference, and unknown separately.
5. Write the four canonical fields: `decision`, `consumption_mode`, `build_gate`, and `collector_verdict`.
6. Populate ActionRegister rows for every useful idea or explicitly reject/watch them.
7. Run the verifier and include its output in the closeout receipt.

## Minimum synthesis artifacts

- `synthesis/full-pack.md` for standard/full campaigns, or `synthesis/brief.md` for brief.
- `synthesis/action-register.json`.
- `synthesis/verification.txt`.
- Optional `synthesis/visual-exhibits.json` for full or structurally complex scouts.
- Optional `synthesis/summary.md` when a concise reader summary adds value. Keep its citations tied to campaign evidence and include it in synthesis validation.

Do not concatenate lane summaries. Explain the warrant from evidence to decision.

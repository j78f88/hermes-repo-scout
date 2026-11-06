# Verdict mapping

Keep these fields separate in manifests, lane outputs, and synthesis.

## `decision`

Human recommendation. The canonical application values are:

- `adopt`
- `pilot`
- `watch`
- `skip`
- `route`
- `block`

Legacy labels such as `DISCARD`, `PATTERN-ONLY`, `TRANSLATION-CANDIDATE`, `SANDBOX-TRIAL`, `FORK-CANDIDATE`, `INTEGRATE-CANDIDATE`, `refresh_no_material_delta`, and `blocked_needs_owner` are collector inputs mapped deterministically into this field plus the two fields below.

## `consumption_mode`

What can be consumed:

- `none`
- `pattern`
- `translation`
- `sandbox`
- `fork`
- `integration`

## `build_gate`

Whether implementation may proceed:

- `describe_only`
- `peer_review`
- `spike_original`
- `build_original`
- `blocked_needs_owner`

## `collector_verdict`

The local CLI collector status and finding summary. This is evidence, not the controller decision.

## Mapping rule

A positive decision does not authorize execution. An interesting repo can still be `pattern_only` with `describe_only`; a low-risk repo can be `sandbox_trial` but only after a separate sandbox plan is accepted.

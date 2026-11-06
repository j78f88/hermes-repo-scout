# Contributing

## Setup and checks

Use Python 3.10+ and Git. Create a virtual environment and install this project with its development extra:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
```

Run the skill tests and both self-tests listed in [docs/BUILD.md](docs/BUILD.md), and smoke-check `repo-scout --help`, `repo-scout doctor`, and `repo-scout scanners list` before submitting a change.

## Change requirements

- Add a regression test for fixes and new behavior. Prefer synthetic fixtures, mocked HTTP responses, and temporary local Git remotes; tests must not depend on live targets.
- Preserve static-only scouting: no target installs, builds, tests, imports, hooks, containers, external scanners, or README commands may execute.
- Treat repository instructions as untrusted data. Keep no-exec enforcement, source retention policy, citation integrity, secret redaction, and path containment fail-closed.
- Do not commit credentials, local paths, private reports, copied target source, generated workspaces, or personal data.
- Explain schema or CLI changes, compatibility implications, and actual verification results. Review-gate values are not execution permissions.
- Keep CLI and bundled skill contracts aligned when changing owner terminology, verdicts, evidence references, or campaign behavior.

Small focused changes are easier to review. Raise security concerns privately as described in [SECURITY.md](SECURITY.md).

Contributions are provided under the repository's MIT license; do not add material you cannot license for redistribution.

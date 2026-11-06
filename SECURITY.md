# Security policy

## Scope

The security-sensitive boundaries are static-only target intake, Git isolation, bounded file reads, path/symlink containment, source-as-data handling, evidence integrity, secret redaction, and transient snapshot cleanup. The bundled Hermes skill adds campaign, citation, index, and explicit-output publication boundaries.

Reports are not proof that a target repository is safe. Redaction is best-effort, and private repository contents or credentials should not be used in public examples or reports.

## Reporting a vulnerability

Do not include exploit details, secrets, private reports, or sensitive paths in a public issue. Use the hosting provider's private vulnerability-reporting feature if the eventual public host enables it. If no private channel is available, ask a maintainer for a secure contact method without disclosing technical details publicly.

Include the affected version/commit, expected boundary, a minimal synthetic reproduction, impact, and any proposed fix. Do not execute an untrusted repository to demonstrate an issue.

## Supported changes

Security fixes are reviewed against the current source tree. There is no promise of long-term support for earlier versions or a guaranteed response time. Any execution-capable mode would need separate design, explicit authorization, isolation and security review; it is not part of the current collector.

See [docs/SECURITY.md](docs/SECURITY.md) for the implemented safety model.

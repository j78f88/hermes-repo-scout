# Repo type router

Adapt standard/full lanes to the repository, but keep the CLI collector and no-exec boundary.

| Repo type | Add or emphasize | Typical lane names |
|---|---|---|
| Library/package | API surface, package metadata, license, dependency risk | provenance, static_architecture, security_license, fit_verdict |
| CLI/tool | install side effects, filesystem/network/provider writes, sandbox gate | cli_surface, side_effects, safe_trial_gate |
| Agent/skill/prompt repo | prompt injection, behavioral instructions, source-text-as-data; read `evidence/skill-scan/skill-scan.json` for Hermes guard findings, shipped scripts and closest local skills | prompt_boundary, doctrine_claims, original_translation |
| Infra/devops | deploy/runtime assumptions, secrets, network exposure, rollback | operations_surface, supply_chain, owner_gates |
| Dataset/benchmark | data provenance, license, evaluation leakage, reproducibility | data_provenance, benchmark_validity, usage_boundary |
| UI/design | assets/fonts/licenses, build toolchain, portable patterns | design_patterns, asset_rights, implementation_fit |
| Multi-repo family | per-repo isolated roots plus comparative synthesis | per_repo_static, comparison, portfolio_verdict |

Do not run target commands to classify type. Use repository metadata, static files, and read-only web/GitHub evidence.

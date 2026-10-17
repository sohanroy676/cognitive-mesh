# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.2.0 is merged, tagged and CI-green. v0.3.0 Dependency Graph is implemented, verified locally, and (with user approval, 2026-10-17) merged to main and pushed; not marked done until CI on main is green. v0.3.1 (JS/TS + dynamic imports) is planned only.

## Recent decisions
- ADR-0006: edge `A -> B` = A depends on B; every edge has evidence; unresolved/ambiguous things are `unresolved` nodes + diagnostics, imports never linked to packages by name; lockfiles stay excluded, so transitive external deps are reported unknown; NetworkX private behind pydantic models.
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- v0.2.0 added no dependencies (YAML is `unvalidated`, not parsed); v0.3.0 added NetworkX.
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Mark v0.3.0 done only after CI on main passes. Pushes and merges always need explicit user approval.
- Dependabot PRs #1, #3, #4: review and merge after v0.3.0 (with approval).
- Local branches are never deleted by the agent (rule in CLAUDE.md); old branches are kept.
- Build/packaging command is untested (TBD).

## Next step
1. Confirm CI on main is green for the v0.3.0 merge, then mark v0.3.0 done in ROADMAP.md/progress.md/README.md (docs branch, merge with approval).
2. Start v0.3.1 (JS/TS + dynamic imports) via `/new-feature`; decide the parser approach in an ADR first.
3. Handle Dependabot PRs #1, #3, #4 (with approval), then plan v0.4.0 Agent Architecture. Lockfile ingestion (for transitive dependencies) was deliberately deferred in ADR-0006.

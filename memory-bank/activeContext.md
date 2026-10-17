# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.3.0 is tagged. v0.3.1 (JS/TS + dynamic imports) is merged to main and pushed (`0d16c4a`), CI green (run 39012713238), not tagged. Dependabot PRs #1, #3, #4 are merged; no PRs are open. v0.4.0 Agent Architecture is next, planned only.

## Recent decisions
- ADR-0007 (accepted by the user): tree-sitter for JS/TS, private to `graph/js_parse.py`; syntax-node matching only; computed dynamic imports are `unresolved:dynamic:*` + `computed_import`; bare/aliased JS specifiers stay unresolved; no `.js`->`.ts` remap.
- ADR-0006: edge `A -> B` = A depends on B; every edge has evidence; unresolved/ambiguous things are `unresolved` nodes + diagnostics, imports never linked to packages by name; lockfiles stay excluded, so transitive external deps are reported unknown; NetworkX private behind pydantic models.
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- v0.2.0 added no dependencies (YAML is `unvalidated`, not parsed); v0.3.0 added NetworkX.
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Pushes and merges always need explicit user approval. Tagging v0.3.1 needs approval.
- gitleaks-action@v3 was merged but never exercised in CI (step is gated on `RUN_SECRET_SCAN`); enabling that repo variable would test it.
- Writing an ~880 KB file and opening it took ~23 s on this Windows machine (inside `open()`, not our code; cause unverified, possibly on-access scanning). Tests use smaller files.
- Local branches are never deleted by the agent (rule in CLAUDE.md); old branches are kept.
- Build/packaging command is untested (TBD).

## Next step
1. Ask about tagging v0.3.1, then plan v0.4.0 Agent Architecture (see docs/DETAILED_ROADMAP.md) via `/new-feature` after the user approves the plan. Lockfile ingestion (for transitive dependencies) was deliberately deferred in ADR-0006.

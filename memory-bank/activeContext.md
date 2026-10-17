# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.3.0 is merged, CI-green and tagged (`v0.3.0`, 2026-10-17). v0.3.1 (JS/TS + dynamic imports) is implemented and verified locally on `feature/v0.3.1-js-dynamic-imports`; it is not merged or pushed and CI has not run, so it is not done yet.

## Recent decisions
- ADR-0007 (accepted by the user): tree-sitter for JS/TS, private to `graph/js_parse.py`; syntax-node matching only; computed dynamic imports are `unresolved:dynamic:*` + `computed_import`; bare/aliased JS specifiers stay unresolved; no `.js`->`.ts` remap.
- ADR-0006: edge `A -> B` = A depends on B; every edge has evidence; unresolved/ambiguous things are `unresolved` nodes + diagnostics, imports never linked to packages by name; lockfiles stay excluded, so transitive external deps are reported unknown; NetworkX private behind pydantic models.
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- v0.2.0 added no dependencies (YAML is `unvalidated`, not parsed); v0.3.0 added NetworkX.
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Pushes and merges always need explicit user approval (v0.3.1 branch is unpushed).
- Dependabot PRs #1, #3, #4: reviewed, none look breaking for our workflow; merging needs approval. Note #3's CI did not exercise gitleaks (step is gated on `RUN_SECRET_SCAN`).
- Writing an ~880 KB file and opening it took ~23 s on this Windows machine (inside `open()`, not our code; cause unverified, possibly on-access scanning). Tests use smaller files.
- Local branches are never deleted by the agent (rule in CLAUDE.md); old branches are kept.
- Build/packaging command is untested (TBD).

## Next step
1. v0.3.1 is committed on its branch. Get the user's approval to push/merge, confirm CI, then mark v0.3.1 done in ROADMAP/README.
2. Handle Dependabot PRs #1, #3, #4 (with approval), then plan v0.4.0 Agent Architecture. Lockfile ingestion (for transitive dependencies) was deliberately deferred in ADR-0006.

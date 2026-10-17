# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.3.0 and v0.3.1 are tagged and pushed. v0.3.2 Dependency Graph Visualization is done: merged to main, pushed, CI green (run 39021177652), not tagged. No PRs open. v0.4.0 Agent Architecture is next, planned only, not started.

## Recent decisions
- ADR-0008 (accepted by the user): Mermaid and self-contained HTML/SVG from one `GraphView`; no new dependency, no script or network loads; escaped, ASCII, deterministic output; visible truncation (HTML 300/1000, Mermaid 100/200); `--format` is exclusive with `--json`/`--deps-of`; exit 5 for write errors.
- ADR-0007 (accepted by the user): tree-sitter for JS/TS, private to `graph/js_parse.py`; syntax-node matching only; computed dynamic imports are `unresolved:dynamic:*` + `computed_import`; bare/aliased JS specifiers stay unresolved; no `.js`->`.ts` remap.
- ADR-0006: edge `A -> B` = A depends on B; every edge has evidence; unresolved/ambiguous things are `unresolved` nodes + diagnostics, imports never linked to packages by name; lockfiles stay excluded, so transitive external deps are reported unknown; NetworkX private behind pydantic models.
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- v0.2.0 added no dependencies (YAML is `unvalidated`, not parsed); v0.3.0 added NetworkX.
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Pushes and merges always need explicit user approval (v0.3.2 was merged and pushed on explicit approval).
- `RUN_SECRET_SCAN=true`; gitleaks runs in CI with `fetch-depth: 0` and passes (full history, no leaks). Detection is still pattern-based.
- Writing an ~880 KB file and opening it took ~23 s on this Windows machine (inside `open()`, not our code; cause unverified, possibly on-access scanning). Tests use smaller files.
- Local branches are never deleted by the agent (rule in CLAUDE.md); old branches are kept.
- Build/packaging command is untested (TBD).

## Next step
1. Ask the user about tagging v0.3.2 (on merge commit `e19d5e1`).
2. Only when the user asks: plan v0.4.0 Agent Architecture (see docs/DETAILED_ROADMAP.md). Lockfile ingestion (for transitive dependencies) was deliberately deferred in ADR-0006.

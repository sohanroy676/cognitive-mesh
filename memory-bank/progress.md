# Progress

<!-- Running log. Append, don't rewrite history. Newest at top.
     This tracks WORK STATUS (agent-facing, session to session).
     For the forward-looking feature plan, see ROADMAP.md instead —
     don't duplicate the full plan here, just what's actually in motion. -->

## Done

- v0.2.0 Application Ingestion (branch `feature/v0.2.0-ingestion`) — 2026-10-16. `cognitive_mesh.ingestion` + `cognitive-mesh ingest` CLI, ADR-0005, 129 tests passing (1 skipped on Windows: control-character filename), ruff/mypy clean. Redo of an earlier attempt (`ed71a4f` on `feature/v0.2.0-application-ingestion`, kept untouched) that adds content-based secret redaction and non-Python source files. DETAILED_ROADMAP v0.2.0 Test/Expected items verified locally. Merged to main (`a254714`) and pushed 2026-10-16; CI on main passed (run 38947231207). Not tagged v0.2.0. Known limitations: pattern-based secret detection is not exhaustive, YAML not syntax-checked, lockfiles excluded, in-memory only, local directories only.
- v0.1.0 closeout — 2026-10-16. Merged to main, pushed, tagged `v0.1.0`; GitHub Actions CI on main (run 38942568274) passed. ROADMAP.md updated.
- v0.1.0 Project Foundation (branch `feature/v0.1.0-project-foundation`) — 2026-10-16. uv + ruff/mypy/pytest, pydantic-settings config, minimal CLI, 20 passing tests, CI rewritten, ADRs 0002-0004. All DETAILED_ROADMAP v0.1.0 Test/Expected items verified locally and from a fresh clone. Later merged and pushed (see closeout above).

## In progress

- Nothing in flight. Next: v0.3.0 Dependency Graph. Ask the user before tagging `v0.2.0`.
- The old branch `feature/v0.2.0-application-ingestion` had uncommitted deletions of the whole ingestion code when this session started (cause unknown); they are saved in `git stash@{0}`. Decide whether to drop the stash and old branch.

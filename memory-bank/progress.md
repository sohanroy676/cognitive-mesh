# Progress

<!-- Running log. Append, don't rewrite history. Newest at top.
     This tracks WORK STATUS (agent-facing, session to session).
     For the forward-looking feature plan, see ROADMAP.md instead —
     don't duplicate the full plan here, just what's actually in motion. -->

## Done

- Session handoff 2026-10-17: added `README.md` (updated every version), planned v0.3.1 (JS/TS + dynamic imports) in ROADMAP.md and DETAILED_ROADMAP.md (planned only, nothing implemented). v0.3.0 merged to main and pushed with user approval; marking it done waits for green CI on main.
- v0.3.0 Dependency Graph (branch `feature/v0.3.0-dependency-graph`) — 2026-10-17. `cognitive_mesh.graph` + `cognitive-mesh graph PATH [--json] [--deps-of ID [--transitive]]`, ADR-0006, NetworkX 3.7 (+ `types-networkx`). 171 tests (170 passed, 1 skipped on Windows), ruff/mypy clean; `graph` commands run locally (see tech-stack.md). Covers fixture graph equality, missing/malformed/unsupported manifests, unresolved requirements and imports, cycles (incl. self-import, truncation flag), direct/indirect traversal, evidence on every edge. Merged to main (`d172312`) and pushed 2026-10-17; CI on main passed (run 39010744225). Not tagged. Known limitations: see ADR-0006 (lockfiles excluded, no JS/TS or dynamic imports, in-memory).
- Housekeeping 2026-10-17: tagged and pushed `v0.2.0` (on `0059190`); dropped the old stash; added the rule "never delete local branches" to CLAUDE.md and both rules dirs.
- v0.2.0 Application Ingestion (branch `feature/v0.2.0-ingestion`) — 2026-10-16. `cognitive_mesh.ingestion` + `cognitive-mesh ingest` CLI, ADR-0005, 129 tests passing (1 skipped on Windows: control-character filename), ruff/mypy clean. Redo of an earlier attempt (`ed71a4f` on `feature/v0.2.0-application-ingestion`, kept untouched) that adds content-based secret redaction and non-Python source files. DETAILED_ROADMAP v0.2.0 Test/Expected items verified locally. Merged to main (`a254714`) and pushed 2026-10-16; CI on main passed (run 38947231207). Not tagged v0.2.0. Known limitations: pattern-based secret detection is not exhaustive, YAML not syntax-checked, lockfiles excluded, in-memory only, local directories only.
- v0.1.0 closeout — 2026-10-16. Merged to main, pushed, tagged `v0.1.0`; GitHub Actions CI on main (run 38942568274) passed. ROADMAP.md updated.
- v0.1.0 Project Foundation (branch `feature/v0.1.0-project-foundation`) — 2026-10-16. uv + ruff/mypy/pytest, pydantic-settings config, minimal CLI, 20 passing tests, CI rewritten, ADRs 0002-0004. All DETAILED_ROADMAP v0.1.0 Test/Expected items verified locally and from a fresh clone. Later merged and pushed (see closeout above).

## In progress

- Nothing in flight. Next: v0.3.1 (JS/TS + dynamic imports). v0.3.0 is not tagged (ask the user).
- Open Dependabot PRs #1 (checkout 4->7), #3 (gitleaks-action 2->3), #4 (setup-uv 6->7): user chose to review and merge them after v0.3.0, with approval.
- Old branch `feature/v0.2.0-application-ingestion` is kept (local branches are never deleted).

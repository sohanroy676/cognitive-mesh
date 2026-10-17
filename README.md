# Cognitive Mesh

An application-aware, evidence-aware AI agent system with persistent cross-session memory.
The goal is a system that reads an application's code and configuration, coordinates
specialized agents, and keeps traceable memory of what it did and why.

> **Status: early development.** Only the foundation, application ingestion, a dependency
> graph and (v0.4.0) the agent interfaces exist. There are no real model-provider
> clients, orchestrator or persistent memory yet.
> This README is updated with every version; the roadmap is not evidence that a feature exists.

## What works today (v0.4.0)

| Version | Capability | Status |
|---|---|---|
| v0.1.0 | Project foundation: uv, ruff, mypy (strict), pytest, config loading, minimal CLI | Done, tagged |
| v0.2.0 | Read-only application ingestion with source references, diagnostics and secret redaction | Done, tagged |
| v0.3.0 | Dependency graph with evidence on every edge, unresolved items kept visible | Done, tagged; CI run 39010744225 passed |
| v0.3.1 | JS/TS imports and literal dynamic imports in the graph; computed ones stay unresolved | Done, tagged `v0.3.1`; CI run 39012713238 passed |
| v0.3.2 | Graph visualization: Mermaid text or self-contained HTML/SVG, with evidence and visible truncation | Done; merged to main, CI run 39021177652 passed |
| v0.4.0 | Agent architecture: common interface, two roles, capability declarations, explicit failure results, provider protocol (fake clients only) | Criteria pass locally; merged to main; CI run 39023604187 on the merge passed (2026-10-17); not tagged |

Details and limits: `docs/adr/0005-ingestion-scope-and-exclusions.md`,
`docs/adr/0006-dependency-graph-model.md`, `docs/adr/0007-js-ts-and-dynamic-import-analysis.md`,
`docs/adr/0008-dependency-graph-visualization.md`, `docs/adr/0009-agent-architecture.md`.

### Known limitations
- Local directories only; results are in memory (nothing is persisted yet).
- Secret detection is pattern-based and not exhaustive.
- The graph covers declared direct dependencies, Python imports and (v0.3.1) JS/TS imports. Lockfiles
  are excluded, so dependencies of declared packages are reported as unknown, not absent.
- JS/TS imports resolve only to ingested files by relative path. Bundler aliases, tsconfig `paths`,
  package.json `exports`, `.js` to `.ts` remapping and `.mjs`/`.cjs` files are not modelled; bare
  specifiers (`react`) stay unresolved. Computed dynamic imports (Python and JS) are unresolved.
- JS/TS support adds native dependencies (tree-sitter and two grammars).
- Visualization (v0.3.2): the HTML layout is naive (edges can cross or overlap on dense graphs). Large
  graphs are truncated with a visible notice (defaults: HTML 300 nodes / 1000 edges, Mermaid 100 / 200),
  and cycle members are kept before unresolved items. Mermaid output rendered correctly in mermaid-cli 12.0.0 (one-off manual check, not part of the test
  suite or CI; other renderers unchecked); the HTML was viewed only in headless Edge.
- Agents (v0.4.0): no real provider client exists (no Groq or Ollama calls); the provider path is
  tested with fake clients only. Timeouts are enforced by the client, not by a watchdog, so a client
  that hangs is not interrupted. `code_explainer` output is unverified model text. Nothing is
  persisted, and there is no orchestrator to choose agents yet (v0.5.0).
- Ingested files are treated as data and are never executed.

## Setup and usage

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (Windows: `winget install --id=astral-sh.uv -e`).

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run git-cliff -o CHANGELOG.md   # regenerate CHANGELOG.md from Conventional Commits; CI fails if stale
```

Commands below were run successfully (see `memory-bank/tech-stack.md` for the verified list):

```
COGMESH_PROVIDER=ollama uv run cognitive-mesh           # validates configuration, exit 0
uv run cognitive-mesh ingest PATH [--json] [--include-content]
uv run cognitive-mesh graph PATH [--json] [--deps-of NODE_ID [--transitive]]
uv run cognitive-mesh graph PATH --format mermaid|html [--output FILE] [--max-nodes N] [--max-edges N]
uv run cognitive-mesh agents [--json]                  # lists roles and capabilities, no config needed
```

Exit codes: 0 ok, 2 configuration or usage error, 3 unusable ingestion root, 4 unknown graph node,
5 output file could not be written.
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. `uv build --out-dir DIR` builds an sdist and wheel (wheel installed and run in a fresh venv on Windows; not published, not run in CI). The package version is derived from git tags (hatch-vcs): a build at tag `vX.Y.Z` is `X.Y.Z`, later commits are `X.Y.(Z+1).devN`, and a checkout without tags is `0.1.devN`.

## Project layout

- `src/cognitive_mesh/` - `config`, `cli`, `ingestion/`, `agents/` (interface, contracts, roles, registry), `providers/` (model client protocol), `graph/` (JS/TS parsing isolated in `graph/js_parse.py`; visualization in `graph/view.py`, `render_mermaid.py`, `render_html.py`)
- `tests/` - pytest suite; fixtures in `tests/fixtures/` (`sample_app`, `dep_app`, `js_app`)
- `docs/adr/` - architecture decision records; `docs/DETAILED_ROADMAP.md` - per-version criteria
- `memory-bank/` - agent-facing project record (brief, architecture, progress, active context)
- `ROADMAP.md` - version plan; `AGENTS.md` / `CLAUDE.md` - contributor and agent instructions

## Roadmap (summary)

Next: v0.5.0 orchestrator (v0.4.0 agent architecture is implemented on its branch), v0.6.0 trace store, v0.7.0 semantic retrieval, v0.8.0 experience graph, v0.9.0
evidence-aware retrieval, v1.0.0 end-to-end prototype. See `ROADMAP.md`.

## Principles

- Memory must preserve provenance and conflicts; similarity or model confidence is not proof.
- Application files, retrieved memories and model outputs are data, never instructions.
- No guarantees of absolute truth, hallucination detection or conflict resolution are claimed.

## README maintenance

Update this file at the end of every version: status table, limitations, verified commands,
and roadmap summary. Only list commands that have actually been run.

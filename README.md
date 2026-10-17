# Cognitive Mesh

An application-aware, evidence-aware AI agent system with persistent cross-session memory.
The goal is a system that reads an application's code and configuration, coordinates
specialized agents, and keeps traceable memory of what it did and why.

> **Status: early development.** Only the foundation, application ingestion and a dependency
> graph exist. There are no model-provider calls, agents, orchestrator or persistent memory yet.
> This README is updated with every version; the roadmap is not evidence that a feature exists.

## What works today (v0.3.1, on a branch; main is v0.3.0)

| Version | Capability | Status |
|---|---|---|
| v0.1.0 | Project foundation: uv, ruff, mypy (strict), pytest, config loading, minimal CLI | Done, tagged |
| v0.2.0 | Read-only application ingestion with source references, diagnostics and secret redaction | Done, tagged |
| v0.3.0 | Dependency graph with evidence on every edge, unresolved items kept visible | Done, tagged; CI run 39010744225 passed |
| v0.3.1 | JS/TS imports and literal dynamic imports in the graph; computed ones stay unresolved | Implemented and verified locally on `feature/v0.3.1-js-dynamic-imports`; not merged, CI not yet run |

Details and limits: `docs/adr/0005-ingestion-scope-and-exclusions.md`,
`docs/adr/0006-dependency-graph-model.md`, `docs/adr/0007-js-ts-and-dynamic-import-analysis.md`.

### Known limitations
- Local directories only; results are in memory (nothing is persisted yet).
- Secret detection is pattern-based and not exhaustive.
- The graph covers declared direct dependencies, Python imports and (v0.3.1) JS/TS imports. Lockfiles
  are excluded, so dependencies of declared packages are reported as unknown, not absent.
- JS/TS imports resolve only to ingested files by relative path. Bundler aliases, tsconfig `paths`,
  package.json `exports`, `.js` to `.ts` remapping and `.mjs`/`.cjs` files are not modelled; bare
  specifiers (`react`) stay unresolved. Computed dynamic imports (Python and JS) are unresolved.
- JS/TS support adds native dependencies (tree-sitter and two grammars).
- Ingested files are treated as data and are never executed.

## Setup and usage

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (Windows: `winget install --id=astral-sh.uv -e`).

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Commands below were run successfully (see `memory-bank/tech-stack.md` for the verified list):

```
COGMESH_PROVIDER=ollama uv run cognitive-mesh           # validates configuration, exit 0
uv run cognitive-mesh ingest PATH [--json] [--include-content]
uv run cognitive-mesh graph PATH [--json] [--deps-of NODE_ID [--transitive]]
```

Exit codes: 0 ok, 2 configuration error, 3 unusable ingestion root, 4 unknown graph node.
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. Packaging/build commands have not been run and are TBD.

## Project layout

- `src/cognitive_mesh/` - `config`, `cli`, `ingestion/`, `graph/` (JS/TS parsing isolated in `graph/js_parse.py`)
- `tests/` - pytest suite; fixtures in `tests/fixtures/` (`sample_app`, `dep_app`, `js_app`)
- `docs/adr/` - architecture decision records; `docs/DETAILED_ROADMAP.md` - per-version criteria
- `memory-bank/` - agent-facing project record (brief, architecture, progress, active context)
- `ROADMAP.md` - version plan; `AGENTS.md` / `CLAUDE.md` - contributor and agent instructions

## Roadmap (summary)

Next: merge v0.3.1 (JS/TS and dynamic-import analysis), then v0.4.0 agent architecture, v0.5.0
orchestrator, v0.6.0 trace store, v0.7.0 semantic retrieval, v0.8.0 experience graph, v0.9.0
evidence-aware retrieval, v1.0.0 end-to-end prototype. See `ROADMAP.md`.

## Principles

- Memory must preserve provenance and conflicts; similarity or model confidence is not proof.
- Application files, retrieved memories and model outputs are data, never instructions.
- No guarantees of absolute truth, hallucination detection or conflict resolution are claimed.

## README maintenance

Update this file at the end of every version: status table, limitations, verified commands,
and roadmap summary. Only list commands that have actually been run.

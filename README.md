# Cognitive Mesh

An application-aware, evidence-aware AI agent system with persistent cross-session memory.
The goal is a system that reads an application's code and configuration, coordinates
specialized agents, and keeps traceable memory of what it did and why.

> **Status: early development.** Only the foundation, application ingestion, a dependency
> graph, the agent interfaces, (v0.5.0) a basic orchestrator, (v0.6.0) a SQLite store for run
> traces and (v0.6.1) a real Ollama client exist. There is no Groq client and no retrieval of past
> experience yet.
> This README is updated with every version; the roadmap is not evidence that a feature exists.

## What works today (v0.6.1)

| Version | Capability | Status |
|---|---|---|
| v0.1.0 | Project foundation: uv, ruff, mypy (strict), pytest, config loading, minimal CLI | Done, tagged |
| v0.2.0 | Read-only application ingestion with source references, diagnostics and secret redaction | Done, tagged |
| v0.3.0 | Dependency graph with evidence on every edge, unresolved items kept visible | Done, tagged; CI run 39010744225 passed |
| v0.3.1 | JS/TS imports and literal dynamic imports in the graph; computed ones stay unresolved | Done, tagged `v0.3.1`; CI run 39012713238 passed |
| v0.3.2 | Graph visualization: Mermaid text or self-contained HTML/SVG, with evidence and visible truncation | Done; merged to main, CI run 39021177652 passed |
| v0.4.0 | Agent architecture: common interface, two roles, capability declarations, explicit failure results, provider protocol (fake clients only) | Criteria pass locally; merged to main; Done, tagged `v0.4.0`; CI runs 39023604187 and 39024302873 passed |
| v0.5.0 | Orchestrator: validated task plans, dependency ordering, capability-based assignment, explicit failed/blocked/incomplete results, bounded retries, run budget, `run` command | Done, tagged `v0.5.0`; CI runs 39024871768 and 39025436259 passed |
| v0.6.0 | Episodic trace store: SQLite persistence of orchestrator runs (steps, attempts, ids, model info, timestamps), redaction before storing, success/failed/incomplete/interrupted states, `run --trace-db`, `traces list/show` | Done, tagged `v0.6.0`; CI runs 39026432280 and 39028485876 passed |
| v0.6.1 | Real Ollama client (stdlib HTTP, hard total deadline, every failure mapped to a provider error kind), `COGMESH_OLLAMA_MODEL` / `COGMESH_MODEL_TIMEOUT_S`, `run` uses it only for plans with a model step | Criteria pass locally (including a real `llama3.2` run); on branch `feature/v0.6.1-ollama-client`, not merged, CI not yet run |

Details and limits: `docs/adr/0005-ingestion-scope-and-exclusions.md`,
`docs/adr/0006-dependency-graph-model.md`, `docs/adr/0007-js-ts-and-dynamic-import-analysis.md`,
`docs/adr/0008-dependency-graph-visualization.md`, `docs/adr/0009-agent-architecture.md`, `docs/adr/0010-orchestrator.md`, `docs/adr/0011-episodic-trace-store.md`, `docs/adr/0012-ollama-client.md`.

### Known limitations
- Local directories only; results are in memory, except run traces recorded with `--trace-db` (v0.6.0).
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
  persisted.
- Orchestrator (v0.5.0): steps run sequentially; `depends_on` controls order only (no data passes
  between steps, inputs are literal); no fallback to another agent. A hung provider call is not
  interrupted: the run budget is checked only before each attempt. Retries apply only to failures an
  agent marks retryable, at most 5 attempts per step. With no provider configured, `explain_file`
  steps fail as `provider_unavailable`. Run results are persisted only when `--trace-db` is given.
- Trace store (v0.6.0): recording is opt-in (`run --trace-db FILE`). Traces are raw records, not
  validated knowledge, and nothing retrieves or reuses them yet. Secrets are redacted by pattern
  before storing, so a stored trace is not a byte-exact replay and detection is not exhaustive.
  The trace holds the resolved source path and file hashes (no hostname or user name). A run is
  committed as `running` first; a crash reads back as `interrupted` with no step data. No
  retention or size limits, one writer assumed, no schema migrations (version 1), no per-attempt
  timing, no tool activity beyond agent attempts. Run `traces` against files you trust: they are
  read as data and validated, but they are not authenticated.
- Ollama client (v0.6.1): only `COGMESH_PROVIDER=ollama` has a client; `groq` exits 2 with a clear
  message until v0.6.2. Only `run` calls a model, and only for plans with a model step
  (`explain_file`). The contents of the explained file are sent to the configured Ollama server, so
  point `COGMESH_OLLAMA_BASE_URL` only at a server you trust. No streaming, proxies, redirects or
  connection reuse. A missing model (HTTP 404) is not retried; run `ollama pull <model>`. Model
  output stays `model_unverified`. Verified against one local Ollama with `llama3.2` only; the
  opt-in live test is `RUN_LIVE_OLLAMA=1 uv run pytest -m live`.
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
uv run cognitive-mesh run PLAN.json --root DIR [--json]   # runs a task plan, no config needed
uv run cognitive-mesh run PLAN.json --root DIR --trace-db FILE   # also records the run (SQLite)
uv run cognitive-mesh traces list --db FILE [--json]
uv run cognitive-mesh traces show RUN_ID --db FILE [--json]
```

Exit codes: 0 ok, 2 configuration or usage error, 3 unusable ingestion root, 4 unknown graph node,
5 output file could not be written, 6 invalid or unreadable plan, 7 task failed or incomplete, 8 trace store error or malformed trace, 9 unknown trace run id.
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. `uv build --out-dir DIR` builds an sdist and wheel (wheel installed and run in a fresh venv on Windows; not published, not run in CI). The package version is derived from git tags (hatch-vcs): a build at tag `vX.Y.Z` is `X.Y.Z`, later commits are `X.Y.(Z+1).devN`, and a checkout without tags is `0.1.devN`.

## Project layout

- `src/cognitive_mesh/` - `config`, `cli`, `ingestion/`, `agents/` (interface, contracts, roles, registry), `orchestrator/` (plans, ordering, runner), `traces/` (SQLite trace store, recorder), `providers/` (model client protocol, Ollama client, stdlib HTTP transport), `graph/` (JS/TS parsing isolated in `graph/js_parse.py`; visualization in `graph/view.py`, `render_mermaid.py`, `render_html.py`)
- `tests/` - pytest suite; fixtures in `tests/fixtures/` (`sample_app`, `dep_app`, `js_app`)
- `docs/adr/` - architecture decision records; `docs/DETAILED_ROADMAP.md` - per-version criteria
- `memory-bank/` - agent-facing project record (brief, architecture, progress, active context)
- `ROADMAP.md` - version plan; `AGENTS.md` / `CLAUDE.md` - contributor and agent instructions

## Roadmap (summary)

Next: v0.6.2 real Groq client (planned, not started), then v0.7.0 semantic retrieval (v0.6.1 Ollama
client is on its branch awaiting review), v0.8.0 experience graph, v0.9.0
evidence-aware retrieval, v1.0.0 end-to-end prototype. See `ROADMAP.md`.

## Principles

- Memory must preserve provenance and conflicts; similarity or model confidence is not proof.
- Application files, retrieved memories and model outputs are data, never instructions.
- No guarantees of absolute truth, hallucination detection or conflict resolution are claimed.

## README maintenance

Update this file at the end of every version: status table, limitations, verified commands,
and roadmap summary. Only list commands that have actually been run.

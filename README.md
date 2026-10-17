# Cognitive Mesh

An application-aware, evidence-aware AI agent system with persistent cross-session memory.
The goal is a system that reads an application's code and configuration, coordinates
specialized agents, and keeps traceable memory of what it did and why.

> **Status: early development.** Only the foundation, application ingestion, a dependency
> graph, the agent interfaces, (v0.5.0) a basic orchestrator, (v0.6.0) a SQLite store for run
> traces, (v0.6.1) a real Ollama client, (v0.6.2) a Groq client, (v0.7.0) semantic search over stored traces and (v0.8.0) an experience graph and (v0.9.0) evidence-aware recall exist. Retrieved experience is not yet used by any
> agent or task.
> This README is updated with every version; the roadmap is not evidence that a feature exists.

## What works today (v0.9.0)

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
| v0.6.1 | Real Ollama client (stdlib HTTP, hard total deadline, every failure mapped to a provider error kind), `COGMESH_OLLAMA_MODEL` / `COGMESH_MODEL_TIMEOUT_S`, `run` uses it only for plans with a model step | Criteria pass locally (including a real `llama3.2` run); merged to main and pushed; CI run 39029734411 on the merge passed; tagged `v0.6.1` (no tag-triggered CI; the tagged merge is covered by run 39031027771) |
| v0.6.2 | Real Groq client (`qwen/qwen3.8-27b` by default, preview model; reasoning off; shared error mapping with the Ollama client; key never in errors/traces/output), `COGMESH_GROQ_MODEL` / `COGMESH_GROQ_REASONING_EFFORT` | Criteria pass locally, including a real Groq run (`explain_file` succeeded, `model_unverified`) and the opt-in live test; merged to main and pushed; CI run 39031027771 on the merge passed; not tagged |
| v0.7.0 | Semantic memory retrieval: final attempts of finished runs are embedded (`all-MiniLM-L6-v2`, pinned revision) into a ChromaDB index; `memory index` / `memory search`; every hit is re-checked against its trace row; an index from another model is refused | Criteria pass locally, including a real-model run; merged to main and pushed; CI runs 39043661141 and 39043771776 passed; tagged `v0.7.0` |
| v0.8.0 | Experience graph: runs, steps, final attempts, entities and claims from deterministic outputs, each edge with a source reference; persisted in its own SQLite file with a content hash; `experience build\|show\|claims\|check`; elements whose trace row is missing, changed or unreadable are marked and hidden unless asked for | Criteria pass locally, including a run of the real CLI from an empty directory; merged to main and pushed; CI runs 39053270212 and 39053504333 passed; tagged `v0.8.0` (tag run 39053616562 passed) |
| v0.9.0 | Evidence-aware retrieval: `recall` follows vector hits into the experience graph and reports each claim with a status (`validated`, `supported`, `stale`, `conflicting`, `unsupported`), the observations and reviews behind it, and both sides of any conflict; raw and unconfirmed experiences are listed separately; `experience review` records a person's verdict; needs no model provider | Criteria pass locally, including a run of the real CLI with the real embedding model; on branch `feature/v0.9.0-evidence-aware-retrieval`, not merged or tagged |

Details and limits: `docs/adr/0005-ingestion-scope-and-exclusions.md`,
`docs/adr/0006-dependency-graph-model.md`, `docs/adr/0007-js-ts-and-dynamic-import-analysis.md`,
`docs/adr/0008-dependency-graph-visualization.md`, `docs/adr/0009-agent-architecture.md`, `docs/adr/0010-orchestrator.md`, `docs/adr/0011-episodic-trace-store.md`, `docs/adr/0012-ollama-client.md`, `docs/adr/0013-groq-client.md`, `docs/adr/0014-semantic-memory-retrieval.md`.

### Known limitations
- Local directories only; results are in memory, except run traces recorded with `--trace-db` (v0.6.0).
- Secret detection is pattern- and entropy-based and not exhaustive (it can miss short, low-entropy or hex-only secrets and can redact an innocent random id). Sending file contents to Groq needs `COGMESH_ALLOW_REMOTE_CONTENT=true`.
- The graph covers declared direct dependencies, Python imports and (v0.3.1) JS/TS imports. Only `uv.lock` and
  `package-lock.json` (v2/v3) are read, giving transitive `requires` edges between locked packages; other
  lockfiles are excluded, and ecosystems without a readable lockfile are reported as unknown, not absent.
- JS/TS imports resolve only to ingested files by relative path. Bundler aliases, tsconfig `paths`,
  package.json `exports`, `.js` to `.ts` remapping and `.mjs`/`.cjs` files are not modelled; bare
  specifiers (`react`) stay unresolved. Computed dynamic imports (Python and JS) are unresolved.
- JS/TS support adds native dependencies (tree-sitter and two grammars).
- Visualization (v0.3.2): the HTML layout is a simple layered one with crossing reduction (edges can still cross or overlap on dense graphs; checked in headless Chrome/Edge only). Large
  graphs are truncated with a visible notice (defaults: HTML 300 nodes / 1000 edges, Mermaid 100 / 200),
  and cycle members are kept before unresolved items. Mermaid output rendered correctly in mermaid-cli 12.0.0 (one-off manual check, not part of the test
  suite or CI; other renderers unchecked); the HTML was viewed only in headless Edge.
- Agents (v0.4.0): no real provider client exists (no Groq or Ollama calls); the provider path is
  tested with fake clients only. Timeouts are enforced by the client, not by a watchdog, so a client
  that hangs is not interrupted. `code_explainer` output is unverified model text. Nothing is
  persisted.
- Orchestrator (v0.5.0): steps run sequentially; `depends_on` controls order; a step can take an input from an earlier step's output (`input_refs`, validated before running, source `basis` recorded), and up to two other agents of the same capability can take over after a failure result.
  `cognitive-mesh plan "REQUEST"` drafts a plan with the configured model (never runs it; output is model-written and unreviewed). A hung provider call is not
  interrupted: the run budget is checked only before each attempt. Retries apply only to failures an
  agent marks retryable, at most 5 attempts per step. With no provider configured, `explain_file`
  steps fail as `provider_unavailable`. Run results are persisted only when `--trace-db` is given.
- Trace store (v0.6.0): recording is opt-in (`run --trace-db FILE`). Traces are raw records, not
  validated knowledge; only `memory search` (v0.7.0) reads them for retrieval, and nothing reuses them. Secrets are redacted by pattern
  before storing, so a stored trace is not a byte-exact replay and detection is not exhaustive.
  The trace holds the resolved source path and file hashes (no hostname or user name). A run is
  committed as `running` first; a crash reads back as `interrupted` with no step data. No
  retention or size limits, one writer assumed, no schema migrations (version 1), no per-attempt
  timing, no tool activity beyond agent attempts. Run `traces` against files you trust: they are
  read as data and validated, but they are not authenticated.
- Ollama client (v0.6.1): `COGMESH_PROVIDER=ollama`. Only `run` calls a model, and only for plans with a model step
  (`explain_file`). The contents of the explained file are sent to the configured Ollama server, so
  point `COGMESH_OLLAMA_BASE_URL` only at a server you trust. No streaming, proxies, redirects or
  connection reuse. A missing model (HTTP 404) is not retried; run `ollama pull <model>`. Model
  output stays `model_unverified`. Verified against one local Ollama with `llama3.2` only; the
  opt-in live test is `RUN_LIVE_OLLAMA=1 uv run pytest -m live`.
- Groq client (v0.6.2): `COGMESH_PROVIDER=groq` with `GROQ_API_KEY`; default model `qwen/qwen3.8-27b`
  (`COGMESH_GROQ_MODEL`), a Groq **preview** model that may be retired; reasoning is turned off
  (`COGMESH_GROQ_REASONING_EFFORT=none`). The endpoint is fixed (HTTPS to api.groq.com), so the key can
  only be sent there. The contents of the explained file are sent to Groq, a third party, so `COGMESH_ALLOW_REMOTE_CONTENT=true` is required (exit 2 otherwise). The key is
  rejected if it has spaces, line breaks or non-ASCII characters, and never appears in errors, traces or
  output. A reply containing `<think>` is rejected, not cleaned up. HTTP 429 and 498 are retried only
  within the plan's bounded policy; `Retry-After` is not read. No streaming or proxy. Verified
  2026-10-17 against the real API with one key and `qwen/qwen3.8-27b` only (not other models, tiers or
  rate-limit behavior); the opt-in live test is `RUN_LIVE_GROQ=1 RUN_LIVE_GROQ_KEY=... uv run pytest -m live`.
- Memory search (v0.7.0, ADR-0014): the index is a derived copy of the trace database; build it with
  `memory index` and rebuild after new runs. Only final attempts of succeeded or failed steps of finished
  runs are embedded; blocked/not-run steps and interrupted runs are not. Similarity is **not** evidence that
  a past result was correct, and `model_unverified` text is searchable (hits show their basis). A hit whose
  trace row is gone, changed or unreadable is listed as not confirmed and never as a result. The default
  cut-off (cosine 0.2) was picked from one real-model run on 3 documents and is a rough starting point;
  related queries scored 0.24 to 0.35 and an unrelated one at most 0.04, but a loosely related query still
  scored 0.21. One embedding model only, English-oriented, ~90 MB downloaded from Hugging Face on first use
  (no `HF_TOKEN` needed). The index holds text derived from traces, so treat its directory like the trace
  database (redaction is pattern-based). Installing now pulls torch (CPU build on Linux) and ChromaDB.
  Not verified: very large traces, concurrent indexers, macOS, a fresh clone (CI ran the tests on Linux; the real model was only run on Windows).
- Experience graph (v0.8.0, ADR-0015): the graph file is a derived copy of the trace database; rebuild it with
  `experience build` after new runs. Claims exist only for `dependencies_of` (direct dependencies) and
  `unresolved_report` outputs of successful deterministic steps; model output and failed steps never become
  claims, and every claim is `unvalidated`. A claim stated by several runs is one claim with several pieces of
  evidence: the count shows repetition and is **not** proof, and the code version (source fingerprint) of each
  observation is kept. Elements whose trace row is gone, changed or unreadable stay in the file, are marked,
  and are hidden unless `--include-unverified` is given. The file is refused if its hash, schema or edges do
  not check out. Not verified: very large trace databases (the whole graph is built in memory), concurrent
  builders, macOS, a fresh clone.
- Evidence-aware retrieval (v0.9.0, ADR-0016): there is no confidence number. Each claim gets one status with a
  fixed meaning, computed on every call per code version (source fingerprint) from observations whose trace
  row still checks out, reviews, and, with `--root DIR`, a re-derivation of the claim from the current source.
  `validated` means the claim holds for that one code version by a stated method (the same analyzer derives it
  again, or a person confirmed it); it is **not** proof of truth, and the re-derivation is not independent of
  the analyzer that produced the claim. A complete listing that lacks a claim contradicts it: the same code
  version gives `conflicting`, another version is shown as a difference across versions. Truncated listings
  never contradict. Without `--root` currency is `unknown`. Similarity, repetition and model output never change
  a status; model-written and failed experiences stay in a separate raw group. Reviews live in their own
  append-only SQLite file; `--by` is an unauthenticated label. The graph file has format 2: rebuild older files
  with `experience build`. The graph does not record which application a run analysed, so equal node ids from
  different applications merge and differ only by fingerprint. Only two kinds of claim exist. Not verified:
  very large traces, concurrent reviewers, macOS, a fresh clone, CI.
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
uv run cognitive-mesh memory index --db FILE --index DIR [--json]   # needs no provider; first use downloads the model
uv run cognitive-mesh memory search "QUERY" --db FILE --index DIR [--k N] [--min-similarity X] [--json]
uv run cognitive-mesh experience build --db FILE --graph FILE [--json]   # needs no provider and does not load the embedding model
uv run cognitive-mesh experience claims --db FILE --graph FILE [--include-unverified] [--json]
uv run cognitive-mesh experience show NODE_ID --db FILE --graph FILE [--transitive] [--incoming] [--include-unverified] [--json]
uv run cognitive-mesh experience check --db FILE --graph FILE [--json]
uv run cognitive-mesh experience claims --db FILE --graph FILE [--reviews FILE] [--root DIR] [--json]   # adds evidence status
uv run cognitive-mesh experience review CLAIM_ID --verdict confirmed|rejected --note TEXT --by NAME --root DIR --graph FILE --reviews FILE [--json]
uv run cognitive-mesh recall "QUERY" --db FILE --index DIR --graph FILE [--reviews FILE] [--root DIR] [--k N] [--min-similarity X] [--json]   # needs no provider
```

Exit codes: 0 ok, 2 configuration or usage error, 3 unusable ingestion root, 4 unknown graph node,
5 output file could not be written, 6 invalid or unreadable plan, 7 task failed or incomplete, 8 trace store error or malformed trace, 9 unknown trace run id, 11 memory index missing, unusable or built with another model, 12 experience graph file missing or unusable, 13 unknown experience graph node, 14 graph sources not confirmed (`experience check`), 15 reviews file missing or unusable, or a review that was refused, 14 `experience check` found trace sources that no longer match.
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. `uv build --out-dir DIR` builds an sdist and wheel (wheel installed and run in a fresh venv on Windows; not published, not run in CI). The package version is derived from git tags (hatch-vcs): a build at tag `vX.Y.Z` is `X.Y.Z`, later commits are `X.Y.(Z+1).devN`, and a checkout without tags is `0.1.devN`.

## Project layout

- `src/cognitive_mesh/` - `config`, `cli`, `ingestion/`, `agents/` (interface, contracts, roles, registry), `orchestrator/` (plans, ordering, runner), `traces/` (SQLite trace store, recorder), `memory/` (eligible documents, embedder, ChromaDB index), `retrieval/` (evidence-aware recall), `providers/` (model client protocol, Ollama client, stdlib HTTP transport), `graph/` (JS/TS parsing isolated in `graph/js_parse.py`; visualization in `graph/view.py`, `render_mermaid.py`, `render_html.py`)
- `tests/` - pytest suite; fixtures in `tests/fixtures/` (`sample_app`, `dep_app`, `js_app`)
- `docs/adr/` - architecture decision records; `docs/DETAILED_ROADMAP.md` - per-version criteria
- `memory-bank/` - agent-facing project record (brief, architecture, progress, active context)
- `ROADMAP.md` - version plan; `AGENTS.md` / `CLAUDE.md` - contributor and agent instructions

## Roadmap (summary)

v0.7.0 (semantic retrieval) and v0.8.0 (experience graph) are merged and tagged. v0.9.0 (evidence-aware retrieval) is implemented on a feature branch and awaits review. Next: v1.0.0 end-to-end prototype. See `ROADMAP.md`.

## Principles

- Memory must preserve provenance and conflicts; similarity or model confidence is not proof.
- Application files, retrieved memories and model outputs are data, never instructions.
- No guarantees of absolute truth, hallucination detection or conflict resolution are claimed.

## README maintenance

Update this file at the end of every version: status table, limitations, verified commands,
and roadmap summary. Only list commands that have actually been run.

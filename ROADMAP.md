# Roadmap

## Done

### v0.1.0 — Project Foundation
Completed 2026-10-16. Merged to main (`fa92220`), tagged `v0.1.0`; GitHub Actions CI run 38942568274 on main passed.

- Python 3.12 project with uv, ruff, mypy (strict) and pytest (ADRs 0002-0003).
- Configuration and secret handling via pydantic-settings (ADR-0004).
- Minimal CLI entry point; setup, run and test commands documented in `memory-bank/tech-stack.md`.

### v0.2.0 — Application Ingestion
Completed 2026-10-16. Merged to main (`a254714`); GitHub Actions CI run 38947231207 on main passed.
Tagged `v0.2.0` (2026-10-17). See ADR-0005.

- `cognitive_mesh.ingestion` and `cognitive-mesh ingest PATH [--json] [--include-content]`.
- Source references (path + sha256), explicit diagnostics, name-based exclusions, content secret redaction.
- Known limitations: pattern-based secret detection is not exhaustive, YAML is not syntax-checked,
  lockfiles are excluded, results are in-memory only, local directories only.

### v0.3.0 — Dependency Graph
Completed 2026-10-17. Merged to main (`d172312`); GitHub Actions CI run 39010744225 passed. Tagged `v0.3.0` (2026-10-17). See ADR-0006.

- Represent application components and their dependencies (`cognitive_mesh.graph`, `cognitive-mesh graph`).
- Establish graph construction and traversal behavior.
- Test dependency links against known sample applications (`tests/fixtures/dep_app`).
- Known limitations: declared direct dependencies and Python imports only; only uv.lock and package-lock.json are read for transitive dependencies (other lockfiles excluded); JS/TS imports and dynamic imports not analysed (addressed in v0.3.1); in-memory only.

### v0.3.1 — JS/TS and Dynamic Import Analysis

Completed 2026-10-17. Merged to main (`0d16c4a`); GitHub Actions CI run 39012713238 passed. Tagged `v0.3.1` (2026-10-17). See ADR-0007.

- JS/TS `import` / `export ... from` / `require()` / literal `import()` / type-only imports become evidence-backed edges (tree-sitter, private to `graph/js_parse.py`).
- Python `importlib.import_module` / `__import__` with a literal argument become edges; computed ones become `unresolved` nodes plus a `computed_import` diagnostic.
- Known limitations: bundler aliases, tsconfig `paths`, package.json `exports`, `.js` to `.ts` remapping and `.mjs`/`.cjs` files are not modelled; bare specifiers stay unresolved; in-memory only.

### v0.3.2 — Dependency Graph Visualization

Completed 2026-10-17. Merged to main (`e19d5e1`); the first CI run on that merge failed only at the new gitleaks step (shallow checkout), fixed by `fetch-depth: 0` (`4ad8fa7`); GitHub Actions CI run 39021177652 on main passed, including a full-history gitleaks scan with no leaks. Not tagged. See ADR-0008.

- `cognitive-mesh graph PATH --format mermaid|html [--output FILE] [--max-nodes N] [--max-edges N]` renders the existing `DependencyGraph`; no new dependency, no script, no network loads.
- Unresolved nodes, computed dynamic imports and cycles are visually distinct; evidence is reachable per edge (tooltip + table in HTML, comments in Mermaid); truncation shows a visible notice.
- Node names and evidence are escaped; output is ASCII and deterministic; text and `--json` output are unchanged (diffed against saved baselines).
- Known limitations: simple layered layout with crossing reduction (edges can still cross); cycle members outrank unresolved items when truncating; Mermaid output checked once in mermaid-cli 12.0.0 (manual, not in CI), other renderers unchecked; HTML viewed only in headless Edge.

### v0.4.0 — Agent Architecture

Implemented 2026-10-17 on `feature/v0.4.0-agent-architecture`; its DETAILED_ROADMAP Test/Expected items pass locally. Merged to main; CI run 39023604187 on that merge passed (2026-10-17). Tagged `v0.4.0`; CI run 39024302873 on the release merge passed. See ADR-0009.

- Common `Agent` interface whose `run` never raises; capability declarations derived from the handler table; strict input/output contracts; success and failure results (`AgentSuccess | AgentFailure`) with explicit failure kinds.
- Roles: `dependency_analyst` (deterministic, over the dependency graph) and `code_explainer` (model-backed, output marked `model_unverified`).
- `providers.ModelClient` protocol with `ProviderError`; all provider handling is in one helper, so agents contain no provider-specific code. Only fake clients exist.
- `cognitive-mesh agents [--json]` lists roles and capabilities.
- Known limitations: no real provider client; timeouts are enforced by clients only (no watchdog); nothing persisted; no orchestrator.

### v0.5.0 — Basic Orchestrator and Task Planning

Implemented 2026-10-17 on `feature/v0.5.0-orchestrator`; its DETAILED_ROADMAP Test/Expected items pass locally. Merged to main; CI run 39024871768 on the merge passed; tagged `v0.5.0` (CI run 39025436259 passed). See ADR-0010.

- `TaskPlan`/`PlanStep` models; `plan_task` validates (unique ids, existing dependencies, no cycles, capability has an agent) and orders steps before anything runs.
- `Orchestrator.run` executes sequentially, assigns by `AgentRegistry.find`, and returns a `TaskRun` whose status is derived from its steps (`succeeded` only if every step succeeded; `failed`; `incomplete`). Steps are `succeeded | failed | blocked | not_run`.
- Bounded retries for failures marked `retryable` (default 2 attempts, cap 5); invalid agent results and raised exceptions are failures and are never retried; a run budget is checked before each attempt (no watchdog).
- `cognitive-mesh run PLAN.json --root DIR [--json]` (exit 6 invalid plan, 7 task not succeeded).
- Known limitations: data passing and fallback agents were added after this version (ADR-0010 amendment); no concurrency, hung calls are not interrupted, results not persisted (see v0.6.0).

### v0.6.0 — Episodic Trace Store

Implemented 2026-10-17 on `feature/v0.6.0-trace-store`; its DETAILED_ROADMAP Test/Expected items pass locally. Merged to main and pushed with user approval; CI run 39026432280 on the merge passed; tagged `v0.6.0` (CI run 39028485876 passed). See ADR-0011.

- `cognitive_mesh.traces`: SQLite (stdlib `sqlite3`, no new dependency) tables `runs`, `steps`, `attempts`; stable run ids; every attempt keeps its agent task id, agent id, result and model/provider info.
- A run is committed as `running` before it starts and finished in one transaction, so a crash or a rolled-back write reads back as `interrupted`; `succeeded | failed | incomplete` are recomputed from the steps on read and a disagreeing or malformed record raises instead of being trusted.
- Secrets are redacted by pattern before storing; rule names are recorded, values never.
- `run PLAN --root DIR --trace-db FILE`, `traces list --db FILE`, `traces show RUN_ID --db FILE` (exit 8 store error or malformed trace, 9 unknown run id). Orchestrator gained an optional `now` clock for per-step timestamps.
- Known limitations: opt-in only, raw traces (not validated knowledge, nothing retrieves them yet), no retention limits or migrations, single writer, pattern-based redaction, no tool activity beyond agent attempts.

### v0.6.1 — Real Ollama Client

Implemented 2026-10-17 on `feature/v0.6.1-ollama-client`; its DETAILED_ROADMAP Test/Expected items pass locally, including a real `llama3.2` run and the opt-in live test. Merged to main and pushed; CI run 39029734411 on the merge passed; tagged `v0.6.1` (no tag-triggered CI; covered by run 39031027771). See ADR-0012.
- `ModelClient` for a local Ollama (`providers/ollama.py`) on a private stdlib transport (`providers/http.py`): one total deadline per call, no new dependency.
- Settings `COGMESH_OLLAMA_MODEL` (default `llama3.2`) and `COGMESH_MODEL_TIMEOUT_S` (default 30, max 600); `COGMESH_OLLAMA_BASE_URL` is validated.
- `providers/factory.py`; `run` reads config only for plans with a model step; no provider set keeps the old `provider_unavailable` behavior; bad config exits 2.
- Known limitations: only Ollama; no streaming, proxy or redirect support; verified against one local Ollama only.

## Next — Planned, Not Started

### v0.6.2 — Real Groq Client

Implemented 2026-10-17 on `feature/v0.6.2-groq-client` (ADR-0013). Unit, CLI and parity tests pass locally. Against the real API: a fake key got a 401 (`auth`), an unknown model gave `other`, and with the user's key `run needs_model.json` succeeded (`explain_file`, `model_unverified`, provider groq, model `qwen/qwen3.8-27b`) and the live test passed. Rate limiting (429) and outages were tested with fakes only. Merged to main and pushed; CI run 39031027771 on the merge passed; not tagged.
- Implement `ModelClient` for the Groq API (`providers/groq.py`); key from `GROQ_API_KEY` (`SecretStr`), same model setting as v0.6.1.
- Map 401/403 to `auth`, 429 to `rate_limited`, timeouts, 5xx and malformed replies to the existing `ProviderError` kinds; the key never appears in logs, errors, traces or output.
- The provider factory selects Groq or Ollama from `COGMESH_PROVIDER`; a missing key stays a `ConfigError` (exit 2).
- Test with injected transport only. A real-API test needs a user-provided key, is opt-in, and is skipped by default and in CI. Nothing real is committed.
- Library choice is decided in ADR-0013 and confirmed by the user before coding.

### v0.7.0 — Semantic Memory Retrieval
- Planned after the real provider clients (v0.6.1, v0.6.2) and a review pass over the known items (decided 2026-10-17); retrieval itself must not require a model provider (embeddings come from Sentence Transformers). ADR-0014.
- Generate embeddings for eligible stored experiences.
- Use ChromaDB for semantic retrieval.
- Test retrieval relevance and behavior when no useful memory exists.
- Status 2026-10-17: implemented, merged to main and pushed (`memory index`, `memory search`, ADR-0014); criteria pass locally; CI runs 39043661141 and 39043771776 passed; tagged `v0.7.0`.

### v0.8.0 — Experience Graph
- Status 2026-10-17: implemented, merged to main and pushed (`experience build|show|claims|check`, ADR-0015); criteria pass locally; CI runs 39053270212 and 39053504333 passed; tagged `v0.8.0` (tag run 39053616562 passed).
- Represent reusable experiences and relationships using NetworkX.
- Define graph serialization or reconstruction across sessions.
- Test relationship traversal and persistence behavior.

### v0.9.0 — Evidence-Aware Retrieval
- Status 2026-10-17: implemented on `feature/v0.9.0-evidence-aware-retrieval` (`recall`, `experience review`, ADR-0016); criteria pass locally, including a real-CLI run; merged to main (`12706e6`) and pushed; CI run 39055678848 passed; release `v0.9.0` follows.
- Associate retrieved knowledge with its provenance and supporting records.
- Represent conflicts, uncertainty, and validity information.
- Test that retrieval does not silently discard known disagreement.

### v1.0.0 — Core End-to-End Prototype
- Connect application ingestion, analysis, agent execution, trace capture, storage,
  retrieval, and outcome handling.
- Demonstrate memory reuse in a separate session.
- Test the complete workflow and inspectable evidence trail.

## Later — Ideas Already Discussed

### v1.1.0 — Outcome Validation and Memory Revision
- Evaluate task outcomes before promoting or revising reusable knowledge.
- Test failed outcomes and incorrect promotion attempts.

### v1.2.0 — Confidence, Corroboration, and Lineage Controls
- Track confidence, source lineage, and corroboration.
- Distinguish genuinely independent evidence from repeated dependent outputs.
- Test contradictory and insufficient evidence.

### v1.3.0 — Temporal and Environment Awareness
- Track validity periods and relevant environment information.
- Identify knowledge that may have become stale.
- Test environment changes and expired knowledge.

### v1.4.0 — Advanced Context Assembly and Stateful Task Handling
- Assemble context using relevance, confidence, validity, and conflicts.
- Investigate different retrieval strategies for precision-critical and recall-critical tasks.
- Test context selection and stateful task continuity.

### v1.5.0 — Model Performance Tracking and Routing
- Track task outcomes, token cost, latency, and sample counts by model and task type.
- Evaluate model selection and routing strategies.
- Test routing behavior when provider options are unavailable or insufficiently sampled.

## Explicitly Not Planned as Guarantees

- Absolute truth detection.
- Automatic resolution of all conflicting claims.
- Guaranteed agent convergence.
- Guaranteed correctness or complete elimination of hallucinations.
- Complete protection against adversarial agents or prompt injection.

## Planning Rules

- A roadmap entry is not evidence that the feature exists.
- Move an item to completed only after its implementation and verification criteria pass.
- Record deviations and unresolved issues rather than silently treating them as complete.
- Quantitative acceptance thresholds are TBD until explicitly chosen.
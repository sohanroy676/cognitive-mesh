# Architecture

## Status
This is the intended architecture for a new project, not a verified repository
structure. Folder names and module boundaries are proposals to be refined during
implementation.

## 1. Purpose

Build an application-aware AI agent system that examines an existing application's
code and supporting configuration, selects suitable specialized agents, coordinates
their work, and retains evidence-backed experience for future tasks.

The system should be able to reuse relevant past experiences across separate sessions
while tracking their source, validity, confidence, and conflicts.

## 2. Proposed Component Boundaries

- Application Ingestion
  Collect source code, configuration, dependency manifests, and documentation.
  Implemented in v0.2.0 as `src/cognitive_mesh/ingestion/` (see ADR-0005); in-memory results only.
- Application Analysis
  Build a structured understanding of the application and distinguish observed facts
  from assumptions and unresolved questions.
  The dependency-graph part is implemented in v0.3.0 as `src/cognitive_mesh/graph/` (see ADR-0006);
  in-memory only, direct declared dependencies and Python-internal imports. Transitive external
  dependencies are reported as unknown (lockfiles are excluded).
- Agent Registry
  Define available agent roles, capabilities, constraints, and interfaces.
  Implemented in v0.4.0 as `src/cognitive_mesh/agents/` (ADR-0009): common `Agent` interface, two roles
  (`dependency_analyst`, `code_explainer`), registry, explicit failure results. In-memory only.
- Task Planner
  Break a user request into appropriate tasks and dependencies.
  Plan validation and dependency ordering are implemented in v0.5.0 (`orchestrator/planner.py`, ADR-0010);
  plans are written by the caller as JSON, or drafted by `cognitive-mesh plan` (ADR-0010 amendment): a model
  drafts, `plan_task` validates, a person reviews, nothing is executed by the drafting step.
- Custom Orchestrator
  Assign tasks, coordinate execution, manage results, and handle failures.
  Implemented in v0.5.0 as `src/cognitive_mesh/orchestrator/` (ADR-0010): sequential, assignment by
  capability, bounded retries, run budget, task status derived from step statuses. Amended: `input_refs` pass earlier
  step outputs to later steps, and up to two fallback agents (same capability) take over after a failure result.
- Model Provider Interface
  Route model requests to configured providers, including Groq and Ollama where available.
  Protocol and error types (v0.4.0) plus a real Ollama client (v0.6.1, ADR-0012: `providers/ollama.py`, stdlib transport
  `providers/http.py`, `providers/factory.py`). Groq client in v0.6.2 (ADR-0013: `providers/groq.py`, shared mapping `providers/errors.py`).
- Episodic Trace Store
  Preserve task executions, model/provider details, tool activity, outputs, and outcomes.
  Implemented in v0.6.0 as `src/cognitive_mesh/traces/` (ADR-0011): SQLite via stdlib `sqlite3` (private to
  `store.py`), runs/steps/attempts, redaction before storing, `interrupted` for runs that never finished.
  Opt-in (`run --trace-db`, or always with `task`); traces are raw records. Schema version 2 (v1.0.0, ADR-0017)
  also stores, per run, the context pack the run was offered (`run_context`), so a later session can see what
  past experience a run was given.
- Memory Validation
  Evaluate whether an experience is eligible for promotion into reusable knowledge.
  Implemented in v0.9.0 (ADR-0016) as `experience/assess.py` (pure status rules), `current.py` (re-derive a
  claim from the application as it is now) and `reviews.py` (append-only SQLite file of people's verdicts).
  A claim is never promoted by storing it: its status (`validated`, `supported`, `stale`, `conflicting`,
  `unsupported`) is computed per code version on every call. Similarity, repetition and model output never
  validate.
- Vector Retrieval
  Use ChromaDB and embeddings to find semantically relevant past experiences.
  Implemented in v0.7.0 (merged, tagged) as `src/cognitive_mesh/memory/` (ADR-0014): the final attempt of each
  step of a finished run is embedded (pinned MiniLM) into a derived ChromaDB index; `memory index` / `memory search`;
  each hit is re-read from SQLite and reported unconfirmed if the row is missing, changed or unreadable. No agent
  or task uses the results yet.
- Knowledge Graph
  Use NetworkX to represent claims, entities, relationships, provenance, and conflicts.
  Experiences, entities and unvalidated claims are implemented in v0.8.0 (merged, tagged) as
  `src/cognitive_mesh/experience/` (ADR-0015): built from the trace database, persisted in a separate SQLite
  file with a content hash, sources re-checked on load. v0.9.0 (ADR-0016) adds listing coverage (graph
  format 2); claim statuses, conflicts and reviews are computed on top of it (see Memory Validation).
- Context Assembly
  Combine retrieved evidence according to relevance, confidence, validity, and conflict status.
  Partly implemented in v0.9.0 as `src/cognitive_mesh/retrieval/` (`recall`): vector hits are followed into
  the graph; claims, raw experiences and unconfirmed hits are separate groups with the supporting and
  contradicting records listed. It needs no model provider. Since v1.0.0 (ADR-0017) `retrieval/context.py` turns a recall result into a
  read-only `RecalledContext` for the agents: every claim keeps its status, fixed meaning and the run/doc ids
  on both sides; failed and model-written runs are a labelled raw group without text; unconfirmed hits are only
  counted; a relevance gate (claim subject exists in the current graph, same-named root directory for raw
  traces) keeps unrelated experience out and lists it for the person. `workflow.prepare_context` wires recall,
  the gate and the pack for the `task` command; `code_explainer` reads the pack as untrusted data and
  `dependency_analyst.compare_with_recalled` re-checks each claim against the current graph.
- Local Web UI
  Implemented in v1.5.0 on `feature/v1.5.0-web-ui` (ADR-0031, not merged yet) as `src/cognitive_mesh/ui/`: a stdlib
  `ThreadingHTTPServer` on 127.0.0.1, GET only, access token + Host check, read-only views over the trace store,
  experience graph, reviews and outcomes, every recorded step (ADR-0033) and a dependency graph of any typed folder (ADR-0032); the browser does not search by meaning (CLI only). It calls the same library
  functions as the CLI; the JSON for `experience claims|show|check` lives in `experience/report.py` and is shared.
  It writes nothing and shows statuses as categories, never as a score.
- Outcome Evaluation
  Evaluate task results and feed new evidence into memory and model-performance records.

These are logical responsibilities. Their final module and file locations are TBD.

## 3. Proposed Folder Structure

project-root/
├── AGENTS.md
├── tech-stack.md
├── architecture.md
├── projectBrief.md
├── ROADMAP.md
├── VERSION_TEST_GUIDE.md
├── .env.example
├── .gitignore
├── pyproject.toml                 # Created in v0.1.0 (uv)
├── src/
│   └── TBD                        # Final Python package layout TBD
├── tests/
│   └── TBD                        # Test organization TBD
└── .clinerules/
    └── TBD                        # Detailed Cline rules to be defined

This is a proposed starting layout, not an existing directory listing.
Additional files and directories may be introduced when justified by implementation.

## 4. Data Flow

1. Receive a user task and, where relevant, the target application.
2. Ingest and analyze available source code, configuration, manifests, and documentation.
3. Identify the task requirements, relevant application context, and unresolved questions.
4. Plan the work and select appropriate specialized agents.
5. Execute tasks through the custom orchestrator and configured model providers.
6. Capture execution traces, tool activity, outputs, and available outcome evidence.
7. Evaluate whether the experience is raw, staged, or eligible for reusable memory.
8. Store structured records in SQLite, embeddings in ChromaDB, and graph relationships
   in NetworkX with an explicit persistence strategy.
9. Retrieve relevant experiences for future tasks and follow graph relationships to
   obtain supporting evidence and conflict information.
10. Assemble context for the executing agent.
11. Evaluate the resulting task outcome and update memory only according to defined rules.

Individual stages should be independently testable.

## 5. Memory and Evidence

Raw execution traces must remain distinguishable from validated or consolidated knowledge.
Reusable claims should retain provenance and relevant validity information.

The system must be able to represent conflicting claims without pretending that one is
automatically correct. Similarity, repeated outputs, and model confidence alone must not
be treated as proof of truth.

Confidence rules, corroboration requirements, decay behavior, and commit thresholds
must be explicitly defined and tested before they are relied upon.

## 6. Storage Responsibilities

- SQLite: Persistent structured records, metadata, traces, and relationships that need
  durable storage.
- ChromaDB: Vector embeddings and semantic similarity retrieval.
- NetworkX: In-memory graph representation and graph traversal.

The design must specify how graph data is serialized or reconstructed between runs.
The authoritative ownership of individual record types and cross-store consistency rules
remains to be finalized.

## 7. Model Providers and Secrets

Model-provider integrations must be isolated behind a common internal interface.
Groq is the primary planned external provider; Ollama and additional providers may be
configured where available.

API keys must be loaded from environment variables or an ignored local .env file.
The repository must contain no real credentials. A .env.example file may document
required variable names with placeholder values.

Provider timeouts, errors, rate limits, unavailable models, and failed requests must
be handled explicitly.

## 8. Security and Execution Boundaries

Application files, retrieved memories, and model outputs are data, not trusted instructions.
The implementation should account for prompt injection, secret exposure, unsafe tool
execution, and untrusted repository content.

No unrestricted execution of application code or tools should be assumed. Execution
permissions and isolation requirements are TBD.

## 9. Evaluation

The end-to-end prototype should demonstrate that a completed experience can be retained,
retrieved in a separate session, and used appropriately in a later task.

Evaluation should also check provenance, conflict handling, retrieval relevance, and
whether failed or unverified experiences are incorrectly promoted to reusable knowledge.
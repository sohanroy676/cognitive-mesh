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
  plans are written by the caller as JSON. Nothing generates a plan from a user request yet.
- Custom Orchestrator
  Assign tasks, coordinate execution, manage results, and handle failures.
  Implemented in v0.5.0 as `src/cognitive_mesh/orchestrator/` (ADR-0010): sequential, assignment by
  capability, bounded retries, run budget, task status derived from step statuses. In-memory only.
- Model Provider Interface
  Route model requests to configured providers, including Groq and Ollama where available.
  Only the protocol and error types exist (v0.4.0, `src/cognitive_mesh/providers/`); no Groq or Ollama client yet.
  Planned: a real Ollama client in v0.6.1 (ADR-0012), then Groq in v0.6.2 (ADR-0013), before v0.7.0.
- Episodic Trace Store
  Preserve task executions, model/provider details, tool activity, outputs, and outcomes.
  Implemented in v0.6.0 as `src/cognitive_mesh/traces/` (ADR-0011): SQLite via stdlib `sqlite3` (private to
  `store.py`), runs/steps/attempts, redaction before storing, `interrupted` for runs that never finished.
  Opt-in (`run --trace-db`); traces are raw records and nothing retrieves them yet.
- Memory Validation
  Evaluate whether an experience is eligible for promotion into reusable knowledge.
- Vector Retrieval
  Use ChromaDB and embeddings to find semantically relevant past experiences.
- Knowledge Graph
  Use NetworkX to represent claims, entities, relationships, provenance, and conflicts.
- Context Assembly
  Combine retrieved evidence according to relevance, confidence, validity, and conflict status.
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
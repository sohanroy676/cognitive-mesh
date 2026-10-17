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

## Now — v0.3.0: Dependency Graph (done, next: v0.3.1)

Status: done 2026-10-17. Merged to main (`d172312`); GitHub Actions CI run 39010744225 passed. Not tagged. See ADR-0006.

- Represent application components and their dependencies (`cognitive_mesh.graph`, `cognitive-mesh graph`).
- Establish graph construction and traversal behavior.
- Test dependency links against known sample applications (`tests/fixtures/dep_app`).
- Known limitations: declared direct dependencies and Python imports only; lockfiles excluded so transitive external dependencies are unknown; JS/TS imports and dynamic imports not analysed; in-memory only.

## Next — Planned, Not Started

### v0.3.1 — JS/TS and Dynamic Import Analysis
- Extend the dependency graph to JS/TS `import` / `require` / dynamic `import()` statements.
- Detect Python dynamic imports (`importlib.import_module`, `__import__`) and record them as
  evidence-backed edges when the target is a literal, otherwise as unresolved.
- Parser choice (new dependency or restricted stdlib approach) to be decided in an ADR.
- Create the README update habit: `README.md` is refreshed every version.

### v0.4.0 — Agent Architecture
- Define agent interfaces, responsibilities, and capability descriptions.
- Establish the initial specialized agent roles.
- Test agent input/output contracts and error handling.

### v0.5.0 — Basic Orchestrator and Task Planning
- Implement the custom orchestration workflow.
- Represent tasks, dependencies, and execution results.
- Select agents based on task requirements and declared capabilities.
- Test planning, execution order, and failure paths.

### v0.6.0 — Episodic Trace Store
- Record task executions and their provenance.
- Persist relevant model/provider details, tool activity, outputs, and outcomes.
- Test trace integrity and persistence across separate sessions.

### v0.7.0 — Semantic Memory Retrieval
- Generate embeddings for eligible stored experiences.
- Use ChromaDB for semantic retrieval.
- Test retrieval relevance and behavior when no useful memory exists.

### v0.8.0 — Experience Graph
- Represent reusable experiences and relationships using NetworkX.
- Define graph serialization or reconstruction across sessions.
- Test relationship traversal and persistence behavior.

### v0.9.0 — Evidence-Aware Retrieval
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
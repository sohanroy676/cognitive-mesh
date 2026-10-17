# Version-by-Version Implementation and Verification Guide

## How to Use This Guide

For each version:
1. Implement only the stated scope and its necessary supporting work.
2. Run the tests for the current version and the relevant earlier regression tests.
3. Inspect the expected outputs instead of relying only on a successful process exit.
4. Record failures, limitations, and unresolved issues.
5. Mark a version complete only when its verification criteria pass.

Exact test commands are TBD until the project environment and test framework are configured.
Use deterministic fixtures and mocked external APIs where possible. Tests requiring real
providers should be identified separately and must not require committed credentials.

---

## v0.1.0 — Project Foundation

### Implement
- Establish the Python 3.12 project environment.
- Create the initial repository layout and package configuration.
- Select dependency management, testing, linting, and formatting tools.
- Establish configuration and environment-variable conventions.
- Add a minimal application entry point and a basic test.

### Test
- Set up a clean environment using the documented instructions.
- Install the declared dependencies.
- Import the application package.
- Run the basic test suite.
- Check that missing required configuration produces a clear error.
- Check that secret files are excluded from version control.

### Expected Output
- A clean setup completes using documented commands.
- The minimal entry point starts or exits successfully as specified.
- The basic test passes.
- No real API credentials are present in tracked files.
- Setup, run, and test commands are documented and reproducible.

### Fix If
- Setup depends on undeclared local packages.
- Imports fail in a clean environment.
- Tests pass only when run from an undocumented working directory.
- Credentials or local machine assumptions are embedded in the code.

---

## v0.2.0 — Application Ingestion

### Implement
- Define supported application inputs and ingestion boundaries.
- Read source code, configuration, dependency manifests, and documentation.
- Preserve source file references and ingestion errors.
- Avoid executing ingested application files as part of ingestion.

### Test
- Ingest a small fixture application with multiple file types.
- Verify discovered files and recorded source references.
- Test missing directories, unsupported files, unreadable files, and malformed inputs.
- Test exclusion rules for irrelevant or sensitive files.

### Expected Output
- Supported files are discovered and represented consistently.
- Each extracted observation can be associated with its source.
- Unsupported or unreadable inputs produce explicit diagnostics.
- Ingestion does not silently invent missing application details.

### Fix If
- Important files are silently skipped.
- Source references cannot be traced back to the input.
- Secrets or irrelevant generated files are ingested without defined controls.
- A malformed file causes an unexplained application-wide failure.

---

## v0.3.0 — Dependency Graph

### Implement
- Define graph nodes and edges for application components and dependencies.
- Build graph relationships from available manifests and supported evidence.
- Record where each discovered relationship came from.

### Test
- Use a fixture with known components and dependency relationships.
- Compare graph nodes and edges with the expected fixture.
- Test missing manifests, unresolved dependencies, and cyclic dependencies.
- Test graph traversal for direct and indirect relationships.

### Expected Output
- Known fixture dependencies are represented correctly.
- Unresolved relationships are visible rather than fabricated.
- Graph traversal returns expected connected components and dependencies.
- Dependency evidence can be traced to its source.

### Fix If
- Relationships appear without supporting input.
- Graph direction is ambiguous or inconsistent.
- Cycles cause traversal failures.
- Missing dependencies are mistaken for proof that no dependency exists.

---

## v0.3.1 — JS/TS and Dynamic Import Analysis

### Implement
- Parse JS/TS static imports, `require()` and literal dynamic `import()` into graph edges with evidence.
- Detect Python dynamic imports (`importlib.import_module`, `__import__`) with literal arguments.
- Represent non-literal or computed dynamic imports as unresolved, never as guessed edges.
- Decide and record the parser approach (ADR); vet any new dependency per the security rules.
- Never execute ingested code.

### Test
- Fixture with JS/TS files using ES imports, CommonJS, dynamic imports, type-only imports.
- Literal vs computed dynamic imports (Python and JS); commented-out and string-embedded imports.
- Malformed JS/TS, huge files, injection text in import strings.
- Regression: all v0.3.0 graph tests still pass.

### Expected Output
- Literal imports appear as edges with file, line and rule evidence.
- Computed dynamic imports are visible as unresolved or flagged, not omitted silently.
- Limits of the analysis (e.g. bundler aliases, tsconfig paths) are stated in diagnostics/ADR.

### Fix If
- Imports in comments or strings produce edges.
- Computed imports produce guessed targets.
- A malformed file aborts the whole graph build.

---

## v0.4.0 — Agent Architecture

### Implement
- Define a common agent interface.
- Define role descriptions, capabilities, input/output contracts, and failure behavior.
- Introduce the initial specialized agent roles selected for the project.
- Separate agent responsibilities from model-provider implementation details.

### Test
- Instantiate each implemented agent using controlled inputs.
- Validate required inputs and returned result structure.
- Test invalid inputs, provider failures, timeouts, and malformed outputs.
- Verify that unsupported capabilities are reported clearly.

### Expected Output
- Agents follow consistent interfaces.
- Their roles and capabilities are discoverable.
- Failures are represented explicitly.
- Agent logic does not require duplicated provider-specific code.

### Fix If
- The orchestrator must know every agent's internal implementation.
- Agents return incompatible result formats.
- Provider errors are mistaken for successful task results.
- An agent claims capabilities that it does not implement.

---

## v0.5.0 — Basic Orchestrator and Task Planning

### Implement
- Represent a task and its execution state.
- Plan task steps and dependencies.
- Assign tasks to suitable agents based on declared capabilities.
- Coordinate execution and collect results.
- Define behavior for failed, blocked, or incomplete tasks.

### Test
- Run a simple task with one agent.
- Run a task with multiple dependent steps.
- Test dependency ordering and missing dependencies.
- Test agent failure, invalid results, and unavailable providers.
- Test that the orchestrator does not mark a failed task as successful.

### Expected Output
- Tasks execute in a valid dependency order.
- Each step has an inspectable status and result.
- Failed steps produce explicit failure information.
- The final task result reflects the actual execution outcome.

### Fix If
- Dependent steps execute before their prerequisites.
- Failures disappear from the final result.
- The orchestrator assigns work to an agent without the required capability.
- Repeated failures create an uncontrolled execution loop.

---

## v0.6.0 — Episodic Trace Store

### Implement
- Define trace records for tasks and agent executions.
- Store relevant inputs, outputs, timestamps, model/provider identifiers, tool activity,
  status, and available environment information.
- Persist trace records using SQLite.
- Define how incomplete and failed executions are represented.

### Test
- Record a successful task and a failed task.
- Retrieve each trace by its identifier.
- Restart the application and retrieve previously stored traces.
- Test missing optional fields and malformed records.
- Verify that stored outputs retain their source and task association.

### Expected Output
- Traces remain available after restarting the application.
- Successful, failed, and incomplete executions can be distinguished.
- Trace records have stable identifiers and consistent fields.
- A trace can be associated with the task and agent that produced it.

### Fix If
- Data disappears between sessions.
- A failed execution is stored as successful.
- Trace fields are silently misassociated.
- Storage errors leave partially written or misleading records.

---

## v0.7.0 — Semantic Memory Retrieval

### Implement
- Define which trace content is eligible for embedding.
- Integrate the selected Sentence Transformers model.
- Store and retrieve embeddings using ChromaDB.
- Associate retrieved results with their original trace records.

### Test
- Index a small collection of known experiences.
- Submit queries with matching and nonmatching meaning.
- Verify that relevant experiences rank above unrelated fixture records.
- Test empty collections, duplicate records, and missing source records.
- Test embedding-model configuration and incompatible vector dimensions.

### Expected Output
- Relevant experiences can be retrieved through semantic similarity.
- Results identify the original records that support them.
- Empty or weakly relevant searches are handled explicitly.
- Similarity scores are not presented as proof of correctness.

### Fix If
- Retrieved results cannot be traced to their source.
- Irrelevant results are treated as authoritative.
- Embeddings from incompatible models are mixed.
- Re-indexing creates uncontrolled duplication.

---

## v0.8.0 — Experience Graph

### Implement
- Define graph nodes and relationships for experiences and reusable claims.
- Use NetworkX for graph representation and traversal.
- Define an explicit serialization or reconstruction mechanism for cross-session use.
- Link graph elements to their source traces.

### Test
- Create a fixture graph with known nodes and relationships.
- Traverse direct and indirect relationships.
- Restart the application and reconstruct the graph.
- Test missing nodes, invalid edges, and duplicate relationships.
- Verify that graph records preserve source references.

### Expected Output
- Expected graph relationships can be queried.
- The graph is available after a fresh application start.
- Relationships can be traced to their source records.
- Missing or invalid graph data produces visible diagnostics.

### Fix If
- The graph exists only in memory when persistence is required.
- Reconstructed relationships differ from saved relationships.
- Traversal returns unrelated or invalid evidence.
- Graph changes silently break links to source traces.

---

## v0.9.0 — Evidence-Aware Retrieval

### Implement
- Connect vector retrieval to graph-based evidence lookup.
- Represent provenance, confidence, validity, and known conflicts.
- Assemble retrieved information with supporting source references.
- Distinguish unverified experiences from validated reusable knowledge.

### Test
- Retrieve a relevant claim with supporting evidence.
- Retrieve claims with contradictory evidence.
- Test claims with missing provenance and expired validity information.
- Test cases where semantic similarity is high but supporting evidence is weak.
- Verify that unverified traces remain distinguishable from validated knowledge.

### Expected Output
- Retrieved claims expose their supporting records.
- Known contradictions are surfaced instead of silently discarded.
- Weak or missing evidence is identified.
- The system does not treat semantic similarity as factual validation.

### Fix If
- Conflicting claims disappear without explanation.
- Unverified outputs are promoted without satisfying defined rules.
- Confidence is shown without its intended interpretation.
- A claim cannot be traced to the evidence used to retrieve it.

---

## v1.0.0 — Core End-to-End Prototype

### Implement
- Connect application ingestion, analysis, task planning, agent execution, and orchestration.
- Record task experiences in persistent storage.
- Retrieve relevant experiences through vector search and graph evidence.
- Supply retrieved context to a later task.
- Record outcomes and expose the workflow for inspection.

### Test
- Run a task against a small fixture application.
- Confirm that the execution produces a trace.
- End the application session and restart it.
- Submit a related task in the new session.
- Verify that relevant past experience is retrieved and supplied as context.
- Inspect source links, conflicts, and task outcomes throughout the workflow.
- Test a failed execution and an unrelated follow-up task.

### Expected Output
- A complete workflow executes from task input to observable result.
- Experience survives a separate application session.
- A later task retrieves relevant previous experience.
- Retrieved evidence is inspectable.
- Failed executions and conflicting evidence are not silently reported as success.

### Fix If
- Memory works only within the original process.
- The second task cannot identify where retrieved information came from.
- Unrelated tasks receive misleading prior context.
- The end-to-end demo succeeds only with hardcoded results.

---

## v1.1.0 — Outcome Validation and Memory Revision

### Implement
- Define outcome validation rules.
- Use available outcome evidence to determine whether knowledge should be retained,
  weakened, revised, or flagged for review.
- Preserve historical records when knowledge is revised.

### Test
- Submit a successful outcome and a failed outcome for related claims.
- Test incomplete or ambiguous outcome evidence.
- Verify that prior evidence remains inspectable after revision.
- Test attempts to promote an experience that fails validation.

### Expected Output
- Memory changes follow explicit rules.
- Failed or ambiguous outcomes do not automatically reinforce claims.
- Revisions retain their history and provenance.
- Unsupported revisions are rejected or flagged.

### Fix If
- Every successful-looking model response increases confidence automatically.
- Failed results are erased.
- Memory changes cannot be explained from recorded evidence.

---

## v1.2.0 — Confidence, Corroboration, and Lineage Controls

### Implement
- Define confidence semantics and update rules.
- Track source lineage and corroboration.
- Establish criteria for independent evidence.
- Preserve conflicting claims rather than automatically resolving them.

### Test
- Submit repeated outputs from the same source.
- Submit evidence from sources considered independent under the defined rules.
- Test contradictory evidence and missing lineage.
- Verify confidence changes against documented rules.

### Expected Output
- Repeated dependent outputs are not mistaken for independent corroboration.
- Confidence changes are explainable.
- Conflicts remain discoverable.
- Claims retain their source lineage.

### Fix If
- Repeated copies of one claim count as independent evidence.
- Confidence rises without a recorded reason.
- Contradictions are removed to simplify retrieval.

---

## v1.3.0 — Temporal and Environment Awareness

### Implement
- Record relevant environment identifiers and validity information.
- Define how outdated or environment-dependent knowledge is treated.
- Establish rules for marking knowledge stale.

### Test
- Retrieve a claim in the environment where it was recorded.
- Change a relevant environment fixture and repeat retrieval.
- Test expired, undated, and environment-independent claims.
- Verify that stale information is identified according to defined rules.

### Expected Output
- Environment-dependent knowledge can be distinguished from generally applicable knowledge.
- Expired or potentially stale records are handled explicitly.
- The system does not assume that an old observation remains valid indefinitely.

### Fix If
- Environment changes are ignored where they matter.
- Every old claim is discarded regardless of its validity.
- Staleness is inferred without a documented rule.

---

## v1.4.0 — Advanced Context Assembly and Stateful Task Handling

### Implement
- Assemble context using relevance, confidence, provenance, validity, and conflict information.
- Define retrieval behavior for precision-critical and recall-critical tasks.
- Define when task continuity or retained execution state is necessary.

### Test
- Compare context assembly for tasks with different retrieval requirements.
- Test conflicting, redundant, and weakly relevant evidence.
- Test context limits and missing evidence.
- Test a stateful task that requires information from an earlier step.

### Expected Output
- Context contains relevant evidence and source references.
- Selection behavior follows the task's defined retrieval requirements.
- Important conflicts are not hidden merely to reduce context size.
- Stateful tasks preserve required information without assuming every task needs a single long-lived agent.

### Fix If
- Context selection is unpredictable or undocumented.
- Relevant conflicts disappear during compression.
- State is lost between dependent task steps.

---

## v1.5.0 — Model Performance Tracking and Routing

### Implement
- Track model/provider performance by task type.
- Record available outcome, token-cost, latency, and sample-count data.
- Define routing behavior and provider fallback rules.
- Introduce exploration only after the required tracking data and routing policy exist.

### Test
- Route tasks when multiple providers are configured.
- Test when only one provider is available.
- Simulate rate limits, timeouts, and provider failures.
- Verify that performance records are associated with the correct model and task type.
- Test routing with insufficient sample data.

### Expected Output
- Routing decisions are inspectable and reproducible under controlled conditions.
- Unavailable providers do not cause silent task success.
- Performance measurements are not mixed across unrelated task types.
- Routing handles insufficient evidence without claiming a proven best model.

### Fix If
- Missing metrics are treated as zero-cost or zero-latency success.
- One model's results are attributed to another.
- Routing depends on unavailable providers without a fallback.
- A small or biased sample is treated as conclusive evidence.

---

## Final Verification Checklist

Before marking any version complete, confirm:

- [ ] The implementation matches the documented scope.
- [ ] Tests cover expected behavior and important failure cases.
- [ ] Persistent data remains correct after restarting the application, where applicable.
- [ ] Outputs can be traced to their sources where evidence is required.
- [ ] Secrets are not hardcoded or committed.
- [ ] Earlier relevant tests still pass.
- [ ] Documentation describes actual behavior, not planned behavior.
- [ ] Remaining limitations and known defects are recorded.
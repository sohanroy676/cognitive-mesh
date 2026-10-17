# ADR-0015: Experience graph over stored traces

## Status
Accepted (design choices confirmed by the user in plan mode 2026-10-17; text, including the lazy `memory/__init__.py` amendment, reviewed and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.6.0 stores raw run traces in SQLite (ADR-0011) and v0.7.0 finds them by meaning (ADR-0014). Nothing yet
records how experiences relate to each other or to the things they were about. v0.8.0 adds a graph of
experiences, the entities they touched, and the claims their deterministic outputs state, so later versions
(v0.9.0 evidence-aware retrieval) can follow relationships to supporting evidence and conflicts.

Constraints from the project brief and earlier ADRs:
- NetworkX is an in-memory structure and is not the database (ADR-0006 keeps it private behind pydantic models
  and leaves serialization to v0.8.0).
- Raw traces stay distinguishable from validated knowledge. Similarity, repetition and model confidence are not
  proof of truth.
- The SQLite trace database stays authoritative, and `TraceStore.open_existing` refuses an older schema
  version. Adding tables to it would force every reader to upgrade first.
- Everything must work without a model provider and without network access.
- `graph/` already means the application dependency graph, so this feature gets its own package.

## Decision
- **Package.** `src/cognitive_mesh/experience/` with `models.py` (frozen pydantic, `extra="forbid"`), `build.py`
  (pure: trace store in, snapshot out), `store.py` (the only module importing `sqlite3` for the graph file),
  `graph.py` (the only module importing `networkx`; wraps `nx.MultiDiGraph`), `verify.py` (source re-checks).
- **Nodes.** Ids are deterministic and clipped: `run:{run_id}`, `step:{run_id}/{step_id}`,
  `attempt:{run_id}/{step_id}/{n}`, `capability:{name}`, `agent:{id}`, `target:{node_id}`, `claim:{hash}`.
  Attempt nodes exist only for the final attempt of steps that ADR-0014 treats as eligible, and carry the
  v0.7.0 `doc_id` and `text_sha256`, so a graph node and a vector index entry share one key.
- **Edges.** `has_step`, `final_attempt`, `used_capability`, `ran_by`, `about`, `evidenced_by` (claim to
  attempt), `subject` and `object` (claim to target). Every edge carries at least one source reference
  (run id, plus doc id and hash where it applies), as ADR-0006 requires evidence on every dependency edge.
  An edge without a source reference cannot be constructed.
- **Claims.** A claim node exists only when a step's final attempt succeeded with `basis = deterministic`.
  Depth-1 items of `dependencies_of` give `depends_on(subject, object, edge_kind)`; `unresolved_report` gives
  `is_unresolved(node_id)`. Transitive items are not claims. `model_unverified` output, failed steps and
  blocked steps never produce a claim. A claim id is a hash of its subject, predicate and object, so the same
  statement from several runs is one node with several `evidenced_by` edges. Each such edge records the run's
  `source_fingerprint`, so observations of different code versions stay distinguishable. Repetition is counted
  and shown, and is never treated as validation. Every claim is `unvalidated` in v0.8.0; promoting a claim is
  later work.
- **Persistence.** A separate SQLite file chosen with `--graph FILE` (no default), stdlib `sqlite3`, tables
  `meta`, `nodes`, `edges`, `edge_refs`, with its own schema version. The trace database stays at version 1
  and `MIGRATIONS` is not touched. The graph file is a derived copy: `experience build` rebuilds it from the
  trace database in one `BEGIN IMMEDIATE` transaction, so a rebuild is idempotent and never leaves duplicate
  rows. Readers use `query_only` and never create a file.
- **Reload check.** `meta` stores a sha256 over the canonical, sorted node and edge list. On open it is
  recomputed, and the open is refused on a mismatch, on a newer schema version, on a file that is not a graph
  file, on a duplicate edge, or on an edge whose endpoint is missing. The rules mirror the trace-store open
  rules.
- **Source status.** After loading, each attempt and run reference is re-checked against the trace database,
  reusing the ADR-0014 check (`store.get`, rebuild the eligible documents, compare doc id and hash). The
  result per element is `ok`, `source_missing`, `source_changed` or `source_unreadable`. It is computed on
  every load and never stored as truth.
- **Dangling sources: keep, mark, diagnose.** Elements with a bad source status are kept and flagged, a
  diagnostic names the code and the node id (never stored values), and they are left out of "supported"
  results unless `--include-unverified` is given. Nothing is deleted silently. Malformed runs in the trace
  database become diagnostics at build time and contribute no nodes.
- **Shared check.** The ADR-0014 hit re-check moves into one small function in `memory/documents.py` that both
  `MemoryIndex` and `experience/verify.py` call, so there is one definition of "source still matches". Memory
  behaviour does not change. Checked 2026-10-17: importing `cognitive_mesh.memory.documents` currently runs
  `memory/__init__.py`, which imports `MemoryIndex` and so `chromadb` (and the embedder module). To keep
  `experience` commands light, `memory/__init__.py` re-exports `MemoryIndex`, `SentenceTransformerEmbedder`
  and the other heavy names lazily (module `__getattr__`, with a `TYPE_CHECKING` import so mypy still sees
  them). A test asserts that importing `cognitive_mesh.experience` leaves `chromadb` and `sentence_transformers`
  out of `sys.modules`. Public names stay importable as before.
- **CLI.** `experience build|show|claims|check`, each with `--graph FILE` and `--db TRACE`, and `--json` where
  the output is structured. New exit codes: 12 for a graph store error (missing file, bad hash, invalid edge,
  newer version), 13 for an unknown node. Trace database errors keep exit 8. All printed ids pass through the
  existing `_printable` helper.
- **Dependencies.** None added. NetworkX is already a runtime dependency (ADR-0006).

## Amendments (made while implementing, 2026-10-17)
- **File identity.** A trace database also has `user_version = 1`, so the graph file is marked with
  `PRAGMA application_id` (`0x434D4547`). `load` and `save` refuse any file without it, and a trace database
  is never written to.
- **Hash scope.** The content hash also covers the build diagnostics, so they cannot be removed from the file
  unnoticed. References on an edge have one canonical order (`ref_key`).
- **Duplicate edges.** The schema makes `(source, target, kind)` the primary key, so a duplicate edge cannot be
  stored; repeated evidence is a second row in `edge_refs`. `save` also refuses duplicate nodes and edges.
- **Extra diagnostics.** `run_not_finished` (an interrupted run adds no nodes) and `unusable_output` (a
  deterministic output that does not match its schema gives no claims) join `malformed_run` and the three
  `source_*` codes.
- **Derived statuses.** Attempts are checked with `memory.documents.verify_source`. A run is `source_missing`,
  `source_unreadable`, or `source_changed` (it exists but no longer yields documents). A step takes its final
  attempt's status. A claim is `ok` while at least one attempt that stated it still matches, and the number
  of matching attempts is reported. Capability, agent and target nodes take the best status among the
  attempts and claims that point at them.
- **CLI additions.** `experience show --incoming` follows edges backwards, and `experience check` exits 14 when
  any source is not `ok` (12 stays for graph file errors, 13 for unknown nodes). `experience build` exits 8 after
  writing the file when a malformed run was skipped, as `memory index` does.
- **Lazy import scope.** Only `MemoryIndex` was heavy: `embedder.py` already imports `sentence_transformers`
  inside its loader. The test covers both modules.

## Alternatives considered
- **Tables in the trace database (`MIGRATIONS[1]`).** One file, but the trace database becomes version 2 and
  every existing reader refuses it until upgraded. It also mixes raw records with derived data.
- **NetworkX node-link JSON file.** Easy to inspect, but whole-file rewrites, no transaction, and no natural
  place for per-edge source references.
- **Experiences only, no claims.** Simpler, but the roadmap asks for reusable claims and v0.9.0 needs something
  to attach evidence and conflicts to.
- **Claims from model output, tagged `model_unverified`.** Wider coverage, but it puts model text into the
  structure meant to support knowledge. Deferred until validation rules exist.
- **Hash only, or rebuild-and-diff on every load.** The first leaves dangling sources unnoticed unless a
  separate command is run; the second makes the stored copy pointless.
- **Prune dangling elements on rebuild, or refuse to load.** Pruning loses the record that something was once
  claimed; refusing makes one deleted run block the whole graph.

## Consequences
- The graph is available after a fresh start and is checkable against both its own hash and the trace
  database. A damaged graph file is refused with exit 12; a changed trace database shows up as marked
  elements, not as a failure.
- Claims are visible, countable and traceable to their attempts, and they are explicitly `unvalidated`. The
  corroboration, confidence and conflict rules arrive with v0.9.0 and must be defined and tested first.
- Only two predicates exist (`depends_on`, `is_unresolved`), because only two deterministic capabilities
  exist. New deterministic agents need an explicit mapping before they produce claims.
- The claim id ignores which code version was observed, and the fingerprint lives on the evidence edge. Two
  observations of different code versions are therefore one claim with two fingerprints, and v0.9.0 decides
  how to treat that.
- Two derived stores now exist (vector index, graph file). They can disagree with each other and with the trace
  database until rebuilt; the shared check and `experience check` report this and do not repair it.
- Unknown or large inputs: a very large trace database makes `build` slow and memory-bound (the snapshot is
  in memory). No limit is set in v0.8.0; this is recorded as unverified.

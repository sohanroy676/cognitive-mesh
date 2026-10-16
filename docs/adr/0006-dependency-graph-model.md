# ADR-0006: Dependency graph model, provenance and unresolved handling

## Status
Accepted

## Date
2026-10-17

## Context
v0.3.0 must represent application components and their dependencies, record where every
relationship came from, keep unresolved relationships visible, and survive cycles. The input is
only the in-memory `IngestionResult` from v0.2.0 (ADR-0005). Lockfiles are excluded by ingestion.

## Decision
- **Direction:** an edge `A -> B` always means "A depends on / imports B". Reverse queries
  (`dependents`) exist, but no edge is ever stored the other way.
- **Nodes:** `component` (a directory holding a supported manifest; id `component:<dir>`),
  `package` (declared external dependency; `pkg:pypi:<PEP 503 name>` / `pkg:npm:<name>`),
  `module` (ingested Python file; `module:<path>`), `stdlib` (top-level name found in
  `sys.stdlib_module_names` of the running interpreter), `unresolved` (never guessed).
  One component per directory, so `pyproject.toml` and `requirements.txt` side by side merge.
- **Edges:** `declares` (component -> package/unresolved), `imports` (module -> module / stdlib /
  unresolved), `contains` (component -> module, by directory only; deepest component wins).
- **Provenance:** every `Edge` carries one or more `Evidence` (`SourceRef`, line when locatable,
  rule, cited text). The model rejects an edge with no evidence. The same relationship stated in
  several files is one edge with merged evidence.
- **Sources parsed (stdlib only, parse-only, never executed):** `pyproject.toml`
  (`project.dependencies`, `optional-dependencies`, `dependency-groups`), `requirements*.txt`,
  `package.json` (four dependency sections), Python `import` / `from ... import` via `ast.parse`.
- **Unresolved, not fabricated:**
  - Requirement lines that are options or URLs (`-r`, `-e`, `git+https://...`) or not understood
    become `unresolved:requirement:*` nodes plus a diagnostic.
  - Imports matching no ingested file and not stdlib become `unresolved:import:<top-level name>`.
    They are **not** linked to a declared package (import name != distribution name, e.g.
    `yaml` / `PyYAML`). An exact normalized-name match is only a labelled hint
    (`candidate_package`), never an edge.
  - An import matching several ingested files is ambiguous: unresolved plus a diagnostic.
  - Manifests ingestion could not parse, or whose layout is unsupported (poetry, `setup.py`,
    `go.mod`, ...), yield a diagnostic and no relationships.
  - No ingested manifest => `no_manifest` warning stating this is not proof of no dependencies.
  - Because lockfiles are excluded, a `transitive_unknown` diagnostic is always emitted:
    dependencies *of* declared external packages are unknown, not absent.
- **Cycles:** traversal is breadth-first with a visited set (iterative, no recursion). The start
  node is never listed as its own dependency; `cycles()` lists cycles separately (including
  self-imports) with a limit and an explicit `truncated` flag.
- **Library:** NetworkX 3.7 (BSD-3, official project, released 2026-09-21) with `types-networkx`
  (typeshed) in dev for strict mypy. NetworkX is private to `cognitive_mesh.graph.graph`; the public
  API returns frozen pydantic models. Serialization across sessions is not decided here (v0.8.0).
- **CLI:** `cognitive-mesh graph PATH [--json] [--deps-of NODE_ID [--transitive]]`. Exit 3 for an
  unusable root (as `ingest`), 4 for an unknown node id.

## Alternatives considered
- Ingest lockfiles to get transitive edges: widens ADR-0005 (new file kind, size limits, per-format
  parsers). Deferred; the graph reports transitive dependencies as unknown instead.
- Link imports to packages by name match: produces confident-looking wrong edges. Rejected.
- JS/TS import parsing: needs a real parser dependency. Out of scope; those files are not analysed.
- Component per manifest file: duplicates one project when two manifests describe it.

## Consequences
- The graph covers declared direct dependencies and Python-internal imports only.
- `sys.stdlib_module_names` reflects the interpreter running the tool (3.12), not the target app.
- Dynamic imports (`importlib`, `__import__`), conditional imports and `sys.path` tricks are not
  modelled, so a missing import edge is not proof of no dependency.
- Line numbers for TOML/JSON declarations are a best-effort text search and may be `None`.
- Graph results are in-memory only.

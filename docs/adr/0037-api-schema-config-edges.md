# ADR-0037: API contract, shared schema and config edges across projects (v1.7.0)

## Status
Proposed 2026-11-03. Waits for the user's acceptance; no code until then. Extends ADR-0036 (workspace); the matching rule follows ADR-0006 (nothing is linked by guesswork). Needs ADR-0005 (never execute, sensitive files never opened) and ADR-0007 (tree-sitter private to `graph/js_parse.py`) to stay in force.

## Date
2026-11-03

## Context
`docs/extended_roadmap.md` v1.7.0 adds three more kinds of cross-project edge to the v1.6.0 `package` edge: API contracts, shared schemas and shared configuration names, plus `workspace deps PROJECT`. Each needs detection rules, a definition of "match", and stated limits, because each can produce wrong links if the rule is loose. Decisions made with the user on 2026-11-03: PyYAML becomes a direct dependency; path matches are labelled rather than hidden; generic environment variable names still link but are marked.

## Decision

### 0. Shared frame
- Edges are computed on every call from ingestion, as `workspace/graph.py` does today. Nothing new is stored; the workspace file schema is unchanged; exit code 19 is reused.
- Each kind is a pure module over `IngestionResult` (`workspace/contracts.py`, `workspace/schemas.py`, `workspace/config_names.py`). `WorkspaceGraph` gains one tuple per edge kind and an `unresolved` tuple.
- Input is the redacted ingestion content. Everything is parse-only and never executed (ADR-0005).
- Every edge carries evidence on both sides (project, path, line, short detail), like `ManifestRef`. If several projects could be the provider, the edge is `ambiguous`, all candidates are listed and none is chosen (as ADR-0036 section 3).
- Detected edges stay distinct from `person` declared links in every output.

### 1. YAML
PyYAML (already in `uv.lock` at 6.0.3 as a transitive dependency; MIT) becomes a direct runtime dependency, and `types-PyYAML` a dev dependency. Only `yaml.safe_load` is used, in one private module (`workspace/yaml_read.py`), behind the existing per-file size limit, with a nesting-depth limit. Malformed or oversized YAML gives a diagnostic and no edges from that file, never a crash. Dependency checks from `01-security.md` are done before adding it.

### 2. API contracts (`api` edges, consumer -> provider)
Provider side:
- OpenAPI 3 and Swagger 2 files (JSON or YAML with a top-level `openapi` or `swagger` key): each `paths` entry x method; a `servers` / `basePath` prefix is applied only when it is a literal path.
- Route declarations with literal paths: FastAPI (`@app.get("/p")`, `APIRouter(prefix=...)`), Flask (`@app.route("/p", methods=[...])`, `Blueprint(url_prefix=...)`), Express (`app.get('/p')`, `router.get`). A prefix counts only when it is a literal in the same file. A router mounted elsewhere keeps its routes with label `prefix_unknown`, and they are not matched.

Consumer side (literal method and path only):
- `requests.<method>(url)`, `httpx.<method>` and client objects, `fetch(url, {method})` (default GET), `axios.<method>` and `axios({method, url})`.
- A URL is accepted when it is a literal, or a template whose only placeholder is a leading base (`f"{BASE}/users"`, `` `${API}/users` ``), labelled `base_unknown`. Any other computed URL is unresolved as `computed_url`.
- Python is read with `ast`. JavaScript and TypeScript are read with tree-sitter, with the new matchers added inside `graph/js_parse.py` only.

Match rule: methods are equal (case-insensitive) and path segments align after removing the query and normalising a trailing slash. Every edge carries one label:
- `exact`: identical literal segments.
- `param_style`: both sides have a parameter at the same segment, in any syntax (`{id}`, `<int:id>`, `:id`).
- `value_for_param`: the consumer has a literal value where the provider has a parameter (`/users/42` against `/users/{id}`).
The host is never used to choose a provider. Same path with a different method is not an edge; it is listed as a near miss (`method_mismatch`) naming the provider route.

### 3. Shared schemas (`shared_schema` edges, symmetric)
- Sources: SQL DDL and migration files (`.sql`; `CREATE TABLE` and `ALTER TABLE ... ADD / RENAME COLUMN` found with regular expressions after stripping comments, no SQL parser); SQLAlchemy (`__tablename__ = "x"`, `Table("x", ...)`); Prisma (`model X`, `@@map`; `.prisma` is added to ingestion classification as source); JSON Schema (a `$schema` key; linked only by an equal `$id`; files without `$id` are listed, not linked).
- Match: the table name, case-insensitive for unquoted identifiers, schema-qualified names compared whole. Each side is labelled with its role: `defines` (DDL / migration), `maps` (ORM), `describes` (JSON Schema).
- Columns are listed per side. A difference (one side has a column the other lacks) is shown as `column_difference` on the edge. It is not a compatibility verdict. Migration order is lexical file order, stated as approximate.

### 4. Config names (`config` edges, reader -> setter)
- Readers with literal names: `os.environ[...]`, `os.environ.get`, `os.getenv`, `process.env.X`, `process.env["X"]`. A computed name is unresolved.
- Setters: `.env.example`, `.env.sample`, `.env.template` (names only); compose `environment:` (map and list forms) and `env_file` entries (the file name only; it is not opened if it is a sensitive file); Dockerfile `ENV` and `ARG`.
- Real `.env` files are never opened (ADR-0005). **No value is ever stored.** Extractors keep names only. A name that itself looks like a secret (the existing random-token test in `ingestion/secrets.py`) is dropped and counted in a diagnostic.
- Generic names come from a fixed, documented list (PORT, HOST, DEBUG, NODE_ENV, LOG_LEVEL, PATH, HOME, ENV, TZ, and similar). They still produce edges, marked `generic` and shown in a separate group.
- A name set by a project and read by nobody in the workspace is listed as `unused_setting`.

### 5. Unresolved items
Every consumer call, env read or schema use without a match is listed under its project with a reason (`no_provider`, `computed_url`, `prefix_unknown`, `base_unknown`, `method_mismatch`, `no_setter`, `no_matching_table`). Nothing is dropped silently, as in ADR-0006.

### 6. CLI
- `workspace deps PROJECT [--json] [--workspace FILE]` prints, in plain sentences, outgoing and incoming edges of all kinds ("web calls GET /users on api (param_style); evidence: web src/client.py:12, api app/routes.py:30"), then declared links labelled `person`, then unresolved items. It opens the workspace file only if it exists.
- `workspace graph` shows the new kinds in text, JSON and Mermaid, with kind and label on each edge.

## Alternatives considered
- Match by path alone: rejected, method distinguishes different operations.
- Fuzzy or best-guess path matching: rejected (roadmap Fix-if); the three labels make every looseness visible instead.
- A SQL parser dependency: rejected for v1.7.0; regular expressions miss dialect features, which shows up as a missing edge, not a wrong one.
- JSON-only specs, no YAML: rejected by the user; most OpenAPI and compose files are YAML.
- Not linking generic env names: rejected by the user; it would hide real links. They are marked instead.

## Consequences
- Detection is only as good as literal patterns: gateways, rewrites, reverse proxies, GraphQL, gRPC, runtime-built routers, computed URLs and dialect-specific SQL stay unresolved or unlinked, and the miss is visible.
- `value_for_param` can link a call to the wrong route of the same shape; it is labelled, not hidden.
- One new direct dependency (PyYAML); YAML from other projects is untrusted data, read with `safe_load` only.
- A secret value in a config file is never stored; a secret-looking name is dropped.
- Not covered: GraphQL / gRPC / event contracts, version-aware spec comparison, runtime or traffic evidence, an HTML workspace view.
- Tests required (from the roadmap): matching route, near miss (method, parameter style), computed URL, shared table, renamed column, env var set but never read, large OpenAPI file, malformed spec, secret value never stored.

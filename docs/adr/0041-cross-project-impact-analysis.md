# ADR-0041: Cross-project impact analysis and contract diff (v1.11.0)

## Status
Accepted by the user 2026-11-04 (drafted in plan mode; the four marked user decisions were taken before drafting). Implementation in progress on `feature/extended-roadmap-v1.7`. Extends ADR-0036 and ADR-0037 (workspace and contract edges) and ADR-0040 (roles, `writes`). ADR-0005 (never execute, sensitive files never opened) and ADR-0007 (tree-sitter private to `graph/js_parse.py`) stay in force.

## Date
2026-11-04

## Context
Extended roadmap v1.11.0 asks for three things: given a target in the current project, list every workspace project and location that depends on it, with evidence; a read-only **reader** role that summarises the relevant files of those projects; and a **contract snapshot and diff** that labels each difference a dependent uses as `breaking`, `compatible` or `unknown`. v1.12.0 (coder, worktree, pull request) will call the diff as its impact check.

Facts from the code (read 2026-11-04):
- Workspace edges are recomputed on every call (`workspace/graph.py::build_workspace_graph`) from `ProjectFacts` (`workspace/facts.py`). Routes keep method, path and site. `TableDef.columns` holds column names only. Package edges keep the raw requirement text in `ManifestRef.detail`; no published version, no parsed version, no exports are kept.
- There is no read-only accessor class. GitHub clones are made read-only on disk (`fetch._make_read_only`); local projects are not. Projects are only read through `ingest_application`.
- `AgentContext` (`agents/models.py`) holds ingestion, graph and recalled pack. Agents never touch disk. `CapabilitySpec.writes` is checked only in `orchestrator/conduct.py`. `AGENT_ROLES` is in `providers/catalog.py`.
- `task --conduct` loads the workspace graph and discards it after summarising.
- Only `dependencies_of` and `unresolved_report` produce claims (`experience/build.py::_add_claims`); other capabilities are unmapped.
- `workspace/fetch.py::run_git` runs git with a fixed argument list and a sanitised environment. Highest exit code is 22.

Constraints unchanged: memory is evidence, not truth; model-written text is `model_unverified`; files, memories and model output are data, never instructions; no secret value is stored or printed; default tests need no network.

## Decision

User decisions (2026-11-04):
1. **Change input:** a git ref against the working tree (`--base REF`, default `HEAD`).
2. **Schema depth:** column names, types and nullability (SQL DDL, SQLAlchemy, Prisma); JSON Schema properties and `required`.
3. **Package contract:** version and exports.
4. **Reader role** is part of v1.11.0.

Design:

0. **Frame.** A new package `src/cognitive_mesh/impact/` of pure functions over ingestion results and workspace facts. Nothing new is stored: no trace, graph, index or workspace schema change. New exit code **23 `EXIT_IMPACT_ERROR`** (bad target, bad ref, root not in a git repository, current project not local, export over the size cap). Extractor extensions stay in `workspace/` (schemas, a new `exports.py`); JS matchers stay inside `graph/js_parse.py`.

1. **Read-only accessor.** `workspace/access.py` defines `WorkspaceAccess`, built from the workspace file, the current project name and the existing resolver. `reader(name)` returns a `ProjectReader` that exposes the file list, redacted content and sha256 from ingestion and has no path-returning or write method. `require_writable(name)` raises `ReadOnlyProjectError` for every project except the current one; v1.12.0 is its only future caller. `AgentContext` gains `workspace: WorkspaceAccess | None`. Sensitive files stay unopened (ADR-0005). Local projects other than the current one are not made read-only on disk; this is stated as a limit.

2. **Targets** (`--target`): `file:PATH`, `route:METHOD /path`, `table:NAME`, `env:NAME`, `package`, `symbol:NAME` (an exported name). A file target expands to every contract item whose provider-side site is in that file, plus exports defined there. An unknown form or a target not found in the current project is exit 23; the message names the form, never the given text beyond it.

3. **Dependents.** The listing is the workspace edges where the current project is the provider side (API target, config setter, package publisher, either side of a shared schema, an export imported by name), each with both sites. Also listed, as `unknown`, are dependents' unresolved items that could reach the target (`computed_url`, `base_unknown`, `no_provider` calls, computed env names, a bare `import pkg` without names). Nothing is dropped. Declared `person` links are shown apart, as before.

4. **Contract snapshot** (`impact/snapshot.py`): routes; tables with column name, normalised type text, nullability and default presence; JSON Schema properties (type) and `required`; config names the project sets; package name, version and exports. Python exports are a literal `__all__`, otherwise the public top-level definitions and re-exports of the package `__init__.py`, with the package found by layout (`unknown` if not exactly one). JS exports come from the entry (`exports["."]`, `main`, `index`) and its `export` statements; `export *` or a computed export makes the list incomplete. Dependents' imported names are read from Python `from pkg import a` and JS named imports and `const {a} = require('pkg')`.

5. **Base export.** `--base REF` must not start with `-`. It is resolved with `git rev-parse --verify REF^{commit}` to a full commit id through `run_git`, then exported with `git archive` of the root's path into a temporary directory using `tarfile` with `filter="data"`, a total size cap and the ingestion limits. The repository is never written (no worktree, no checkout). The current project must be local. The temporary directory is removed afterwards. The "after" side is the working tree.

6. **Verdicts.** Each difference between the base and the working-tree snapshot is matched against the dependents' usage of the **base** contract. A difference a dependent uses gets a verdict with the dependent's site and the provider's site. A difference nobody uses is listed as `unused`.
   - **API:** route removed, or method or path changed: `breaking`. Route added: `compatible`. A used route that becomes `prefix_unknown` or `unsupported_pattern`: `unknown`. Request and response bodies are not compared (stated).
   - **Table:** table removed, used column removed or renamed, or base type changed: `breaking`. Only length or precision changed, or nullability changed: `unknown`. Added nullable or defaulted column: `compatible`. Added `NOT NULL` column without a default: `unknown`. A dependent side without a column list: `unknown`.
   - **JSON Schema:** used property removed or type changed: `breaking`. Added optional property: `compatible`. Added required property: `unknown`.
   - **Config:** a name the current project set and a dependent reads is no longer set: `breaking` (other setters in the workspace are listed). Generic names are marked. A name added: `compatible`.
   - **Package:** published name changed: `breaking`. The new version against each dependent's declared spec: outside is `breaking`, inside is `compatible`, an unparseable spec or a missing version is `unknown`. PEP 440 specs use `packaging` (already locked as a transitive dependency, 26.3; it becomes a direct dependency after the `01-security.md` check). npm specs use a small hand-written subset: exact, `^`, `~`, comparison pairs, `x` and `*`; `||`, hyphen ranges, tags and pre-releases are `unknown`. A used export removed: `breaking`; an export added: `compatible`; a dependent that imports the whole module, or an incomplete export list: `unknown`.
   - **Presenting `unknown`:** it is its own group, always with a reason code and both sites. The summary line counts breaking, unknown and compatible separately. `unknown` is never folded into `compatible`. Every `compatible` names the usage evidence it rests on; without evidence the verdict is `unknown`.

7. **Capabilities.**
   - `dependency_analyst.dependents_of`: deterministic, input is a target, needs `ctx.workspace`, otherwise a fixed `missing_context` failure.
   - A new agent **`reader`** (role `reader`, added to `AGENT_ROLES`) with capability `summarise_dependents` (`uses_model=True`, `writes=False`). Code picks the files from the `dependents_of` sites under stated caps (files, characters per file, total). They are sent as JSON data with the untrusted-data system prompt, as `code_explainer` does. Output is a summary per project with cited paths; a cited path that was not sent is refused. Output is `model_unverified`, and evidence refs name `PROJECT:path` and the sha256.
   - Neither capability maps to claims (an explicit comment in `experience/build.py`, as for `compare_with_recalled`).
   - The contract diff is a library function and a CLI command, not a capability: it needs a git export and agents never touch disk. v1.12.0 calls the library.

8. **CLI.** `workspace impact PROJECT --target T [--json]` and `workspace contract-diff PROJECT [--base REF] [--json]`, both with `--workspace FILE`. `run` and `task` (plan and conduct modes) accept `--workspace FILE --project NAME` to fill `ctx.workspace`. The conductor payload lists the new capabilities like any other.

9. **Not in v1.11.0:** HTTP request and response bodies, GraphQL, gRPC, runtime evidence, stored impact reports, a UI view, a capability for the diff, claims from impact results, OS-level write prevention for local non-current projects (only the accessor).

## Alternatives considered
- **A git worktree for the base:** writes under `.git/worktrees`; `git archive` leaves the repository untouched.
- **Two folders (`--before`, `--after`):** no git needed, but v1.12.0's flow is a ref and a worktree; the person would have to prepare copies.
- **Names-only schemas:** a type change would be invisible, not even `unknown`. Rejected by the user.
- **Version only for packages:** a removed function used elsewhere would go unseen. Rejected by the user.
- **Deterministic only, reader deferred:** rejected by the user.
- **Storing impact reports:** no consumer until v1.12.0.

## Consequences
- Detection stays as good as literal patterns (ADR-0037 limits carry over); `unknown` will be common, and that is intended.
- Verdicts judge only what the extractors can see; "compatible" means "no evidence of breakage in the contracts we extract", and the output says so.
- One dependency moves from transitive to direct (`packaging`).
- `ALTER COLUMN ... TYPE` and dialect-specific types are read by regular expressions; a miss shows as `unknown` or a missing difference, and is listed in the limits.
- The reader's summary is a model's reading of other projects' files: unverified, and it never validates a verdict.
- Tests required (from the roadmap): renamed route is breaking, optional addition is compatible, computed route is unknown, removed env var, changed column type in a shared table, an attempt to write another project raises; plus bad ref, ref starting with `-`, non-git root, GitHub project as current, oversized archive, reader refusing uncited paths, and no secret value in any output.

## Implementation notes (2026-11-04)
- Built as written, with these differences found while building:
  - **Base export** runs `git archive --format=tar --output=FILE COMMIT -- .` with the project folder as the working directory. The `COMMIT:path` form drafted above archives nothing when git is run from a subfolder, so a project inside a larger repository (a monorepo folder) is exported by running inside it. A test covers that case. The ref must match a fixed pattern (`fullmatch`), and git refusing the repository and git refusing the ref give different messages.
  - **`workspace/graph.py`** gained `ProjectRead` (project, root, ingestion, contract facts, published packages), `read_projects` and `link_reads`; `build_workspace_graph` is now those two in a row, with the same output. `workspace/cli.py::load_workspace` returns the graph and the reads.
  - **Typed columns** are a `Column` record on `TableDef.typed`; `TableDef.columns` (names) is unchanged, so ADR-0037 edges and output are unchanged. SQL types are read up to the first constraint word; `ALTER COLUMN ... TYPE`, `SET/DROP NOT NULL` and MySQL `MODIFY` are applied in order. JSON Schema "nullable" means "not required".
  - **Type verdicts** compare the type family (text before `(`, `[` or `<`): a different family is `breaking`, the same family with different arguments (`varchar(100)` to `varchar(200)`) is `unknown`.
  - **Package contract:** Python exports come from `src/<module>/__init__.py` or `<module>/__init__.py` next to the manifest (`<module>` is the distribution name with `-` and `.` replaced by `_`); JS exports come from `exports["."]`, `main` or `module`, else `src/index.*` or `index.*`. A `dist/` entry that is not in the repository leaves the export list incomplete, which gives `unknown`, not a pass.
  - **`--workspace` / `--project`** are accepted by `run`, by `task --plan` and by `task --conduct`. The `--root` folder must be the named project's folder (exit 19 otherwise). Agents reach other projects only through `AgentContext.workspace`.
  - **Added things** (a route, column, setting or export) are `compatible` with the note "added; nothing that exists today can depend on it"; an added column is compatible only when a dependent's column list proves it is not used, and a required one is `unknown`.
- `packaging` (PyPA, 26.3) is now a direct dependency; the lock file gained only that line.
- Limits that are still true: other local projects are not read-only on disk (only the accessor stops a write); `from pkg.sub import x` and other submodule imports are not followed; request and response bodies, GraphQL, gRPC and runtime traffic are not compared; migration order is lexical file order; the reader has not been run with a real model; no GitHub-cloned project was used as a dependent by hand (the code path is the same as for local projects).

# ADR-0036: Workspace and cross-project package dependencies (v1.6.0)

## Status
Accepted by the user 2026-11-03. Implementation in progress on `feature/extended-roadmap`. Amends ADR-0006 (name matching) for cross-project edges only.

## Date
2026-11-03

## Context
`docs/extended_roadmap.md` v1.6.0 needs a named set of projects (local folders or GitHub repositories) and a dependency graph that links them. Today the graph covers one application (ADR-0006), ingestion reads local directories only and never executes code (ADR-0005), and no code calls `git` or any subprocess. ADR-0006 forbids linking an import to a package by name. Linking project A to project B needs a rule that is stricter than "the names match". The decisions made with the user on 2026-11-03: GitHub repos are fetched with the `git` CLI, v1.6.0 supports public repositories only, other projects in a workspace are read-only.

## Decision

### 1. Workspace store
A new SQLite file (`--workspace FILE`), stdlib `sqlite3`, private to `workspace/store.py`, with its own `PRAGMA application_id` and schema version. A foreign file, a newer version or malformed rows are refused (same pattern as `experience/reviews.py`; exit code 19, `EXIT_WORKSPACE_ERROR`, the next free number after `EXIT_UI_ERROR = 18`).
Tables:
- `projects`: name (unique within the workspace, validated like an app id), kind (`local` | `github`), location (absolute path, or the normalised URL), recorded commit (github only, full 40-hex), application id and its `how` (ADR-0021), added_at, removed_at (nullable).
- `declared_links`: from project, to project, kind, note, by, created_at, removed_at. Source is always `person`.
Removing a project sets `removed_at`. Its detected edges stop being computed and its declared links stop being listed, but the rows and any history that refers to the project stay.

### 2. Fetching a GitHub repository
- Only `https://github.com/OWNER/REPO` (optional `.git`, no query, fragment, credentials or port) is accepted; OWNER and REPO match `[A-Za-z0-9._-]{1,100}`.
- `git` is run with a fixed argument list and no shell, with `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, no credential helper, hooks disabled by pointing `core.hooksPath` at an empty directory, and `protocol.file.allow=never` for URL clones. A timeout applies to every call.
- Clones go into a cache directory (`--cache DIR`; default is a `cache/` directory beside the workspace file). The first add records the commit that `HEAD` points to; later fetches check out exactly the recorded commit, detached. If that commit is no longer reachable the result is `moved_commit`, reported, never silently replaced by a newer one. Updating the recorded commit is an explicit command.
- After checkout the working tree is made read-only. The project is then read only through the ingestion layer, which never executes anything (ADR-0005).
- Failures map to fixed messages, as in `providers/errors.py`: `git_missing`, `offline`, `not_found_or_private` (a private repo without credentials looks the same as a missing one), `moved_commit`, `other`. Messages never contain git's raw output, which could hold URLs with credentials.
- Public repositories only. A token or `gh` login is decided in the v1.12.0 ADR (open question 1 of the roadmap).

### 3. Cross-project `package` edge (amends ADR-0006)
An edge A -> B of kind `package` is created only when:
1. A's manifest declares a dependency named N (pyproject, requirements, package.json, as `graph/builders.py` already reads them), and
2. B's own manifest declares B's package name as N.
Names compare after normalisation (PyPI via the existing `normalize_pypi`; npm exact, scope included). Evidence has two sides: A's manifest path and line, and B's manifest path and line. An import statement alone never creates the edge. A declared dependency that no workspace project publishes stays an external dependency, as before. If two workspace projects publish the same name, the edge is marked `ambiguous`, both candidates are listed, and none is chosen. Version constraints are not compared in v1.6.0 (stated limit).

### 4. Declared edges
`workspace link A B --kind K --note TEXT --by NAME` stores an edge the analysis cannot see. It is a different record type (source `person`), rendered with a different style and labelled in every output, and never counted as detected evidence or as corroboration.

### 5. Rendering and CLI
The workspace graph reuses `graph/view.py` and the text, JSON, Mermaid and HTML renderers. Node ids get a project prefix; projects are clusters. Commands: `workspace add PATH|URL`, `workspace list`, `workspace remove NAME`, `workspace link`, `workspace graph [--format ... --json]`, `workspace refresh` (re-reads local projects; moves nothing for github ones). Every read command opens the workspace file only if it exists and creates nothing otherwise.

## Alternatives considered
- Tarball download over stdlib HTTP: no `git` needed, but no commit pinning by history and an archive-extraction attack surface (symlinks, path traversal). Rejected by the user in favour of `git`.
- Link by import name: simple, but ADR-0006 rejected it for good reason; a common name would link unrelated projects.
- Store edges in the experience graph file: that file is rebuilt by `experience build` and hashed; workspace membership is user input and must survive a rebuild.
- Follow the newest commit of a GitHub project automatically: evidence would change without anyone choosing it.

## Consequences
- First use of a subprocess in the codebase: the argv, environment, timeout and error mapping above are tested and are the only sanctioned way to call `git`.
- Cross-project edges rest on manifest evidence on both sides, so a project that publishes nothing or uses an unusual manifest produces no edge; the miss is visible as an external dependency.
- Version-aware matching, lockfiles across projects, monorepo sub-packages beyond what ingestion finds, private repositories and API/schema/config edges (v1.7.0) are out of scope.
- Risks accepted: cloned third-party content is untrusted data and is read, never run; a clone cache can grow without a limit in v1.6.0 (the cache path is listed by `workspace list`; cleanup is left to the user).

## Amendment during implementation (2026-11-03)
- The HTML renderer from section 5 is not implemented: text, JSON and Mermaid exist. Reason: the existing HTML page is built from a single-application `DependencyGraph` whose evidence needs a file hash per node, which a project-level graph does not have. Decide separately whether to add a workspace HTML view.
- A workspace is the file itself; it has no separate name.
- A GitHub project's application id is declared as `OWNER/REPO` (how = declared), because the cache directory name would be a poor derived id.
- Added failure kind `timeout` (300 s per git call).
- `set_commit` exists in the store but no command calls it yet; moving a pin is not exposed.

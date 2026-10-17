# ADR-0007: JS/TS import analysis and dynamic imports

## Status
Accepted (confirmed by the user 2026-10-17)

## Date
2026-10-17

## Context
v0.3.1 extends the dependency graph (ADR-0006) to JS/TS imports and to dynamic imports in Python
and JS/TS. The same rules hold: every edge has evidence, anything unresolved stays visible and is
never guessed, and ingested code is never executed. ADR-0006 deferred JS/TS because it needs a
real parser. Reliable import extraction means telling code from comments, strings, template
literals, regex literals and JSX text, which a line-based scan cannot do.

## Decision
- **Parser:** tree-sitter, via three runtime dependencies: `tree-sitter` 0.26.0,
  `tree-sitter-javascript` 0.25.0, `tree-sitter-typescript` 0.23.2 (TypeScript and TSX grammars).
  Parsing builds a syntax tree from text only; no ingested code is executed or imported.
- **Isolation:** tree-sitter is imported only by `cognitive_mesh/graph/js_parse.py`, the way
  NetworkX is private to `graph.py`. It exposes a pure function from (file suffix, text) to plain
  `JsImport` records (line, rule, literal specifier or `None` when computed, clipped cited text)
  plus a `has_error` flag. Grammar choice is by suffix: `.ts` TypeScript, `.tsx` TSX,
  `.js`/`.jsx` JavaScript. The tree walk is iterative, with no recursion.
- **Recognised syntax (real syntax nodes only):** `import` (rule `js:import`; `ts:import-type` for
  `import type`), `export ... from` (`js:export-from`), TS `import x = require("y")`
  (`ts:import-require`), `require("x")` with exactly one argument (`js:require`) and dynamic
  `import(...)` (`js:dynamic-import`). Because matching is on syntax nodes, text inside comments,
  strings, template text, regex literals and JSX text can never create an edge.
- **Literal vs computed:** an argument is literal only if it is a plain string or a template string
  with no substitutions. Anything else is computed.
- **Syntax errors:** nodes inside tree-sitter `ERROR` subtrees are ignored. A file with errors
  yields a `parse_error` warning ("imports taken only from error-free regions") and never aborts
  the build.
- **Resolution (no guessing):**
  - Relative specifiers (`./`, `../`) are joined as POSIX paths and tried as the exact path, then
    with `.ts/.tsx/.js/.jsx`, then `/index.*`, kept only if the file was ingested. One match gives
    an `imports` edge, several give `ambiguous_import` and an unresolved node, none gives
    `unresolved:import:js:<specifier>`. No `.js` -> `.ts` remapping. A path escaping the root is
    unresolved.
  - `node:` specifiers become `stdlib:node:<name>`. Bare Node builtin names are not special-cased.
  - Bare and aliased specifiers (`react`, `@scope/pkg/sub`, `@/x`) become
    `unresolved:import:js:<package name>`; `candidate_package` is set only as a hint when
    `pkg:npm:<name>` is declared, never as an edge (as in ADR-0006).
  - Computed dynamic imports become an `unresolved:dynamic:<path>:<line>` node, an `imports` edge
    with evidence, and a `computed_import` warning. No target is guessed.
- **Python dynamic imports:** `importlib.import_module(...)` (also via `import importlib as x` or
  `from importlib import import_module [as y]`) and `__import__(...)`. A literal, non-relative
  first argument resolves through the existing Python resolution with rule
  `python:dynamic-import`. A non-literal argument, relative name or `package=` argument is
  unresolved plus `computed_import`.
- **Diagnostics for limits:** when any JS/TS file is analysed, a `js_resolution_limited` info
  diagnostic states what is not modelled.
- **New diagnostic codes:** `computed_import`, `js_resolution_limited`. No new edge kinds.

## Dependency vetting (security rules)
Checked 2026-10-17. All three are published from the official `tree-sitter` GitHub organisation
(repos `py-tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`; not archived; MIT;
maintainer Max Brunsfeld, the tree-sitter author). Wheels exist for CPython 3.12 on Windows. The
core and both grammars load together (JS grammar ABI 15, TS/TSX ABI 14, core 0.26.0 accepts both)
and the core ships `py.typed` for strict mypy. They contain native code, which enlarges the
supply-chain surface; versions are locked in `uv.lock`.

## Alternatives considered
- Hand-written stdlib scanner: avoids a dependency but cannot reliably separate regex literals from
  division or handle JSX text and nested template literals. Mistakes would create false edges or
  false negatives, which conflicts with the evidence rules. Rejected.
- `esprima` (Python): unmaintained since 2018, no TypeScript. Rejected.
- Node-based tooling (`@babel/parser`, TypeScript compiler): needs a Node runtime and running
  external tools. Rejected.
- Pinning a lower tree-sitter core: not needed, the spike passed.

## Consequences
- JS/TS import edges and Python literal dynamic imports now appear with file, line and rule
  evidence. A missing edge is still not proof of no dependency.
- Not modelled: bundler aliases, `tsconfig` `paths`/`baseUrl`, `package.json` `exports`/`imports`,
  `.js` -> `.ts` remapping, `.mjs`/`.cjs`/`.mts`/`.cts` files (ingestion does not classify them),
  `require` shadowing, conditional or runtime-constructed paths, `eval`.
- The TypeScript grammar (0.23.2, 2024) is older than the JavaScript grammar; newer TS syntax may
  parse with errors, which is reported as a warning, not hidden.
- Native extensions add install weight and a binary supply-chain dependency.
- Graph results remain in memory only.

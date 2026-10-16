# System Patterns

## Naming conventions
- Package `cognitive_mesh` (import), `cognitive-mesh` (distribution and console script).
- Env vars: `COGMESH_` prefix, except the conventional `GROQ_API_KEY`.
- Tests: `tests/test_*.py`.

## Folder structure logic
- `src/` layout: all product code in `src/cognitive_mesh/`; tests in `tests/`.
- Significant decisions go in `docs/adr/` (0002-0004 cover tooling and config, 0005 ingestion, 0006 dependency graph).

## Common patterns used in this project
- Configuration is read only through `cognitive_mesh.config.load_settings()`; never `os.environ` directly elsewhere.
- Secrets are `SecretStr`; never log or print them, and keep them out of error messages.
- Config failures raise `ConfigError`; the CLI maps them to exit code 2 (0 = ok).
- Tests run via an autouse fixture (`tests/conftest.py`) that clears config env vars and uses an empty temp cwd, so a real `.env` never leaks in.

- Ingestion (`cognitive_mesh.ingestion`, ADR-0005): per-file problems are `Diagnostic`s, never exceptions; only a bad root raises `IngestionError` (CLI exit 3). Ingested content is data: never imported, executed or put in diagnostics (not even parser error text). All file reads go through `ingest._read_bytes`; tests spy on it to prove sensitive files are never opened.
- Every ingested record carries a `SourceRef` (relative POSIX path + sha256 of the original bytes) and the rule that classified it. Secrets found in content are replaced by `[REDACTED:<rule>]`; the sha256 still refers to the original bytes.
- Test fixtures: benign app committed in `tests/fixtures/sample_app/`; hostile files (secrets, binaries, malformed) are created in `tmp_path` at test time, and fake secrets are assembled at runtime. Keep parametrize ids short (huge inputs in ids break pytest on Windows).

## Things to avoid
- Hardcoding credentials or committing `.env` files (only `.env.example`, placeholders only).
- Documenting commands that have not been run successfully.
- Marking a roadmap version done before its verification criteria pass.
- Dependency graph (`cognitive_mesh.graph`, ADR-0006): `A -> B` always means "A depends on / imports B". Every `Edge` has >=1 `Evidence` (SourceRef + line + rule + cited text); the model rejects none. Anything not resolvable becomes an `unresolved` node plus a diagnostic, never a guessed edge; imports are never linked to packages by name. Builders (`graph/builders.py`) are pure over `IngestionResult` and never touch the disk. NetworkX stays private to `graph/graph.py`; public API returns frozen pydantic models; outputs are sorted for determinism. Only `GraphError` (unknown node id) is raised (CLI exit 4).
- Test fixtures: `tests/fixtures/dep_app/` is the graph fixture (cycle a<->b, undeclared and declared imports, `-r`/URL requirement lines); `tests/fixtures/` is excluded from mypy.

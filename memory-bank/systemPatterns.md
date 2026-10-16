# System Patterns

## Naming conventions
- Package `cognitive_mesh` (import), `cognitive-mesh` (distribution and console script).
- Env vars: `COGMESH_` prefix, except the conventional `GROQ_API_KEY`.
- Tests: `tests/test_*.py`.

## Folder structure logic
- `src/` layout: all product code in `src/cognitive_mesh/`; tests in `tests/`.
- Significant decisions go in `docs/adr/` (0002-0004 cover tooling and config).

## Common patterns used in this project
- Configuration is read only through `cognitive_mesh.config.load_settings()`; never `os.environ` directly elsewhere.
- Secrets are `SecretStr`; never log or print them, and keep them out of error messages.
- Config failures raise `ConfigError`; the CLI maps them to exit code 2 (0 = ok).
- Tests run via an autouse fixture (`tests/conftest.py`) that clears config env vars and uses an empty temp cwd, so a real `.env` never leaks in.

## Things to avoid
- Hardcoding credentials or committing `.env` files (only `.env.example`, placeholders only).
- Documenting commands that have not been run successfully.
- Marking a roadmap version done before its verification criteria pass.

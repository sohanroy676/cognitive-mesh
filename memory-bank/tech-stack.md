# Tech Stack

## Status
v0.1.0 foundation is implemented: tooling, config loading, and a minimal CLI. The commands
in "Verified commands" below were run successfully on 2026-10-16 (Windows 11, Git Bash,
uv 0.13.1, CPython 3.12.3). Everything else here (providers, memory stores, agents) is still
planned and unimplemented. GitHub Actions CI passed on main (run 38942568274, 2026-10-16).

## Language and Runtime
- Language: Python
- Python version: 3.12
- Dependency manager: uv (ADR-0002); `pyproject.toml` + committed `uv.lock`, `.python-version` = 3.12
- Virtual environment approach: uv-managed `.venv/` in the repo root (git-ignored)
- Layout: `src/cognitive_mesh/` (hatchling build backend), `tests/`

## AI Models and Providers
- Primary planned external API provider: Groq
- Additional providers: Pluggable; specific providers TBD
- Local model runtime: Ollama
- Model selection and routing: Custom Python implementation
- Model IDs, provider SDKs, and model availability: TBD

The application should support multiple configured model providers where available.
Provider-specific logic should be isolated behind a common internal interface.
API credentials must come from environment variables or a local, ignored .env file.
Never hardcode credentials in source code or commit secrets.

## Memory and Data
- Vector database: ChromaDB
- Graph representation and traversal: NetworkX 3.7 (runtime dependency since v0.3.0; `types-networkx` in dev; ADR-0006)
- JS/TS import parsing: tree-sitter 0.26.0 + tree-sitter-javascript 0.25.0 + tree-sitter-typescript 0.23.2 (runtime dependencies since v0.3.1; MIT, official tree-sitter org; private to `graph/js_parse.py`; ADR-0007)
- Persistent structured storage: SQLite
- Embeddings: Sentence Transformers
- Embedding model and version: TBD

SQLite is intended to store structured records and metadata.
ChromaDB is intended for semantic vector retrieval.
NetworkX is intended for graph operations. Persistence and reconstruction of the graph
must be implemented explicitly; NetworkX alone is not the persistent database.

## Application Architecture
- Orchestration: Custom Python orchestrator
- Agent roles and interfaces: To be defined during implementation
- Application understanding: Source code, configuration, dependency manifests,
  and documentation, subject to the supported ingestion scope
- Memory approach: Episodic traces, evidence-aware knowledge, and context retrieval

## Testing
- Test framework: pytest 9.1.1 (ADR-0003)
- Test directory: `tests/`
- Test file naming convention: `test_*.py`
- Test command: `uv run pytest`

`tests/conftest.py` has an autouse fixture that clears `COGMESH_*`/`GROQ_API_KEY` and
runs each test in an empty temp cwd so a developer's real `.env` cannot leak in.
Tests should cover unit behavior, integration boundaries, and end-to-end workflows.
Tests requiring external APIs must support controlled mocking or explicitly configured
integration credentials.

## Linting and Formatting
- Linter: ruff 0.17.1 (rules E, F, W, I, B, UP, SIM, S)
- Formatter: ruff format (black is not used)
- Static type checker: mypy 2.4.0, strict, pydantic plugin; checks `src` and `tests`
- Commands: `uv run ruff check .`, `uv run ruff format --check .` (drop `--check` to apply), `uv run mypy`

## Build, Run, and Development
- Install uv (one-time, Windows): `winget install --id=astral-sh.uv -e`
- Environment setup / install: `uv sync` (CI uses `uv sync --locked`)
- Application startup: `uv run cognitive-mesh` (also `uv run python -m cognitive_mesh`, exercised by a test)
- Build or packaging command: TBD (not run)

### Verified commands
```
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
COGMESH_PROVIDER=ollama uv run cognitive-mesh   # exit 0, "config OK (provider=ollama)"
uv run cognitive-mesh                           # no config: exit 2, names COGMESH_PROVIDER
uv run cognitive-mesh ingest tests/fixtures/sample_app          # exit 0, lists 9 files
uv run cognitive-mesh ingest tests/fixtures/sample_app --json   # exit 0 (--include-content adds file text)
uv run cognitive-mesh ingest does-not-exist                     # exit 3, "ingestion root does not exist"
uv run cognitive-mesh graph tests/fixtures/dep_app              # exit 0, nodes/edges/unresolved/cycle/diagnostics
uv run cognitive-mesh graph tests/fixtures/dep_app --json       # exit 0
uv run cognitive-mesh graph tests/fixtures/dep_app --deps-of module:src/dep_app/a.py --transitive   # exit 0
uv run cognitive-mesh graph tests/fixtures/dep_app --deps-of nope   # exit 4, "unknown node"
uv run cognitive-mesh graph does-not-exist                      # exit 3
uv run cognitive-mesh graph tests/fixtures/js_app               # exit 0, 7 unresolved, computed_import + js_resolution_limited
uv run cognitive-mesh graph tests/fixtures/js_app --json        # exit 0
uv run cognitive-mesh graph tests/fixtures/js_app --deps-of module:src/index.ts --transitive   # exit 0
uv run cognitive-mesh graph tests/fixtures/js_app --deps-of nope   # exit 4
uv run cognitive-mesh graph tests/fixtures/dep_app --format mermaid          # exit 0, flowchart text
uv run cognitive-mesh graph tests/fixtures/js_app --format html --output FILE   # exit 0, writes ASCII/LF HTML
uv run cognitive-mesh graph tests/fixtures/dep_app --format html --max-nodes 3  # exit 0, truncation banner + stderr notice
uv run cognitive-mesh graph tests/fixtures/dep_app --format html --json         # exit 2 (usage error)
uv run cognitive-mesh graph tests/fixtures/dep_app --format html --output missing/dir/x.html   # exit 5
```
The v0.1.0 commands also passed in a fresh `git clone` of its feature branch; the `ingest` commands were run only in the working tree (not yet in a fresh clone or CI).
The `dep_app` `graph` commands were run 2026-10-17 in the working tree on branch `feature/v0.3.0-dependency-graph` (not in a fresh clone; CI runs the test suite, not these CLI calls).
The `js_app` graph commands were run 2026-10-17 in the working tree on branch `feature/v0.3.1-js-dynamic-imports` (not yet in a fresh clone or CI).
The `--format` graph commands were run 2026-10-17 in the working tree on branch `feature/v0.3.2-graph-visualization` (not yet in a fresh clone or CI); the HTML output was also opened in headless Edge. No dependency was added in v0.3.2.
Note: the "no config: exit 2" check needs a working directory without a local `.env`.

Do not document unverified commands as working commands.

## Configuration (ADR-0004)
Settings load via `cognitive_mesh.config.load_settings()` from the environment, then an
ignored `.env` in the working directory. Copy `.env.example` to `.env` to start.
- `COGMESH_PROVIDER` (required): `groq` or `ollama`
- `GROQ_API_KEY`: required when provider is `groq`; held as `SecretStr`
- `COGMESH_OLLAMA_BASE_URL`, `COGMESH_LOG_LEVEL`: optional, have defaults
Missing/invalid values raise `ConfigError`; the CLI prints it to stderr and exits 2.

## External Services
- Groq API: Planned primary external model provider
- Ollama: Planned local model runtime
- Other model APIs: Optional future integrations
- API credentials and endpoint configuration: see Configuration above (no provider calls implemented yet)

The core application should separate provider configuration from orchestration,
memory, and agent logic.
# Tech Stack

## Status
v0.1.0 foundation is implemented: tooling, config loading, and a minimal CLI. The commands
in "Verified commands" below were run successfully on 2026-10-16 (Windows 11, Git Bash,
uv 0.13.1, CPython 3.12.3). Everything else here (memory stores, Groq client) is still
planned and unimplemented. v0.6.1 added a real Ollama client (stdlib HTTP, no new dependency; ADR-0012). GitHub Actions CI passed on main (run 38942568274, 2026-10-16).

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
- Ollama client implemented in v0.6.1 (`providers/ollama.py`, ADR-0012) on stdlib `http.client` (`providers/http.py`, one total deadline per call, no new dependency). Groq client implemented in v0.6.2 (`providers/groq.py`, ADR-0013) on the same transport; shared failure mapping in `providers/errors.py`; default model `qwen/qwen3.8-27b` (Groq preview model, the only Qwen on Groq's list on 2026-10-17; `COGMESH_GROQ_MODEL`), `COGMESH_GROQ_REASONING_EFFORT` default `none`. Default Ollama model: `llama3.2`.

The application should support multiple configured model providers where available.
Provider-specific logic should be isolated behind a common internal interface.
API credentials must come from environment variables or a local, ignored .env file.
Never hardcode credentials in source code or commit secrets.

## Memory and Data
- Vector database: ChromaDB
- Graph representation and traversal: NetworkX 3.7 (runtime dependency since v0.3.0; `types-networkx` in dev; ADR-0006)
- JS/TS import parsing: tree-sitter 0.26.0 + tree-sitter-javascript 0.25.0 + tree-sitter-typescript 0.23.2 (runtime dependencies since v0.3.1; MIT, official tree-sitter org; private to `graph/js_parse.py`; ADR-0007)
- Persistent structured storage: SQLite (stdlib `sqlite3`; run traces since v0.6.0, schema version 1, ADR-0011; no new dependency)
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
- Marker `live` (real provider, needs an explicit env var such as `RUN_LIVE_OLLAMA=1`) is skipped by default and in CI.

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

## Changelog
- Generator: git-cliff 2.14.2 (dev dependency; official orhun/git-cliff), config in `cliff.toml`.
- Regenerate: `uv run git-cliff -o CHANGELOG.md`; commit as `chore(changelog): regenerate`, as the last commit on a branch. For a release use `--tag vX.Y.Z` before the tag exists.
- Skipped on purpose: merge commits, `chore(deps)` bumps, `chore(changelog)`, and non-conventional subjects (git-cliff warns "14 commit(s) were skipped"; these are the merge commits).
- CI step "Changelog is up to date" regenerates and runs `git diff --exit-code CHANGELOG.md`.
- Verified 2026-10-17 locally: output is deterministic (two runs identical); in a scratch clone, a fresh file gives exit 0 and an added `feat:` commit makes the diff check fail. The CI step ran on main in run 39022644447 (2026-10-17, merge of `chore/changelog-git-cliff`) and passed (git-cliff skipped 15 commits for non-conventional subjects, no diff).

## Build, Run, and Development
- Install uv (one-time, Windows): `winget install --id=astral-sh.uv -e`
- Environment setup / install: `uv sync` (CI uses `uv sync --locked`)
- Application startup: `uv run cognitive-mesh` (also `uv run python -m cognitive_mesh`, exercised by a test)
- Build: `uv build --out-dir DIR` builds an sdist and a wheel (run 2026-10-17; `dist/` is git-ignored). The wheel installed into a fresh venv and `cognitive-mesh` ran from an empty directory (no config: exit 2; `COGMESH_PROVIDER=ollama`: exit 0; `graph` on a tiny app: exit 0). The sdist holds only the placeholder `.env.example`. Not verified: publishing, other platforms, CI. Versioning: the version comes from git tags via hatch-vcs 0.5.0 (build-time only; MIT, by the hatch author; pulls setuptools-scm from pypa). `pyproject.toml` has `dynamic = ["version"]`, `local_scheme = "no-local-version"`; `cognitive_mesh.__version__` reads `importlib.metadata` (`0+unknown` if not installed). Verified 2026-10-17: a branch build gave `0.3.3.dev10`; in a scratch clone a throwaway tag `v9.9.9` gave exactly `9.9.9` (sdist and wheel); a shallow clone without tags gave `0.1.dev1` with a warning, no failure. An editable install keeps the version computed at `uv sync` time, so re-run `uv sync` after tagging. CI checks out full history. CI run 39022644447 on main passed the full test suite with the git-derived version in place; the version string CI computed was not inspected in the log.

### Mermaid check (manual, not in CI)
Run 2026-10-17 in a scratch directory (nothing added to the repo): `npm install @mermaid-js/mermaid-cli` (12.0.0, MIT, official mermaid-js) with `PUPPETEER_SKIP_DOWNLOAD=1` and a puppeteer config pointing at the installed Edge, then `mmdc -p pp.json -i FILE.mmd -o FILE.svg`. Golden files and a hostile-filename graph rendered (exit 0, node counts matched, no script markup in the SVG, screenshots reviewed). Note: check mmdc's own exit status, not a piped one.

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
uv run cognitive-mesh agents                    # exit 0, no config needed, lists 2 agents
uv run cognitive-mesh agents --json             # exit 0, includes input/output schemas
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app          # exit 0, 2 steps succeeded
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --json   # exit 0
uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # exit 7, provider_unavailable, dependent blocked
uv run cognitive-mesh run tests/fixtures/plans/cycle.json --root tests/fixtures/dep_app       # exit 6, "dependency cycle among steps: a, b"
uv run cognitive-mesh run tests/fixtures/plans/nope.json --root tests/fixtures/dep_app        # exit 6, "cannot read the plan file"
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root does-not-exist                  # exit 3
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db FILE   # exit 0, prints trace: RUN_ID
uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app --trace-db FILE   # exit 7, recorded as failed
COGMESH_MODEL_TIMEOUT_S=120 uv run cognitive-mesh plan "REQUEST" [--output FILE]   # repo root, .env provider ollama + llama3.2: exit 0, prints a plan, stderr says unreviewed (run 2026-10-17; the plan it drafted had a nonsensical node_id)
uv run cognitive-mesh traces list --db FILE [--json]    # exit 0 (newest first)
uv run cognitive-mesh traces show RUN_ID --db FILE [--json]   # exit 0; in a new process too
uv run cognitive-mesh traces show nope --db FILE                # exit 9, "no run has the id"
uv run cognitive-mesh traces list --db missing.sqlite3          # exit 8, "the trace database does not exist"; creates nothing
COGMESH_PROVIDER=ollama COGMESH_MODEL_TIMEOUT_S=120 uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app --trace-db FILE   # exit 0 with a running Ollama + llama3.2; explain_file succeeded, trace saved
uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # empty cwd, no config: exit 7, provider_unavailable
COGMESH_PROVIDER=ollama COGMESH_OLLAMA_BASE_URL=http://127.0.0.1:1 uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # exit 7, provider_unavailable after 2 attempts
COGMESH_PROVIDER=ollama COGMESH_OLLAMA_MODEL=no-such-model uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # exit 7, provider_error (HTTP 404, not retried)
COGMESH_PROVIDER=groq uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # empty cwd, no key: exit 2, names GROQ_API_KEY
COGMESH_PROVIDER=groq GROQ_API_KEY=gsk_fake uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # empty cwd, no opt-in: exit 2, names COGMESH_ALLOW_REMOTE_CONTENT, no request made (2026-10-17)
COGMESH_PROVIDER=groq GROQ_API_KEY=gsk_fake COGMESH_ALLOW_REMOTE_CONTENT=true uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # real call to api.groq.com with a fake key: exit 7, provider_error (auth), not retried
COGMESH_PROVIDER=groq uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app [--json --trace-db FILE]   # repo root, key from the ignored .env: exit 0, explain_file succeeded, model_unverified, provider groq; key absent from stdout/stderr/trace db
COGMESH_PROVIDER=groq COGMESH_GROQ_MODEL=no/such-model uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app   # exit 7, provider_error (other)
RUN_LIVE_GROQ=1 RUN_LIVE_GROQ_KEY=... uv run pytest -m live tests/test_live_groq.py   # 1 passed; skipped by default and in CI
COGMESH_PROVIDER=bogus uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app   # exit 0: plans without a model step never read config
RUN_LIVE_OLLAMA=1 uv run pytest -m live   # opt-in real Ollama test, 1 passed; skipped by default and in CI
```
The v0.1.0 commands also passed in a fresh `git clone` of its feature branch; the `ingest` commands were run only in the working tree (not yet in a fresh clone or CI).
The `dep_app` `graph` commands were run 2026-10-17 in the working tree on branch `feature/v0.3.0-dependency-graph` (not in a fresh clone; CI runs the test suite, not these CLI calls).
The `js_app` graph commands were run 2026-10-17 in the working tree on branch `feature/v0.3.1-js-dynamic-imports` (not yet in a fresh clone or CI).
The `--format` graph commands were run 2026-10-17 in the working tree on branch `feature/v0.3.2-graph-visualization` (not yet in a fresh clone or CI); the HTML output was also opened in headless Edge. No dependency was added in v0.3.2.
The `agents` commands were run 2026-10-17 in the working tree on branch `feature/v0.4.0-agent-architecture` (not yet in a fresh clone or CI); v0.4.0 added no dependency.
The `run` commands were run 2026-10-17 in the working tree on branch `feature/v0.5.0-orchestrator` (not yet in a fresh clone or CI); v0.5.0 added no dependency.
The `--trace-db` and `traces` commands were run 2026-10-17 in the working tree on branch `feature/v0.6.0-trace-store` with the database in a scratch directory (not yet in a fresh clone or CI); v0.6.0 added no dependency.
The v0.6.1 commands below were run 2026-10-17 on branch `feature/v0.6.1-ollama-client` against a local Ollama (`llama3.2:latest` pulled), from the repo and from an empty directory via `uv run --project`; not yet in a fresh clone or CI.
Note: the "no config: exit 2" check needs a working directory without a local `.env`.

Do not document unverified commands as working commands.

## Configuration (ADR-0004)
Settings load via `cognitive_mesh.config.load_settings()` from the environment, then an
ignored `.env` in the working directory. Copy `.env.example` to `.env` to start.
- `COGMESH_PROVIDER` (required): `groq` or `ollama`
- `GROQ_API_KEY`: required when provider is `groq`; held as `SecretStr`; must be printable ASCII without spaces or line breaks
- `COGMESH_ALLOW_REMOTE_CONTENT` (default `false`): must be `true` for `COGMESH_PROVIDER=groq` (file contents go to Groq's servers); checked in `providers/factory.py`, otherwise exit 2 naming the variable; Ollama is unaffected. The two older `run` commands above that make a real Groq call from the repo (the `.env` one and the `no/such-model` one) now also need it; they were run before it existed. The live test builds `GroqClient` directly and is unaffected.
- `COGMESH_GROQ_MODEL` (default `qwen/qwen3.8-27b`), `COGMESH_GROQ_REASONING_EFFORT` (`none` default; `default|low|medium|high`, or empty to omit)
- `COGMESH_OLLAMA_BASE_URL` (validated: http/https, host, no credentials/query), `COGMESH_OLLAMA_MODEL` (default `llama3.2`), `COGMESH_MODEL_TIMEOUT_S` (default 30, >0, max 600), `COGMESH_LOG_LEVEL`: optional, have defaults
- `run` reads config only when the plan has a model step (`load_settings_if_configured`: no `COGMESH_PROVIDER` means no client; any other problem exits 2). Other commands never read it.
Missing/invalid values raise `ConfigError`; the CLI prints it to stderr and exits 2.

## External Services
- Groq API: external model provider; client implemented and verified in v0.6.2 (fixed endpoint; one key, `qwen/qwen3.8-27b`)
- Ollama: local model runtime; client implemented in v0.6.1 (only `run` with a model step calls it)
- Other model APIs: Optional future integrations
- API credentials and endpoint configuration: see Configuration above (Ollama calls exist since v0.6.1, Groq since v0.6.2)

The core application should separate provider configuration from orchestration,
memory, and agent logic.
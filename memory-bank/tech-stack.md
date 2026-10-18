# Tech Stack

## Status
v0.1.0 foundation is implemented: tooling, config loading, and a minimal CLI. The commands
in "Verified commands" below were run successfully on 2026-10-16 (Windows 11, Git Bash,
uv 0.13.1, CPython 3.12.3). Everything else here is still planned and unimplemented, except
the v0.7.0 vector index (ChromaDB + Sentence Transformers, merged, tagged `v0.7.0`) and the v0.8.0 experience graph (merged, tagged `v0.8.0`); the v0.9.0 evidence-aware `recall` (ADR-0016) is merged and tagged `v0.9.0` (merge `12706e6`; CI runs 39055678848, 39055806280 and, for the tag, 39055934929 passed; no new dependency). v0.6.1 added a real Ollama client (stdlib HTTP, no new dependency; ADR-0012). GitHub Actions CI passed on main (run 38942568274, 2026-10-16). v1.0.0 (ADR-0017: `task`, the context pack, trace schema 2) is merged to main (`109f7b8`; CI run 39060264282 passed) and tagged `v1.0.0` (release merge `21bf079`; tag run 39060736575 passed); no new dependency.

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
- Graph representation and traversal: NetworkX 3.7 (runtime dependency since v0.3.0; `types-networkx` in dev; ADR-0006). v0.8.0 (ADR-0015) uses it again, private to `experience/graph.py`, and stores the experience graph in its own SQLite file (stdlib `sqlite3`, `PRAGMA application_id`, no new dependency)
- JS/TS import parsing: tree-sitter 0.26.0 + tree-sitter-javascript 0.25.0 + tree-sitter-typescript 0.23.2 (runtime dependencies since v0.3.1; MIT, official tree-sitter org; private to `graph/js_parse.py`; ADR-0007)
- Persistent structured storage: SQLite (stdlib `sqlite3`; run traces since v0.6.0, ADR-0011; schema version 2 since v1.0.0, whose first migration adds `run_context`, the context pack a run was offered, ADR-0017; no new dependency). v0.9.0 adds a separate append-only reviews file (`experience/reviews.py`, ADR-0016, own schema version 1, `application_id` `CMER`); the experience graph file is format 2
- Embeddings: Sentence Transformers 6.1.0 (Apache-2.0), runtime dependency since v0.7.0 on branch `feature/v0.7.0-semantic-memory` (ADR-0014); torch 2.14.1 is a direct dependency so that `[tool.uv.sources]` can take the CPU build from the official PyTorch index on Linux (`uv.lock` has no `nvidia-*`/`cuda` entries; checked 2026-10-17, Linux install verified by CI run 39043661141 on 2026-10-17: torch CPU, 4 s with `uv sync --locked`)
- Embedding model and version: `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (Apache-2.0, 384 dimensions), fixed in `memory/embedder.py`
- Vector store: chromadb 1.5.9 (Apache-2.0), runtime dependency since v0.7.0, private to `memory/index.py`; telemetry off, no Chroma embedding function, cosine space

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
- CI step "Changelog is up to date" regenerates and runs `git diff --exit-code CHANGELOG.md`. If the newest section is a version whose tag does not exist yet (a release merge on main before tagging), it regenerates with `--tag vX.Y.Z`, so the release merge no longer fails; found when the v0.7.0 merge run 39050618067 failed. Checked 2026-10-17 in a scratch clone with the v0.7.0 tag removed (pass) and in the repo with the tag (pass). Proven in CI: the v0.8.0 release merge on main (run 39053504333, no tag yet) passed, then the tag run 39053616562 passed too.
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
uv run cognitive-mesh graph tests/fixtures/lock_app --deps-of component:. --transitive   # exit 0, reaches locked packages (depth 2) from uv.lock and package-lock.json (run 2026-10-17)
uv run cognitive-mesh graph tests/fixtures/dep_app --format mermaid          # exit 0, flowchart text
uv run cognitive-mesh graph tests/fixtures/js_app --format html --output FILE   # exit 0, writes ASCII/LF HTML
uv run cognitive-mesh graph tests/fixtures/js_app --format html --output FILE   # re-run 2026-10-17 after the layout change; screenshot in headless Chrome and Edge (both Chromium) reviewed
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
RUN_LIVE_EMBEDDINGS=1 uv run pytest -m live tests/test_live_embeddings.py -s   # 1 passed (2026-10-17, real MiniLM download ~90 MB); skipped by default and in CI
uv run cognitive-mesh memory index --db FILE --index DIR     # empty scratch cwd via uv run --project: exit 0, "4 added"; second run "4 unchanged", model not re-run for unchanged text
uv run cognitive-mesh memory search "QUERY" --db FILE --index DIR [--k N] [--min-similarity X] [--json]   # exit 0; relevant query ranks the right step first; unrelated query prints "no sufficiently similar experience"
uv run cognitive-mesh memory search x --db FILE --index missing-dir   # exit 11, "the memory index does not exist"; creates nothing
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db T   # run twice, then needs_model.json (exit 7), in an empty scratch directory via uv run --project
uv run cognitive-mesh experience build --db T --graph G     # exit 0, 33 nodes, 46 edges, 6 claims (all unvalidated), prints the content sha256
uv run cognitive-mesh experience claims --db T --graph G    # new process: exit 0, each claim "observed 2x (2 still confirmed, 1 code version(s))", then the unvalidated note
uv run cognitive-mesh experience check --db T --graph G     # exit 0, "content hash matches", sources: ok=33
uv run cognitive-mesh experience check --db T2 --graph G    # T2 = copy of T with one run deleted: exit 14, source_missing=5, diagnostics on stderr; claims then show "1 still confirmed"
uv run cognitive-mesh experience claims --db T --graph G3   # G3 = copy of G with one claim name edited: exit 12, "the stored content hash does not match the graph content"
uv run cognitive-mesh experience show run:ID --db T --graph G2   # G2 = node record set to an invalid value: exit 12, "malformed record"
# v0.9.0, empty scratch directory via uv run --project, real MiniLM (T: ok.json run twice, a.py edited to import util instead of b, ok.json run again, needs_model.json; IDX: 8 added; G: 40 nodes, 67 edges, 7 claims)
uv run cognitive-mesh recall "dependencies of module a.py" --db T --index IDX --graph G   # exit 0; claims "supported", "no --root given: whether claims are current is unknown", the other-version listing shown as "contradicted by ... other or unrecorded version"
uv run cognitive-mesh recall "dependencies of module a.py" --db T --index IDX --graph G --root APP --k 3   # exit 0; the removed import is "stale", the kept and the new one "validated" (re-derived, path:line and hash shown)
uv run cognitive-mesh experience review CLAIM_ID --verdict rejected --note "TEXT" --by NAME --root APP --graph G --reviews R   # exit 0, "review #1 recorded ... unauthenticated"
uv run cognitive-mesh recall ... --root APP --reviews R           # new process: the reviewed claim is [conflicting] (output checked; exit status not captured)
uv run cognitive-mesh experience claims --db T --graph G --root APP --reviews R   # [conflicting] with the review and both sides, [validated], [stale] (output checked; exit status not captured)
uv run cognitive-mesh recall ... --db T2 --index IDX --graph G    # T2 = copy of T with one run deleted: exit 0, counts show "1 not confirmed"
uv run cognitive-mesh recall "zebra quantum pastry" --db T --index IDX --graph G   # prints "no sufficiently similar experience" and "5 weak match(es) below 0.2 dropped" (exit status not captured)
# v1.0.0, empty scratch directory via uv run --project, real MiniLM, tests/fixtures/e2e_app copied to ./app (each command its own process; T, IDX, G are files in that directory)
uv run cognitive-mesh task "dependencies of the cart module" --plan tests/fixtures/plans/e2e_session1.json --root app --db T --index IDX --graph G   # no index or graph yet: exit 0, "no prior context", both reasons printed, trace id
uv run cognitive-mesh task "explain the project file" --plan tests/fixtures/plans/needs_model.json --root app --db T --index IDX --graph G   # exit 7, explain_file failed provider_unavailable, dependent blocked, trace recorded
uv run cognitive-mesh task "dependencies of the cart module" --plan tests/fixtures/plans/e2e_session1.json --root app --db T --index IDX --graph G   # after editing app/src/shopdemo/cart.py to import tax instead of pricing: exit 0
uv run cognitive-mesh memory index --db T --index IDX       # exit 0, 6 added
uv run cognitive-mesh experience build --db T --graph G     # exit 0, 25 nodes, 32 edges, 2 claims
uv run cognitive-mesh task "which modules does the cart import now" --plan tests/fixtures/plans/e2e_session2.json --root app --db T --index IDX --graph G   # exit 0: cart->pricing [stale], cart->tax [validated], each with run ids supporting it and contradicting it on the other code version; 0 withheld
uv run cognitive-mesh traces show RUN_ID --db T             # exit 0; prints the offered context (2 claims with statuses) and the run
uv run cognitive-mesh task "zebra quantum pastry" --plan tests/fixtures/plans/e2e_session2.json --root app --db T --index IDX --graph G   # exit 0, "no prior context", nothing withheld
uv run cognitive-mesh task "which modules does the cart import now" --plan OTHER_PLAN.json --root other --db T --index IDX --graph G --min-similarity 0   # other = a different small application: exit 0, empty pack, "5 not applicable" (2 claims whose subject is absent, 3 raw traces from a differently named directory), each listed with its reason
# v1.0.0 verification pass, 2026-10-18 (details and numbers in progress.md)
# 2026-10-18 later: uv run cognitive-mesh run refs.json --root tests/fixtures/dep_app [--trace-db FILE|--json]   # refs.json = two dependencies_of steps, step 2 takes node_id via input_refs: exit 0; the same plan without depends_on exits 6
# COGMESH_PROVIDER=ollama COGMESH_MODEL_TIMEOUT_S=180 uv run cognitive-mesh plan "REQUEST" --output FILE   # 3 requests with llama3.2: 2 rejected by validation, 1 accepted but failed when run
# COGMESH_PROVIDER=groq COGMESH_ALLOW_REMOTE_CONTENT=true uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app [--json]   # repo root, key from the ignored .env: first run exit 7 provider_error (other), unexplained; two repeats exit 0, explain_file succeeded
# uv run cognitive-mesh memory search "x" --db T --index IDX_BUILT_WITH_FAKE_EMBEDDER   # exit 11, different-model message; memory index on it also exit 11
# fresh clone of GitHub main c362e99: uv sync --locked; uv run pytest (1030 passed, 5 skipped); ruff/format/mypy clean; the e2e_app walkthrough above ran with the real MiniLM and matched
uv run cognitive-mesh experience review CLAIM_ID --verdict rejected --note "TEXT" --by NAME --root app --graph G --reviews R   # then: task ... --reviews R shows the claim [conflicting] (exit 0); --reviews missing.sqlite3 exits 15
uv run cognitive-mesh experience claims --db T2 --graph G --root app --reviews R --include-unverified   # T2 = copy of T with a run deleted: exit 0, source_missing claim shown, still [conflicting]
uv run cognitive-mesh experience show attempt:RUN/deps/1 --db T --graph G --incoming --transitive   # exit 0, claim, step, run
COGMESH_PROVIDER=ollama COGMESH_OLLAMA_BASE_URL=http://127.0.0.1:PROXY uv run cognitive-mesh task "REQUEST" --plan explain_cart.json --root app --db T --index IDX --graph G --json   # 3 runs through a scratch recording proxy to a real llama3.2: exit 0, prompt inspected
RUN_LIVE_EMBEDDINGS=1 uv run pytest -m live tests/test_live_e2e.py -s   # 1 passed (2026-10-17, real MiniLM): related claim kept, "zebra quantum pastry" got an empty pack at the default cut-off 0.2
# v1.2.0 `corroborated`, run 2026-10-18 on branch `docs/verify-corroborated` in an empty scratch directory via uv run --project (tests/fixtures/e2e_app copied to ./app; each command exit 0; no embeddings or provider involved)
uv run cognitive-mesh run e2e_session1.json --root app --trace-db T; uv run cognitive-mesh experience build --db T --graph G   # 1 claim (cart depends_on pricing)
uv run cognitive-mesh experience claims --db T --graph G --root app   # `single`, 1 independent source (analyzer, 2 records), [validated] by re-derivation
uv run cognitive-mesh experience review CLAIM --verdict confirmed --note TEXT --by NAME --root app --graph G --reviews R   # then claims --reviews R: `corroborated`, 2 independent sources (analyzer, person)
uv run cognitive-mesh experience outcome CLAIM --source check --result succeeded --detail TEXT --by NAME --root app --graph G --outcomes O   # then claims --reviews R --outcomes O: `corroborated`, 3 sources (analyzer, check, person). The check command text was only recorded; nothing ran it
# v1.3.0 application identity, run 2026-10-18 on branch `feature/v1.3.0-application-identity` in an empty scratch directory via uv run --project
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db T --app-id shop-one   # exit 0
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db T                    # exit 0
uv run cognitive-mesh traces show RUN_ID --db T   # "application id: shop-one [declared]" / "dep-app@dep_app [manifest]"
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db T --app-id "bad id"   # exit 2
# v1.3.0 validity, run 2026-10-18 on branch `feature/v1.3.0-environment-validity`, scratch dir, dep_app copy
uv run cognitive-mesh experience review CLAIM --verdict confirmed --note ok --by me --root app --graph G --reviews R --valid-until 2020-01-01   # exit 15, "valid_until is before today", nothing stored
uv run cognitive-mesh experience review CLAIM --verdict confirmed --note ok --by me --root app --graph G --reviews R --valid-until 2030-01-01   # exit 0; R at schema 2
uv run cognitive-mesh experience claims --db T --graph G --root app --reviews R   # exit 0; prints "date used for expiry: DATE (UTC)"
```
The v1.0.0 commands above were run 2026-10-17 (UTC) on branch `feature/v1.0.0-end-to-end` (not in a fresh clone or CI). `--json`, `--reviews`, exit codes 2, 3, 6, 11, 12 and 15 for `task`, and a first session, are covered by `tests/test_cli_task.py` through `main()` and `--json` by `tests/test_e2e_sessions.py` in separate processes with the fake embedder; they were not run by hand. Run later (2026-10-18, see the verification block above and progress.md): `task` with a real Ollama model, `--reviews` in a real session, a fresh clone, large synthetic traces and concurrent sessions. Still not run: `task` with a real Groq model.
The v0.9.0 commands above were run 2026-10-17 on branch `feature/v0.9.0-evidence-aware-retrieval` (not in a fresh clone or CI). Exit codes 3, 8, 11, 12, 13 and 15 for `recall` and `experience review` (missing graph, reviews file, root, index, database; unknown or non-claim node; refused note) and the format-1 graph refusal are covered by `tests/test_cli_recall.py` through `main()`, not run in the real CLI. Not run: `--include-unverified` with `--reviews`, very large traces, concurrent reviewers.
The `experience` commands were run 2026-10-17 on branch `feature/v0.8.0-experience-graph` from an empty directory (not in a fresh clone or CI). Not run: `show --incoming` and `--include-unverified` outside the tests, very large traces.
The `memory` commands were run 2026-10-17 on branch `feature/v0.7.0-semantic-memory` from an empty directory against a trace database made by `run ok.json` and `run needs_model.json` (not in a fresh clone or CI). Not run for real: an index built with a different real model (covered by tests with a fake embedder).
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
# Cognitive Mesh

[![CI](https://github.com/sohanroy676/cognitive-mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/sohanroy676/cognitive-mesh/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

**Application-aware AI agents with a memory that can show its evidence.**

Cognitive Mesh reads the code and configuration of one or more projects, plans work with a *conductor* model,
runs small specialised agents over the projects, and keeps a **traceable memory of what happened**: every step,
what it produced, which model made it, and which version of the code it looked at. Later tasks can recall that
experience, and every remembered claim comes with the records that support it and the records that contradict it.

> **Memory is evidence, not truth.** Similarity, repetition and a model's confidence never count as proof. Raw
> records stay separate from claims, conflicts are shown instead of settled quietly, and a claim's status is
> computed for one code version when you look at it, never stored as a verdict.

## Contents

[Features](#features) · [Quick start](#quick-start) · [Run the web app](#run-the-web-app) ·
[Command line](#command-line) · [Configuration](#configuration) · [How it works](#how-it-works) ·
[Project layout](#project-layout) · [Development](#development) · [Status and limitations](#status-and-limitations) ·
[Documentation](#documentation)

## Features

- **Reads applications without running them.** Local folders and public GitHub repositories (source, manifests,
  lockfiles, docs); secret-looking text is redacted before anything is stored.
- **Dependency graphs.** File-level graph of Python and JavaScript/TypeScript imports and declared packages, and a
  project-level graph across related repositories (package, API route, shared schema and config links). Every edge
  carries its evidence (file, line, rule); what cannot be resolved is listed as unresolved, never guessed.
- **Agents and a conductor.** A JSON plan is validated and run step by step with retries and explicit failures. A
  conductor model drafts the plan and picks a model per step from a catalog (Groq, local Ollama, any
  OpenAI-compatible endpoint), with the reason shown and a fallback if a model is rate-limited.
- **Small, reviewable changes.** A coder model drafts a diff of at most 3 files and 100 lines in a separate git
  worktree and branch; a contract check refuses it if it breaks something another project uses; nothing is pushed
  or merged without your approval of exactly that diff.
- **Persistent, evidence-aware memory.** Every run is recorded in SQLite; past steps are searchable by meaning
  (ChromaDB + sentence embeddings); an experience graph turns them into claims with supporting and contradicting
  records; recall puts those into a new task with each claim's status.
- **Local web app.** Chat, dependency graph and data browser on three local ports, described below.
- **Local read-only UI** (`ui`) for browsing recorded runs, claims and graphs.

## Quick start

Requirements: [git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you;
Windows: `winget install --id=astral-sh.uv -e`). Development has been on Windows 11; CI runs on Ubuntu. macOS has
not been tried.

```bash
git clone https://github.com/sohanroy676/cognitive-mesh.git
cd cognitive-mesh
git checkout feature/extended-roadmap-v1.7     # the branch that holds the web app (see Status)
uv sync                                        # large the first time: PyTorch and ChromaDB
uv run pytest                                  # a few minutes; needs no model or API key
```

Try it with no model and no key, on a sample app that ships with the repository:

```bash
uv run cognitive-mesh graph tests/fixtures/dep_app
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db traces.sqlite3
uv run cognitive-mesh experience build --db traces.sqlite3 --graph graph.sqlite3
uv run cognitive-mesh ui --db traces.sqlite3 --graph graph.sqlite3 --root tests/fixtures/dep_app
```

`data/agent-experiences/` is a real experience database (five Claude agents each added a small feature to a
different project and logged what they read, decided, changed and checked); open it with
`uv run cognitive-mesh ui --db data/agent-experiences/traces.sqlite3 --graph data/agent-experiences/graph.sqlite3`.

## Run the web app

`cognitive-mesh serve` starts three servers on `127.0.0.1` only, with one access token per start. Run it **from the
repository root**.

### 1. One-time setup

1. `uv sync` (see Quick start).
2. **Groq (coordinator, and a worker).** Put your key in a file named `.env` in the repository root (it is
   git-ignored; copy `.env.example` as a start) and allow remote content, because file contents are sent to Groq:
   ```
   GROQ_API_KEY=your-key
   GEMINI_API_KEY=your-gemini-key   # optional second remote worker (entry `gemini`)
   COGMESH_ALLOW_REMOTE_CONTENT=true
   COGMESH_ALLOW_TRAINING_USE=true
   ```
3. **Local model (optional second worker).** Install [Ollama](https://ollama.com), then `ollama pull llama3.2` and
   keep `ollama serve` running (the desktop app does this for you).
4. The model catalog is [`docs/serve-catalog.toml`](docs/serve-catalog.toml): entry `groq`
   (`openai/gpt-oss-120b`, all roles) and entry `local-llama` (llama3.2). Edit it to change models; it never holds
   a key. Check the Groq model name against what your account lists.

### 2. Start it

```powershell
# Windows PowerShell; home = the folder where everything is stored (workspace, traces, graphs, index, chat)
uv run cognitive-mesh serve --home E:\work\cogmesh-testing\home
```

```bash
# macOS / Linux
uv run cognitive-mesh serve --home ~/cogmesh-home
```

It prints three links, each with the token. Open them (keep this terminal running; stop with `Ctrl+C`):

| Port | Page | What it is | Writes? |
|------|------|------------|---------|
| 8770 | **Chat** | add projects, ask for a small change or a question, review the plan and diff, approve / revise / reject | only through approved actions |
| 8771 | **Dependency graph** | *Files and modules*: every file of a folder as a node, saved after the first read. *Projects and their links*: the graph across your projects | no |
| 8772 | **Data** | the whole vector index (search by meaning, nearest records, 2-D map) and every SQLite table, with filter, sort, full-row view | no |

Without `--catalog`, the server uses `docs/serve-catalog.toml` with `groq` as conductor if that file exists; it
prints which models are unusable (for example a missing key) at start-up. Use `--catalog FILE --conductor ENTRY` to
choose others, `--app-port/--graph-port/--data-port` to change ports, and `--no-browser` to skip opening one.

### 3. Use it

1. **Chat → Projects.** Enter a local folder (or a GitHub address) and *Add project*. It is stored in the
   workspace database in `--home` and is still there after a restart. Use *Add a folder or project to X* under a
   project to attach more folders; they are linked to it and appear in the graph. (Files are added by their folder.)
2. Choose the project to change and **Confirm dependencies and start**. Other projects are only ever read.
3. Choose **Only answer a question** or **Make a small change**, write the prompt and **Send**. Groq plans the
   steps and chooses a model for each; the plan, the reasons, the answers and (for a change) the diff, the contract
   check and the check result are shown. Model-written text is labelled unverified and shown as plain text.
4. For a change, **Approve**, **Revise** (with a note) or **Reject**. A change needs the project to be a git
   repository. Pushing and opening a pull request is a second, separate confirmation and needs `GITHUB_TOKEN` in
   `.env`. The project's check command runs only if you start the server with `--allow-unisolated-checks`; it then
   runs with your rights and is not isolated.
5. **Dependency graph.** Type a folder such as `E:\Projects` and *Show graph*. The first read of a large folder
   takes a while; after that it is loaded from the saved graph (also after a restart). *Re-read the folder* reads
   the files again. Use *Find* and zoom on large graphs; hover a node for its neighbours and the evidence.
6. **Data.** Browse what the app stored; nothing here can be edited.

To present from another computer (a recorded example that needs no keys, plus how to run it live and send a
pull request), see [`demo/README.md`](demo/README.md).

If it will not start: `cannot listen on the ports` means a port is in use (stop the other server or pass other
ports); `models: ... not usable` lines name the missing key or opt-in.

## Command line

Everything the web app uses is also available as commands, for example `graph`, `run`, `task`, `plan`, `recall`,
`memory`, `experience`, `workspace`, `change`, `decisions`, `models`, `eval`, `ui` and `serve`.
Run `uv run cognitive-mesh --help`. Options and exit codes are listed in [docs/COMMANDS.md](docs/COMMANDS.md);
version-by-version feature notes (catalog, decisions, conducted tasks, cross-project impact, small changes,
evaluation) are in [docs/FEATURES.md](docs/FEATURES.md).

## Configuration

Settings come from the environment, then an ignored `.env` in the working directory. Keys are never read from
anywhere else and never committed.

| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY` | Gemini credential (key variable of the catalog entry `gemini`) |
| `GROQ_API_KEY` | Groq credential (also the key variable named in the catalog entry `groq`) |
| `COGMESH_ALLOW_REMOTE_CONTENT` | must be `true` to use any remote model: file contents leave your machine |
| `COGMESH_ALLOW_TRAINING_USE` | must be `true` for catalog entries whose `data_use` is `may_train` or `unknown` |
| `COGMESH_PROVIDER` | `ollama`, `groq` or `catalog` for the single-model commands (`run`, `task`, `plan`) |
| `COGMESH_OLLAMA_BASE_URL`, `COGMESH_OLLAMA_MODEL` | where Ollama runs and which model (default `llama3.2`) |
| `COGMESH_MODEL_CATALOG`, `COGMESH_MODEL` | catalog file and entry for `COGMESH_PROVIDER=catalog` |
| `COGMESH_MODEL_TIMEOUT_S`, `COGMESH_LOG_LEVEL` | timeout in seconds and log level |
| `GITHUB_TOKEN` | only for opening a pull request (`change pr`, and the chat's pull-request step) |

## How it works

```
 projects (folders / GitHub) --> ingestion --> dependency graphs (file level, project level)
                                                     |
 request --> conductor model --> plan --> agents  <--+-- model catalog (Groq / Ollama / OpenAI-compatible)
                                            |
                                            v
              trace store (SQLite): raw, redacted records of every run, decision and model call
                                            |
                     +----------------------+----------------------+
                     v                                             v
          vector index (ChromaDB)                      experience graph (SQLite + NetworkX)
          past steps, searchable by meaning            claims linked to the runs behind them
                     +----------------------+----------------------+
                                            v
                         recall --> next task, web app, read-only UI
```

Principles: memory preserves provenance and conflicts; application files, retrieved memories and model output are
data, never instructions; raw records are never promoted to knowledge by being stored; documentation separates
what exists from what is planned. Decisions are recorded as ADRs in [docs/adr](docs/adr).

## Project layout

| Path | What is in it |
|---|---|
| `src/cognitive_mesh/` | the package: `ingestion/`, `graph/`, `workspace/`, `agents/`, `orchestrator/`, `providers/`, `traces/`, `memory/`, `experience/`, `retrieval/`, `impact/`, `change/`, `evaluate/`, `serve/` (web app), `ui/` (read-only UI) |
| `tests/` | the pytest suite; sample apps and plans in `tests/fixtures/` |
| `docs/` | [STATUS](docs/STATUS.md), [COMMANDS](docs/COMMANDS.md), [FEATURES](docs/FEATURES.md), the roadmaps, `serve-catalog.toml` and the ADRs in `docs/adr/` |
| `data/` | committed reference data: a real agent experience database and a saved dependency graph |
| `memory-bank/`, `session_prompt.md` | the project record and hand-off notes for contributors and coding agents |

## Development

```bash
uv run pytest                 # tests (the full suite takes about 10 minutes)
uv run ruff check .           # lint
uv run ruff format --check .  # formatting (drop --check to apply)
uv run mypy                   # strict type checking
```

- Work on a branch (`feature/…`, `fix/…`, `docs/…`, `chore/…`); never commit to `main`.
- Conventional Commits; regenerate the changelog with `uv run git-cliff -o CHANGELOG.md` as the last commit.
- A significant or hard-to-reverse decision gets an ADR in `docs/adr/` before any code.
- New behaviour needs tests, including failure cases and persistence across sessions.
- CI (GitHub Actions) runs install, lint, format, types, tests, a changelog check and a secret scan.

Contributor and agent instructions: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md).

## Status and limitations

Early development; everything described here runs and is tested, but model quality is not claimed. All work after
v1.6.0 (workspace, impact analysis, model catalog and routing, conductor, small changes, evaluation, web app) lives
on the branch `feature/extended-roadmap-v1.7` (tagged up to `v2.0.0`) and has not been merged to `main`.

- It does not decide what is true or detect every hallucination; it records evidence and disagreement.
- Analysis is static; dynamic imports and unusual build setups are reported as unresolved.
- Text a model wrote is stored as unverified.
- **Not yet verified:** approving a change and opening a pull request with a real model and real GitHub from the
  browser; llama3.2 as a worker (the conductor has so far chosen Groq, because the local model has no recorded
  results); browsers other than Edge; macOS and Linux for the web app. A saved dependency graph is not refreshed
  automatically.
- Servers listen on `127.0.0.1` only; there is no hosted service. Project checks are not isolated.

Version-by-version limitations: [docs/STATUS.md](docs/STATUS.md).

## Documentation

[Status](docs/STATUS.md) · [Commands and exit codes](docs/COMMANDS.md) · [Feature notes](docs/FEATURES.md) ·
[Roadmap](ROADMAP.md) · [Extended roadmap](docs/extended_roadmap.md) · [Decisions (ADRs)](docs/adr) ·
[Reference data](data/README.md)

## License

No license has been chosen yet, so all rights are reserved until one is added.

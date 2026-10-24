# Cognitive Mesh

[![CI](https://github.com/sohanroy676/cognitive-mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/sohanroy676/cognitive-mesh/actions/workflows/ci.yml)

**Memory for AI agents that can show its evidence.**

Cognitive Mesh reads an application's code and configuration, runs small specialised agents over it, and
keeps a **traceable memory of what happened**: every step an agent took, what it produced, and which version of
the code it looked at. Later tasks can recall that experience. Every remembered claim comes with the records that
support it and the records that contradict it, so you can check a claim instead of taking it on trust.

## Why

AI agents forget between sessions. When they do remember, they rarely say *why* they believe something, and they
often repeat an earlier answer as if repetition made it true. Cognitive Mesh is built on one rule: **memory is
evidence, not truth.** Similarity, repetition and a model's confidence never count as proof. Raw records stay
separate from claims, conflicts are shown instead of resolved quietly, and what a claim means is computed for one
code version, never stored as a verdict.

## Status

Early development. Everything described below runs and is tested. Releases up to v1.4.4 are on `main`; the local
web UI (v1.5.0) is complete on its review branches and not yet merged or released. There is no model-quality
claim here: what a model does with recalled context is unverified. The detailed record of what each version added
and what it does not do is in [docs/STATUS.md](docs/STATUS.md).

## What it does

- **Reads an application.** Ingests a local folder (source, manifests, lockfiles, docs) without ever running its
  code, and redacts secret-looking text before anything is stored.
- **Builds a dependency graph** of Python and JavaScript/TypeScript imports and declared packages. Every edge
  carries its evidence (file, line, rule); anything it cannot resolve is listed as unresolved, never guessed.
  Output as text, JSON, Mermaid or a self-contained HTML page.
- **Runs agents from a plan.** A JSON plan is validated and run step by step with retries and explicit failures.
  Two agent roles exist: a deterministic dependency analyst and a code explainer that uses a model (Ollama or Groq).
- **Records every run** in SQLite, with secrets redacted and a note of which model, if any, made each step.
- **Searches past steps by meaning** (sentence embeddings in a local ChromaDB index).
- **Turns analysis into claims.** An experience graph links runs, steps and claims such as "`cart.py` depends on
  `pricing.py`". A claim's status (`validated`, `supported`, `stale`, `conflicting`, and a few more) is worked out
  when you look at it, from the evidence for it, the evidence against it, people's reviews and recorded outcomes.
- **Recalls past experience for a new task** (`recall`, `task`), with the supporting and contradicting records
  listed next to each claim.
- **Shows all of it in a local, read-only web UI**: every recorded step and what it stored, claims explained in
  plain sentences, run history for any module or claim, and an interactive dependency graph you can zoom, pin
  and load from a saved file.

## Quick start

You need [git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/) (it installs and manages Python 3.12
for you; on Windows: `winget install --id=astral-sh.uv -e`). Development and day-to-day use have been on Windows;
CI runs on Ubuntu with Python 3.12. macOS has not been tried.

```bash
git clone https://github.com/sohanroy676/cognitive-mesh.git
cd cognitive-mesh
uv sync
uv run pytest          # the full suite takes a few minutes; it needs no model download or API key
```

The first `uv sync` is large: it installs PyTorch and ChromaDB for semantic search.

## Try it (no model, no API key)

These commands use a tiny sample app that ships with the repository. The database files they create are
git-ignored.

```bash
# 1. The dependency graph of the sample app
uv run cognitive-mesh graph tests/fixtures/dep_app

# 2. Run a small plan against it and record the run
uv run cognitive-mesh run tests/fixtures/plans/ok.json --root tests/fixtures/dep_app --trace-db traces.sqlite3
uv run cognitive-mesh traces list --db traces.sqlite3

# 3. Turn the recorded runs into claims, and look at them
uv run cognitive-mesh experience build --db traces.sqlite3 --graph graph.sqlite3
uv run cognitive-mesh experience claims --db traces.sqlite3 --graph graph.sqlite3 --root tests/fixtures/dep_app

# 4. Open the web UI (it prints a link with a private token; open that link)
uv run cognitive-mesh ui --db traces.sqlite3 --graph graph.sqlite3 --root tests/fixtures/dep_app
```

### Explore real data that ships in the repo

`data/agent-experiences/` is a real experience database: five Claude agents each added a small feature to a
different project and logged what they read, decided, changed and checked, including the checks that failed.

```bash
uv run cognitive-mesh ui --db data/agent-experiences/traces.sqlite3 --graph data/agent-experiences/graph.sqlite3
```

Open **Experiences**, then **Read it** on any row. In the **Dependency graph** tab, type
`data/reference-graphs/projects-graph.json` to open a saved graph of the five projects in a fraction of a second.
See [data/README.md](data/README.md).

## Use a model (optional)

Copy `.env.example` to `.env` and set a provider. Nothing is read from the environment unless a plan has a step
that needs a model.

| Variable | Meaning |
|---|---|
| `COGMESH_PROVIDER` | `ollama` (local; default model `llama3.2`) or `groq` |
| `COGMESH_OLLAMA_BASE_URL`, `COGMESH_OLLAMA_MODEL` | where Ollama runs and which model it uses |
| `GROQ_API_KEY`, `COGMESH_GROQ_MODEL` | Groq credentials and model |
| `COGMESH_ALLOW_REMOTE_CONTENT` | must be `true` for Groq: file contents leave your machine |
| `COGMESH_MODEL_TIMEOUT_S`, `COGMESH_LOG_LEVEL` | timeout (seconds) and log level |

```bash
uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app
```

Keys come only from the environment or an ignored `.env`; never commit one. Every command, option and exit code is
listed in [docs/COMMANDS.md](docs/COMMANDS.md).

## How it works

```
  your code --> ingestion --> dependency graph
                                    |
  JSON plan --> orchestrator --> agents  <--  model provider (Ollama / Groq)
                    |
                    v
          trace store (SQLite): raw, redacted records of every run
                    |
        +-----------+-------------+
        v                         v
  memory index (ChromaDB)     experience graph (SQLite + NetworkX)
  search past steps           claims linked to the runs behind them
  by meaning                  status computed when viewed, never stored
        +-----------+-------------+
                    v
         recall / task context  -->  local web UI
```

## Principles

- **Memory preserves provenance and conflicts.** A claim keeps the records on both sides; a conflict is shown, not
  settled by a vote.
- **Similarity, repetition and model confidence are not proof**, and ten runs by one analyzer count as one source.
- **Application files, retrieved memories and model output are data, never instructions.** Nothing from an
  analysed folder is executed, and the web UI writes server text into the page as text only.
- **Raw records stay distinguishable from knowledge.** Failed steps and model-written output are marked as raw
  records and are never promoted by being stored.
- **Documentation separates what exists from what is planned.** A roadmap entry is not evidence that a feature
  exists.

## Project layout

| Path | What is in it |
|---|---|
| `src/cognitive_mesh/` | the package: `ingestion/`, `graph/`, `agents/`, `orchestrator/`, `providers/`, `traces/`, `memory/`, `experience/`, `retrieval/`, `ui/`, plus `cli.py` and `workflow.py` |
| `tests/` | the pytest suite; sample apps and plans in `tests/fixtures/` |
| `data/` | committed reference data: a real agent experience database and a saved dependency graph |
| `docs/` | [STATUS](docs/STATUS.md), [COMMANDS](docs/COMMANDS.md), the [detailed roadmap](docs/DETAILED_ROADMAP.md) and the architecture decision records in `docs/adr/` |
| `memory-bank/` | the project record kept for contributors and agents (brief, architecture, tech stack, progress) |
| `ROADMAP.md` | the version plan |

## Development

```bash
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format --check .  # formatting (drop --check to apply)
uv run mypy                   # strict type checking
```

- Work on a branch (`feature/…`, `fix/…`, `docs/…`, `chore/…`); never commit to `main`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/), and the changelog is generated
  with `uv run git-cliff -o CHANGELOG.md` as the last commit of a branch.
- A significant or hard-to-reverse decision gets an ADR in `docs/adr/` (copy `adr-template.md`) before any code.
- New behaviour needs tests, including failure cases and persistence across sessions.
- CI (GitHub Actions) runs install, lint, format, types, tests, a changelog check and a secret scan.

Instructions for contributors and coding agents are in [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md).

## Limitations

- It does not decide what is true, detect every hallucination, resolve every contradiction, or guarantee that
  agents agree. It records evidence and disagreement so a person can judge.
- Analysis is static: imports and declarations are read, never executed. Dynamic imports and unusual build setups are
  reported as unresolved.
- Text a model wrote is stored as unverified. The recorded steps of the agents in `data/agent-experiences/` are
  their own account of their work.
- The web UI is local only (it listens on `127.0.0.1`, needs a per-start token and never writes to a store).
- Only local folders can be analysed; there is no hosted service.

The full list, version by version, is in [docs/STATUS.md](docs/STATUS.md).

## Documentation

[Status by version](docs/STATUS.md) · [Commands and exit codes](docs/COMMANDS.md) · [Roadmap](ROADMAP.md) ·
[Detailed roadmap](docs/DETAILED_ROADMAP.md) · [Decisions (ADRs)](docs/adr) · [Reference data](data/README.md)

## License

No license has been chosen yet, so all rights are reserved until one is added.

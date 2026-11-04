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

Early development. Everything described below runs and is tested. Releases up to v1.6.0 (workspace of related projects and manifest-evidenced cross-project package edges, `cognitive-mesh workspace`; v1.5.0 is the local
read-only web UI) are on `main`. There is no model-quality
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
- **Runs a local web app (v2.0.0)**: a chat that takes the projects that belong together, shows the
  dependencies found between them, plans each prompt with a conductor model that picks a catalog model per step, reads
  the other projects, makes a small change only if it breaks nothing they use, and waits for your approval. A
  separate dependency-graph page and a separate data browser (the whole vector index and every SQLite store) sit on
  their own ports. See [Run the web app](#run-the-web-app-v200).
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

## Run the web app (v2.0.0)

One command starts three local servers (127.0.0.1 only, one access token per start, printed as three URLs):

```bash
uv run cognitive-mesh serve --home my-mesh --catalog catalog.toml --conductor ENTRY
```

| Port | Page | Can it write? |
|------|------|---------------|
| 8770 | **Chat**: add projects, confirm the dependencies, ask for a small change or a question, review the plan, the impact check and the diff, then approve, revise or reject | Yes, but only the listed actions, each behind an approval request tied to the diff you were shown (used once, expires, dead after a restart) |
| 8771 | **Dependency graph**: the workspace graph with evidence for every line; detected edges are solid, ones you declared are dashed; "who depends on this?" for a target | No |
| 8772 | **Data**: the whole vector index (paged, search by meaning, nearest records, a 2-D map) and every table of the trace, workspace, chat, outcomes and graph files, with filter, sort and a full-row view | No (read-only on purpose: these are evidence records) |

How a prompt is handled: the conductor (a model from your catalog) plans read-only steps and chooses a model for
each, with its reason shown; other projects in the workspace are only ever read. In **Make a small change** mode the
coder then drafts a diff of at most 3 files and 100 lines in a separate git worktree and branch, the contract check
refuses it if it breaks something another project uses, and the declared check runs only if you started the server
with `--allow-unisolated-checks` (it runs with your rights, not isolated). Nothing is pushed unless you approve and
then confirm "open a pull request", which also needs `GITHUB_TOKEN` and `COGMESH_ALLOW_REMOTE_CONTENT=true`. Every
turn records its plan, decisions with their reasoning, model calls and your verdict; the **Trace** box under a turn
and the Data page lead to them. Model-written text is always labelled unverified and shown as plain text.

Without `--catalog` and `--conductor` the servers still start and you can add projects and browse the graph and the
data; the chat just cannot start a turn. The `--home` folder holds every file (workspace, traces, index, graph, chat,
worktrees). Only a **local folder** can be changed; a GitHub project is read-only.

What has been run: the three pages in headless Edge and the HTTP flow (plan, draft, impact, approve, replay refused,
reject, revise, restart) with scripted models and a fake embedder, and `serve` itself with no model. **Not yet run:**
a real model (local or free-tier), a real GitHub push or pull request, clicking the approval buttons in a browser,
and browsers other than Edge. See [ADR-0044](docs/adr/0044-local-api-server-and-chat.md).

## Use a model (optional)

Copy `.env.example` to `.env` and set a provider. Nothing is read from the environment unless a plan has a step
that needs a model.

| Variable | Meaning |
|---|---|
| `COGMESH_PROVIDER` | `ollama` (local; default model `llama3.2`), `groq`, or `catalog` (a model from your catalog file) |
| `COGMESH_OLLAMA_BASE_URL`, `COGMESH_OLLAMA_MODEL` | where Ollama runs and which model it uses |
| `GROQ_API_KEY`, `COGMESH_GROQ_MODEL` | Groq credentials and model |
| `COGMESH_ALLOW_REMOTE_CONTENT` | must be `true` for Groq: file contents leave your machine |
| `COGMESH_MODEL_CATALOG`, `COGMESH_MODEL` | with `catalog`: the catalog file and the entry id to use (also `--catalog FILE` and `--model ID` on `run`, `task` and `plan`) |
| `COGMESH_ALLOW_TRAINING_USE` | must be `true` for catalog entries that declare `data_use` `may_train` or `unknown` |
| `COGMESH_MODEL_TIMEOUT_S`, `COGMESH_LOG_LEVEL` | timeout (seconds) and log level |

```bash
uv run cognitive-mesh run tests/fixtures/plans/needs_model.json --root tests/fixtures/sample_app
```

A catalog (v1.8.0, [ADR-0038](docs/adr/0038-model-catalog-and-call-records.md); example in
[docs/model-catalog.example.toml](docs/model-catalog.example.toml)) lists models behind an OpenAI-compatible
endpoint or Ollama. It never holds a key, only the name of the variable that does. A remote entry needs
`COGMESH_ALLOW_REMOTE_CONTENT=true`, and a free tier whose `data_use` you declared `may_train` or `unknown`
also needs `COGMESH_ALLOW_TRAINING_USE=true`; `data_use` is your statement, not checked. Each model call is
recorded with the run (latency, tokens when the provider reports them, result kind; never prompt or reply
text): `cognitive-mesh models stats --db FILE` shows counts per model, role and task type and does not rank
models. Request shapes for Gemini, OpenRouter and Mistral are from their documentation and have not been run
against the real services.

Decisions (v1.9.0, [ADR-0039](docs/adr/0039-decision-records.md)) are records of what was decided, the options, the reasoning and the evidence cited: `cognitive-mesh decisions record` stores one (a cited file keeps its hash), `decisions trace --pr ORG/REPO#N` goes from a pull request back to the decisions and their evidence, and a status (`stale` when a cited file changed, `validated` only by a recorded outcome for the current code) is computed each time. Reasoning is what its author wrote, never evidence for itself; nothing writes a decision on its own yet.

Conducted tasks (v1.10.0, [ADR-0040](docs/adr/0040-orchestrator-model.md)): `cognitive-mesh task REQUEST --conduct --conductor ENTRY --catalog FILE ...` lets a conductor model draft the plan and choose a catalog model for each model step. Its plan is data: it is validated before anything runs (an unusable plan exits 22 and nothing runs), every assignment shows a count-based evidence label (`recorded` from 20 calls with a known outcome, otherwise `insufficient_evidence`; nothing is called better), a rate-limited or unavailable model hands over to the next eligible catalog entries, and the plan, each assignment and every call are recorded, with the conductor's reasons stored as `model_unverified` decisions. `--explore-rate` is off by default. Only a scratch stub server has conducted a task so far; no real model has.

Cross-project impact (v1.11.0, [ADR-0041](docs/adr/0041-cross-project-impact-analysis.md)): `cognitive-mesh workspace impact PROJECT --target T --workspace FILE` lists every other workspace project and location that depends on a file, route, table, environment variable, package or exported name of the current project, with the evidence on both sides; what may depend on it but cannot be resolved (a computed URL, a whole-module import) is listed apart as `unknown`. `workspace contract-diff PROJECT [--base REF]` compares the project's working tree with a git ref (a read-only `git archive`; nothing is checked out or written) and labels each difference that other projects use as `breaking`, `compatible` or `unknown`, where `unknown` is never counted as a pass. `run` and `task` accept `--workspace FILE --project NAME`, so the `dependents_of` capability and the model-backed `reader` role can see the other projects, read-only (only the named project may ever be written). Only the contracts the extractors can read are compared (routes, typed columns, JSON Schema properties, configuration names, package version and exports); request and response bodies are not. The reader has not been run with a real model.

Showing improvement (v1.13.0, [ADR-0043](docs/adr/0043-showing-improvement.md)): `cognitive-mesh eval run TASKSET.toml --out DIR --model ID --catalog FILE [--repeats N] [--allow-unisolated-checks]` runs each task of a small task set through the change pipeline twice, memory off and memory on, with the same model; the memory-on arm sees only its own earlier tasks. `eval report DIR [--json]` prints counts for both arms, lists every aborted or unfinished item and says `too few paired tasks to conclude anything` below 20 pairs. It shows nothing about improvement at this scale: it is the tool for measuring it later. Checks run with your privileges and are not isolated.

Small changes (v1.12.0, [ADR-0042](docs/adr/0042-small-change-agent.md)): `cognitive-mesh change propose REQUEST --root DIR --workspace FILE --project NAME --db FILE --outcomes FILE --worktrees DIR --file PATH ...` asks a coder model for a small unified diff of the current project only (default limit 3 files and 100 changed lines). The diff is judged by our own parser first (plain relative paths inside the project, no renames, deletions, binaries, modes or secrets files, nothing in another workspace project), applied in a separate git worktree on a new `cogmesh/change-ID` branch (the checked-out branch and working tree are never touched, repository hooks never run), and held against the v1.11.0 contract diff: a change that breaks something another project uses is refused and sent back, up to two revisions. The project's check command is the one a person declared with `workspace set-check PROJECT -- COMMAND ARG...` (never read from the repository); it runs only with `--allow-unisolated-checks`, in a reduced environment with a timeout and a process-tree kill, and its exit code is stored as a `check` outcome labelled `env_only`: this is **not** isolation, the command runs with your rights and a raw socket still works. `change approve ID --by NAME` records a person's approval of exactly one commit and diff; `change pr ID --root DIR` then pushes the branch (never forced) and opens a pull request with a `GITHUB_TOKEN` and `COGMESH_ALLOW_REMOTE_CONTENT=true`. Nothing is pushed without those two steps and nothing is merged by the tool. The diff, its summary and its reasoning are a model's words (`model_unverified`); only a recorded check validates, and only for that command. No real model has written a diff and no pull request has been opened on github.com yet.

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
- The web UI (`ui`) and the graph and data servers of `serve` are local only and never write; the chat server of
  `serve` writes only through approved actions. All listen on `127.0.0.1` and need a per-start token.
- Only local folders can be analysed; there is no hosted service.

The full list, version by version, is in [docs/STATUS.md](docs/STATUS.md).

## Documentation

[Status by version](docs/STATUS.md) · [Commands and exit codes](docs/COMMANDS.md) · [Roadmap](ROADMAP.md) ·
[Detailed roadmap](docs/DETAILED_ROADMAP.md) · [Decisions (ADRs)](docs/adr) · [Reference data](data/README.md)

## License

No license has been chosen yet, so all rights are reserved until one is added.

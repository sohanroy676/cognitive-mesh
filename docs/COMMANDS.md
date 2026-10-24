# Commands and exit codes

The full command list. Every command below was run successfully (the verified list with dates and outputs is in [`memory-bank/tech-stack.md`](../memory-bank/tech-stack.md)). For a first walk-through see the [README](../README.md).

## Setup and usage

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (Windows: `winget install --id=astral-sh.uv -e`).

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run git-cliff -o CHANGELOG.md   # regenerate CHANGELOG.md from Conventional Commits; CI fails if stale
```

Commands below were run successfully (see `memory-bank/tech-stack.md` for the verified list):

```
COGMESH_PROVIDER=ollama uv run cognitive-mesh           # validates configuration, exit 0
uv run cognitive-mesh ingest PATH [--json] [--include-content]
uv run cognitive-mesh graph PATH [--json] [--deps-of NODE_ID [--transitive]]
uv run cognitive-mesh graph PATH --format mermaid|html [--output FILE] [--max-nodes N] [--max-edges N]
uv run cognitive-mesh agents [--json]                  # lists roles and capabilities, no config needed
uv run cognitive-mesh run PLAN.json --root DIR [--json]   # runs a task plan, no config needed
uv run cognitive-mesh run PLAN.json --root DIR --trace-db FILE   # also records the run (SQLite)
uv run cognitive-mesh traces list --db FILE [--json]
uv run cognitive-mesh traces show RUN_ID --db FILE [--json]
uv run cognitive-mesh memory index --db FILE --index DIR [--json]   # needs no provider; first use downloads the model
uv run cognitive-mesh memory search "QUERY" --db FILE --index DIR [--k N] [--min-similarity X] [--json]
uv run cognitive-mesh experience build --db FILE --graph FILE [--json]   # needs no provider and does not load the embedding model
uv run cognitive-mesh experience claims --db FILE --graph FILE [--include-unverified] [--json]
uv run cognitive-mesh experience show NODE_ID --db FILE --graph FILE [--transitive] [--incoming] [--include-unverified] [--json]
uv run cognitive-mesh experience check --db FILE --graph FILE [--json]
uv run cognitive-mesh experience claims --db FILE --graph FILE [--reviews FILE] [--root DIR] [--outcomes FILE] [--json]   # adds evidence status and recorded outcomes
uv run cognitive-mesh experience review CLAIM_ID --verdict confirmed|rejected --note TEXT --by NAME --root DIR --graph FILE --reviews FILE [--json]
uv run cognitive-mesh experience outcome CLAIM_ID --source check|person --result succeeded|failed|ambiguous --detail TEXT --by NAME --root DIR --graph FILE --outcomes FILE [--json]
uv run cognitive-mesh experience promote CLAIM_ID --by NAME --root DIR --db FILE --graph FILE --outcomes FILE [--reviews FILE] [--json]   # exit 17 if refused; the refusal is recorded
uv run cognitive-mesh recall "QUERY" --db FILE --index DIR --graph FILE [--reviews FILE] [--root DIR] [--k N] [--min-similarity X] [--json]   # needs no provider
uv run cognitive-mesh task "REQUEST" --plan PLAN.json --root DIR --db FILE --index DIR --graph FILE [--reviews FILE] [--k N] [--min-similarity X] [--json]   # recalled context + run; no provider unless the plan has a model step
uv run cognitive-mesh ui [--db FILE] [--graph FILE] [--index DIR] [--reviews FILE] [--outcomes FILE] [--root DIR] [--port N] [--no-browser]   # local read-only web UI on 127.0.0.1; prints a URL with this session's token (ADR-0031)
```

Exit codes: 0 ok, 2 configuration or usage error, 3 unusable ingestion root, 4 unknown graph node,
5 output file could not be written, 6 invalid or unreadable plan, 7 task failed or incomplete, 8 trace store error or malformed trace, 9 unknown trace run id, 11 memory index missing, unusable or built with another model, 12 experience graph file missing or unusable, 13 unknown experience graph node, 14 graph sources not confirmed (`experience check`), 15 reviews file missing or unusable, or a review that was refused, 14 `experience check` found trace sources that no longer match, 18 `ui` could not listen on its port.
Configuration comes from the environment or an ignored `.env` (copy `.env.example`); never commit
secrets. `uv build --out-dir DIR` builds an sdist and wheel (wheel installed and run in a fresh venv on Windows; not published, not run in CI). The package version is derived from git tags (hatch-vcs): a build at tag `vX.Y.Z` is `X.Y.Z`, later commits are `X.Y.(Z+1).devN`, and a checkout without tags is `0.1.devN`.

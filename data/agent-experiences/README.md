# Agent experiences: five agents, five projects

A real experience database, made on 2026-10-25. Five Claude agents (model `claude-sonnet-5-5`) each added one
small feature to a different project and logged what they read, every decision with its reasons, the change and
every check, including the ones that failed. The records are stored the way Cognitive Mesh stores experience.

Nothing here is validated knowledge. The agents' own steps are **model-written and unverified**: they are the
agents' account of what they did, and nothing proves they decided in the order logged.

## Open it

```
uv run cognitive-mesh ui --db data/agent-experiences/traces.sqlite3 --graph data/agent-experiences/graph.sqlite3
```

Experiences lists every recorded step; "Read it" shows what a step stored. Claims lists the claims drawn from the
analyzer runs; Runs lists the 15 runs. The SQLite files are the canonical copy. `exports/` holds the same records
as JSON so they can be read and compared in git; they were written from the databases by `tools/export_experience_data.py`
(the same code the UI uses), and `reports/` holds the agents' original logs.

## What is in it

- 15 runs, 82 experiences (steps), 164 claims, from five projects.
- For each project, three runs, in this order: the real analyzer (`unresolved_report`, `dependencies_of`) on the
  project **before** the change, the agent's steps, and the same analysis **after** the agent's last commit.
- Steps by what was done: `decide` 20, `dependencies_of` 21, `run_checks` 13, `unresolved_report` 10,
  `change_code` 8, `read_project` 5, `reflect` 5.
- 78 steps succeeded and 4 failed. The failures are real checks that failed on the way and were fixed:
  a variable shadowed in `claims_view` that emptied the default output, a test that assumed claims with mixed
  statuses, a leftover `.clineignore` mention in a template, and a filename with a `"` that Windows rejects.

| Project | Branch of the agent's clone | Feature |
|---|---|---|
| DependencyGraph | `agent/json-output` | `graph scan --format text\|json` |
| Incident-commander | `agent/alerts-command` | read-only `alerts` command |
| agent-config-deployer | `agent/claude-code-agent` | Claude Code as a second agent through its JSON config |
| cognitive-mesh | `agent/claims-status-filter` | `--status` filter for `experience claims` |
| onboarding-coordinator | `agent/status-command` | read-only `status` command |

The code those agents wrote is **not** in this repository. It lives in clones under `E:\experience-db\work\`
(on this machine only), each on its branch with one commit, and nothing was pushed.

## Which model made what

- The 47 steps the agents wrote are `model_unverified`, with provider `anthropic` and model `claude-sonnet-5-5`
  stored on each step.
- The 31 analyzer steps are `deterministic`: no model took part, so they carry no model. They are not labelled
  with one, because that would be false.
- The 4 failed steps carry no model either (a failure record has no model field). Each agent run states who made
  it in its plan (`made_by`), so the run page shows it.
- No step was made by Llama. The project's own default for model steps it runs itself is still Ollama
  `llama3.2` (unchanged).

## Limits

- Claims are statuses computed when viewed, never stored. With no application folder given, each claim reads
  `supported` (164 of 164) and whether it is still current is unknown. To check a claim against code, pass a project's
  folder as `--root`.
- The agents' decisions come from their own logs. The checks they report were run by them; only the cognitive-mesh
  agent's UI and claims tests (102 passed) were re-run independently, against its clone.
- Several things were not run: the agent-config-deployer GUI, Claude Code against the deployed files, and the
  onboarding `status` command against a real provisioned run.
- The records mention the private structure of these projects (file names, decisions, short code excerpts).
  Secret-like patterns and personal details were scanned for before committing and none were found, but redaction is
  pattern-based. Think before pushing this folder anywhere public.

## How it was made

`AGENT_BRIEF.md` is the brief every agent followed. `tools/build_experience_db.py` validates each report, runs the
analyzer on the base commit and on the head commit, and stores the agent's steps through the project's own trace
store (secrets are redacted before storing). Paths in the tools are those of the machine it was built on.

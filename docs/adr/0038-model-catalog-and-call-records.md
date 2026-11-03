# ADR-0038: Model catalog, OpenAI-compatible client and call records

## Status
Accepted by the user 2026-11-03 (drafted in plan mode; the four marked user decisions were taken before drafting)

## Date
2026-11-03

## Context
Extended roadmap v1.8.0 asks that any model reachable with an API key can be used (free tiers such as Gemini's
are the development default), that switching models is a configuration edit, and that every model call leaves a
performance record. Today there are two hand-written clients: Ollama (ADR-0012) and Groq (ADR-0013).

Facts from the code (read 2026-11-03):
- `ModelClient.complete(ModelRequest) -> ModelResponse(provider, model, text)`; agents reach it only through
  `agents/base.py::call_model`. `GroqClient` has a **constant endpoint** (ADR-0013 rejected a base-URL setting so
  the key can only go to Groq) and ignores `usage`.
- `providers/errors.py` maps HTTP status and transport failures to `ProviderErrorKind` with fixed messages; 429
  and 498 are `rate_limited`.
- Trace schema is at version 4 (`MIGRATIONS` 1..3). `AttemptTrace.agent_task_id` is `{task}.{step}.{attempt_no}`.
  `ModelInfo` on a result holds provider and model only; there is no latency, token or error record for a call,
  and failed attempts keep no model information.
- Settings accept `COGMESH_PROVIDER=groq|ollama`. `python-dotenv` is in `uv.lock` only as a dependency of
  `pydantic-settings`.

Constraints unchanged: keys come only from the environment or an ignored `.env` as `SecretStr`; no prompt, reply,
body, header, exception text or key in any error, log, trace or catalog; model output is untrusted and
`model_unverified`; no network in default tests; document only what was run.

## Decision

User decisions (2026-11-03):
1. **Free tiers:** each catalog entry declares `data_use = may_train | no_training | unknown`. This is the
   person's statement, not something the program verifies. `may_train` and `unknown` additionally require
   `COGMESH_ALLOW_TRAINING_USE=true`. Every remote entry still requires `COGMESH_ALLOW_REMOTE_CONTENT=true`
   (ADR-0013 stays in force). A fixed warning naming host and `data_use` goes to stderr on every run that builds a
   remote client.
2. **Key safety:** a non-loopback `base_url` must be `https` with no credentials, query or fragment. Each remote
   entry names its key variable (`[A-Z][A-Z0-9_]*_API_KEY`). The catalog is read only from an explicit
   `--catalog` / `COGMESH_MODEL_CATALOG` path, never from an analysed project. The host appears in the warning.
3. **Selection:** `COGMESH_PROVIDER=catalog` with `COGMESH_MODEL=<entry id>` (or `--model ID` on `run`, `task`
   and `plan`). `groq` and `ollama` settings keep working unchanged. Using an entry for an agent role it does not
   list is a configuration error (exit 2).
4. **Retention:** call records live with the run in the trace store, append-only, never pruned.

Design:

1. **Client.** New `providers/openai_compat.py`: `OpenAICompatClient(base_url, model, api_key: SecretStr | None,
   options, transport)` on the unchanged `providers/http.py`. Reply parsing moves from `groq.py::_parse_reply`
   to a shared `providers/openai_wire.py` used by both clients (same checks: object, non-empty `choices`, string
   content, `finish_reason == "stop"`, `<think>` markers rejected). `GroqClient` keeps its constant endpoint and
   behaviour. Failure mapping is the existing `providers/errors.py`. HTTP 429 is `rate_limited` whether it means a
   rate limit or an exhausted quota: the body is never read to tell them apart (a stated limit; `Retry-After` is
   still not read, as in ADR-0013). Per-entry wire options: `json_mode` (bool), `max_tokens_field`
   (`max_completion_tokens` | `max_tokens`), `reasoning_effort` (optional string). Request shapes for Gemini's
   compatible endpoint, OpenRouter and Mistral come from their documentation and are **unverified** until an
   opt-in live test has run against each.
2. **Token usage.** `ModelResponse` gains `prompt_tokens: int | None` and `completion_tokens: int | None`.
   Read from `usage.prompt_tokens` / `usage.completion_tokens` (OpenAI wire) and `prompt_eval_count` /
   `eval_count` (Ollama). A missing, negative, non-integer or boolean value is `None` (unknown), never 0. This
   amends ADR-0013's "`usage` is ignored": only these two integers are read.
3. **Catalog.** A TOML file read with stdlib `tomllib` (no new dependency). `[models.<id>]` tables with:
   `wire` (`openai_compatible` | `ollama`), `base_url`, `model`, `api_key_env` (optional for loopback),
   `context_tokens`, `tier` (`free` | `paid`), `data_use`, `roles` (agent roles, plus `planner` for `plan`), and
   the wire options. Pydantic models with `extra="forbid"`; ids match `[a-z0-9][a-z0-9._-]*`; model names use the
   rule in `config.py::_MODEL_NAME`. Every string value is also run through `ingestion/secrets.py`
   (`redact_secrets`, `looks_random`) and the file is refused if one looks like a secret, which catches a key
   pasted into the catalog. A host is remote unless it is `localhost` or a loopback address. Errors name the entry
   and field, never the value. A file size cap and an entry-count cap apply.
4. **Config and factory.** `Settings.provider` adds `catalog`; new settings `model`, `model_catalog`,
   `allow_training_use` (default false). `build_model_client(settings, role)` loads the catalog and checks, in
   order: entry exists, role allowed, https rule, `COGMESH_ALLOW_REMOTE_CONTENT`, `COGMESH_ALLOW_TRAINING_USE`,
   key present. The key is read from the process environment, then from the ignored `.env` through
   `dotenv_values` (`python-dotenv` becomes a declared direct dependency at the already-locked version; it is a
   maintained package by the same group as `pydantic-settings`' own dependency). Same printable-ASCII rule as
   `GROQ_API_KEY`. Every failure is a `ConfigError` (exit 2) that names variables, never values. A key variable
   is never shared silently: a catalog where one `api_key_env` is used by entries with different hosts is
   reported by `models list` as a warning (not an error).
5. **Call records.** A `RecordingClient` wrapper (`providers/recording.py`) times each `complete()` with a
   monotonic clock and keeps: `agent_task_id`, `call_no`, entry id (or none for `groq`/`ollama` settings),
   provider, model, agent role, task type (the capability), `latency_ms`, `prompt_tokens`, `completion_tokens`,
   and `result` = `ok` | a `ProviderErrorKind` value | `client_error` (a non-`ProviderError` exception). Latency
   is recorded for failures too. `Orchestrator` gets an optional `on_attempt(agent_task_id, agent_id,
   capability)` callback invoked just before `agent.run` (`orchestrator/runner.py`, line ~130), so a record is
   keyed by the existing `AttemptTrace.agent_task_id` and joins its attempt, step status and run: this is the
   "outcome link", and no outcome is inferred beyond the step status. No prompt, reply, header, URL path or error
   text is stored.
6. **Storage.** `MIGRATIONS[4]` takes the trace schema to 5 with a table `model_calls` (primary key `(run_id,
   agent_task_id, call_no)`, token columns nullable, `result` constrained to the values above). Calls are written
   in the run's finishing transaction, so a run that crashes loses its calls (it reads back `interrupted`, as in
   ADR-0011). Rows are validated on read and cross-checked against the attempt; a malformed row is reported,
   never dropped silently. `plan` drafting is not a run and is not recorded in v1.8.0 (stated limit). Runs
   recorded before schema 5 have no call records; stats say so and never invent them.
7. **CLI.** `models list --catalog FILE [--json]` (no network): host, tier, `data_use`, roles, key variable name
   and whether it is set, never the value. `models stats --db FILE [--task-type T] [--json]`: grouped by (entry or
   provider, model, role, task type), never across task types; calls by result, median and p90 latency of `ok`
   calls with their count, token totals with the number of calls whose tokens were known and unknown, and step
   outcomes for the attempts. Output is sorted by name, always shows sample counts, ends with a fixed note that
   the numbers describe recorded calls and do not rank models, and never calls a model "best". New exit code
   `EXIT_CATALOG_ERROR = 20` for an unreadable or invalid catalog (a missing trace database stays exit 8).
8. **Retention.** Records are append-only and kept as long as the database; deleting a run is not supported
   today. No pruning command.

## Alternatives considered
- **A fixed allowlist of provider hosts in code:** strongest key safety, but every new provider is a code change,
  which defeats "switching models is a catalog edit".
- **JSON or YAML catalog:** `tomllib` is stdlib; YAML needs a dependency.
- **Replacing the `groq`/`ollama` settings by the catalog:** breaks existing `.env` files for no gain now.
- **Warning only for free-tier data use:** the person asked for a second opt-in.
- **Reading the 429 body to separate rate limit from quota:** would put provider text on a parsing path and
  differ per provider; both retry the same way under the run budget.
- **Recording each call immediately:** survives a crash but breaks the single-transaction finish rule (ADR-0011).
- **Pinning one key variable to one host:** reduced to a `models list` warning; a hard rule would block legitimate
  gateways that proxy several hosts.

## Consequences
- Switching models is a catalog edit plus `COGMESH_MODEL`.
- ADR-0013's "key can only go to Groq" now holds only for `COGMESH_PROVIDER=groq`. For catalog entries the
  guarantees are: https, the named variable only, an explicit catalog path, and the warning.
- `data_use` is only as true as the person's entry; nothing checks the provider's real terms.
- 429 for quota exhaustion is retried by the orchestrator's bounded retries and then fails; stats count it as
  `rate_limited`.
- Samples are small by nature; the stats make no ranking claim, and routing on them waits for v1.10.0 with a
  stated minimum sample count.
- Not done: streaming, `Retry-After`, plan-drafting records, pruning, verifying the request shape for any
  provider other than Groq and Ollama before a live test is run.

## Tests (to be written with the code)
Fake transport per wire format and every failure row; parity of OpenAI-compatible, Groq and Ollama on the same
failures; 429 and quota-shaped 429 mapped to `rate_limited` with bounded retries; timeout; 404 for a removed
model; missing or garbled usage becomes unknown; canary key never in errors, traces, catalog output or CLI
output; refused catalog cases (http remote URL, key-looking value, unknown field, unknown role, oversize file,
missing opt-ins, missing key) all exit 2 or 20 as specified; records of two task types never merged; call records
persist across processes; migration 4 to 5 on an old database; opt-in live tests per provider (`live` marker plus
`RUN_LIVE_<PROVIDER>=1` and a key variable), skipped by default and in CI.

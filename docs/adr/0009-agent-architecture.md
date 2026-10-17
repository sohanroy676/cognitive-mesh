# ADR-0009: Agent architecture and the provider boundary

## Status
Accepted (plan approved by the user 2026-10-17: two roles, provider-owned timeouts, `agents` CLI command)

## Date
2026-10-17

## Context
v0.4.0 needs a common agent interface, discoverable roles and capabilities, input/output contracts
and explicit failures, with agent logic separated from model-provider details. Nothing calls a real
provider yet (no Groq or Ollama client). Inputs to agents, ingested file content and model output
are untrusted data (CLAUDE.md). The v0.5.0 orchestrator must be able to choose agents from declared
capabilities alone, without knowing any agent's internals, and must never mistake a failure for a
success.

## Decision
- **Packages:** `cognitive_mesh.agents` and `cognitive_mesh.providers`. Agents import only the
  provider protocol. No network code, no provider SDK, no new dependency.
- **Provider interface (`providers/base.py`).** `ModelRequest` (system, prompt, `timeout_s` > 0,
  `max_output_chars`, `response_format`), `ModelResponse` (provider, model, text),
  `ProviderErrorKind` (timeout, unavailable, rate_limited, auth, bad_response, other),
  `ProviderError` (has `kind` and `retryable`) and the `ModelClient` Protocol
  (`provider`, `model`, `complete(request)`). **Timeouts are provider-owned:** the client must
  enforce `timeout_s` and raise `ProviderError(kind=timeout)`. There is no watchdog thread; a client
  that hangs is not interrupted (a hard deadline can come with the v0.5.0 orchestrator).
- **Contracts (`agents/models.py`, frozen pydantic).** `CapabilitySpec` (name, description,
  `uses_model`, JSON schemas of input and output), `AgentSpec` (id, role, description, capabilities),
  `AgentTask` (task id, capability, inputs), `AgentContext` (optional ingestion result and
  dependency graph; agents never read the disk), and the result union `AgentSuccess | AgentFailure`
  discriminated on `status`. Both echo agent id, task id and capability.
  - `AgentSuccess`: validated `output`, `basis` (`deterministic` or `model_unverified`), `evidence`
    (source path, sha256, line, rule), and `model` (provider, model) when a model was used.
    `model_unverified` means a model produced the content; it is not proof of truth.
  - `AgentFailure`: `kind`, a `message`, and `retryable`. Kinds: invalid_input,
    unsupported_capability, missing_context, provider_unavailable, provider_timeout, provider_error,
    malformed_output, internal_error.
- **Base class (`agents/base.py`).** An agent is a table of capabilities (name, description, input
  model, output model, handler, `uses_model`). `spec()` is derived from the table, so an agent cannot
  declare a capability without a handler. `run(task, context)` never raises. It checks the
  capability, validates inputs (`extra="forbid"`, length limits), calls the handler, validates the
  output model and wraps the result. A handler reports expected problems by raising `AgentFault`; any
  other exception becomes `internal_error` naming only the exception type.
- **Failure messages never contain input values, file content, model text or exception messages**
  (same rule as ingestion diagnostics). They hold ids, kinds and pydantic error locations and types.
- **One provider path.** `call_model(client, request)` is the only place that handles provider
  behavior: `ProviderError` maps to provider_timeout / provider_unavailable / provider_error (with
  `retryable`), any other client exception to provider_error, and a non-`ModelResponse` return or
  over-long text to malformed_output. Agents do not duplicate it.
- **Roles.**
  - `dependency_analyst`: deterministic; needs the graph. Capabilities `dependencies_of`
    (node id, optional transitive, limit) and `unresolved_report` (limit). Wraps the existing
    `DependencyGraph` methods; every dependency carries its edge evidence.
  - `code_explainer`: uses a model; needs ingestion. Capability `explain_file` (relative path). The
    file content (already redacted by ingestion) goes into the prompt as one JSON-encoded string
    next to a note that it is untrusted data. The reply must be strict JSON
    `{summary, symbols}` with no extra fields; anything else is malformed_output. Success is
    `model_unverified` with the file's source reference as evidence.
- **Registry (`agents/registry.py`).** `AgentRegistry` registers, gets, lists specs (sorted) and
  finds agents by capability. `AgentRegistryError` (duplicate or unknown id) is the only exception
  the package raises. `default_registry(model_client=None)` builds both agents; with no client,
  `code_explainer` returns provider_unavailable.
- **CLI:** `cognitive-mesh agents [--json]` lists roles, capabilities and schemas from the registry.
  It needs no configuration and makes no calls.

## Alternatives considered
- A watchdog thread per call: Python cannot kill a thread, so a hung call would keep running.
  Deferred to the orchestrator.
- Agents calling provider SDKs directly: duplicates error handling and ties roles to a vendor.
- Free-form dict results: the orchestrator would need per-agent knowledge. Rejected.
- A stub third role (planner): would look like a capability that is not implemented. Rejected.

## Consequences
- The orchestrator can select by `find(capability)` and treat any non-success result as a failure.
- Nothing is persisted and no real provider exists yet; the provider path is tested with fake
  clients only. Timeouts depend on future clients honoring `timeout_s`.
- `code_explainer` output is unverified model text; callers must not treat it as established fact.
- The failure message rule means some failures give little detail by design.

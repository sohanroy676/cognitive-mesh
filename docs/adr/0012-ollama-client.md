# ADR-0012: Real Ollama client

## Status
Accepted (design agreed in plan mode and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
Since v0.4.0 agents reach a model only through the `providers.ModelClient` protocol, but no real client
exists: the CLI passes none, so `explain_file` always fails `provider_unavailable`. v0.6.1 adds the first
real client, for a local Ollama runtime (model `llama3.2`), and wires it into `run` only when configured.
Constraints: the client must stop at `ModelRequest.timeout_s` even when the server is slow or silent;
every failure must map to an existing `ProviderErrorKind`; prompt text, file content, reply text, response
bodies and exception text must never reach errors, logs, traces or CLI output (ADR-0009); commands that
need no model must keep running with no configuration; tests must not touch a network by default.
v0.6.2 (Groq, ADR-0013) will reuse the same transport, so it must not be Ollama-specific.

User decisions (2026-10-17): standard-library HTTP (no new dependency); `run` keeps today's behavior when
no provider is configured; the model timeout is configurable.

## Decision
- **HTTP: stdlib `http.client`, no new dependency.** A private transport (`providers/http.py`) exposes
  `HttpTransport.post_json(url, body, headers, *, timeout_s, max_body_bytes) -> HttpReply(status, body)`.
  `StdlibTransport` enforces one monotonic deadline for the whole call: connect with the time left, then
  read the body in chunks, setting the socket timeout to the time left before each read, and stop at
  `max_body_bytes + 1`. No redirects, proxies or retries (retries belong to the orchestrator, ADR-0010).
  It raises only `TransportError(kind)` with kind `timeout`, `unavailable` or `too_large`; it keeps no
  exception text. Per-read timeouts alone (urllib, httpx) would let a slow-drip server run past `timeout_s`.
- **Client** `providers/ollama.py::OllamaClient(base_url, model, transport=StdlibTransport())`,
  `provider="ollama"`. It posts to `{base}/api/chat` with `{"model", "messages": [system, user],
  "stream": false}` plus `"format": "json"` when `response_format == "json"`. `ModelResponse.model` is the
  configured model name; the name the server reports is not trusted.
- **Error mapping** (messages are fixed text plus the kind or HTTP status number only):

  | Condition | ProviderErrorKind | Retryable |
  |---|---|---|
  | deadline exceeded (connect or read) | `timeout` | yes |
  | refused, reset, DNS failure, closed early | `unavailable` | yes |
  | 429 | `rate_limited` | yes |
  | 5xx | `unavailable` | yes |
  | 401, 403 | `auth` | no |
  | 404 (model not pulled) and other 4xx | `other` | no |
  | 1xx/3xx, body over the cap, non-JSON, missing or ill-typed `message.content`, `done` not true, `done_reason == "length"` | `bad_response` | no |

  The body cap is `max_output_chars * 6 + 64 KiB` (worst-case JSON escaping). `agents.base.call_model`
  stays the only place provider results are handled and still checks the reply length.
- **Settings** (`config.py`, only through `load_settings`): `COGMESH_OLLAMA_MODEL` (default `llama3.2`,
  non-blank, safe characters, at most 200 chars); `COGMESH_MODEL_TIMEOUT_S` (default 30, greater than 0, at
  most 600); `COGMESH_OLLAMA_BASE_URL` is validated (http or https, host required, no userinfo, query or
  fragment). `load_settings_if_configured()` returns `None` only when the sole problem is a missing
  `COGMESH_PROVIDER`; every other problem still raises `ConfigError`.
- **Factory and CLI.** `providers/factory.py::build_model_client(settings)` builds the Ollama client and
  makes no network call. For `groq` it raises `ConfigError` saying the client is not implemented yet
  (v0.6.2). `default_registry(model_client=None, *, model_timeout_s=30.0)` passes the timeout to
  `CodeExplainer`. `run` reads configuration only when the validated plan contains a capability whose spec
  has `uses_model`: no provider configured means no client (today's behavior: the step fails
  `provider_unavailable`, dependents are blocked, exit 7); a configuration error exits 2; otherwise it
  builds the client. `ingest`, `graph`, `agents`, `traces` and plans without model steps never read config.
- **Logging.** None is added; the package has no logging today, and a client that never logs cannot leak.
- **Tests.** A fake transport covers every row of the table and checks that canary strings in the prompt,
  file content, reply and error body never appear in errors, results or traces. A local
  `http.server` on 127.0.0.1 covers real timeouts (silent and slow-drip servers stopped near `timeout_s`),
  refused connections, oversized and early-closed bodies. A live test against a real Ollama needs the
  `live` marker and `RUN_LIVE_OLLAMA=1`; it is skipped by default and in CI.

## Alternatives considered
- **httpx** (new runtime dependency, BSD-3, several transitive packages): has `MockTransport`, but its
  timeouts are also per phase, so a total deadline still needs our own code.
- **The official `ollama` package** (MIT, built on httpx): Ollama-only, so Groq would need a second SDK, and
  its exception types would have to be mapped anyway.
- **Exit 2 when a plan needs a model and nothing is configured:** stricter, but it changes a verified
  command and its tests.
- **Fixed 30 s timeout:** a local model loading on a CPU can exceed it.

## Consequences
- No new dependency and one transport shared by the Groq client; the deadline is exact and unit-testable.
- We own a small HTTP layer (no redirects, no proxy support, no streaming, no connection reuse). Streaming
  and proxies are out of scope until needed.
- A missing model (404) is not retried and is reported as `other`; the user must run `ollama pull`.
- Model output stays `model_unverified`; this ADR does not make a reply more trustworthy.
- The `groq` provider value still fails with a clear configuration error until v0.6.2.

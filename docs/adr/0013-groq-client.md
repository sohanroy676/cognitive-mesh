# ADR-0013: Real Groq client

## Status
Accepted (drafted in plan mode and confirmed by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.6.1 added a real Ollama client on a private stdlib transport (ADR-0012). `COGMESH_PROVIDER=groq` still
exits 2 because no Groq client exists. v0.6.2 adds one, using a Qwen model as the user decided.

Facts checked against Groq's docs on 2026-10-17 (not verified by a real call; no key was used):
- Endpoint: `POST https://api.groq.com/openai/v1/chat/completions`, header `Authorization: Bearer <key>`.
- The only Qwen model on Groq's model list is `qwen/qwen3.8-27b`. It is a **preview** model (131,072 context,
  16,384 max completion tokens, JSON object mode supported). Preview models can be retired at short notice.
- Qwen on Groq reasons by default. `reasoning_effort` accepts `none|default|low|medium|high`.
  `reasoning_format` is `raw` (reasoning inside `<think>` tags in `content`), `parsed` (separate
  `message.reasoning`) or `hidden`; JSON mode needs `parsed` or `hidden`, not `raw`.
- Error codes: 400, 401, 403, 404, 413, 422, 424, 429, 498 (flex tier at capacity), 499, 500, 502, 503. Error
  bodies look like `{"error": {"message", "type"}}`.

Constraints are the same as ADR-0012: stop at `timeout_s`, map every failure to an existing
`ProviderErrorKind`, never put prompt, file content, reply text, response bodies, exception text or the key
into errors, logs, traces or output, and no network in default tests. Both clients must map the same failure
the same way.

## Decision
- **Transport:** reuse `providers/http.py` unchanged (it already supports https). No new dependency.
- **Endpoint is a constant** in `providers/groq.py`. There is no base-URL setting, so the key can only be sent
  to Groq over HTTPS. Tests inject a fake transport.
- **Settings** (`config.py`): `COGMESH_GROQ_MODEL` (default `qwen/qwen3.8-27b`, same name rule as
  `COGMESH_OLLAMA_MODEL`); `COGMESH_GROQ_REASONING_EFFORT` (default `none`; one of
  `none|default|low|medium|high`, or empty to omit the field for models without reasoning).
  `GROQ_API_KEY` stays a `SecretStr`, is still required and non-blank when the provider is `groq`, and must
  also be printable ASCII without whitespace so it can never inject a header line. The error names the
  variable only, never the value.
- **Request:** `{"model", "messages": [system, user], "stream": false, "max_completion_tokens":
  min(max_output_chars, 16384)}` plus `"response_format": {"type": "json_object"}` for JSON requests and
  `"reasoning_effort"` when set. The `Authorization` header is built inside `complete()` for each call and
  is never stored, logged or put in an error. The client holds the key as `SecretStr`, so `repr` is safe.
- **Reasoning:** turned off (`none`, user decision). The client reads only `choices[0].message.content`;
  `message.reasoning` and `usage` are ignored and never stored. If the content contains `<think>` or
  `</think>`, the reply is rejected as `bad_response`; it is never stripped and trusted. Content stays
  untrusted data and is still parsed as strict JSON by the agent (ADR-0009).
- **Reply checks:** body is a JSON object; `choices` is a non-empty list; `choices[0].message.content` is a
  string; `finish_reason == "stop"` (`length` is "truncated", anything else "not finished").
- **Shared error mapping** (new `providers/errors.py`, used by both clients; messages are fixed text plus the
  provider name and the status number only):

  | Condition | ProviderErrorKind | Retryable |
  |---|---|---|
  | deadline exceeded | `timeout` | yes |
  | refused, reset, DNS failure, closed early | `unavailable` | yes |
  | 429, 498 | `rate_limited` | yes |
  | 5xx | `unavailable` | yes |
  | 401, 403 | `auth` | no |
  | other 4xx (400, 404 unknown model, 413, 422, 499) | `other` | no |
  | 1xx/3xx, body over the cap, bad reply shape, `<think>` in content, truncated | `bad_response` | no |

  The only change for Ollama is that 498 now means `rate_limited`; every other Ollama result is unchanged.
- **Retries:** none in the client. `rate_limited` is retryable, so the orchestrator's bounded retries and run
  budget (ADR-0010) apply. `Retry-After` is not read.
- **Factory/CLI:** `build_model_client` builds `GroqClient` for `groq`. A missing or malformed key stays a
  `ConfigError` (exit 2). `cli._registry_for` is unchanged: models are configured only for plans with a model
  step.
- **Tests:** fake transport for every table row and reply-shape failure; canary strings for prompt, file
  content, reply, error body and key must never appear in errors, results, traces or CLI output; a parity test
  runs the same failures through both clients; CLI tests use a faked transport. The real-API test needs the
  `live` marker, `RUN_LIVE_GROQ=1` and `RUN_LIVE_GROQ_KEY`, and is skipped by default and in CI.

## Alternatives considered
- **The `groq` SDK or httpx:** new dependency and a second error model to map; the stdlib transport already
  gives an exact total deadline.
- **`reasoning_format="hidden"` or `"parsed"`:** possibly better answers, but slower, costlier and the output
  can hit the token cap. Turning reasoning off is the simpler default; the effort setting allows a change.
- **Stripping `<think>` blocks from the content:** that would trust text shaped by the model; rejection is
  safer.
- **A configurable base URL:** would let a misconfiguration send the key elsewhere; not needed.
- **A production model (`llama-3.3-70b-versatile`) as the default:** stable, but the user chose Qwen.

## Consequences
- `qwen/qwen3.8-27b` is a preview model and may change or be retired; the fix is to set `COGMESH_GROQ_MODEL`.
- Explained file contents are sent to Groq, a third party (README limitation).
- No streaming, proxy, redirect or `Retry-After` support. The shape of the request and reply is from Groq's
  docs only until a real key is used; it is not verified against the live API.
- Model output stays `model_unverified`.

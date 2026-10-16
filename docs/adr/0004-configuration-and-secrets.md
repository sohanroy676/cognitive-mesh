# ADR-0004: Configuration via pydantic-settings and ignored .env

## Status
Accepted

## Date
2026-10-16

## Context
Provider credentials (Groq) and endpoints (Ollama) must never be hardcoded or committed, and
missing required configuration must fail clearly.

## Decision
Use `pydantic-settings`. Variables use the `COGMESH_` prefix, except the conventional
`GROQ_API_KEY`. Values come from the environment, then an ignored `.env` in the working
directory. Keys are `SecretStr` so they do not appear in `repr`/`str`. `.env.example` is
tracked and contains placeholders only. `load_settings()` converts validation errors into a
`ConfigError` naming the offending variables without echoing values; the CLI exits with code 2.

## Alternatives considered
- Stdlib dataclass + python-dotenv: fewer dependencies, but hand-written validation and messages.

## Consequences
- Adds pydantic as a dependency (likely needed later anyway).
- `.env` is resolved relative to the current working directory, so tests isolate the cwd.
- Required variables will grow as providers are implemented; update `.env.example` with them.

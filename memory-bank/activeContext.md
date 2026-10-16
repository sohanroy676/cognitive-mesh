# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.1.0 Project Foundation is implemented on `feature/v0.1.0-project-foundation` (local only), awaiting review.

## Recent decisions
- uv + pyproject, hatchling, src layout `cognitive_mesh` (ADR-0002).
- ruff (lint+format, no black) + mypy strict + pytest (ADR-0003).
- pydantic-settings, `COGMESH_` prefix, `SecretStr`, ignored `.env`, exit code 2 on config error (ADR-0004).
- CI secret-scan toggle moved from a workflow `env` var to the `RUN_SECRET_SCAN` repository variable. Dependabot switched to the `uv` ecosystem.
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Push/merge needs user approval. CI has never run on GitHub; confirm it is green after the first push.
- Build/packaging command is untested (TBD).
- Whether to configure CHANGELOG generation (only if the user asks).

## Next step
Get approval to push the branch and open a PR; then start v0.2.0 Application Ingestion via `/new-feature`.

# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.2.0 Application Ingestion is implemented, verified locally, merged to main and pushed (2026-10-16); CI on main passed (run 38947231207). v0.3.0 not started.

## Recent decisions
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- No new dependencies (YAML is `unvalidated`, not parsed).
- CHANGELOG generation deliberately not set up (not requested).

## Blockers / open questions
- Tagging `v0.2.0` needs user approval (not tagged).
- Old branch `feature/v0.2.0-application-ingestion` (commit `ed71a4f`) and `git stash@{0}` (its uncommitted deletions of the ingestion code, origin unknown) are kept for the user to decide on.
- Local branch `feature/v0.1.0-project-foundation` is merged; user chose to keep it.
- Build/packaging command is untested (TBD).

## Next step
Start v0.3.0 Dependency Graph via `/new-feature` (ingestion output in `cognitive_mesh.ingestion` is the input; lockfiles are currently excluded and may need revisiting).

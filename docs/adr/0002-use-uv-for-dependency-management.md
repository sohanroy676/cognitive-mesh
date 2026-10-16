# ADR-0002: Use uv with pyproject.toml for dependency management

## Status
Accepted

## Date
2026-10-16

## Context
v0.1.0 needs reproducible setup on Python 3.12 across a Windows dev machine and Linux CI.
`pip` + `requirements.txt` was the placeholder assumption in CI and dependabot. uv was not
installed locally at decision time (installed with `winget install --id=astral-sh.uv -e`).

## Decision
Use uv with `pyproject.toml` (PEP 621 + dependency groups), a committed `uv.lock`, and
`.python-version` pinned to 3.12. Build backend is hatchling. Code lives in a `src/` layout
as package `cognitive_mesh`; tests live in `tests/`.

## Alternatives considered
- pip + venv + pyproject: no extra tooling, but no lockfile, so installs are less reproducible.
- Poetry / PDM: heavier, no advantage for this project's needs.

## Consequences
- Contributors must install uv. CI uses `astral-sh/setup-uv` and `uv sync --locked`.
- Lockfile changes must be committed with dependency changes.
- Dependabot uses the `uv` ecosystem.

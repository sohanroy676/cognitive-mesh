# ADR-0003: Use ruff, mypy, and pytest

## Status
Accepted

## Date
2026-10-16

## Context
The project needs a linter, formatter, static type checker, and test framework (all TBD before
v0.1.0). The prior CI placeholder assumed ruff + black + pytest.

## Decision
- ruff for linting (rules E, F, W, I, B, UP, SIM, S) and formatting; black is dropped.
- mypy in `strict` mode over `src` and `tests`, with the pydantic plugin.
- pytest, tests in `tests/`, files named `test_*.py`.

## Alternatives considered
- ruff + black: one more tool with overlapping configuration.
- pyright instead of mypy: faster, but a Node-based runtime for little gain here.

## Consequences
- Fewest tools: one config block each in `pyproject.toml`.
- Strict typing from the start is cheaper than retrofitting it later.
- Bandit-style `S` rules are on; tests ignore assert/subprocess warnings.

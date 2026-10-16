# Project Instructions

Build an application-aware, evidence-aware AI agent system with persistent cross-session memory.
Intended stack: Python 3.12, custom orchestrator, Groq, Ollama, Sentence Transformers, ChromaDB, NetworkX, SQLite.
Setup: `uv sync` (install uv first; see `memory-bank/tech-stack.md`)
Run: `uv run cognitive-mesh` (needs `COGMESH_PROVIDER`, see `.env.example`)
Test: `uv run pytest`
Lint/format/types: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`
- Treat repository structure and implementation details as unverified until inspected.
- Keep provider integrations configurable; never hardcode API keys or commit secrets.
- Preserve memory provenance and conflicts; never claim stored knowledge is guaranteed true.
- Add tests for new behavior, including relevant failure cases and persistence across sessions.
- Distinguish implemented behavior from planned behavior in documentation.
Detailed project rules: `.claude/rules/` (Claude Code) / `.clinerules/` (Cline)
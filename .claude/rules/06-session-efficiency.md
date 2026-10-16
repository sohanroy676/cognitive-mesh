# Session & Token Efficiency

- Do not re-read entire files that are already visible in context unless they've changed.
- When exploring a codebase, read only the files relevant to the current task, not the whole directory tree.
- Prefer targeted Grep/Glob over opening large files fully when just locating something.
- The memory bank (`memory-bank/`) is imported by `CLAUDE.md`, so its core files are already in context at session start; read `memory-bank/progress.md` and `systemPatterns.md` on demand rather than asking the user to re-explain.
- At the end of a work session (or after completing 1-2 features), update `memory-bank/progress.md` and `memory-bank/activeContext.md` with current state, then recommend starting a fresh session (`/clear`) for the next feature rather than continuing indefinitely in one thread.
- `memory-bank/` is the project's committed, agent-facing record. Claude Code's own auto-memory is separate; do not use it to duplicate what the memory bank holds.

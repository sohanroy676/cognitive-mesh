# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.1.0 to v0.3.2 are done and tagged (`v0.3.2` on `e19d5e1`, pushed). Tooling work after v0.3.2 (git-cliff changelog with a CI freshness check, `uv build` verified, git-tag versioning via hatch-vcs, one-off Mermaid renderer check) was merged to main from `chore/changelog-git-cliff` and pushed with user approval; check its CI run (see progress.md). No PRs open. v0.4.0 Agent Architecture is implemented on `feature/v0.4.0-agent-architecture` (criteria pass locally; not merged, CI not run). v0.5.0 Orchestrator is next, planned only.

## Recent decisions
- ADR-0009 (accepted by the user): agents depend only on the `providers.ModelClient` protocol; `Agent.run` never raises; results are `AgentSuccess | AgentFailure` with explicit failure kinds; capability specs are derived from the handler table; messages never contain input values, file content, model text or exception text; model-written output is `model_unverified`; timeouts are provider-owned (no watchdog).
- ADR-0008 (accepted by the user): Mermaid and self-contained HTML/SVG from one `GraphView`; no new dependency, no script or network loads; escaped, ASCII, deterministic output; visible truncation (HTML 300/1000, Mermaid 100/200); `--format` is exclusive with `--json`/`--deps-of`; exit 5 for write errors.
- ADR-0007 (accepted by the user): tree-sitter for JS/TS, private to `graph/js_parse.py`; syntax-node matching only; computed dynamic imports are `unresolved:dynamic:*` + `computed_import`; bare/aliased JS specifiers stay unresolved; no `.js`->`.ts` remap.
- ADR-0006: edge `A -> B` = A depends on B; every edge has evidence; unresolved/ambiguous things are `unresolved` nodes + diagnostics, imports never linked to packages by name; lockfiles stay excluded, so transitive external deps are reported unknown; NetworkX private behind pydantic models.
- ADR-0005: local directories only; classification by name/extension; never executed (parse-only stdlib checks); sensitive names never opened; content secret scan with redaction; symlinks not followed.
- Redo chosen over restoring `ed71a4f`: adds content-based redaction and common-language source files.
- v0.2.0 added no dependencies (YAML is `unvalidated`, not parsed); v0.3.0 added NetworkX.
- CHANGELOG generation is git-cliff (`cliff.toml`) with a CI freshness check; version comes from git tags (hatch-vcs). Rule: regenerate `CHANGELOG.md` as the LAST commit of every branch (`chore(changelog): regenerate`), otherwise CI fails. A docs-only closeout commit made after CI therefore also needs a regenerate commit after it.

## Blockers / open questions
- Pushes and merges always need explicit user approval (v0.3.2 was merged and pushed on explicit approval).
- `RUN_SECRET_SCAN=true`; gitleaks runs in CI with `fetch-depth: 0` and passes (full history, no leaks). Detection is still pattern-based.
- Writing an ~880 KB file and opening it took ~23 s on this Windows machine (inside `open()`, not our code; cause unverified, possibly on-access scanning). Tests use smaller files.
- Local branches are never deleted by the agent (rule in CLAUDE.md); old branches are kept.
- `uv build` verified locally. Version now derives from git tags (hatch-vcs); the branch awaits approval and a CI run. Mermaid output was checked once with mermaid-cli 12.0.0 (manual).

## Next step
1. Done: CI run 39022644447 on main passed (changelog check + gitleaks); recorded on branch `docs/record-ci-39022644447`, awaiting the user's approval to merge/push.
2. Optionally tag nothing: no version bump is pending (v0.3.2 is the latest tag; main is ahead by tooling commits only).
3. Review v0.4.0 on its branch, then merge and CI on approval; after that plan v0.5.0 (see docs/DETAILED_ROADMAP.md). A real provider client (Groq/Ollama) is not scheduled in v0.4.0; timeouts rely on clients honoring `timeout_s`. Lockfile ingestion (for transitive dependencies) was deliberately deferred in ADR-0006.

# Active Context

<!-- Update this at the end of every session. This is the first thing to read when resuming. -->

## Current focus
v0.1.0 to v0.5.0 are done and tagged (`v0.5.0` pushed; CI runs 39024871768 and 39025436259 passed). v0.6.0 Episodic Trace Store is implemented on `feature/v0.6.0-trace-store` (ADR-0011, criteria pass locally, 486 tests); not merged, not pushed, CI not run, not tagged. Tooling work after v0.3.2 (git-cliff changelog with a CI freshness check, `uv build` verified, git-tag versioning via hatch-vcs, one-off Mermaid renderer check) was merged to main from `chore/changelog-git-cliff` and pushed with user approval; its CI run 39022644447 passed. No PRs open. v0.5.0 Basic Orchestrator is implemented and merged to main (branch `feature/v0.5.0-orchestrator`, ADR-0010; criteria pass locally; pushed with user approval; CI run 39024871768 on the merge passed; tagging `v0.5.0` approved in principle, done on a `chore/release-v0.5.0` branch). v0.6.0 Episodic Trace Store is next; plan agreed 2026-10-17 (ADR-0011 not yet written).

## Recent decisions
- ADR-0011 (accepted by the user): SQLite trace store, stdlib `sqlite3` only; opt-in `run --trace-db`; run committed as `running` first and finished in one transaction (a crash reads back `interrupted`); status recomputed and cross-checked on read, malformed rows raise or are reported by `list`; secrets redacted before storing; per-step timestamps only when the orchestrator has a `now` clock; exit 8/9.
- ADR-0010 (accepted by the user): sequential orchestrator; plans validated before running; assignment by `registry.find(cap)[0]`; steps `succeeded|failed|blocked|not_run`; task status derived from steps; retry only `retryable` agent failures (default 2, cap 5); invalid results and raised exceptions fail the step and are not retried; run budget checked before each attempt (no watchdog); `depends_on` is ordering only; `run` CLI exits 6/7.
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
1. Ask the user to review and approve merging `feature/v0.6.0-trace-store` (plain-English summary first), then push, check `gh run list`, record the CI result on a docs/ branch, and ask before tagging `v0.6.0` (`git-cliff --tag v0.6.0` on a `chore/release-v0.6.0` branch; `uv sync --reinstall-package cognitive-mesh` after tagging).
2. Then plan v0.7.0 Semantic Memory Retrieval (docs/DETAILED_ROADMAP.md; next ADR is 0012). Traces are raw; decide which are eligible for embedding and how failed/interrupted ones are treated.
3. Known items: no real provider client (Groq/Ollama); clients must honor `timeout_s`; lockfile ingestion deferred (ADR-0006); secret detection is pattern-based; orchestrator has no data passing between steps or fallback agent (ADR-0010).

# Changelog

All notable changes to this project are documented here.
Format follows Keep a Changelog. Generated from Conventional Commits by git-cliff; do not hand-edit. Regenerate with `uv run git-cliff -o CHANGELOG.md`.

## [Unreleased]

### Added
- **experience:** Build the experience graph from traces
- **experience:** Persist the graph in a checked SQLite file
- **experience:** Query the graph and re-check its sources
- **experience:** Add experience build, show, claims and check commands

### Changed
- **memory:** Share verify_source and load MemoryIndex lazily

### Documentation
- **adr:** Propose ADR-0015 experience graph
- **adr:** Accept ADR-0015
- Record v0.8.0 experience graph

### Maintenance
- Regenerate changelog as the release for untagged versions

## [0.7.0] - 2026-10-17

### Added
- **traces:** Add versioned schema migrations to the trace store
- **providers:** Require opt-in before sending content to Groq
- **ingestion:** Add secret formats and an entropy check
- **graph:** Read uv.lock and package-lock.json for transitive dependencies
- **orchestrator:** Pass step outputs to later steps and add fallback agents
- **orchestrator:** Draft a plan from a request with a model
- **graph:** Reduce edge crossings in the HTML layout
- **memory:** Add semantic retrieval over stored traces

### Documentation
- Record CI-on-tags and trace-store migrations
- Record the pre-v0.7.0 review pass
- Record lockfiles, data passing, plan drafting and layout
- Hand off after the pre-v0.7.0 review pass
- Record CI run 39033918355 and the v0.7.0 design
- **adr:** Accept ADR-0014 semantic memory retrieval
- Document v0.7.0 semantic memory retrieval
- Hand off after v0.7.0 semantic memory retrieval
- Record CI run 39043661141 for v0.7.0
- Record CI run 39043771776 and prepare v0.7.0 release

### Maintenance
- Run on release tags and check the changelog section
- **orchestrator:** Cover input references in drafted plans

## [0.6.2] - 2026-10-17

### Added
- **providers:** Add Groq client with shared error mapping

### Documentation
- Record v0.6.2 Groq client
- Record real-API Groq verification
- Hand off after v0.6.2
- Fix a mis-encoded dash in progress.md
- Record CI for v0.6.2 and fix stale status

## [0.6.1] - 2026-10-17

### Added
- **providers:** Add Ollama model client

### Documentation
- **adr:** Add ADR-0012 Ollama client
- Record v0.6.1 Ollama client
- Hand off after v0.6.1
- Record v0.6.1 CI and v0.6.2 plan
- **adr:** Propose ADR-0013 Groq client

## [0.6.0] - 2026-10-17

### Added
- **traces:** Add SQLite episodic trace store

### Documentation
- **adr:** Propose ADR-0011 episodic trace store
- **adr:** Accept ADR-0011 episodic trace store
- Record v0.6.0 episodic trace store
- Hand off after v0.6.0 trace store
- Plan v0.6.1 ollama and v0.6.2 groq clients
- Hand off after planning v0.6.1 and v0.6.2

## [0.5.0] - 2026-10-17

### Added
- **orchestrator:** Add task planning and bounded execution

### Documentation
- Record v0.5.0 orchestrator and ADR-0010
- Hand off after v0.5.0 orchestrator
- Record v0.5.0 CI result

## [0.4.0] - 2026-10-17

### Added
- **agents:** Add agent interface, roles and registry

### Documentation
- Mark v0.3.2 done after green CI
- Document changelog workflow
- Record verified build and version gap
- Record git versioning and Mermaid renderer check
- Hand off after changelog and versioning work
- Record CI run 39022644447 for changelog merge
- **adr:** Add ADR-0009 agent architecture
- Record v0.4.0 agent architecture
- Hand off after v0.4.0 agent architecture
- Record v0.4.0 CI result

### Maintenance
- Fetch full history for gitleaks
- Add git-cliff changelog generator
- Check CHANGELOG.md is up to date
- Derive package version from git tags

## [0.3.2] - 2026-10-17

### Added
- **graph:** Render dependency graph as Mermaid or HTML

### Documentation
- Mark v0.3.1 done after green CI
- Plan v0.3.2 graph visualization before v0.4.0
- Record v0.3.2 visualization status and commands
- Handoff after v0.3.2 merge

## [0.3.1] - 2026-10-17

### Added
- **graph:** Analyse JS/TS and dynamic imports

### Documentation
- Record v0.3.1 status and verified commands

## [0.3.0] - 2026-10-17

### Added
- **graph:** Add evidence-backed dependency graph

### Documentation
- **rules:** Never delete local branches
- Record v0.3.0 graph design and status
- Add README and plan v0.3.1
- Mark v0.3.0 done after green CI

## [0.2.0] - 2026-10-16

### Added
- **ingestion:** Add read-only application ingestion

### Documentation
- Mark v0.1.0 complete after green CI
- Record v0.1.0 closeout in progress and CLAUDE.md
- Record v0.2.0 merge state and handoff
- Mark v0.2.0 done after green CI

## [0.1.0] - 2026-10-16

### Added
- **foundation:** Add uv project, config loading and CLI entry point

### Documentation
- Record verified v0.1.0 commands and update memory bank

### Maintenance
- Add project scaffolding, roadmap and memory bank
- Add Claude Code config and fix CI for Python 3.12



# Changelog

All notable changes to this project are documented here.
Format follows Keep a Changelog. Generated from Conventional Commits by git-cliff; do not hand-edit. Regenerate with `uv run git-cliff -o CHANGELOG.md`.

## [Unreleased]

### Documentation
- **progress:** Record CI for v1.4.3
- **progress:** Record stdlib claims on two real Python minors
- **adr:** Measure profile cut-offs on a real corpus
- **progress:** Record real-model recall timing at 5,000 and 20,000 runs

## [1.4.3] - 2026-10-19

### Added
- **context:** Show recorded outcomes in the task pack

### Documentation
- Record CI runs for v1.4.2
- **adr:** Propose ADR-0029 outcomes in the context pack
- **adr:** Accept ADR-0029
- Record ADR-0029 implementation and ADR-0030 profile
- **adr:** Propose ADR-0030 source-check cost
- **adr:** Accept ADR-0030
- Record ADR-0030 outcome, stop rule applied
- Handoff and release notes for v1.4.3

## [1.4.2] - 2026-10-19

### Changed
- **experience:** Share contradiction listings, scope show and promote

### Documentation
- Record CI runs for v1.4.1
- **adr:** Propose ADR-0028 whole-graph assessment cost
- Record ADR-0028 proposal in memory bank
- **adr:** Accept ADR-0028
- Record ADR-0028 outcome and measurements
- Record ADR-0028 follow-up checks at scale and with real MiniLM
- Prepare release v1.4.2 and handoff

## [1.4.1] - 2026-10-19

### Added
- **experience:** Stdlib edges are environment-scoped claims
- **experience:** Hash stored record texts, graph format 6

### Changed
- **experience:** Parse and dump each distinct ref once

### Documentation
- **adr:** Propose ADR-0025 and ADR-0026
- Record stdlib environment claims
- **adr:** Record ADR-0026 profiling outcome
- **progress:** Record CI for e19c0c6
- **adr:** Propose ADR-0027 graph load verification cost
- **adr:** Accept ADR-0027
- Record ADR-0027 outcome and format 6
- Record measured whole-graph stages and peak memory
- Prepare release v1.4.1 and handoff

### Maintenance
- **experience:** Pin graph hash and ref order before ADR-0027

## [1.4.0] - 2026-10-18

### Added
- **retrieval:** Ordered selection and task profiles
- **orchestrator:** Declared task state between steps
- **agents:** Add compare_dependencies

### Documentation
- **progress:** Record CI runs for v1.3.0
- **adr:** Propose ADR-0023 context assembly and task state
- **adr:** Accept ADR-0023
- Record v1.4.0 implementation and amend ADR-0023
- Record v1.4.0 merge and CI run
- **adr:** Propose ADR-0024 compare_dependencies
- Record compare_dependencies
- **progress:** Record compare_dependencies run
- **progress:** Record post-v1.4.0 measurements
- Mark v1.4.0 done

### Maintenance
- **agents:** Cover compare_dependencies and accept ADR-0024

## [1.3.0] - 2026-10-18

### Added
- **identity:** Record an application id per run
- **validity:** Add scope, environment comparison and expiry rules
- **validity:** Add valid_until to reviews and outcomes
- **validity:** Scope, environment and expiry in claim assessment

### Documentation
- Record application identity work (v1.3.0 part 1)
- **adr:** Propose ADR-0022 environment and validity
- **adr:** Accept ADR-0022
- Record v1.3.0 environment and validity work
- Mark v1.3.0 done and write the handoff

### Fixed
- **recall:** Pass outcomes to the claim assessment

## [1.2.1] - 2026-10-18

### Added
- **recall:** Follow claim subjects instead of the whole graph

### Documentation
- Record CI for v1.2.0
- **adr:** Propose application identity (ADR-0021)
- **adr:** Accept ADR-0021 application identity
- Record real-CLI corroborated check
- Handoff after v1.2.1 and ADR-0021
- Note the removed git push prompt
- Mark v1.2.1 merged

### Maintenance
- **settings:** Drop the git push permission prompt

## [1.2.0] - 2026-10-18

### Added
- **plan:** Show a valid example plan in the draft prompt
- **experience:** Corroborate claims by independent source class

### Documentation
- Record the v1.1.0 release and its CI
- **adr:** Propose bounded recall cost (ADR-0019)
- **adr:** Accept ADR-0019
- Record CI runs for the v1.0.1 release
- **progress:** Record plan drafting step A
- **adr:** Propose ADR-0020 corroboration and lineage
- **adr:** Accept ADR-0020
- **progress:** Record v1.2.0 corroboration
- Mark v1.2.0 merged

### Fixed
- **plan:** Name the refusal and complete depends_on in drafts

## [1.1.0] - 2026-10-18

### Added
- **experience:** Record outcomes and promote only validated claims
- **plan:** Refuse repeated steps and unknown files in drafts

### Documentation
- **adr:** Propose outcome validation (ADR-0018)
- Record the session state for v1.1.0 planning
- **adr:** Accept ADR-0018
- Record the v1.1.0 implementation state
- Record the verification items for v1.1.0
- Record the small-model plan experiment
- Record the plan node-id fix and the re-run
- Record the plan repair and the re-run
- Record the round-two plan results
- **readme:** Release notes for v1.1.0

### Fixed
- **plan:** Accept a file path as a module node id
- **plan:** Let explain_file take a module node id
- **plan:** Refuse unusable inputs and repair a refused draft once

### Maintenance
- **cli:** Cover outcome on a node that is not a claim
- **traces:** Declare the note input on the fake capability

## [1.0.1] - 2026-10-18

### Documentation
- Record CI runs and handoff for v1.0.0
- Record the v1.0.0 verification pass
- Record the remaining v1.0.0 verification items
- Hand off after the v1.0.0 verification pass
- Note the release-section date pitfall

### Fixed
- **traces:** Tolerate parallel creation of a new trace database
- **memory:** Retry opening the index while another process creates it
- **experience:** Create graph and reviews files atomically

### Maintenance
- **e2e:** Pin that same-named applications share context

## [1.0.0] - 2026-10-17

### Added
- **agents:** Add recalled-context pack model
- **retrieval:** Build the gated context pack from recall
- **agents:** Let agents read the recalled-context pack
- **traces:** Record the context a run was offered (schema 2)
- **cli:** Add task command that supplies recalled context

### Changed
- **graph:** Share claim re-derivation as rederive

### Documentation
- Record CI runs and handoff for v0.9.0
- **adr:** Accept ADR-0017 context supply
- Record v1.0.0 end-to-end prototype
- Record v1.0.0 merge and hand off to the next session

### Maintenance
- **e2e:** Run the context workflow across separate processes
- **agents:** Require no diagnostics for compare_with_recalled
- **live:** Check the default cut-off end to end with the real model
- **fixtures:** Add the e2e_app demo application and plans

## [0.9.0] - 2026-10-17

### Added
- **experience:** Record listing coverage in graph format 2
- **experience:** Add claim status, reviews and re-derivation
- **retrieval:** Add recall command and experience review

### Documentation
- Record CI runs for v0.8.0
- **adr:** Add ADR-0016 evidence-aware retrieval
- Record v0.9.0 evidence-aware retrieval
- Record v0.9.0 merge and CI

## [0.8.0] - 2026-10-17

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
- Mark v0.8.0 released and hand off

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



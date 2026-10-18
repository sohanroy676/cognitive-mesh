# ADR-0021: Application identity

## Status
Accepted (drafted and accepted 2026-10-18 under the user's delegation to choose the best option). Implemented on branch `feature/v1.3.0-application-identity` (not merged). Implementation detail: only run nodes store `how`; claim and attempt nodes store the id alone, so a claim merged from several runs does not depend on run order. The three open questions are answered below.

## Date
2026-10-18

## Context
The context gate of ADR-0017 offers a claim when its subject exists in the current dependency graph, and a raw trace
when the run's root directory has the same name. Neither says which application a run analysed, so two different
applications with the same directory name and module paths share context. `tests/test_e2e_identity_limit.py` pins
this: the foreign claim is never `validated` for code that does not contain it, but it is offered, labelled with
the runs that back it. The graph file does not record the application either, so equal node ids from different
applications merge into one claim (ADR-0016 limit).

What exists today: each run stores `source_root` (a path string) and `source_fingerprint` (a hash of the analysed
files, which changes with every code change, so it identifies a code version, not an application).

`DETAILED_ROADMAP.md` puts "environment identifiers and validity information" in v1.3.0. Identity is the part of
that item the prototype already needs; time-based expiry and environment-dependent knowledge are a separate
question (ADR-0022, not drafted here).

## Decision (proposed)
1. **An application id is recorded per run**, with how it was obtained: `declared` (the person passed `--app-id NAME`),
   `manifest` (the name in `pyproject.toml` `[project]` or `package.json`, plus the directory name), or
   `directory` (directory name only). The `how` is stored and shown; a `directory` id is labelled weak.
2. **The gate compares ids.** A claim is offered only if its evidencing runs share the current run's application id.
   Runs recorded before the change have no id (`unrecorded`); they keep today's gate (subject exists, same directory
   name), are labelled `unrecorded`, and are never promoted to a match.
3. **Claims are per application.** The claim id includes the application id, so equal node ids from different
   applications no longer merge. Claims from runs without an id keep their current ids and stay separate from
   id-bearing claims.
4. **Nothing proves identity.** The id is a label chosen or derived locally, like `--by` labels. Two applications
   can still share a declared name; the code version (fingerprint) rules keep a foreign claim from being `validated`.
5. Trace schema 2 -> 3 (one nullable column pair, migration additive), graph format 3 -> 4 (older files refused,
   as before), and the claim-id change means graphs must be rebuilt from the trace database, which stays the
   source of truth.

## Alternatives considered
- **Absolute root path as identity.** Breaks when the application is cloned or moved, which is the normal case
  across sessions and machines.
- **Git root-commit hash.** Stable across clones, but needs git, fails for non-git directories, and forks share it.
- **Content fingerprint of the manifest only.** Changes whenever dependencies change, so it would split one
  application into many.
- **Keep the directory-name gate and document it.** No migration, but the known cross-application contamination
  stays.

## Consequences
- Easier: the gate and the graph can tell applications apart; `task` reports which id it matched.
- Harder: a claim-id change invalidates stored graph files, review/outcome files that name old claim ids
  (reviews and outcomes are keyed by claim id and fingerprint) need a stated rule: they stay attached to the
  old ids and are shown as orphaned, or a one-time re-keying is added. This ADR proposes the first (no silent
  rewrite of append-only files).
- Accepted limitation: identity is declared or derived, not authenticated; renaming a project splits its history.

## Answers to the open questions
1. **Declared plus derived.** Explicit-only would leave every existing workflow with no id; the derived id (with its
   stored `how`) gives most applications a usable id with no extra flag, and the weak `directory` label keeps its
   limit visible.
2. **Orphan, do not re-key.** Reviews and outcomes are append-only and tied to a claim id; rewriting them would
   break the rule that history is never silently changed. Orphans are listed (the existing orphan listing), never dropped.
3. **Separate ADR-0022** for environment identifiers, validity periods and expiry. Identity can ship and be tested
   alone; the staleness rules need their own documented rule (the roadmap forbids inferring staleness without one).

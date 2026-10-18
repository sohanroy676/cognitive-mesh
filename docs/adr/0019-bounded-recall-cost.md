# ADR-0019: Bounding the cost of whole-graph recall

## Status
Accepted (drafted 2026-10-18; accepted 2026-10-18 under the user's delegation to choose the best option. The subject index is a v1.x implementation item; no code change is made by this ADR.)

## Date
2026-10-18

## Context
`recall` and `task` read the whole experience graph on every call. `retrieval/recall.py` follows vector hits into
the graph, and `experience/assess.py` assesses every claim, so the work grows with the whole graph, not with the
query. The v1.0.0 verification pass measured this on synthetic runs (5 code versions of a 40-module application,
fake embedder for the 5,000 and 20,000 rows; `progress.md`, verification item (b)):

| runs | graph nodes / edges | build | recall | task | peak memory (recall / task) |
|---|---|---|---|---|---|
| 1,000 | not recorded | 3.5 s | 17 s (about 10 s model load) | 18 s | 701 MB / not recorded |
| 5,000 | not recorded | 19 s | 14 s | 15 s | 819 MB / not recorded |
| 20,000 | 144,563 / 409,036 | 116 s | 66 s | 69 s | 2.4 GB / 2.9 GB |

Time and memory are roughly linear in runs up to a few thousand, then slow and memory-hungry. The v1.0.0 notes
deferred this; this ADR records the options so a later decision is made on the numbers. Two limits matter: the
trace database is the source of truth, and the graph file is rebuilt from it (ADR-0015), so any change must keep
that rule.

The cost matters only when a user runs thousands of tasks against one application. Nothing in the current
prototype needs more than a few thousand runs.

## Decision
Proposed: do not change the algorithm yet. Record the measured limit in the user-facing docs (the README limits
section, once v1.1.0 is merged) and open a v1.x implementation item with this design:

1. **Subject index.** At build time, store for each claim its subject node id (already derivable from the claim's
   edges). A recall whose vector hits name a subject node follows only the claims with that subject, then their
   supporting and contradicting attempts. Claims on other subjects are never assessed for that call.
2. **Same results.** The subject index is a lookup, not a new rule: it must return exactly the claims a whole-graph
   recall returns for the same query. A test compares the two on the fixture graphs and on a generated 5,000-run
   graph, and must match on every claim.
3. **Fallback.** A recall with no subject hit keeps the current behaviour (whole-graph), reported as such, so
   nothing relevant is silently dropped.

## Amendment (v1.2.1, implemented 2026-10-18)
- The claim node stores `subject` (the dependency-graph node id); graph format 3, older files refused. A load
  refuses a claim whose `subject` differs from the name of its `subject` edge target.
- `recall` follows the subjects of the claims its hits are evidence for. It checks sources and assesses only
  those claims, the attempts that state them and the attempts `assess_claim` reads as contradiction candidates
  (for `is_unresolved` claims, every `unresolved_report` attempt). `RecallResult` reports
  `subjects_followed` and `claims_assessed`.
- **Fallback changed:** when no hit reaches a claim, nothing can be reported either way, so recall assesses
  no claim instead of scanning the whole graph. The result is identical to a whole-graph recall (that is what
  the tests compare), so the reported whole-graph fallback in point 3 would only have added cost.
- Not reduced: loading the graph file and building `ExperienceGraph` still grow with the whole graph, and
  `experience claims`/`outcome`/`check` still use whole-graph checks.

## Alternatives considered
- **Keep whole-graph recall and document the limit.** No risk to correctness, but a `task` over tens of thousands of
  runs stays at about 70 s and 2.9 GB. Acceptable for a prototype, not for the stated use.
- **Incremental graph maintained on every trace write.** Removes the rebuild, but couples the trace store to the
  graph and makes the "graph is derived from traces" rule harder to keep. Rejected for now.
- **Cap the number of runs considered (most recent N).** Simple and fast, but it silently hides older evidence, which
  the brief forbids: conflicts and old runs must stay visible. Rejected.
- **Move the graph to a graph database.** A new dependency and a new persistence model for a problem that a
  subject index solves. Rejected.

## Consequences
- Easier: recall and task cost follow the query's subjects, not the size of the whole history.
- Harder: the graph file gains an index that must stay consistent with the claims, and the format version changes
  (older files refused, as in ADR-0016).
- Accepted limitation: a query whose vector hits name no subject falls back to the full scan, so the worst case
  does not improve.
- Accepted limitation: the 20,000-run figures are synthetic and used a fake embedder. Real-model indexing costs about
  76 ms per document (extrapolated, not run), so indexing, not recall, dominates with a real model.

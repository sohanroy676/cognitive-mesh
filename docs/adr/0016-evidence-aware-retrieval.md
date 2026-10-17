# ADR-0016: Evidence-aware retrieval, validation and conflicts

## Status
Accepted (design choices confirmed by the user in plan mode 2026-10-17; text reviewed and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.7.0 finds past experiences by meaning (ADR-0014) and v0.8.0 records their claims in a graph, all
`unvalidated` (ADR-0015). ADR-0015 left four things to v0.9.0: how claims are corroborated or validated, how
conflicts are found and shown, what confidence means, and how observations of different code versions are
treated (the claim id ignores the version; the source fingerprint lives on the evidence edge).

Constraints from the project brief and earlier ADRs:
- Similarity, repetition across runs and model confidence are never proof. Raw traces stay distinguishable
  from validated knowledge. Conflicts are shown, never resolved by picking a winner or hidden.
- The trace database stays authoritative and untouched (`MIGRATIONS` unchanged). The vector index and the
  graph file are derived copies and may be rebuilt at any time.
- Retrieval must work without a model provider, without config and without network access.
- Confidence rules must be defined and tested before they are relied on.

## Decision
- **Status, not score.** Each claim gets one categorical status with a fixed meaning plus raw counts. There
  is no number, so nothing implies a calibrated probability. Statuses are computed on every call from the
  trace database, the graph, the reviews and (optionally) the current source; they are never stored.
  Only attempts whose source check is `ok` (the shared `verify_source`) count as support or contradiction.
- **Statuses**, evaluated in this order:
  1. `conflicting`: for some fingerprint F there is ok support on F and either a complete contradicting
     observation on F or a `rejected` review on F; or, with `--root`, support/confirmation on the current F
     while re-derivation refutes the claim; or `confirmed` and `rejected` reviews on the same F.
  2. `validated`: `--root` is given and the claim is re-derived from the current source, or a `confirmed`
     review exists for the current fingerprint. The output names the method and its evidence.
  3. `unsupported`: no attempt with an ok source (provenance missing, changed or unreadable).
  4. `stale`: `--root` is given and the claim is supported only by observations of other fingerprints and is
     not re-derivable now (including a subject that is absent from the current graph).
  5. `supported`: no `--root`; ok support, no conflict; currency is reported as `unknown`.
  `validated` never means true. It means the claim holds for one named code version by a stated method.
- **Validation, two kinds, both tied to one fingerprint.** `rederived`: `--root APP` is ingested, its
  fingerprint computed and the deterministic dependency graph rebuilt with the existing analyzer; the claim is
  looked up in it (`holds`, `refuted` or `subject_absent`). It is not independent of the analyzer that
  produced the claim, and the output says so. `reviewed`: a person records `confirmed` or `rejected` with a
  required note and a free-text `--by`; this is unauthenticated. Neither similarity, repetition nor model
  output can validate a claim.
- **Conflicts, closed-world over complete outputs.** A `dependencies_of A` output with `truncated = false`
  that lacks `(B, edge_kind)` at depth 1 contradicts `A depends_on B`; a complete `unresolved_report` that
  lacks X contradicts `X is_unresolved`. A truncated output never contradicts. Same fingerprint:
  `contradicted` (the deterministic tool disagreed with itself). Different fingerprints:
  `differs_across_versions`. Both sides are always listed with their attempts. No winner is chosen.
- **Validity is the fingerprint, nothing else.** With `--root` the current fingerprint is computed; without
  it currency is `unknown`. There is no time-based expiry. Old evidence is shown, never deleted.
- **Graph format 2.** Attempt nodes of `dependencies_of` and `unresolved_report` gain
  `coverage: complete|truncated` taken from the output. `FORMAT_VERSION` becomes 2; a format-1 file is
  refused with a message to rebuild (the graph is derived). Contradictions then need no trace re-read.
- **Reviews in their own file.** `--reviews FILE`, stdlib `sqlite3` in `experience/reviews.py` only, own
  `application_id` and schema version, append-only (no update or delete API). Each row holds claim id,
  fingerprint, verdict, note, reviewer and time. A newer review never erases an older one. A review whose
  claim is no longer in the graph is shown as orphaned. Note and reviewer are length-limited, control
  characters are refused, secrets are redacted before storing, and output is printed escaped.
- **Connecting retrieval to the graph.** `recall` runs `MemoryIndex.search` (which already re-checks each hit
  against SQLite), maps a hit `run/step/attempt` to the node `attempt:{doc_id}`, and follows `evidenced_by`
  edges to claims. Both sides are verified against the same trace row, so ok on both means the same text
  hash. Similarity is shown only as "retrieved via", never as weight or status.
- **Output groups, always separate:** `claims` (status, counts, all supporting and contradicting attempts with
  fingerprints, reviews), `experiences_without_claims` (failed, `model_unverified` or claimless, labelled
  raw), `unconfirmed` (source not confirmed, `not_in_graph`, with a reason) and notes (similarity note, claim
  note, status meanings, whether `--root` was given). `--json` carries the same groups.
- **Commands.** `recall QUERY --db --index --graph [--reviews] [--root] [--k] [--min-similarity] [--json]`;
  `experience review CLAIM_ID --verdict --note --by --root --graph --reviews`;
  `experience claims` gains `[--reviews] [--root]` and prints status and counts. New exit code 15 for the
  reviews file; existing codes 3, 8, 11, 12, 13 are reused. No config is read and no model client is built.
- **Code layout.** `experience/current.py` (re-derivation, the only new user of the dependency graph),
  `experience/reviews.py`, `experience/assess.py` (pure status rules), and a `retrieval/` package
  (`models.py`, `recall.py`). No new dependency.

## Alternatives considered
- **A numeric confidence score.** Looks precise and cannot be calibrated with the data we have; it would
  invite treating repetition as proof. Rejected for now; categories plus counts carry the same information.
- **Time-based expiry.** Age says nothing about whether code changed; the fingerprint does. Rejected.
- **Only same-version conflicts.** Hides cross-version disagreement that a reader may need. Rejected; it is
  shown as `differs_across_versions` instead.
- **Reviews in the trace database or in the graph file.** The first mixes validated knowledge with raw traces
  and forces a migration on every reader; the second needs a special case because `experience build` rewrites
  every graph table. A separate file keeps both derived and raw stores clean.
- **Extending `memory search`.** Would make a similarity command carry validation semantics. A separate
  `recall` command keeps the two meanings apart.
- **Letting model output validate or contradict.** Model text is data, and its confidence is not evidence.

## Amendments made while implementing (2026-10-17)
- `experience review` reads only the graph file, the reviews file and `--root`; it needs no `--db`.
- `experience claims` keeps its v0.8.0 line and appends the evidence status in brackets; with `--root` or
  `--reviews` it also prints the observations, reviews and re-derivation behind each status. In those outputs
  the repetition-only note replaces the v0.8.0 note "claims are unvalidated", which would contradict a
  `validated` status.
- A confirming review or a holding re-derivation counts as support for the version it names, and a rejecting
  review or a refuted re-derivation as contradiction, so a rejection of a claim that still re-derives is
  `conflicting`, not `validated`.
- Limit found: the graph does not record which application a run analysed (`source_root` is not copied into
  it), so claims about equal node ids from different applications merge and differ only by fingerprint.
- Hits the index verified but the graph does not know (a run newer than `experience build`) are listed as
  `not_in_graph` under unconfirmed, with a hint to rebuild.

## Consequences
- Retrieval answers are inspectable: every claim lists the attempts and reviews it rests on, and every
  status follows a table-driven rule with a test.
- Re-derivation is the same analyzer that produced the claim, so `validated` can repeat an analyzer bug. A
  review is a human assertion with no authentication. Both limits are printed.
- Absence-based contradiction relies on `truncated = false`; an analyzer that under-reports without saying so
  would produce false contradictions or false validations. Only two predicates exist, so only two kinds of
  claim can be assessed; new deterministic capabilities need an explicit mapping.
- A format-1 graph file must be rebuilt after the upgrade. The whole graph is still read into memory.
- Three derived or separate stores now exist (vector index, graph file, reviews file); reviews can outlive
  the claims they refer to and are then reported as orphaned.

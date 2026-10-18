# ADR-0023: Context assembly profiles, conflict-safe selection and declared task state

## Status
Accepted (design choices confirmed by the user 2026-10-18: profile per task, trace schema bump acceptable, the limit counts items; text reviewed and accepted by the user 2026-10-18, before any code was written)

## Date
2026-10-18

## Context
v1.4.0 asks for context assembled by relevance, confidence, provenance, validity and conflict; defined
behaviour for precision-critical and recall-critical tasks; and a definition of when a task needs retained
state. Checked in the code on 2026-10-18:

- `retrieval/context.build_context` gates hits, then cuts claims and raw items to `MAX_CLAIMS` / `MAX_RAW`
  (50 each) in recall order. A conflicting claim ranked 51st is dropped as `over_limit`; nothing says which.
  That is the roadmap's "relevant conflicts disappear" defect, and the order is undocumented.
- `k` and `min_similarity` are the only retrieval knobs. There is one behaviour for every task.
- Between steps the only state is `input_refs` (one field of one earlier step's output, with its basis and
  model recorded). A step that needs several fields, or a whole output, cannot ask for it.

Constraints kept from ADR-0016/0017: statuses are computed, never stored or scored; similarity is not proof;
the pack is untrusted data; no model summarises or compresses evidence (a summary could hide a conflict);
agents import nothing from `experience`.

## Decision
- **Selection order is fixed and documented.** After the existing gates, items are sorted by: (1) status
  group: `conflicting`, `contested` first; then `stale`, `expired`, `environment_changed`,
  `environment_unverified`, `unknown`; then `validated`; then `supported`; then `unsupported`; (2) corroboration
  (more independent sources first); (3) best similarity, descending; (4) claim id, ascending. The sort is a
  pure function with no clock or randomness, so the same input gives the same pack.
- **Conflicts are never cut for size.** The item limit (50, unchanged, counted in items) applies to the
  non-conflict groups only. `conflicting` and `contested` claims are always kept; if they alone exceed the
  limit the pack holds all of them and says so (`conflicts_over_limit`). Items cut by the limit are listed by
  id and status in `withheld.dropped` (not only counted), and shown to the person.
- **Two profiles, chosen per task** with `task --profile precision|recall` (default `precision`, today's
  behaviour). The person chooses; nothing infers it and no model decides.
  - `precision`: current cut-off, `k` as given, claims in every status, raw items only from the same
    application, `unsupported` and `unknown` claims kept but ordered last.
  - `recall`: lower default cut-off and larger default `k` (explicit `--k` and `--min-similarity` still win),
    and raw items from runs with no recorded application are also offered, labelled `unrecorded`. It never
    bypasses the application, subject, expiry or environment gates: those protect against misleading context,
    not against missing context.
  The numbers are set in code constants with the reason next to them, not tuned claims; the 0.2 cut-off still
  rests on 3 documents and the recall value is equally rough.
- **Redundancy.** Claims are already one node per claim id. Raw items of the same run and step collapse to the
  final attempt (already the unit indexed); a collapsed duplicate is counted, never silently removed.
- **Task state is declared, held by the orchestrator, and finite.** A plan may declare `state` entries: a name,
  a source `step_id` and either a list of output fields or `*` for the whole output. A later step may take
  them through `input_refs` by name. The runner stores the value only from a succeeded step, keeps its basis
  and model with it (so `model_unverified` stays visible), and records for each use which step and fields
  supplied it. A reference to a step that failed, was blocked or never ran, or to a field that is absent,
  fails the step with a fixed message; state is never defaulted. Nothing persists beyond the run; there is no
  long-lived agent, and a task with no `state` behaves exactly as before.
- **Trace schema 3 -> 4** (a `MIGRATIONS` entry; additive): `run_context` gains a `profile` column, written
  with the pack and cross-checked against the stored pack on read (a mismatch is a malformed record). The
  pack JSON itself carries the profile, the number of conflicts kept, `conflicts_over_limit` and the dropped
  ids, so older rows (column NULL, fields absent) read as profile `precision` with no drops recorded, not
  back-filled. `task` prints the profile and each dropped id. Amendment at implementation time: step
  `input_sources` are not persisted by the trace store (ADR-0010 amendment, unchanged), so the state
  sources a step used appear in `run --json` / `task --json` output (`input_sources`, with `state`, step,
  fields and basis) but not in the trace database; persisting them would be a further schema change and is
  not done here.
- **Not done:** token counting, summarising, embedding-based redundancy removal, automatic profile choice,
  state shared across runs (that is memory and has its own rules), a per-step profile.

## Alternatives considered
- One adaptive behaviour chosen by a model or by the request text: unpredictable and untestable. Rejected.
- Profile per plan step: more flags and a harder trace; no use case yet. Deferred.
- A character or token budget: needs a tokenizer per provider and invites truncating mid-claim. Deferred; the
  item count is the unit users asked for.
- Summarising low-priority evidence to fit more: hides conflicts and adds model output to the pack. Rejected.
- Dropping the limit for everything: unbounded packs for a small local model. Rejected; only conflicts are exempt.
- A single long-lived stateful agent: hidden state, hard to trace, not needed by the roadmap. Rejected.
- Implicit state (every step sees all earlier outputs): leaks unrelated and model-written text into steps.
  Rejected in favour of declared references.

## Consequences
- Which items reach an agent follows a written order; conflicts survive any size limit.
- A very conflicted application can produce a pack larger than 50 claims; a small local model may truncate
  it itself (unverified).
- `recall` mode trades precision for coverage and says which items it admitted that `precision` would not.
- Cut-off and `k` values for both profiles remain rough until measured on real data.
- Plans with state are slightly longer to write; a broken reference stops the step instead of running on
  missing data.
- Schema bump 3 -> 4: older trace files are migrated on write; older readers refuse the new file.
- Tests required before done: ordering determinism; a conflict at position 60 survives; profile differences
  on one fixture; redundant and weak evidence; missing evidence gives the explicit empty pack; a 3-step plan
  where step 3 needs step 1's output, and failed/blocked/absent sources each fail with their message;
  persistence of profile and drops across processes; at least three hand-run mutation checks.

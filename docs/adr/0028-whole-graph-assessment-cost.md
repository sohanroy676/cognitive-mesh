# ADR-0028: Cost of whole-graph assessment and source checks (amends ADR-0019, ADR-0026, ADR-0027)

## Status
Accepted (text reviewed and accepted by the user 2026-10-19, before any code was written)

## Date
2026-10-19

## Context
ADR-0027 left two stages untouched. Measured 2026-10-19 at 20,000 synthetic runs (105,642 nodes, 251,200 edges,
one run each, Windows 11, CPython 3.12, no model): whole-graph `check_sources` 14.8 s and whole-graph `assess_claims`
20.3 s; the process peak of 2.5 GB is set by `assess_claims`, which adds about 1.4 GB on top of the loaded graph.
`recall` and `task` avoid both (ADR-0019 scopes them to the subjects their hits reach).

Reading the code at `8d8f997` shows which commands actually pay (the ADR-0026/0027 text and the README say
"`experience claims/outcome/check`"; that is wrong for `outcome`):
- `experience outcome` (`cli.py`, `_experience_outcome`) only loads the graph. It runs neither stage.
- `experience claims` runs `check_sources` and `assess_claims` over every claim.
- `experience promote` runs both over every claim, then reads the one entry for the claim it was given.
- `experience show` runs `check_sources` over the whole graph to print one node's neighbourhood.
- `experience check` runs `check_sources`; checking the whole graph is its purpose.

Scoped helpers already exist and are used by `recall`: `assess_claims(..., only=...)`, `attempts_behind()`
(`experience/assess.py`) and `check_attempts()` (`experience/verify.py`).

Hypothesis, not yet measured: in `assess_claim`, `_contradiction_candidates` returns every attempt that listed the
claim's subject (or every `unresolved_report` attempt for an `is_unresolved` claim). Each becomes a
`ContradictingAttempt` object kept in the result. With about a thousand such attempts per subject and 1,600
claims, the same subject's candidates are walked and rebuilt for every claim on that subject, and `outcomes` is
filtered linearly per claim. Whether this, `SupportingAttempt` construction, or `corroborate` is the cost is what
the profile in point 1 must show.

Constraints: statuses are computed on every call and never stored (ADR-0016); every source is re-checked against
the trace database (ADR-0015); no result may change; no new dependency.

## Decision
1. **Profile first, before any code.** A scratch tool (not committed; same generator and approach as ADR-0027's
   `measure.py`) at 5,000 and 20,000 runs records time and peak working set for: the parts of `assess_claim`
   (`claim_parts`, the candidate walk, `SupportingAttempt` and `ContradictingAttempt` construction, `corroborate`,
   `assess_validity`, outcome filtering), the number of contradicting attempts per claim, and the retained size of
   the result (tracemalloc); and the parts of `check_sources` (`verify_source`, run status, step/claim/entity
   aggregation). Numbers and commands go to `progress.md` and this ADR's outcome section.
2. **Scope the commands that need one claim or one neighbourhood.** `experience promote` assesses only the given
   claim (`only={claim_id}`) after `check_attempts(attempts_behind(...))`, and `experience show` checks only the
   nodes it reaches, as `recall` already does. This changes no output; it is shipped whatever the gate says, and an
   equality test (point 3) proves it.
3. **Whole-graph work (`claims`, `check`) has a decision gate.** A stage is optimised only if it is at least 50% of
   its command's time. Allowed candidates, each of which must return results identical claim for claim (and node
   for node) to the code at `8d8f997`, on the fixtures and on generated graphs of 5,000 and 20,000 runs, compared
   against saved reference JSON as in ADR-0027:
   - compute the contradiction candidates once per subject (and once for the `unresolved_report` set) and share
     them across the claims of that subject, filtering coverage and source status once;
   - group outcomes by claim id once, as reviews already are;
   - inside `check_sources`, any saving that still re-checks every attempt against its trace row.
   Success gate at 20,000 runs: `assess_claims` at most half of the same-session baseline time. Memory is reported,
   not gated (point 4).
4. **No weaker evidence, nothing hidden.** Every contradicting attempt stays listed in each assessment and in
   `experience claims --json`; source checks stay full on every call. If the profile shows that memory is dominated
   by the size of the result itself (the complete contradicting lists), reducing it changes what is shown (for
   example counting other-version contradictions instead of listing them, or emitting claims one at a time). That
   comes back to the user as a new decision and is not taken here.
5. **Stop rule.** If no stage passes the gate, or a change would alter any result or weaken a check, only point 2
   ships and the measured limit is documented in the README, with the `outcome` wording corrected.

## Alternatives considered
- **Store assessments in the graph file:** statuses are never stored (ADR-0016), and a stored status would go stale
  when code, reviews or outcomes change. Rejected.
- **Update assessments incrementally on every trace write:** couples the trace store to the graph (rejected in
  ADR-0019).
- **Sample or truncate the contradicting attempts:** hides records the person should see. Rejected.
- **Assess in parallel processes:** raises memory use, which is already the larger problem. Rejected.
- **Optimise now without profiling:** the cause above is a hypothesis; ADR-0026 and ADR-0027 both showed the
  measurement changes the target.

## Consequences
- The work is bounded by a measurement and an equality test, so it cannot silently change a status or a listing.
- It may end with only the scoping of `promote` and `show` (stop rule).
- `experience claims` and `experience check` stay whole-graph by design; their cost is reduced only if the gate
  allows.
- Real-model recall timing at 5,000 and more runs, and the size of the final listing for a large graph, are not
  addressed here.

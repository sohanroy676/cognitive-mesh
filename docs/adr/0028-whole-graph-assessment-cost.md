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

## Outcome (2026-10-19, branch `feature/assessment-cost`)
Profiled first (scratch `profile28.py`, not committed), on the ADR-0027 synthetic graphs (format 6, 1,600 claims,
105,642 nodes and 251,200 edges at 20,000 runs), one run each, Windows 11, CPython 3.12, no model.

| at code `8d8f997` | 5,000 runs | 20,000 runs |
|---|---|---|
| `check_sources`, whole graph | 3.71 s | 14.86 s |
| `assess_claims`, whole graph | 4.57 s | 21.54 s |
| `ContradictingAttempt` objects built (claims x about 243 / 975) | 390,000 | 1,560,000 |
| memory held by the `assess_claims` result (tracemalloc) | 431 MB | 1,684 MB |

Where the time went (cProfile, so inflated): `check_sources` spends 76% in `_check_attempts` and 62% in `store.get`
(one read per run, already cached per run; 20,000 reads), which is the re-check this ADR forbids weakening. In
`assess_claims` the candidate walk (`_contradiction_candidates`, `_same_application`) took 10.3 s of the 44 s
profiled, `corroborate` 2.3 s (1,560,000 `con()` calls), and pydantic construction of the contradicting objects
and `assess_claim`'s own loop most of the rest. The hypothesis in Context was right: every claim about one subject
rebuilt the same ~975 objects.

**Point 2 (scoping), shipped:** `experience promote` now checks only the attempts the claim is judged by
(`check_attempts(attempts_behind(...))`) and assesses only that claim; `experience show` checks only the shown node
and what it reaches with the new `check_nodes` (`experience/verify.py`), which applies the same rules as
`check_sources` (a run is judged from its attempts' documents, an entity from every attempt and claim pointing at
it). At 20,000 runs `promote`'s check and assessment take 0.44 to 0.51 s (five random claims; whole-graph path
about 36 s) and equal the saved reference output of the old code. `show --transitive` from a claim took 9.2 to
10.1 s because it reaches shared entities (`capability:dependencies_of` alone is 9.0 s: its status rests on every
attempt that used it); that is exact by design, not approximated. Without `--transitive` it reaches only
neighbours. 300 random nodes of every kind checked in 0.23 s and all equal the whole-graph statuses.

**Point 3 (whole-graph `claims`), gate met:** `assess_claims` now builds the contradiction candidates once per
(predicate, subject, application) and the immutable `ContradictingAttempt` objects once per (attempt, same-version)
and shares them across claims (`_Listings` in `experience/assess.py`), groups outcomes by claim once, and calls
`con()` once per distinct fingerprint. Every contradicting attempt is still listed in every assessment; the
serialised assessments of all 1,600 claims equal the saved reference of the old code at 5,000 and 20,000 runs.

| after | 5,000 runs | 20,000 runs |
|---|---|---|
| `assess_claims`, whole graph | 1.23 s (was 4.57 s) | 5.43 s (was 21.54 s; gate: at most half, 10.8 s, met) |
| traced peak during the call | 39 MB (was 431 MB) | 116 MB (was 1,684 MB) |

Point 4 was not triggered: the memory fell as a side effect of sharing objects, not by hiding records. The
JSON output of `experience claims` is unchanged and still lists every contradicting attempt per claim, so its size
is unchanged too.

**Not changed (stop rule for this stage):** `check_sources` (about 15 s at 20,000 runs) stays, because 76% of it is
the per-run re-read of the trace row and no saving was found that keeps every attempt re-checked. `experience
check` therefore still costs about 15 s plus the load, and `experience claims` about 15 s plus 5 s plus the load.

**Correction:** `experience outcome` only loads the graph; it runs neither stage. Earlier text (ADR-0026, ADR-0027,
README) that listed `experience claims/outcome/check` should read `claims`, `promote`, `show` and `check`.

**Follow-up checks (2026-10-19, the two items first listed as not run):**
- *Reviews and outcomes at scale.* Scratch `equal_ro.py` (session scratchpad, not committed) loads the old
  `experience/assess.py` from `git show 8d8f997:...` as a separate module and compares its `assess_claims` with the new
  one, serialised claim for claim, on the 5,000 and 20,000 run graphs with 909 reviews and 694 outcomes spread over the
  1,600 claims (confirmed and rejected reviews on several code versions, one expired; person and check outcomes
  succeeded, failed and ambiguous, with and without `valid_until`), with no root and with a stubbed current source at
  two fingerprints (re-derivation holds, refuted or subject absent by hash). All equal, also with `only=` a subset of
  178 claims. Because the synthetic data makes every claim `conflicting`, three damaged-source variants were added
  (the sources of some code versions, or 70% of attempts, marked missing or changed): all equal, and the statuses
  seen were `conflicting`, `unsupported`, `expired`, `contested`, `unknown` and `validated`. Times at 20,000 runs with
  reviews and outcomes: old 18.8 to 26.0 s, new 5.0 to 6.2 s. Not reached by this data: `stale`, `supported`, the
  environment statuses (covered by the unit tests only).
- *Real MiniLM.* Real CLI, real `all-MiniLM-L6-v2` (cached), scratch directory, `tests/fixtures/e2e_app`: `task`
  session 1, `cart.py` edited to import `tax`, `task` again, `memory index` (4 added), `experience build` (2 claims),
  `experience review` (confirmed for cart->tax, rejected for cart->pricing), `experience outcome` (check, succeeded),
  then `experience claims`, `recall` and `task` with `--reviews` and `--outcomes`, every command exit 0. cart->tax
  was `[validated]` and `corroborated` with 3 independent sources (analyzer, check, person); cart->pricing was
  `[stale]` with the rejecting review listed; `task` offered both with their run ids on both sides and "reviews: 1
  confirmed, 0 rejected"; `--reviews missing.sqlite3` exited 15. The `task` pack shows review counts but no outcome
  line (the outcome shows only in the status and in `recall`/`claims`); that is existing behaviour, not changed.

**Tests:** `tests/test_assessment_cost.py` (14): `check_nodes` equals `check_sources` for every node, alone and
together, on an untouched and a damaged database (deleted, edited, malformed runs) and for a run re-recorded
unfinished; it reads only the runs it needs; the shared assessment equals a fresh claim-by-claim assessment with
reviews and a current source; one listing is "same version" for one claim and not another; a listing with a
missing source never contradicts; `only=` unchanged; `show` and `promote` through `main()` give the whole-graph
statuses on a damaged database and keep their errors (exit 8 missing database, unknown node, not a claim).
Mutations that failed tests: cache key ignoring same-version (2 failed), candidate key ignoring subject (4),
entity status ignoring claims (3), run status skipping its attempts (1, after adding the unfinished-run case; the
first version of the tests did not catch it). Suite 1323 passed, 5 skipped; ruff, format and mypy clean.

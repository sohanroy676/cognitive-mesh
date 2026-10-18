# ADR-0022: Environment identifiers, validity periods and staleness

## Status
Accepted 2026-10-18 (the user took the recommendations on the three open questions below: ship the environment comparison tested with synthetic claims; withhold `expired` claims from the agent pack; set `valid_until` on `experience review` and `experience outcome`). Part of v1.3.0, after ADR-0021. No code written yet.

## Date
2026-10-18

## Context
`DETAILED_ROADMAP.md` v1.3.0 asks the system to record environment identifiers and validity information, to say how
outdated or environment-dependent knowledge is treated, and to have a documented rule for marking knowledge stale.
Its "Fix if" list forbids three things: ignoring environment changes where they matter, discarding every old claim
regardless of validity, and inferring staleness without a documented rule.

What exists today:
- Every run stores `started_at`, `finished_at`, a `source_fingerprint` (a hash of the analysed files) and a
  `TraceEnvironment` (cognitive-mesh version, Python version, platform name of the machine that ran the analysis).
- Claim status is computed per code version on every call (ADR-0016): `stale` already means "re-derived against the
  current code and no longer found"; `unknown` means nothing could be checked. A claim about code is therefore tied to
  a code version by the fingerprint, not to a date.
- The only claims built so far are `depends_on` and `is_unresolved`, derived by a deterministic static analyser from
  files. They do not depend on the machine, the clock or the network. Their analysis does depend on the analyser
  version, which `cognitive_mesh_version` already records.

So most of what the roadmap calls "environment-dependent" does not yet exist as a claim type. This ADR has to define
the rule without inventing claim types the system cannot produce, and without letting a clock quietly decide truth.

## Decision (proposed)
1. **Every claim declares its scope.** A claim has an `environment_scope`:
   - `code` (default for today's claims): valid for the code version it was observed on, whatever machine or date.
     Staleness is decided by the fingerprint and re-derivation, as today. Age alone never changes its status.
   - `environment`: valid only for the recorded environment (analyser version, Python version, platform, and any
     named identifiers a future claim type adds). No such claim is produced in v1.3.0; the scope and the comparison
     are implemented and tested with synthetic claims, so the first real environment-dependent claim type does not
     need a new rule.
   - A claim with no recorded scope (older graph files, or a run without environment data) is `unscoped`: it is shown
     as such and never treated as `code` or `environment` by guess.
2. **Environment identifiers are recorded, not inferred.** The run already stores the analyser, Python and platform
   values. v1.3.0 adds nothing that reads the host beyond these (no hostname, user or path), to keep the stored record
   free of personal data. A fixed list of identifier names is part of the schema; a value outside it is refused.
3. **Comparison is explicit and three-valued.** For an `environment` claim, the current environment is compared with
   the recorded one per identifier: `same`, `different`, or `unknown` (an identifier missing on either side). Any
   `different` makes the claim `environment_changed`; any `unknown` with no `different` makes it
   `environment_unverified`. Neither is `validated`. Today's `code` claims ignore the comparison, because their
   evidence is the re-derivation, and the comparison is not a substitute for it.
4. **Validity periods are optional and declared, never defaulted.** A claim may carry `valid_until` (a UTC date) only
   when something sets it: a person's review or outcome that names an expiry, or a future claim type whose source
   states one. There is no global time-to-live. Without `valid_until` a claim does not expire by time.
   - Past `valid_until`: status `expired`. It is listed, never dropped, and is not `validated` or `corroborated`.
   - `valid_until` in the future or absent: no effect on status.
   - The clock comes from one injected `now`, read once per command and printed with the result, so a test can fix it
     and a person can see which date was used. A missing or unreadable clock yields `unknown`, not `expired`.
5. **The staleness rule (documented, the only one).** A claim is marked stale or expired only by one of these, and the
   output names which fired:
   - the code changed and re-derivation no longer finds it (`stale`, ADR-0016);
   - its `valid_until` is before the date used (`expired`);
   - its `environment` scope compares as `different` (`environment_changed`).
   Age, retrieval frequency, similarity and model opinion are not reasons. "No re-derivation possible" stays
   `unknown`, not stale.
6. **Nothing is deleted.** These states change what is offered and how it is labelled. The relevance gate withholds
   `expired` and `environment_changed` claims from the agent pack with a listed reason, as it does for another
   application; `environment_unverified` and `unscoped` claims are offered with that label. History in the trace
   database, the reviews file and the outcomes file is not rewritten.
7. **Storage.** `environment_scope` and the environment identifiers live on the claim node and run node in the graph
   (format 4 -> 5, older files refused as before). `valid_until` is stored with the review or outcome that sets it
   (those files get an additive schema version bump), not on the claim, so the claim node stays derivable from the
   trace database alone. Trace schema is unchanged.

## Alternatives considered
- **A default time-to-live for all claims.** Simple, but it discards valid code facts because they are old, which the
  roadmap forbids, and the number would be arbitrary.
- **Treat the fingerprint as the only validity rule and skip scopes.** Correct for today's claims, but leaves no place
  for a claim that depends on the machine, and the roadmap asks for that distinction explicitly.
- **Record the full host environment (hostname, installed packages, env vars).** Richer, but it stores personal and
  possibly secret data and makes comparisons noisy; a fixed identifier list is enough for the rule.
- **Infer expiry from how fast a claim type "usually" changes.** That is staleness without a documented rule.

## Consequences
- Easier: the three roadmap cases (expired, undated, environment-independent) each map to a named state with a tested
  rule; a future environment-dependent claim type plugs in without a new rule.
- Harder: another graph format bump and two additive file-version bumps; more statuses to explain in output.
- Accepted limits: v1.3.0 produces no real environment-dependent claim, so the environment path is exercised by
  synthetic claims only; `valid_until` exists only where a person or a future source sets it, so most claims will
  never expire by time; platform and version strings are coarse and can be equal on genuinely different machines.

## Answers to the open questions
1. Ship with synthetic claims; the roadmap tests the environment path with a fixture.
2. `expired` claims are withheld from the agent pack with a listed reason.
3. `valid_until` is set on `experience review` and `experience outcome`.

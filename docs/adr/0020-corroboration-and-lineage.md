# ADR-0020: Corroboration and lineage

## Status
Proposed (drafted 2026-10-18; awaiting the user's acceptance. No code is changed by this ADR.)

## Date
2026-10-18

## Context
v1.2.0 (`docs/DETAILED_ROADMAP.md`) asks that repeated dependent outputs are not mistaken for independent
corroboration, that confidence changes are explainable, that conflicts stay discoverable, and that claims keep their
source lineage.

What exists today (ADR-0015, 0016, 0018):
- A claim node has one `evidenced_by` edge per run that observed it, each with the run's source fingerprint. `claims`
  prints "observed Nx", where N is the number of runs. `ClaimCounts.distinct_fingerprints` already counts the code
  versions among the ok attempts.
- Validation comes from three places: a re-derivation from `--root`, a person's review, and a recorded outcome
  (source `check`, `person` or `promotion`). Statuses are categories computed on every call, never stored.

The problem: "observed 5x" is five runs of the same deterministic analyzer on the same code. They are one
observation repeated, not five. The analyzer is deterministic, so a re-derivation by the same analyzer is not
independent of it either. A different code version is a different fact, so a claim that held on two versions is
persistence, not corroboration of either.

## Decision
Corroboration is evidence that **different kinds of method** agree about **one code version**. Repetition by one
method adds nothing.

1. **Method classes** (the only independence rule):
   - `analyzer`: observations in traces and re-derivations from `--root`. They share one deterministic
     implementation, so any number of them is one source.
   - `person`: reviews and outcomes whose source is `person`. Reviews are unauthenticated (ADR-0016), so reviewer
     names do not make people independent; all of them are one source.
   - `check`: outcomes whose source is `check`.
   - `promotion` outcomes, model output and similarity are not evidence (unchanged) and never count.
2. **Per code version.** For a fingerprint F, the independent sources of a claim are the method classes that
   support it on F. The assessment reports, for the current version when `--root` is given, otherwise for each
   version separately, never merged across versions. Persistence across versions is shown as a separate count.
3. **Corroboration is a category**, computed on every call and never stored, next to the existing status (the
   status is unchanged):
   - `none`: no lineage, meaning no supporting attempt whose source check is ok and no other evidence.
   - `single`: one method class supports it on F.
   - `corroborated`: two or more method classes support it on F and none contradicts it on F.
   - `contradicted`: any method class contradicts it on F. Support is still listed; this never hides or removes
     either side (the status stays `conflicting` or `contested` as today).
4. **Explainable.** Every assessment carries a `corroboration_reason`: a fixed sentence built from the facts, for
   example "supported by analyzer (3 runs, 1 independent source) and person (1 review)". Repeats are reported as
   dependent repeats per class, with their run and review ids. A category never changes without a changed input.
5. **Lineage.** Each independent source lists its records: analyzer run ids and fingerprints, review ids, outcome
   ids. A supporting attempt with no fingerprint, or whose source check is not ok, is listed as `no_lineage` and
   counts for nothing.
6. **Surfaces.** `experience claims` replaces "observed Nx" with "N independent source(s), M dependent
   repeat(s)" plus the category. `recall` and `RecalledContext` carry the category and reason read-only. Nothing
   outside the pure rules in `experience/assess.py` decides it. The graph format does not change.

## Alternatives considered
- **Independence by fingerprint** (one source per code version). Easy, and it uses data already in the graph, but
  it treats one analyzer observed on two versions as two corroborations, which is persistence, and a new commit
  would raise confidence with no new evidence. Rejected.
- **A numeric confidence** combining counts and weights. Needs thresholds that nothing measures yet and invites
  false precision; the project already rejected numbers in ADR-0016. Rejected.
- **Counting every run, discounted by a decay factor.** Still treats repeats as evidence. Rejected.
- **Trusting reviewer names or model names as independence.** Neither is authenticated or independent; rejected.

## Consequences
- Easier: repeated runs can no longer raise confidence; every category has a reason and a lineage listing.
- Harder: with only the analyzer in play, most claims stay `single`; `corroborated` needs a person's review or a
  recorded check outcome. That is intended, and it will look stingy until reviews are used.
- The "observed Nx" count is replaced in the CLI output; tests that pin that text change with it.
- Accepted limitation: `person` is one class, so two real reviewers still count once. Authenticated identities
  would change that and need their own ADR.
- Accepted limitation: the analyzer's correctness is assumed. If it is wrong, every analyzer-based source is wrong
  together, which is why they are one source.

## Tests this ADR requires (`tests/test_corroboration.py`)
- Same analyzer observed in 1 and in 5 runs on one fingerprint: `single`, 1 independent source, 4 repeats.
- A re-derivation added to an analyzer observation: still `single`.
- Analyzer plus a person review, or plus a `check` outcome, on the current fingerprint: `corroborated`.
- Analyzer on F1, person review on F2: `single` on each, never `corroborated`; persistence count 1 version
  with a review.
- A rejecting review or failed outcome next to support: `contradicted`, both sides listed, status unchanged.
- No fingerprint or source not ok: `none`, labelled `no_lineage`.
- A `promotion` outcome and a model-written run add nothing.
- Every category has a non-empty reason; the same inputs give the same category and reason.

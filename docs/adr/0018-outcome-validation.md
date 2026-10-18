# ADR-0018: Outcome validation and memory revision

## Status
Accepted (design choices confirmed by the user in plan mode 2026-10-18; text accepted by the user 2026-10-18, before any v1.1.0 code was written)

## Date
2026-10-18

## Context
Up to v1.0.0 a claim's status (ADR-0016) is computed from the code: it is re-derived from the application, checked
against a complete listing, or confirmed or rejected by a person's review (ADR-0016). Nothing records whether the
work a claim supported actually turned out right. A run that succeeded proves only that the agent finished, not
that its claim held. v1.1.0 must let outcome evidence weaken, flag or revise knowledge, while keeping every earlier
state inspectable, and must refuse promotion when checks fail.

## Decision
1. **Outcome evidence** is one of two kinds, and a run's own success is never one:
   - an *automated check*: the exit code of a named command the orchestrator ran and recorded, with the command
     text and the code fingerprint it ran against;
   - a *person's outcome*: a verdict with a reason, recorded like a review (ADR-0016).
   A person's verdict is kept with its reason and is never silently overwritten by an automated result.
2. **Outcome events are append-only.** Each outcome is a new event that names its run and its claim. A failed
   outcome makes the derived status `contested`; an ambiguous one (no check ran, the check was cut short, or the
   fingerprint no longer matches) makes it `unknown`. Earlier events are never deleted or edited, so prior states
   stay inspectable.
3. **Disagreement is shown, not resolved silently.** When a person's verdict and an automated result disagree,
   the derived status is `conflicting` and both sources are listed. A later review is recorded as the resolution;
   it does not erase the disagreement.
4. **Revisions are append-only rows.** Each revision names the previous revision, the evidence it used, and the
   code fingerprint it was computed on. A revised claim keeps its old rows; "current" is the latest row for a claim.
5. **A promotion that fails validation is refused.** The promotion call returns an explicit failure listing each
   unmet check, and writes a `rejected` event so the attempt is visible. The experience stays unpromoted.
6. Statuses are categories computed per code version on every call, as in ADR-0016. None is stored as a number,
   and nothing an agent produced (model text, repetition, similarity) counts as outcome evidence.

## Alternatives considered
- **Automated checks only.** Simpler, but a person could not record that a claim was wrong without writing a
  command. Rejected: the people reviewing the outcome are often the only source for it.
- **Person's verdict only.** Reuses `reviews.py` directly, but nothing is checked automatically. Rejected for the
  same reason.
- **Downgrade the status in place.** Smaller store, but the earlier state is lost. Rejected: the brief requires
  that prior evidence stays inspectable.
- **Latest record wins on disagreement.** Simple, but the disagreement is hidden. Rejected.
- **Stage a rejected promotion silently.** Matches how raw traces are kept, but a refusal is easy to miss. Rejected.
- **Overwrite a revised row and copy the old state to a history table.** Keeps one current row, but the link
  between a revision and the evidence it used is weaker. Rejected.

## Consequences
- Easier: every status change can be traced to an event or revision with its evidence and fingerprint.
- Harder: storage grows with each outcome and revision; a query for "current" needs the latest row per claim.
- Accepted limitation: an automated check proves only that a command exited with a code. It does not prove the
  claim was true, and a missing check is reported as `unknown`, never as success.
- Accepted limitation: `conflicting` can persist until a person reviews it; the system does not resolve it.
- Schema: a new store version with a migration, tested both ways (ADR-0011 pattern).

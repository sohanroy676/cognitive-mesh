# ADR-0029: Recorded outcomes in the context pack (amends ADR-0017, ADR-0018, ADR-0023)

## Status
Accepted (text reviewed and accepted by the user 2026-10-19, before any code was written)

## Date
2026-10-19

## Context
`task` offers the agents a `RecalledContext` pack (ADR-0017). For each claim the pack carries its status, meaning,
corroboration, the run/doc ids on both sides, and the counts of confirming and rejecting reviews. It carries
nothing about recorded outcomes (ADR-0018), although `assess_claim` already computes them: `ClaimAssessment.outcome`
(`OutcomeState`: `none`, `succeeded`, `failed`, `ambiguous`, `conflict`, for the current code version only) and
`ClaimAssessment.outcomes` (every outcome for the claim, with fingerprints). The status already reflects an outcome
in two cases (`contested` after a failure, `unknown` after an ambiguous result), but a claim that is `validated` or
`corroborated` partly because of a successful outcome, or that has an outcome recorded for another code version,
shows no sign of it. The gap was seen in the real MiniLM walkthrough of ADR-0028 (`task --reviews --outcomes`
printed the review counts and no outcome line). `cli.py` `_print_context` and the prompt built by
`code_explainer.prior_context_for_prompt` both print the pack as it is, so whatever the pack lacks, the person and
the model both lack.

Constraints: the pack holds ids, statuses and counts, never trace excerpts or free text that someone wrote (review
notes are excluded for that reason); a pack is stored with its run (`run_context`, trace schema 2) and older rows must
keep reading; `RecalledContext` and `ClaimItem` are `extra="forbid"`, frozen pydantic models; an outcome is a
recorded account, not proof (ADR-0018); no new dependency.

## Decision
1. **Three fields on `ClaimItem` (`agents/context.py`), all with defaults:**
   - `outcome: str | None = None`: the `OutcomeState` value for the current code version, as `assess_claim`
     computed it. `None` means the pack was written before this field existed, never "no outcome".
   - `outcomes_current: int = 0` and `outcomes_other: int = 0`: how many evidence outcomes (`succeeded`, `failed`,
     `ambiguous`, i.e. `EVIDENCE_RESULTS`) were recorded for this claim on the current code version and on any other
     or unrecorded version. A refused promotion (`rejected`, source `promotion`) is not evidence and is not counted.
2. **One field on `RecalledContext`:** `outcomes_consulted: bool | None = None`. `True` if an outcomes file was
   read for this task, `False` if none was given, `None` for a pack written before this field existed. This is
   what makes "no outcome recorded" different from "outcomes were not looked at".
3. **No outcome text enters the pack.** `detail` (a check's command or a person's reason), `recorder` and dates are
   untrusted text or irrelevant to the agent and stay in the outcomes file, as review notes do.
4. **Where it is set.** `retrieval/context.py::_claim_item` fills the three claim fields from the assessment
   (`a.outcome`, and `a.outcomes` split by `a.current_fingerprint`). `workflow.prepare_context` sets
   `outcomes_consulted` from whether `outcomes_file` was given, on the missing-index/graph path too.
   The relevance gate, the selection order (ADR-0023) and the withheld rules are unchanged: an outcome never moves
   or removes a claim by itself.
5. **Where it is shown.** `cli._print_context` prints, per claim with outcomes, a line of the form
   `outcome: STATE for the current code version (N current, M other version)`, and one pack-level line when
   `outcomes_consulted` is `False` (`outcomes: not consulted (no --outcomes file)`). `traces show` has its own printer
   (`_print_offered_context`, which reads the stored pack); it gets the same per-claim line, and a stored older pack
   prints `outcomes: not recorded in this pack`.
   `code_explainer` passes the pack through `prior_context_for_prompt` unchanged, within the existing size budget,
   so the prompt JSON gains the fields and the system prompt does not change.
6. **Compatibility.** No trace schema bump: every new field has a default, so an older stored pack validates and reads
   back as "not recorded". A pack written by this version cannot be read by older code (`extra="forbid"`); that is
   accepted because the trace database is not downgraded in place.

## Alternatives considered
- **Copy outcome `detail` into the pack:** the text is written by a person or is a command line, i.e. untrusted free
  text for the model. Rejected, same as review notes.
- **Show only the newest outcome:** hides a person and a check that disagree (`conflict`) and hides history on other
  versions. Rejected; the state plus both counts is cheap.
- **Reorder or filter claims by outcome:** a selection change belongs with ADR-0023's order and needs its own
  evidence. Out of scope; the status already carries `contested` and `unknown`.
- **Reuse the review counts for outcomes:** reviews and outcomes are different record types with different rules
  (ADR-0018, ADR-0020 source classes). Merging the counts would blur them. Rejected.
- **Bump the trace schema:** unnecessary while the fields have defaults. Rejected.

## Consequences
- The person and the agents can see when a claim leans on a recorded outcome and whether the outcome belongs to the
  current code version, without reading the outcomes file.
- `None` versus `0`/`False` keeps old and new packs distinguishable; tests must pin that an old stored pack never
  reads as "no outcome".
- The pack grows by about 40 bytes per claim, and by one line in the prompt size budget.
- Limits: the pack says that outcomes exist and what state they give, not who recorded them or why. An outcome is
  still a recorded account, not proof the claim is true.

## Tests that must exist before this is done
- `build_context` / `_claim_item` for each `OutcomeState`, outcomes on another version only, a refused promotion not
  counted, and no outcomes file (`outcomes_consulted` false, `outcome` `none`).
- An old stored pack JSON without the new fields validates and reads back with `None`; `traces show` labels it.
- `task` through `main()` prints the line; `--json` carries the fields.
- A hostile `detail` string in an outcome never appears in the pack JSON or the model prompt.

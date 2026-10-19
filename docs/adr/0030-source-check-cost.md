# ADR-0030: Cost of the whole-graph source check (amends ADR-0028)

## Status
Accepted (text reviewed and accepted by the user 2026-10-19, before any code was written)

## Date
2026-10-19

## Context
ADR-0028 left `check_sources` unchanged under its stop rule: 76% of it was the per-run re-read of the trace row, and
no saving was found that keeps every attempt re-checked. It asked for a new decision before anything else is tried.
This ADR records a fresh profile and the choices that remain.

Measured 2026-10-19 on new synthetic data (scratch `gen30.py`, `prof30.py`, `bulk30.py` in the session scratchpad,
not committed; 400 modules, one dependencies step per run plus a second step in every third run, 4 code versions;
Windows 11, CPython 3.12, no model, one run each, no stray python processes). These graphs are not the ADR-0027/0028
graphs, which no longer exist, so absolute numbers differ from ADR-0028.

| | 5,000 runs | 20,000 runs |
|---|---|---|
| nodes / edges | 36,045 / 86,287 | 132,487 / 324,171 |
| `check_sources` | 2.58 s | 10.83 s |
| four queries per run (as `TraceStore.get` does) | 0.90 s | 3.58 s |
| the same four tables in four bulk queries | 0.05 s | 0.22 s |

cProfile at 20,000 runs (inflated, 14.8 s): `_check_attempts` 9.1 s (61%); inside it `verify_source` 8.9 s, `get`
7.6 s, of which SQLite `execute` 4.1 s (80,000 calls), `_build_run` 2.9 s, `eligible_documents` 1.2 s; and
`ExperienceGraph.__init__` (NetworkX construction) 2.75 s, which is not a source check.

Constraints: every attempt is re-checked against its trace row on every call (ADR-0015, ADR-0028 point 4); statuses
are never stored (ADR-0016); no result may change; no new dependency.

## Decision
1. **Batched reads, if the gate passes.** Add `TraceStore.get_many(run_ids)`: it reads the `runs`, `steps`,
   `attempts` and `run_context` rows for a chunk of run ids with one query per table (chunks bounded by SQLite's
   variable limit), groups them by run id, and builds each run with the same `_build_run`, so validation, redaction
   handling and error mapping are exactly as `get`. A missing id is reported as missing and a malformed run as
   malformed, per id. `check_sources` and `check_nodes` fill the `verify_source` cache from it. Every attempt is still
   compared with its rebuilt document.
2. **Gate (as in ADR-0028):** it ships only if, at 20,000 runs, `check_sources` is at most half of the same-session
   baseline and the statuses, `claim_support` and diagnostics are identical node for node to a saved reference of
   the current code, on the fixtures, on the generated graphs, and on damaged variants (a deleted run, a changed
   attempt row, a malformed attempt row, a malformed `run_context`, an unfinished run). Measured floor: batching saves
   about 3.4 s of 10.8 s (31%) because `_build_run`, `eligible_documents` and hashing remain, so the gate is expected
   **not** to be met.
3. **Stop rule.** If the gate is not met, nothing ships, the limit is documented in the README (about 11 s at 20,000
   runs on this machine, plus the graph load), and the two decisions below stay open.
4. **Decisions only the user may take; neither is assumed:**
   - *Narrower validation:* `_build_run` validates the whole run, including parts that cannot change a document
     (for example the environment and plan). Skipping them would make a run with, say, a malformed environment
     readable for the check, i.e. change what "unreadable" means. Not done unless the user accepts that change,
     with its damaged-variant tests.
   - *Early stop for entity and run statuses in `show`:* a status is `ok` as soon as one attempt behind it is `ok`.
     Stopping there gives identical output but checks fewer attempts, which ADR-0028 point 4 forbids for the
     stated reason that every attempt is re-checked. Not done unless the user relaxes that point for `show` only.

## Alternatives considered
- **Cache results between calls:** a stored status goes stale when a trace row changes; rejected (ADR-0016).
- **A read-only snapshot with `PRAGMA` tuning (mmap, cache size):** not measured; the queries are not the larger cost
  once batched, and the setting changes behaviour per machine. Not pursued.
- **Parallel processes:** raises memory use; rejected in ADR-0028.
- **Sampling attempts:** hides unchecked records; rejected.

## Consequences
- If the stop rule applies, the cost stays and is written down with numbers, so nobody re-derives it.
- `get_many` would also let `memory index` and `recall` verification read in batches later, but that is not part of
  this decision.
- Accepting either decision in point 4 weakens or reinterprets a check, so it needs its own tests and a note in the
  README.

## Outcome (2026-10-19, stop rule applied; no code in `src/`)
Point 1 was prototyped outside the repo (scratch `batch30.py`, session scratchpad): the four tables are read in four
bulk queries and `TraceStore.get` is served from them through the unchanged `_build_run`, so validation and error
mapping are those of `get`. `check_sources` returned **identical statuses, claim support and diagnostic count, node
for node** (132,487 nodes) to the saved reference of the current code in every run. Damaged-variant equality was not
run, because the gate was not met.

Timing at 20,000 synthetic runs, each in a fresh process, alternating, no stray python processes:

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| baseline `check_sources` | 11.63 s | 11.16 s | 11.44 s | 11.44 s |
| batched (bulk load + check) | 6.50 s | 6.25 s | 6.53 s | 6.50 s |

Batched is 57% of baseline, above the 50% gate of point 2 (earlier single comparisons gave 43% when the batched run
came second in one process, with a warm file cache, and 55 to 57% when it came first; the fresh-process medians are
the fair figure). The time left is `_build_run` validation, `eligible_documents`, hashing, and the NetworkX graph
construction inside `check_sources`, which batching does not touch. At 5,000 runs the same prototype took 42% (warm).

**Decision (point 3):** nothing ships. The limit is in the README. The two decisions in point 4 (narrower validation,
early stop in `show`) are not taken and stay open; either would be a new ADR. A future attempt could try batching
together with a cheaper `_build_run` for the check path, which is exactly the validation question of point 4.

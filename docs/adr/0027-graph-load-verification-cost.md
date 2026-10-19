# ADR-0027: Cost of verifying the experience graph on load (amends ADR-0026)

## Status
Accepted (text reviewed and accepted by the user 2026-10-19, before any code was written)

## Date
2026-10-19

## Context
ADR-0026 measured a 17.0 s graph load at 20,000 synthetic runs and stopped, because no stage reached its 60% gate
and point 3 forbids weaker verification. It left a question: can the two dominant stages, record validation and
the content hash, be made cheaper without being weaker?

Reading `experience/store.py` (`load`) and `experience/models.py` (`canonical`, `ref_key`) at `e19c0c6` shows that
the cost is repeated pydantic and JSON work, not hashing:
- `load` parses one `TraceRef` per `edge_refs` row with `model_validate_json`, then sorts each edge's refs with
  `ref_key`, which calls `model_dump` and `json.dumps` for every ref on every comparison key.
- Identical reference text is stored on many edges: in the 5,000-run graph the `edge_refs` table has 82,000 rows
  but only 15,500 distinct `ref_json` texts (about 5.3 rows per text, measured 2026-10-19), so the same text is
  parsed and keyed several times.
- `canonical()` then re-dumps every node, edge and ref, sorts the refs again with `ref_key`, and builds one large
  JSON string before hashing. SHA-256 itself takes 0.17 s at 20,000 runs.

Baseline for this ADR, measured 2026-10-19 with a scratch tool (not committed; `prof.py` in the session
scratchpad) on Windows 11, CPython 3.12, one process, no model. The generator is a variant of the ADR-0026 one
(40 modules, 5 code versions, two `dependencies_of` steps per run, a failed step every 10th run). Its graph sizes
are close to, but not the same as, ADR-0026's, and the machine was slower, so these numbers are compared only
with each other. Seconds, wall time without tracing; peak MB from a separate `tracemalloc` pass.

| stage | 5,000 runs | 20,000 runs | share at 20,000 | traced peak MB at 20,000 |
|---|---|---|---|---|
| nodes / edges | 27,642 / 65,200 | 105,642 / 251,200 | | |
| `load_1` SQL read | 0.27 | 1.12 | 5% | 285 |
| `load_2` validate nodes | 0.40 | 2.41 | 12% | 277 |
| `load_3a` parse ref rows | 0.51 | 2.26 | 11% | 440 |
| `load_3b` sort refs (`ref_key`) | 0.61 | 2.04 | 10% | 29 |
| `load_3c` build `ExpEdge` | 0.75 | 1.93 | 9% | 134 |
| `load_4` consistency | 0.06 | 1.53 | 7% | 36 |
| `load_5a` `canonical()` | 1.37 | 5.84 | 29% | 401 |
| `load_5b` SHA-256 | 0.03 | 0.17 | 1% | 134 |
| sum of stages | 4.0 | 17.3 | | |
| `load()` as called | 3.99 | 20.46 | | 1,540 (whole load) |

Ref handling (`3a` to `3c`) is 30% and `canonical()` is 29% of the load at 20,000 runs. Timings at 20,000 runs are
noisy on this machine (the sum of stages and the `load()` call differ by 3 s), so any gate must be measured as
the median of repeated runs.

Constraints: the trace database is the source of truth and the graph is derived (ADR-0015); a load refuses a
foreign file, a newer version, malformed records, dangling edges, duplicates, a differing claim subject, a hash
mismatch and an invalid build time; no new dependency.

## Decision
Two levels, taken in order. Level B is taken only if Level A misses the gate.

1. **Level A: remove repeated work, change nothing stored.**
   - In `load`, parse each distinct `ref_json` text once (a dict from text to the frozen `TraceRef`; sharing frozen
     models is safe) and compute each `ref_key` once.
   - In `canonical()`, dump each distinct reference once per call.
   - The canonical text, and so every content hash, stays byte-identical, and existing graph files keep working.
     This is pinned by golden hashes of the test fixtures before and after.
2. **Gate.** At 20,000 runs the graph load must reach at most 8.5 s (the half of ADR-0026's 17.0 s that the user
   chose), and also at most half of the baseline load measured in the same session on the same machine, because
   the machine differs from ADR-0026's. Median of at least three runs. Peak memory by stage is measured and
   reported; it is not gated.
3. **Level B: graph format 6, hash over the stored rows (only if A misses the gate).**
   - `save` writes each record in canonical form: node JSON as `canonical` would dump it, and `ref_json` exactly
     as `ref_key` renders it.
   - The content hash is SHA-256 over a length-framed stream: the format version, then nodes ordered by id, then
     edges ordered by (source, target, kind) each followed by its refs ordered by `ref_json`, then diagnostics by
     `seq`. `save` computes it from the very strings it inserts; `load` computes it incrementally from the rows in
     that SQL order, without building the whole canonical text.
   - It is still a full hash checked on every load, and it covers every byte that is parsed, so it is not weaker
     than today's: a whitespace-only edit of a stored record, which today still loads, is refused afterwards.
   - SQLite's BINARY collation on UTF-8 must order ids like Python's code-point order; a test with non-ASCII ids
     pins this, and if it fails the ordering is done in Python.
   - A format-5 file is refused with the existing message to rebuild with `experience build`.
4. **Invariants for both levels.** Every refusal listed in Context stays and keeps its message. Results are
   identical to the code at `e19c0c6`, claim for claim: `assess_claims` and `check_sources` output compared for
   equality on the fixtures and on the generated 5,000-run and 20,000-run graphs (reference outputs were saved
   from the current code before any change).
5. **Stop rule.** If Level B also misses the gate, or any step changes a result or weakens a refusal, the change
   is dropped and the decision comes back to the user, with the measured limit documented in the README.

## Alternatives considered
- **Skip or defer the hash on `recall`:** weakens a refusal the project relies on (ADR-0026 point 3). Rejected.
- **Build with `model_construct` and skip validation:** removes the malformed-record refusal. Rejected.
- **A cached pickle of the loaded graph:** unsafe to load, and a second source of truth. Rejected (ADR-0026).
- **Hash the raw rows without a canonical form (Level B without canonical storage):** makes row order and
  whitespace part of the content without defining them. Rejected; Level B stores canonical text instead.
- **Incremental graph updated on every trace write:** couples the trace store to the graph (ADR-0019). Rejected.
- **Subject-scoped loading of only the needed rows:** would need the whole-graph hash to be skipped or split;
  that is a separate decision, not taken here.

## Consequences
- Level A is a local, reversible change with no format change. If it meets the gate, the format stays at 5.
- Level B costs a format bump: users rebuild the graph file (the trace database is untouched), and tests that
  tamper with stored JSON by hand need updating on purpose.
- The whole-graph `assess_claims` (20 s and 1.7 GB traced at 20,000 runs here) and `check_sources` (15 s) are not
  part of this decision; `recall` and `task` already avoid them (ADR-0019). `experience claims|outcome|check`
  still pay them.
- Real-model recall timing at 5,000+ runs is separate (open item 2).

## Outcome (2026-10-19, branch `feature/graph-load-cost`)
Level A was implemented first and measured, then Level B, as decided. Same generator and machine as the baseline
above; median of three `graph_store.load` calls at 20,000 runs (105,642 nodes, 251,200 edges); the old code was
measured the same way in a detached worktree at `e19c0c6`.

| code | load, median (range) | traced peak of one load |
|---|---|---|
| `e19c0c6` (baseline) | 15.89 s (14.27 to 17.40) | 1,539 MB |
| Level A | 10.93 s (10.81 to 13.11) | 1,216 MB |
| Level A + B (format 6) | 7.49 s (7.45 to 7.54) | 814 MB |

At 5,000 runs: 3.99 s (one run) with the old code, 2.76 s with Level A, 1.93 s with Level B.

**Gate:** Level A missed it (10.93 s is above 8.5 s and above half of 15.89 s). Level B met both parts: 7.49 s is
at most 8.5 s and at most 7.95 s (half of the same-session baseline).

**Results unchanged:** the loaded snapshot has the same content as the one loaded by the code at `e19c0c6`
(the old-format content hash of the new snapshot, computed with the old code, equals the hash saved by the old
code), and `check_sources` statuses (105,642) and `assess_claims` output (1,600 claims) are equal to the saved
reference outputs of the old code, at 5,000 and 20,000 runs.

**As built, with two deviations to note:**
- Level B hashes the stored texts with a length-framed stream (`ContentHasher` in `experience/models.py`);
  `save` and `load` feed it the strings they write and read; `canonical()` is removed. Content hashes of every
  graph changed by design (format 6); the fixture hash is pinned in `tests/test_graph_load_cost.py`.
- `load` now refuses an `edge_refs` row that belongs to no stored edge ("malformed record"). Format 5 ignored such
  rows, so this is stricter, not weaker. Stored text that is not ASCII is reported as a hash mismatch, not a crash.
  As decided, `save` writes `ref_json` exactly as `ref_key` renders it and nodes and diagnostics as sorted-key
  ASCII JSON. Detail the ADR text did not give: an edge's (source, target, kind) is hashed as a JSON array, so that
  non-ASCII ids stay ASCII in the stream.

Whole-process stages, measured 2026-10-19 with a scratch script (`measure.py`, session scratchpad, not
committed) that runs `load`, `check_consistent`, `ExperienceGraph` construction, whole-graph `check_sources` and
`assess_claims` in one process and reads the peak working set (Windows `GetProcessMemoryInfo`). The old code ran
from a temporary detached worktree at `e19c0c6` on the format-5 graph of the same runs. One run each, so timings
vary by a second or two (this load took 9.86 s against the 7.49 s median above).

| stage at 20,000 runs | old (`e19c0c6`) | new (format 6) |
|---|---|---|
| `load` | 17.13 s | 9.86 s |
| `check_consistent` | 0.75 s | 0.71 s |
| `ExperienceGraph` construction | 1.24 s | 1.27 s |
| `check_sources` (whole graph) | 15.03 s | 14.79 s |
| `assess_claims` (whole graph) | 19.14 s | 20.32 s |
| peak working set after `load` | 1,738 MB | 916 MB |
| peak working set of the process | 2,709 MB | 2,457 MB |

At 5,000 runs: load 4.08 s to 2.21 s; peak working set after load 469 MB to 275 MB; process peak 779 MB to 696 MB.

**Unchanged, as expected:** `check_consistent`, graph construction, `check_sources` and `assess_claims` are within
run-to-run noise. `check_consistent` is about 0.7 s here (the earlier 1.5 s came from a slower, noisier profiling
run). The process peak falls by only about 9% because it is set by `assess_claims`, which adds about 1.4 GB on top
of the loaded graph and which this ADR did not touch. `experience claims/outcome/check` pay it; `recall` and
`task` do not (ADR-0019).


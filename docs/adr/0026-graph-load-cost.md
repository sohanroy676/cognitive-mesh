# ADR-0026: Cost of loading the experience graph (amends ADR-0019)

## Status
Accepted (text reviewed and accepted by the user 2026-10-18, before any code was written)

## Date
2026-10-18

## Context
ADR-0019 and its v1.2.1 amendment scoped what `recall` assesses to the subjects its hits reach. What is not
reduced: loading the graph file, verifying its content hash, building `ExperienceGraph` (NetworkX), and the
whole-graph checks in `experience claims`, `experience outcome` and `experience check`. At 20,000 synthetic runs
(144,563 nodes, 409,036 edges) recall took about 66 s and 2.4 GB peak; those figures predate the subject index,
and nobody has measured where the remaining time goes (file read, hash, graph construction, source checks, or
model load).

Constraints: the trace database is the source of truth and the graph is derived (ADR-0015); a load refuses a
foreign file, a newer version, malformed records, dangling edges and a hash mismatch; no new dependency.

## Decision
Measure first, then change only what the measurement shows.
1. **Profile before any code.** A scratch tool (not committed) generates the 5,000-run and 20,000-run graphs of
   the verification pass and records wall time and peak memory for: `graph_store.load` split into file read,
   record validation, hash check and `ExperienceGraph` construction; the source check; assessment; and the
   `experience claims|outcome|check` commands. Results and the commands used go to `progress.md`.
2. **Decision gate.** If one stage is at least 60% of the load time, optimise that stage only. Candidate changes,
   each of which must keep every load refusal above and return identical results (compared claim for claim with
   the current code on the fixtures and the generated graphs):
   - build the adjacency from the loaded records on demand instead of constructing a NetworkX graph up front;
   - verify the hash in the same streamed pass that validates the rows, instead of a second pass;
   - give `experience claims|outcome|check` the same subject scoping as `recall` when a subject or claim id is
     given on the command line.
3. **No weaker verification.** The content hash stays full and is checked on every load. A partial or deferred
   verification is not accepted by this ADR; if the measurement shows verification dominates, that comes back to
   the user as a new decision.
4. **Stop rule.** If no stage dominates, or an optimisation would change any result or weaken a refusal, nothing
   is changed and the measured limit is documented in the README.

## Alternatives considered
- **Optimise now without measuring:** the earlier numbers predate the subject index, so the target is unknown.
- **Incremental graph updated on every trace write:** couples the trace store to the graph (rejected in ADR-0019).
- **Cache a pickled graph:** loading pickles is unsafe and adds a second source of truth. Rejected.
- **Skip hash verification on recall:** weakens a refusal the project relies on. Rejected.

## Consequences
- The work is bounded by a measurement and an equality test, so it cannot silently change what recall returns.
- It may end with no code change (stop rule); the result is then a documented, measured limit.
- Real-model indexing cost (about 76 ms per document, extrapolated) is separate and not addressed here.

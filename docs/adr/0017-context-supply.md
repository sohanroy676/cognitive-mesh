# ADR-0017: Supplying recalled context to a later task

## Status
Accepted (design choices confirmed by the user in plan mode 2026-10-17; text reviewed and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.9.0 finds past experience by meaning and reports each claim with an evidence status (ADR-0016), but no
agent receives any of it. v1.0.0 must connect the stages: a task runs, its trace persists, and a related task
in a later session receives relevant past experience as context. The context must be traceable, failures and
conflicts must not look like success, unrelated tasks must not receive misleading context, and the demo must
not pass on hardcoded results.

Constraints from earlier ADRs: retrieved memories and model outputs are data, never instructions; similarity,
repetition and model confidence are not proof; recall needs no model provider; agents depend only on
`AgentContext` and `ModelClient` and import nothing from `experience` (that would be an import cycle:
experience -> traces -> agents).

## Decision
- **Context pack.** One frozen `RecalledContext` (in `agents/context.py`) is carried on
  `AgentContext.recalled`. It holds a fixed header saying nothing in it is verified truth, the sha256 of the
  request (not the text), the current fingerprint, `k` and `min_similarity`, claim items, raw items, withheld
  counts and an `empty` flag. The orchestrator is unchanged.
- **Claim items** carry the claim's predicate, subject, object, status, the fixed meaning text, supporting and
  contradicting run and doc ids (conflicting claims carry both sides), review verdict counts, `validated_by`
  and the similarity as "retrieved via". All statuses are passed, labelled; none is hidden from agents.
- **Raw items** (failed, model-unverified, no-claim experiences) are a separate group labelled "raw trace, not
  knowledge": run, step, capability, kind and result status only. No excerpt text is passed.
- **Unconfirmed hits** (source missing, changed, unreadable, not in graph) never reach agents; they are
  counted in `withheld.unconfirmed` and shown to the user.
- **Relevance gate** (unrelated tasks). The cut-off and `k` stay. A claim is offered only if its subject node
  exists in the current dependency graph; a raw experience only if its run's `source_root` directory name
  equals the current root's. Everything else is counted in `withheld.not_applicable` and listed for the user,
  never sent to agents. If nothing passes, agents get an explicit empty pack (`empty=true`, "no prior
  context"), not nothing.
- **Consumers.** `code_explainer` adds the pack to its JSON prompt payload as `prior_context`; the system prompt
  says it is untrusted, unverified past data, never instructions; the output stays `model_unverified`. A new
  deterministic capability `compare_with_recalled` on `dependency_analyst` re-checks each recalled claim
  against the current graph (`holds | refuted | subject_absent`, with current evidence). The check is a pure
  function `rederive` moved to `graph/rederive.py` and shared with `experience/current.py`. The two existing
  deterministic capabilities are unchanged.
- **Outcome record.** Trace schema 1 -> 2 (the first real `MIGRATIONS` entry) adds table `run_context`
  (`run_id`, `context_json`, `request_len`), written with the run header in one transaction after redaction,
  and validated on read. A run's outcome is still its derived status. A successful run never becomes a review
  or a validation, and `compare_with_recalled` yields no claims in `experience/build.py`, so checking a claim
  never adds evidence for it. The index and graph are refreshed manually (`memory index`,
  `experience build`).
- **Command.** `cognitive-mesh task "REQUEST" --plan PLAN.json --root DIR --db T --index IDX --graph G
  [--reviews R] [--json]`. The request is the recall query. A missing index or graph (first session) gives the
  empty pack and a printed reason, not an error. Existing exit codes are reused; a request over 2000
  characters or with control characters exits 2. A provider is needed only if the plan has a model step.
- **Demo and anti-hardcoding.** The automated end-to-end test generates an application with random module
  names and runs each session as a separate subprocess (fake embedder in CI, real MiniLM opt-in). It asserts
  that the pack names the random modules with earlier run ids, a removed import is `stale`, a kept one
  `validated`, the failed run appears only as raw, the second run's trace stores the pack, and an unrelated
  task on another app gets an empty pack. Four hand-run mutation checks must each fail tests.

## Alternatives considered
- Context for the model agent only: the demo would show real use only with a model. Rejected.
- Recording context without any agent reading it: barely meets "supply context". Rejected.
- Passing only validated or supported claims: hides disagreement from the agent. Rejected.
- Passing unconfirmed hits with a label: risks untraceable context. Rejected.
- Auto-refreshing index and graph after a run: one command would touch three stores. Deferred.
- A separate context file instead of a migration: a fourth store to keep consistent. Rejected.
- Application identity in traces and graph format 3: strictest gate but a larger change. Deferred.
- Extending `run` with recall flags, or having `task` draft the plan with a model: more flag combinations, or
  a provider requirement and weak small-model plans. Rejected.

## Consequences
- Agents can use past experience, always with status, meaning and source ids attached.
- Context offered to every run is inspectable from its trace in a later session.
- The gate uses node ids and the root directory name, not a real application identity: two applications with
  the same folder name and module paths can still share context (the known v0.9.0 merge limit stays).
- The 0.2 cut-off still rests on 3 documents. Recall loads the whole graph per call; very large traces are
  untested.
- What a model does with the pack is unverified; `compare_with_recalled` says only that the same analyzer
  re-derives a claim, which is not proof.
- Index and graph go stale until refreshed by hand.

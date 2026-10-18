# ADR-0024: A deterministic capability that reads declared task state

## Status
Proposed (awaits review and acceptance before any code)

## Date
2026-10-18

## Context
ADR-0023 added declared task state: a later step can take the whole output, or named fields, of an earlier
step as one input. No registered capability accepts a mapping, so the feature is exercised only by test
agents and the roadmap criterion "a stateful task preserves information from an earlier step" is shown with
fakes. A real capability should use it, without a model and without making a check look like evidence.

## Decision
- Add capability `compare_dependencies` to `dependency_analyst` (basis `deterministic`, no model).
  Input: `first` and `second`, each the output of a `dependencies_of` step (an object with `node_id` and a
  `dependencies` list). Output: `first_node`, `second_node`, `shared` (node ids in both), `only_first`,
  `only_second`, each a sorted list capped at `limit` (default 200, max 1000), with `truncated` and the
  full counts. Edge evidence is not copied; the two source steps hold it.
- The inputs are validated: a missing `node_id`, a non-list `dependencies`, an item without a string
  `node_id`, or an input that is not an object fails the step with a fixed message that names the input,
  not its content. A report whose own `truncated` flag is true is accepted but the output says so
  (`inputs_truncated`), because shared/only-X lists would then be incomplete.
- Plans use it through state: two `dependencies_of` steps, `state` entries `a` and `b` with `fields: "*"`,
  and `input_refs` of kind state for `first` and `second`. The planner's existing checks apply.
- The output is not a claim. `experience/build.py` and `experience/current.py` get an explicit decision
  (like `compare_with_recalled`): the capability yields no claims and is not re-derivable. A comparison of
  two earlier outputs never adds evidence for the edges it compares.
- Tests: shared/only-X on the `dep_app` fixture; identical, disjoint and empty inputs; each malformed input;
  truncated inputs flagged; limit and `truncated`; a full plan through the orchestrator with state from two
  steps, with the source recorded; the trace of such a run builds no claims; a failed first step blocks the
  comparison.

## Alternatives considered
- A model-written comparison: output would be `model_unverified` and add nothing a set operation cannot.
  Rejected.
- Comparing two nodes inside one `dependencies_of` call: removes the need for state but not the gap in
  the feature it is meant to demonstrate. Rejected.
- A generic "merge reports" capability: broader and harder to validate. Deferred.

## Consequences
- Declared state is used by a real, model-free capability, so a stateful task can be run and tested end to end.
- A new input shape (a mapping) means the capability's input schema is permissive about extra keys of the
  report; only the fields it reads are validated.
- Comparison is on the current graph or on any two earlier outputs of the same capability; it says nothing
  about whether either output was correct.

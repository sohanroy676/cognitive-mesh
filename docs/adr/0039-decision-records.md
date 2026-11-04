# ADR-0039: Decision records

## Status
Proposed (drafted in plan mode 2026-11-04; the four marked user decisions were taken before drafting; waiting for the user's acceptance, no code until then)

## Date
2026-11-04

## Context
Extended roadmap v1.9.0 asks for a **decision** record: what was decided, the options considered, the reasoning
given, the evidence cited, who decided (person, or model and role) and when. Decisions must join the experience
graph and the vector index, have a status computed like a claim's, and be traceable from a commit, branch or PR.
v1.10.0's conductor will write every plan as a decision, so this version builds the record and its checks. No
conductor exists yet.

Facts from the code (read 2026-11-04):
- Trace schema is 5 (`MIGRATIONS[1..4]`, `traces/store.py`). A migration is appended as plain statements; the store
  wraps it with `user_version`. `open_existing` refuses an older schema (exit 8). `model_calls` rows are keyed by
  `agent_task_id` and joined to attempts by that string only.
- The experience graph file is format 6 (`experience/models.py`). `build_from_traces` reads only the trace store,
  so anything stored there survives a rebuild. Reviews and outcomes are separate append-only files that are never
  in the graph. Older formats are refused with "rebuild it with `experience build`"; there is no migration.
- Claim status is a pure function (`experience/assess.py`): statuses are computed per code version on every call,
  never stored. Outcomes (`experience/outcomes.py`, schema 2) are keyed by `claim:` ids only.
- The vector index has one collection, `experiences`, and no document kind. `MemoryIndex.search` requires
  `run_id`, `step_id` and `attempt_no` metadata, so a foreign document in that collection would break every
  search, and `index_traces` would count it as an orphan. `TEXT_FORMAT_VERSION` is part of the model key, so
  changing it makes every existing index fail with exit 11.
- `source_fingerprint` (`traces/recorder.py`) hashes the whole tree; each ingested file also has its own sha256
  (`SourceRef`, `ingestion/models.py`). No commit or branch is recorded anywhere. Git runs only in
  `workspace/fetch.py` (`run_git`, a fixed argument list, no shell).
- `plan` drafting records nothing (ADR-0038 stated limit).

Constraints unchanged: memory is evidence, not truth; model-written text, including a conductor's reasoning, is
`model_unverified`; no secret, prompt or exception text in any record or message; analysed files are never
executed; default tests need no network.

## Decision

User decisions (2026-11-04):
1. **Storage:** the trace store, schema 6.
2. **Producers:** a person through `decisions record`, plus a library function `record_decision()` that also
   accepts model authorship (used by tests now and by the v1.10.0 conductor). Plan drafting stays unrecorded.
3. **Git links:** at record time the commit and branch of `--root` are read when it is a git repository and
   stored as `observed`; later links to a commit, branch or PR are added by a person and stored as `declared`.
   Nothing is checked against GitHub.
4. **Outcomes:** the outcomes file accepts decision ids, and outcomes feed decision status.

Design:

1. **Record (trace schema 6).** `MIGRATIONS[5]` adds three append-only tables, with `BEFORE UPDATE` and
   `BEFORE DELETE` triggers that abort, as in `reviews.py`:
   - `decisions`: `decision_id` (`decision:` plus 32 hex, the sha256 of the canonical record), `app_id`,
     `fingerprint` (the code version at record time from `source_fingerprint` when `--root` is given, otherwise
     NULL), optional `run_id` and `step_id` (the run it was made in), `made_by` (`person` | `model`), `recorder`
     (person) or `entry_id`, `provider`, `model`, `role` and `agent_task_id` (model; the last joins `model_calls`),
     `basis` (`person` | `model_unverified`; model authorship forces `model_unverified`), `summary`,
     `options_json`, `chosen`, `reasoning`, `supersedes` (one earlier decision or NULL), `created_at`,
     `redactions_json`.
   - `decision_citations (decision_id, n, kind, target, path, sha256)`: kind `node` (an experience-graph id such
     as a claim, target or run), `file` (a path under the root) or `decision`. A file citation takes its sha256
     from the ingested file under `--root` at record time; a person never types a hash, and a file that cannot be
     found under the root is refused.
   - `decision_links (id, decision_id, kind, value, how, recorder, created_at)`: kind `commit` | `branch` | `pr`,
     how `observed` | `declared`.
   - **Validation.** Every text goes through `redact_secrets` and is refused if empty, over-long or holding
     control characters. Caps: summary 300, at most 10 options of 200 each, chosen 200, reasoning 4,000, at most
     50 citations, link value 300. A cited or superseded decision must already exist and belong to the same
     `app_id`, so a decision cannot cite itself, a later one, or another application's. File paths are relative,
     POSIX, with no `..`. Messages never echo given text. `TraceStore.add_decision` writes one decision and its
     citations in one transaction. The v1.10.0 conductor will write its decisions in the run's finishing
     transaction through the same validation.
   - **Reading.** Rows are validated on read and a malformed row raises or is reported with its id, never
     skipped (the ADR-0011 rule). Databases at schema 5 are upgraded by opening for writing (`run`, `task`,
     `decisions record`), as for schema 5 before.
2. **Graph format 7.** New node kinds `decision` and `file`; new edge kinds `decided_in` (decision to run),
   `cites` (decision to node, file or decision) and `supersedes`. `build_from_traces` reads decisions from the
   same trace store, so a rebuild never loses a citation. A `cites` edge to a file carries the cited sha256; every
   decision edge carries a reference to its trace row so `check_sources` can report `source_missing`,
   `source_changed` or `source_unreadable` for it as for attempts. The hash rules of ADR-0027 are unchanged (the
   `F` frame carries the new version), and older graph files are refused with the rebuild message.
3. **Status** (new pure module `experience/decision_assess.py`, no I/O, no clock, no model). Inputs: the decision,
   its citations resolved against the graph, the current file hashes under `--root` (when given), the assessed
   status of cited claims and decisions, and the outcomes for the current code version. The first matching rule
   wins:
   1. `conflicting`: outcomes disagree, or two decisions supersede the same earlier decision (both sides are
      listed);
   2. `contested`: a failed outcome for the current code version;
   3. `superseded`: a later decision supersedes it (the successor is named);
   4. `unsupported`: it cites nothing, or a cited node, file or decision is gone;
   5. `stale`: a cited file changed, or a cited claim or decision is stale, conflicting, contested, expired or
      environment_changed;
   6. `validated`: a succeeded outcome (person or check) for the current code version;
   7. `unknown`: an ambiguous outcome;
   8. `supported` otherwise.
   Without `--root`, file citations are `unknown`, never `ok`, and the status says so. Every status lists its
   reasons. Reasoning text, repetition, similarity and the success of the run a decision was made in never count
   as evidence. A model-written decision is `model_unverified` and can reach `validated` only through a recorded
   outcome.
4. **Outcomes file schema 3.** The subject of an outcome may be a `decision:` id. Source `promotion` stays
   claim-only. A schema 2 file is upgraded on write and an older program refuses a schema 3 file.
   `experience outcome` accepts either id kind.
5. **Index.** A separate Chroma collection `decisions` in the same `--index` directory, with its own text format
   version and the same compatibility guard. The `experiences` collection and `TEXT_FORMAT_VERSION` are untouched,
   so existing indexes keep working. A decision document holds the summary, options, chosen option and reasoning
   (cut at 8,000 characters like attempt documents), its id is the decision id, its metadata carries the text
   sha256, and a hit is re-verified against the trace row. `memory index` adds decisions; an index without the
   collection makes `recall` say that decisions are not indexed, not fail.
6. **Recall.** `RecallResult` gains a `decisions` group: each entry shows its status and reasons, and how it was
   found (by meaning, or because it cites a recalled claim or attempt). Reasoning text is shown labelled
   `model_unverified` when model-written. Decisions are **not** added to the `task` context pack in v1.9.0; what
   the conductor sees is decided in the v1.10.0 ADR (stated limit).
7. **CLI** (`decisions_cli.py`, in the style of `models_cli.py`):
   - `decisions record --db FILE --summary T --option T ... --chosen T --reasoning T [--cite-node ID]
     [--cite-file PATH] [--cite-decision ID] [--supersedes ID] [--run ID --step ID] [--root DIR] [--app-id ID]
     --by NAME` (or `--from-json FILE`). With `--root`, the fingerprint, file hashes and the observed commit and
     branch are read.
   - `decisions link ID --db FILE (--commit SHA | --branch NAME | --pr REF) --by NAME`.
   - `decisions list|show --db FILE [--graph FILE --reviews --outcomes --root] [--json]`.
   - `decisions trace (ID | --commit SHA | --branch NAME | --pr REF) --db FILE --graph FILE [...]`: follows
     `supersedes` and `cites` back to the evidence, with statuses and outcomes, in one command, and lists the
     links as observed or declared.
   - New exit code `EXIT_DECISION_ERROR = 21` for an invalid record or an unusable decisions table; missing
     database stays 8, unknown id 13.
   - The observed commit and branch use `workspace/fetch.py::run_git` with a fixed argument list. A folder that
     is not a git repository, or a failing git, records no observed link and says so. An observed commit says
     nothing about uncommitted changes; the fingerprint, not the commit, is the evidence about code.
8. **Not in v1.9.0:** a UI view, the context pack, recording of plan drafts, checking a PR or branch against
   GitHub, deleting or editing a decision (a correction is a new decision that supersedes).

## Alternatives considered
- **A separate decisions file (`--decisions FILE`):** matches reviews and outcomes, but `experience build` would
  need a second input, every command gains a flag, and conductor decisions could not share a transaction with the
  run that made them.
- **One collection with a `kind` field:** breaks `MemoryIndex.search` and `index_traces` for existing code paths
  and would need a `TEXT_FORMAT_VERSION` bump that fails every existing index.
- **Treating reasoning as evidence when it cites real files:** rejected; a citation is checked against the
  current files, the sentence around it is not.
- **Recording plan drafts now:** plan drafting has no run; it needs a run kind of its own. Deferred.
- **Verifying PR links through GitHub:** needs credentials and a network rule (v1.12.0).

## Consequences
- Every decision can be traced to its evidence and outcome in one command, and a rebuild keeps all citations.
- Graph format 7 and trace schema 6 mean one rebuild of the graph and one write-open of old trace databases.
- Decisions are append-only; wrong ones are superseded, never removed.
- `declared` links are only as true as the person who wrote them; `observed` links describe the repository when
  the decision was recorded.
- Limits stated up front: no UI, no context pack, no plan-draft records; file citations are by content hash, so a
  moved file reads as `unsupported`; node citations to claims depend on the graph being rebuilt.

## Tests
File cited then edited reads `stale`; file deleted or cited node gone after a rebuild reads `unsupported`; no
citations reads `unsupported`; supersession chain, and a fork shown `conflicting` on both sides; a decision citing
itself, an unknown or another application's decision refused; a model-written decision recorded five times never
`validated`, and a successful run never counts; outcomes succeeded, failed, ambiguous and in conflict (person and
check); secret in reasoning redacted; oversize and control-character text refused with no echo; append-only
triggers; migration 5 to 6 on an old database; graph format 6 refused; rebuild keeps citations; an old index
without the decisions collection still searches; persistence and recall across processes; `decisions trace
--commit` against a fixture git repository and a non-git folder; exit 21 paths; a model decision joins its
`model_calls` row; opt-in live check with the real embedding model (`RUN_LIVE_EMBEDDINGS=1`).

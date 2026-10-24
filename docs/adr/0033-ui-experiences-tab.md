# ADR-0033: An Experiences tab that lists every recorded step; the Recall tab is removed (amends ADR-0031 and ADR-0032)

## Status
Accepted (text reviewed and accepted by the user 2026-10-24, before any code was written)

## Date
2026-10-24

## Context
The user asked to remove the Recall tab and replace it with an Experiences tab that shows every experience in the
database at once. Asked what one row should be, the user chose **every recorded step**.

Facts about the code base (inspected 2026-10-24):
- The code already has a definition: `MemoryDocument` is "one eligible experience: the final attempt of one step of a
  finished run" (ADR-0014). `memory.documents.eligible_documents(trace)` returns them for one run and returns none for
  an interrupted run. Steps that were blocked or not run have no attempt, so they are not experiences.
- Each experience has a run id, step id, attempt number, capability, final result (`success` or `failure`) and, for a
  success, a basis (`deterministic` or `model_unverified`). Its text can hold file content or model output, which is
  untrusted data (ADR-0017 offers failed and model-written steps to agents as metadata only).
- The experience graph (ADR-0015) has one attempt node per experience, and claim nodes point at attempts with
  `evidenced_by`. It is derived from the trace database and can be out of date.
- ADR-0031 lists `/api/recall` among the endpoints and the UI had a Recall tab (a vector search that loads the embedding
  model, about 13 s and the heaviest memory use in the UI). ADR-0032 removed the Search tab and kept its API.
- `TraceStore.list()` plus `get(run_id)` reads every run once; `memory index` already does this. At 20,000 synthetic
  runs there are about 26,000 experiences.

## Decision
1. **Remove the Recall tab from the UI.** `/api/recall`, its tests and the `recall` command stay, as for Search in
   ADR-0032. The UI no longer loads the embedding model at all, so `--index` has no effect on the UI and the
   "How to read this page" key is shown on Claims only. `--index` stays accepted so existing commands keep working.
2. **Add `GET /api/experiences`** (needs `--db`; `--graph` is optional). It reads the trace database directly, not the
   graph, so the list is always what is recorded now. For every finished run it takes `eligible_documents` and returns,
   newest run first, one row per experience: run id, run start time and status, step id, attempt number, capability,
   final result, basis, code version, and the claims that point at it.
   - **Claims column:** when `--graph` is given, claims come from the graph's `evidenced_by` edges to that attempt
     (id and text). Without `--graph` the column is absent and the page says why.
   - **Consistency, not a full source check:** the response says how many experiences in the database are not in the
     graph yet (the graph is out of date, rebuild it with `experience build`), and how many graph attempts no longer
     exist in the database. A full source check of every attempt is the whole-graph cost ADR-0028 and ADR-0030 measured
     (about 11 s at 20,000 runs), so this page does not run it; the Claims and Runs pages still do for what they show.
   - **No text:** a row carries metadata only, never the step's output or text. Output stays on the run page, which
     already shows it as untrusted data.
   - **Malformed runs** are counted and listed by id, as `traces list` does, never hidden.
3. **Everything at once, without freezing the page.** The server returns up to 10,000 experiences, newest first. If
   there are more it says how many exist and how many are returned; the ceiling is a starting point to be checked.
   The page holds the whole list and draws it in chunks of 200 with a visible "Show 200 more" and "Show all" button, so
   the list can be read in full without building tens of thousands of elements on first paint.
4. **Reading aids.** A summary above the list counts experiences by capability, by result and by basis. The list can be
   filtered by capability, by result, by basis and by claims (with or without), and by text in the run, step or claim
   id. A failed or model-written experience is labelled as a raw record, not validated knowledge (ADR-0017). Each row
   links to its run (Runs tab), and each claim name links to its history (`Runs?for=`). Ids use the pin helper of
   ADR-0032, so a pinned id is marked.
5. **Plain wording.** The page opens with two sentences saying what an experience is (one step of a past run, as
   recorded) and what it is not (a fact), and every label is a sentence fragment a new user can read, as on Claims.
6. **Tests and checks.** Tests: the list equals `eligible_documents` over every run of a fixture database (count and
   ids); newest first; failed and model-written rows are present and labelled; blocked and not-run steps are absent;
   interrupted runs contribute nothing; claims match the graph, are absent without `--graph`, and a stale graph is
   reported with counts; malformed runs are counted; the ceiling truncates visibly; no row contains output text; hostile
   text in a step id or capability comes back as inert text; the files are byte-identical afterwards; a missing
   `--db` says so. The static scan still forbids markup parsing and style assignment. The page is checked in headless
   Edge with real input: all rows reachable, chunking, filters, links, pinned marks.

Out of scope: searching by meaning (the CLI keeps `recall` and `memory search`), showing step output, editing or
deleting anything, a per-step source check, and paging on the server.

## Alternatives considered
- **Experiences from the graph file.** Cheaper and it carries claims, but it can be out of date, so the list would not
  be "all experiences in the database"; the chosen design reads the database and uses the graph only for claims.
- **Claims and steps together, or one row per run.** Offered to the user and not chosen; they repeat the Claims and Runs
  tabs.
- **A full source check of every row.** Whole-graph cost (ADR-0028, ADR-0030) for a list page; replaced by the cheap
  consistency counts.
- **Server-side paging.** Needs state and breaks "everything at once"; chunked drawing keeps the full list in the page.
- **Keep Recall as a second tab.** The user asked to remove it; its heavy model load is also the reason the UI was
  stopped for memory pressure earlier in this session.

## Consequences
- Easier: seeing everything the system has recorded, including failed and model-written steps, in one list; the UI no
  longer loads the embedding model, so it starts lighter and needs `--index` for nothing.
- Harder: nobody can search past experience by meaning in the browser any more; they use the CLI (`recall`,
  `memory search`). The Experiences page repeats part of what Runs shows, from the step's side.
- Cost: reading every run is linear in the number of runs (the same read `memory index` does); the cost at 20,000
  runs is unmeasured for this page and is checked in the verification step, along with the 10,000-row ceiling.
- Limits: a graph that is out of date shows an empty or partial Claims column with an explicit count, not wrong
  claims; rows have no output text, so judging one needs a click through to the run.
- Nothing in this ADR is implemented. The README and the verified-commands list must not describe the tab as existing
  until the tests and the manual checks in point 6 pass.

## Outcome of the implementation (2026-10-25, Windows 11, headless Edge driven over the DevTools protocol)
Built as written, with these details and findings:
- **`TraceStore.iter_runs()`** (one pass, newest first, an unreadable run as `(id, None)`) was added and `list()` now uses it, so
  the page does not read every run twice. `/api/experiences` uses it; `runs` (runs with at least one step) was added to the
  payload.
- **Buttons above and below the list:** the first browser run showed that "Show all" at the bottom of a 200-row table is easy to
  miss, so the same buttons are also above the table. A compact time is shown (full time on hover) and the short columns do not
  wrap.
- **Measured** (synthetic databases, one fresh process each, no graph): 300 runs (600 steps) 0.2 s; 5,000 runs (10,000
  steps) 3.6 s, payload 2.8 MB, traced peak 10 MB; 20,000 runs (40,000 steps, 10,000 listed) 14.3 s, payload 2.8 MB, peak 15 MB.
  Time is linear in runs; the payload is capped by the ceiling. The 10,000-row ceiling is kept.
- **Checked in a real browser (30 checks, all passing):** the Recall tab is gone; Overview's "See every recorded step"; first
  200 of 600 drawn; Show 200 more; Show all; raw-record marks (103 expected, 103 found); filters by result, basis,
  capability, claims and text; a no-match message; run links; claims column and claim links to their history; a stray-text
  check on eight pages.
- **Found while testing:** the page showed the word "null" (for example "from 300 runs.null") because the browser's own
  `append(null)` writes text, unlike the page's `h()` helper. It affected earlier tabs wherever an optional piece was appended.
  All appends now go through `put()` and `reput()`, and a test forbids the unsafe form.
- **Not checked:** the page on a database with a graph that is out of date (covered by API tests, not drawn in the browser);
  50,000-row lists drawn with "Show all" (the ceiling stops it at 10,000); touch, screen readers, light mode, Firefox, Safari,
  macOS, Linux.

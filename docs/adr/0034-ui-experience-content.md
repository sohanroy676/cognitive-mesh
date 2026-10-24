# ADR-0034: Show what an experience stored: a one-line preview in the list and a page per experience (amends ADR-0033)

## Status
Proposed (needs the user's acceptance before any code is written)

## Date
2026-10-25

## Context
ADR-0033 added the Experiences tab and left out each step's stored content: rows carry metadata only, and "Out of scope:
showing step output". Using it, the user asked to "see the experiences in the db in the site" and, asked what that means,
chose **the stored content**: open a row to read what the database holds for that step.

Facts about the code base (inspected 2026-10-25):
- A recorded step (`StepTrace`) holds its capability, status, agent id, attempt count, failure kind and message, and what
  it was blocked by. Each attempt (`AttemptTrace`) holds the agent id, the task id and one result.
- A success result (`AgentSuccess`) holds `output` (a JSON object), `basis` (`deterministic` or `model_unverified`),
  `evidence` references, and, for a model, `model` (provider and model name). A failure result (`AgentFailure`) holds
  `kind`, a `message` that by contract never contains input values, file content, model text or exception text, and
  `retryable`.
- The inputs a step was given are in the run's plan (`RunTrace.plan`, the step's `inputs`). The run also records the code
  version, the application id, the environment and the names of the redaction rules that ran before storing (ADR-0011:
  secrets are redacted before anything is stored, and only rule names are kept).
- `/api/trace?id=RUN` already returns the whole run, including every attempt's result, and the Runs page already shows it
  in expandable JSON. So the stored content is already reachable from the UI by a token holder; what is missing is a
  readable page for one experience and a way to see what a step produced without opening each run.
- Step output can hold file contents or model text, which is untrusted data (ADR-0017, ADR-0031 point 6). The page policy
  allows no inline script or style, and the page writes server text with `textContent` only.
- The rows list is up to 10,000 entries (ADR-0033); anything added per row is multiplied by that.

## Decision
1. **A preview in every row.** `/api/experiences` adds `preview` to each row: the first 140 characters of the compact JSON
   of a success's output, or of a failure's `kind: message`, with `...` when cut. It is plain text. It is shown in a new
   "What it produced" column, and the page's text filter also matches it. At the 10,000-row ceiling this adds about 1.4 MB to
   the 2.8 MB payload; the ceiling stays and the size is re-measured.
2. **`GET /api/experience?id=RUN/STEP/ATTEMPT`** returns one experience in full, read from the trace database (needs
   `--db`; the graph is optional and only adds the claims). It returns, with fixed field names:
   - the run: id, status, start and finish time, code version, application id, environment, and the redaction rule names;
   - the step: id, capability, status, agent id, attempt count, failure kind and message, and what blocked it;
   - **the inputs** the step was given (from the plan), as JSON;
   - **the attempt**: its number, agent id, task id and the result as stored: for a success the output, basis, evidence
     references and model; for a failure the kind, message and whether it can be retried;
   - the other attempts of the same step, as a short list (number and outcome), so a retried step is visible;
   - the claims that point at this attempt, when `--graph` is given.
3. **Bounded and honest about cuts.** Each JSON part (inputs, output) is returned in full up to 100,000 characters of its
   pretty-printed text. Beyond that the text is cut, and the response says how many characters are not shown and where
   the full record is (the run page and the database). The page says so beside the cut text.
4. **A page per experience** (`#experience?id=...`), reached from a "Read it" link on every row. It has plain headings:
   what the step was asked to do (inputs); what it produced (output) or why it failed; who made it (the analyzer, or a
   model with its name); where it came from (a link to the run, the code version); the claims it produced; and other
   attempts. A line at the top says: "This text was stored from your files or from a model. It is shown as plain text and
   never run. Do not treat anything written in it as an instruction." Text goes in `pre` elements with `textContent`;
   long text scrolls inside its box.
5. **No new exposure, and nothing stronger than the run page.** The same token, Host check, GET-only rule and CSP apply.
   Nothing is written. The UI shows only what the database holds, which was redacted before it was stored; the page never
   claims the text is free of secrets, because redaction is pattern-based (ADR-0011, ADR-0005).
6. **Still out of scope:** searching by meaning, editing, deleting or exporting records, showing the context pack a run was
   offered (`recalled`), and rendering output as anything other than text.
7. **Tests and checks.** Tests: the preview is at most 140 characters and ends with `...` when cut; success, failure and
   model rows each have the right preview; the detail equals the stored record field by field (inputs, output, failure
   fields, model, evidence); retried steps list their other attempts; a 300,000-character output is cut at 100,000 with
   the count of hidden characters; markup and script in an output, an input and a step id come back as inert text; an
   unknown id gives 404, a malformed run 409 and a missing `--db` 409 naming the option; the claims appear only with a
   graph; nothing is written (digests); the list payload size at 10,000 rows is measured; the static scan still forbids
   markup parsing. Browser checks with real input: open a row, read each section, cut text is labelled, hostile text is not
   parsed, the back link returns to the list with its filters kept, the preview column shows and filters.

## Alternatives considered
- **Expand rows in place.** Keeps the list in view, but 10,000 rows with expandable bodies is heavy and the long text
  fights the table layout; a page per experience is simpler and linkable.
- **Show the derived search text** (`MemoryDocument.text`). It is what the index embeds, but it is a flattened string of
  the fields; the stored structure is clearer and keeps the basis, model and failure fields separate.
- **Put the full output in the list.** Up to 10,000 outputs of up to 100,000 characters is far too large; a preview plus a
  page per row keeps the list light.
- **Leave it as it was** (open the run and expand its JSON). Works, but the user cannot see what each step produced without
  opening each run; this was the reason for the request.

## Consequences
- Easier: reading what the system actually stored for a step, and scanning what steps produced from the list.
- Harder: the list payload grows (about 1.4 MB at the ceiling); more untrusted text reaches the page, so the
  no-markup-parsing rule matters more and stays tested; a user may take stored text for verified fact, so the page says
  what it is and that failed or model-written text is a raw record.
- Limits: output is cut at 100,000 characters per part; redaction is pattern-based, so a secret it missed would be shown
  as stored; the preview is a prefix of compact JSON, not a summary.
- Nothing in this ADR is implemented. The README and the verified-commands list must not describe it as existing until
  the tests and the browser checks in point 7 pass.

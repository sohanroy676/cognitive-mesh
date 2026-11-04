# ADR-0044: Local web application: chat, workspace graph and data browser (v2.0.0)

## Status
Accepted by the user 2026-11-04 (drafted in plan mode; amended during implementation, see the end). User answers: the data browser is read-only; v2.0.0 handles small changes and questions only; three ports; the 2-D embedding projection is included as an addition; `--single-port` is not requested. Implementation in progress on `feature/extended-roadmap-v1.7`. Replaces ADR-0031's read-only, GET-only rule for the chat server only. Extends ADR-0036/0037 (workspace), ADR-0040 (conductor), ADR-0041 (impact), ADR-0042 (change pipeline), ADR-0043 (evaluation) and ADR-0039 (decisions). ADR-0005, ADR-0011, ADR-0013, ADR-0017, ADR-0018 and ADR-0031's token, Host, CSP and "library text only via textContent" rules stay in force.

## Date
2026-11-04

## Context
Extended roadmap v2.0.0 plus the user's session brief (2026-11-04):
- A web application with a **chat** page. It first asks which projects belong together and notes their dependencies in a dependency graph.
- The dependency graph is **exposed on its own port** so the person can look at it directly.
- Each prompt gets **dynamically allocated agents**. If a change depends on other projects, they are read, a change that does not affect their dependencies is chosen and implemented, and the **experiences and decisions with their reasoning are recorded and traceable**, to show agent performance over time.
- Experiences, decisions and model records live in the vector database, which is **exposed on another port** with a UI that shows the **entire database** and lets the person interact with it.
- The README is updated before hand-off.

Facts from the code (read 2026-11-04):
- `ui/server.py` is a stdlib `ThreadingHTTPServer` on 127.0.0.1: Host check, per-start token (query once, then an `HttpOnly; SameSite=Strict` cookie `cogmesh_token`, or `X-Cogmesh-Token`), CSP, fixed static allowlist, every non-GET method answers 405. The API is `UiApi.routes()` (GET handlers returning `(status, payload)`).
- `workspace/` already builds `WorkspaceGraph` (package, API, schema and config edges with evidence on both sides, declared links) and `workspace add|list|link|deps|impact|contract-diff`. `change/pipeline.make_change` drafts, judges, applies in a worktree, runs the impact check, runs the declared check and records append-only events; `change approve` and `change pr` already gate the push on a person.
- `memory/index.py::MemoryIndex` wraps two Chroma collections (trace steps and decisions). `traces/store.py` holds runs, steps, attempts, `model_calls`, decisions, change events (schema 8).
- Cookies are scoped by host, not by port: two servers on 127.0.0.1 would overwrite each other's cookie if they used the same name.

## Decision

Items marked **(user)** are open questions the user must settle before acceptance; the text gives the proposed default.

0. **Frame.** `cognitive-mesh serve` starts three listeners in one process, all on 127.0.0.1 (no `--host`):
   - **App** (default 8770): chat and approvals. The only server that accepts writes.
   - **Graph** (default 8771): workspace dependency graph. Read-only, GET only.
   - **Data** (default 8772): database browser for the vector index and the SQLite stores. Read-only, GET only.
   One token per start is shared by all three (printed once as three URLs). Each server uses its own cookie name (`cogmesh_app`, `cogmesh_graph`, `cogmesh_data`) because cookies ignore ports. A page calls only its own port's API (no CORS, no cross-origin fetch); the pages link to each other with `?token=`. The graph and data pages reuse `ui/static` code (`graph.js`, the Experiences list, `explain.js`) where possible. New package `src/cognitive_mesh/serve/` (`config.py`, `app_server.py`, `chat.py`, `actions.py`, `events.py`, `data_api.py`, `cli.py`) next to `ui/`; the existing `cognitive-mesh ui` stays unchanged. New exit code **26 `EXIT_SERVE_ERROR`**. No new dependency; the front end is plain JavaScript without a build step.

1. **Writes (App server only).** The 405 rule is replaced for this server by an explicit action list; every other method or path stays 405/404. Actions, all `POST` with `Content-Type: application/json`, a body of at most 64 KB:
   `projects.add`, `projects.link` (declared edge), `projects.confirm` (accept a detected edge list), `chat.send`, `change.revise`, `change.approve`, `change.reject`, `change.pr`, `review.add`, `outcome.add`.
   Each request must pass, in this order: Host check; `Origin` header present and equal to the server's own origin (a request without Origin is refused); the token in `X-Cogmesh-Token` (the cookie alone is **not** enough for a POST, so a cross-site form cannot ride it); JSON content type; a pydantic schema per action with unknown fields refused.

2. **Approvals.** Nothing that changes a project, pushes or records a verdict happens from `chat.send`. When the pipeline reaches a decision point the server creates an **approval request** (random 128-bit id, the action, the change id, the sha256 of the diff shown, an expiry). `change.approve`/`reject`/`pr` must quote that id and that hash; the hash must still match the stored diff; an id works once (a used, expired or unknown id is refused with a fixed message, a replay is a refusal not a no-op). Requests and their outcomes are append-only rows in the chat file. `change.pr` additionally needs `GITHUB_TOKEN` and `COGMESH_ALLOW_REMOTE_CONTENT=true` as in ADR-0042; the server never reads or returns the token.

3. **Chat store.** A separate SQLite file (`--chat FILE`, own `application_id`, own schema version 1; no trace schema bump): `sessions`, `turns` (session, role, text, created), `approvals`, and append-only `turn_events` (turn, seq, kind, small JSON, time). A turn that never finished reads back `interrupted` after a restart (derived, as in ADR-0011). User and model text is stored after `redact_secrets` with long lines cut first (session-12 fact).

4. **Chat flow (App).**
   a. *Onboarding:* the page asks for projects (a local folder or a GitHub URL) → `projects.add` (the ADR-0036 path: clone cache, read-only for dependents) → the page shows the detected edges with their evidence on both sides and the unresolved items, and asks the person to confirm, add declared links, or remove → `projects.confirm`. The graph server shows the same workspace file.
   b. *A prompt:* `chat.send` names the **current project** (the one being changed). The conductor (ADR-0040) drafts the plan; the page shows each step's role, assigned catalog model and the recorded reason, then live step events.
   c. *Impact:* before any diff is applied the run reads the dependents of the current project through the read-only `WorkspaceAccess` (ADR-0041) and the contract diff decides. A draft that breaks a used contract is refused and the refusal is fed back to the coder, at most two revisions (ADR-0042). The page shows the impact report with the `breaking | compatible | unknown | unused` groups; `unknown` is never shown as a pass.
   d. *Diff and approval:* the page shows the diff as text, the check status and its isolation label, and offers Approve, Revise (with a note, counts as a revision) and Reject. After approval, `change.pr` pushes a branch and opens the PR; the turn ends with a link.
   e. *Record:* every turn writes its decisions (the conductor's plan, per-step assignments, the coder's choice with reasoning as `model_unverified`), model calls, change events and the person's verdict into the existing stores, so **trace view** links from a turn to its decisions, evidence, outcomes and (for the dependents) the files that were read. A passed check is the only automatic `check` outcome (ADR-0018, ADR-0042).
   f. *Improvement:* the chat has a "memory" switch (on by default) that passes the gated pack and past changes to the coder (ADR-0043); `eval report` output for the same stores is linked from the data server. The chat claims no improvement itself; it shows counts and the sample line (ADR-0043).
   g. *Scope of a prompt:* **(user)** proposed: v2.0.0 handles prompts that ask for a small change (the ADR-0042 limits: at most 3 files and 100 changed lines) and prompts that ask a question (answered by `code_explainer` / `reader`, no write). Anything larger is declined with the reason shown.

5. **Graph server.** `GET /api/workspace` (projects, detected and declared edges, unresolved, evidence), `/api/workspace/graph` (nodes and layout for `graph.js`, projects as clusters), `/api/workspace/impact?project=&target=`. The page is the ADR-0032 viewer pointed at the workspace file: pan, zoom, hover, click for evidence, filter by edge kind and by declared versus detected. It reads the workspace file on every request, so edges the chat adds appear after a reload; it never writes.

6. **Data server.** Read-only, with two sources:
   - **Vector index (Chroma):** `GET /api/index/collections` (names, counts, embedding model and revision), `/api/index/items?collection=&offset=&limit=` (page of id, document text cut at 2,000 characters, metadata; limit at most 200, so the **whole collection** can be paged through), `/api/index/item?id=` (full text up to 100,000 characters with the cut count), `/api/index/search?q=&collection=&k=` (by meaning, one serialised embedder per process as in ADR-0031; the cut-off and similarity shown as "retrieved via" only), and `/api/index/neighbours?id=&k=` (items near an item). Each item links to its source row.
   - **SQLite stores (traces, decisions, model calls, change events, outcomes, reviews, chat, workspace):** `/api/db/tables`, `/api/db/rows?table=&offset=&limit=&q=` over an allowlist of table names, columns from the schema, values as text, secrets already redacted at write time. No SQL from the client.
   The page offers table and collection pickers, a paged grid with sort, filter by column, free-text and semantic search, a record panel, links between records (decision ↔ model call ↔ run ↔ change ↔ outcome), and an optional 2-D projection of the embedding space **(user)** computed server-side on demand for at most 5,000 points with a fixed, stated method. "Interact" means browse, search, filter and follow links; **(user)** proposed: no edit or delete of database rows from the browser (an append-only evidence store is not edited; the only writes are the App server's approved actions).

7. **Safety rules for text.** Every stored or model-written string reaches the DOM through `textContent` or an equivalent that cannot create elements; diffs are shown in a `<pre>` as text; no `innerHTML` with data, no markdown rendering of model text; CSP unchanged (`script-src 'self'`, `connect-src 'self'`, `frame-src 'none'` for the app page). Responses carry `Cache-Control: no-store`. The App server sends `X-Frame-Options: DENY`/`frame-ancestors 'none'`.

8. **Concurrency and restart.** One task runs at a time per server (a second `chat.send` while a turn is running is refused with the turn id); the task runs on a worker thread and writes `turn_events`; progress is read by the page through `GET /api/turn?id=&after=` (polling, no websocket, no new dependency). A server killed mid-task leaves a turn that reads back `interrupted`; an approval request of such a turn expires.

9. **Models and offline operation.** Tests and the default demo use a fake OpenAI-compatible model and the fake embedder. A live free-tier model is opt-in (`RUN_LIVE_CHAT=1` plus the catalog and key as in ADR-0038); using it needs `COGMESH_ALLOW_REMOTE_CONTENT=true` and the training-use opt-in. The server refuses to start a turn with a remote entry otherwise, and says which variable is missing.

10. **README and docs.** The README gains a "Run the web demo" section with only commands that were actually run, three-port table, and a "What this does not do" list. `docs/STATUS.md`, `docs/COMMANDS.md`, `memory-bank/*` are updated at the end of the version as usual.

## Tests planned
Token, Host, Origin and content-type checks on every POST (missing, wrong, cross-origin); cookie alone refused for POST; per-port cookie names; schema refusal of unknown fields and oversized bodies; approval hash mismatch, expiry, replay, unknown id; no write without an approval row; a full flow on the fixture workspace with a fake model (onboard, prompt, plan, impact refusal then compliant draft, approve, PR path with a fake GitHub transport); server restart mid-turn reads `interrupted`; model text containing `<script>` and markup shown inert (browser test with Node when available, as in the UI tests); data server pages through a 5,000-item index and a 20,000-row table, refuses unknown tables and columns, never returns a secret, cuts long text visibly; graph server shows declared and detected edges apart; read-only servers answer 405 to POST.

## Alternatives considered
- **One port with three tabs:** simpler, but the brief asks for the graph and the database on their own ports so each can be demoed or embedded separately. Kept as a flag (`--single-port`) only if the user wants it **(user)**.
- **Reuse `cognitive-mesh ui` for the data views and add a second server only for chat:** less new code, but the existing UI is built around the trace/graph/claims files, not the Chroma collections or a table browser, and the brief wants a distinct demo UI. The new data pages reuse its code, not its server.
- **WebSocket or SSE for progress:** nicer, but adds either a dependency or hand-written protocol code; polling a monotonic `after` cursor is enough for one task at a time.
- **A shared cookie for all ports:** cookies ignore ports, so one server could read or clobber the other's session. Distinct names plus the `X-Cogmesh-Token` requirement on writes avoid it.
- **Editable database browser (update/delete rows):** matches "interact" in the widest sense, but the traces, decisions and outcomes are evidence records (append-only by design, ADR-0011/0018). Not proposed.
- **Let the model decide when to push:** rejected; the person approves each push.

## Consequences
- ADR-0031's "the UI never writes" no longer holds for the App server; it holds for the Graph and Data servers and for `cognitive-mesh ui`.
- A person with the token can make the app clone repositories and, with a `GITHUB_TOKEN`, push branches after approving; the token is the credential, so it is never put in a log or a URL after the first redirect.
- The Data server exposes every stored model text and decision to anyone who holds the token; redaction at write time is the only protection.
- Three listeners in one process share one embedder and one chat worker; a model load (about 13 s on this machine, ADR-0030) happens on the first semantic search.
- The front end is hand-written JavaScript: no build step, no framework, so a larger UI will cost more code than with a framework.
- Out of scope: remote access, accounts, multi-user use, websockets, editing evidence records, the VS Code extension (v2.1.0), large-scale benchmarks (Phase F).

## Amendments made during implementation (2026-11-04)
Found by running the code; each changes a decision above.
1. **The conductor cannot plan a write.** `plan_task` refuses a plan with a step that writes ("only `change propose` may run such a step", ADR-0042), and the first flow test showed it. So decision 4b/4c changed: the conductor plans **read-only analysis steps** (explain a file, list dependents, a `reader` summary of other projects) and assigns a catalog model to each; that plan runs, and its conductor decisions, model calls and outputs are recorded as in `task --conduct`. **Code**, not a model, then picks the coder: the first usable catalog entry with the `coder` role, and the files the plan read are the hint. The chat shows this as a `coder` line with its reason.
2. **A visible Change / Ask switch** decides whether a write follows the analysis (default Change). A write is never inferred from model text. `chat.send` takes `mode`.
3. **Action list.** `projects.confirm` starts a session for the chosen local project after onboarding (it is not a stored "confirmation" record). `review.add` and `outcome.add` are **not in v2.0.0**; the person's approval and rejection are recorded as decision records instead. A GitHub project is read-only and cannot be chosen as the project to change.
4. **Approvals.** Reject and Revise use the `approve` request; each spends it. A request is also dead after a restart (it belongs to a dead process), and a failed approve or pull request reopens a fresh request instead of leaving the turn stuck. The approval is asked after the memory refresh, so a person never races it. No override exists in the web for a failed or missing check on a pull request (the CLI's `--without-passing-check` is not offered).
5. **Added `/api/meta`** on all three servers (token and the real ports) so the pages can link to each other; reading the token needs the cookie or the header, and no CORS header is sent, so another origin cannot read it.
6. **POST bodies are taken off the socket before any answer**, otherwise an early refusal reset the connection on Windows before the client read it.
7. `numpy` became a direct dependency (already locked, 2.5.4) for the 2-D projection (PCA by SVD, at most 5,000 points).
8. `change/cli.py` gained `approve_change` and `open_pull_request` (the CLI's own logic, extracted, tests unchanged); `MemoryIndex` gained read-only `open_for_browse`, `collections`, `page`, `item`, `neighbours`, `vectors`. The Chroma index stores vectors and metadata only, so the data server re-reads each record's text from the trace database and says when it cannot confirm it.

## Verified
Tests: `tests/test_serve_http.py`, `test_serve_flow.py`, `test_serve_data.py` (scripted models, fake embedder, no network). Run by hand 2026-11-04: the three pages in headless Edge against scripted models (chat with plan, draft, impact and approval card; graph with kind filters and a dashed declared edge; data pages) and `cognitive-mesh serve` started for real with no model configured. **Not run:** a real model (free-tier or local), a real GitHub push or pull request, Firefox/Safari, a phone-width window, the Change flow in a browser with real mouse clicks (the approve, revise and reject buttons were exercised through the HTTP API only), the 2-D map in a browser, the real MiniLM through the data page.

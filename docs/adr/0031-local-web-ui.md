# ADR-0031: Local read-only web UI

## Status
Proposed (needs the user's acceptance before any code is written)

## Date
2026-10-24

## Context
Everything the system records is reachable only through CLI text or `--json`: traces, claims with their statuses and
supporting and contradicting records, the dependency graph, and recall and search results. The user asked for a UI
and chose, on 2026-10-24: a local web UI started by the CLI; read-only in the first version; Python standard library
only (`http.server`, vanilla HTML/JS/CSS); numbered v1.5.0, with Model Performance Tracking and Routing moving to
v1.6.0.

Constraints from the project rules:
- Application files, retrieved memories and model outputs are data, never instructions. Trace content can contain
  attacker-controlled text (file contents, model output), so a browser UI is a new place for it to do harm (script
  injection, reads from other origins).
- Statuses are categories computed per code version on every call (ADR-0016). The UI must not turn them into a score
  or a claim of truth, must keep raw experiences, claims and unconfirmed hits in separate groups, and must show
  similarity only as "retrieved via".
- The stores have no authentication. `experience review` records are unauthenticated by design.
- No new dependency (ADR-0012 precedent). The existing ADR-0008 HTML renderer already escapes everything and carries
  a no-script CSP.

What the code gives us (inspected 2026-10-24): every view has a read-only library call (`TraceStore.open_existing`,
`experience.store.load`, `check_sources`, `assess_claims`, `retrieval.recall`, `MemoryIndex.open_existing`,
`build_view` + `render_html`). Results are mostly pydantic models. The JSON for `experience claims`, `show` and
`check` is built inline in the CLI handlers, so it must move to shared functions to keep the CLI and the UI from
drifting. Stores use plain `sqlite3.connect` without `check_same_thread=False`, so a threaded server must open a
store inside each request. Loading the embedding model takes about 13 s, so it must load once per process.

## Decision
Add `cognitive-mesh ui`, a local, read-only, GET-only web server in a new `cognitive_mesh/ui/` package.

1. **Binding.** `127.0.0.1` only; there is no `--host` option. `--port` defaults to 8765; `--port 0` picks a free
   port. A port in use exits with a new code 17 naming the port.
2. **Access token.** At start-up the server makes a token with `secrets.token_urlsafe` and prints
   `http://127.0.0.1:PORT/?token=...`. The first visit sets an `HttpOnly`, `SameSite=Strict` cookie; later requests
   may use the cookie or an `X-Cogmesh-Token` header. A missing or wrong token gets 403. The token is compared with
   `secrets.compare_digest`, is never logged, and exists only in memory.
3. **Host check.** Only `Host: 127.0.0.1:PORT` and `localhost:PORT` are accepted (DNS-rebinding defense); anything
   else gets 403 before routing.
4. **Methods and headers.** Only GET. Other methods get 405. No CORS headers. Every response carries
   `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self';
   frame-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. Request lines and header sizes are bounded by the
   stdlib limits; query strings are length-limited by the existing `MAX_QUERY_CHARS`.
5. **Read-only.** Stores are opened only through the existing `open_existing` / `load` paths, which refuse a missing
   file and set `query_only`. The UI never calls `create`, `add_*`, `build`, `review`, `outcome` or `promote`. A
   missing input is reported and nothing is created. A test hashes every input file before and after hitting every
   endpoint.
6. **Data is untrusted.** The JavaScript writes library data into the page only with `textContent` and
   `setAttribute` on fixed attributes; `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` and `eval`
   are forbidden and a test scans `app.js` for them. The dependency graph is the ADR-0008 page from `render_html`,
   served as its own response and shown in an iframe with `sandbox=""` (no scripts, no same-origin).
7. **Static files.** `index.html`, `app.js` and `app.css` are package data, served from a fixed allowlist of names,
   never from a path built from the request.
8. **Evidence rules carry over.** A claim is shown with its status label, its fixed `STATUS_MEANING` text, and the
   run and document ids on both sides. The "unvalidated", "no --root given" and "reviews are unauthenticated" notes
   appear wherever the CLI prints them. Similarity appears only as "retrieved via". There is no score, no sorting by
   confidence, and no green/red "true/false" styling: statuses use text labels plus a neutral marker.
9. **Errors.** The exceptions `_guarded` maps to exit codes map to HTTP 400, 404 or 409 with the library's fixed
   messages (which contain no paths). Any other exception returns a generic 500 with no traceback or path in the
   body. A `traces list` with malformed rows and an `experience check` with unverified sources are returned as
   warnings in a 200 body, not as errors.
10. **Concurrency and model load.** `ThreadingHTTPServer`; each store is opened and closed inside the request. One
    embedder per process, created lazily under a lock through `cli._make_embedder` (so tests keep substituting the
    fake) and reused by every recall and search request.
11. **Sharing code with the CLI.** The JSON built inline for `experience claims`, `show` and `check` moves to pure
    functions in `experience/report.py`. The CLI `--json` output must stay byte-identical (existing CLI tests are
    the guard) and the UI endpoints return the same payloads.
12. **Endpoints (all GET):** `/api/status`, `/api/traces`, `/api/traces/<run_id>`, `/api/claims`,
    `/api/nodes/<id>`, `/api/check`, `/api/recall`, `/api/search`, `/api/graph.html` (only with `--root`).

Out of scope for v1.5.0: running `task` or `run`, recording reviews, outcomes or promotions, rebuilding the index or
graph, authentication beyond the token, remote access, caching, and live updates.

## Alternatives considered
- **Terminal UI (e.g. Textual).** Stays in the terminal but adds a dependency and is weak for graph drawing.
- **Static HTML report.** Smallest attack surface and no server, but no interactive recall or search, and the claims
  assessment would be frozen at generation time.
- **FastAPI + uvicorn.** Typed routing fits pydantic, but adds two runtime dependencies for a read-only local tool;
  rejected by the user for v1.5.0.
- **No token (rely on 127.0.0.1).** Any local process or web page in the user's browser could then read trace
  content, which can include file excerpts. The token and Host check cost little.
- **Share one store connection across threads.** Would need `check_same_thread=False` and locking in four stores;
  opening per request matches how the CLI already uses them.
- **Call the CLI as a subprocess.** Would reload the model (about 13 s) on every query and parse text.

## Consequences
- Easier: inspecting evidence and conflicts without reading JSON; the model loads once per UI session, which partly
  addresses the recall start-up cost (open item b2) for interactive use only.
- Harder: a second output surface for untrusted data, held in check by the rules above and by tests; one more public
  interface (the JSON payloads) that the CLI and UI now share, so changing either needs the parity tests updated.
- Known limits accepted: the token protects against other origins and processes of other users but not against
  malware running as the same user (it can read the files directly anyway); `claims` and `recall` re-run the full
  assessment per request, which takes seconds on large graphs (ADR-0027/0028/0030 numbers apply); `--root` adds a
  re-derivation of the application on each such request; there is no caching; Windows is the only platform the
  server has been run on until the verification step says otherwise.
- Nothing in this ADR is implemented yet. The README, ROADMAP and verified-commands lists must not describe the UI as
  existing until the criteria in `docs/DETAILED_ROADMAP.md` v1.5.0 pass.

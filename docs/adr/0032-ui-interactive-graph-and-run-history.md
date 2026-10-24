# ADR-0032: Interactive dependency graph, typed folders, pinned ids and run history in the UI (amends ADR-0031)

## Status
Accepted (text reviewed and accepted by the user 2026-10-24, before any code was written)

## Date
2026-10-24

## Context
After using the v1.5.0 UI (ADR-0031) the user asked for three changes:
1. The Dependency graph tab should take a folder name typed in the page and build the graph live; the graph should be
   zoomable; the full graph (not truncated) should be one click away; hovering a node should show its id; ids should be
   stored and recognised when they appear again. The user chose: **any folder I type**, and **pins kept in this
   browser**.
2. Runs should show all previous runs of graph nodes and claims.
3. The "Search runs" tab is not needed, because Runs already lists every run.

Facts about the code base (inspected 2026-10-24):
- ADR-0031 fixes the graph root at start (`--root`), serves the ADR-0008 page (`render_html`, no script) in a
  `sandbox=""` iframe, and says the page's own policy allows no script from library data. That page cannot zoom or show
  an inspector, and an iframe with no script cannot talk to the app.
- `build_view` already produces a `GraphView` (nodes with generated keys, edges, `layers`, truncation counts) and
  `render_html._positions` already computes a layered layout on the server. `build_view(graph, max_nodes, max_edges)`
  truncates at 300 nodes and 1000 edges for HTML.
- Ingestion (ADR-0005) never executes code, never opens sensitive file names, redacts secrets in content, refuses a
  root that is a symlink or junction, skips symlinks inside it, and stops at 5,000 files and 1 MiB per file
  (`DEFAULT_MAX_FILES`, `DEFAULT_MAX_FILE_BYTES`). On 2026-10-24 `E:\Projects` (five projects) built 545 nodes and
  1,781 edges in about 7 s inside the server.
- The experience graph has run, step, attempt, target and claim nodes joined by `evidenced_by`, `subject` and similar
  edges; `ExperienceGraph.reachable(id, forward=False, transitive=True)` walks them; `check_nodes` verifies only the
  nodes it is given (ADR-0028).
- The page policy allows `script-src 'self'`, so a second script file is allowed; inline `style` attributes and
  `<style>` are not.

## Decision
1. **Typed folder.** The Graph tab has a folder field. `GET /api/graph?path=DIR&full=0|1` builds the dependency graph of
   that directory on the server and returns JSON. `--root` stays as the default folder and as the folder used to check
   claims (ADR-0016); it no longer limits what the Graph tab may show. The server accepts any directory the user
   account can read, as chosen by the user. It refuses, with a fixed message and HTTP 400/409:
   - an empty path, a path over 1,024 characters, or one containing control characters or NUL;
   - a UNC path (`\\host\share`, `//host/share`) or a Windows device path (`\\.\`, `\\?\`), so the server never
     reaches out to the network on a page's request;
   - a path that does not exist or is not a directory;
   - a root that is a symlink or junction (the existing ingestion rule).
   Relative paths resolve against the server's working directory and are shown back in resolved form. Ingestion limits
   stay as they are. The response lists file paths and import names but no file content.
2. **Bounded work.** One graph build runs at a time (a lock); a second request waits, and the page shows that it is
   waiting. There is no cache in this version: "live" means rebuilt on every request. Because the whole build is
   synchronous, the page shows "Building the graph..." until it arrives.
3. **Full graph.** `full=0` (default) applies the existing 300/1000 limits and says so. `full=1` removes the limits up
   to a hard ceiling of 3,000 nodes and 10,000 edges; above that the server returns 409 with the counts and points to
   `cognitive-mesh graph DIR --json`. The ceilings are a starting point to be checked against browser frame times in
   the verification step and may be changed by amending this ADR. Truncation is never silent: the page shows shown and
   total counts, and which kinds were omitted.
4. **Interactive renderer.** `app.js` (or a second same-origin file `graph.js`) draws the JSON as SVG built with
   `createElementNS` and `textContent`; the ADR-0008 page is no longer shown in the UI (the endpoint and the CLI
   `--format html` stay). The layout is the existing server-side layered layout, exposed as a function and returned as
   x/y per node, so the CLI and the UI agree. Interaction:
   - zoom by mouse wheel, pinch, and the buttons and keys `+`, `-`, `0` (fit); pan by dragging and by arrow keys;
     implemented by changing the SVG `viewBox` with `setAttribute`, never by inline style;
   - hover or keyboard focus on a node shows its id, kind, label and edge counts in an inspector strip, and the id can
     be copied; hover or focus dims everything except the node and its direct neighbours (a class toggle) because a
     dense graph is unreadable otherwise;
   - an edge shows its evidence (path and line) in the inspector on hover or focus;
   - nodes are focusable, with visible focus, and the inspector has the same content for keyboard users.
   Graph text from the folder (file names can contain `<script>`) only ever reaches the page as text.
5. **Pinned ids.** Clicking a node, or the pin button in the inspector, pins its id. Pins are stored only in the
   browser (`localStorage`, key `cogmesh.pins.v1`, at most 200 entries, each `{space, id, label, pinned_at}`), in a
   `try/catch` so the page works when storage is unavailable. Nothing is written to the server or to any project file.
   `space` is `deps` (dependency graph ids such as `module:src/a.py`) or `exp` (experience ids such as
   `claim:...`), because the same text can name different things. Every place the UI prints an id goes through one
   helper: a pinned id gets a pin mark and a link back to the pinned node (in the right view), an unpinned id gets a
   plain link. A pin list on the Graph tab lets the user unpin. Limit: `localStorage` is per origin, and an origin
   includes the port, so pins survive restarts only if the same `--port` is used (the default 8765 is stable;
   `--port 0` is not).
6. **Run history for nodes and claims.** `GET /api/history?id=ID` returns every recorded run that touched that
   experience-graph node (a claim, a target such as `target:module:src/a.py`, a run, a step or an attempt). The Runs tab
   gets a field for an id and the same list is shown from a node, a claim or a pinned id. Each row has run id, start
   time, status, code version, step and attempt, and what that run said about it: for a claim, the supporting and
   contradicting attempts from the assessment (ADR-0016); for a target, the runs whose outputs named it. The history is
   built with `ExperienceGraph.reachable(..., forward=False, transitive=True)`, and only the nodes it reaches are
   source-checked (`check_nodes`, ADR-0028). A row whose source is not confirmed is shown and marked, not hidden. Runs
   are raw records: the page says they are not validated knowledge. It needs `--db` and `--graph`; without them it
   says so. A dependency-graph node that no run mentions shows "No recorded run mentions this id".
7. **Search tab removed.** The "Search runs" tab and its copy are removed from the UI. `/api/search`, its tests and the
   CLI `memory search` stay, so nothing a script relies on changes. The Runs tab shows every run, newest first, and can
   be filtered by id (point 6).
8. **Tests and checks.** Tests: path validation (empty, long, control characters, UNC, device paths, missing, a file,
   a symlink root where the platform allows); ceilings and the explicit refusal above them; `full` removing the limits;
   the JSON graph equals `graph --json` for the same folder; a file named with markup comes back as inert text;
   no file is created or changed in the typed folder; two simultaneous builds run one after the other; history rows for
   a claim, a target and an unknown id, with an unverified row marked; the static scan extends to forbid assigning
   `.style`, `cssText` and `setAttribute("style"`; the "exactly one script tag" check becomes "only same-origin script
   tags". The JavaScript has no test runner in this repository, so zoom, pan, hover and pinning are checked by hand in
   headless Edge with screenshots, and the verified-commands list says which were not run. Pure helpers (zoom math, pin
   storage) are kept small and separate so a runner can be added later.

Out of scope: writing anything (pins are browser-only), caching graphs, comparing two folders, executing any code, a
folder picker dialog (browsers do not give a page a path), remote access, and making a very dense graph readable
beyond zoom, neighbour highlighting and the existing layered layout.

## Alternatives considered
- **Only folders under an allowed list (`--browse-root`).** Safer; offered to the user as the recommended option and
  declined in favour of typing any folder. Kept as a possible later hardening that needs no page change.
- **Fixed folder list from launch.** Smallest change but not "type a folder".
- **Keep the ADR-0008 iframe and add CSS-only zoom.** No script from library data, but no hover inspector, no pinning and
  no neighbour highlighting; the iframe cannot reach the page.
- **Let the iframe run its own script.** Would need `allow-scripts` on a page that renders untrusted names; rejected.
- **A graph library (Cytoscape, D3, vis).** A runtime dependency and a CDN or vendored file, against the no-dependency
  decision (ADR-0031, ADR-0012); the SVG needed here is small.
- **Pins on the server.** Needs a file the UI writes, which breaks the read-only rule and needs its own ADR.
- **Cache built graphs.** Makes repeat views fast but needs invalidation (files change) and memory limits; deferred
  until the measured cost justifies it.

## Consequences
- Easier: exploring any local project without restarting the server; reading a large graph by zooming and following
  neighbours; seeing every run behind a claim or a node in one place; removing a tab that duplicated Runs.
- Harder: the server now reads whatever directory a token holder names. A leaked token exposes file names, structure
  and import names of any directory the account can read, not only the configured stores (it still exposes no file
  content, and still writes nothing). This is accepted knowingly because the user chose it; the token, the Host check
  and loopback binding of ADR-0031 are the controls, and a later `--browse-root` allowlist is the escape hatch.
- Builds are CPU and disk heavy (about 7 s for 545 nodes); two people cannot share one server meaningfully, and a
  large typed folder can occupy the server for the length of one build.
- A second renderer exists for the same graph (JS in the UI, Python for the CLI HTML); they share the layout function
  and the `GraphView`, but their drawing code can drift, so tests compare the data, not the pictures.
- Unknown until measured: frame rate and memory with 3,000 nodes in SVG; whether neighbour highlighting makes the
  `E:\Projects` graph readable; whether pins surviving only on a fixed port confuses users.
- Nothing in this ADR is implemented. The README, ROADMAP and verified-commands list must not describe these features
  as existing until the tests and the manual checks in point 8 pass.

## Outcome of the implementation (2026-10-24, Windows 11, headless Edge, Node 22 driving it over the DevTools protocol)
Done as written, with these details and findings:
- **Added beyond the ADR text:** a "Find a node" field (jump to a node by part of its id); a note when a graph has more than 150 nodes
  ("starts small, zoom in or use Find"); node `shape` is the payload key for the drawing style (a key named `style` tripped the
  rule that forbids assigning `.style`); zoom, pan and key events are redrawn once per animation frame; edges use
  `stroke-opacity`, not `opacity`, and arrowheads of dimmed edges are removed while a node is highlighted.
- **A target is pinned in the `deps` space** (its name is the dependency-graph id), so a module id pinned from the graph and from
  the Runs page is one pin.
- **Measured:**
  - `E:\Projects` (five projects), full graph: 552 nodes, 1,819 edges; the server took about 7 s, the page drew it in the same
    wait; a sustained zoom test (40 wheel steps at 60 Hz) gave frames of average 29 ms, 95th percentile 83 to 100 ms, worst
    230 to 300 ms.
  - A synthetic folder of 2,900 modules, full graph: 5,790 edges, payload 1.5 MB; the server took 32 s (not profiled: ingestion
    and layout both run in it); the page drew it in 2.7 s; the same zoom test gave average 32 ms, 95th percentile 83 ms, worst
    100 ms. The 3,000 node ceiling is kept; the limit people will meet first is the build time, not drawing.
- **Checked in a real browser (32 checks, all passing):** typed folder builds; wheel, buttons and `+`, `-`, `0` zoom; hover shows the
  id and dims non-neighbours; click pins; pin mark; find; pins recognised in claim text and in the Runs header; run history for
  a module id, for a claim (support and contradiction rows) and for an unknown id; unpin; truncation banner and one-click full
  graph; refusals for a missing folder and a network path; pins survive a reload.
- **Not checked:** hovering an edge to read its evidence and the Copy id button (written, not driven); touch pinch and
  drag on a touch screen; keyboard-only use beyond the zoom keys and Find; a screen reader; light mode; Firefox and Safari;
  macOS and Linux; a folder with more than the ingestion limit of 5,000 files; pins with `--port 0` across restarts.
- **Known limits:** at "Fit" a graph of hundreds of nodes is a tall narrow strip, because the layered layout puts at most ten
  nodes per row; it is readable only after zooming, which is what Find and neighbour highlighting are for. The 32 s build for
  2,900 modules is long for "live".

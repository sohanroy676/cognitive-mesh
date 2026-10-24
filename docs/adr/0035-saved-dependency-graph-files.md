# ADR-0035: Load a saved dependency graph file in the UI's Graph tab (amends ADR-0032)

## Status
Proposed (needs the user's acceptance before any code is written)

## Date
2026-10-25

## Context
The user asked that the dependency graph of `E:\Projects` be kept as a reference in the repository and that, when the
UI is pointed at that file, it just loads it. The snapshot is committed as `data/reference-graphs/projects-graph.json`
(557 nodes, 1,855 edges, 1.6 MB). Today the Graph tab only builds a graph from a folder; building `E:\Projects`
takes about 7 s and a 2,900-module folder about 32 s (ADR-0032).

Facts about the code base (inspected 2026-10-25):
- `cognitive-mesh graph DIR --json` already writes the graph as `{nodes, edges, diagnostics, summary}`: nodes with
  id, kind, name and the file that introduced them; edges with their evidence (path, line, rule, cited text).
- `DependencyGraph(nodes, edges, diagnostics)` is built from the pydantic models `Node`, `Edge` and
  `GraphDiagnostic`; it has `to_dict()` but no way to build one back from a dictionary.
- The UI's `/api/graph?path=...` resolves the typed text to a folder (`ui/paths.py`: empty, over-long,
  control-character, network and device paths are refused; a file is refused as "not a directory"), builds the graph
  under a lock, then `graph_payload` turns it into JSON with the shared layered layout. Evidence reaches the page as
  path, line and rule only, never the cited text (ADR-0032 point 1).
- Layout is computed from the graph on every request, so it need not be stored.

## Decision
1. **The Graph tab's path may name a saved graph file.** A typed path that is an existing regular file ending in
   `.json` is loaded as a saved dependency graph; a folder behaves exactly as before; any other file keeps being
   refused, now with "that path is not a folder or a saved graph file (.json)". Nothing is ingested and nothing is run.
2. **The file format is the one the CLI already writes** (`cognitive-mesh graph DIR --json`). No new format, no version
   field. A user creates a snapshot with `cognitive-mesh graph DIR --json > FILE`. The UI never writes.
3. **`DependencyGraph.from_dict(data)`** is added. It validates every node, edge and diagnostic through the existing
   models and refuses anything else (a missing key, an unknown node kind, an edge that names a node that is not
   there) with `GraphError`. A round trip `from_dict(g.to_dict())` must give an equal graph.
4. **Bounded and path-free errors.** The file must be a regular file (not a symlink or junction), at most 25 MB; the
   same path rules as folders apply (no control characters, no network or device paths). A file that cannot be read,
   is not JSON, or is not a valid graph gives HTTP 409 and the fixed message "that file is not a saved dependency
   graph". The message never contains text from the file.
5. **The same view as a folder.** The loaded graph goes through `graph_payload`, so the default 300-node and 1,000-edge
   view, the "Show the full graph" button, the 3,000/10,000 ceiling, the layout, zoom, hover and pins all work the
   same. Pins record the file path as their source.
6. **A snapshot is labelled as one.** The response says `source: saved file` with the path, the size and the file's
   modified time, and the page shows: "Loaded from a saved file, not built from the folder just now. The folder may
   have changed since it was saved." A saved graph is never presented as the current state of a folder.
7. **Same trust as typed folders (ADR-0032).** A token holder can make the server read any `.json` file the account can
   read, but only a file that validates as a graph returns anything, and only graph fields are returned (no cited
   text). File names and import names inside the file are untrusted text and reach the page as text only.
8. **Tests and checks.** Tests: the `from_dict` round trip on fixture graphs; loading a saved fixture graph gives the
   same nodes, edges and layout as building the folder; loading the committed 557-node reference file; the 25 MB cap;
   not JSON, JSON of another shape, an unknown node kind, a dangling edge, a directory named `x.json`, a symlinked file
   and an unreadable file each give the fixed message with no file text in it; evidence carries no cited text; hostile
   names come back inert; the files are byte-identical afterwards; a folder path still works. A browser check with real
   input types the reference file's path, sees the graph in about a second with the snapshot label, zooms and pins.

Out of scope: saving a graph from the UI, comparing two snapshots, loading an experience database through the same box,
a version field or a compact format, and any change to how folders are built.

## Alternatives considered
- **A `--graph-file` option on `ui`.** Works, but needs a restart to switch files and adds an option; typing the path
  matches how folders are chosen.
- **A file picker.** Browsers do not give a page a path, so the server could not read the file.
- **A new, smaller format with the layout stored.** Faster to parse, but a second format to keep in step with the CLI's;
  the 1.6 MB file parses in well under a second and the layout is cheap to recompute.
- **Keep rebuilding from the folder.** Always current, but takes 7 s or more and cannot reproduce a past state, which is
  what a reference is for.

## Consequences
- Easier: opening the reference graph at once, reproducibly, and keeping several snapshots to open by path.
- Harder: a snapshot can be mistaken for the present; the label is the only guard. One more kind of file the server reads,
  with a parser that must stay strict.
- Limits: the file holds absolute paths of the machine it was made on (`ref.root`); they are not sent to the page.
  Nothing checks that a snapshot still matches the folder it came from.
- Nothing in this ADR is implemented. The README and `data/reference-graphs/README.md` must not describe loading as
  working until the tests and the browser check in point 8 pass.

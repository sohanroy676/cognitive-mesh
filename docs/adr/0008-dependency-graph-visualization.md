# ADR-0008: Dependency graph visualization

## Status
Accepted (plan approved by the user 2026-10-17: both formats, no new dependency)

## Date
2026-10-17

## Context
v0.3.0/v0.3.1 build an evidence-backed `DependencyGraph` (ADR-0006, ADR-0007) that can only be read
as terminal text or JSON. v0.3.2 adds a viewable rendering. Constraints: unresolved items, computed
dynamic imports and cycles must stay visibly distinct from resolved edges; evidence must stay
reachable; large graphs must truncate visibly, not silently; node names, paths and evidence come
from ingested files and are untrusted (they can contain markup, quotes, newlines, bidi overrides);
output must be deterministic; existing text and `--json` output must not change; no new dependency
(security rules), no CDN or network loads.

## Decision
- **Two formats from one view model.** `cognitive_mesh.graph.view.build_view` selects and numbers
  nodes and edges once (`GraphView`); `render_mermaid` and `render_html` are pure functions of it.
  Selection only picks from the graph; it never adds, merges or rewrites nodes, edges or evidence.
  - `mermaid`: flowchart text for GitHub or any Mermaid renderer.
  - `html`: one self-contained page with inline SVG and tables. No JavaScript, no external
    resource, no link, with a Content-Security-Policy meta tag (`default-src 'none'`) as a second
    layer.
- **CLI:** `cognitive-mesh graph PATH --format {mermaid,html} [--output FILE] [--max-nodes N]
  [--max-edges N]`. `--format` cannot be combined with `--json`, `--deps-of` or `--transitive`, and
  `--output`/`--max-*` need `--format`; violations and non-positive limits are argparse usage errors
  (exit 2). Output goes to stdout unless `--output` is given (file overwritten); a write failure
  prints to stderr and exits 5. A truncation notice is also printed to stderr. Without `--format`
  the command is unchanged.
- **Output is ASCII with LF line endings**, no timestamps, and never an absolute path
  (`SourceRef.root` is not rendered; only the relative `ref.path`).
- **Untrusted text.**
  - HTML: control and non-printable characters are shown as visible escapes (`\n`, `‮`), then
    HTML-escaped, then non-ASCII becomes `&#N;`. Graph text goes only into text nodes and SVG
    `<title>`s; element ids and classes are generated (`n3`, `e7`, `k-module`) and the CSS is a
    constant.
  - Mermaid: graph text is never an identifier (ids are `n0`...). Every character outside
    `[A-Za-z0-9 ._/:@,-]` in a label or comment is written as a `#<code>;` entity, so quotes,
    brackets, `-->`, backticks, braces (hence `%%{init}%%` directives) and newlines are inert.
    There are no `click` lines.
- **Visual distinctions (never colour only; each node box states its kind in text).**
  - Node kinds component, module, package, stdlib, unresolved each have a style. Unresolved nodes
    are dashed; computed dynamic imports (`unresolved:dynamic:` ids, ADR-0007) have their own dotted
    style and the text "computed import".
  - Edges: `contains` thin grey, `imports`/`declares` solid, an edge to an unresolved node dashed
    (Mermaid `-.->`), an edge inside a dependency cycle thick red (Mermaid `==>`, label "cycle").
    A cycle edge joins two members of the same strongly connected component, or is a self-loop.
    Cycle membership uses `DependencyGraph.cyclic_components()`, which is linear time and has no
    limit, so it does not depend on the 1000-cycle cap of `cycles()`.
  - The HTML has a legend. Members of cycles are labelled "(in cycle)".
- **Evidence stays reachable.** Every shown edge has a number `eN`. HTML: an SVG tooltip on each
  edge plus an Edges table with every evidence item (path, line, rule, detail). Mermaid: a
  `%% eN: path:line rule (+k more)` comment per edge; full detail is in `--format html` or
  `--json`.
- **Size limits and truncation.** Defaults: html 300 nodes / 1000 edges, mermaid 100 / 200. Nodes
  are kept by priority: components, cycle members, computed imports, unresolved, modules,
  packages, stdlib (ties by id). Edges are kept only between kept nodes, cycle edges first, then
  edges to unresolved nodes, then the rest. When anything is omitted a visible notice states shown
  vs total nodes and edges and the omitted nodes by kind (HTML banner, Mermaid `notice` node and
  comment, stderr line). The summary table always describes the full graph. Tables list shown items
  only, with caps (50 cycles, 200 diagnostics) that also state how many exist.
- **HTML layout** is hand-written and deterministic: layers are the topological generations of the
  cycle-condensed graph (dependents on top), each layer sorted by id and wrapped at 10 boxes per
  row. Labels are clipped to 30 characters in the drawing; the full text is in the tooltip and
  tables. The canvas scrolls horizontally instead of shrinking.
- **New API:** `DependencyGraph.cyclic_components()` and `DependencyGraph.layers()` (read-only),
  `build_view`, `render_html`, `render_mermaid`, `GraphView`.

## Alternatives considered
- Graphviz/DOT: needs an external binary or a new dependency. Rejected.
- D3, vis.js or Mermaid's JS embedded in the HTML: executable content or a CDN. Rejected.
- NetworkX layouts: need numpy, a new dependency. Rejected.
- Mermaid only: viewing needs a renderer (usually online), and safety would depend on it. HTML only:
  nothing to paste into a GitHub README. Both were chosen on request.

## Consequences
- A person can open the HTML offline, or paste the Mermaid text, and see components, dependencies,
  unresolved items and cycles, with evidence one hover or table row away.
- The layout is naive: edges cross and long edges overlap on dense graphs. Truncation keeps cycle
  members before unresolved items, so in a graph dominated by one large cycle unresolved nodes can
  be omitted (the notice says how many nodes of each kind were).
- Mermaid output is inert text, but how it is displayed depends on the viewer's Mermaid
  configuration; this is not verified against any Mermaid renderer here.
- The HTML was checked in headless Edge only; other browsers are untested.
- A view is a rendering of the in-memory graph; nothing is persisted. No new dependency.

# Reference dependency graph of `E:\Projects`

`projects-graph.json` is the output of `cognitive-mesh graph E:\Projects --json`, taken on 2026-10-25. It is the
dependency graph of the five projects in that folder (DependencyGraph, Incident-commander, agent-config-deployer,
cognitive-mesh, onboarding-coordinator), kept as a reference so it does not have to be rebuilt.

- 557 nodes (10 components, 319 modules, 125 packages, 53 stdlib, 50 unresolved) and 1,855 edges (1,367 imports,
  269 contains, 179 requires, 40 declares), with 15 diagnostics.
- It is a snapshot: the folder has changed since it was taken. An earlier graph of the same folder had 552 nodes
  and 1,819 edges; the cognitive-mesh repository has grown since.
- An edge from A to B means A depends on B. Every edge carries its evidence (file, line, rule). Unresolved imports are
  listed as unresolved, never guessed.

## Open it in the web UI

```
uv run cognitive-mesh ui
```

In the **Dependency graph** tab, type the path of this file (for example
`data/reference-graphs/projects-graph.json`) and choose **Build graph**. It opens in about 0.12 s, against 8 to 9 s
to build the same projects from their folder. The page says it was loaded from a saved file, not built just now,
because the folder may have changed since. The default view shows 300 of the 557 nodes; **Show the full graph**
shows all of them. Zoom, hover, Find and pins work as for a folder.

Make your own snapshot of any folder with:

```
uv run cognitive-mesh graph PATH/TO/FOLDER --json > my-graph.json
```

A file that is not a valid saved graph is refused with a fixed message that never contains text from the file
(ADR-0035).

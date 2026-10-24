# Reference dependency graph of `E:\Projects`

`projects-graph.json` is the output of `cognitive-mesh graph E:\Projects --json`, taken on 2026-10-25. It is the
dependency graph of the five projects in that folder (DependencyGraph, Incident-commander, agent-config-deployer,
cognitive-mesh, onboarding-coordinator), kept as a reference so it does not have to be rebuilt.

- 557 nodes (10 components, 319 modules, 125 packages, 53 stdlib, 50 unresolved) and 1,855 edges (1,367 imports,
  269 contains, 179 requires, 40 declares), with 15 diagnostics.
- It is slightly larger than the graph viewed earlier in the session (552 nodes, 1,819 edges), because the
  cognitive-mesh repository has grown since. It is a snapshot: the folder has changed since it was taken.
- An edge from A to B means A depends on B. Every edge carries its evidence (file, line, rule). Unresolved imports are
  listed as unresolved, never guessed.

Loading this file directly in the web UI is not implemented. It is proposed in ADR-0035.

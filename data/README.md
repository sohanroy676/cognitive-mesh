# Data kept on purpose

Reference data committed to the repository, so it can be opened again without being rebuilt.

- `agent-experiences/`: a real experience database from five agents adding features to five projects (databases,
  readable exports, the agents' logs, and the tools that built it).
- `reference-graphs/`: a saved dependency graph of `E:\Projects`.

The rest of the repository ignores `*.sqlite3` (trace databases can hold file content). A narrow exception in
`.gitignore` lets `data/agent-experiences/*.sqlite3` through, and `.gitattributes` marks those files as binary.

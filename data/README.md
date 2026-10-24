# Data kept on purpose

Reference data committed to the repository, so it can be opened again without being rebuilt.

- `agent-experiences/`: a real experience database from five agents adding features to five projects (databases,
  readable exports, the agents' logs, and the tools that built it).
- `reference-graphs/`: a saved dependency graph of `E:\Projects`.

The rest of the repository ignores `*.sqlite3` (trace databases can hold file content). A narrow exception in
`.gitignore` lets `data/agent-experiences/*.sqlite3` through, and `.gitattributes` marks those files as binary.

## Works on any machine

Everything here is self-contained. It was made on one computer, from folders under `E:\Projects` and a scratch
folder `E:\experience-db`; those folders are **not needed** to open or use the data. The databases and JSON files
carry absolute paths from that machine as plain text (so a reader can see where things came from), and the web UI
never uses them. The two scripts under `agent-experiences/tools/` record how the data was built and only run on a
machine that has those folders; they are kept as provenance, not as part of the project.

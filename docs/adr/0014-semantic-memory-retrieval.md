# ADR-0014: Semantic memory retrieval over stored traces

## Status
Accepted (design choices confirmed by the user in plan mode 2026-10-17; text reviewed and accepted by the user 2026-10-17, before any code was written)

## Date
2026-10-17

## Context
v0.6.0 stores raw run traces in SQLite (ADR-0011) but nothing can find them again. v0.7.0 lets a later
task look up relevant earlier experiences by meaning. The project brief requires that retrieved knowledge
traces back to its supporting records and that similarity, repeated output and model confidence are never
treated as proof.

Facts checked on PyPI / Hugging Face on 2026-10-17 (not yet installed or run):
- `sentence-transformers` 6.1.0: Apache-2.0, authors Nils Reimers and Tom Aarsen (Hugging Face), pure-Python
  wheel 740 KB, requires `torch>=2.2`, `transformers>=5`, `scikit-learn`, `scipy`, `numpy`.
- `torch` 2.14.1: Windows cp312 wheel 124 MB; Linux x86_64 wheel 554 MB and it pulls CUDA packages on Linux,
  which is too heavy for CI unless the CPU build is chosen.
- `chromadb` 1.5.9: Apache-2.0, wheel 23 MB, requires among others `onnxruntime`, `grpcio`,
  `opentelemetry-*`, `kubernetes`, `pydantic>=2`.
- `sentence-transformers/all-MiniLM-L6-v2`: Apache-2.0, `model.safetensors` ~90 MB, 384 dimensions, revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.

Constraints: retrieval must work without a model provider (Groq/Ollama); the SQLite trace database stays
authoritative; no network in default or CI test runs; raw traces must stay distinguishable from validated
knowledge (nothing in v0.7.0 promotes anything to "knowledge").

## Decision
- **Eligible content.** One document per step of a *finished* run (`succeeded|failed|incomplete`; never
  `interrupted` or malformed runs), taken from the step's final attempt, for steps whose final attempt
  succeeded or failed. `blocked` and `not_run` steps have no attempt and are skipped. Text is built
  deterministically (`TEXT_FORMAT_VERSION = 1`): capability, the step's plan inputs (already redacted in
  `plan_json`), then the final output (success) or failure kind plus the fixed failure message (failure).
  Status and `basis` (`deterministic` or `model_unverified`) are stored as metadata and shown in every hit.
  The text is hashed (sha256).
- **Model.** Fixed in code for v0.7.0: `sentence-transformers/all-MiniLM-L6-v2` at the pinned revision above,
  normalised vectors, 384 dimensions. No `COGMESH_EMBEDDING_MODEL` setting, so no unpinned model can be chosen.
  The model is loaded lazily and only by `memory index` / `memory search`, never at import. `HF_HUB_OFFLINE`
  is respected; the first use downloads ~90 MB from Hugging Face (documented, not hidden).
- **Dependencies.** `sentence-transformers` and `chromadb` become runtime dependencies. `torch` is taken from the
  official PyTorch CPU index through an explicit `[[tool.uv.index]]` plus `[tool.uv.sources]`, so Linux CI does not
  install CUDA. The lockfile is checked for `nvidia-*` / `cuda-*` entries.
- **Module boundaries** (`src/cognitive_mesh/memory/`): `documents.py` (pure), `embedder.py` (the only module
  importing `sentence_transformers`), `index.py` (the only module importing `chromadb`), `models.py` (frozen
  pydantic results). Chroma never embeds: we pass vectors and create the collection with no embedding function,
  cosine space, and `anonymized_telemetry=False`.
- **Location.** The index is a directory given by `--index DIR`; there is no default. It is a derived,
  rebuildable copy of the trace database. Readers (`search`) never create a directory.
- **Identity and duplicates.** Chroma id = `run_id/step_id/attempt_no`; metadata holds those fields plus the
  text hash. Re-indexing upserts by id and skips unchanged hashes, so running it twice changes nothing. No new
  trace-database table, so no migration.
- **Traceability.** Every hit is re-read from SQLite (`TraceStore.open_existing`) and its document rebuilt and
  re-hashed. Result: `ok`, `source_missing` (row gone) or `source_changed` (hash differs). Only `ok` hits are
  returned as results; the others are listed separately as unverifiable and never presented as supporting
  records.
- **Model clash.** The collection metadata records model name, revision, dimension and text-format version. Any
  mismatch with the embedder in use, or a vector of the wrong dimension, is refused with a fixed message
  telling the user to build a new index in another directory. Nothing is mixed or silently rebuilt.
- **Weak results.** Default top-k 5 and a minimum cosine similarity (optional `COGMESH_MEMORY_MIN_SIMILARITY`,
  `--min-similarity`; the default is chosen from the opt-in live test and recorded here when known). Hits below
  it are counted and dropped. If none remain the output says "no sufficiently similar experience" (exit 0,
  empty list in `--json`). Every result carries the note that similarity is not evidence of correctness.
- **CLI.** `memory index --db TRACE --index DIR [--json]` and `memory search QUERY --db TRACE --index DIR
  [--k N] [--min-similarity X] [--json]`. New exit code 11 for an unusable, missing or incompatible index; trace
  store errors keep exit 8. These commands never read provider configuration.
- **Errors.** `MemoryIndexError` messages are fixed text; they never contain document text, the query, vectors
  or exception text (same rule as ADR-0011/0012).

## Alternatives considered
- Index table inside the trace database: stronger bookkeeping, but makes `memory index` a writer to the
  authoritative store and needs the first real migration. Rejected for now.
- Optional `[memory]` extra: keeps the base install light, but adds missing-extra paths to tests and docs.
- ONNX MiniLM through `onnxruntime` only (no torch): far smaller, but departs from the planned stack.
- `bge-small-en-v1.5` (MIT, ~133 MB): often better quality, needs a query prefix; MiniLM chosen as the standard
  small model.
- `--rebuild` flag for model changes: convenient, but a silent rebuild is exactly what must not happen; a new
  directory is explicit.
- Succeeded-only or deterministic-only documents: would make failed experiences (for example
  `provider_unavailable`) unrecallable, or exclude model-written outputs entirely.

## Consequences
- Installation gets much heavier (torch, onnxruntime, grpc, opentelemetry, kubernetes through Chroma). CI time
  and size must be measured on the first run after the change.
- The index can go stale; `memory index` must be re-run after new runs, and `search` reports staleness per hit.
- Embedding the output text means model-written (`model_unverified`) text is searchable; hits show that basis.
- Redaction is pattern-based (ADR-0005), so a secret it misses would also be embedded; the index directory must
  be treated like the trace database.
- Only one model is supported; changing it needs a new ADR amendment and a new index directory.
- Limits not yet verified: very large traces, concurrent indexers, other platforms, non-English text.

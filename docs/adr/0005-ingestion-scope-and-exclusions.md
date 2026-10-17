# ADR-0005: Ingestion scope, exclusions and the never-execute rule

## Status
Accepted

## Date
2026-10-16

## Context
v0.2.0 must read an application's source, configuration, manifests and documentation without
executing anything, keep traceable source references, and avoid ingesting secrets or generated
files. Ingested content is untrusted data and may contain prompt-injection text.

An earlier attempt (commit `ed71a4f`, branch `feature/v0.2.0-application-ingestion`) blocked
secrets by file name only and treated only `.py` as source. This ADR replaces that design.

## Decision
- **Input:** one local directory. A missing, non-directory, unreadable or symlinked root raises
  `IngestionError`, the only exception ingestion raises (CLI exit code 3). Every per-file problem
  becomes a `Diagnostic` and ingestion continues.
- **Classification by name, then extension; no guessing.** Kinds: `manifest`, `config`, `source`,
  `documentation`. The matching rule is stored on each record. No match means `unsupported_type`.
  Source: `.py` plus plain-text `.js .jsx .ts .tsx .go .java .rs .c .h .cpp .hpp .cs .rb .sh .ps1 .sql`.
- **Never executed.** Files are read as bytes (through the single function `ingest._read_bytes`)
  and decoded as UTF-8 (BOM allowed). Nothing is imported, run or compiled. Syntax checks are
  parse-only with the standard library: `json`, `tomllib`, `configparser`, and `ast.parse` for
  Python. Malformed files are still ingested with `parse_status="error"` and a diagnostic.
  YAML, XML and non-Python source are `unvalidated` (no new dependency).
- **Excluded without reading:**
  - Tooling/vendor/build directories (`.git`, `.venv`, `node_modules`, `__pycache__`, `build`,
    `dist`, `*.egg-info`, ...).
  - Generated files: lockfiles, `*.pyc`, `*.min.js`, `*.map`.
  - Sensitive file names: `.env` and `.env.*` (except `.env.example/.sample/.template`), keys and
    certificates, `id_rsa*`, `credentials*`, `secrets.*`, `.npmrc`, `.pypirc`, `.netrc`, ...
  - Symlinks and junctions (never followed), files over 1 MiB, binary files (NUL bytes), and
    anything past 5,000 examined files (reported as `file_limit_reached`).
- **Content secret scan.** Every decoded file is scanned for private-key blocks, AWS, GitHub,
  Groq, OpenAI-style and Slack tokens, and `api_key|secret|token|password` assignments with a
  value of 12+ characters. Matches are replaced by `[REDACTED:<rule>]`. The diagnostic names the
  rule and line, never the value. Obvious placeholders (`your...`, `changeme`, `${VAR}`, values
  containing `(`) are left alone so `.env.example` survives.
- **Source references.** Each record carries a `SourceRef` (resolved root, relative POSIX path,
  size, sha256 of the original bytes, taken before redaction) and the classification rule.
- Diagnostics never include file content, including parser error text.

## Alternatives considered
- Honor `.gitignore`: depends on git and a parser; ingestion rules should be explicit and
  testable on their own. May be added later as an extra signal.
- Skip files that contain secrets instead of redacting: loses the rest of the file's evidence.
- Entropy-based secret detection: more false positives and scope. Deferred.
- YAML validation via PyYAML: a new dependency for a syntax check only. Deferred.

## Consequences
- Secret detection is pattern-based and **not exhaustive**. A secret that matches no rule, or a
  password containing `(`, can still be ingested. Treat ingested content as potentially sensitive.
- Lockfiles are excluded as generated; v0.3.0 may need to revisit that for dependency resolution.
- Ingested records are in-memory only; persistence arrives with the trace store (v0.6.0).
- Only local directories are supported. No archives, git URLs or remote sources.

## Amendment 2026-10-17: more secret rules and an entropy check
Accepted by the user before coding. Still best effort, not exhaustive.
- New format rules: `google_api_key`, `stripe_key`, `npm_token`, `jwt`, and `url_credentials` (only the
  password in `scheme://user:password@host` is replaced).
- New `high_entropy_token`: an unbroken run of 32 or more base64/URL-safe characters with lower case,
  upper case and a digit, Shannon entropy of at least 4.2 bits per character, and no alphabet-like run of
  six consecutive characters. Hex-only runs (hashes) are never flagged. Named rules win over it.
- Known costs: it can redact an innocent random id or a path component (seen once in this repository's
  own `.claude` settings), and it misses short, low-entropy or hex-only secrets.
- Reports still hold only the rule name and line, never the value.

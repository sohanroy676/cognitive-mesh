"""Read-only ingestion of a local application directory.

Ingested files are data. They are read as bytes and decoded; nothing is imported, executed or
compiled. Every skipped, altered or failed file yields a Diagnostic instead of an exception.
"""

import ast
import configparser
import hashlib
import json
import os
import tomllib
import warnings
from collections.abc import Callable
from pathlib import Path

from cognitive_mesh.ingestion.models import (
    Diagnostic,
    DiagnosticCode,
    IngestedFile,
    IngestionError,
    IngestionResult,
    ParseStatus,
    Severity,
    SourceRef,
)
from cognitive_mesh.ingestion.rules import (
    Classification,
    classify,
    generated_rule,
    is_excluded_dir,
    sensitive_rule,
)
from cognitive_mesh.ingestion.secrets import redact_secrets

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_FILES = 5000


def _read_bytes(path: str, limit: int) -> bytes:
    """The only place file contents are read. Reads at most limit + 1 bytes."""
    with open(path, "rb") as handle:  # noqa: PTH123
        return handle.read(limit + 1)


def _check_ini(text: str) -> None:
    configparser.ConfigParser(interpolation=None).read_string(text)


def _check_python(text: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ast.parse(text)  # parses only; never compiles or executes


# Syntax checkers by suffix. Each raises on malformed input.
_CHECKERS: dict[str, tuple[str, Callable[[str], object]]] = {
    ".json": ("JSON", json.loads),
    ".toml": ("TOML", tomllib.loads),
    ".lock": ("TOML", tomllib.loads),  # only uv.lock is classified; it is TOML
    ".ini": ("INI", _check_ini),
    ".cfg": ("INI", _check_ini),
    ".py": ("Python", _check_python),
}


class _Collector:
    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    def add(
        self,
        path: str,
        code: DiagnosticCode,
        severity: Severity,
        message: str,
        rule: str | None = None,
    ) -> None:
        self.diagnostics.append(
            Diagnostic(path=path, code=code, severity=severity, rule=rule, message=message)
        )


def _parse_status(rel: str, text: str, sink: _Collector) -> ParseStatus:
    """Syntax-check by suffix. Messages carry the line number at most, never file content."""
    suffix = os.path.splitext(rel)[1].lower()  # noqa: PTH122
    checker = _CHECKERS.get(suffix)
    if checker is None:
        return ParseStatus.UNVALIDATED
    label, check = checker
    try:
        check(text)
    except (ValueError, SyntaxError, configparser.Error, RecursionError, MemoryError) as exc:
        line = getattr(exc, "lineno", None)
        where = f" near line {line}" if isinstance(line, int) else ""
        sink.add(rel, DiagnosticCode.MALFORMED, "warning", f"invalid {label}{where}")
        return ParseStatus.ERROR
    return ParseStatus.OK


def _ingest_file(
    root: str,
    rel: str,
    abs_path: str,
    size: int,
    cls: Classification,
    max_bytes: int,
    sink: _Collector,
) -> IngestedFile | None:
    if size > max_bytes:
        sink.add(rel, DiagnosticCode.TOO_LARGE, "warning", f"over {max_bytes} bytes; not read")
        return None
    try:
        data = _read_bytes(abs_path, max_bytes)
    except OSError as exc:
        sink.add(rel, DiagnosticCode.UNREADABLE, "error", f"cannot read ({type(exc).__name__})")
        return None
    if len(data) > max_bytes:  # grew after the stat
        sink.add(rel, DiagnosticCode.TOO_LARGE, "warning", f"over {max_bytes} bytes; not kept")
        return None
    if b"\x00" in data:
        sink.add(rel, DiagnosticCode.BINARY, "warning", "contains NUL bytes; treated as binary")
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        sink.add(rel, DiagnosticCode.UNDECODABLE, "warning", "not valid UTF-8")
        return None

    status = _parse_status(rel, text, sink)
    redacted, redactions = redact_secrets(text)
    for red in redactions:
        sink.add(
            rel,
            DiagnosticCode.SECRET_REDACTED,
            "warning",
            f"possible secret redacted at line {red.line}",
            rule=red.rule,
        )
    return IngestedFile(
        ref=SourceRef(
            root=root, path=rel, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
        ),
        kind=cls.kind,
        language=cls.language,
        matched_rule=cls.rule,
        parse_status=status,
        content=redacted,
        redactions=redactions,
    )


def _validate_root(root: Path) -> Path:
    if root.is_symlink() or root.is_junction():
        raise IngestionError(f"ingestion root is a symlink or junction: {root}")
    if not root.exists():
        raise IngestionError(f"ingestion root does not exist: {root}")
    if not root.is_dir():
        raise IngestionError(f"ingestion root is not a directory: {root}")
    try:
        with os.scandir(root):
            pass
    except OSError as exc:
        raise IngestionError(
            f"ingestion root is not readable: {root} ({type(exc).__name__})"
        ) from exc
    return root.resolve()


def _handle_file(
    entry: os.DirEntry[str],
    rel: str,
    root_str: str,
    max_file_bytes: int,
    sink: _Collector,
) -> IngestedFile | None:
    sensitive = sensitive_rule(entry.name)
    if sensitive is not None:
        sink.add(
            rel,
            DiagnosticCode.EXCLUDED_SENSITIVE,
            "info",
            "sensitive file name; not opened",
            rule=sensitive,
        )
        return None
    generated = generated_rule(entry.name)
    if generated is not None:
        sink.add(
            rel,
            DiagnosticCode.EXCLUDED_GENERATED,
            "info",
            "generated file; not read",
            rule=generated,
        )
        return None
    cls = classify(entry.name)
    if cls is None:
        sink.add(rel, DiagnosticCode.UNSUPPORTED_TYPE, "info", "unsupported file type")
        return None
    try:
        size = entry.stat(follow_symlinks=False).st_size
    except OSError as exc:
        sink.add(rel, DiagnosticCode.UNREADABLE, "error", f"cannot stat ({type(exc).__name__})")
        return None
    return _ingest_file(root_str, rel, entry.path, size, cls, max_file_bytes, sink)


def ingest_application(
    root: str | Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> IngestionResult:
    """Ingest a local directory. Raises IngestionError only if the root itself is unusable."""
    resolved = _validate_root(Path(root))
    root_str = str(resolved)
    sink = _Collector()
    files: list[IngestedFile] = []
    examined = 0
    pending = [resolved]

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name.casefold())
        except OSError as exc:
            rel_dir = directory.relative_to(resolved).as_posix()
            sink.add(
                rel_dir, DiagnosticCode.UNREADABLE, "error", f"cannot list ({type(exc).__name__})"
            )
            continue
        subdirs: list[Path] = []
        for entry in entries:
            rel = Path(entry.path).relative_to(resolved).as_posix()
            if entry.is_symlink() or entry.is_junction():
                sink.add(rel, DiagnosticCode.SYMLINK_SKIPPED, "warning", "link not followed")
            elif entry.is_dir(follow_symlinks=False):
                if is_excluded_dir(entry.name):
                    sink.add(rel, DiagnosticCode.EXCLUDED_DIR, "info", "excluded directory")
                else:
                    subdirs.append(Path(entry.path))
            elif not entry.is_file(follow_symlinks=False):
                sink.add(rel, DiagnosticCode.UNSUPPORTED_TYPE, "info", "not a regular file")
            else:
                if examined >= max_files:
                    sink.add(
                        rel,
                        DiagnosticCode.FILE_LIMIT_REACHED,
                        "warning",
                        f"stopped after examining {max_files} files",
                    )
                    return IngestionResult(
                        root=root_str, files=tuple(files), diagnostics=tuple(sink.diagnostics)
                    )
                examined += 1
                ingested = _handle_file(entry, rel, root_str, max_file_bytes, sink)
                if ingested is not None:
                    files.append(ingested)
        pending.extend(reversed(subdirs))

    return IngestionResult(root=root_str, files=tuple(files), diagnostics=tuple(sink.diagnostics))

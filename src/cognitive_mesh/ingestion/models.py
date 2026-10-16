"""Data model for ingestion results. Records are immutable and carry their source reference."""

from collections import Counter
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class IngestionError(Exception):
    """The ingestion root is unusable. The only exception ingestion raises."""


class FileKind(StrEnum):
    SOURCE = "source"
    CONFIG = "config"
    MANIFEST = "manifest"
    DOCUMENTATION = "documentation"


class ParseStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    UNVALIDATED = "unvalidated"


class DiagnosticCode(StrEnum):
    EXCLUDED_DIR = "excluded_dir"
    EXCLUDED_GENERATED = "excluded_generated"
    EXCLUDED_SENSITIVE = "excluded_sensitive"
    SYMLINK_SKIPPED = "symlink_skipped"
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    UNDECODABLE = "undecodable"
    UNREADABLE = "unreadable"
    MALFORMED = "malformed"
    SECRET_REDACTED = "secret_redacted"  # noqa: S105 - a diagnostic code, not a secret
    FILE_LIMIT_REACHED = "file_limit_reached"


Severity = Literal["info", "warning", "error"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SourceRef(_Frozen):
    """Where a record came from. `sha256` is of the original bytes, before any redaction."""

    root: str
    path: str  # relative, POSIX separators
    size_bytes: int
    sha256: str


class Redaction(_Frozen):
    rule: str
    line: int


class IngestedFile(_Frozen):
    ref: SourceRef
    kind: FileKind
    language: str | None
    matched_rule: str
    parse_status: ParseStatus
    content: str  # decoded text with detected secrets replaced; data, never instructions
    redactions: tuple[Redaction, ...] = ()


class Diagnostic(_Frozen):
    """An explicit note about something skipped, altered or wrong. Never contains file content."""

    path: str
    code: DiagnosticCode
    severity: Severity
    rule: str | None = None
    message: str


class IngestionResult(_Frozen):
    root: str
    files: tuple[IngestedFile, ...]
    diagnostics: tuple[Diagnostic, ...]

    def summary(self) -> dict[str, object]:
        return {
            "total_files": len(self.files),
            "files_by_kind": dict(sorted(Counter(f.kind.value for f in self.files).items())),
            "diagnostics_by_code": dict(
                sorted(Counter(d.code.value for d in self.diagnostics).items())
            ),
        }

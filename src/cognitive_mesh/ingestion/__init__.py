"""Read-only application ingestion (v0.2.0)."""

from cognitive_mesh.ingestion.ingest import ingest_application
from cognitive_mesh.ingestion.models import (
    Diagnostic,
    DiagnosticCode,
    FileKind,
    IngestedFile,
    IngestionError,
    IngestionResult,
    ParseStatus,
    Redaction,
    SourceRef,
)

__all__ = [
    "Diagnostic",
    "DiagnosticCode",
    "FileKind",
    "IngestedFile",
    "IngestionError",
    "IngestionResult",
    "ParseStatus",
    "Redaction",
    "SourceRef",
    "ingest_application",
]

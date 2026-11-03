"""Read one ingested project into `ProjectFacts` (ADR-0037). Parse-only; nothing is executed."""

import ast
import posixpath

from cognitive_mesh.graph import js_parse
from cognitive_mesh.ingestion import FileKind, IngestedFile, IngestionResult, ParseStatus
from cognitive_mesh.ingestion.rules import ENV_TEMPLATES
from cognitive_mesh.workspace import config_names, contracts, schemas
from cognitive_mesh.workspace.facts import EnvRead, ProjectFacts
from cognitive_mesh.workspace.yaml_read import load_document

_DOC_SUFFIXES = (".json", ".yaml", ".yml")


def _python(facts: ProjectFacts, file: IngestedFile) -> None:
    try:
        tree = ast.parse(file.content)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return
    path = file.ref.path
    facts.routes.extend(contracts.python_routes(facts.project, path, tree))
    calls, unresolved = contracts.python_calls(facts.project, path, tree)
    facts.calls.extend(calls)
    facts.unresolved.extend(unresolved)
    facts.tables.extend(schemas.python_tables(facts.project, path, tree))
    config_names.python_env(facts, path, tree)


def _javascript(facts: ProjectFacts, file: IngestedFile) -> None:
    suffix = posixpath.splitext(file.ref.path)[1]
    if not js_parse.supports(suffix):
        return
    parsed = js_parse.parse_js_calls(suffix, file.content)
    path = file.ref.path
    facts.routes.extend(contracts.js_routes(facts.project, path, parsed))
    calls, unresolved = contracts.js_calls(facts.project, path, parsed)
    facts.calls.extend(calls)
    facts.unresolved.extend(unresolved)
    for read in parsed.env_reads:
        if read.name is None:
            where = contracts.site_of(facts.project, path, read.line, "(computed name)")
            facts.env_reads.append(EnvRead(None, where))
        else:
            config_names.add_read(facts, read.name, path, read.line)


def _document(facts: ProjectFacts, file: IngestedFile, base: str) -> None:
    text, path = file.content, file.ref.path
    wants_api = "openapi" in text or "swagger" in text
    wants_schema = base.endswith(".json") and "$schema" in text
    wants_compose = bool(config_names.COMPOSE_FILE.match(base))
    if not (wants_api or wants_schema or wants_compose):
        return
    doc = load_document(path, text)
    if doc is None:
        facts.notes.append(("unreadable_document", f"{path} could not be parsed"))
        return
    if wants_api:
        facts.routes.extend(contracts.openapi_routes(facts.project, path, text, doc))
    if wants_schema:
        tables, unresolved = schemas.json_schema(facts.project, path, doc)
        facts.tables.extend(tables)
        facts.unresolved.extend(unresolved)
    if wants_compose:
        config_names.compose_names(facts, path, text, doc)


def extract_project(project: str, result: IngestionResult) -> ProjectFacts:
    facts = ProjectFacts(project)
    for file in result.files:
        if file.parse_status is ParseStatus.ERROR and file.language != "python":
            continue
        base = posixpath.basename(file.ref.path).lower()
        if file.language == "python":
            _python(facts, file)
        elif file.language in ("javascript", "typescript"):
            _javascript(facts, file)
        elif file.language == "sql":
            facts.tables.extend(schemas.sql_tables(project, file.ref.path, file.content))
        elif file.language == "prisma":
            facts.tables.extend(schemas.prisma_tables(project, file.ref.path, file.content))
        elif file.kind is FileKind.CONFIG:
            if base in ENV_TEMPLATES:
                config_names.env_template_names(facts, file.ref.path, file.content)
            elif base == "dockerfile":
                config_names.dockerfile_names(facts, file.ref.path, file.content)
            elif base.endswith(_DOC_SUFFIXES):
                _document(facts, file, base)
    return facts

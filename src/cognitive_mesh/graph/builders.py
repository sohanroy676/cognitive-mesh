"""Pure builders: turn an IngestionResult into graph nodes, edges and diagnostics.

Nothing here touches the disk or executes anything. Only text already ingested (and redacted) is
parsed, and only with the standard library (`tomllib`, `json`, `ast.parse`). A relationship is
added only when a file states it; anything that cannot be resolved becomes an `unresolved` node.
"""

import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from posixpath import dirname

from cognitive_mesh.graph.models import (
    Edge,
    EdgeKind,
    Evidence,
    GraphDiagnostic,
    GraphDiagnosticCode,
    Node,
    NodeKind,
    Severity,
)
from cognitive_mesh.ingestion import FileKind, IngestedFile, IngestionResult, ParseStatus

_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_SPEC_START = ("<", ">", "=", "!", "~", ";", "@", "(")
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_MAX_ID_TEXT = 200


class _Accumulator:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, EdgeKind], list[Evidence]] = {}
        self.diagnostics: list[GraphDiagnostic] = []

    def node(self, node: Node) -> None:
        self.nodes.setdefault(node.id, node)

    def edge(self, source: str, target: str, kind: EdgeKind, evidence: Evidence) -> None:
        found = self.edges.setdefault((source, target, kind), [])
        if evidence not in found:
            found.append(evidence)

    def diag(self, path: str, code: GraphDiagnosticCode, severity: Severity, message: str) -> None:
        self.diagnostics.append(
            GraphDiagnostic(path=path, code=code, severity=severity, message=message)
        )


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_ID_TEXT else text[:_MAX_ID_TEXT] + "..."


def _find_line(content: str, needle: str) -> int | None:
    for number, line in enumerate(content.splitlines(), 1):
        if needle in line:
            return number
    return None


def normalize_pypi(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(raw: str) -> tuple[str, str] | None:
    """Return (name, rest) for a plain PEP 508-style requirement, else None. Conservative."""
    match = _REQ_RE.match(raw.strip())
    if match is None:
        return None
    rest = match.group(3).strip()
    if rest and not rest.startswith(_SPEC_START):
        return None
    return match.group(1), (match.group(2) or "") + rest


def _component(acc: _Accumulator, file: IngestedFile, root: str, name: str | None) -> str:
    directory = dirname(file.ref.path) or "."
    cid = f"component:{directory}"
    fallback = Path(root).name if directory == "." else directory
    acc.node(Node(id=cid, kind=NodeKind.COMPONENT, name=name or fallback, ref=file.ref))
    return cid


def _declare_pypi(
    acc: _Accumulator,
    file: IngestedFile,
    cid: str,
    raw: str,
    scope: str,
    rule: str,
    line: int | None,
) -> None:
    parsed = _parse_requirement(raw)
    evidence = Evidence(ref=file.ref, line=line, rule=rule, detail=f"{_clip(raw)} [{scope}]")
    if parsed is None:
        acc.diag(
            file.ref.path,
            GraphDiagnosticCode.UNPARSEABLE_REQUIREMENT,
            "warning",
            f"line {line}: requirement not understood; recorded as unresolved"
            if line
            else "requirement not understood; recorded as unresolved",
        )
        uid = f"unresolved:requirement:{_clip(raw.strip())}"
        acc.node(Node(id=uid, kind=NodeKind.UNRESOLVED, name=_clip(raw.strip()), ref=file.ref))
        acc.edge(cid, uid, EdgeKind.DECLARES, evidence)
        return
    name, _ = parsed
    pid = f"pkg:pypi:{normalize_pypi(name)}"
    acc.node(Node(id=pid, kind=NodeKind.PACKAGE, name=name, ecosystem="pypi", ref=file.ref))
    acc.edge(cid, pid, EdgeKind.DECLARES, evidence)


def _requirement_list(
    acc: _Accumulator, file: IngestedFile, cid: str, value: object, scope: str, rule: str
) -> None:
    if not isinstance(value, list):
        acc.diag(file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "warning", f"{rule} is not a list")
        return
    for item in value:
        if isinstance(item, str):
            _declare_pypi(acc, file, cid, item, scope, rule, _find_line(file.content, item))
        else:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.SKIPPED_ENTRY,
                "info",
                f"non-string entry in {rule} skipped (e.g. include-group)",
            )


def _pyproject(acc: _Accumulator, file: IngestedFile, root: str) -> None:
    try:
        data = tomllib.loads(file.content)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError):
        acc.diag(file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "error", "cannot parse TOML")
        return
    project = data.get("project")
    groups = data.get("dependency-groups")
    if not isinstance(project, dict) and not isinstance(groups, dict):
        acc.diag(
            file.ref.path,
            GraphDiagnosticCode.UNSUPPORTED_MANIFEST,
            "warning",
            "no [project] or [dependency-groups] table; other layouts (e.g. poetry) are not parsed",
        )
        return
    name = project.get("name") if isinstance(project, dict) else None
    cid = _component(acc, file, root, name if isinstance(name, str) else None)
    if isinstance(project, dict):
        if "dependencies" in project:
            _requirement_list(
                acc, file, cid, project["dependencies"], "runtime", "pyproject:project.dependencies"
            )
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group, items in sorted(optional.items()):
                _requirement_list(
                    acc,
                    file,
                    cid,
                    items,
                    f"optional:{group}",
                    "pyproject:project.optional-dependencies",
                )
        elif optional is not None:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "warning",
                "project.optional-dependencies is not a table",
            )
    if isinstance(groups, dict):
        for group, items in sorted(groups.items()):
            _requirement_list(
                acc, file, cid, items, f"group:{group}", "pyproject:dependency-groups"
            )


def _requirements_txt(acc: _Accumulator, file: IngestedFile, root: str) -> None:
    cid = _component(acc, file, root, None)
    for number, line in enumerate(file.content.splitlines(), 1):
        text = re.sub(r"(^|\s)#.*$", "", line).strip()
        if not text:
            continue
        if text.startswith("-") or ("://" in text and "@" not in text):
            # -r / -e / --index-url / bare URLs: not a plain requirement, never guessed at
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.UNPARSEABLE_REQUIREMENT,
                "warning",
                f"line {number}: option or URL line not followed; recorded as unresolved",
            )
            uid = f"unresolved:requirement:{_clip(text)}"
            acc.node(Node(id=uid, kind=NodeKind.UNRESOLVED, name=_clip(text), ref=file.ref))
            evidence = Evidence(
                ref=file.ref, line=number, rule="requirements:line", detail=_clip(text)
            )
            acc.edge(cid, uid, EdgeKind.DECLARES, evidence)
            continue
        _declare_pypi(acc, file, cid, text, "runtime", "requirements:line", number)


def _package_json(acc: _Accumulator, file: IngestedFile, root: str) -> None:
    try:
        data = json.loads(file.content)
    except (ValueError, RecursionError):
        acc.diag(file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "error", "cannot parse JSON")
        return
    if not isinstance(data, dict):
        acc.diag(
            file.ref.path,
            GraphDiagnosticCode.PARSE_ERROR,
            "error",
            "top-level JSON is not an object",
        )
        return
    name = data.get("name")
    cid = _component(acc, file, root, name if isinstance(name, str) else None)
    for section in _NPM_SECTIONS:
        if section not in data:
            continue
        table = data[section]
        if not isinstance(table, dict):
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "warning",
                f"{section} is not an object",
            )
            continue
        for dep, spec in sorted(table.items()):
            pid = f"pkg:npm:{dep}"
            acc.node(Node(id=pid, kind=NodeKind.PACKAGE, name=dep, ecosystem="npm", ref=file.ref))
            detail = f"{_clip(dep)}: {_clip(json.dumps(spec))} [{section}]"
            evidence = Evidence(
                ref=file.ref,
                line=_find_line(file.content, json.dumps(dep)),
                rule=f"package.json:{section}",
                detail=detail,
            )
            acc.edge(cid, pid, EdgeKind.DECLARES, evidence)


def _is_requirements(name: str) -> bool:
    return name.startswith("requirements") and name.endswith(".txt")


def _manifests(acc: _Accumulator, result: IngestionResult) -> None:
    manifests = [f for f in result.files if f.kind is FileKind.MANIFEST]
    if not manifests:
        acc.diag(
            ".",
            GraphDiagnosticCode.NO_MANIFEST,
            "warning",
            "no dependency manifest was ingested; this does not mean the application has no "
            "dependencies",
        )
    for file in manifests:
        base = file.ref.path.rsplit("/", 1)[-1].lower()
        if file.parse_status is ParseStatus.ERROR:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "error",
                "ingestion could not parse this manifest; no relationships taken from it",
            )
        elif base == "pyproject.toml":
            _pyproject(acc, file, result.root)
        elif _is_requirements(base):
            _requirements_txt(acc, file, result.root)
        elif base == "package.json":
            _package_json(acc, file, result.root)
        else:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.UNSUPPORTED_MANIFEST,
                "warning",
                "manifest type is ingested but its dependencies are not parsed in v0.3.0",
            )


def _dotted_names(path: str) -> list[str]:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    names = [".".join(parts)]
    if parts[0] == "src" and len(parts) > 1:
        names.append(".".join(parts[1:]))
    return names


class _ModuleIndex:
    def __init__(self, paths: list[str]) -> None:
        self.paths = frozenset(paths)
        self.by_dotted: dict[str, set[str]] = {}
        for path in paths:
            for dotted in _dotted_names(path):
                self.by_dotted.setdefault(dotted, set()).add(path)

    def exact(self, dotted: str) -> set[str]:
        return self.by_dotted.get(dotted, set())

    def prefix(self, dotted: str) -> set[str]:
        segments = dotted.split(".")
        for end in range(len(segments), 0, -1):
            found = self.exact(".".join(segments[:end]))
            if found:
                return found
        return set()

    def relative_file(self, base: str, rel: str) -> str | None:
        stem = f"{base}/{rel}" if base else rel
        for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
            if candidate in self.paths:
                return candidate
        return None


def _import_statement(node: ast.Import | ast.ImportFrom) -> str:
    return _clip(ast.unparse(node))


def _python_imports(acc: _Accumulator, result: IngestionResult) -> None:
    pyfiles = [f for f in result.files if f.kind is FileKind.SOURCE and f.language == "python"]
    index = _ModuleIndex([f.ref.path for f in pyfiles])
    declared = {n.id for n in acc.nodes.values() if n.kind is NodeKind.PACKAGE}
    for file in pyfiles:
        acc.node(
            Node(
                id=f"module:{file.ref.path}", kind=NodeKind.MODULE, name=file.ref.path, ref=file.ref
            )
        )
    for file in pyfiles:
        if file.parse_status is not ParseStatus.OK:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "error",
                "ingestion could not parse this source file; no imports taken from it",
            )
            continue
        try:
            tree = ast.parse(file.content)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            acc.diag(file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "error", "cannot parse Python")
            continue
        source = f"module:{file.ref.path}"
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    _link_absolute(acc, file, source, stmt, alias.name, index, declared)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.level:
                    _link_relative(acc, file, source, stmt, index)
                else:
                    _link_from(acc, file, source, stmt, index, declared)


def _evidence(file: IngestedFile, stmt: ast.Import | ast.ImportFrom) -> Evidence:
    return Evidence(
        ref=file.ref, line=stmt.lineno, rule="python:import", detail=_import_statement(stmt)
    )


def _link_found(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.Import | ast.ImportFrom,
    found: set[str],
    label: str,
) -> None:
    if len(found) > 1:
        acc.diag(
            file.ref.path,
            GraphDiagnosticCode.AMBIGUOUS_IMPORT,
            "warning",
            f"line {stmt.lineno}: import of {_clip(label)} matches several files; "
            "recorded as unresolved",
        )
        _link_unresolved(acc, file, source, stmt, label)
        return
    acc.edge(source, f"module:{next(iter(found))}", EdgeKind.IMPORTS, _evidence(file, stmt))


def _link_unresolved(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.Import | ast.ImportFrom,
    label: str,
    candidate: str | None = None,
) -> None:
    uid = f"unresolved:import:{_clip(label)}"
    acc.node(
        Node(
            id=uid,
            kind=NodeKind.UNRESOLVED,
            name=_clip(label),
            ref=file.ref,
            candidate_package=candidate,
        )
    )
    acc.edge(source, uid, EdgeKind.IMPORTS, _evidence(file, stmt))


def _link_external(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.Import | ast.ImportFrom,
    dotted: str,
    declared: set[str],
) -> None:
    top = dotted.split(".")[0]
    if top in sys.stdlib_module_names:
        sid = f"stdlib:{top}"
        acc.node(Node(id=sid, kind=NodeKind.STDLIB, name=top))
        acc.edge(source, sid, EdgeKind.IMPORTS, _evidence(file, stmt))
        return
    candidate = f"pkg:pypi:{normalize_pypi(top)}"
    _link_unresolved(acc, file, source, stmt, top, candidate if candidate in declared else None)


def _link_absolute(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.Import | ast.ImportFrom,
    dotted: str,
    index: _ModuleIndex,
    declared: set[str],
) -> None:
    found = index.prefix(dotted)
    if found:
        _link_found(acc, file, source, stmt, found, dotted)
    else:
        _link_external(acc, file, source, stmt, dotted, declared)


def _link_from(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.ImportFrom,
    index: _ModuleIndex,
    declared: set[str],
) -> None:
    module = stmt.module or ""
    if not module:
        return
    module_linked = False
    for alias in stmt.names:
        sub = index.exact(f"{module}.{alias.name}") if alias.name != "*" else set()
        if sub:
            _link_found(acc, file, source, stmt, sub, f"{module}.{alias.name}")
        elif not module_linked:
            module_linked = True
            _link_absolute(acc, file, source, stmt, module, index, declared)


def _link_relative(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: ast.ImportFrom,
    index: _ModuleIndex,
) -> None:
    label = "." * stmt.level + (stmt.module or "")
    base = dirname(file.ref.path)
    for _ in range(stmt.level - 1):
        if not base:
            _link_unresolved(acc, file, source, stmt, label)
            return
        base = dirname(base)
    if stmt.module:
        rel = stmt.module.replace(".", "/")
        module_dir = f"{base}/{rel}" if base else rel
        target = index.relative_file(base, rel)
        if target is None:
            _link_unresolved(acc, file, source, stmt, label)
            return
        acc.edge(source, f"module:{target}", EdgeKind.IMPORTS, _evidence(file, stmt))
        for alias in stmt.names:
            sub = index.relative_file(module_dir, alias.name) if alias.name != "*" else None
            if sub and sub != target:
                acc.edge(source, f"module:{sub}", EdgeKind.IMPORTS, _evidence(file, stmt))
        return
    package_init = index.relative_file(base, "__init__")
    for alias in stmt.names:
        sub = index.relative_file(base, alias.name) if alias.name != "*" else None
        target = sub or package_init
        if target is None:
            _link_unresolved(acc, file, source, stmt, label)
        else:
            acc.edge(source, f"module:{target}", EdgeKind.IMPORTS, _evidence(file, stmt))


def _contains(acc: _Accumulator) -> None:
    components = {n.id: n for n in acc.nodes.values() if n.kind is NodeKind.COMPONENT}
    dirs = {cid: cid.removeprefix("component:") for cid in components}
    for node in list(acc.nodes.values()):
        if node.kind is not NodeKind.MODULE or node.ref is None:
            continue
        owners = [(cid, d) for cid, d in dirs.items() if d == "." or node.name.startswith(d + "/")]
        if not owners:
            continue
        cid, directory = max(owners, key=lambda item: 0 if item[1] == "." else len(item[1]))
        evidence = Evidence(
            ref=node.ref,
            line=None,
            rule="rule:under_manifest_dir",
            detail=f"{node.name} is under directory {directory}",
        )
        acc.edge(cid, node.id, EdgeKind.CONTAINS, evidence)


def build(result: IngestionResult) -> tuple[list[Node], list[Edge], list[GraphDiagnostic]]:
    acc = _Accumulator()
    _manifests(acc, result)
    _python_imports(acc, result)
    _contains(acc)
    acc.diag(
        ".",
        GraphDiagnosticCode.TRANSITIVE_UNKNOWN,
        "info",
        "lockfiles are excluded from ingestion: dependencies of declared packages are unknown, "
        "not absent",
    )
    nodes = sorted(acc.nodes.values(), key=lambda n: n.id)
    edges = [
        Edge(source=s, target=t, kind=k, evidence=tuple(ev))
        for (s, t, k), ev in sorted(
            acc.edges.items(), key=lambda item: (item[0][0], item[0][1], item[0][2].value)
        )
    ]
    return nodes, edges, acc.diagnostics

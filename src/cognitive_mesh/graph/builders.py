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
from collections.abc import Callable
from pathlib import Path
from posixpath import dirname, join, normpath, splitext

from cognitive_mesh.graph import js_parse
from cognitive_mesh.graph.lockfiles import (
    LockfileProblem,
    LockParse,
    parse_package_lock,
    parse_uv_lock,
)
from cognitive_mesh.graph.models import (
    Ecosystem,
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
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_JS_LANGUAGES = frozenset({"javascript", "typescript"})
_Located = ast.Import | ast.ImportFrom | ast.Call


class _Accumulator:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, EdgeKind], list[Evidence]] = {}
        self.diagnostics: list[GraphDiagnostic] = []
        self.locked: set[str] = set()  # ecosystems with at least one parsed lockfile

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
        elif base == "uv.lock":
            _lockfile(acc, file, parse_uv_lock, "pypi")
        elif base == "package-lock.json":
            _lockfile(acc, file, parse_package_lock, "npm")
        else:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.UNSUPPORTED_MANIFEST,
                "warning",
                "manifest type is ingested but its dependencies are not parsed",
            )


def _lockfile(
    acc: _Accumulator,
    file: IngestedFile,
    parser: Callable[[IngestedFile], LockParse],
    ecosystem: Ecosystem,
) -> None:
    try:
        parsed = parser(file)
    except LockfileProblem as exc:
        acc.diag(
            file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "error", f"{exc}; no relationships"
        )
        return
    for pkg in parsed.packages:
        acc.node(
            Node(
                id=pkg.id,
                kind=NodeKind.PACKAGE,
                name=pkg.name,
                ecosystem=pkg.ecosystem,
                ref=file.ref,
            )
        )
    for dep in parsed.dependencies:
        acc.edge(dep.source, dep.target, EdgeKind.REQUIRES, dep.evidence)
    acc.locked.add(ecosystem)


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


def _import_statement(node: _Located) -> str:
    return _clip(ast.unparse(node))


def _dynamic_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the importlib module and to importlib.import_module in this file."""
    modules: set[str] = set()
    functions: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == "importlib":
                    modules.add(alias.asname or "importlib")
                elif alias.name.startswith("importlib.") and alias.asname is None:
                    modules.add("importlib")
        elif isinstance(stmt, ast.ImportFrom) and stmt.module == "importlib" and not stmt.level:
            functions.update(a.asname or a.name for a in stmt.names if a.name == "import_module")
    return modules, functions


def _is_dynamic_import(call: ast.Call, modules: set[str], functions: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "__import__" or func.id in functions
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _link_computed(
    acc: _Accumulator, file: IngestedFile, source: str, evidence: Evidence, label: str
) -> None:
    """A dynamic import whose target is not a plain literal: visible, never guessed."""
    uid = f"unresolved:dynamic:{file.ref.path}:{evidence.line}"
    acc.node(Node(id=uid, kind=NodeKind.UNRESOLVED, name=_clip(label), ref=file.ref))
    acc.edge(source, uid, EdgeKind.IMPORTS, evidence)
    acc.diag(
        file.ref.path,
        GraphDiagnosticCode.COMPUTED_IMPORT,
        "warning",
        f"line {evidence.line}: dynamic import target is not a plain literal; recorded as "
        "unresolved",
    )


def _link_dynamic_python(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    call: ast.Call,
    index: _ModuleIndex,
    declared: set[str],
) -> None:
    first = call.args[0] if call.args else None
    literal = first.value if isinstance(first, ast.Constant) else None
    if isinstance(literal, str) and literal and not literal.startswith("."):
        _link_absolute(acc, file, source, call, literal, index, declared)
    else:  # computed, empty, relative or keyword-only: the target cannot be known statically
        _link_computed(acc, file, source, _evidence(file, call), _import_statement(call))


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
        modules, functions = _dynamic_aliases(tree)
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    _link_absolute(acc, file, source, stmt, alias.name, index, declared)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.level:
                    _link_relative(acc, file, source, stmt, index)
                else:
                    _link_from(acc, file, source, stmt, index, declared)
            elif isinstance(stmt, ast.Call) and _is_dynamic_import(stmt, modules, functions):
                _link_dynamic_python(acc, file, source, stmt, index, declared)


def _evidence(file: IngestedFile, stmt: _Located) -> Evidence:
    rule = "python:dynamic-import" if isinstance(stmt, ast.Call) else "python:import"
    return Evidence(ref=file.ref, line=stmt.lineno, rule=rule, detail=_import_statement(stmt))


def _link_found(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    stmt: _Located,
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
    stmt: _Located,
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
    stmt: _Located,
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
    stmt: _Located,
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


def _js_relative(spec: str, from_path: str, paths: frozenset[str]) -> list[str] | None:
    """Ingested files a relative specifier can mean, or None if it escapes the root."""
    joined = normpath(join(dirname(from_path), spec))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    if joined == ".":
        candidates = [f"index{ext}" for ext in _JS_EXTENSIONS]
    else:
        candidates = [
            joined,
            *(joined + ext for ext in _JS_EXTENSIONS),
            *(f"{joined}/index{ext}" for ext in _JS_EXTENSIONS),
        ]
    return list(dict.fromkeys(c for c in candidates if c in paths))


def _js_package_name(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _link_js(
    acc: _Accumulator,
    file: IngestedFile,
    source: str,
    spec: str,
    evidence: Evidence,
    paths: frozenset[str],
    declared: set[str],
) -> None:
    if spec in (".", "..") or spec.startswith(("./", "../")):
        found = _js_relative(spec, file.ref.path, paths)
        if found and len(found) == 1:
            acc.edge(source, f"module:{found[0]}", EdgeKind.IMPORTS, evidence)
            return
        if found:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.AMBIGUOUS_IMPORT,
                "warning",
                f"line {evidence.line}: import of {_clip(spec)} matches several files; "
                "recorded as unresolved",
            )
        uid, label, candidate = f"unresolved:import:js:{_clip(spec)}", spec, None
    elif spec.startswith("node:") and len(spec) > len("node:"):
        sid = f"stdlib:node:{_clip(spec[5:])}"
        acc.node(Node(id=sid, kind=NodeKind.STDLIB, name=_clip(spec)))
        acc.edge(source, sid, EdgeKind.IMPORTS, evidence)
        return
    else:
        name = _js_package_name(spec)
        candidate = f"pkg:npm:{name}" if f"pkg:npm:{name}" in declared else None
        uid, label = f"unresolved:import:js:{_clip(name)}", name
    acc.node(
        Node(
            id=uid,
            kind=NodeKind.UNRESOLVED,
            name=_clip(label),
            ref=file.ref,
            candidate_package=candidate,
        )
    )
    acc.edge(source, uid, EdgeKind.IMPORTS, evidence)


def _js_imports(acc: _Accumulator, result: IngestionResult) -> None:
    jsfiles = [f for f in result.files if f.kind is FileKind.SOURCE and f.language in _JS_LANGUAGES]
    if not jsfiles:
        return
    paths = frozenset(f.ref.path for f in jsfiles)
    declared = {n.id for n in acc.nodes.values() if n.kind is NodeKind.PACKAGE}
    for file in jsfiles:
        acc.node(
            Node(
                id=f"module:{file.ref.path}", kind=NodeKind.MODULE, name=file.ref.path, ref=file.ref
            )
        )
    for file in jsfiles:
        if file.parse_status is ParseStatus.ERROR:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "error",
                "ingestion could not parse this source file; no imports taken from it",
            )
            continue
        try:
            parsed = js_parse.parse_js_imports(splitext(file.ref.path)[1], file.content)
        except (ValueError, RuntimeError, MemoryError):
            acc.diag(file.ref.path, GraphDiagnosticCode.PARSE_ERROR, "error", "cannot parse JS/TS")
            continue
        if parsed.has_error:
            acc.diag(
                file.ref.path,
                GraphDiagnosticCode.PARSE_ERROR,
                "warning",
                "syntax errors in JS/TS; imports taken only from error-free regions",
            )
        source = f"module:{file.ref.path}"
        for item in parsed.imports:
            evidence = Evidence(
                ref=file.ref, line=item.line, rule=item.rule, detail=_clip(item.cited)
            )
            if not item.specifier:  # computed, or an empty specifier
                _link_computed(acc, file, source, evidence, item.cited)
            else:
                _link_js(acc, file, source, item.specifier, evidence, paths, declared)
    acc.diag(
        ".",
        GraphDiagnosticCode.JS_RESOLUTION_LIMITED,
        "info",
        "JS/TS imports are resolved only to ingested files by relative path; bundler aliases, "
        "tsconfig paths, package.json exports, .js to .ts remapping and .mjs/.cjs files are not "
        "modelled, so bare and aliased specifiers stay unresolved",
    )


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
    _js_imports(acc, result)
    _contains(acc)
    unlocked = sorted(
        {n.ecosystem for n in acc.nodes.values() if n.ecosystem and n.ecosystem not in acc.locked}
    )
    if unlocked or not acc.locked:
        scope = ", ".join(unlocked) + " packages" if unlocked else "any ecosystem"
        acc.diag(
            ".",
            GraphDiagnosticCode.TRANSITIVE_UNKNOWN,
            "info",
            f"no readable lockfile for {scope} (only uv.lock and package-lock.json v2/v3 are "
            "read): dependencies of declared packages are unknown, not absent",
        )
    nodes = sorted(acc.nodes.values(), key=lambda n: n.id)
    edges = [
        Edge(source=s, target=t, kind=k, evidence=tuple(ev))
        for (s, t, k), ev in sorted(
            acc.edges.items(), key=lambda item: (item[0][0], item[0][1], item[0][2].value)
        )
    ]
    return nodes, edges, acc.diagnostics

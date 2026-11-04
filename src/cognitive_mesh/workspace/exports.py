"""What a project publishes as a package: name, version and exported names (ADR-0041).

Parse-only. The package directory is found by layout, never by importing anything; a package that
cannot be found, or an export list that cannot be read in full, is reported as incomplete, never
guessed.
"""

import ast
import json
import posixpath
import re
import tomllib
from dataclasses import dataclass

from cognitive_mesh.graph import js_parse
from cognitive_mesh.ingestion import FileKind, IngestedFile, IngestionResult, ParseStatus
from cognitive_mesh.workspace.models import ManifestRef

_JS_CONDITIONS = ("import", "default", "require", "node")
_JS_INDEXES = ("src/index.ts", "src/index.js", "index.ts", "index.js")


@dataclass(frozen=True)
class PackageContract:
    ecosystem: str  # pypi | npm
    name: str  # as the manifest writes it
    version: str | None  # None when the manifest has none or it is dynamic
    manifest: ManifestRef
    entry: str | None  # path of the file the exports were read from
    exports: frozenset[str]
    exports_complete: bool


def import_root(name: str) -> str:
    """The top-level module name a PyPI distribution name usually imports as."""
    return re.sub(r"[-.]+", "_", name).lower()


def _files(result: IngestionResult) -> dict[str, IngestedFile]:
    return {f.ref.path: f for f in result.files}


def _line_of(content: str, needle: str) -> int | None:
    for number, line in enumerate(content.splitlines(), 1):
        if needle in line:
            return number
    return None


def _string_list(node: ast.expr) -> list[str] | None:
    if isinstance(node, ast.List | ast.Tuple):
        items = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        return items if len(items) == len(node.elts) else None
    return None


def python_exports(tree: ast.Module) -> tuple[frozenset[str], bool]:
    """A literal `__all__`, else the public top-level names. Complete unless a star import or a
    computed `__all__` makes the list unreliable."""
    public: set[str] = set()
    declared: list[str] | None = None
    complete = True
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            public.add(stmt.name)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "__all__" and stmt.value is not None:
                    listed = _string_list(stmt.value)
                    if listed is None:
                        complete = False
                    else:
                        declared = listed
                else:
                    public.add(target.id)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__":
                listed = _string_list(stmt.value)
                if listed is None:
                    complete = False
                else:
                    declared = [*(declared or []), *listed]
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name == "*":
                    complete = False
                else:
                    public.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                public.add(alias.asname or alias.name.split(".")[0])
    if declared is not None:
        return frozenset(declared), complete
    return frozenset(n for n in public if not n.startswith("_")), complete


def _python_contract(
    project: str, file: IngestedFile, files: dict[str, IngestedFile]
) -> PackageContract | None:
    try:
        table = tomllib.loads(file.content).get("project")
    except (ValueError, RecursionError):
        return None
    name = table.get("name") if isinstance(table, dict) else None
    if not isinstance(name, str) or not name:
        return None
    version = table.get("version") if isinstance(table, dict) else None
    base = posixpath.dirname(file.ref.path)
    module = import_root(name)
    entry = next(
        (
            path
            for path in (
                posixpath.join(base, "src", module, "__init__.py"),
                posixpath.join(base, module, "__init__.py"),
            )
            if path in files
        ),
        None,
    )
    ref = ManifestRef(
        project=project,
        path=file.ref.path,
        line=_line_of(file.content, "version") if isinstance(version, str) else None,
        detail=f"name = {name}",
    )
    exports: frozenset[str] = frozenset()
    complete = False
    if entry is not None:
        try:
            exports, complete = python_exports(ast.parse(files[entry].content))
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            exports, complete = frozenset(), False
    return PackageContract(
        "pypi", name, version if isinstance(version, str) else None, ref, entry, exports, complete
    )


def _js_entry_name(data: dict[str, object]) -> str | None:
    node: object = data.get("exports")
    if isinstance(node, dict) and "." in node:
        node = node["."]
    for _ in range(4):
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            node = next((node[k] for k in _JS_CONDITIONS if k in node), None)
        else:
            break
    for key in ("main", "module"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _js_contract(
    project: str, file: IngestedFile, files: dict[str, IngestedFile]
) -> PackageContract | None:
    try:
        data = json.loads(file.content)
    except (ValueError, RecursionError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name or not isinstance(data, dict):
        return None
    version = data.get("version")
    base = posixpath.dirname(file.ref.path)
    declared = _js_entry_name(data)
    candidates = [posixpath.normpath(posixpath.join(base, declared))] if declared else []
    candidates += [posixpath.normpath(posixpath.join(base, i)) for i in _JS_INDEXES]
    entry = next((c for c in candidates if c in files), None)
    ref = ManifestRef(
        project=project,
        path=file.ref.path,
        line=_line_of(file.content, '"version"') if isinstance(version, str) else None,
        detail=f"name = {name}",
    )
    exports: frozenset[str] = frozenset()
    complete = False
    if entry is not None:
        suffix = posixpath.splitext(entry)[1]
        if js_parse.supports(suffix):
            found = js_parse.parse_js_exports(suffix, files[entry].content)
            exports, complete = frozenset(found.names), not found.incomplete
    return PackageContract(
        "npm", name, version if isinstance(version, str) else None, ref, entry, exports, complete
    )


def package_contracts(project: str, result: IngestionResult) -> list[PackageContract]:
    """One contract per manifest that names a package (pyproject.toml, package.json)."""
    files = _files(result)
    found: list[PackageContract] = []
    for file in result.files:
        if file.kind is not FileKind.MANIFEST or file.parse_status is ParseStatus.ERROR:
            continue
        base = posixpath.basename(file.ref.path).lower()
        contract = None
        if base == "pyproject.toml":
            contract = _python_contract(project, file, files)
        elif base == "package.json":
            contract = _js_contract(project, file, files)
        if contract is not None:
            found.append(contract)
    return found

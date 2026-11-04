"""What a dependent takes from a package: the names it imports, or the whole module (ADR-0041)."""

import ast
import posixpath
from dataclasses import dataclass

from cognitive_mesh.graph import js_parse
from cognitive_mesh.ingestion import IngestionResult
from cognitive_mesh.workspace.exports import import_root
from cognitive_mesh.workspace.models import ManifestRef


@dataclass(frozen=True)
class PackageUse:
    names: frozenset[str]
    whole: bool  # the module is bound as a whole (`import pkg`, `from pkg import *`, `require`)
    sites: tuple[ManifestRef, ...]


def _site(project: str, path: str, line: int, detail: str) -> ManifestRef:
    return ManifestRef(project=project, path=path, line=line, detail=detail[:200])


def _python_use(project: str, result: IngestionResult, root: str) -> PackageUse:
    names: set[str] = set()
    whole = False
    sites: list[ManifestRef] = []
    for file in result.files:
        if file.language != "python":
            continue
        try:
            tree = ast.parse(file.content)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == root:
                taken = [a.name for a in node.names]
                whole = whole or "*" in taken
                names.update(n for n in taken if n != "*")
                sites.append(_site(project, file.ref.path, node.lineno, f"from {root} import ..."))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == root or alias.name.startswith(f"{root}."):
                        whole = True
                        sites.append(
                            _site(project, file.ref.path, node.lineno, f"import {alias.name}")
                        )
    return PackageUse(frozenset(names), whole, tuple(sites))


def _js_use(project: str, result: IngestionResult, package: str) -> PackageUse:
    names: set[str] = set()
    whole = False
    sites: list[ManifestRef] = []
    for file in result.files:
        suffix = posixpath.splitext(file.ref.path)[1]
        if file.language not in ("javascript", "typescript") or not js_parse.supports(suffix):
            continue
        for item in js_parse.parse_js_named_imports(suffix, file.content):
            if item.module != package:
                continue
            names.update(item.names)
            whole = whole or item.whole
            sites.append(_site(project, file.ref.path, item.line, f"import from {package}"))
    return PackageUse(frozenset(names), whole, tuple(sites))


def package_usage(project: str, result: IngestionResult, ecosystem: str, name: str) -> PackageUse:
    """The imports of one package name in one project. Submodule imports are not followed."""
    if ecosystem == "pypi":
        return _python_use(project, result, import_root(name))
    return _js_use(project, result, name)

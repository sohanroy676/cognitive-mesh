"""Cross-project `package` edges (ADR-0036, amends ADR-0006).

An edge A -> B exists only when A's manifest declares a dependency named N and B's own manifest
declares B's package name as N. Names compare after PEP 503 normalisation (PyPI) or exactly (npm).
An import name alone never links. A name nobody in the workspace publishes stays external.
"""

import json
import re
import tomllib
from collections import defaultdict

from cognitive_mesh.graph import DependencyGraph, EdgeKind, NodeKind
from cognitive_mesh.graph.builders import normalize_pypi
from cognitive_mesh.ingestion import FileKind, IngestionResult, ParseStatus
from cognitive_mesh.workspace.models import ManifestRef, PackageEdge

_NAME_LINE = re.compile(r'^\s*"?name"?\s*[=:]')
_MAX_DETAIL = 200

Key = tuple[str, str]  # (ecosystem, normalised name)


def _key(ecosystem: str, name: str) -> Key:
    return (ecosystem, normalize_pypi(name) if ecosystem == "pypi" else name)


def _name_line(content: str, header: str | None) -> int | None:
    inside = header is None
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if header is not None and stripped.startswith("["):
            inside = stripped == header
        if inside and _NAME_LINE.match(line):
            return number
    return None


def published_names(project: str, result: IngestionResult) -> dict[Key, ManifestRef]:
    """The package names this project's own manifests declare for itself."""
    found: dict[Key, ManifestRef] = {}
    for file in result.files:
        if file.kind is not FileKind.MANIFEST or file.parse_status is ParseStatus.ERROR:
            continue
        base = file.ref.path.rsplit("/", 1)[-1].lower()
        try:
            if base == "pyproject.toml":
                table = tomllib.loads(file.content).get("project")
                name = table.get("name") if isinstance(table, dict) else None
                ecosystem, header = "pypi", "[project]"
            elif base == "package.json":
                data = json.loads(file.content)
                name = data.get("name") if isinstance(data, dict) else None
                ecosystem, header = "npm", None
            else:
                continue
        except (ValueError, RecursionError):
            continue
        if not isinstance(name, str) or not name or len(name) > _MAX_DETAIL:
            continue
        ref = ManifestRef(
            project=project,
            path=file.ref.path,
            line=_name_line(file.content, header),
            detail=f"name = {name}",
        )
        found.setdefault(_key(ecosystem, name), ref)
    return found


def declared_dependencies(project: str, graph: DependencyGraph) -> dict[Key, list[ManifestRef]]:
    """Every dependency the project's manifests declare, with each declaring line."""
    found: dict[Key, list[ManifestRef]] = defaultdict(list)
    for edge in graph.edges(EdgeKind.DECLARES):
        node = graph.node(edge.target)
        if node.kind is not NodeKind.PACKAGE or node.ecosystem is None:
            continue
        for evidence in edge.evidence:
            found[_key(node.ecosystem, node.name)].append(
                ManifestRef(
                    project=project,
                    path=evidence.ref.path,
                    line=evidence.line,
                    detail=evidence.detail,
                )
            )
    return dict(found)


def link_projects(
    published: dict[str, dict[Key, ManifestRef]],
    declared: dict[str, dict[Key, list[ManifestRef]]],
) -> list[PackageEdge]:
    """Match declarations to publications across projects. Pure; output is sorted."""
    publishers: dict[Key, list[str]] = defaultdict(list)
    for project, names in published.items():
        for key in names:
            publishers[key].append(project)
    edges: list[PackageEdge] = []
    for source in sorted(declared):
        for key, refs in sorted(declared[source].items()):
            owners = sorted(publishers.get(key, []))
            if not owners or source in owners:
                continue  # nobody publishes it (external), or the project's own name
            ambiguous = len(owners) > 1
            for target in owners:
                edges.append(
                    PackageEdge(
                        source=source,
                        target=target,
                        ecosystem=key[0],
                        name=key[1],
                        declared=tuple(refs),
                        published=published[target][key],
                        ambiguous=ambiguous,
                        candidates=tuple(owners) if ambiguous else (),
                    )
                )
    return edges

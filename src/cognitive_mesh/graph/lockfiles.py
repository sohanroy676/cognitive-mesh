"""Read locked, transitive dependencies from `uv.lock` and `package-lock.json` (ADR-0006 amendment).

Pure functions over already-ingested (and redacted) text, standard library only, nothing executed.
A lockfile states which locked package depends on which; nothing is inferred beyond that. Packages
are keyed by name, so several locked versions of one npm package collapse into one node.
"""

import json
import re
import tomllib
from dataclasses import dataclass, field

from cognitive_mesh.graph.models import Ecosystem, Evidence
from cognitive_mesh.ingestion import IngestedFile

MAX_PACKAGES = 20_000
_NAME_NORMALIZE = re.compile(r"[-_.]+")


class LockfileProblem(Exception):
    """The lockfile cannot be used. The message is fixed text, never file content."""


@dataclass(frozen=True)
class LockedPackage:
    id: str
    name: str
    ecosystem: Ecosystem


@dataclass(frozen=True)
class LockedDependency:
    source: str  # package id that depends on `target`
    target: str
    evidence: Evidence


@dataclass
class LockParse:
    packages: list[LockedPackage] = field(default_factory=list)
    dependencies: list[LockedDependency] = field(default_factory=list)


def _line_of(content: str, needle: str) -> int | None:
    for number, line in enumerate(content.splitlines(), 1):
        if needle in line:
            return number
    return None


def _clip(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _pypi_id(name: str) -> str:
    return f"pkg:pypi:{_NAME_NORMALIZE.sub('-', name).lower()}"


def parse_uv_lock(file: IngestedFile) -> LockParse:
    try:
        data = tomllib.loads(file.content)
    except (ValueError, RecursionError):
        raise LockfileProblem("cannot parse the TOML lockfile") from None
    entries = data.get("package", [])
    if not isinstance(entries, list):
        raise LockfileProblem("the lockfile has no package list")
    if len(entries) > MAX_PACKAGES:
        raise LockfileProblem(f"the lockfile lists more than {MAX_PACKAGES} packages")
    out = LockParse()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        source = entry.get("source")
        # The project itself and workspace members are not external packages; their direct
        # dependencies are already declared by their manifests.
        if isinstance(source, dict) and any(
            k in source for k in ("virtual", "editable", "directory")
        ):
            continue
        name = str(entry["name"])
        version = str(entry.get("version", "?"))
        src = _pypi_id(name)
        out.packages.append(LockedPackage(src, name, "pypi"))
        deps = entry.get("dependencies", [])
        if not isinstance(deps, list):
            continue
        line = _line_of(file.content, f'name = "{name}"')
        for dep in deps:
            if isinstance(dep, dict) and isinstance(dep.get("name"), str):
                dep_name = str(dep["name"])
                out.packages.append(LockedPackage(_pypi_id(dep_name), dep_name, "pypi"))
                out.dependencies.append(
                    LockedDependency(
                        src,
                        _pypi_id(dep_name),
                        Evidence(
                            ref=file.ref,
                            line=line,
                            rule="uv.lock:dependencies",
                            detail=_clip(f"{name} {version} -> {dep_name}"),
                        ),
                    )
                )
    return out


def _npm_name(key: str) -> str:
    return key.rsplit("node_modules/", 1)[-1]


def _resolve_npm(key: str, dep: str, packages: dict[str, object]) -> str | None:
    """Node's lookup: nearest node_modules/<dep> walking up from the requiring package."""
    base = key
    while True:
        candidate = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if candidate in packages:
            return candidate
        if not base:
            return None
        idx = base.rfind("/node_modules/")
        base = base[:idx] if idx >= 0 else ""


def parse_package_lock(file: IngestedFile) -> LockParse:
    try:
        data = json.loads(file.content)
    except (ValueError, RecursionError):
        raise LockfileProblem("cannot parse the JSON lockfile") from None
    if not isinstance(data, dict):
        raise LockfileProblem("top-level JSON is not an object")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise LockfileProblem("only lockfileVersion 2 and 3 (a 'packages' table) are supported")
    if len(packages) > MAX_PACKAGES:
        raise LockfileProblem(f"the lockfile lists more than {MAX_PACKAGES} packages")
    out = LockParse()
    for key, entry in packages.items():
        # "" is the root project (declared by package.json); "link" entries are workspace links.
        if not key or not isinstance(entry, dict) or entry.get("link") is True:
            continue
        if not isinstance(key, str) or "node_modules/" not in key:
            continue
        name = _npm_name(key)
        src = f"pkg:npm:{name}"
        out.packages.append(LockedPackage(src, name, "npm"))
        line = _line_of(file.content, json.dumps(key))
        version = entry.get("version")
        for section in ("dependencies", "optionalDependencies"):
            table = entry.get(section)
            if not isinstance(table, dict):
                continue
            for dep in table:
                resolved = _resolve_npm(key, str(dep), packages)
                if resolved is None:  # e.g. an optional dependency that was not installed
                    continue
                dep_name = _npm_name(resolved)
                out.packages.append(LockedPackage(f"pkg:npm:{dep_name}", dep_name, "npm"))
                out.dependencies.append(
                    LockedDependency(
                        src,
                        f"pkg:npm:{dep_name}",
                        Evidence(
                            ref=file.ref,
                            line=line,
                            rule=f"package-lock.json:{section}",
                            detail=_clip(
                                f"{name} {version if isinstance(version, str) else '?'}"
                                f" -> {dep_name}"
                            ),
                        ),
                    )
                )
    return out

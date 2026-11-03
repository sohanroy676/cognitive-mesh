"""Shared configuration names (ADR-0037): one project reads an environment variable name that
another sets. Names only. A value is never read into a record, and a real `.env` is never opened
(ingestion refuses it). A name that itself looks like a random token is dropped and counted.
"""

import ast
import re
from collections import defaultdict
from typing import Any

from cognitive_mesh.ingestion.secrets import looks_random
from cognitive_mesh.workspace.contracts import site_of
from cognitive_mesh.workspace.facts import EnvRead, EnvSet, ProjectFacts
from cognitive_mesh.workspace.models import ConfigEdge, Site, Unresolved

GENERIC_NAMES = frozenset(
    {
        "PORT",
        "HOST",
        "HOSTNAME",
        "DEBUG",
        "NODE_ENV",
        "ENV",
        "ENVIRONMENT",
        "LOG_LEVEL",
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "TZ",
        "PWD",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "PYTHONPATH",
        "CI",
        "VERBOSE",
    }  # fmt: skip
)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_DOCKER = re.compile(r"^\s*(ENV|ARG)\s+(.*)$", re.IGNORECASE)
COMPOSE_FILE = re.compile(r"^(docker-)?compose(\..+)?\.ya?ml$", re.IGNORECASE)


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name)) and not looks_random(name)


def _accept(facts: ProjectFacts, name: str, file: str) -> bool:
    if valid_name(name):
        return True
    facts.notes.append(
        (
            "secret_like_name",
            f"a name in {file} is not a plain name or looks like a secret; dropped",
        )
    )
    return False


def add_set(facts: ProjectFacts, name: str, file: str, line: int | None) -> None:
    if _accept(facts, name, file):
        facts.env_sets.append(EnvSet(name, site_of(facts.project, file, line, name)))


def add_read(facts: ProjectFacts, name: str, file: str, line: int | None) -> None:
    if _accept(facts, name, file):
        facts.env_reads.append(EnvRead(name, site_of(facts.project, file, line, name)))


def _find_line(text: str, name: str) -> int | None:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return number
    return None


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def python_env(facts: ProjectFacts, file: str, tree: ast.AST) -> None:
    for node in ast.walk(tree):
        key: ast.AST | None = None
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            if not isinstance(node.ctx, ast.Load):
                continue
            key = node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if (func.attr == "get" and _is_environ(func.value)) or (
                func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os"
            ):
                key = node.args[0] if node.args else ast.Constant(value=None)
            else:
                continue
        else:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            add_read(facts, key.value, file, node.lineno)
        else:
            facts.env_reads.append(
                EnvRead(None, site_of(facts.project, file, node.lineno, "(computed name)"))
            )


def env_template_names(facts: ProjectFacts, file: str, text: str) -> None:
    """`NAME=...` lines of `.env.example` and friends; the part after `=` is never looked at."""
    for number, line in enumerate(text.splitlines(), 1):
        if m := _ENV_LINE.match(line):
            add_set(facts, m.group(1), file, number)


def compose_names(facts: ProjectFacts, file: str, text: str, doc: Any) -> None:
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict):
        return
    for service in services.values():
        if not isinstance(service, dict):
            continue
        env = service.get("environment")
        names: list[str] = []
        if isinstance(env, dict):
            names = [k for k in env if isinstance(k, str)]
        elif isinstance(env, list):
            names = [e.split("=", 1)[0] for e in env if isinstance(e, str)]
        for name in names:
            add_set(facts, name.strip(), file, _find_line(text, name.strip()))
        if service.get("env_file"):
            facts.notes.append(
                ("env_file_not_read", f"{file} names env_file entries; their content is not read")
            )


def dockerfile_names(facts: ProjectFacts, file: str, text: str) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        m = _DOCKER.match(line)
        if m is None:
            continue
        tokens = m.group(2).split()
        if not tokens:
            continue
        if any("=" in t for t in tokens) or m.group(1).upper() == "ARG":
            names = [t.split("=", 1)[0] for t in tokens if "=" in t or m.group(1).upper() == "ARG"]
        else:
            names = [tokens[0]]  # `ENV NAME value`
        for name in names:
            add_set(facts, name, file, number)


# --- matching ---------------------------------------------------------------------------------


def _sites(items: list[EnvRead] | list[EnvSet]) -> tuple[Site, ...]:
    return tuple(sorted({i.site for i in items}, key=lambda s: (s.path, s.line or 0)))


def link_config(facts: dict[str, ProjectFacts]) -> tuple[list[ConfigEdge], list[Unresolved]]:
    setters: dict[str, dict[str, list[EnvSet]]] = defaultdict(lambda: defaultdict(list))
    readers: dict[str, dict[str, list[EnvRead]]] = defaultdict(lambda: defaultdict(list))
    unresolved: list[Unresolved] = []
    for project in sorted(facts):
        for s in facts[project].env_sets:
            setters[s.name][project].append(s)
        for r in facts[project].env_reads:
            if r.name is None:
                unresolved.append(
                    Unresolved(project=project, kind="config", reason="computed_name", site=r.site)
                )
            else:
                readers[r.name][project].append(r)
    edges: list[ConfigEdge] = []
    for name in sorted(readers):
        for reader, reads in sorted(readers[name].items()):
            others = sorted(p for p in setters.get(name, {}) if p != reader)
            if not others and reader not in setters.get(name, {}):
                unresolved.extend(
                    Unresolved(project=reader, kind="config", reason="no_setter", site=r.site)
                    for r in reads
                )
            for target in others:
                edges.append(
                    ConfigEdge(
                        source=reader,
                        target=target,
                        name=name,
                        generic=name.upper() in GENERIC_NAMES,
                        readers=_sites(reads),
                        setters=_sites(setters[name][target]),
                        ambiguous=len(others) > 1,
                        candidates=tuple(others) if len(others) > 1 else (),
                    )
                )
    for name in sorted(setters):
        if name in readers:
            continue
        for project, sets in sorted(setters[name].items()):
            unresolved.append(
                Unresolved(
                    project=project, kind="config", reason="unused_setting", site=sets[0].site
                )
            )
    return edges, unresolved

"""Shared schema edges (ADR-0037): two projects that use the same table or JSON Schema `$id`.

Sources are parse-only: SQL DDL and migrations (regular expressions, no SQL parser), SQLAlchemy,
Prisma and JSON Schema files. Column differences are reported, never judged. ORM names are
compared as unquoted identifiers (lower case).
"""

import ast
import re
from collections import defaultdict
from dataclasses import replace
from itertools import combinations
from typing import Any

from cognitive_mesh.workspace.contracts import site_of
from cognitive_mesh.workspace.facts import Column, ProjectFacts, TableDef
from cognitive_mesh.workspace.models import SchemaEdge, Site, Unresolved

_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_NAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"
_CREATE = re.compile(
    rf"\bCREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP(?:ORARY)?\s+)|UNLOGGED\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})\s*\(",
    re.IGNORECASE,
)
_ALTER = re.compile(
    rf"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({_NAME})\s+(.*?)(?:;|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_FIRST_IDENT = re.compile(rf"^\s*({_IDENT})")
_ADD = re.compile(rf"^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?({_IDENT})", re.IGNORECASE)
_DROP = re.compile(rf"^DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?({_IDENT})", re.IGNORECASE)
_RENAME = re.compile(rf"^RENAME\s+COLUMN\s+({_IDENT})\s+TO\s+({_IDENT})", re.IGNORECASE)
_NOT_COLUMN = frozenset(
    {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK", "KEY", "INDEX", "EXCLUDE", "LIKE"}
)
_ALTER_TYPE = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?({_IDENT})\s+(?:SET\s+DATA\s+)?TYPE\s+(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_SET_NOT_NULL = re.compile(rf"^ALTER\s+(?:COLUMN\s+)?({_IDENT})\s+SET\s+NOT\s+NULL", re.IGNORECASE)
_DROP_NOT_NULL = re.compile(
    rf"^ALTER\s+(?:COLUMN\s+)?({_IDENT})\s+DROP\s+NOT\s+NULL", re.IGNORECASE
)
_MODIFY = re.compile(rf"^MODIFY\s+(?:COLUMN\s+)?({_IDENT})\s+(.*)$", re.IGNORECASE | re.DOTALL)
_CONSTRAINT_START = re.compile(
    r"\b(NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|REFERENCES|UNIQUE|CHECK|CONSTRAINT|GENERATED|"
    r"COLLATE|AUTO_INCREMENT|AUTOINCREMENT|IDENTITY)\b",
    re.IGNORECASE,
)
_PRISMA_MODEL = re.compile(r"^\s*model\s+(\w+)\s*\{(.*?)^\s*\}", re.MULTILINE | re.DOTALL)
_PRISMA_FIELD = re.compile(r"^\s*(\w+)\s+([A-Za-z_]\w*)(\[\]|\?)?(.*)$")
_PRISMA_MAP = re.compile(r'@map\(\s*"([^"]+)"\s*\)')
_PRISMA_TABLE_MAP = re.compile(r'@@map\(\s*"([^"]+)"\s*\)')


def _ident(raw: str) -> str:
    """Quoted identifiers keep their case, unquoted ones are folded to lower case."""
    raw = raw.strip()
    if raw[:1] in ('"', "`", "[") and len(raw) >= 2:
        return raw[1:-1]
    return raw.lower()


def _qualified(raw: str) -> str:
    return ".".join(_ident(part) for part in re.split(r"\s*\.\s*", raw.strip()))


def _strip_comments(text: str) -> str:
    """Remove SQL comments but keep line breaks, so line numbers stay right."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", text)


def _split_top(text: str) -> list[str]:
    """Split on commas outside parentheses and quotes."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for i, ch in enumerate(text):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _matching_paren(text: str, open_at: int) -> int | None:
    depth = 0
    quote = ""
    for i in range(open_at, len(text)):
        ch = text[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _line_at(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _compact(text: str) -> str | None:
    """A type written the same way every time: lower case, no spaces."""
    squeezed = re.sub(r"\s+", "", text).lower()
    return squeezed or None


def _column_from_def(name: str, rest: str) -> Column:
    """A column from the text after its name: type up to the first constraint word."""
    stop = _CONSTRAINT_START.search(rest)
    type_text = _compact(rest[: stop.start()] if stop else rest)
    not_null = re.search(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", rest, re.IGNORECASE)
    has_default = bool(
        re.search(r"\bDEFAULT\b|\bAUTO_?INCREMENT\b|\bIDENTITY\b", rest, re.IGNORECASE)
        or (type_text or "").endswith("serial")
    )
    return Column(name, type_text, nullable=not_null is None, default=has_default)


def sql_tables(project: str, file: str, text: str) -> list[TableDef]:
    """Tables and columns declared by `CREATE TABLE`, then changed by `ALTER TABLE`, in order."""
    clean = _strip_comments(text)
    state: dict[str, tuple[int, dict[str, Column]]] = {}
    events: list[tuple[int, str, str, int]] = []  # (position, kind, name, body start)
    for m in _CREATE.finditer(clean):
        events.append((m.start(), "create", _qualified(m.group(1)), m.end() - 1))
    for m in _ALTER.finditer(clean):
        events.append((m.start(), "alter", _qualified(m.group(1)), m.start(2)))
    for pos, kind, name, body_start in sorted(events):
        line = _line_at(clean, pos)
        columns = state.setdefault(name, (line, {}))[1]
        if kind == "create":
            end = _matching_paren(clean, body_start)
            if end is None:
                continue
            for part in _split_top(clean[body_start + 1 : end]):
                first = _FIRST_IDENT.match(part)
                if first and first.group(1).upper() not in _NOT_COLUMN:
                    column = _ident(first.group(1))
                    columns[column] = _column_from_def(column, part[first.end() :])
            continue
        statement = _ALTER.match(clean, pos)
        if statement is None:
            continue
        for clause in _split_top(statement.group(2)):
            clause = clause.strip()
            renamed, dropped, added = (
                _RENAME.match(clause),
                _DROP.match(clause),
                _ADD.match(clause),
            )
            retyped, set_nn, drop_nn, modified = (
                _ALTER_TYPE.match(clause),
                _SET_NOT_NULL.match(clause),
                _DROP_NOT_NULL.match(clause),
                _MODIFY.match(clause),
            )
            if renamed:
                old, new = _ident(renamed.group(1)), _ident(renamed.group(2))
                columns[new] = replace(columns.pop(old, Column(old)), name=new)
            elif dropped:
                columns.pop(_ident(dropped.group(1)), None)
            elif added and added.group(1).upper() not in _NOT_COLUMN:
                column = _ident(added.group(1))
                columns[column] = _column_from_def(column, clause[added.end() :])
            elif retyped:
                column = _ident(retyped.group(1))
                before = columns.get(column, Column(column))
                columns[column] = replace(
                    before, type=_column_from_def(column, retyped.group(2)).type
                )
            elif set_nn or drop_nn:
                match = set_nn or drop_nn
                assert match is not None  # noqa: S101 - one of the two matched
                column = _ident(match.group(1))
                columns[column] = replace(
                    columns.get(column, Column(column)), nullable=drop_nn is not None
                )
            elif modified:
                column = _ident(modified.group(1))
                columns[column] = _column_from_def(column, modified.group(2))
    return [
        TableDef(
            "table",
            name,
            "defines",
            site_of(project, file, line, f"table {name}"),
            frozenset(cols),
            tuple(sorted(cols.values(), key=lambda c: c.name)),
        )
        for name, (line, cols) in state.items()
    ]


# --- SQLAlchemy -------------------------------------------------------------------------------


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _first_str(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _kw_bool(call: ast.Call, key: str) -> bool | None:
    for kw in call.keywords:
        if (
            kw.arg == key
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, bool)
        ):
            return kw.value.value
    return None


def _annotation_type(annotation: ast.expr | None) -> tuple[str | None, bool | None]:
    """The type and nullability of `Mapped[int]`, `Mapped[Optional[int]]`, `Mapped[int | None]`."""
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Mapped"
    ):
        return None, None
    inner = annotation.slice
    optional = False
    if (
        isinstance(inner, ast.Subscript)
        and isinstance(inner.value, ast.Name)
        and inner.value.id == "Optional"
    ):
        inner, optional = inner.slice, True
    elif isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.BitOr):
        sides = [inner.left, inner.right]
        kept = [s for s in sides if not (isinstance(s, ast.Constant) and s.value is None)]
        if len(kept) == 1:
            inner, optional = kept[0], True
    return _compact(ast.unparse(inner)), optional


def _sa_column(name: str, call: ast.Call, annotation: ast.expr | None = None) -> Column:
    """A SQLAlchemy `Column(...)` / `mapped_column(...)`: type from the first type-like argument."""
    type_text: str | None = None
    for arg in call.args:
        if isinstance(arg, ast.Constant):
            continue  # the column name
        if isinstance(arg, ast.Call) and _call_name(arg) in ("ForeignKey", "Computed", "Identity"):
            continue
        type_text = _compact(ast.unparse(arg))
        break
    note_type, note_optional = _annotation_type(annotation)
    if type_text is None:
        type_text = note_type
    nullable = _kw_bool(call, "nullable")
    if nullable is None:
        if _kw_bool(call, "primary_key"):
            nullable = False
        elif note_optional is not None:
            nullable = note_optional
        elif _call_name(call) == "Column":
            nullable = True
    has_default = any(
        kw.arg in ("default", "server_default", "insert_default") for kw in call.keywords
    )
    return Column(name, type_text, nullable, has_default)


def _class_columns(cls: ast.ClassDef) -> list[Column]:
    columns: list[Column] = []
    for stmt in cls.body:
        target: ast.AST | None
        annotation: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value, annotation = stmt.target, stmt.value, stmt.annotation
        else:
            continue
        if _call_name(value) in ("Column", "mapped_column") and isinstance(target, ast.Name):
            assert isinstance(value, ast.Call)  # noqa: S101 - _call_name saw a Call
            columns.append(_sa_column((_first_str(value) or target.id).lower(), value, annotation))
    return columns


def python_tables(project: str, file: str, tree: ast.AST) -> list[TableDef]:
    found: list[TableDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets
                    )
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    name = stmt.value.value.lower()
                    where = site_of(project, file, node.lineno, f"table {name}")
                    typed = _class_columns(node)
                    found.append(
                        TableDef(
                            "table",
                            name,
                            "maps",
                            where,
                            frozenset(c.name for c in typed),
                            tuple(sorted(typed, key=lambda c: c.name)),
                        )
                    )
        elif _call_name(node) == "Table":
            assert isinstance(node, ast.Call)  # noqa: S101
            name = (_first_str(node) or "").lower()
            if name:
                typed = [
                    _sa_column((_first_str(a) or "").lower(), a)
                    for a in node.args
                    if isinstance(a, ast.Call) and _call_name(a) == "Column" and _first_str(a)
                ]
                found.append(
                    TableDef(
                        "table",
                        name,
                        "maps",
                        site_of(project, file, node.lineno, f"table {name}"),
                        frozenset(c.name for c in typed),
                        tuple(sorted(typed, key=lambda c: c.name)),
                    )
                )
    return found


# --- Prisma -----------------------------------------------------------------------------------


def prisma_tables(project: str, file: str, text: str) -> list[TableDef]:
    models = {m.group(1) for m in _PRISMA_MODEL.finditer(text)}
    found: list[TableDef] = []
    for m in _PRISMA_MODEL.finditer(text):
        body = m.group(2)
        mapped = _PRISMA_TABLE_MAP.search(body)
        name = (mapped.group(1) if mapped else m.group(1)).lower()
        columns: dict[str, Column] = {}
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith(("@@", "//")):
                continue
            field = _PRISMA_FIELD.match(line)
            if field is None or field.group(2) in models:
                continue  # relation fields are not columns
            alias = _PRISMA_MAP.search(field.group(4))
            column = (alias.group(1) if alias else field.group(1)).lower()
            listed = field.group(3) == "[]"
            columns[column] = Column(
                column,
                field.group(2).lower() + ("[]" if listed else ""),
                nullable=field.group(3) == "?",
                default="@default" in field.group(4),
            )
        where = site_of(project, file, _line_at(text, m.start()), f"model {m.group(1)}")
        found.append(
            TableDef(
                "table",
                name,
                "maps",
                where,
                frozenset(columns),
                tuple(sorted(columns.values(), key=lambda c: c.name)),
            )
        )
    return found


# --- JSON Schema ------------------------------------------------------------------------------


def json_schema(project: str, file: str, doc: Any) -> tuple[list[TableDef], list[Unresolved]]:
    if not isinstance(doc, dict) or not isinstance(doc.get("$schema"), str):
        return [], []
    schema_id = doc.get("$id")
    if not isinstance(schema_id, str) or not schema_id:
        where = site_of(project, file, None, "JSON Schema without $id")
        return [], [Unresolved(project=project, kind="schema", reason="no_id", site=where)]
    props = doc.get("properties")
    required = doc.get("required")
    needed = {r for r in required if isinstance(r, str)} if isinstance(required, list) else set()
    typed: list[Column] = []
    if isinstance(props, dict):
        for key, spec in props.items():
            if not isinstance(key, str):
                continue
            kind = spec.get("type") if isinstance(spec, dict) else None
            if isinstance(kind, list):
                names = sorted(k for k in kind if isinstance(k, str) and k != "null")
                type_text: str | None = "|".join(names) or None
                null_ok = "null" in kind
            else:
                type_text = kind if isinstance(kind, str) else None
                null_ok = False
            typed.append(
                Column(
                    key,
                    type_text,
                    nullable=key not in needed or null_ok,
                    default=isinstance(spec, dict) and "default" in spec,
                )
            )
    where = site_of(project, file, None, f"$id {schema_id}")
    return [
        TableDef(
            "json_schema",
            schema_id,
            "describes",
            where,
            frozenset(c.name for c in typed),
            tuple(sorted(typed, key=lambda c: c.name)),
        )
    ], []


# --- matching ---------------------------------------------------------------------------------


def _merge(defs: list[TableDef]) -> tuple[tuple[Site, ...], tuple[str, ...], frozenset[str]]:
    sites = tuple(sorted({d.site for d in defs}, key=lambda s: (s.path, s.line or 0)))
    roles = tuple(sorted({d.role for d in defs}))
    columns: frozenset[str] = frozenset().union(*(d.columns for d in defs))
    return sites, roles, columns


def link_schemas(facts: dict[str, ProjectFacts]) -> tuple[list[SchemaEdge], list[Unresolved]]:
    by_name: dict[tuple[str, str], dict[str, list[TableDef]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for project in sorted(facts):
        for table in facts[project].tables:
            by_name[(table.kind, table.name)][project].append(table)
    edges: list[SchemaEdge] = []
    unresolved: list[Unresolved] = []
    for (kind, name), owners in sorted(by_name.items()):
        if len(owners) == 1:
            ((project, defs),) = owners.items()
            reason = "no_matching_table" if kind == "table" else "no_matching_schema"
            unresolved.append(
                Unresolved(project=project, kind="schema", reason=reason, site=defs[0].site)
            )
            continue
        for first, second in combinations(sorted(owners), 2):
            a_sites, a_roles, a_cols = _merge(owners[first])
            b_sites, b_roles, b_cols = _merge(owners[second])
            both_known = bool(a_cols) and bool(b_cols)
            edges.append(
                SchemaEdge(
                    source=first,
                    target=second,
                    kind=kind,
                    name=name,
                    source_sites=a_sites,
                    target_sites=b_sites,
                    source_roles=a_roles,
                    target_roles=b_roles,
                    only_in_source=tuple(sorted(a_cols - b_cols)) if both_known else (),
                    only_in_target=tuple(sorted(b_cols - a_cols)) if both_known else (),
                )
            )
    for project in sorted(facts):
        unresolved.extend(u for u in facts[project].unresolved if u.kind == "schema")
    return edges, unresolved

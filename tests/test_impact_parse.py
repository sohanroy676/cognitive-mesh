"""Typed columns, JS exports and imports, and package exports (ADR-0041 extractors)."""

import ast
import json
from pathlib import Path

from cognitive_mesh.graph.js_parse import parse_js_exports, parse_js_named_imports
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.workspace import schemas
from cognitive_mesh.workspace.exports import package_contracts, python_exports
from cognitive_mesh.workspace.facts import Column
from contracts_support import write_project


def _cols(defs: list) -> dict[str, Column]:  # type: ignore[type-arg]
    return {c.name: c for c in defs[0].typed}


def test_sql_columns_carry_type_nullability_and_default() -> None:
    sql = """\
CREATE TABLE t (
    id serial PRIMARY KEY,
    name VARCHAR( 50 ) NOT NULL DEFAULT 'x',
    note text,
    CONSTRAINT u UNIQUE (name)
);
ALTER TABLE t ADD COLUMN age integer;
ALTER TABLE t ALTER COLUMN note TYPE varchar(10);
ALTER TABLE t ALTER COLUMN note SET NOT NULL;
ALTER TABLE t RENAME COLUMN age TO years;
"""
    cols = _cols(schemas.sql_tables("p", "m.sql", sql))
    assert cols["id"] == Column("id", "serial", nullable=False, default=True)
    assert cols["name"] == Column("name", "varchar(50)", nullable=False, default=True)
    assert cols["note"] == Column("note", "varchar(10)", nullable=False, default=False)
    assert cols["years"] == Column("years", "integer", nullable=True, default=False)
    assert "age" not in cols


def test_sql_modify_and_drop_not_null() -> None:
    sql = (
        "CREATE TABLE t (a int NOT NULL, b int);\n"
        "ALTER TABLE t MODIFY COLUMN b bigint NOT NULL;\n"
        "ALTER TABLE t ALTER COLUMN a DROP NOT NULL;\n"
    )
    cols = _cols(schemas.sql_tables("p", "m.sql", sql))
    assert cols["b"].type == "bigint" and cols["b"].nullable is False
    assert cols["a"].nullable is True


def test_sqlalchemy_columns() -> None:
    code = """\
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class U(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column("Email", String(50), nullable=False, default="x")
    bio = Column(String)
    age: Mapped[int] = mapped_column()
    nick: Mapped[str | None] = mapped_column(String(10))
"""
    cols = _cols(schemas.python_tables("p", "m.py", ast.parse(code)))
    assert cols["id"].nullable is False
    assert cols["email"] == Column("email", "string(50)", nullable=False, default=True)
    assert cols["bio"].nullable is True and cols["bio"].type == "string"
    assert cols["age"] == Column("age", "int", nullable=False, default=False)
    assert cols["nick"].nullable is True and cols["nick"].type == "string(10)"


def test_prisma_fields_carry_type_and_optional() -> None:
    text = (
        "model User {\n  id Int @id @default(autoincrement())\n  name String?\n"
        "  tags String[]\n  posts Post[]\n}\nmodel Post {\n  id Int @id\n}\n"
    )
    cols = _cols(schemas.prisma_tables("p", "s.prisma", text))
    assert cols["id"] == Column("id", "int", nullable=False, default=True)
    assert cols["name"].nullable is True
    assert cols["tags"].type == "string[]" and cols["tags"].nullable is False
    assert "posts" not in cols


def test_json_schema_properties_and_required() -> None:
    doc = {
        "$schema": "x",
        "$id": "urn:a",
        "required": ["a"],
        "properties": {
            "a": {"type": "string"},
            "b": {"type": ["integer", "null"]},
            "c": {"type": "number", "default": 1},
            "d": {},
        },
    }
    (table,), _ = schemas.json_schema("p", "s.json", doc)
    cols = {c.name: c for c in table.typed}
    assert cols["a"] == Column("a", "string", nullable=False, default=False)
    assert cols["b"].type == "integer" and cols["b"].nullable is True
    assert cols["c"].default is True
    assert cols["d"].type is None


def test_js_exports_and_defaults() -> None:
    src = (
        "export function a(){}\nexport const b=1, c=2;\nexport {d as e, f};\n"
        "export default class X{}\nexport interface I{}\n"
    )
    found = parse_js_exports(".ts", src)
    assert found.names == ("I", "a", "b", "c", "default", "e", "f")
    assert found.incomplete is False


def test_js_export_star_and_destructuring_make_the_list_incomplete() -> None:
    assert parse_js_exports(".ts", "export * from './x';\nexport const k = 1;").incomplete
    assert parse_js_exports(".js", "export const {q} = o;").incomplete
    assert parse_js_exports(".js", "export const = ;").incomplete  # syntax error


def test_js_named_imports_and_require() -> None:
    src = (
        "import d, {a, b as c} from 'lib';\nimport * as ns from 'lib2';\n"
        "const {x, y: z} = require('lib3');\nconst w = require('lib4');\nimport './side';\n"
    )
    by_module = {i.module: i for i in parse_js_named_imports(".ts", src)}
    assert by_module["lib"].names == ("a", "b", "default") and not by_module["lib"].whole
    assert by_module["lib2"].whole
    assert by_module["lib3"].names == ("x", "y") and not by_module["lib3"].whole
    assert by_module["lib4"].whole
    assert by_module["./side"].whole


def test_python_exports_prefer_a_literal_all() -> None:
    names, complete = python_exports(ast.parse("__all__ = ['a']\ndef a(): ...\ndef b(): ...\n"))
    assert names == {"a"} and complete
    names, complete = python_exports(
        ast.parse("def a(): ...\ndef _p(): ...\nX = 1\nfrom m import y as z\n")
    )
    assert names == {"a", "X", "z"} and complete
    _, complete = python_exports(ast.parse("from m import *\n"))
    assert not complete
    _, complete = python_exports(ast.parse("__all__ = make()\n"))
    assert not complete


def test_package_contracts_read_version_and_exports(tmp_path: Path) -> None:
    py = write_project(
        tmp_path / "py",
        {
            "pyproject.toml": '[project]\nname = "My-Lib"\nversion = "2.1.0"\n',
            "src/my_lib/__init__.py": "def hello(): ...\n",
        },
    )
    js = write_project(
        tmp_path / "js",
        {
            "package.json": json.dumps(
                {"name": "@s/ui", "version": "3.0.0", "main": "./src/index.ts"}
            ),
            "src/index.ts": "export const Button = 1;\n",
        },
    )
    (a,) = package_contracts("py", ingest_application(py))
    assert (a.ecosystem, a.name, a.version, a.entry) == (
        "pypi",
        "My-Lib",
        "2.1.0",
        "src/my_lib/__init__.py",
    )
    assert a.exports == {"hello"} and a.exports_complete
    (b,) = package_contracts("js", ingest_application(js))
    assert (b.version, b.entry, b.exports) == ("3.0.0", "src/index.ts", frozenset({"Button"}))


def test_a_package_without_a_found_entry_has_incomplete_exports(tmp_path: Path) -> None:
    root = write_project(tmp_path / "p", {"pyproject.toml": '[project]\nname = "lonely"\n'})
    (found,) = package_contracts("p", ingest_application(root))
    assert found.version is None and found.entry is None and not found.exports_complete

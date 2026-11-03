"""Shared schema edges (ADR-0037): same table or `$id`; column differences shown, not judged."""

import json
from pathlib import Path

from contracts_support import graph_of, reasons

MIGRATION = """\
-- users table
CREATE TABLE IF NOT EXISTS public.Users (
    id integer PRIMARY KEY,
    "Email" text NOT NULL, /* the address */
    name text,
    CONSTRAINT uq UNIQUE (name)
);
ALTER TABLE public.users ADD COLUMN created_at timestamp, DROP COLUMN name;
ALTER TABLE public.users RENAME COLUMN id TO user_id;
"""

MODEL = """\
from sqlalchemy import Column, Integer, String


class User(Base):
    __tablename__ = "Users"
    id = Column(Integer, primary_key=True)
    email = Column("Email", String)
    nickname = Column(String)
"""


def test_schema_qualified_name_is_not_the_bare_name(tmp_path: Path) -> None:
    g = graph_of(
        tmp_path,
        {"db": {"migrations/001.sql": MIGRATION}, "app": {"models.py": MODEL}},
    )
    assert g.schemas == ()  # `public.users` is compared whole, so it is not `users`
    assert reasons(g, "schema") == {("db", "no_matching_table"), ("app", "no_matching_table")}


def test_unqualified_tables_match_case_insensitively(tmp_path: Path) -> None:
    sql = MIGRATION.replace("public.", "")
    g = graph_of(tmp_path, {"db": {"m.sql": sql}, "app": {"models.py": MODEL}})
    (edge,) = g.schemas
    assert (edge.source, edge.target, edge.name) == ("app", "db", "users")
    assert edge.source_roles == ("maps",) and edge.target_roles == ("defines",)
    assert edge.column_difference
    # db columns after DDL: id renamed to user_id, name dropped, created_at added, "Email" kept
    assert edge.only_in_target == ("Email", "created_at", "user_id")
    assert edge.only_in_source == ("email", "id", "nickname")
    assert edge.target_sites[0].path == "m.sql" and edge.target_sites[0].line == 2


def test_prisma_model_with_map(tmp_path: Path) -> None:
    prisma = (
        'model Account {\n  id Int @id\n  owner User\n  mail String @map("email")\n'
        '  @@map("accounts")\n}\nmodel User {\n  id Int @id\n}\n'
    )
    sql = "CREATE TABLE accounts (id int, email text, owner_id int);"
    g = graph_of(tmp_path, {"p": {"schema.prisma": prisma}, "s": {"a.sql": sql}})
    (edge,) = (e for e in g.schemas if e.name == "accounts")
    assert edge.only_in_source == () and edge.only_in_target == ("owner_id",)


def test_json_schema_links_by_id_only(tmp_path: Path) -> None:
    def doc(**extra: object) -> str:
        base = {"$schema": "https://json-schema.org/draft/2020-12/schema", "properties": {"a": {}}}
        return json.dumps({**base, **extra})

    g = graph_of(
        tmp_path,
        {
            "x": {"order.schema.json": doc(**{"$id": "https://ex/order"})},
            "y": {"o.json": doc(**{"$id": "https://ex/order"})},
            "z": {"anon.json": doc()},
        },
    )
    assert [(e.kind, e.name) for e in g.schemas] == [("json_schema", "https://ex/order")]
    assert ("z", "no_id") in reasons(g, "schema")


def test_unshared_table_is_listed_not_dropped(tmp_path: Path) -> None:
    g = graph_of(
        tmp_path,
        {
            "a": {"a.sql": "CREATE TABLE only_a (id int);"},
            "b": {"b.sql": "CREATE TABLE only_b (id int);"},
        },
    )
    assert g.schemas == ()
    assert reasons(g, "schema") == {("a", "no_matching_table"), ("b", "no_matching_table")}


def test_sqlalchemy_table_call_and_commented_out_ddl(tmp_path: Path) -> None:
    core = "t = Table('events', meta, Column('id', Integer), Column('at', DateTime))\n"
    sql = "-- CREATE TABLE events (id int);\nSELECT 1;\n"
    g = graph_of(tmp_path, {"a": {"m.py": core}, "b": {"x.sql": sql}})
    assert g.schemas == ()  # a comment does not define a table


def test_malformed_sql_does_not_crash(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"a": {"x.sql": "CREATE TABLE broken (id int, ((("}})
    assert g.schemas == ()

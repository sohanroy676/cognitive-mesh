"""Config-name edges (ADR-0037): names only, never a value, never a real `.env`."""

import json
from pathlib import Path

from cognitive_mesh.workspace.render import to_json, to_text
from contracts_support import graph_of, reasons

# built at run time so no secret-shaped literal sits in the source
SECRET_VALUE = "sk_" + "live_" + "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm"  # noqa: S105 - a fake test value

ENV_EXAMPLE = f"""\
PAYMENTS_URL=http://payments.local
STRIPE_KEY={SECRET_VALUE}
export LOG_LEVEL=info
UNUSED_FLAG=1
"""

READER = """\
import os
url = os.environ["PAYMENTS_URL"]
level = os.environ.get("LOG_LEVEL", "warn")
key = os.getenv("NO_ONE_SETS_THIS")
name = "X"
computed = os.environ.get(name)
os.environ["WRITTEN_IN_CODE"] = "1"
"""


def test_reader_links_to_setter_and_generic_names_are_marked(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"pay": {".env.example": ENV_EXAMPLE}, "web": {"app.py": READER}})
    assert {(e.source, e.target, e.name, e.generic) for e in g.config} == {
        ("web", "pay", "PAYMENTS_URL", False),
        ("web", "pay", "LOG_LEVEL", True),
    }
    edge = next(e for e in g.config if e.name == "PAYMENTS_URL")
    assert edge.readers[0].line == 2 and edge.setters[0].path == ".env.example"
    found = reasons(g, "config")
    assert ("web", "no_setter") in found and ("web", "computed_name") in found
    assert ("pay", "unused_setting") in found  # STRIPE_KEY and UNUSED_FLAG nobody reads
    assert "WRITTEN_IN_CODE" not in json.dumps(to_json(g))  # assignment is not a read


def test_no_value_is_stored_anywhere(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"pay": {".env.example": ENV_EXAMPLE}, "web": {"app.py": READER}})
    everything = json.dumps(to_json(g)) + to_text(g) + repr(g)
    assert SECRET_VALUE not in everything
    assert "payments.local" not in everything


def test_real_env_file_is_never_read(tmp_path: Path) -> None:
    g = graph_of(
        tmp_path,
        {
            "pay": {".env": "ONLY_IN_REAL_ENV=1\n"},
            "web": {"a.py": "import os\nos.getenv('ONLY_IN_REAL_ENV')\n"},
        },
    )
    assert g.config == ()
    assert ("web", "no_setter") in reasons(g, "config")


def test_secret_looking_name_is_dropped_and_counted(tmp_path: Path) -> None:
    odd = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"
    g = graph_of(tmp_path, {"a": {".env.example": f"{odd}=1\nGOOD_NAME=2\n"}})
    assert odd not in json.dumps(to_json(g))
    assert any(d.code == "secret_like_name" for d in g.diagnostics)
    assert ("a", "unused_setting") in reasons(g, "config")


def test_compose_dockerfile_and_javascript(tmp_path: Path) -> None:
    compose = (
        "services:\n  api:\n    env_file: .env\n    environment:\n      DB_HOST: db\n"
        "  worker:\n    environment:\n      - QUEUE_URL=amqp://x\n      - BARE_NAME\n"
    )
    docker = "FROM python\nENV APP_HOME=/app APP_MODE production\nENV SINGLE value\nARG BUILD_ID\n"
    js = (
        "const a = process.env.DB_HOST;\nconst b = process.env['QUEUE_URL'];\n"
        "const c = process.env[k];\n"
    )
    py = (
        "import os\nos.getenv('APP_HOME'); os.getenv('SINGLE')\n"
        "os.getenv('BUILD_ID'); os.getenv('BARE_NAME')\n"
    )
    g = graph_of(
        tmp_path,
        {
            "infra": {"docker-compose.yml": compose, "Dockerfile": docker},
            "ui": {"src/x.js": js},
            "svc": {"m.py": py},
        },
    )
    names = {(e.source, e.name) for e in g.config}
    assert names == {
        ("ui", "DB_HOST"),
        ("ui", "QUEUE_URL"),
        ("svc", "APP_HOME"),
        ("svc", "SINGLE"),
        ("svc", "BUILD_ID"),
        ("svc", "BARE_NAME"),
    }
    assert ("ui", "computed_name") in reasons(g, "config")
    assert any(d.code == "env_file_not_read" for d in g.diagnostics)
    assert "amqp" not in json.dumps(to_json(g))


def test_two_setters_make_the_edge_ambiguous(tmp_path: Path) -> None:
    g = graph_of(
        tmp_path,
        {
            "a": {".env.example": "SHARED_NAME=1\n"},
            "b": {".env.example": "SHARED_NAME=2\n"},
            "c": {"x.py": "import os\nos.getenv('SHARED_NAME')\n"},
        },
    )
    assert {(e.target, e.ambiguous, e.candidates) for e in g.config} == {
        ("a", True, ("a", "b")),
        ("b", True, ("a", "b")),
    }

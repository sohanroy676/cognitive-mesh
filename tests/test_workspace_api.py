"""API contract edges (ADR-0037): equal method, aligned path, a label for every match."""

import json
from pathlib import Path

from contracts_support import FASTAPI, api_pairs, graph_of, reasons

PY_CLIENT = """\
import os
import requests
import httpx

BASE = os.environ.get("API_URL")
client = httpx.Client()
requests.get("http://anything.example/users?x=1")
requests.get("/users/42")
requests.get("/users/{id}")
requests.delete("/users")
requests.get(BASE + "/users")
requests.post(f"{BASE}/orders")
requests.post(f"/orders/{BASE}")
client.get("/api/v1/items")
requests.get("/nobody/serves/this")
"""


def test_python_matches_are_labelled_and_nothing_is_guessed(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"api": {"app.py": FASTAPI}, "web": {"client.py": PY_CLIENT}})
    assert api_pairs(g) == {
        ("web", "api", "GET", "/users", "exact"),
        ("web", "api", "GET", "/users/{user_id}", "value_for_param"),
        ("web", "api", "GET", "/users/{user_id}", "param_style"),
        ("web", "api", "GET", "/api/v1/items", "exact"),
        ("web", "api", "POST", "/orders", "exact"),
    }
    base = [e for e in g.api if e.path == "/orders"]
    assert base[0].caveats == ("base_unknown",)
    found = reasons(g, "api")
    assert ("web", "method_mismatch") in found  # DELETE /users: GET exists
    assert ("web", "computed_url") in found  # BASE + "/users" and f"/orders/{BASE}"
    assert ("web", "no_provider") in found
    # the host is never used: the http://anything.example call matched on its path only
    assert all("anything" not in json.dumps(e.model_dump()) for e in g.api)


def test_evidence_names_both_locations(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"api": {"app.py": FASTAPI}, "web": {"client.py": PY_CLIENT}})
    edge = next(e for e in g.api if e.path == "/users" and e.match == "exact")
    assert (edge.consumer.project, edge.consumer.path, edge.consumer.line) == (
        "web",
        "client.py",
        7,
    )
    assert (edge.provider.project, edge.provider.path, edge.provider.line) == ("api", "app.py", 8)


def test_router_mount_prefix_and_unmounted_router(tmp_path: Path) -> None:
    g = graph_of(tmp_path, {"api": {"app.py": FASTAPI}, "web": {"c.py": PY_CLIENT}})
    # /api (mount) + /v1 (router) + /items; the router that is never mounted here is not matched
    assert any(e.path == "/api/v1/items" for e in g.api)
    assert ("api", "prefix_unknown") in reasons(g, "api")


def test_flask_route_default_get_and_blueprint_prefix(tmp_path: Path) -> None:
    flask = (
        "from flask import Flask, Blueprint\napp = Flask(__name__)\n"
        "bp = Blueprint('x', __name__, url_prefix='/ignored')\n"
        "@app.route('/ping')\ndef ping(): ...\n"
        "@app.route('/save', methods=['POST', 'PUT'])\ndef save(): ...\n"
        "@bp.route('/b')\ndef b(): ...\n"
        "app.register_blueprint(bp, url_prefix='/bp')\n"
    )
    client = (
        "import requests\nrequests.get('/ping')\nrequests.put('/save')\n"
        "requests.get('/bp/b')\nrequests.get('/ignored/b')\n"
    )
    g = graph_of(tmp_path, {"srv": {"a.py": flask}, "cli": {"c.py": client}})
    assert {(e.method, e.path) for e in g.api} == {
        ("GET", "/ping"),
        ("PUT", "/save"),
        ("GET", "/bp/b"),
    }


def test_javascript_consumers_and_express_provider(tmp_path: Path) -> None:
    server = (
        "const express = require('express');\nconst app = express();\n"
        "const router = express.Router();\nconst stray = express.Router();\n"
        "app.get('/ping', h);\nrouter.get('/status', h);\napp.post('/orders/:id', h);\n"
        "stray.get('/lost', h);\napp.use('/api', router);\n"
    )
    client = (
        "fetch('/ping');\nfetch(`${API}/ping`);\naxios.post('/orders/7');\n"
        "axios({ method: 'get', url: '/api/status' });\nfetch('/ping', { method: 'DELETE' });\n"
        "fetch(url);\nfetch('/lost');\nfetch('/ping', { method: verb });\n"
    )
    g = graph_of(tmp_path, {"srv": {"server.js": server}, "ui": {"client.ts": client}})
    assert api_pairs(g) == {
        ("ui", "srv", "GET", "/ping", "exact"),
        ("ui", "srv", "POST", "/orders/:id", "value_for_param"),
        ("ui", "srv", "GET", "/api/status", "exact"),
    }
    assert sum(e.caveats == ("base_unknown",) for e in g.api) == 1
    found = reasons(g, "api")
    assert {("ui", "method_mismatch"), ("ui", "computed_url"), ("ui", "computed_method")} <= found
    assert ("ui", "no_provider") in found  # /lost sits on a router mounted nowhere
    assert ("srv", "prefix_unknown") in found


OPENAPI_YAML = """\
openapi: 3.0.0
servers:
  - url: https://svc.example/base
paths:
  /reports/{id}:
    get: {}
    put: {}
  /health:
    get: {}
"""


def test_openapi_yaml_and_json_with_server_prefix(tmp_path: Path) -> None:
    spec_json = json.dumps({"swagger": "2.0", "basePath": "/v2", "paths": {"/ping": {"get": {}}}})
    client = "import requests\nrequests.get('/base/reports/9')\nrequests.get('/v2/ping')\n"
    g = graph_of(
        tmp_path,
        {
            "svc": {"openapi.yaml": OPENAPI_YAML},
            "old": {"swagger.json": spec_json},
            "cli": {"c.py": client},
        },
    )
    assert api_pairs(g) == {
        ("cli", "svc", "GET", "/base/reports/{id}", "value_for_param"),
        ("cli", "old", "GET", "/v2/ping", "exact"),
    }
    edge = next(e for e in g.api if e.target == "svc")
    assert edge.provider.path == "openapi.yaml" and edge.provider.line == 5


def test_malformed_and_hostile_specs_do_not_crash(tmp_path: Path) -> None:
    deep = "openapi: 3\npaths:\n" + "".join("  " * i + f"k{i}:\n" for i in range(1, 80))
    aliases = "openapi: 3\nx: &a [1]\n" + "".join(f"y{i}: *a\n" for i in range(150))
    g = graph_of(
        tmp_path,
        {
            "a": {"openapi.yaml": "openapi: [unclosed\n  - : :"},
            "b": {"openapi.yml": deep},
            "c": {"openapi.json": "{not json openapi"},
            "d": {"swagger.yaml": aliases},
        },
    )
    assert g.api == ()
    # "c" is malformed JSON: ingestion already marks it (parse error) and it is not read again
    assert {d.project for d in g.diagnostics if d.code == "unreadable_document"} == {"a", "b", "d"}


def test_large_openapi_file(tmp_path: Path) -> None:
    paths = {f"/r{i}/{{id}}": {"get": {"summary": "x" * 20}} for i in range(9000)}
    spec = json.dumps({"openapi": "3.0.0", "paths": paths})
    assert len(spec) < 1024 * 1024
    client = "import requests\nrequests.get('/r8999/5')\n"
    g = graph_of(tmp_path, {"big": {"openapi.json": spec}, "cli": {"c.py": client}})
    assert api_pairs(g) == {("cli", "big", "GET", "/r8999/{id}", "value_for_param")}


def test_two_providers_are_ambiguous_and_none_is_chosen(tmp_path: Path) -> None:
    srv = "from flask import Flask\napp = Flask(__name__)\n@app.get('/x')\ndef x(): ...\n"
    g = graph_of(
        tmp_path,
        {
            "a": {"s.py": srv},
            "b": {"s.py": srv},
            "c": {"c.py": "import requests\nrequests.get('/x')\n"},
        },
    )
    assert {(e.target, e.ambiguous, e.candidates) for e in g.api} == {
        ("a", True, ("a", "b")),
        ("b", True, ("a", "b")),
    }


def test_a_project_never_links_to_itself(tmp_path: Path) -> None:
    both = FASTAPI + "\nimport requests\nrequests.get('/users')\n"
    g = graph_of(tmp_path, {"solo": {"app.py": both}})
    assert g.api == ()
    assert ("solo", "no_provider") in reasons(g, "api")


def test_consumer_parameter_does_not_match_a_literal_route(tmp_path: Path) -> None:
    srv = "from flask import Flask\napp = Flask(__name__)\n@app.get('/users/me')\ndef x(): ...\n"
    cli = "import requests\nrequests.get('/users/<id>')\n"
    g = graph_of(tmp_path, {"srv": {"s.py": srv}, "cli": {"c.py": cli}})
    assert g.api == ()

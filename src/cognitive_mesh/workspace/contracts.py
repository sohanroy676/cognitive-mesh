"""API contract edges (ADR-0037): a consumer's literal method and path against a provider's.

Provider: OpenAPI/Swagger files and literal route declarations (FastAPI, Flask, Express).
Consumer: requests, httpx, fetch and axios calls with a literal method and path.
A match needs equal methods and aligned path segments; every match says how exact it is.
The host is never used. Anything computed stays unresolved.
"""

import ast
import re
from typing import Any
from urllib.parse import urlsplit

from cognitive_mesh.graph.js_parse import JsArg, JsCallResult
from cognitive_mesh.workspace.facts import Call, ProjectFacts, Route
from cognitive_mesh.workspace.models import ApiEdge, Site, Unresolved

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_UNSUPPORTED = re.compile(r"[*()?+]")
_RANK = {"exact": 0, "param_style": 1, "value_for_param": 2}
_MAX_DETAIL = 200
_UNKNOWN = "\0"  # a prefix exists but is not a literal


def site_of(project: str, path: str, line: int | None, detail: str) -> Site:
    return Site(project=project, path=path, line=line, detail=detail[:_MAX_DETAIL])


def clean_path(raw: str) -> str:
    """Drop query and fragment and a trailing slash."""
    path = re.split(r"[?#]", raw, maxsplit=1)[0]
    return path.rstrip("/") or "/"


def classify_url(kind: str, value: str | None) -> tuple[str | None, str | None, tuple[str, ...]]:
    """(path, reason, caveats) for a literal URL, a `${BASE}/path` template, or something else."""
    if kind == "base_template" and value is not None and value.startswith("/"):
        return clean_path(value), None, ("base_unknown",)
    if kind == "literal" and value is not None:
        if value.startswith(("http://", "https://")):
            return clean_path(urlsplit(value).path or "/"), None, ()
        if value.startswith("/"):
            return clean_path(value), None, ()
        return None, "relative_url", ()
    return None, "computed_url", ()


def _join(prefix: str, path: str) -> str:
    return clean_path("/" + prefix.strip("/") + "/" + path.strip("/")) if prefix else path


def _route(
    project: str, file: str, line: int | None, method: str, path: str, problem: str | None
) -> Route:
    if problem is None and _UNSUPPORTED.search(path):
        problem = "unsupported_pattern"
    upper = method.upper()
    return Route(upper, path, site_of(project, file, line, f"{upper} {path}"), problem)


def _unresolved(project: str, reason: str, site: Site) -> Unresolved:
    return Unresolved(project=project, kind="api", reason=reason, site=site)


# --- OpenAPI / Swagger ------------------------------------------------------------------------


def _literal_prefix(url: str) -> str:
    path = urlsplit(url).path if "//" in url else url
    return "" if "{" in path or "}" in path else path


def _find_line(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return None


def openapi_routes(project: str, file: str, text: str, doc: Any) -> list[Route]:
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc):
        return []
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return []
    prefix = ""
    servers = doc.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = servers[0].get("url")
        prefix = _literal_prefix(url) if isinstance(url, str) else ""
    elif isinstance(doc.get("basePath"), str):
        prefix = _literal_prefix(doc["basePath"])
    found: list[Route] = []
    for raw, item in paths.items():
        if not isinstance(raw, str) or not raw.startswith("/") or not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            if method in item:
                found.append(
                    _route(project, file, _find_line(text, raw), method, _join(prefix, raw), None)
                )
    return found


# --- Python (ast) -----------------------------------------------------------------------------


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _py_url(node: ast.AST | None) -> tuple[str, str | None]:
    text = _const_str(node)
    if text is not None:
        return "literal", text
    if isinstance(node, ast.JoinedStr) and len(node.values) == 2:
        first, second = node.values
        tail = _const_str(second)
        if isinstance(first, ast.FormattedValue) and tail is not None:
            return "base_template", tail
    return "computed", None


def imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _ctor_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _prefix_arg(call: ast.Call, key: str) -> str:
    given = _kw(call, key)
    if given is None:
        return ""
    return _const_str(given) or _UNKNOWN


def _py_objects(tree: ast.AST) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """({router name: (constructor, own prefix)}, {app names}) from simple assignments."""
    routers: dict[str, tuple[str, str]] = {}
    apps: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        ctor = _ctor_name(value)
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        if ctor in ("FastAPI", "Flask"):
            apps.add(target.id)
        elif ctor == "APIRouter":
            routers[target.id] = (ctor, _prefix_arg(value, "prefix"))
        elif ctor == "Blueprint":
            routers[target.id] = (ctor, _prefix_arg(value, "url_prefix"))
    return routers, apps


def _py_mounts(tree: ast.AST, apps: set[str]) -> dict[str, str]:
    """{router name: mount prefix} for routers mounted on a known app in the same file."""
    mounts: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, owner = node.func.attr, node.func.value
        if attr not in ("include_router", "register_blueprint") or not node.args:
            continue
        router = node.args[0]
        if isinstance(owner, ast.Name) and owner.id in apps and isinstance(router, ast.Name):
            key = "prefix" if attr == "include_router" else "url_prefix"
            mounts[router.id] = _prefix_arg(node, key)
    return mounts


def _decorators(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str, ast.Call]]:
    out: list[tuple[str, str, ast.Call]] = []
    for deco in fn.decorator_list:
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and isinstance(deco.func.value, ast.Name)
        ):
            out.append((deco.func.value.id, deco.func.attr, deco))
    return out


def _methods(attr: str, deco: ast.Call) -> list[str]:
    if attr in HTTP_METHODS:
        return [attr]
    if attr != "route":
        return []
    listed = _kw(deco, "methods")
    if isinstance(listed, ast.List | ast.Tuple):
        return [m for e in listed.elts if (m := _const_str(e)) is not None]
    return ["get"]


def _full_prefix(
    owner: str, routers: dict[str, tuple[str, str]], apps: set[str], mounts: dict[str, str]
) -> str | None:
    """The route prefix, or None when it cannot be known from this file."""
    if owner in apps:
        return ""
    if owner not in routers or owner not in mounts:
        return None  # defined elsewhere, or never mounted here
    ctor, own = routers[owner]
    mount = mounts[owner]
    if _UNKNOWN in (own, mount):
        return None
    if ctor == "Blueprint":
        return mount or own  # register_blueprint(url_prefix=...) replaces the blueprint's own
    return mount + own  # FastAPI: include_router prefix is put in front of the router's


def python_routes(project: str, file: str, tree: ast.AST) -> list[Route]:
    if not imported_modules(tree) & {"fastapi", "flask"}:
        return []
    routers, apps = _py_objects(tree)
    mounts = _py_mounts(tree, apps)
    found: list[Route] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for owner, attr, deco in _decorators(node):
            path = _const_str(deco.args[0]) if deco.args else None
            if path is None or not path.startswith("/"):
                continue
            prefix = _full_prefix(owner, routers, apps, mounts)
            for method in _methods(attr, deco):
                if prefix is None:
                    found.append(_route(project, file, deco.lineno, method, path, "prefix_unknown"))
                else:
                    full = _join(prefix, path)
                    found.append(_route(project, file, deco.lineno, method, full, None))
    return found


def _client_names(tree: ast.AST, libs: set[str]) -> set[str]:
    clients = set(libs)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            target, value = node.optional_vars, node.context_expr
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and _ctor_name(value) in ("Client", "AsyncClient", "Session")
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in libs
        ):
            clients.add(target.id)
    return clients


def python_calls(project: str, file: str, tree: ast.AST) -> tuple[list[Call], list[Unresolved]]:
    libs = imported_modules(tree) & {"requests", "httpx"}
    if not libs:
        return [], []
    clients = _client_names(tree, libs)
    calls: list[Call] = []
    unresolved: list[Unresolved] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in clients
            and node.func.attr in HTTP_METHODS
        ):
            continue
        method = node.func.attr.upper()
        kind, value = _py_url(node.args[0] if node.args else _kw(node, "url"))
        path, reason, caveats = classify_url(kind, value)
        if path is None:
            where = site_of(project, file, node.lineno, f"{method} (not a plain path)")
            unresolved.append(_unresolved(project, str(reason), where))
        else:
            where = site_of(project, file, node.lineno, f"{method} {path}")
            calls.append(Call(method, path, where, caveats))
    return calls, unresolved


# --- JavaScript / TypeScript ------------------------------------------------------------------


def _js_mount_prefix(arg: JsArg) -> str:
    return arg.value if arg.kind == "literal" and arg.value is not None else _UNKNOWN


def _express_objects(parsed: JsCallResult) -> dict[str, str]:
    return {
        b.name: ("app" if b.callee == "express" else "router")
        for b in parsed.bindings
        if b.callee in ("express", "express.Router", "Router")
    }


def _express_mounts(parsed: JsCallResult, kinds: dict[str, str]) -> dict[str, str]:
    mounts: dict[str, str] = {}
    for call in parsed.calls:
        if call.callee is None or not call.callee.endswith(".use"):
            continue
        if kinds.get(call.callee[: -len(".use")]) != "app":
            continue
        args = list(call.args)
        prefix = ""
        if args and args[0].kind != "name":
            prefix = _js_mount_prefix(args[0])
            args = args[1:]
        if args and args[0].kind == "name" and args[0].value in kinds:
            mounts[args[0].value] = prefix
    return mounts


def js_routes(project: str, file: str, parsed: JsCallResult) -> list[Route]:
    kinds = _express_objects(parsed)
    if not kinds:
        return []
    mounts = _express_mounts(parsed, kinds)
    found: list[Route] = []
    for call in parsed.calls:
        if call.callee is None or "." not in call.callee:
            continue
        owner, _, attr = call.callee.rpartition(".")
        if owner not in kinds or attr not in HTTP_METHODS:
            continue
        first = call.args[0] if call.args else None
        if first is None or first.kind != "literal" or not (first.value or "").startswith("/"):
            continue
        path = first.value or "/"
        mount = "" if kinds[owner] == "app" else mounts.get(owner)
        if mount is None or mount == _UNKNOWN:
            found.append(_route(project, file, call.line, attr, path, "prefix_unknown"))
        else:
            found.append(_route(project, file, call.line, attr, _join(mount, path), None))
    return found


def _literal_method(arg: JsArg | None) -> str | None:
    if arg is not None and arg.kind == "literal" and arg.value:
        return arg.value.lower()
    return None


def _js_request(
    call_callee: str | None, args: tuple[JsArg, ...]
) -> tuple[bool, str | None, JsArg | None]:
    """(is a request, method or None when computed, url argument)."""
    if call_callee == "fetch" and args:
        options = args[1] if len(args) > 1 else None
        given = options.prop("method") if options is not None and options.kind == "object" else None
        return True, "get" if given is None else _literal_method(given), args[0]
    if call_callee and call_callee.startswith("axios.") and args:
        name = call_callee[len("axios.") :]
        if name in HTTP_METHODS:
            return True, name, args[0]
    if call_callee == "axios" and args and args[0].kind == "object":
        url = args[0].prop("url")
        given = args[0].prop("method")
        if url is not None:
            return True, "get" if given is None else _literal_method(given), url
    return False, None, None


def js_calls(project: str, file: str, parsed: JsCallResult) -> tuple[list[Call], list[Unresolved]]:
    calls: list[Call] = []
    unresolved: list[Unresolved] = []
    for call in parsed.calls:
        is_request, method, url = _js_request(call.callee, call.args)
        if not is_request or url is None:
            continue
        if method is None or method not in HTTP_METHODS:
            where = site_of(project, file, call.line, "(method not a literal)")
            unresolved.append(_unresolved(project, "computed_method", where))
            continue
        upper = method.upper()
        path, reason, caveats = classify_url(url.kind, url.value)
        if path is None:
            where = site_of(project, file, call.line, f"{upper} (not a plain path)")
            unresolved.append(_unresolved(project, str(reason), where))
        else:
            where = site_of(project, file, call.line, f"{upper} {path}")
            calls.append(Call(upper, path, where, caveats))
    return calls, unresolved


# --- matching ---------------------------------------------------------------------------------

_PARAM = re.compile(r"^(\{[^{}/]+\}|<[^<>/]+>|:[A-Za-z_]\w*)$")


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def match_label(provider: str, consumer: str) -> str | None:
    """How the consumer path fits the provider path, or None when it does not."""
    have, want = _segments(provider), _segments(consumer)
    if len(have) != len(want):
        return None
    label = "exact"
    for served, asked in zip(have, want, strict=True):
        served_param, asked_param = bool(_PARAM.match(served)), bool(_PARAM.match(asked))
        if served_param and asked_param:
            step = "param_style"
        elif served_param:
            step = "value_for_param"
        elif asked_param or served != asked:
            return None
        else:
            step = "exact"
        if _RANK[step] > _RANK[label]:
            label = step
    return label


def _candidates(
    facts: dict[str, ProjectFacts], source: str, call: Call
) -> tuple[dict[str, tuple[int, str, Route, str]], list[tuple[str, Route]]]:
    """The best matching route per provider project, and routes of other methods that fit."""
    best: dict[str, tuple[int, str, Route, str]] = {}
    near: list[tuple[str, Route]] = []
    for target in sorted(facts):
        if target == source:
            continue
        for route in facts[target].routes:
            label = None if route.problem is not None else match_label(route.path, call.path)
            if label is None:
                continue
            if route.method != call.method:
                near.append((target, route))
                continue
            key = (_RANK[label], route.path)
            if target not in best or key < best[target][:2]:
                best[target] = (key[0], route.path, route, label)
    return best, near


def link_api(facts: dict[str, ProjectFacts]) -> tuple[list[ApiEdge], list[Unresolved]]:
    """Match every project's calls to the other projects' routes. Pure; output is sorted."""
    edges: list[ApiEdge] = []
    unresolved: list[Unresolved] = []
    for name in sorted(facts):
        unresolved.extend(
            _unresolved(name, route.problem, route.site)
            for route in facts[name].routes
            if route.problem is not None
        )
    for source in sorted(facts):
        unresolved.extend(u for u in facts[source].unresolved if u.kind == "api")
        for call in facts[source].calls:
            best, near = _candidates(facts, source, call)
            if not best:
                if near:
                    target, route = near[0]
                    detail = f"{call.site.detail}; {target} serves {route.method} {route.path}"
                    where = site_of(source, call.site.path, call.site.line, detail)
                    unresolved.append(_unresolved(source, "method_mismatch", where))
                else:
                    unresolved.append(_unresolved(source, "no_provider", call.site))
                continue
            owners = tuple(sorted(best))
            for target in owners:
                _, _, route, label = best[target]
                edges.append(
                    ApiEdge(
                        source=source,
                        target=target,
                        method=route.method,
                        path=route.path,
                        match=label,
                        caveats=call.caveats,
                        consumer=call.site,
                        provider=route.site,
                        ambiguous=len(owners) > 1,
                        candidates=owners if len(owners) > 1 else (),
                    )
                )
    return edges, unresolved

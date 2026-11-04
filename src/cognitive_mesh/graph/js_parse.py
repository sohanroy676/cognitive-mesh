"""JS/TS import extraction with tree-sitter (ADR-0007). The only module that imports tree-sitter.

Text in, plain records out. Nothing is executed or imported from the analysed code. Only real
syntax nodes are matched, so text in comments, strings, template text, regex literals and JSX
text cannot produce an import. Subtrees with syntax errors are ignored and reported.
"""

from dataclasses import dataclass
from functools import cache
from typing import Literal

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

_MAX_CITE = 200
_GRAMMAR_BY_SUFFIX: dict[str, Literal["javascript", "typescript", "tsx"]] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass(frozen=True)
class JsImport:
    line: int
    rule: str
    specifier: str | None  # None when the target is computed (not a plain literal)
    cited: str  # the matched statement or call, clipped


@dataclass(frozen=True)
class JsParseResult:
    imports: tuple[JsImport, ...]
    has_error: bool  # syntax errors found; imports inside erroneous regions were skipped


def supports(suffix: str) -> bool:
    return suffix.lower() in _GRAMMAR_BY_SUFFIX


@cache
def _parser(grammar: str) -> Parser:
    if grammar == "javascript":
        language = Language(tree_sitter_javascript.language())
    elif grammar == "typescript":
        language = Language(tree_sitter_typescript.language_typescript())
    else:
        language = Language(tree_sitter_typescript.language_tsx())
    return Parser(language)


def _cite(node: Node) -> str:
    raw = node.text or b""
    text = raw[: _MAX_CITE + 1].decode("utf-8", errors="replace")
    return " ".join(text.split())[:_MAX_CITE]


def _literal(node: Node | None) -> str | None:
    """The value of a plain string or substitution-free template string, else None."""
    if node is None:
        return None
    if node.type == "string":
        parts = [c for c in node.children if c.type not in ('"', "'")]
        if all(c.type == "string_fragment" for c in parts) and parts:
            return b"".join(c.text or b"" for c in parts).decode("utf-8", errors="replace")
        return None
    if node.type == "template_string":
        inner = [c for c in node.children if c.type != "`"]
        if all(c.type == "string_fragment" for c in inner) and inner:
            return b"".join(c.text or b"" for c in inner).decode("utf-8", errors="replace")
    return None


def _args(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [c for c in arguments.named_children if c.type != "comment"]


def _match(node: Node) -> JsImport | None:
    line = node.start_point[0] + 1
    if node.type == "import_statement":
        require = next((c for c in node.children if c.type == "import_require_clause"), None)
        if require is not None:
            source = next((c for c in require.children if c.type == "string"), None)
            return JsImport(line, "ts:import-require", _literal(source), _cite(node))
        rule = "ts:import-type" if any(c.type == "type" for c in node.children) else "js:import"
        return JsImport(line, rule, _literal(node.child_by_field_name("source")), _cite(node))
    if node.type == "export_statement":
        source = node.child_by_field_name("source")
        if source is not None:
            return JsImport(line, "js:export-from", _literal(source), _cite(node))
        return None
    if node.type == "call_expression":
        callee = node.child_by_field_name("function")
        if callee is None:
            return None
        args = _args(node)
        if callee.type == "import":
            return JsImport(
                line, "js:dynamic-import", _literal(args[0]) if args else None, _cite(node)
            )
        if callee.type == "identifier" and callee.text == b"require":
            return JsImport(
                line, "js:require", _literal(args[0]) if len(args) == 1 else None, _cite(node)
            )
    return None


def parse_js_imports(suffix: str, text: str) -> JsParseResult:
    """Extract imports from JS/TS source text. Raises ValueError for an unsupported suffix."""
    grammar = _GRAMMAR_BY_SUFFIX.get(suffix.lower())
    if grammar is None:
        raise ValueError(f"unsupported suffix: {suffix}")
    tree = _parser(grammar).parse(text.encode("utf-8"))
    found: list[JsImport] = []
    has_error = tree.root_node.has_error
    stack: list[Node] = [tree.root_node]
    while stack:  # iterative: deeply nested input must not hit the recursion limit
        node = stack.pop()
        if node.is_error or node.is_missing:
            continue
        match = _match(node)
        if match is not None and not node.has_error:
            found.append(match)
        stack.extend(reversed(node.children))
    found.sort(key=lambda item: (item.line, item.rule, item.cited))
    return JsParseResult(imports=tuple(found), has_error=has_error)


# --- calls, bindings and env reads for workspace contracts (ADR-0037) -------------------------


@dataclass(frozen=True)
class JsArg:
    """One call argument, reduced to what matching needs. Nothing computed is evaluated."""

    kind: Literal["literal", "base_template", "name", "object", "computed"]
    value: str | None = None  # literal text; for base_template the text after the leading ${...}
    props: tuple[tuple[str, "JsArg"], ...] = ()  # object properties with plain-identifier keys

    def prop(self, key: str) -> "JsArg | None":
        return next((v for k, v in self.props if k == key), None)


@dataclass(frozen=True)
class JsCall:
    line: int
    callee: str | None  # dotted identifier text such as `axios.get`; None when not that shape
    args: tuple[JsArg, ...]


@dataclass(frozen=True)
class JsBinding:
    """`const NAME = callee(...)`: tells an express app or router from other objects."""

    line: int
    name: str
    callee: str | None


@dataclass(frozen=True)
class JsEnvRead:
    line: int
    name: str | None  # None when the key is computed


@dataclass(frozen=True)
class JsCallResult:
    calls: tuple[JsCall, ...]
    bindings: tuple[JsBinding, ...]
    env_reads: tuple[JsEnvRead, ...]
    has_error: bool


def _dotted(node: Node | None) -> str | None:
    parts: list[str] = []
    while node is not None:
        if node.type == "identifier":
            parts.append((node.text or b"").decode("utf-8", errors="replace"))
            return ".".join(reversed(parts))
        if node.type != "member_expression":
            return None
        prop = node.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return None
        parts.append((prop.text or b"").decode("utf-8", errors="replace"))
        node = node.child_by_field_name("object")
    return None


def _arg(node: Node, depth: int = 0) -> JsArg:
    literal = _literal(node)
    if literal is not None:
        return JsArg("literal", literal)
    if node.type == "identifier":
        return JsArg("name", (node.text or b"").decode("utf-8", errors="replace"))
    if node.type == "template_string":
        inner = [c for c in node.children if c.type != "`"]
        if (
            len(inner) == 2
            and inner[0].type == "template_substitution"
            and inner[1].type == "string_fragment"
        ):
            return JsArg("base_template", (inner[1].text or b"").decode("utf-8", errors="replace"))
    if node.type == "object" and depth < 2:
        props: list[tuple[str, JsArg]] = []
        for pair in node.named_children:
            if pair.type != "pair":
                continue
            key = pair.child_by_field_name("key")
            val = pair.child_by_field_name("value")
            if key is None or val is None or key.type not in ("property_identifier", "string"):
                continue
            name = _literal(key) if key.type == "string" else (key.text or b"").decode()
            if name:
                props.append((name, _arg(val, depth + 1)))
        return JsArg("object", None, tuple(props))
    return JsArg("computed")


def _is_process_env(node: Node | None) -> bool:
    return node is not None and _dotted(node) == "process.env"


def _env_read(node: Node) -> JsEnvRead | None:
    line = node.start_point[0] + 1
    if node.type == "member_expression" and _is_process_env(node.child_by_field_name("object")):
        prop = node.child_by_field_name("property")
        if prop is not None and prop.type == "property_identifier":
            return JsEnvRead(line, (prop.text or b"").decode("utf-8", errors="replace"))
    if node.type == "subscript_expression" and _is_process_env(node.child_by_field_name("object")):
        return JsEnvRead(line, _literal(node.child_by_field_name("index")))
    return None


def parse_js_calls(suffix: str, text: str) -> JsCallResult:
    """Extract calls, `const X = f(...)` bindings and `process.env` reads from JS/TS text."""
    grammar = _GRAMMAR_BY_SUFFIX.get(suffix.lower())
    if grammar is None:
        raise ValueError(f"unsupported suffix: {suffix}")
    tree = _parser(grammar).parse(text.encode("utf-8"))
    calls: list[JsCall] = []
    bindings: list[JsBinding] = []
    reads: list[JsEnvRead] = []
    stack: list[Node] = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            continue
        if not node.has_error:
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                calls.append(
                    JsCall(
                        node.start_point[0] + 1,
                        _dotted(function),
                        tuple(_arg(a) for a in _args(node)),
                    )
                )
            elif node.type == "variable_declarator":
                name = node.child_by_field_name("name")
                init = node.child_by_field_name("value")
                if (
                    name is not None
                    and name.type == "identifier"
                    and init is not None
                    and init.type == "call_expression"
                ):
                    callee = _dotted(init.child_by_field_name("function"))
                    bindings.append(
                        JsBinding(
                            node.start_point[0] + 1,
                            (name.text or b"").decode("utf-8", errors="replace"),
                            callee,
                        )
                    )
            else:
                read = _env_read(node)
                if read is not None:
                    reads.append(read)
        stack.extend(reversed(node.children))
    key = lambda item: item.line  # noqa: E731
    return JsCallResult(
        calls=tuple(sorted(calls, key=key)),
        bindings=tuple(sorted(bindings, key=key)),
        env_reads=tuple(sorted(reads, key=key)),
        has_error=tree.root_node.has_error,
    )


# --- exports and named imports for impact analysis (ADR-0041) --------------------------------


@dataclass(frozen=True)
class JsExports:
    """The names a module exports. `incomplete` is True when the list cannot be trusted:
    `export * from`, a destructured export, or syntax errors."""

    names: tuple[str, ...]
    incomplete: bool


@dataclass(frozen=True)
class JsNamedImport:
    """What one import or `require` takes from a module. `whole` is True when the code binds the
    module as a whole (default, namespace or plain `require`), so the names used are not known."""

    line: int
    module: str
    names: tuple[str, ...]
    whole: bool


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", errors="replace") if node is not None else ""


_DECLARED_NAME_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "function_signature",
        "module",
    }
)


def _declared_names(declaration: Node) -> tuple[list[str], bool]:
    """(names, incomplete) of the declaration in `export <declaration>`."""
    if declaration.type in _DECLARED_NAME_TYPES:
        name = _text(declaration.child_by_field_name("name"))
        return ([name] if name else []), not name
    if declaration.type in ("lexical_declaration", "variable_declaration"):
        names: list[str] = []
        incomplete = False
        for child in declaration.named_children:
            if child.type != "variable_declarator":
                continue
            target = child.child_by_field_name("name")
            if target is not None and target.type == "identifier":
                names.append(_text(target))
            else:
                incomplete = True  # destructuring: the exported names are computed
        return names, incomplete
    return [], True


def parse_js_exports(suffix: str, text: str) -> JsExports:
    """The top-level `export` names of JS/TS text. Raises ValueError for an unsupported suffix."""
    grammar = _GRAMMAR_BY_SUFFIX.get(suffix.lower())
    if grammar is None:
        raise ValueError(f"unsupported suffix: {suffix}")
    tree = _parser(grammar).parse(text.encode("utf-8"))
    names: list[str] = []
    incomplete = tree.root_node.has_error
    for statement in tree.root_node.named_children:
        if statement.type != "export_statement" or statement.has_error:
            continue
        if any(c.type == "*" for c in statement.children):
            incomplete = True
            continue
        if any(c.type == "default" for c in statement.children):
            names.append("default")
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is not None:
            found, partial = _declared_names(declaration)
            names.extend(found)
            incomplete = incomplete or partial
            continue
        clause = next((c for c in statement.named_children if c.type == "export_clause"), None)
        if clause is not None:
            for spec in clause.named_children:
                if spec.type == "export_specifier":
                    label = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                    if label is not None:
                        names.append(_text(label))
            continue
        if any(c.type == "namespace_export" for c in statement.named_children):
            node = next(c for c in statement.named_children if c.type == "namespace_export")
            names.append(_text(node.named_children[0]) if node.named_children else "")
    return JsExports(tuple(sorted({n for n in names if n})), incomplete)


def _import_names(statement: Node) -> tuple[list[str], bool]:
    clause = next((c for c in statement.named_children if c.type == "import_clause"), None)
    if clause is None:
        return [], True  # `import "x"`: nothing is named
    names: list[str] = []
    whole = False
    for part in clause.named_children:
        if part.type == "identifier":
            names.append("default")
        elif part.type == "namespace_import":
            whole = True
        elif part.type == "named_imports":
            names.extend(
                _text(spec.child_by_field_name("name"))
                for spec in part.named_children
                if spec.type == "import_specifier"
            )
    return names, whole


def _require_names(declarator: Node) -> tuple[str, list[str], bool] | None:
    value = declarator.child_by_field_name("value")
    if value is None or value.type != "call_expression":
        return None
    callee = value.child_by_field_name("function")
    args = _args(value)
    if (
        callee is None
        or callee.type != "identifier"
        or _text(callee) != "require"
        or len(args) != 1
    ):
        return None
    module = _literal(args[0])
    if module is None:
        return None
    target = declarator.child_by_field_name("name")
    if target is not None and target.type == "object_pattern":
        names: list[str] = []
        whole = False
        for part in target.named_children:
            if part.type == "shorthand_property_identifier_pattern":
                names.append(_text(part))
            elif part.type == "pair_pattern":
                key = part.child_by_field_name("key")
                if key is not None and key.type == "property_identifier":
                    names.append(_text(key))
                else:
                    whole = True
            else:
                whole = True  # rest element or anything else
        return module, names, whole
    return module, [], True


def parse_js_named_imports(suffix: str, text: str) -> tuple[JsNamedImport, ...]:
    """Each static import and plain `const ... = require("m")`, with the names it takes."""
    grammar = _GRAMMAR_BY_SUFFIX.get(suffix.lower())
    if grammar is None:
        raise ValueError(f"unsupported suffix: {suffix}")
    tree = _parser(grammar).parse(text.encode("utf-8"))
    found: list[JsNamedImport] = []
    stack: list[Node] = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            continue
        line = node.start_point[0] + 1
        if node.type == "import_statement" and not node.has_error:
            module = _literal(node.child_by_field_name("source"))
            if module is not None:
                names, whole = _import_names(node)
                found.append(JsNamedImport(line, module, tuple(sorted(set(names))), whole))
        elif node.type == "variable_declarator" and not node.has_error:
            required = _require_names(node)
            if required is not None:
                module, names, whole = required
                found.append(JsNamedImport(line, module, tuple(sorted(set(names))), whole))
        stack.extend(reversed(node.children))
    return tuple(sorted(found, key=lambda item: (item.line, item.module)))

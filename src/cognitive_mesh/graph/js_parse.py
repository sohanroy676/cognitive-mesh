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

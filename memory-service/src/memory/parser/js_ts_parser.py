import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from memory.models import ParsedEdge, ParsedFile, ParsedSymbol

_GRAMMARS = {
    "typescript": lambda: Language(tsts.language_typescript()),
    "tsx": lambda: Language(tsts.language_tsx()),
    "javascript": lambda: Language(tsjs.language()),
}

_DEF_TYPES = (
    "function_declaration",
    "class_declaration",
    "method_definition",
    "arrow_function",
    "function_expression",
)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8")


def _name_of(node: Node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    return _text(n, src) if n else "<anonymous>"


def _callee_name(call: Node, src: bytes) -> str:
    fn = call.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        return _text(prop, src) if prop else ""
    if fn.type == "identifier":
        return _text(fn, src)
    return ""


class JsTsParser:
    def __init__(self, grammar: str) -> None:
        if grammar not in _GRAMMARS:
            raise ValueError(f"unknown grammar {grammar!r}")
        self._parser = Parser(_GRAMMARS[grammar]())

    def parse(self, path: str, source: str) -> ParsedFile:
        src = source.encode("utf8")
        tree = self._parser.parse(src)
        symbols: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []

        def collect_imports(root: Node) -> None:
            stack = [root]
            while stack:
                n = stack.pop()
                if n.type == "import_statement":
                    s = n.child_by_field_name("source")
                    if s is not None:
                        edges.append(ParsedEdge("<module>", _text(s, src).strip("'\""), "imports"))
                elif n.type == "call_expression":
                    fn = n.child_by_field_name("function")
                    if fn is not None and fn.type == "identifier" and _text(fn, src) == "require":
                        args = n.child_by_field_name("arguments")
                        if args is not None:
                            for a in args.children:
                                if a.type == "string":
                                    edges.append(ParsedEdge("<module>", _text(a, src).strip("'\""), "imports"))
                                    break
                stack.extend(n.children)

        def collect_calls(def_node: Node, owner: str) -> None:
            stack = list(def_node.children)
            while stack:
                n = stack.pop()
                if n.type in _DEF_TYPES:
                    continue  # nested defs own their own calls
                if n.type == "call_expression":
                    fn = n.child_by_field_name("function")
                    is_require = fn is not None and fn.type == "identifier" and _text(fn, src) == "require"
                    if not is_require:
                        callee = _callee_name(n, src)
                        if callee:
                            edges.append(ParsedEdge(owner, callee, "calls"))
                stack.extend(n.children)

        def record(node: Node, name: str, kind: str, scope: str) -> str:
            qualname = f"{scope}.{name}" if scope else name
            symbols.append(
                ParsedSymbol(qualname, name, kind, node.start_point[0] + 1, node.end_point[0] + 1)
            )
            return qualname

        def visit(node: Node, scope: str) -> None:
            for child in node.children:
                if child.type == "function_declaration":
                    qual = record(child, _name_of(child, src), "function", scope)
                    collect_calls(child, qual)
                    visit(child, qual)
                elif child.type == "class_declaration":
                    qual = record(child, _name_of(child, src), "class", scope)
                    visit(child, qual)
                elif child.type == "method_definition":
                    qual = record(child, _name_of(child, src), "method", scope)
                    collect_calls(child, qual)
                    visit(child, qual)
                elif child.type in ("lexical_declaration", "variable_declaration"):
                    for d in child.children:
                        if d.type != "variable_declarator":
                            continue
                        value = d.child_by_field_name("value")
                        if value is not None and value.type in ("arrow_function", "function_expression"):
                            name_node = d.child_by_field_name("name")
                            name = _text(name_node, src) if name_node else "<anonymous>"
                            qual = record(d, name, "function", scope)
                            collect_calls(value, qual)
                            visit(value, qual)
                else:
                    visit(child, scope)

        collect_imports(tree.root_node)
        visit(tree.root_node, "")
        return ParsedFile(path, "javascript", source, symbols, edges)

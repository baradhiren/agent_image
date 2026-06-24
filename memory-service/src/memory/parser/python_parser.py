import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from memory.models import ParsedEdge, ParsedFile, ParsedSymbol

_PY_LANGUAGE = Language(tspython.language())


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8")


def _name_of(node: Node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    return _text(n, src) if n else "<anonymous>"


def _callee_name(call: Node, src: bytes) -> str:
    fn = call.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return _text(attr, src) if attr else ""
    return _text(fn, src)


class PythonParser:
    def __init__(self) -> None:
        self._parser = Parser(_PY_LANGUAGE)

    def parse(self, path: str, source: str) -> ParsedFile:
        src = source.encode("utf8")
        tree = self._parser.parse(src)
        symbols: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []

        def collect_calls(def_node: Node, owner: str) -> None:
            stack = list(def_node.children)
            while stack:
                n = stack.pop()
                if n.type in ("function_definition", "class_definition"):
                    continue
                if n.type == "call":
                    callee = _callee_name(n, src)
                    if callee:
                        edges.append(ParsedEdge(owner, callee, "calls"))
                stack.extend(n.children)

        def collect_imports(node: Node) -> None:
            for n in node.children:
                if n.type == "dotted_name":
                    edges.append(ParsedEdge("<module>", _text(n, src).split(".")[0], "imports"))

        def visit(node: Node, scope: str) -> None:
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    name = _name_of(child, src)
                    qualname = f"{scope}.{name}" if scope else name
                    kind = "class" if child.type == "class_definition" else ("method" if scope else "function")
                    symbols.append(ParsedSymbol(qualname, name, kind, child.start_point[0] + 1, child.end_point[0] + 1))
                    if child.type == "function_definition":
                        collect_calls(child, qualname)
                    visit(child, qualname)
                elif child.type in ("import_statement", "import_from_statement"):
                    collect_imports(child)
                else:
                    visit(child, scope)

        visit(tree.root_node, "")
        return ParsedFile(path, "python", source, symbols, edges)

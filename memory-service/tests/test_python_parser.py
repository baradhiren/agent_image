from memory.parser.python_parser import PythonParser

SAMPLE = '''import os

def helper():
    return os.getcwd()

class Service:
    def run(self):
        return helper()
'''


def test_symbols():
    parsed = PythonParser().parse("svc.py", SAMPLE)
    kinds = {s.qualname: s.kind for s in parsed.symbols}
    assert kinds["helper"] == "function"
    assert kinds["Service"] == "class"
    assert kinds["Service.run"] == "method"


def test_edges():
    parsed = PythonParser().parse("svc.py", SAMPLE)
    calls = {(e.src_qualname, e.dst_name) for e in parsed.edges if e.kind == "calls"}
    imports = {e.dst_name for e in parsed.edges if e.kind == "imports"}
    assert ("Service.run", "helper") in calls
    assert "os" in imports
    assert parsed.language == "python" and parsed.path == "svc.py"

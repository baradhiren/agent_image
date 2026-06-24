from memory.parser.js_ts_parser import JsTsParser

TS_SAMPLE = """import { helper } from './util';

function top(): number {
  return helper();
}

const arrow = (): number => {
  return top();
};

class Service {
  run(): number {
    return arrow();
  }
}
"""

JS_REQUIRE = """const util = require('./util');

function f() {
  return util.doThing();
}
"""


def test_ts_symbols():
    parsed = JsTsParser("typescript").parse("svc.ts", TS_SAMPLE)
    kinds = {s.qualname: s.kind for s in parsed.symbols}
    assert kinds["top"] == "function"
    assert kinds["arrow"] == "function"        # arrow bound to const
    assert kinds["Service"] == "class"
    assert kinds["Service.run"] == "method"
    assert parsed.language == "javascript"


def test_ts_edges():
    parsed = JsTsParser("typescript").parse("svc.ts", TS_SAMPLE)
    calls = {(e.src_qualname, e.dst_name) for e in parsed.edges if e.kind == "calls"}
    imports = {e.dst_name for e in parsed.edges if e.kind == "imports"}
    assert ("top", "helper") in calls
    assert ("arrow", "top") in calls
    assert ("Service.run", "arrow") in calls
    assert "./util" in imports


def test_js_require_and_member_call():
    parsed = JsTsParser("javascript").parse("f.js", JS_REQUIRE)
    imports = {e.dst_name for e in parsed.edges if e.kind == "imports"}
    calls = {(e.src_qualname, e.dst_name) for e in parsed.edges if e.kind == "calls"}
    assert "./util" in imports
    assert ("f", "doThing") in calls          # member_expression -> property name
    assert all(e.dst_name != "require" for e in parsed.edges if e.kind == "calls")


def test_tsx_grammar_loads():
    parsed = JsTsParser("tsx").parse("c.tsx", "function C() { return null; }\n")
    assert {s.qualname for s in parsed.symbols} == {"C"}

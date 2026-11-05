"""Where the analyzer's guarantee stops.

Every case is a small program with a known ground truth: whether it does
something the declaration does not cover. The expected verdict is pinned, so the
boundary of the guarantee is evidenced and not only described.

Outcomes:
  detected         the program bypasses the declaration and the analyzer rejects it
  accepted-bypass  the program bypasses the declaration and the analyzer accepts it
                   (a documented assumption of the guarantee, not a defect we hide)
  false-reject     the program is harmless and the analyzer rejects it
  legit-accepted   the program is harmless and the analyzer accepts it
"""

import sys

import pytest

from pyifc.analysis import analyze_source

HEADER = """\
from pyifc import Application, Bucket
{extra_imports}
app = Application("evasion")
items = Bucket("items")
{module_level}
"""


def _program(body: str, *, module_level: str = "", extra_imports: str = "", declared: str = "@app.read(items)") -> str:
    head = HEADER.format(extra_imports=extra_imports, module_level=module_level)
    handler = f"@app.handler(method='GET', path='/items')\n{declared}\ndef handler():\n"
    indented = "".join(f"    {line}\n" for line in body.strip().splitlines())
    return head + handler + indented


# (id, outcome, expected error code or None, program)
CASES = [
    (
        "undeclared-direct-call",
        "detected",
        "E_UNDECLARED_USE",
        _program("items.put('k', b'v')"),
    ),
    (
        "alias",
        "detected",
        "E_UNSUPPORTED_ALIAS",
        _program("alias = items\nalias.put('k', b'v')"),
    ),
    (
        "bound-method",
        "detected",
        "E_UNSUPPORTED_INDIRECT_USE",
        _program("write = items.put\nwrite('k', b'v')"),
    ),
    (
        "container-in-handler",
        "detected",
        "E_UNSUPPORTED_INDIRECT_USE",
        _program("stores = [items]\nstores[0].put('k', b'v')"),
    ),
    (
        "resource-passed-to-helper",
        "detected",
        "E_UNSUPPORTED_INDIRECT_USE",
        _program("helper(items)"),
    ),
    (
        "getattr",
        "detected",
        "E_UNSUPPORTED_INDIRECT_USE",
        _program("getattr(items, 'put')('k', b'v')"),
    ),
    (
        "nested-function",
        "detected",
        "E_UNSUPPORTED_NESTED_RESOURCE_USE",
        _program("def inner():\n    return items.put('k', b'v')\nreturn inner()"),
    ),
    (
        "transitive-helper-call",
        "detected",
        "E_UNSUPPORTED_HELPER_RESOURCE_USE",
        _program("return helper()") + "def helper():\n    return items.put('k', b'v')\n",
    ),
    (
        "helper-reference-without-call",
        "detected",
        "E_UNSUPPORTED_HELPER_RESOURCE_USE",
        _program("return helper()") + "def helper():\n    store = items\n    return store\n",
    ),
    (
        "helper-in-class-method",
        "detected",
        "E_UNSUPPORTED_HELPER_RESOURCE_USE",
        _program("return Store().run()")
        + "class Store:\n    def run(self):\n        return items.put('k', b'v')\n",
    ),
    (
        "module-level-container",
        "detected",
        "E_UNSUPPORTED_MODULE_LEVEL_USE",
        _program("return STORES[0].put('k', b'v')", module_level="STORES = [items]"),
    ),
    (
        "module-level-alias",
        "detected",
        "E_UNSUPPORTED_MODULE_LEVEL_USE",
        _program("return shared.put('k', b'v')", module_level="shared = items"),
    ),
    (
        "cross-module-resource",
        "detected",
        "E_UNSUPPORTED_CROSS_MODULE_RESOURCE",
        _program("shared_bucket.put('k', b'v')", extra_imports="from other import shared_bucket"),
    ),
    (
        "direct-boto3-import",
        "detected",
        "E_DIRECT_SDK_USE",
        _program("boto3.client('s3').put_object(Bucket='x', Key='k', Body=b'v')", extra_imports="import boto3"),
    ),
    (
        "direct-botocore-from-import",
        "detected",
        "E_DIRECT_SDK_USE",
        _program("return Config()", extra_imports="from botocore.config import Config"),
    ),
    # Reaching a resource without writing its name in the body.
    (
        "default-argument",
        "detected",
        "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
        _program("store.put('k', b'v')").replace("def handler():", "def handler(store=other):")
        .replace('items = Bucket("items")', 'items = Bucket("items")\nother = Bucket("other")'),
    ),
    (
        "keyword-only-default",
        "detected",
        "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
        _program("store.put('k', b'v')").replace("def handler():", "def handler(*, store=other):")
        .replace('items = Bucket("items")', 'items = Bucket("items")\nother = Bucket("other")'),
    ),
    (
        "annotation-mentions-resource",
        "detected",
        "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
        _program("return 1").replace("def handler():", "def handler(x: items = None):"),
    ),
    (
        "foreign-decorator-argument",
        "detected",
        "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
        _program("return 1", declared="@wrap(items)\n@app.read(items)"),
    ),
    (
        "resource-constructed-through-module-attribute",
        "detected",
        "E_RESOURCE_DECLARATION",
        _program("pyifc.Bucket('hidden').put('k', b'v')", extra_imports="import pyifc"),
    ),
    (
        "module-attribute-resource",
        "detected",
        "E_UNSUPPORTED_CROSS_MODULE_RESOURCE",
        _program("helpers.shared.put('k', b'v')", extra_imports="import helpers"),
    ),
    (
        "call-valued-rebinding",
        "detected",
        "E_UNSUPPORTED_RESOURCE_REBINDING",
        _program("return items.get('k')", module_level="items = make_store()"),
    ),
    (
        "global-rebinding",
        "detected",
        "E_UNSUPPORTED_RESOURCE_REBINDING",
        _program("global items\nitems = make_store()\nreturn items.get('k')"),
    ),
    (
        "exec-string",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("exec(\"items.put('k', b'v')\")"),
    ),
    (
        "globals-lookup",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("globals()['items'].put('k', b'v')"),
    ),
    (
        "vars-lookup",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("vars()['items'].put('k', b'v')"),
    ),
    (
        "sys-modules-lookup",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("sys.modules[__name__].items.put('k', b'v')", extra_imports="import sys"),
    ),
    (
        "dunder-import-sdk",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("__import__('boto3').client('s3')"),
    ),
    (
        "importlib-sdk",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("importlib.import_module('boto3')", extra_imports="import importlib"),
    ),
    (
        "component-calls-component",
        "detected",
        "E_UNSUPPORTED_COMPONENT_CALL",
        _program("return writer()", module_level=(
            "@app.handler(method='POST', path='/w', id='writer')\n@app.write(items)\n"
            "def writer():\n    return items.put('k', b'v')\n"
        )).replace('def handler():', 'def handler():', 1),
    ),
    (
        "type-parameter-bound",
        "detected",
        "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
        _program("return handler.__type_params__[0].__bound__.put('k', b'v')").replace(
            "def handler():", "def handler[T: other]():"
        ).replace('items = Bucket("items")', 'items = Bucket("items")\nother = Bucket("other")'),
    ),
    (
        "constructor-alias",
        "detected",
        "E_RESOURCE_DECLARATION",
        _program("return make('other').put('k', b'v')", module_level="make = Bucket"),
    ),
    (
        "builtins-eval",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("return builtins.eval(\"items.put('k', b'v')\")", extra_imports="import builtins"),
    ),
    (
        "from-sys-import-modules",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("return modules[__name__].items.put('k', b'v')", extra_imports="from sys import modules"),
    ),
    (
        "frame-introspection",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("return sys._getframe(0).f_globals['items'].put('k', b'v')", extra_imports="import sys"),
    ),
    (
        "getattr-on-module",
        "detected",
        "E_UNSUPPORTED_DYNAMIC_ACCESS",
        _program("return getattr(mod, 'items').put('k', b'v')", extra_imports="import mod"),
    ),
    # Bypasses the analyzer cannot see. They are the stated proviso of the guarantee:
    # the code of other modules is not analyzed.
    (
        "unseen-module-does-the-write",
        "accepted-bypass",
        None,
        _program("helpers.write_marker()", extra_imports="import helpers"),
    ),
    (
        "unseen-module-returns-a-handle",
        "accepted-bypass",
        None,
        _program("get_store().put('k', b'v')", extra_imports="from helpers import get_store"),
    ),
    (
        "string-built-attribute-path-to-frame-globals",
        "accepted-bypass",
        None,
        _program(
            "try:\n    1 / 0\nexcept ArithmeticError as error:\n    trace = error.__traceback__\n"
            "return operator.attrgetter('tb_frame.f_globals')(trace)['items'].put('k', b'v')",
            extra_imports="import operator",
        ),
    ),
    # Harmless programs.
    (
        "pure-helper",
        "legit-accepted",
        None,
        _program("return parse(items.get('k'))") + "def parse(payload):\n    return payload\n",
    ),
    (
        "declared-and-used",
        "legit-accepted",
        None,
        _program("return items.get('k')"),
    ),
    (
        "stdlib-get-on-dict",
        "legit-accepted",
        None,
        _program("import os\nreturn os.environ.get('X')\n", extra_imports="import os"),
    ),
    # Fail-closed costs: harmless code that the subset rejects.
    (
        "foreign-client-object",
        "false-reject",
        "E_UNSUPPORTED_CROSS_MODULE_RESOURCE",
        _program("return cache.get('k')", extra_imports="from helpers import cache"),
    ),
    (
        "helper-parameter-shadows-resource-name",
        "false-reject",
        "E_UNSUPPORTED_HELPER_RESOURCE_USE",
        _program("return echo('x')") + "def echo(items):\n    return items\n",
    ),
]


_NEEDS_PY312 = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 type parameters need Python 3.12")


@pytest.mark.parametrize(
    ("case_id", "outcome", "code", "source"),
    [pytest.param(*case, marks=_NEEDS_PY312) if case[0] == "type-parameter-bound" else case for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_analyzer_verdict_matches_ground_truth(case_id: str, outcome: str, code: str | None, source: str) -> None:
    result = analyze_source(source)
    error_codes = [item.code for item in result.errors]

    if code is None:
        assert error_codes == [], f"{case_id}: expected acceptance, got {error_codes}"
        assert result.specification is not None
    else:
        assert code in error_codes, f"{case_id}: expected {code}, got {error_codes}"
        assert result.specification is None


def test_outcome_counts_are_the_ones_the_report_cites() -> None:
    counts: dict[str, int] = {}
    for _, outcome, _, _ in CASES:
        counts[outcome] = counts.get(outcome, 0) + 1

    assert counts == {
        "detected": 36,
        "accepted-bypass": 3,
        "legit-accepted": 3,
        "false-reject": 2,
    }


def test_sdk_import_rule_does_not_fire_on_other_modules() -> None:
    source = _program("return items.get('k')", extra_imports="import json\nfrom collections.abc import Callable")

    assert analyze_source(source).errors == ()

"""Lambda diagnostics retain the rejecting expression across enclosing bodies."""

from textwrap import indent
from pathlib import Path

import pytest

from ..compilation_context import activate_compiler
from ..parse.nodes import SourceLocation
from .reject import (ThirUnsupported, begin_attempt, begin_stmt, note_detail)
from .testutil import _assert_byte_identical, _compile, _strict_reject


RECORD = '''from typing import Iterator
from tpy import int32

class Node:
    name: str

    def __init__(self, name: str):
        self.name = name

'''

SORT = '''items = [Node("pear"), Node("apple")]
result = sorted(
    items,
    key=lambda n: n.name,
)
'''


@pytest.mark.parametrize("prefix,depth,suffix", [
    ("def run():\n", 4, ""),
    ("class Test:\n    def run(self):\n", 8, ""),
    ("class Test:\n    def __init__(self):\n", 8, ""),
    ("", 0, ""),
    ("def run() -> Iterator[int32]:\n", 4, "    yield 1\n"),
    ("async def run():\n", 4, ""),
])
def test_lambda_body_reject_location(prefix: str, depth: int, suffix: str) -> None:
    source = RECORD + prefix + indent(SORT, " " * depth) + suffix
    err, _ = _strict_reject(source)
    assert err.reason == "expr.lambda:lambda.body:field.result_type"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "key=lambda" in text)
    assert err.loc is not None and err.loc.line == line


@pytest.mark.parametrize("body,param_type,return_type,expected", [
    ("print(n, end='')", "int32", "None", "lambda.void_body"),
    ("n", "int32 | None", "int32 | None", "lambda.parameter_type"),
])
def test_lambda_argument_gate_retains_cause_and_location(
        body: str, param_type: str, return_type: str, expected: str) -> None:
    invoke = "f(n)" if return_type == "None" else "return f(n)"
    source = f'''from tpy import Fn, int32
def apply(f: Fn[[{param_type}], {return_type}], n: {param_type}) -> {return_type}:
    {invoke}
def run(n: {param_type}):
    apply(
        lambda n: {body},
        n,
    )
'''
    err, _ = _strict_reject(source)
    assert err.reason.endswith(":" + expected)
    assert err.loc is not None and err.loc.line == 6


def test_detail_location_stays_with_its_reason() -> None:
    compiler, _ = _compile("def run():\n    pass\n")
    loc = SourceLocation(line=12, column=8)
    with activate_compiler(compiler):
        begin_attempt()
        begin_stmt()
        note_detail("lambda.parameter_type", loc=loc)
        note_detail("lambda.void_body", loc=SourceLocation(line=19, column=4))
        assert ThirUnsupported("expr.call:lambda.parameter_type").loc == loc
        assert ThirUnsupported("expr.call:unrelated").loc is None
        explicit = SourceLocation(line=7, column=2)
        err = ThirUnsupported("expr.call:lambda.parameter_type", loc=explicit)
        assert err.loc == explicit
        begin_stmt()
        assert ThirUnsupported("expr.call:lambda.parameter_type").loc is None


def test_lambda_diagnostics_leave_supported_twins_accepted() -> None:
    # The named str-field key and scalar lambda key are both supported.
    _assert_byte_identical(RECORD + '''
def key(n: Node) -> str:
    return n.name

def run():
    nodes = [Node("pear"), Node("apple")]
    named = sorted(nodes, key=key)
    scalar = sorted(nodes, key=lambda n: len(n.name))
    print(named[0].name, scalar[0].name)
''')


def test_lambda_argument_reject_names_the_second_lambda() -> None:
    source = '''from tpy import Fn, int32
def apply(first: Fn[[int32], int32], second: Fn[[int32 | None], int32 | None]):
    print(first(1), second(None))
def run():
    apply(
        lambda n: n + 1,
        lambda n: n,
    )
'''
    err, _ = _strict_reject(source)
    assert err.reason.endswith(":lambda.parameter_type")
    assert err.loc is not None and err.loc.line == 7


@pytest.mark.parametrize("case,cause", [
    ("error_lambda_nocopy_snapshot", "capture_nocopy"),
    ("error_frame_lambda_nocopy_snapshot", "capture_nocopy"),
    ("error_frame_lambda_ref_capture", "capture_frame_slot"),
    ("error_lambda_narrowed_escaping_capture", "capture_narrowed_reference"),
])
def test_lambda_capture_reject_location(case: str, cause: str) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "tests/cases/nested_def" / case / "src/main.py").read_text()
    err, _ = _strict_reject(source)
    assert err.reason == f"expr.lambda:lambda.{cause}"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "# tpyc: error" in text)
    assert err.loc is not None and err.loc.line == line


@pytest.mark.parametrize("statement", [
    "print(apply(\n    lambda n: 1,\n))",
    "pair = (apply(\n    lambda n: 1,\n), 2)",
    "if apply(\n    lambda n: 1,\n):\n    print('yes')",
    "while apply(\n    lambda n: 1,\n):\n    break",
    "print(f'{apply(\n    lambda n: 1,\n)}')",
])
def test_lambda_argument_cause_survives_enclosing_expression(statement: str) -> None:
    source = '''from tpy import Fn, int32
def apply(f: Fn[[int32 | None], int32]) -> int32:
    return 0
def run():
''' + indent(statement, " " * 4) + "\n"
    err, _ = _strict_reject(source)
    assert err.reason.endswith(":lambda.parameter_type")
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "lambda n" in text)
    assert err.loc is not None and err.loc.line == line


def test_nested_lambda_argument_keeps_inner_location() -> None:
    source = '''from tpy import Fn, int32
def inner(f: Fn[[int32 | None], int32]) -> int32:
    return f(None)
def outer(f: Fn[[int32], int32]) -> int32:
    return f(1)
def run():
    outer(lambda n: inner(
        lambda x: 1,
    ))
'''
    err, _ = _strict_reject(source)
    assert err.reason.endswith(":lambda.parameter_type")
    assert err.loc is not None and err.loc.line == 8


@pytest.mark.parametrize("condition", ["if", "while"])
def test_lambda_location_in_suspending_condition(condition: str) -> None:
    source = RECORD + f'''def run() -> Iterator[int32]:
    items = [Node("a")]
    {condition} len(sorted(items,
                         key=lambda n: n.name)) > 0:
        yield 1
'''
    err, _ = _strict_reject(source)
    assert err.reason == "expr.lambda:lambda.body:field.result_type"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "key=lambda" in text)
    assert err.loc is not None and err.loc.line == line


@pytest.mark.parametrize("field_type,value", [
    ("tuple[int32, int32]", "({result}, 1)"),
    ("tuple[tuple[int32], int32]", "(({result},), 1)"),
])
def test_lambda_location_in_constructor_tuple(
        field_type: str, value: str) -> None:
    # A direct field initializer takes the MIL path rather than the body path.
    result = "len(sorted(items,\n            key=lambda n: n.name))"
    source = RECORD + f'''class Test:
    pair: {field_type}
    def __init__(self, items: list[Node]):
        self.pair = {value.format(result=result)}
'''
    err, _ = _strict_reject(source)
    assert err.reason == "expr.lambda:lambda.body:field.result_type"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "key=lambda" in text)
    assert err.loc is not None and err.loc.line == line


def test_lambda_location_in_await_operand() -> None:
    source = RECORD + '''from tpy import Fn, Own
from tpy.coro import Awaitable, Poll, Waker, poll_ready
class Ready(Awaitable[int32]):
    def __poll__(self, waker: Waker) -> Own[Poll[int32]]:
        return poll_ready(1)
def ready(f: Fn[[Node], str]) -> Own[Ready]:
    return Ready()
async def run() -> int32:
    return await ready(
        lambda n: n.name,
    )
'''
    err, _ = _strict_reject(source)
    assert err.reason == "expr.lambda:lambda.body:field.result_type"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "lambda n" in text)
    assert err.loc is not None and err.loc.line == line


@pytest.mark.parametrize("suffix", ["", " for i in range(1)"])
def test_lambda_location_in_container_call_element(suffix: str) -> None:
    source = RECORD + '''from tpy import Fn, Own
def make(f: Fn[[Node], str]) -> Own[list[int32]]:
    return [1]
def run():
    values = [make(
        lambda n: n.name,
    )''' + suffix + "]\n"
    err, _ = _strict_reject(source)
    assert err.reason == "expr.lambda:lambda.body:field.result_type"
    line = next(i for i, text in enumerate(source.splitlines(), 1)
                if "lambda n" in text)
    assert err.loc is not None and err.loc.line == line

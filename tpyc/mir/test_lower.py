"""MIR semantics and topology over emitted THIR from unit-owned source."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRBodyId, MIRBodyKind, MIRBranch, MIRFunction, MIRNotCovered, MIRPlace,
)
from .testutil import execute

SOURCE = """\
from tpy import int32

def choose(flag: bool, x: int32) -> int32:
    result = x
    while flag:
        if x > 0:
            result = 1
            break
        flag = False
    else:
        result = 2
    return result

def lazy_and(flag: bool) -> int32:
    n = 0
    selected = flag and ((n := 1) > 0)
    return n if selected else 2

def lazy_or(flag: bool) -> int32:
    n = 0
    selected = flag or ((n := 1) > 0)
    return n if selected else 2

def conditional(flag: bool) -> int32:
    n = 0
    result = (n := 3) if flag else (n := 4)
    return n if result == n else 0

def loop_condition() -> int32:
    flag = False
    result = 0
    while (flag := not flag):
        result = 5
        continue
    return 0 if flag else result

def nested(flag: bool) -> int32:
    outer = True
    inner = True
    result = 0
    while outer:
        outer = False
        while inner:
            inner = False
            if flag:
                result = 6
                break
            continue
        else:
            result = 7
        continue
    else:
        result = 8 if result == 6 else 9
    return result

def early(flag: bool) -> int32:
    if flag:
        return 10
    else:
        return 11

def void_body() -> None:
    pass
"""


@pytest.fixture(scope="module")
def functions() -> dict[str, th.THIRFunction]:
    compiler, modules = _compile(SOURCE)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    return {node.name: fn for node, fn in ctx.thir_functions.items()}


def lower(fn: th.THIRFunction, name: str = "fixture") -> MIRFunction:
    result = lower_function(fn, MIRBodyId("test", name), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("name,args,expected", [
    ("choose", (True, 3), 1), ("choose", (True, -2), 2), ("choose", (False, 7), 2),
    ("lazy_and", (True,), 1), ("lazy_and", (False,), 2),
    ("lazy_or", (True,), 0), ("lazy_or", (False,), 1),
    ("conditional", (True,), 3), ("conditional", (False,), 4),
    ("loop_condition", (), 5), ("nested", (True,), 8), ("nested", (False,), 9),
    ("early", (True,), 10), ("early", (False,), 11), ("void_body", (), None),
])
def test_actual_thir_paths(functions: dict[str, th.THIRFunction], name: str,
                          args: tuple[int | bool, ...], expected: int | bool | None) -> None:
    assert execute(lower(functions[name], name), *args) == expected


def test_deterministic_body_scoped_ids(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["choose"]
    a = lower_function(fn, MIRBodyId("one", "choose@1"), kind=MIRBodyKind.FREE_FUNCTION)
    b = lower_function(fn, MIRBodyId("two", "choose@1"), kind=MIRBodyKind.FREE_FUNCTION)
    c = lower_function(fn, MIRBodyId("one", "choose@2"), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(a, MIRFunction) and isinstance(b, MIRFunction) and isinstance(c, MIRFunction)
    assert {s.id for s in a.slots}.isdisjoint(s.id for s in b.slots)
    assert {s.id for s in a.slots}.isdisjoint(s.id for s in c.slots)
    assert dump_function(a) == dump_function(lower_function(
        fn, a.id, kind=MIRBodyKind.FREE_FUNCTION))
    assert any(s.loc is not None for block in a.blocks for s in block.statements)


def test_lazy_write_is_behind_branch(functions: dict[str, th.THIRFunction]) -> None:
    fn = lower(functions["lazy_and"])
    n = next(s.id for s in fn.slots if s.name == "n")
    entry = next(b for b in fn.blocks if b.id == fn.entry)
    assert isinstance(entry.terminator, MIRBranch)
    assert len([s for s in entry.statements if s.target == MIRPlace(n)]) == 1
    right = next(b for b in fn.blocks if b.id == entry.terminator.then)
    bypass = next(b for b in fn.blocks if b.id == entry.terminator.otherwise)
    assert any(s.target == MIRPlace(n) for s in right.statements)
    assert not any(s.target == MIRPlace(n) for s in bypass.statements)


@pytest.mark.parametrize("op,flag,expected", [("&&", False, 0), ("&&", True, 1),
                                            ("||", False, 1), ("||", True, 0)])
def test_boolean_value_select_sibling(functions: dict[str, th.THIRFunction],
                                     op: str, flag: bool, expected: int) -> None:
    fn = functions["lazy_and"]
    decl = fn.body[1]
    assert isinstance(decl, th.THIRVarDecl) and isinstance(decl.init, th.THIRBinOp)
    binary = decl.init
    select = th.THIRValueSelect(BOOL, binary.left, binary.right, op)
    # Return the mutated slot even on the bypass path: eager evaluation must fail.
    sibling = replace(fn, body=(fn.body[0], replace(decl, init=select),
                                th.THIRReturn(th.THIRName(INT32, "n"))))
    assert execute(lower(sibling), flag) == expected


def test_dump_small_function() -> None:
    fn = th.THIRFunction("f", (th.THIRParam("x", INT32),), INT32,
                         (th.THIRReturn(th.THIRName(INT32, "x")),), th.THIRFunctionLayout())
    assert dump_function(lower(fn)) == (
        "fn test::fixture -> int32\nentry bb0\n"
        "  %0: int32 parameter x\n  %1: int32 temporary\n"
        "bb0:\n  %1 = read %0\n  return %1\n")


def test_unreachable_supported_tail_is_not_executed(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["early"]
    fn = replace(fn, body=(*fn.body, th.THIRReturn(th.THIRLiteral(INT32, 99))))
    assert execute(lower(fn), True) == 10


@pytest.mark.parametrize("kind", [k for k in MIRBodyKind if k is not MIRBodyKind.FREE_FUNCTION])
def test_body_kinds_explicitly_excluded(functions: dict[str, th.THIRFunction], kind: MIRBodyKind) -> None:
    result = lower_function(functions["choose"], MIRBodyId("test", "choose"), kind=kind)
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported body kind"

"""The per-node facts THIR publishes for the loan analysis's leaf vocabulary:
`certified_op` on operator nodes and `passing` on parameters."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from . import nodes as th
from .testutil import _compile, _entry, _lower_fn

_OPS = """\
from tpy import int32, int64, float32

def compare(a: int32, b: int32) -> bool:
    # Only `==` and `<` resolve to a dunder; the other four are derived.
    return (a == b) and (a != b) and (a < b) and (a <= b) and (a > b) and (a >= b)

def arith(a: int32, b: int32, x: float, y: float32, c: int64) -> float:
    n = a + b
    m = -a
    return x * y + float(n + m) + float(c // c)

def promoted(a: int32, big: int) -> bool:
    # `int32.__int__` promotes the left operand into BigInt's operator.
    return a + big == big

def reverse_promoted(big: int, a: int32) -> bool:
    # `int32.__int__` promotes the right operand into BigInt's operator.
    return big + a == big

def mixed(big: int, x: float) -> bool:
    # A BigInt operand of a float compare is cast to the float's type.
    return big < x

def negated(big: int) -> int:
    return -big

def literals(a: int32, b: int64, x: float) -> bool:
    # A literal operand converts to the typed operand's primitive.
    return a == 1 and 2 <= a and b < 5 and x * 2.0 > 1.5

def big_literal(big: int) -> bool:
    return big < 5

def params(a: int32, s: str, xs: list[int32], t: tuple[int32, int32], o: int32 | None) -> None:
    xs.append(a)
"""


def _nodes(fn, kind):
    out = []

    def walk(value):
        if isinstance(value, kind):
            out.append(value)
        if isinstance(value, (tuple, list)):
            for item in value:
                walk(item)
        elif isinstance(value, th.THIRNode) or type(value).__module__ == th.__name__:
            for name in getattr(value, "__dataclass_fields__", {}):
                walk(getattr(value, name))

    walk(fn.body)
    return out


def _lower(name):
    fn = _lower_fn(_OPS, name)
    assert fn is not None, name
    return fn


def test_all_six_int32_comparisons_are_certified():
    ops = [b for b in _nodes(_lower("compare"), th.THIRBinOp) if b.op not in ("&&", "||")]
    assert sorted(b.op for b in ops) == sorted(["==", "!=", "<", "<=", ">", ">="])
    # Resolved and derived comparisons alike, without a method-name list.
    assert {b.op for b in ops if b.resolved is None} == {"!=", "<=", ">", ">="}
    assert all(b.certified_op for b in ops)


def test_primitive_arithmetic_is_certified():
    fn = _lower("arith")
    binops = _nodes(fn, th.THIRBinOp)
    assert binops and all(b.certified_op for b in binops)
    unary = _nodes(fn, th.THIRUnaryArith)
    assert len(unary) == 1 and unary[0].resolved is not None and unary[0].certified_op


def test_conversion_around_the_operator_refuses_certification():
    promoted = [b for b in _nodes(_lower("promoted"), th.THIRBinOp) if b.op == "+"]
    assert promoted and promoted[0].resolved.promotion is not None
    assert not promoted[0].certified_op
    # An untemplated `__int__` renders no wrapper; the promotion fact still refuses it.
    bare = replace(promoted[0], resolved=replace(promoted[0].resolved, left_wrapper="{expr}"))
    assert not bare.certified_op
    reverse = [b for b in _nodes(_lower("reverse_promoted"), th.THIRBinOp) if b.op == "+"]
    assert reverse and reverse[0].resolved.promotion is not None
    assert reverse[0].resolved.right_wrapper != "{expr}" and reverse[0].resolved.left_wrapper == "{expr}"
    assert not reverse[0].certified_op
    mixed = _nodes(_lower("mixed"), th.THIRBinOp)
    assert mixed and (mixed[0].left_cast or mixed[0].right_cast)
    assert not mixed[0].certified_op


def test_literal_operands_of_primitive_operations_are_certified():
    ops = [b for b in _nodes(_lower("literals"), th.THIRBinOp) if b.op not in ("&&", "||")]
    assert len(ops) == 5 and all(b.certified_op for b in ops)
    assert any(b.resolved is None for b in ops) and any(b.resolved is not None for b in ops)


def test_literal_against_bigint_is_not_certified():
    ops = _nodes(_lower("big_literal"), th.THIRBinOp)
    assert ops and not any(b.certified_op for b in ops)


def test_bigint_operations_are_not_certified():
    unary = _nodes(_lower("negated"), th.THIRUnaryArith)
    assert unary and not unary[0].certified_op


def test_params_publish_their_passing():
    passing = {p.name: p.passing for p in _lower("params").params}
    assert passing == {
        "a": ParamPassing.VALUE,
        "s": ParamPassing.VIEW,
        "xs": ParamPassing.MUT_REF,
        "t": ParamPassing.CONST_REF,
        "o": ParamPassing.VALUE,
    }


@pytest.mark.parametrize("name", ["compare", "arith", "params"])
def test_every_lowered_param_carries_passing(name):
    assert all(p.passing is not None for p in _lower(name).params)


_CONST = """\
from tpy import int32

class Box:
    v: int32
    items: list[int32]
    def __init__(self, v: int32, items: list[int32]) -> None:
        self.v = v
        self.items = items
    def put(self, other: Box, seen: list[int32]) -> int32:
        other.v = 1
        return len(seen)

def reads(b: Box, xs: list[int32]) -> int32:
    return b.v + len(xs)

def writes(b: Box, xs: list[int32]) -> None:
    b.v = 2
    xs.append(1)

def main() -> None:
    b = Box(1, [1])
    print(reads(b, [1]), b.put(b, [2]))
    writes(b, [3])

main()
"""


def test_passing_carries_the_signature_const_verdict():
    # A reference param the body only reads passes `const T&`, a mutated one `T&`,
    # for free functions, methods and constructors alike.
    compiler, modules = _compile(_CONST)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    passing = {fn.name: {p.name: p.passing for p in fn.params} for fn in ctx.thir_functions.values()}
    assert passing["reads"] == {"b": ParamPassing.CONST_REF, "xs": ParamPassing.CONST_REF}
    assert passing["writes"] == {"b": ParamPassing.MUT_REF, "xs": ParamPassing.MUT_REF}
    assert passing["put"] == {"other": ParamPassing.MUT_REF, "seen": ParamPassing.CONST_REF}
    ctor, = ctx.thir_constructors.values()
    assert {p.name: p.passing for p in ctor.params} == {"v": ParamPassing.VALUE, "items": ParamPassing.CONST_REF}

"""The per-node facts THIR publishes for the loan analysis's leaf vocabulary:
`certified_op` on operator nodes and `passing` on parameters."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import (
    BOOL, BYTES, CHAR, FLOAT, FLOAT32, INT32, STR, STRVIEW, UINT8, FloatLiteralType, IntLiteralType, NominalType,
    certified_primitive_op, certified_primitive_subscript,
)
from .scalar_leaves import converted_literal, leaf_constant
from . import nodes as th
from .testutil import _compile, _entry, _lower_fn

_OPS = """\
from tpy import int32, int64, float32, char, Own, String

class Meters:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __int__(self) -> int:
        return 5

def user_promoted(m: Meters, big: int) -> int:
    # A user record's `__int__` is user code, not a runtime conversion.
    return m + big

def promoted_compare(a: int32, big: int) -> bool:
    return a == big

class Vec:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __add__(self, other: Vec) -> Own[Vec]:
        return Vec(self.x + other.x)

def vecs(a: Vec, b: Vec) -> int32:
    # A user record's dunder runs user code.
    return (a + b).x

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

def mixed_width(a: int32, b: int64) -> int64:
    # `int32 + int64` widens the int32 operand into int64's operator.
    return a + b

def same_width(b: int64, c: int64) -> int64:
    return b + c

def literals(a: int32, b: int64, x: float) -> bool:
    # A literal operand converts to the typed operand's primitive.
    return a == 1 and 2 <= a and b < 5 and x * 2.0 > 1.5

def literal_receiver(i: int32) -> float:
    # A float literal is the receiver of `float.__add__(other: AnyFixedInt)`, then of `__radd__`.
    return (1.0 + i) + (i + 1.0)

def big_literal(big: int) -> bool:
    return big < 5

def big_ops(a: int, b: int) -> bool:
    # BigInt arithmetic over names and a BigInt literal, and a derived comparison.
    return a + b * 2 > a

def big_fixed(big: int, i: int32) -> bool:
    # Derived comparisons between BigInt and a fixed-width int, both orders.
    return i > big and big != i

def float_big(x: float, big: int) -> float:
    # The stub's `float.__add__(int)` template converts the BigInt itself.
    return x + big

def strs(s: str, t: str) -> bool:
    # A String result compared with a str literal is a mixed pair sema leaves to C++.
    return s + t == "x" and s < t and s != t

def widen(i: int32) -> int:
    x: int = i
    return x

def narrow(xs: list[int32], big: int) -> int32:
    return xs[big]

def char_str(c: char) -> str:
    s: str = c
    return s

def to_float(i: int32) -> float:
    x: float = i
    return x

def takes_str(s: str) -> int32:
    return len(s)

def string_str(s: String) -> int32:
    # `string_to_str` passes the String through at the `str` view parameter.
    return takes_str(s)

def slices(s: str) -> str:
    return s[1:]

def element(s: str, b: bytes, i: int32) -> bool:
    return s[i] == 'a' and b[0] == 1

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


def test_a_primitive_promotion_is_a_certified_conversion():
    # `int32.__int__` converts one operand into BigInt's operator: runtime code between leaves.
    promoted = [b for b in _nodes(_lower("promoted"), th.THIRBinOp) if b.op == "+"]
    assert promoted and promoted[0].resolved.promotion is not None
    assert promoted[0].promoted_operand == 0 and promoted[0].certified_op
    # The certificate is the promotion method's, whatever wrapper renders it.
    bare = replace(promoted[0], resolved=replace(promoted[0].resolved, left_wrapper="{expr}"))
    assert bare.certified_op
    reverse = [b for b in _nodes(_lower("reverse_promoted"), th.THIRBinOp) if b.op == "+"]
    assert reverse and reverse[0].resolved.promotion is not None
    assert reverse[0].resolved.right_wrapper != "{expr}" and reverse[0].resolved.left_wrapper == "{expr}"
    assert reverse[0].promoted_operand == 1 and reverse[0].certified_op
    compare, = _nodes(_lower("promoted_compare"), th.THIRBinOp)
    assert compare.resolved.promotion is not None and compare.promoted_operand == 0 and compare.certified_op


def test_a_mixed_width_operand_cast_refuses_certification():
    mixed, = _nodes(_lower("mixed_width"), th.THIRBinOp)
    assert mixed.resolved.widens_operand and mixed.resolved.promotion is None
    assert not mixed.certified_op
    same, = _nodes(_lower("same_width"), th.THIRBinOp)
    assert not same.resolved.widens_operand and same.certified_op


def test_other_promotions_refuse_certification():
    user, = _nodes(_lower("user_promoted"), th.THIRBinOp)
    assert user.resolved.promotion is not None and user.promoted_operand is None and not user.certified_op
    op = [b for b in _nodes(_lower("promoted"), th.THIRBinOp) if b.op == "+"][0]
    promotion = op.resolved.promotion
    # A conversion with an argument, or one that yields something else, is no promotion certificate.
    for damaged in (replace(promotion, params=list(op.resolved.method.params)),
                    replace(promotion, return_type=INT32), replace(promotion, owning_type_qname=None),
                    op.resolved.method):
        wrapped = replace(op, resolved=replace(op.resolved, promotion=damaged))
        assert wrapped.promoted_operand is None and not wrapped.certified_op, damaged


def test_conversion_around_the_operator_refuses_certification():
    mixed = _nodes(_lower("mixed"), th.THIRBinOp)
    assert mixed and (mixed[0].left_cast or mixed[0].right_cast)
    assert not mixed[0].certified_op


def test_literal_operands_of_primitive_operations_are_certified():
    ops = [b for b in _nodes(_lower("literals"), th.THIRBinOp) if b.op not in ("&&", "||")]
    assert len(ops) == 5 and all(b.certified_op for b in ops)
    assert any(b.resolved is None for b in ops) and any(b.resolved is not None for b in ops)


def test_a_literal_is_checked_at_its_own_operand_position():
    ops = [b for b in _nodes(_lower("literal_receiver"), th.THIRBinOp)
           if isinstance(b.left, th.THIRLiteral) or isinstance(b.right, th.THIRLiteral)]
    forward, reverse = (next(b for b in ops if b.resolved.is_reverse is rev) for rev in (False, True))
    method = forward.resolved.method
    assert len(method.params) == 1 and method.params[0].type.is_protocol
    # The literal converts into the receiver's owner, a leaf, whichever side it is on.
    assert forward.certified_op and reverse.certified_op
    assert forward.operand_position_type(0) == FLOAT and reverse.operand_position_type(1) == FLOAT
    assert forward.operand_position_type(1) == method.params[0].type
    # At the protocol-typed parameter a literal has no leaf to convert into.
    literal = IntLiteralType(3)
    assert certified_primitive_op(method, (FloatLiteralType(1.0), INT32), FLOAT)
    assert not certified_primitive_op(method, (FLOAT, literal), FLOAT)
    assert not certified_primitive_op(method, (literal, FLOAT), FLOAT, receiver=1)


def test_an_int_literal_converts_into_a_float_leaf_only_exactly():
    assert converted_literal(FLOAT, 1000) == 1000.0 and type(converted_literal(FLOAT, 1000)) is float
    assert converted_literal(FLOAT, 2 ** 53) == float(2 ** 53)
    assert converted_literal(FLOAT, 2 ** 53 + 1) is None
    assert converted_literal(FLOAT32, 2 ** 24) == float(2 ** 24)
    assert converted_literal(FLOAT32, 2 ** 24 + 1) is None
    # A leaf's own constants pass unchanged; a bool is no int literal of a float.
    assert converted_literal(INT32, 7) == 7 and converted_literal(FLOAT, 1.5) == 1.5
    assert converted_literal(FLOAT, True) is None and converted_literal(INT32, 1.5) is None
    # Which union alternative a literal names stays its own type's question.
    assert not leaf_constant(FLOAT, 3)


def test_literal_against_bigint_is_certified():
    ops = _nodes(_lower("big_literal"), th.THIRBinOp)
    assert ops and all(b.certified_op for b in ops)


def test_bigint_operations_are_certified():
    unary = _nodes(_lower("negated"), th.THIRUnaryArith)
    assert unary and unary[0].certified_op
    ops = _nodes(_lower("big_ops"), th.THIRBinOp)
    assert sorted(b.op for b in ops) == ["*", "+", ">"] and all(b.certified_op for b in ops)
    # The BigInt literal is typed int, not a number literal: an owned constant.
    literal, = (b.right for b in ops if b.op == "*")
    assert isinstance(literal, th.THIRLiteral) and str(literal.result_type) == "int"


def test_bigint_against_a_fixed_int_is_certified_by_the_runtime_fact():
    ops = [b for b in _nodes(_lower("big_fixed"), th.THIRBinOp) if b.op not in ("&&", "||")]
    assert len(ops) == 2 and all(b.resolved is None and b.certified_op for b in ops)


def test_float_plus_bigint_is_certified():
    op, = _nodes(_lower("float_big"), th.THIRBinOp)
    assert op.resolved.promotion is None and op.left_cast is None and op.right_cast is None
    assert op.certified_op


def test_str_operations_are_certified_but_not_a_mixed_comparison():
    ops = {b.op: b for b in _nodes(_lower("strs"), th.THIRBinOp) if b.op not in ("&&", "||")}
    assert ops["+"].certified_op and ops["<"].certified_op and ops["!="].certified_op
    # `String == str` is unresolved and no fact admits the pair.
    assert ops["=="].resolved is None and not ops["=="].certified_op


def test_user_record_operand_is_not_certified():
    op, = _nodes(_lower("vecs"), th.THIRBinOp)
    assert op.resolved is not None and not op.certified_op


@pytest.mark.parametrize("name,coercion", [
    ("widen", "fixed_int_to_bigint"), ("narrow", "bigint_narrow"), ("char_str", "char_to_str"),
    ("to_float", "fixed_int_to_float"),
])
def test_value_conversions_are_certified(name, coercion):
    coerce, = (c for c in _nodes(_lower(name), th.THIRCoerce) if c.coercion_name == coercion)
    assert coerce.certified_conversion and coerce.conversion_refusal is None


def test_a_passthrough_into_an_owned_leaf_aliases_its_source():
    # The `String` renders in place at the view parameter: no new value.
    coerce, = (c for c in _nodes(_lower("string_str"), th.THIRCoerce) if c.coercion_name == "string_to_str")
    assert coerce.wrap is None and not coerce.certified_conversion
    assert coerce.conversion_refusal == "conversion aliases its source"


def test_literal_and_view_coercions_are_not_certified_conversions():
    literal = th.THIRCoerce(INT32, th.THIRLiteral(IntLiteralType(1), 1), "int_literal_to_fixed_int")
    assert not literal.certified_conversion
    assert literal.conversion_refusal == "unsupported coercion"
    name = th.THIRName(STR, "s")
    assert not th.THIRCoerce(STRVIEW, name, "str_to_strview").certified_conversion
    # A borrow-only rule renders a view although its target type is str.
    assert not th.THIRCoerce(STR, th.THIRName(CHAR, "c"), "char_to_borrowed_str").certified_conversion
    assert th.THIRCoerce(STR, th.THIRName(CHAR, "c"), "char_to_str", wrap="f({0})").certified_conversion
    # A tag no rule declares is refused.
    assert not th.THIRCoerce(STR, name, "strlit_overload_pin").certified_conversion


def test_element_reads_of_owned_leaves_are_certified():
    reads = _nodes(_lower("element"), th.THIRSubscript)
    assert len(reads) == 2 and all(r.certified_op for r in reads)


def test_a_slice_is_a_view_not_a_certified_read():
    assert not _nodes(_lower("slices"), th.THIRSubscript)
    # The stub's slice overload yields a view: no certificate for any index.
    basic_slice = NominalType("basic_slice", (), _module_qname="tpy.basic_slice")
    assert not certified_primitive_subscript(STR, basic_slice, STRVIEW)
    assert not certified_primitive_subscript(STR, INT32, STRVIEW)
    # Only a fixed-width int indexes an element; an inert non-int does not.
    assert not certified_primitive_subscript(STR, FLOAT, CHAR)
    assert not certified_primitive_subscript(STR, BOOL, CHAR)
    assert certified_primitive_subscript(STR, INT32, CHAR)
    assert certified_primitive_subscript(BYTES, IntLiteralType(0), UINT8)


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

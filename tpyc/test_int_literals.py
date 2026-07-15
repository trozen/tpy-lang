"""Unit tests for the shared integer-literal rendering policy helpers."""

from tpyc.codegen_cpp.int_literals import bare_over_int32_int_literal
from tpyc.parse import TpyIntLiteral, TpyName, TpyUnaryOp
from tpyc.typesys import IntLiteralType


def test_over_int32_literal_token_is_flagged():
    # A bare >int32 literal token: renders as a platform-dependent `long`, the
    # macOS BigInt-overload-ambiguity case -> must be retargeted.
    node = TpyIntLiteral(value=86400000000)
    assert bare_over_int32_int_literal(node, IntLiteralType(value=86400000000))


def test_unary_minus_literal_token_is_flagged():
    node = TpyUnaryOp(op="-", operand=TpyIntLiteral(value=86400000000))
    assert bare_over_int32_int_literal(node, IntLiteralType(value=-86400000000))


def test_small_literal_token_is_not_flagged():
    # <= int32: `int` -> BigInt(int32_t) exact everywhere; stays bare.
    node = TpyIntLiteral(value=5)
    assert not bare_over_int32_int_literal(node, IntLiteralType(value=5))


def test_folded_binop_is_not_flagged():
    # A constant-folded BinOp (e.g. 2**63-1) is typed IntLiteralType with a
    # value, but its NODE is not a literal token. It must NOT be flagged --
    # retargeting it disabled the fold (the sys_maxsize regression). A
    # non-literal node stands in for the folded-BinOp operand here.
    non_literal = TpyName(name="x")
    assert not bare_over_int32_int_literal(
        non_literal, IntLiteralType(value=9223372036854775807))

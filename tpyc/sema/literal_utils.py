"""Shared helpers for extracting literal values from AST expression nodes.

Handles bare `str` / `int` / `bool` literals plus the unary-minus form
`-<int_literal>` (CPython encodes `-1` as `TpyUnaryOp("-", TpyIntLiteral(1))`),
and peels a single `TpyCoerce` wrapper so callers don't have to.
"""
from ..parse import (
    TpyCall, TpyExpr, TpyName, TpyStrLiteral, TpyIntLiteral, TpyBoolLiteral,
    TpyUnaryOp, TpyCoerce,
)
from ..typesys import ALL_FIXED_INTS, LiteralValue, LiteralTag


_FIXED_INT_NAMES = frozenset(str(t) for t in ALL_FIXED_INTS)


def literal_value_from_expr(expr: TpyExpr | None) -> LiteralValue | None:
    """Return a tagged `LiteralValue` for a literal AST node, else None."""
    if expr is None:
        return None
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyStrLiteral):
        return LiteralValue(LiteralTag.STR, inner.value)
    if isinstance(inner, TpyBoolLiteral):
        return LiteralValue(LiteralTag.BOOL, inner.value)
    if isinstance(inner, TpyIntLiteral):
        return LiteralValue(LiteralTag.INT, inner.value)
    if (isinstance(inner, TpyUnaryOp) and inner.op == "-"
            and isinstance(inner.operand, TpyIntLiteral)):
        return LiteralValue(LiteralTag.INT, -inner.operand.value)
    return None


def fixed_int_literal_value_from_expr(expr: TpyExpr) -> int | None:
    """Extract a bare integer literal or fixed-int ctor of one."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    value = literal_value_from_expr(expr)
    if value is not None and value.tag is LiteralTag.INT:
        assert isinstance(value.value, int) and not isinstance(value.value, bool)
        return value.value
    if (isinstance(expr, TpyCall) and len(expr.args) == 1
            and isinstance(expr.func, TpyName)
            and expr.func_name in _FIXED_INT_NAMES):
        inner = expr.args[0]
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        value = literal_value_from_expr(inner)
        if value is not None and value.tag is LiteralTag.INT:
            assert isinstance(value.value, int) and not isinstance(value.value, bool)
            return value.value
    return None

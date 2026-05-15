"""Shared helpers for extracting literal values from AST expression nodes.

Handles bare `str` / `int` / `bool` literals plus the unary-minus form
`-<int_literal>` (CPython encodes `-1` as `TpyUnaryOp("-", TpyIntLiteral(1))`),
and peels a single `TpyCoerce` wrapper so callers don't have to.
"""
from ..parse import (
    TpyExpr, TpyStrLiteral, TpyIntLiteral, TpyBoolLiteral, TpyUnaryOp, TpyCoerce,
)
from ..typesys import LiteralValue, LiteralTag


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

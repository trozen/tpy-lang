"""Shared helpers for extracting literal values from AST expression nodes.

Handles bare `str` / `int` / `bool` literals plus the unary-minus form
`-<int_literal>` (CPython encodes `-1` as `TpyUnaryOp("-", TpyIntLiteral(1))`),
and peels a single `TpyCoerce` wrapper so callers don't have to.
"""
from ..parse import (
    TpyBytesLiteral, TpyCall, TpyExpr, TpyFloatLiteral, TpyName,
    TpyNoneLiteral, TpyStrLiteral, TpyIntLiteral, TpyBoolLiteral,
    TpyUnaryOp, TpyCoerce, const_tuple_index,
)
from ..type_def_registry import is_char_type
from ..prescan import literal_constant
from ..typesys import (
    ALL_FIXED_INTS, BOOL, BYTES, FloatLiteralType, IntLiteralType, LiteralValue,
    LiteralTag, NONE, STR, TpyType, is_any_str_type,
)


_FIXED_INT_NAMES = frozenset(str(t) for t in ALL_FIXED_INTS)
_FIXED_INT_BY_NAME = {str(t): t for t in ALL_FIXED_INTS}


def is_char_literal_init(target: TpyType, actual: TpyType, expr: TpyExpr) -> bool:
    """A single-char `str` literal initializing a `char` slot.

    `char` has no coercion from `str` (a runtime string cannot narrow to one
    character), but the one-character literal is unambiguous and codegen
    renders it as a C++ char literal. Shared by the assignment, argument and
    default-value checks so the one spelling they all exempt stays one
    predicate rather than a copy per call site.
    """
    return (is_char_type(target) and is_any_str_type(actual)
            and isinstance(expr, TpyStrLiteral) and len(expr.value) == 1)


def const_expr_type(expr: TpyExpr) -> 'TpyType | None':
    """Type of a constant default expression, or None when unjudgeable.

    Covers the grammar the parser admits in default position (see
    `_validate_const_default`) minus the two shapes resolved elsewhere: an
    enum member (`check_enum_member_default`) and a `Final[T]` name (bound
    too late to see here). Literal types mirror `analyze_expr` exactly so a
    default is judged by the same rules as the equivalent assignment.
    """
    if isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyNoneLiteral):
        return NONE
    if isinstance(expr, TpyBoolLiteral):  # before int: bool is an int subclass
        return BOOL
    if isinstance(expr, TpyIntLiteral):
        return IntLiteralType(expr.value)
    if isinstance(expr, TpyFloatLiteral):
        return FloatLiteralType(expr.value)
    if isinstance(expr, TpyStrLiteral):
        return STR
    if isinstance(expr, TpyBytesLiteral):
        return BYTES
    # An integer constant over literals folds as analyze_expr folds it.
    const = literal_constant(expr)
    if const is not None and not const.is_float and const.value is not None:
        return IntLiteralType(const.value)
    if isinstance(expr, TpyUnaryOp) and expr.op == "-":
        inner = const_expr_type(expr.operand)
        if isinstance(inner, FloatLiteralType) and inner.value is not None:
            return FloatLiteralType(-inner.value)
        return None
    if (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
            and expr.func_name in _FIXED_INT_BY_NAME):
        return _FIXED_INT_BY_NAME[expr.func_name]
    return None


def literal_value_from_expr(expr: TpyExpr | None) -> LiteralValue | None:
    """Return a tagged `LiteralValue` for a literal AST node, else None."""
    if expr is None:
        return None
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyStrLiteral):
        return LiteralValue(LiteralTag.STR, inner.value)
    if isinstance(inner, TpyBoolLiteral):
        return LiteralValue(LiteralTag.BOOL, inner.value)
    n = const_tuple_index(inner)
    return LiteralValue(LiteralTag.INT, n) if n is not None else None


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


def numeric_literal_truth(expr: TpyExpr) -> bool | None:
    """The compile-time truth of a numeric literal (`2.5`, `-0.0`, `+1`,
    `int32(2)`), else None. A fixed-int ctor's literal is range-checked by
    sema, so its value is the value C++ holds."""
    if isinstance(expr, TpyUnaryOp) and expr.op in ("+", "-"):
        return numeric_literal_truth(expr.operand)
    lit = const_expr_type(expr)
    if (isinstance(lit, (IntLiteralType, FloatLiteralType))
            and lit.value is not None):
        return bool(lit.value)
    v = fixed_int_literal_value_from_expr(expr)
    return None if v is None else bool(v)

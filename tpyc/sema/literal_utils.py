"""Shared helpers for extracting literal values from AST expression nodes.

Handles bare `str` / `int` / `bool` literals plus the unary-minus form
`-<int_literal>` (CPython encodes `-1` as `TpyUnaryOp("-", TpyIntLiteral(1))`),
and peels a single `TpyCoerce` wrapper so callers don't have to.
"""
from typing import TYPE_CHECKING

from ..parse import (
    TpyBytesLiteral, TpyCall, TpyExpr, TpyFloatLiteral, TpyName,
    TpyNoneLiteral, TpyStrLiteral, TpyIntLiteral, TpyBoolLiteral,
    TpyUnaryOp, TpyCoerce, const_tuple_index,
)
from ..type_def_registry import is_bool_type, is_char_type, is_str_type
from ..prescan import literal_constant
from ..typesys import (
    ALL_FIXED_INTS, BOOL, BYTES, INT32, FunctionInfo, holds_literal, FloatLiteralType, IntLiteralType,
    LiteralType,
    LiteralValue, LiteralTag, NONE, NominalType, OwnType, STR, TpyType,
    is_any_str_type, is_integer_type, unwrap_readonly, unwrap_ref_type,
)
from ..value_category import peel_coerce
from .read_binding import ReadKind, resolve_read

if TYPE_CHECKING:
    from collections.abc import Iterable
    from .context import SemanticContext


_FIXED_INT_NAMES = frozenset(str(t) for t in ALL_FIXED_INTS)
_FIXED_INT_BY_NAME = {str(t): t for t in ALL_FIXED_INTS}


def numeric_literal_type(t: TpyType | None) -> IntLiteralType | FloatLiteralType | None:
    """`t` as a numeric literal's type -- what sema gives a literal, a
    constant folded over literals, or an expression that keeps a literal's
    type (a walrus, a conditional with equal branches) -- else None. The
    one test of a literal operand, so the ranking and the literal pin agree
    on which operands are literals."""
    if t is None:
        return None
    t = unwrap_ref_type(t)
    if isinstance(t, OwnType):
        t = t.wrapped
    return t if isinstance(t, (IntLiteralType, FloatLiteralType)) else None


def literal_member(lit: LiteralType,
                   members: 'tuple[TpyType, ...]') -> TpyType | None:
    """The one of `members` (an `@overload` implementation parameter's
    types) a `Literal` stub at that parameter narrows: its base, or for an
    integer Literal any integer type, whose width the stub then takes."""
    for member in members:
        base = member.base_type if isinstance(member, LiteralType) else member
        if base == lit.base_type or (lit.is_int_base()
                                     and is_integer_type(base)):
            return base
    return None


def _value_fits_base(v: LiteralValue, lit: LiteralType) -> bool:
    """Whether a literal value is of the kind `lit`'s set holds."""
    if v.tag is LiteralTag.STR:
        return lit.is_str_base()
    if v.tag is LiteralTag.BOOL:
        return lit.is_bool_base()
    return v.tag is LiteralTag.INT and lit.is_int_base()


def source_values(ctx: 'SemanticContext', source_expr: TpyExpr | None,
                  ) -> tuple[LiteralValue, ...] | None:
    """The values a source expression is proven to hold: a literal's own,
    or every literal a local of this body is ever bound to (a local some
    binding of which stores no literal proves none)."""
    if source_expr is None:
        return None
    lv = literal_value_from_expr(source_expr)
    if lv is not None:
        return (lv,)
    name = peel_coerce(source_expr)
    if not isinstance(name, TpyName):
        return None
    values = ctx.func.literal_locals.bound_values(name.name)
    if values is None or resolve_read(ctx, name.name).kind is not ReadKind.LOCAL:
        # A comprehension's or a lambda's own binding of the name, or a
        # capture, is another variable.
        return None
    return values


def literal_admits(ctx: 'SemanticContext', expected: LiteralType,
                   actual: TpyType, source_expr: TpyExpr | None,
                   ) -> TpyType | LiteralValue | None:
    """Whether a `Literal` slot admits this value: the type to check it as
    (`actual` when the type proves it, else the `Literal` of the values
    its source is proven to hold), or the first proven value outside the
    set, or None when nothing proves the value is in it."""
    if expected.admits(actual):
        return actual
    values = source_values(ctx, source_expr)
    if values is None or not all(_value_fits_base(v, expected)
                                 for v in values):
        return None
    for v in values:
        if v not in expected.values:
            return v
    return proven_literal(values, actual, expected.base_type)


_KIND_DEFAULT = {LiteralTag.STR: STR, LiteralTag.BOOL: BOOL,
                 LiteralTag.INT: INT32}


def proven_literal(values: tuple[LiteralValue, ...], actual: TpyType,
                   fallback: TpyType | None = None) -> LiteralType | None:
    """The `Literal` of the values a value of type `actual` is proven to
    hold, all of one kind: its base is the value's own concrete type of that
    kind (an `int32` local into an `int` set converts), else `fallback`,
    else the kind's default. None for values of mixed or no kind."""
    tags = {v.tag for v in values}
    tag = tags.pop() if len(tags) == 1 else None
    if tag not in _KIND_DEFAULT:
        return None
    of_kind = {LiteralTag.STR: is_str_type, LiteralTag.BOOL: is_bool_type,
               LiteralTag.INT: is_integer_type}[tag]
    bare = unwrap_readonly(unwrap_ref_type(actual))
    base = (bare if isinstance(bare, NominalType) and of_kind(bare)
            else fallback or _KIND_DEFAULT[tag])
    return LiteralType(base, values)


def is_char_literal_init(target: TpyType, actual: TpyType, expr: TpyExpr) -> bool:
    """A single-char `str` literal initializing a `char` slot.

    `char` has no coercion from `str` (a runtime string cannot narrow to one
    character), but the one-character literal is unambiguous and codegen
    renders it as a C++ char literal -- one byte, so only an ASCII
    character, which `str(c)` reads back as the same text.
    """
    return (is_char_type(target) and is_any_str_type(actual)
            and isinstance(expr, TpyStrLiteral) and len(expr.value) == 1
            and expr.value.isascii())


def const_expr_type(expr: TpyExpr) -> 'TpyType | None':
    """Type of a constant default expression, or None when unjudgeable.

    Covers the grammar the parser admits in default position (see
    `_validate_const_default`) minus the two shapes resolved elsewhere: an
    enum member or class constant (`check_type_member_default`) and a
    `Final[T]` name (bound too late to see here). Literal types mirror `analyze_expr` exactly so a
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


def bound_value_from_expr(expr: TpyExpr | None) -> LiteralValue | None:
    """The value a local bound once to `expr` holds when `expr` is a
    literal (`literal_value_from_expr`) or `None`."""
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyNoneLiteral):
        return LiteralValue(LiteralTag.NONE, None)
    return literal_value_from_expr(expr)


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


def enriched_literal(ctx: 'SemanticContext', arg_t: TpyType,
                     arg: 'TpyExpr | None') -> TpyType:
    """Argument `arg` of type `arg_t` as overload ranking sees it where the
    set ranks literals (`ranks_literals`), for a written argument and an
    omitted one's default alike: a literal or a local proven to hold
    literals is its `Literal`."""
    if is_str_type(arg_t) and isinstance(arg, TpyStrLiteral):
        return LiteralType(STR, (LiteralValue(LiteralTag.STR, arg.value),))
    if isinstance(arg_t, IntLiteralType) and arg_t.value is not None:
        return LiteralType(INT32, (LiteralValue(LiteralTag.INT, arg_t.value),))
    if is_bool_type(arg_t) and isinstance(arg, TpyBoolLiteral):
        return LiteralType(BOOL, (LiteralValue(LiteralTag.BOOL, arg.value),))
    if isinstance(peel_coerce(arg) if arg is not None else None, TpyName):
        values = source_values(ctx, arg)
        lit = proven_literal(values, arg_t) if values else None
        if lit is not None:
            return lit
    return arg_t


def ranks_literals(candidates: 'Iterable[FunctionInfo]') -> bool:
    """Whether a call to `candidates` ranks literals as their `Literal`
    (`enriched_literal`): some candidate has a `Literal`
    parameter, so protocol-based matching (`hash(Hashable)`) keeps the
    plain types elsewhere."""
    return any(holds_literal(p.type) for c in candidates for p in c.params)

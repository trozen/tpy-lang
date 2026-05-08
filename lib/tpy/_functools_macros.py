# tpy: macro_module
"""Compile-time class macros for `functools`.

Hosts `@total_ordering`; the runtime helpers (`reduce`, etc.) live
in `lib/tpy/functools.py`. The split exists because TPy modules
cannot mix `# tpy: macro_module` with runtime functions that
compile to C++.
"""

from tpyc.macro_api import ClassInfo, MacroError, class_macro, ast, types
from _macro_helpers import ORDER_DUNDERS, ORDER_DUNDER_OPS


_ANCHOR_TO_OP = dict(zip(ORDER_DUNDERS, ORDER_DUNDER_OPS))


# (anchor, missing_op) -> builder taking `(sa, eq)` where
# `sa = self <anchor_op> other` and `eq = self == other`.
# Each pair has one canonical derivation; `__lt__` and `__gt__`
# (likewise `__le__` and `__ge__`) share formulas because each
# strict / weak inequality lets us derive its mirror identically.
_DERIVATIONS = {
    ("__lt__", "__gt__"): lambda sa, eq: ast.unary("!", ast.binop(sa, "||", eq)),
    ("__lt__", "__le__"): lambda sa, eq: ast.binop(sa, "||", eq),
    ("__lt__", "__ge__"): lambda sa, eq: ast.unary("!", sa),
    ("__le__", "__ge__"): lambda sa, eq: ast.binop(ast.unary("!", sa), "||", eq),
    ("__le__", "__lt__"): lambda sa, eq: ast.binop(sa, "&&", ast.unary("!", eq)),
    ("__le__", "__gt__"): lambda sa, eq: ast.unary("!", sa),
    ("__gt__", "__lt__"): lambda sa, eq: ast.unary("!", ast.binop(sa, "||", eq)),
    ("__gt__", "__ge__"): lambda sa, eq: ast.binop(sa, "||", eq),
    ("__gt__", "__le__"): lambda sa, eq: ast.unary("!", sa),
    ("__ge__", "__le__"): lambda sa, eq: ast.binop(ast.unary("!", sa), "||", eq),
    ("__ge__", "__gt__"): lambda sa, eq: ast.binop(sa, "&&", ast.unary("!", eq)),
    ("__ge__", "__lt__"): lambda sa, eq: ast.unary("!", sa),
}


def _build_body(anchor: str, op_name: str):
    s = ast.name("self")
    o = ast.name("other")
    sa = ast.binop(s, _ANCHOR_TO_OP[anchor], o)
    eq = ast.binop(s, "==", o)
    return _DERIVATIONS[(anchor, op_name)](sa, eq)


@class_macro
def total_ordering(cls: ClassInfo) -> None:
    """Synthesize the missing rich-comparison operators on `cls`.

    Requires `__eq__` plus at least one of `__lt__` / `__le__` /
    `__gt__` / `__ge__`. The first defined ordering op (in that
    order) is the "anchor"; the remaining three are derived from it
    and `__eq__`. Defers the actual synthesis so peer macros like
    `@dataclass` (which adds `__eq__`) compose regardless of
    decorator order.
    """
    cls.defer_until_macros_complete(_apply_total_ordering)


def _apply_total_ordering(cls: ClassInfo) -> None:
    if not cls.has_method_or_inherited("__eq__"):
        raise MacroError(
            f"@total_ordering requires {cls.name!r} to define __eq__"
        )
    defined = [name for name in ORDER_DUNDERS if cls.has_method_or_inherited(name)]
    if not defined:
        raise MacroError(
            f"@total_ordering requires {cls.name!r} to define one of "
            f"__lt__, __le__, __gt__, __ge__"
        )
    anchor = defined[0]
    other_type = types.named(cls.name)
    for op_name in ORDER_DUNDERS:
        if op_name == anchor or cls.has_method_or_inherited(op_name):
            continue
        cls.add_method(ast.function(
            op_name, [("other", other_type)], types.bool,
            [ast.return_(_build_body(anchor, op_name))],
            is_method=True, is_readonly=True,
        ))

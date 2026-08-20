"""THIR lowering for the two NON-BODY constant positions in the skeleton:
a class constant's default and a `Final` global's initializer.

These are not bodies -- they have no statements, no scope, and no flush
point -- but they render through the SAME expression dispatch every body
uses, and the domain includes overflow-checked arithmetic
(`::tpy::mul_check<int32_t>(BASE, 2)`). A dedicated constant renderer
would therefore be a second copy of the arithmetic renderer, so the
position routes through the ordinary `_lower_expr` arms instead, seeded
with the only scope a constant can see: the other compile-time constants
sema accepted as leaf references (`analyzed_finals`), which render bare at
namespace and class scope alike.

Sits above `lower/` and `emit.py` rather than inside either: it is the one
seam that both lowers and emits, which the `lower/` layering forbids.
"""

from __future__ import annotations

from ..namespace import BindingKind
from ..parse.nodes import (
    TpyCall, TpyFunction, TpyMethodCall, TpyTupleLiteral,
)
from ..typesys import TpyType, TupleType, VoidType, unwrap_readonly
from .emit import _EmitState, _NO_COMMENTS, _emit_expr
from .fallback import ThirUnsupported, note
from .lower.context import _LowerCtx
from .lower.expressions import (
    _lower_char_targeted,
    _lower_tuple_literal,
    _slot_literal_retype,
)
from .lower.predicates import _readonly_global_type, _value_tuple_global


def final_global_scope(analyzer, final_names) -> 'dict[str, TpyType]':
    """The module's `Final` globals as a bare-reading constant scope.

    Sema's `analyzed_finals` is the exact set a constant initializer may
    reference by name, and a `Final` lives at namespace scope as a plain
    `const T` -- so every read renders bare, like the read-only value
    globals a function body seeds."""
    scope: dict[str, TpyType] = {}
    for name in final_names:
        gt = analyzer.ctx.global_scope.lookup(name)
        if gt is None:
            nb = analyzer.global_ns.lookup_local(name)
            gt = (nb.type if nb is not None
                  and nb.kind is BindingKind.VARIABLE else None)
        add_constant_scope_entry(scope, name, gt, analyzer)
    return scope


def add_constant_scope_entry(scope: 'dict[str, TpyType]', name: str,
                             declared: 'TpyType | None', analyzer) -> None:
    """Admit one constant name into a constant scope, gated by the same
    family predicate a read-only global read is gated by -- a name outside
    it has no verified bare-read render, so leaving it out makes a
    referencing initializer fall back rather than diverge."""
    st = _readonly_global_type(declared, analyzer)
    if st is not None:
        scope[name] = st


def lower_constant(expr, target_type: 'TpyType | None', analyzer, *,
                   const_scope: 'dict[str, TpyType]',
                   render_type=None, render_type_stored=None,
                   render_resolve=None) -> 'str | None':
    """Render one compile-time-constant initializer through THIR, or None
    when it does not lower (the caller then emits it via `gen_expr`).

    `const_scope` is the bare-reading constant names visible here (sibling
    `Final` globals, and for a class constant the earlier constants of the
    same class body) mapped to their declared types -- the mirror of a
    function body's read-only global seeding, which is the only scope a
    constant initializer can reference.
    """
    carrier = TpyFunction(name="__tpy_const", params=[],
                          return_type=VoidType(), body=[])
    lc = _LowerCtx(carrier, analyzer, render_type,
                   render_type_stored=render_type_stored,
                   render_resolve=render_resolve)
    declared = dict(const_scope)
    lc.prescan.global_readonly = frozenset(const_scope)
    try:
        node = _lower_constant_expr(expr, target_type, lc, declared)
        state = _EmitState(comments=_NO_COMMENTS)
        rendered = _emit_expr(node, state)
        # A namespace-scope initializer has no statement above it to flush
        # into, so any construct that needs a `__tmp_N` decl or a hoisted
        # slot must reject rather than emit a reference to a declaration
        # that never lands. No admitted shape reaches this today -- sema's
        # constant grammar stops at literals, unary ops, `analyzed_finals`
        # reads, numeric binops, one-arg primitive ctors and tuple literals,
        # none of which allocate -- so this holds the line if that grammar
        # widens, and has no boundary pin because it has no witness.
        if (state.temps.has_pending_since((0, 0))
                or state.temps.has_named_since((0, 0))
                or state.hoist_lines):
            raise ThirUnsupported("const.needs_statement_scope")
        return rendered
    except ThirUnsupported as ex:
        note(ex.reason)
        return None


def _lower_constant_expr(expr, target_type: 'TpyType | None',
                         lc: '_LowerCtx', declared: 'dict[str, TpyType]'):
    """Lower the initializer against its DECLARED slot.

    The slot is what makes `A: Final[int] = 100` spell `::tpy::BigInt(100)`
    and `MAX: Final[Int32] = 100` spell `100`; a tuple constant needs it for
    the element forms (`std::string_view` vs the owned `std::string` the
    literal's own resolved type would give), and a `Final[Char]` needs it for
    the `'A'` character literal a bare str literal would otherwise spell."""
    slot = (unwrap_readonly(target_type)
            if isinstance(target_type, TpyType) else None)
    while (isinstance(expr, (TpyCall, TpyMethodCall))
           and expr.macro_expansion is not None):
        # gen_expr renders a `@call_macro` expansion IN PLACE and carries the
        # target into it; the general THIR macro arm lowers the expansion
        # target-LESS, which is invisible at a body position (a literal's
        # target is reapplied post-hoc) but not here -- a macro expanding to
        # a tuple literal would spell the literal's own element types.
        expr = expr.macro_expansion
    if isinstance(expr, TpyTupleLiteral) and isinstance(slot, TupleType):
        # The tuple-literal arm of `_lower_expr` self-types from the
        # expression, which is the right rule at its own positions but not
        # here -- the declared tuple is the AST's target (`gen_expr(init,
        # var_type)`), and its element types drive the spelling. The family
        # is the constant one (`_value_tuple_global`), which admits the
        # static `std::string_view` element a runtime tuple literal cannot
        # have.
        vt = _value_tuple_global(slot, lc.analyzer)
        if vt is None:
            raise ThirUnsupported("const.tuple_slot")
        return _lower_tuple_literal(expr, vt, lc, declared)
    return _slot_literal_retype(
        _lower_char_targeted(expr, target_type, lc, declared),
        target_type, lc)

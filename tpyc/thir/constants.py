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

import contextlib

from ..codegen_cpp.context import CondRegion
from ..namespace import BindingKind
from ..parse.nodes import (
    TpyCall, TpyFunction, TpyMethodCall, TpyTupleLiteral,
)
from ..typesys import TpyType, TupleType, VoidType, unwrap_readonly
from .emit import _EmitState, _emit_expr
from .reject import ThirUnsupported, note
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
    referencing initializer reject rather than diverge."""
    st = _readonly_global_type(declared, analyzer)
    if st is not None:
        scope[name] = st


class _ConstPositionSink:
    """The temp sink and one module-cumulative counter of a CONSTANT position.

    A namespace-scope initializer has no statement above it to flush into,
    so a construct needing a `__tmp_N` declaration rejects at the allocation
    rather than emitting a reference to a declaration that never lands. The
    hidden-name streams carry no such constraint, so they just count; one
    instance per stream keeps each numbering independent, as a body's do."""

    def __init__(self) -> None:
        self._n = 0

    def _no_scope(self, *args, **kwargs):
        raise ThirUnsupported("const.needs_statement_scope")

    create = _no_scope
    declare_named = _no_scope
    declare_named_auto = _no_scope

    @contextlib.contextmanager
    def conditional_region(self):
        # Entered by every short-circuit operand render, not only an
        # allocating one -- and nothing can bank here, since `create`
        # rejects, so the region always closes with an empty prefix.
        yield CondRegion()

    def checkpoint(self) -> tuple[int, int]:
        return (0, 0)

    def has_pending_since(self, checkpoint) -> bool:
        return False

    def has_named_since(self, checkpoint) -> bool:
        return False

    def flush_since(self, out, checkpoint, indent: str) -> None:
        ...

    def flush(self, out, indent: str) -> None:
        ...

    def next(self) -> int:
        self._n += 1
        return self._n

    def draw(self) -> int:
        n = self._n
        self._n += 1
        return n


def lower_constant(expr, target_type: 'TpyType | None', analyzer, *,
                   const_scope: 'dict[str, TpyType]',
                   render_type=None, render_type_stored=None,
                   render_resolve=None) -> 'str | None':
    """Render one compile-time-constant initializer through THIR, or None
    when it does not lower -- the caller turns that into a reject.

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
        state = _EmitState(
                           temps=_ConstPositionSink(),
                           with_counter=_ConstPositionSink(),
                           try_counter=_ConstPositionSink(),
                           finally_guard_counter=_ConstPositionSink())
        rendered = _emit_expr(node, state)
        if state.hoist_lines:
            raise ThirUnsupported("const.needs_statement_scope")
        return rendered
    except ThirUnsupported as ex:
        note(ex.reason, ex.loc, ex.message)
        return None


def _lower_constant_expr(expr, target_type: 'TpyType | None',
                         lc: '_LowerCtx', declared: 'dict[str, TpyType]'):
    """Lower the initializer against its DECLARED slot.

    The slot is what makes `A: Final[int] = 100` spell `::tpy::BigInt(100)`
    and `MAX: Final[int32] = 100` spell `100`; a tuple constant needs it for
    the element forms (`std::string_view` vs the owned `std::string` the
    literal's own resolved type would give), and a `Final[char]` needs it for
    the `'A'` character literal a bare str literal would otherwise spell."""
    slot = (unwrap_readonly(target_type)
            if isinstance(target_type, TpyType) else None)
    while (isinstance(expr, (TpyCall, TpyMethodCall))
           and expr.macro_expansion is not None):
        # The general macro arm lowers an expansion target-LESS, which is
        # invisible at a body position (a literal's target is reapplied
        # post-hoc) but not here -- a macro expanding to a tuple literal
        # would spell the literal's own element types. Peeling the expansion
        # up front keeps the declared target on it.
        expr = expr.macro_expansion
    if isinstance(expr, TpyTupleLiteral) and isinstance(slot, TupleType):
        # The tuple-literal arm of `_lower_expr` self-types from the
        # expression, which is the right rule at its own positions but not
        # here -- the declared tuple is the target, and its element types
        # drive the spelling. The family
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

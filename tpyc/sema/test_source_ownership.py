"""Sema's per-element facts about a tuple SOURCE at an owning slot: which
element paths it lends (`tuple_borrowed_paths`), which elements it hands
over as objects of its own (`tuple_source_elem_owned`, derived from it)
and the per-leaf record built for every owning sink (`source_records`) --
and of the slot: which element paths it owns (`owned_slot_paths`). A
scalar source gets the same record with one leaf, at path `()`; a copied
leaf is WARNED, or HEDGED for an open payload. Every program this file
compiles also checks that the records of its sources -- names, walrus
reads and scalars included -- agree with the copy warnings, per leaf path
(`_assert_records_agree_with_warnings`), and that each MOVE leaf is a
move the lowered bodies make (`_assert_move_leaves_render_moves`)."""

import dataclasses
import re
from dataclasses import replace
from pathlib import Path
import pytest

from ..diagnostics import SemanticError
from ..identity_map import IdentityMap
from ..codegen_cpp.context import ThirRejectError
from ..parse import (TpyAssign, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess,
                     TpyIfExpr, TpyMethodCall, TpyName, TpyNamedExpr,
                     TpyReturn, TpySubscript, TpyTupleLiteral,
                     TpyTupleUnpack, TpyVarDecl, TpyYield)
from ..parse.nodes import (SourceLocation, TpyNestedDef, walk_body_stmts,
                          walk_expr_tree)
from ..sema import alias_rebind
from ..sema.alias_rebind import (bare_tuple, direct_elements, owned_slot_paths,
                                 tuple_borrowed_paths,
                                 tuple_source_elem_owned)
from ..sema.binding_ownership import ReadKind, local_ownership
from ..sema.compatibility import TupleSink, TypeCompatibility
from ..sema.context import NonOwningBind, SemanticContext
from ..sema.source_records import (Disposition, Leaf, LeafPermission,
                                   SourceRecord, read_record, verdict)
from ..compilation_context import activate_compiler
from ..thir.lower import checks as _lower_checks
from ..thir.lower import expressions as _lower_expressions
from ..thir.lower import bindings as _bindings
from ..thir.lower import convert as _lower_convert
from ..thir.lower import iter_module_callables
from ..thir.validate import THIRValidationError
from ..thir.nodes import (THIRCoerce, THIRConsumingIter, THIRFieldAccess,
                          THIRFormConvert, THIRFunction, THIRMethodCall,
                          THIRMove, THIRName, THIRNode, THIRResumableLeaves,
                          THIRReturn, THIRSelf, THIRSubscript)
from ..thir.testutil import _compile as _compile_program, _entry
from ..thir.source import Held
from ..typesys import (SendType, TpyType, TupleType, TypeParamRef,
                       peel_slot, unwrap_own, unwrap_readonly)
from ..value_category import peel_value_wrappers

_LEAF_IN_WARNING = re.compile(r"(?:copies|may copy) .*\(.*tuple element "
                              r"([0-9.]+)\)")
# A name read's copy of the whole tuple: it speaks for every leaf the
# record copies.
_WHOLE_IN_WARNING = re.compile(r"^(?:copies|may copy) tuple\[")
# A scalar source's copy into an owning slot: it speaks for its one leaf.
_SCALAR_IN_WARNING = re.compile(r"^(?:copies|may copy) (?!tuple\[)"
                                r"(?!.*tuple element).* into ")
# An iterator's per-element copy (`copy_iter()` names its remedy): a copy
# no sink's record covers.
_ELEMENT_COPY_WARNING = re.compile(r" elements(?: if not a value type)?; "
                                   r"use copy_iter\(\)")
# A copy into a call's temporary argument (the deferred argument check,
# `CallAnalyzer.defer_argument_copies`): a sink that keeps no record, even
# where its operand lies inside a source's span.
_TEMP_ARG_WARNING = re.compile(r" into argument (?:'[^']*'|[0-9]+) "
                               r"of '[^']*\(\.\.\.\)'")

# Lines whose record is known to disagree with the program's final copy
# diagnostics (`_DIAGNOSTIC_DISAGREEMENTS`) or with the copy the lowering
# makes (`_RENDER_DISAGREEMENTS`), keyed by a snippet the program must
# contain (which program, and which parametrization of it) and the exact
# stripped line, with the entry that tracks each. The rows keyed by `_B`
# are the program of `test_blocker_rows_are_recorded` (`_BLOCKERS`).
_B = "def row1(x: Own[P]) -> int32:"
_DIAGNOSTIC_DISAGREEMENTS: dict[tuple[str, str], str] = {
    # A nested def's return of a captured `Own` name copies it unwarned (an
    # `Own[T | None]` one is moved out of the enclosing binding).
    (_B, "return x"): "BUGS.md#nested-def-captured-own-return-copies",
    # A walrus stored into `Any` copies the object it names, unwarned, and
    # so does a borrowing call.
    (_B, "h.x = (w := a)"): "BUGS.md#any-store-walrus-copies-silently",
    # A tuple local stored into `Any` puts its element pointers in the cell.
    (_B, "h.x = t"): "BUGS.md#any-slot-rebound-local-holds-pointer",
    ("def borrow(h: H) -> A:", "h.b = (w := a)"):
        "BUGS.md#any-store-walrus-copies-silently",
    ("def borrow(h: H) -> A:", "h.b = borrow(h)"):
        "BUGS.md#any-store-walrus-copies-silently",
    # An open `T` member of a tuple literal held by a list literal.
    ("def box[T](x: T) -> Own[list[tuple[T, int32]]]:", "return [(x, 1)]"):
        "BUGS.md#generic-tuple-member-list-literal-unhedged",
    # A walrus-bound literal returned: the walrus target has no verdict.
    ("def ret() -> tuple[Own[A], int32]:", "return (t := (A(1), 2))"):
        "BUGS.md#walrus-tuple-owning-sink-rejects",
    # A lambda's tuple parameter returned at an `Own` tuple result copies
    # its borrowed leaf unwarned (the lowering refuses the lambda).
    ("    g: Callable[[tuple[Own[A], int32]], tuple[Own[A], int32]] = "
     "lambda p: p",
     "h: Callable[[tuple[A, int32]], Own[tuple[A, int32]]] = lambda q: q"):
        "BUGS.md#lambda-own-result-borrowed-param-copies-unwarned",
    # A generic generator's yields recorded HEDGED once the frame's slot
    # settles, with no hedge diagnostic.
    ("def mix[T](items: list[T], extra: Iterator[T]) -> Iterator[T]:",
     "yield x"): "BUGS.md#generic-yield-mixed-source-demotes-frame",
    ("def mix[T](items: list[T], extra: Iterator[T]) -> Iterator[T]:",
     "yield y"): "BUGS.md#generic-yield-mixed-source-demotes-frame",
    # A generic local bound from a lending call copies, unwarned.
    ("def keep_rebound[T](xs: list[T], x: T, z: T) -> None:",
     "y = ident(x)"): "BUGS.md#generic-lending-local-copies-unwarned",
    ("def keep_rebound[T](xs: list[T], x: T, z: T) -> None:",
     "y = ident(z)"): "BUGS.md#generic-lending-local-copies-unwarned",
}
_RENDER_DISAGREEMENTS: dict[tuple[str, str], str] = {
    # A `@dynamic` local moves in the record; its move has no well-formed
    # render, so the C++ copies it (ill-formed).
    (_B, "return keep(s)"): "BUGS.md#dyn-local-own-arg-ill-formed",
    # A nested def's return of a captured `Own[P | None]` moves the
    # enclosing binding's optional, which the record copies.
    (_B, "return x"): "BUGS.md#nested-def-captured-own-return-copies",
    # A storage tuple local rebound to a borrowed tuple copies at the
    # rebind, which sema reads as a borrow: at the sink its record copies,
    # the C++ moves the local's own copy on.
    ("def deep(b: tuple[tuple[A, int32], int32]) -> None:", "xs = [t]"):
        "BUGS.md#owned-local-rebound-from-lvalue-copies-silently",
}


def _copied_elems(ctx: SemanticContext,
                  e: TpyExpr) -> frozenset[int] | None:
    """The top-level elements the owning sink's record of source `e`
    copies; None for its scalar leaf, empty for no copy."""
    paths = verdict(ctx, e).copied
    if () in paths:
        return None
    return frozenset(p[0] for p in paths)


def _span(loc: SourceLocation) -> tuple[tuple[int, int], tuple[int, int]]:
    end = (loc.end_line if loc.end_line is not None else loc.line,
           loc.end_column if loc.end_column is not None else 10**9)
    return (loc.line, loc.column), end


def _source_of(loc: SourceLocation,
               sources: list[TpyExpr]) -> list[TpyExpr]:
    """The registered sources a diagnostic at `loc` speaks for: those at
    its node itself, else the innermost ones its node spans (a statement's
    or a walrus's warning is about the read inside it, not a binding
    spanning the statement; each clone's, where a body is analyzed once
    per clone), else the innermost one its location falls in (a literal
    member's warning)."""
    lo, hi = _span(loc)
    exact = [e for e in sources if _span(e.loc) == (lo, hi)]
    if exact:
        return exact
    within = [e for e in sources
              if lo <= _span(e.loc)[0] and _span(e.loc)[1] <= hi]
    if within:
        return [e for e in within
                if not any(_span(o.loc) != _span(e.loc)
                           and _span(e.loc)[0] <= _span(o.loc)[0]
                           and _span(o.loc)[1] <= _span(e.loc)[1]
                           for o in within)]
    at = (loc.line, loc.column)
    inside = [e for e in sources
              if _span(e.loc)[0] <= at < _span(e.loc)[1]]
    if not inside:
        return []
    best = _span(max(inside, key=lambda e: _span(e.loc)[0]).loc)
    return [e for e in inside if _span(e.loc) == best]


def _member_path(src: TpyExpr, node: TpyExpr,
                 prefix: tuple[int, ...] = ()) -> tuple[int, ...] | None:
    """The path of `node` as a (nested) member of tuple literal `src`."""
    lit = peel_value_wrappers(src)
    if not isinstance(lit, TpyTupleLiteral):
        return None
    for i, m in enumerate(lit.elements):
        if peel_value_wrappers(m) is node:
            return prefix + (i,)
        below = _member_path(m, node, prefix + (i,))
        if below is not None:
            return below
    return None


def _leaves_under(ctx: SemanticContext,
                  node: TpyExpr) -> list[tuple] | None:
    """The record leaves of `node` -- a registered source, or a literal
    member of one -- by their path below it; None when no record reaches
    it."""
    node = peel_value_wrappers(node)
    rec = read_record(ctx, node)
    if rec is not None:
        return list(rec.leaves.items())
    for e, rec in ctx.source_records.items():
        at = _member_path(e, node)
        if at is not None:
            return [(p[len(at):], leaf) for p, leaf in rec.leaves.items()
                    if p[:len(at)] == at]
    return None


def _silent_leaf(ctx: SemanticContext, src: TpyExpr,
                 path: tuple[int, ...]) -> bool:
    """Whether the leaf at `path` of `src` is a `Send[T]` one, which some
    sinks copy untold (BUGS.md#send-own-tuple-leaf-copy-unwarned)."""
    t: TpyType | None = ctx.get_expr_type(peel_value_wrappers(src))
    for i in path:
        tt = bare_tuple(t)
        if tt is None or i >= len(tt.element_types):
            return False
        t = tt.element_types[i]
    if t is None:
        return False
    return isinstance(unwrap_readonly(unwrap_own(t)), SendType)


def _value_bound(entry: object, line: int, t: TpyType | None) -> bool:
    """Whether `t`, read at `line`, is a type parameter its function
    bounds by `ValueType`: the one payload whose copy is proven harmless,
    which a record gives no leaf."""
    t = peel_slot(t) if t is not None else None
    if not isinstance(t, TypeParamRef):
        return False
    funcs = list(entry.ast.functions) + [
        m for r in entry.ast.all_records() for m in r.methods]
    for f in funcs:
        loc = f.loc
        if loc is None or not loc.line <= line <= (loc.end_line or loc.line):
            continue
        bound = f.type_param_bounds.get(t.name)
        if bound is not None and bound.qualified_name() == "tpy.ValueType":
            return True
    return False


def _assert_records_agree_with_warnings(
        entry: object, registered: list[tuple[SemanticContext, TpyExpr]],
        source: str) -> None:
    """Records agree with the FINAL copy diagnostics (deduplicated, a hedge
    an instantiation promoted in its place). Every registered owning sink
    has a record. For each source -- a name and a walrus read included --
    the leaves its record says the sink copies (WARNED / HEDGED) are
    exactly the leaf paths its own warnings name -- HEDGED where a hedge
    ("may copy") names it -- a whole-tuple copy warning naming every leaf
    the record copies (and requiring one), a scalar's copy warning its one
    leaf at `()`; an explicit `copy()` leaf is named by none, a `copy()`
    source's owned leaves are EXPLICIT, and a warning names only leaves the
    record copies. A copied leaf no warning names is a `Send[T]` leaf (a
    filed silent copy).
    Lines matching a
    `_DIAGNOSTIC_DISAGREEMENTS` key are skipped."""
    ctx = entry.analyzer.ctx
    compat = entry.analyzer.compat
    lines = source.splitlines()
    known = {text: slug for (marker, text), slug
             in _DIAGNOSTIC_DISAGREEMENTS.items() if marker in source}

    def text_at(line: int) -> str:
        return (lines[line - 1] if 0 < line <= len(lines) else "").strip()

    def excluded(line: int) -> bool:
        return text_at(line) in known

    for owner, e in registered:
        # A library module's sources have their records in its own context.
        if owner is not ctx:
            continue
        if e.loc is not None and not excluded(e.loc.line):
            assert read_record(ctx, e) is not None, (
                e.loc.line, type(e).__name__)
    sources = [e for e in ctx.source_records.keys() if e.loc is not None]
    named: dict[int, set[tuple[int, ...]]] = {id(e): set() for e in sources}
    hedged: dict[int, set[tuple[int, ...]]] = {id(e): set()
                                                for e in sources}
    whole: dict[int, bool] = {}
    for d in ctx.diagnostics:
        if not (d.level.value == "warning" and d.loc is not None):
            continue
        m = _LEAF_IN_WARNING.search(d.message)
        scalar = _SCALAR_IN_WARNING.search(d.message)
        if not (m or scalar or _WHOLE_IN_WARNING.search(d.message)):
            continue
        if (_TEMP_ARG_WARNING.search(d.message)
                or _ELEMENT_COPY_WARNING.search(d.message)):
            continue
        if excluded(d.loc.line):
            continue
        hedge = d.message.startswith("may copy")
        # A warning outside every source is a sink's that keeps no record
        # (a local binding's member copy).
        for e in _source_of(d.loc, sources):
            at = (tuple(int(i) for i in m.group(1).split(".")) if m
                  else () if scalar else None)
            if at is None:
                whole[id(e)] = hedge or whole.get(id(e), False)
                continue
            named[id(e)].add(at)
            if hedge:
                hedged[id(e)].add(at)
    for e in sources:
        rec = ctx.source_records[e]
        if excluded(e.loc.line):
            continue
        perms = {p: leaf.permission for p, leaf in rec.leaves.items()}
        copied = {p for p, perm in perms.items() if perm.copies}
        explicit = {p for p, perm in perms.items()
                    if perm is LeafPermission.EXPLICIT}
        mine = named[id(e)]
        hedges = hedged[id(e)]
        where = (e.loc.line, type(e).__name__)
        if id(e) in whole:
            assert copied, (where, perms)
            mine = mine | copied
            if whole[id(e)]:
                hedges = hedges | copied
        assert mine <= copied, (where, copied, mine)
        # A copied leaf no warning names is a `Send[T]` leaf.
        assert all(_silent_leaf(ctx, e, p)
                   for p in copied - mine), (where, perms, mine)
        assert all((perms[p] is LeafPermission.HEDGED) == (p in hedges)
                   for p in mine), (where, perms, hedges)
        assert not (explicit & mine), (where, explicit, mine)
        if compat.is_copy_call(e):
            assert all(leaf.permission is LeafPermission.EXPLICIT
                       for leaf in rec.leaves.values() if leaf.slot_owns), (
                where, perms)


def _compile(source: str) -> tuple[object, list]:
    """Compile `source` and check its records against its warnings
    (`_assert_records_agree_with_warnings`), over every source an owning
    sink registered."""
    registered: list[tuple[SemanticContext, TpyExpr]] = []
    register = TypeCompatibility.register_sink

    def recording(self: TypeCompatibility, src: TpyExpr | None,
                  slot: TpyType | None, *args: object,
                  **kwargs: object) -> None:
        register(self, src, slot, *args, **kwargs)
        # The registrations `register_sink` keeps, by its own rule.
        if (src is not None
                and peel_value_wrappers(src) in self.ctx.func.tuple_sinks):
            registered.append((self.ctx, src))

    TypeCompatibility.register_sink = recording
    try:
        compiler, modules = _compile_program(source)
    finally:
        TypeCompatibility.register_sink = register
    _assert_records_agree_with_warnings(_entry(modules), registered, source)
    _assert_move_leaves_render_moves(compiler, _entry(modules), source)
    return compiler, modules


# The reads an owning sink's record can move: a name, and the element or
# the consuming method's field a sink moves out of storage the body owns.
_READS = (THIRName, THIRSelf, THIRSubscript, THIRFieldAccess)
# The render gate's two failures (`_assert_move_leaves_render_moves`).
_MOVE_NOT_RENDERED = "RENDER: a moved leaf is not moved"
_COPY_MOVED = "RENDER: a copied leaf is moved"
_FRAMES_NOT_LOWERED = "RENDER: frames not lowered"


def _peel_read(e: object) -> object:
    """The read a conversion, coercion or move wraps."""
    while not isinstance(e, _READS):
        inner = getattr(e, "value", None) or getattr(e, "expr", None)
        if inner is None or not isinstance(e, (THIRMove, THIRCoerce,
                                               THIRFormConvert,
                                               THIRConsumingIter)):
            return e
        e = inner
    return e


def _peel_moved(e: object) -> object:
    """The read a move of `e` moves out of; None where a conversion between
    them copies the read first (the move then takes the copy)."""
    while not isinstance(e, _READS):
        if isinstance(e, THIRFormConvert):
            if not e.move:
                return None
            e = e.value
        elif isinstance(e, THIRCoerce):
            e = e.expr
        elif isinstance(e, (THIRMove, THIRConsumingIter)):
            e = e.value
        else:
            return None
    return e


def _taken_paths(read: object) -> list[tuple[int, ...]]:
    """What a whole move of `read` takes: a scalar's object (`()`), a
    tuple's elements it holds by value (`(i,)`) -- a pointer element's
    object stays where it is. A borrow-form tuple read with no element
    layout holds its elements as its type spells them (an `Own` element
    inline, a reference one as a pointer)."""
    src = read.source
    if src is not None and src.const:
        # A const object's `std::move` copies it.
        return []
    if src is not None and src.elems is not None:
        return [(i,) for i, el in enumerate(src.elems)
                if not el.pointer_held]
    t = bare_tuple(src.binding.type if src is not None
                   and src.binding is not None else None)
    if t is not None and src.held is Held.BORROWED:
        return [(i,) for i, et in enumerate(t.element_types)
                if not TupleType._element_is_pointer_repr(et)]
    return [()]


def _at(e: object) -> 'tuple[int, int] | None':
    loc = getattr(e, "loc", None)
    return (loc.line, loc.column) if loc is not None else None


# Bodies of the sources under test that do not lower yet: their reject ends
# emission before the resumable frames, which the render gate then cannot
# read, so a reject is excused only in a program holding the entry's marker
# snippet, at its (body, reject detail). Each names the test whose program
# reaches it (`test_unlowered_bodies_still_reject` keeps every entry live)
# and the entry tracking the reject. Any other frame left unlowered fails
# the gate.
_UNLOWERED_BODIES: dict[tuple[str, str, str], tuple[str, str]] = {
    # A `@dynamic` local at an `Own[P]` parameter has no well-formed
    # render: the argument's conversion refuses it.
    ("def dyn(k: int32) -> int32:", "dyn", "expr.call:arg.lift.borrow"):
        ("test_blocker_rows_are_recorded",
         "BUGS.md#dyn-local-own-arg-ill-formed"),
    (", xs: list[tuple[T, int32]]) -> None:", "app",
     "expr.call:call.ret_type.tuple"):
        ("test_stamped_generic_lending_call_hedges_each_leaf",
         "BUGS.md#open-t-tuple-generic-only-rejects"),
    (", xs: list[tuple[T, int32]]) -> None:", "app",
     "expr.method_call:method.ret_type"):
        ("test_stamped_generic_lending_call_hedges_each_leaf",
         "BUGS.md#open-t-tuple-generic-only-rejects"),
    ("def comp(a: A) -> list[tuple[A, A]]:", "comp",
     "expr.list_comprehension"):
        ("test_every_position_publishes_the_record",
         "scripts/thir_migration/review/bins_expr2.json"),
    ("def elem_arg", "f", "stmt.var_decl:decl.tuple_literal_shape"):
        ("test_wrapped_and_union_leaves_at_every_owning_sink",
         "scripts/thir_migration/review/bins_stmt3.json"),
    ("def elem_arg", "f",
     "stmt.var_decl:decl.tuple_literal_shape:expr.tuple_literal"):
        ("test_wrapped_and_union_leaves_at_every_owning_sink",
         "BUGS.md#send-own-tuple-leaf-copy-unwarned"),
    ("def gen(t: tuple[A | B, int32]) -> Iterator[Own[tuple[A | B, int32]]]:",
     "gen", "res.param_type:tuple"):
        ("test_yielded_tuple_name_union_leaf_warns",
         "BUGS.md#res-param-tuple-element-shapes"),
    ("def gen[T](t: tuple[A | T, int32]) -> "
     "Iterator[Own[tuple[A | T, int32]]]:", "gen", "res.param_type:tuple"):
        ("test_open_union_leaf_of_a_name_hedges_at_a_yield",
         "BUGS.md#res-param-tuple-element-shapes"),
    ("async def ar(t: tuple[A | B, int32]) -> Own[tuple[A | B, int32]]:",
     "ar", "res.param_type:tuple"):
        ("test_async_return_of_a_union_leaf_name_warns",
         "BUGS.md#res-param-tuple-element-shapes"),
    ("def lam() -> int32:", "lam", "stmt.return:subscript.tuple_shape"):
        ("test_owning_yield_and_lambda_parameter_names_publish_records",
         "scripts/thir_migration/review/bins_expr1.json"),
    ("def nyield[T]", "nyield", "res.param_type:tuple"):
        ("test_nested_yield_member_of_stamped_call_is_hedged",
         "BUGS.md#open-t-tuple-generic-only-rejects"),
    ("def to_walrus(a: A) -> Iterator[tuple[Own[A], int32]]:", "to_walrus",
     "expr.walrus"):
        ("test_own_tuple_yield_takes_the_return_rule",
         "BUGS.md#walrus-tuple-owning-sink-rejects"),
    ("def r1(p: A) -> Own[tuple[A, int32]] | None:", "r1",
     "expr.tuple_literal"):
        ("test_nullable_own_tuple_yield_takes_the_return_rule",
         "scripts/thir_migration/review/bins_expr3.json"),
    ("def ret() -> tuple[Own[A], int32]:", "ret", "expr.tuple_literal"):
        ("test_walrus_and_lambda_parameter_reads_keep_their_context",
         "scripts/thir_migration/review/bins_expr3.json"),
    ("def walrus_arg(a: A, b: B) -> None:", "walrus_arg", "expr.walrus"):
        ("test_nested_members_and_walrus_args_follow_their_sites_rule",
         "BUGS.md#walrus-tuple-owning-sink-rejects"),
}
# The `_UNLOWERED_BODIES` entries the render gate excused in this process.
_UNLOWERED_USED: set[tuple[str, str, str]] = set()


def _reject_key(err: Exception) -> 'tuple[str, str] | None':
    m = re.search(r"in function '(\w+)'.*\(([\w.:]+)\)", str(err))
    return (m.group(1), m.group(2)) if m is not None else None


def _emitted_reads(compiler: object, entry: object, source: str
                   ) -> tuple[set, set, set]:
    """The bodies codegen lowers for the entry module -- every function,
    method and constructor, and each resumable frame emission reached
    (as `collect_thir` runs it; a body that does not lower contributes
    nothing): the
    name reads they render, by location, and the moves their consuming
    operations make of them -- `(line, column, path)`, path `()` for the
    whole read (a `std::move`, a moving conversion, a consuming iteration,
    the `return` of the bare name a plain body moves implicitly), `(i,)`
    for an element a tuple conversion moves out. The third set holds the
    moves that take the read itself, not a copy a conversion made of it
    first (`_peel_moved`): a scalar's at `()`, a tuple's per element it
    holds by value."""
    rendered: set = set()
    moved: set = set()
    taken: set = set()
    seen: set[int] = set()

    def mark(e: object, path: tuple, *, implicit: bool = False) -> None:
        read = _peel_read(e)
        # A tuple converted element by element reads each element of its
        # source (a located-less tuple subscript): moving one moves that
        # element of the source read.
        while (isinstance(read, THIRSubscript) and read.tuple_index is not None
               and read.loc is None):
            path = (read.tuple_index,)
            read = _peel_read(read.receiver)
        at = _at(read)
        if at is not None and isinstance(read, _READS):
            moved.add(at + (path,))
            binding = read.source.binding if read.source else None
            # A returned name C++ moves by itself only where it names an
            # object of the body's own (a reference or pointer copies).
            if implicit and not (binding is not None and binding.owned
                                 and not binding.pointer):
                return
            if path:
                taken.add(at + (path,))
            elif _peel_moved(e) is read:
                # A whole tuple move takes each element the read holds by
                # value; a pointer element's object stays where it is.
                taken.update({at + (p,) for p in _taken_paths(read)})

    def walk(node: object, frame: bool) -> None:
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, frame)
            return
        if isinstance(node, (dict, IdentityMap)):
            for x in node.values():
                walk(x, frame)
            return
        if not dataclasses.is_dataclass(node) or isinstance(node, type):
            return
        if not isinstance(node, (THIRNode, THIRFunction,
                                 THIRResumableLeaves)) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, _READS) and _at(node) is not None:
            rendered.add(_at(node))
        if isinstance(node, (THIRMove, THIRConsumingIter)):
            mark(node.value, ())
        elif isinstance(node, THIRMethodCall) and node.move_receiver:
            mark(node.receiver, ())
        elif isinstance(node, THIRFormConvert):
            if node.move:
                mark(node.value, ())
        elif (isinstance(node, THIRReturn) and not frame
              and node.value is not None):
            read = node.value
            while isinstance(read, THIRCoerce):
                read = read.expr
            if isinstance(read, THIRName) and read.reads_binding:
                mark(read, (), implicit=True)
        for f in dataclasses.fields(node):
            if f.name != "source":
                walk(getattr(node, f.name), frame)

    rejected: Exception | None = None
    with activate_compiler(compiler):
        codegen = compiler._make_codegen(entry, None)
        compiler._thir_survey = True
        try:
            compiler._run_codegen(codegen, entry, entry.name)
        except (ThirRejectError, THIRValidationError) as ex:
            rejected = ex
        finally:
            compiler._thir_survey = False
            compiler._thir_deferred_rejects = []
    ctx = codegen.ctx
    # A reject ends emission before the resumable frames are lowered, so a
    # gate that skipped it would pass over every frame vacuously: a body
    # that does not lower is skipped only where no frame is left out.
    frames = [f.name for f, _ in iter_module_callables(entry.ast,
                                                       entry.analyzer)
              if (f.is_generator or f.is_async)
              and ctx.thir_resumables.get(f) is None]
    if frames:
        at = (_reject_key(rejected) if isinstance(rejected, ThirRejectError)
              else None)
        excused = [k for k in _UNLOWERED_BODIES
                   if k[0] in source and k[1:] == at]
        assert excused, (_FRAMES_NOT_LOWERED, frames, rejected)
        _UNLOWERED_USED.update(excused)
    walk([ctx.thir_functions, ctx.thir_overload_functions,
          ctx.thir_top_level, ctx.thir_constructors], False)
    walk(ctx.thir_resumables, True)
    return rendered, moved, taken


def _assert_move_leaves_render_moves(compiler: object, entry: object,
                                     source: str) -> None:
    """Records agree with the render: a name read whose record moves a leaf
    is a read the emitted C++ moves -- the read whole, or the element the
    leaf is under -- or one the C++ moves by itself: a lambda's returned
    parameter, and a comprehension clause's moving sink. And the inverse:
    a read whose record copies a leaf is never moved out of whole, nor is
    the element that leaf is under (a move of a copy a conversion made
    first does not count). A read the C++ does not render (its body does
    not lower) and a `_RENDER_DISAGREEMENTS` row are skipped."""
    ctx = entry.analyzer.ctx
    rendered, moved_at, taken_at = _emitted_reads(compiler, entry, source)
    lines = source.splitlines()
    skipped = {text for (marker, text) in _RENDER_DISAGREEMENTS
               if marker in source}
    # A walrus's record is keyed on its value: a name value is that name's
    # read, a literal's members are member reads.
    names = [e for e in (*ctx.source_records.keys(),
                         *ctx.member_sources.keys())
             if e.loc is not None and (
                 isinstance(e, TpyName)
                 or (isinstance(e, TpySubscript)
                     and isinstance(e.obj, TpyName))
                 or (isinstance(e, TpyFieldAccess) and e.consuming_move))]
    for e in names:
        text = (lines[e.loc.line - 1].strip()
                if 0 < e.loc.line <= len(lines) else "")
        at = (e.loc.line, e.loc.column)
        if text in skipped or at not in rendered:
            continue
        rec = verdict(ctx, e)
        for p in rec.copied:
            assert not (at + ((),) in taken_at
                        or (p and at + (p[:1],) in taken_at)), (
                _COPY_MOVED, e.loc.line, text, p)
        moved = sorted(rec.moved)
        if not moved:
            continue
        binding = ctx.read_bindings.get(e)
        if ((binding is not None and binding.kind is ReadKind.LAMBDA)
                or ctx.comp_moves.get(e) is not None):
            continue
        for p in moved:
            assert (at + ((),) in moved_at
                    or (p and at + (p[:1],) in moved_at)), (
                _MOVE_NOT_RENDERED, e.loc.line, text, p)

PRELUDE = """\
from tpy import int32, Own
class A:
    def __init__(self, x: int32) -> None:
        self.x = x
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
    def get(self, a: A) -> tuple[A, int32]:
        return (a, 2)
    def ref_pair(self) -> tuple[A, int32]:
        return self.p
def mk(a: A) -> tuple[A, int32]:
    return (a, 1)
def mixed(a: A) -> tuple[Own[A], A]:
    return (A(a.x), a)
def owning(a: A) -> Own[tuple[A, A]]:
    return (A(a.x), A(a.x))
# Only their signatures are read: these literals do not lower at these
# results yet, and a body that rejects ends emission before the resumable
# frames the render gate reads.
def mk2(a: A) -> tuple[A, A]:
    raise ValueError("signature only")
def nested_mark(a: A) -> tuple[A, tuple[Own[A], int32]]:
    raise ValueError("signature only")
def nested_mixed(a: A) -> tuple[Own[tuple[A, int32]], A]:
    raise ValueError("signature only")
"""


def _returned(source: str) -> dict[str, tuple[TpyExpr, object]]:
    """The returned expression of every function of `source` (after the
    prelude), coercions peeled, by function name, with the analyzer."""
    _compiler, modules = _compile(PRELUDE + source)
    entry = _entry(modules)
    out = {}
    for f in entry.ast.functions:
        for s in f.body:
            if isinstance(s, TpyReturn) and s.value is not None:
                v = s.value
                while isinstance(v, TpyCoerce):
                    v = v.expr
                out[f.name] = (v, entry.analyzer)
    return out


@pytest.mark.parametrize("fn, kind", [
    ("r_call", TpyCall),
    ("r_method", TpyMethodCall),
    ("r_field", TpyFieldAccess),
    ("r_subscript", TpySubscript),
    ("r_select_name", TpyIfExpr),
])
def test_copied_elements_recorded_for_every_source_kind(fn: str,
                                                        kind: type) -> None:
    returned = _returned("""\
def r_call(a: A) -> Own[tuple[A, int32]]:
    return mk(a)
def r_method(h: H, a: A) -> Own[tuple[A, int32]]:
    return h.get(a)
def r_field(h: H) -> Own[tuple[A, int32]]:
    return h.p
def r_subscript(xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    return xs[0]
def r_select_name(a: A, c: bool) -> Own[tuple[A, int32]]:
    p = mk(a)
    return mk(a) if c else p
""")
    expr, analyzer = returned[fn]
    assert isinstance(expr, kind)
    # Element 0 is the borrowed reference the owning slot copies.
    assert _copied_elems(analyzer.ctx, expr) == frozenset({0})


def test_name_keeps_its_return_check_record() -> None:
    # A tuple NAME's copy is recorded by the one recorder, by index.
    expr, analyzer = _returned("""\
def r_name(a: A) -> Own[tuple[A, int32]]:
    t = mk(a)
    return t
""")["r_name"]
    assert isinstance(expr, TpyName)
    assert _copied_elems(analyzer.ctx, expr) == frozenset({0})


def test_owning_call_records_nothing() -> None:
    expr, analyzer = _returned("""\
def r_owning(a: A) -> Own[tuple[A, A]]:
    return owning(a)
""")["r_owning"]
    assert not _copied_elems(analyzer.ctx, expr)


def _warnings(analyzer) -> list[str]:
    return [d.message for d in analyzer.ctx.diagnostics
            if d.level.value == "warning"]


def test_owning_call_nested_value_tuple_records_nothing() -> None:
    # `-> Own[tuple[...]]` hands over every element, a nested value tuple's
    # reference members included: the field store copies nothing. (The
    # `nested_owned` body itself is refused by the lowering, not by sema.)
    _compiler, modules = _compile(PRELUDE + """def nested_owned() -> Own[tuple[tuple[A, int32], int32]]:
    return ((A(1), 1), 2)
class N:
    q: tuple[tuple[A, int32], int32]
    def __init__(self) -> None:
        self.q = nested_owned()
""")
    entry = _entry(modules)
    init = next(m for r in entry.ast.all_records() if r.name == "N"
                for m in r.methods if m.name == "__init__")
    store = next(s for s in init.body if isinstance(s, TpyAssign))
    v = store.value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyCall)
    assert not _copied_elems(entry.analyzer.ctx, v)
    assert not [w for w in _warnings(entry.analyzer) if "tuple element" in w]


def test_owned_element_yield_of_borrow_call_records_copy() -> None:
    # `yield mk(a)` at an `Own` element: the borrowed element 0 copies,
    # warned and declared, as the return's does.
    _compiler, modules = _compile("from typing import Iterator\n" + PRELUDE
                                  + """def gen(a: A) -> Iterator[tuple[Own[A], int32]]:
    yield mk(a)
""")
    entry = _entry(modules)
    gen = next(f for f in entry.ast.functions if f.name == "gen")
    v = next(s for s in gen.body if isinstance(s, TpyYield)).value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyCall)
    assert _copied_elems(entry.analyzer.ctx, v) == frozenset({0})
    assert any("copies A into owned storage (tuple element 0)" in w
               for w in _warnings(entry.analyzer))


def test_field_returning_method_owns_nothing() -> None:
    # A method handing back a field of `self` hands over no element.
    inits, analyzer = _initializers("r = h.ref_pair()\nprint(r[0].x)")
    expr = inits["r"]
    assert isinstance(expr, TpyMethodCall)
    slot = analyzer.get_expr_type(expr)
    assert tuple_source_elem_owned(analyzer.ctx, expr, slot) == (False, False)


def _initializers(body: str) -> tuple[dict[str, TpyExpr], object]:
    """The initializer of every local of `subject` (after the prelude),
    coercions peeled, by name, with the analyzer."""
    _compiler, modules = _compile(
        PRELUDE + "def subject(c: bool, a: A, h: H, t: tuple[A, A]) -> None:\n"
        + "".join("    " + line + "\n" for line in body.splitlines()))
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    out = {}
    for st in subject.body:
        if isinstance(st, TpyVarDecl) and st.init is not None:
            v = st.init
            while isinstance(v, TpyCoerce):
                v = v.expr
            out[st.name] = v
    return out, entry.analyzer


@pytest.mark.parametrize("name, expected", [
    # A borrow-returning call hands nothing over.
    ("borrow", (False, False)),
    # The Own positions of the declared return.
    ("mix", (True, False)),
    # Every reference element of `-> Own[tuple[...]]`.
    ("own", (True, True)),
    # A select owns an element only where every arm does.
    ("sel", (True, False)),
    ("sel_own", (True, True)),
    # A NAME arm owns nothing.
    ("sel_name", (False, False)),
    # A name, a field and a literal of borrowed members own nothing.
    ("alias", (False, False)),
    ("field", (False, False)),
    ("lit", (False, False)),
])
def test_rvalue_source_ownership_per_arm(name: str,
                                         expected: tuple[bool, ...] | None
                                         ) -> None:
    inits, analyzer = _initializers("""\
borrow = mk2(a)
mix = mixed(a)
own = owning(a)
sel = mixed(a) if c else mixed(a)
sel_own = owning(a) if c else owning(a)
sel_name = mixed(a) if c else mix
alias = t
field = h.p
lit = (a, a)
print(borrow[0].x, mix[0].x, own[0].x, sel[0].x, sel_own[0].x)
print(sel_name[0].x, alias[0].x, field[0].x, lit[0].x)
""")
    expr = inits[name]
    slot = analyzer.get_expr_type(expr)
    assert tuple_source_elem_owned(analyzer.ctx, expr, slot) == expected


@pytest.mark.parametrize("target, owned", [
    # A borrowing call lends its element: the target aliases it.
    ("b0", False),
    # An `Own` element marker hands that element over, its sibling not.
    ("m0", True),
    ("m1", False),
    # `-> Own[tuple]` hands every reference element over.
    ("o0", True),
    ("o1", True),
    # A method's plain `-> tuple` lends; list.pop hands its element over.
    ("g0", False),
    ("p0", True),
    # A tuple NAME read again keeps its element.
    ("t0", False),
    # A scalar element is a value bind, never an owned one.
    ("pn", False),
    # An open T element is one body for every instantiation: not owned.
    ("x0", False),
])
def test_unpack_target_owns_what_its_call_source_hands_over(
        target: str, owned: bool) -> None:
    # The unpack's per-target tag and the target's ownership record are
    # one verdict: the source's per-element hand-over.
    _compiler, modules = _compile(PRELUDE + """\
def owning_t[T](x: T) -> Own[tuple[T, int32]]:
    raise ValueError("signature only")
def generic[T](x: T) -> None:
    x0, x1 = owning_t(x)
    print(x1)
def subject(a: A, h: H, t: tuple[A, int32]) -> None:
    b0, b1 = mk(a)
    m0, m1 = mixed(a)
    o0, o1 = owning(a)
    g0, g1 = h.get(a)
    st = [(A(1), 1)]
    p0, pn = st.pop()
    t0, t1 = t
    print(b0.x, b1, m0.x, m1.x, o0.x, o1.x, g0.x, g1, p0.x, pn, t0.x, t1)
    print(t[0].x)
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    body = next(f for f in entry.ast.functions
                if f.name == ("generic" if target == "x0" else "subject"))
    unpack = next(s for s in body.body if isinstance(s, TpyTupleUnpack)
                  and target in s.targets)
    i = unpack.targets.index(target)
    assert unpack.is_owned[i] is owned
    assert bool(local_ownership(ctx, body, target)) is owned


_ANY_SOURCE = """\
import asyncio
class Store:
    k: int32
    def __init__(self) -> None:
        self.k = 1
    def __getitem__(self, i: int32) -> Own[tuple[A, int32]]:
        raise ValueError("signature only")
async def a_owning(a: A) -> Own[tuple[A, A]]:
    raise ValueError("signature only")
async def a_mixed(a: A) -> tuple[Own[A], int32]:
    raise ValueError("signature only")
def subject(a: A, h: H, s: Store, xs: list[tuple[Own[A], int32]]) -> None:
    w0, w1 = (w := owning(a))
    s0, s1 = s[0]
    f0, f1 = h.p
    x0, x1 = xs[0]
    print(w0.x, w1.x, w[0].x, s0.x, s1, f0.x, f1, x0.x, x1)
async def asubject(a: A) -> int32:
    q0, q1 = await a_owning(a)
    r0, r1 = await a_mixed(a)
    t = asyncio.create_task(a_owning(a))
    u0, u1 = await t
    fut: asyncio.Future[tuple[A, int32]] = asyncio.Future()
    fut.set_result((A(5), 6))
    v0, v1 = await fut
    return q0.x + q1.x + r0.x + r1 + u0.x + u1.x + v0.x + v1
"""


@pytest.mark.parametrize("target, owned", [
    # A walrus is the name it binds: its elements stay the name's.
    ("w0", False),
    # A `__getitem__` returning by value hands over what it declares.
    ("s0", True),
    # A field and a container's element lend every reference element,
    # whatever `Own` the element type spells.
    ("f0", False),
    ("x0", False),
    # An await hands over its awaited result's `Own` markers.
    ("q0", True),
    ("q1", True),
    ("r0", True),
    # A `Task` / `Future` payload hands over what the payload type's
    # markers own, as analyzed.
    ("u0", False),
    ("v0", False),
])
def test_unpack_target_owns_what_any_source_hands_over(
        target: str, owned: bool) -> None:
    # Every non-name source takes the one rule a call source does: the
    # target owns the element its source hands over (`tuple_borrowed_paths`).
    _compiler, modules = _compile_program(PRELUDE + _ANY_SOURCE)
    entry = _entry(modules)
    body = next(f for f in entry.ast.functions
                if f.name == ("asubject" if target[0] in "qruv" else "subject"))
    unpack = next(s for s in body.body
                  if isinstance(s, TpyTupleUnpack) and target in s.targets)
    i = unpack.targets.index(target)
    assert unpack.is_owned[i] is owned
    assert unpack.is_ref[i] is not owned


@pytest.mark.parametrize("name, expected", [
    # A walrus lends what the name it binds does: every reference path.
    ("walrus", {(0,), (1,)}),
    # A `__getitem__` returning by value lends as its declared return.
    ("getitem", set()),
])
def test_borrowed_paths_of_walrus_and_getitem(name: str,
                                              expected: set[tuple[int, ...]]
                                              ) -> None:
    _compiler, modules = _compile_program(PRELUDE + _ANY_SOURCE)
    entry = _entry(modules)
    body = next(f for f in entry.ast.functions if f.name == "subject")
    unpack = next(s for s in body.body if isinstance(s, TpyTupleUnpack)
                  and s.targets[0] == ("w0" if name == "walrus" else "s0"))
    expr = unpack.value
    slot = entry.analyzer.get_expr_type(expr)
    assert tuple_borrowed_paths(entry.analyzer.ctx, expr, slot) == expected


@pytest.mark.parametrize("name, expected", [
    # A literal lends each member it does not capture by value.
    ("lit", {(0,), (1,)}),
    # A call: the plain reference elements of its declared return.
    ("borrow", {(0,), (1,)}),
    # An `Own` marker at any depth hands its subtree over.
    ("mark", {(0,)}),
    # A wholly-owned nested element lends nothing; its sibling does.
    ("nest_mix", {(1,)}),
    # `-> Own[tuple]` lends nothing.
    ("own", set()),
    # A select lends what either arm lends.
    ("sel", {(1,)}),
    ("sel_name", {(0,), (1,)}),
    # Every reference path of a name, a field, a subscript.
    ("alias", {(0,), (1,)}),
    ("field", {(0,)}),
    ("sub", {(0,)}),
    # A method handing back its own field lends it.
    ("ref", {(0,)}),
])
def test_borrowed_paths_per_source_kind(name: str,
                                        expected: set[tuple[int, ...]]
                                        ) -> None:
    inits, analyzer = _initializers("""\
lit = (a, a)
borrow = mk2(a)
mark = nested_mark(a)
nest_mix = nested_mixed(a)
own = owning(a)
sel = mixed(a) if c else mixed(a)
sel_name = mk2(a) if c else t
alias = t
field = h.p
xs = [h.p]
sub = xs[0]
ref = h.ref_pair()
print(lit[0].x, borrow[0].x, mark[0].x, nest_mix[1].x, own[0].x, sel[0].x)
print(sel_name[0].x, alias[0].x, field[0].x, sub[0].x, ref[0].x)
""")
    expr = inits[name]
    slot = analyzer.get_expr_type(expr)
    assert tuple_borrowed_paths(analyzer.ctx, expr, slot) == expected


def test_borrowed_paths_none_for_no_tuple() -> None:
    inits, analyzer = _initializers("n = a\nprint(n.x)")
    expr = inits["n"]
    assert tuple_borrowed_paths(analyzer.ctx, expr,
                                analyzer.get_expr_type(expr)) is None


def _records(source: str) -> tuple[dict[int, object], list[str]]:
    """The elements each source of `source` (after the prelude) copies
    (`_copied_elems`), by line, with the warnings."""
    _compiler, modules = _compile(PRELUDE + source)
    entry = _entry(modules)
    return (_copied_by_line(entry.analyzer.ctx, PRELUDE.count("\n")),
            _warnings(entry.analyzer))


def _copied_by_line(ctx: SemanticContext,
                    offset: int) -> dict[int, frozenset[int] | None]:
    """Every source with a copying leaf, by line after `offset`: the
    elements it copies (`_copied_elems`)."""
    out: dict[int, frozenset[int] | None] = {}
    for e in [*ctx.source_records.keys(), *ctx.member_sources.keys()]:
        copied = _copied_elems(ctx, e)
        if (e.loc is not None and e.loc.line > offset
                and copied != frozenset()):
            out[e.loc.line - offset] = copied
    return out


def test_select_with_name_arm_records_at_every_sink() -> None:
    # A borrowing call or a name, at a return, a whole `Own` argument, an
    # `Own` element argument and an append: element 0 copies at each.
    records, warnings = _records("""\
def take(p: Own[tuple[A, int32]]) -> int32:
    return p[0].x
def take_elem(p: tuple[Own[A], int32]) -> int32:
    return p[0].x
def at_return(a: A, c: bool) -> Own[tuple[A, int32]]:
    p = mk(a)
    return mk(a) if c else p
def at_param(a: A, c: bool) -> int32:
    p = mk(a)
    return take(mk(a) if c else p)
def at_element_param(a: A, c: bool) -> int32:
    p = mk(a)
    return take_elem(mk(a) if c else p)
def at_append(a: A, c: bool) -> None:
    p = mk(a)
    xs: list[tuple[A, int32]] = []
    xs.append(mk(a) if c else p)
""")
    assert records == {7: frozenset({0}), 10: frozenset({0}),
                       13: frozenset({0}), 17: frozenset({0})}
    assert sum("copies A into owned storage (tuple element 0)" in w
               for w in warnings) == 4


def test_nested_mixed_records_only_the_borrowed_element() -> None:
    # Element 0 is a wholly-owned nested tuple; only element 1 copies.
    records, warnings = _records("""\
class N:
    p: tuple[tuple[A, int32], A]
    def __init__(self, a: A) -> None:
        self.p = nested_mixed(a)
""")
    assert records == {4: frozenset({1})}
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into field (tuple element 1); use copy() to make this "
        "explicit"]


def test_nested_own_markers_record_nothing() -> None:
    # `tuple[Own[A], tuple[Own[A], int32]]` into a field: every reference
    # element is handed over.
    records, warnings = _records("""\
def mixn(a: A) -> tuple[Own[A], tuple[Own[A], int32]]:
    return (A(1), (A(2), 1))
class N:
    r: tuple[A, tuple[A, int32]]
    def __init__(self, a: A) -> None:
        self.r = mixn(a)
""")
    assert records == {}
    assert not [w for w in warnings if "tuple element" in w]


def test_equal_type_select_of_owning_names_records_copy() -> None:
    # A select of two `tuple[Own[A], int32]` names has the parameter's very
    # type, yet a select arm never moves: element 0 copies. (The C++ build
    # of the program fails: the select is an lvalue at a `&&` parameter.)
    records, warnings = _records("""\
def fresh() -> tuple[Own[A], int32]:
    return (A(1), 1)
def take(p: tuple[Own[A], int32]) -> int32:
    return p[0].x
def names(c: bool) -> int32:
    p = fresh()
    q = fresh()
    r = take(p if c else q)
    return r + p[0].x + q[0].x
def call_name(c: bool) -> int32:
    p = fresh()
    r = take(fresh() if c else p)
    return r + p[0].x
""")
    assert records == {8: frozenset({0}), 12: frozenset({0})}
    assert warnings.count("copies A into owned storage (tuple element 0); "
                          "use copy() to make this explicit") == 2


NESTED = """\
class N:
    p: tuple[tuple[A, int32], int32]
    def __init__(self) -> None:
        self.p = ((A(1), 1), 2)
"""


def test_nested_slot_marker_records_its_element() -> None:
    # An `Own` marker nested in the slot owns path 0.0: the field source
    # lends it, so it copies, warned by its path and recorded by element 0.
    records, warnings = _records(NESTED + """\
def take(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return 1
def ret(n: N) -> tuple[tuple[Own[A], int32], int32]:
    return n.p
def run(n: N) -> int32:
    return take(n.p)
""")
    assert records == {8: frozenset({0}), 10: frozenset({0})}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2


def test_nested_slot_marker_at_owned_yield() -> None:
    _compiler, modules = _compile("from typing import Iterator\n" + PRELUDE
                                  + NESTED + """\
def gen(n: N) -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    yield n.p
""")
    entry = _entry(modules)
    gen = next(f for f in entry.ast.functions if f.name == "gen")
    v = next(s for s in gen.body if isinstance(s, TpyYield)).value
    while isinstance(v, TpyCoerce):
        v = v.expr
    assert isinstance(v, TpyFieldAccess)
    assert _copied_elems(entry.analyzer.ctx, v) == frozenset({0})
    assert [w for w in _warnings(entry.analyzer) if "tuple" in w] == [
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit"]


def test_returned_nested_field_records_its_element() -> None:
    # `return n.p` copies element 0.0 into the owned return slot.
    records, warnings = _records(NESTED + """\
def direct(n: N) -> Own[tuple[tuple[A, int32], int32]]:
    return n.p
""")
    assert records == {6: frozenset({0})}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"]


def test_returned_nested_param_warns_like_its_append() -> None:
    # A nested tuple PARAMETER returned into `Own[tuple[...]]` copies path
    # 0.0, warned as the append of the same parameter warns it.
    records, warnings = _records("""\
def ret(x: tuple[tuple[A, int32], int32]) -> Own[tuple[tuple[A, int32], int32]]:
    return x
def app(x: tuple[tuple[A, int32], int32]) -> None:
    ys: list[tuple[tuple[A, int32], int32]] = []
    ys.append(x)
""")
    assert records[2] == frozenset({0})
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2


def test_mixed_local_field_store_moves_its_nocopy_element() -> None:
    # The local owns element 0 and the store is its last use: the
    # `@nocopy` element moves, only the borrowed element 1 copies.
    _compiler, modules = _compile(PRELUDE + """\
from tpy import nocopy
@nocopy
class G:
    def __init__(self, x: int32) -> None:
        self.x = x
class F:
    gp: tuple[G, A]
    def __init__(self) -> None:
        self.gp = (G(0), A(0))
    def literal(self, a: A) -> None:
        t = (G(7), a)
        self.gp = t
""")
    assert [w for w in _warnings(_entry(modules).analyzer) if "cop" in w] == [
        "copies A into field (tuple element 1); use copy() to make this "
        f"explicit ('t' is bound to existing storage at line "
        f"{PRELUDE.count(chr(10)) + 11})"]


@pytest.mark.parametrize("slot, expected", [
    ("tuple[A, int32]", set()),
    ("tuple[Own[A], A]", {(0,)}),
    ("Own[tuple[A, tuple[A, int32]]]", {(0,), (1, 0)}),
    ("tuple[tuple[Own[A], int32], int32]", {(0, 0)}),
    ("tuple[Own[tuple[A, int32]], A]", {(0, 0)}),
])
def test_owned_slot_paths(slot: str, expected: set[tuple[int, ...]]) -> None:
    _compiler, modules = _compile(PRELUDE + f"def f(p: {slot}) -> None:\n"
                                  "    pass\n")
    entry = _entry(modules)
    f = next(fn for fn in entry.ast.functions if fn.name == "f")
    assert owned_slot_paths(f.params[0][1]) == expected


def test_generic_element_call_hedges_per_leaf() -> None:
    # An open `T` at an `Own` element slot is decided per leaf: the call's
    # copy is hedged for element 0 and declared as such.
    records, warnings = _records("""\
def gmk[T](a: T) -> tuple[T, int32]:
    return (a, 1)
def gtake[T](p: tuple[Own[T], int32]) -> int32:
    return 0
def run[T](a: T) -> int32:
    return gtake(gmk(a))
""")
    assert records == {6: frozenset({0})}
    assert ("may copy T into owned storage (tuple element 0) if not a value "
            "type; use copy() to make this explicit") in warnings


# -- the per-leaf record (`source_records`) -----------------------------------

LEAF_PRELUDE = """\
from tpy import int32, Own, copy, nocopy, Fn
from typing import Callable
class A:
    def __init__(self, v: int32) -> None:
        self.v = v
class B:
    def __init__(self, w: int32) -> None:
        self.w = w
@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v
class U:
    t: tuple[A | B, int32]
    def __init__(self, t: Own[tuple[A | B, int32]]) -> None:
        self.t = t
def mk[T](x: T) -> tuple[T, int32]:
    return (x, 1)
def mk2[T](x: T) -> tuple[tuple[T, int32], int32]:
    raise ValueError("signature only: the nested literal does not lower")
def pair(a: A, b: A) -> tuple[A, A]:
    return (a, b)
def opair(a: A) -> tuple[Own[A], A]:
    return (A(a.v), a)
def take(t: tuple[Own[A], int32]) -> int32:
    return t[0].v
"""
_LEAF_LINES = LEAF_PRELUDE.count("\n")


def _leaf_records(source: str) -> tuple[
        dict[tuple[int, str], SourceRecord], list[str], SemanticContext]:
    """Every tuple-source record of `source` (after the leaf prelude) by
    (line, source node kind), with the warnings and the context."""
    _compiler, modules = _compile(LEAF_PRELUDE + source)
    return _records_of(_entry(modules).analyzer.ctx)


def _copy_arguments(ctx: SemanticContext) -> IdentityMap:
    """The argument of every `copy()` a record covers: its record is the
    explicit copy the call's own record already states, not a sink's."""
    out = IdentityMap()
    for e in [*ctx.source_records.keys(), *ctx.member_sources.keys()]:
        if (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
                and e.func.name == "copy" and len(e.args) == 1):
            out[peel_value_wrappers(e.args[0])] = True
    return out


def _records_of(ctx: SemanticContext) -> tuple[
        dict[tuple[int, str], SourceRecord], list[str], SemanticContext]:
    out = {}
    arguments = _copy_arguments(ctx)
    for e, rec in ctx.source_records.items():
        if e in arguments:
            continue
        line = e.loc.line - _LEAF_LINES if e.loc is not None else None
        if line is not None and line > 0:
            out[(line, type(e).__name__)] = rec
    warnings = [d.message for d in ctx.diagnostics
                if d.level.value == "warning" and d.loc is not None
                and d.loc.line > _LEAF_LINES]
    return out, warnings, ctx


def _perms(rec: SourceRecord) -> dict[tuple[int, ...], LeafPermission]:
    return {p: leaf.permission for p, leaf in rec.leaves.items()}


P = LeafPermission


def test_generic_results_hedge_per_leaf() -> None:
    records, warnings, _ctx = _leaf_records("""\
def r_call[T](x: T) -> Own[tuple[T, int32]]:
    return mk(x)
def r_select[T](x: T, y: T, c: bool) -> Own[tuple[T, int32]]:
    return mk(x) if c else mk(y)
def r_nested[T](x: T) -> Own[tuple[tuple[T, int32], int32]]:
    return mk2(x)
""")
    assert _perms(records[(2, "TpyCall")]) == {(0,): P.HEDGED}
    sel = records[(4, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.HEDGED}
    assert _perms(records[(6, "TpyCall")]) == {(0, 0): P.HEDGED}
    # One hedge per leaf, none for the whole tuple.
    assert warnings == [
        "may copy T into owned storage (tuple element 0) if not a value "
        "type; use copy() to make this explicit"] * 2 + [
        "may copy T into owned storage (tuple element 0.0) if not a value "
        "type; use copy() to make this explicit"]


def test_union_leaf_copies_like_its_scalar_twin() -> None:
    records, warnings, ctx = _leaf_records("""\
def r_select(x: tuple[A | B, int32], y: tuple[A | B, int32],
             c: bool) -> Own[tuple[A | B, int32]]:
    return x if c else y
def r_field(u: U) -> Own[tuple[A | B, int32]]:
    return u.t
""")
    sel = records[(3, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.WARNED}
    assert _perms(records[(5, "TpyFieldAccess")]) == {(0,): P.WARNED}
    assert warnings.count("copies A | B into owned storage (tuple element "
                          "0); use copy() to make this explicit") == 2
    # The prelude ctor's `self.t = t` moves its `Own` parameter in.
    assert not [d for d in ctx.diagnostics if "into field" in d.message]


def test_tuple_name_union_leaf_warns_as_its_plain_twin() -> None:
    records, warnings, _ctx = _leaf_records("""\
def f(t: tuple[A | B, int32]) -> int32:
    xs: list[tuple[A | B, int32]] = [t]
    return t[1]
class W:
    t: tuple[A | B, int32]
    def __init__(self, t: tuple[A | B, int32]) -> None:
        self.t = (A(0), 0)
    def store(self, t: tuple[A | B, int32]) -> None:
        self.t = t
""")
    # The list literal and the field store each copy the union leaf, as
    # they copy a plain `A` one.
    assert sorted(warnings) == [
        "copies A | B into field (tuple element 0); use copy() to make this "
        "explicit",
        "copies A | B into owned storage (tuple element 0); use copy() to "
        "make this explicit"]
    assert _perms(records[(2, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(9, "TpyName")]) == {(0,): P.WARNED}


def _line_warnings(ctx: SemanticContext) -> list[tuple[int, str]]:
    """The copy warnings after the leaf prelude, by line, their remedy
    dropped."""
    return sorted((d.loc.line - _LEAF_LINES, d.message.split(";")[0])
                  for d in ctx.diagnostics
                  if d.level.value == "warning" and d.loc is not None
                  and d.loc.line > _LEAF_LINES and "cop" in d.message)


def test_tuple_name_union_leaf_warns_at_every_owning_position() -> None:
    # A tuple name's union leaf copied into owned storage at each position
    # warns as its plain-`A` twin does (most of these bodies do not lower
    # yet; the warning is sema's).
    _records, _w, ctx = _leaf_records("""\
class H:
    p: tuple[A | B, int32]
    def __init__(self, p: tuple[A | B, int32]) -> None:
        self.p = p
def own_arg(p: Own[tuple[A | B, int32]]) -> int32:
    return 7
def app(t: tuple[A | B, int32]) -> None:
    xs: list[tuple[A | B, int32]] = []
    xs.append(t)
def fld(h: H, t: tuple[A | B, int32]) -> None:
    h.p = t
def arg(t: tuple[A | B, int32]) -> None:
    own_arg(t)
def comp(ts: list[tuple[A | B, int32]]) -> None:
    ys = [t for t in ts]
    print(len(ys))
def nested(t: tuple[tuple[A | B, int32], int32]) -> None:
    zs: list[tuple[tuple[A | B, int32], int32]] = [t]
def opt(t: tuple[A | B | None, int32]) -> None:
    os: list[tuple[A | B | None, int32]] = [t]
def closure(t: tuple[A | B, int32]) -> None:
    def inner() -> None:
        ws: list[tuple[A | B, int32]] = [t]
    inner()
""")
    assert _line_warnings(ctx) == [
        (4, "copies A | B into field (tuple element 0)"),
        (9, "copies A | B into owned storage (tuple element 0)"),
        (11, "copies A | B into field (tuple element 0)"),
        (13, "copies A | B into owned storage (tuple element 0)"),
        (15, "copies A | B into owned storage (tuple element 0)"),
        (18, "copies A | B into owned storage (tuple element 0.0)"),
        (20, "copies None | A | B into owned storage (tuple element 0)"),
        (23, "copies A | B into owned storage (tuple element 0)")]


def test_yielded_tuple_name_union_leaf_warns() -> None:
    _records, _w, ctx = _leaf_records("""\
from typing import Iterator
def gen(t: tuple[A | B, int32]) -> Iterator[Own[tuple[A | B, int32]]]:
    yield t
""")
    assert _line_warnings(ctx) == [
        (3, "copies A | B into owned storage (tuple element 0)")]


def test_tuple_name_open_union_leaf_hedges_per_leaf() -> None:
    # A leaf whose copy an instantiation decides takes the hedge, as its
    # scalar twin does; a reference head (`list[T]`) copies whatever `T`
    # is, and a `T: ValueType` alternative copies nothing observable.
    _records, _w, ctx = _leaf_records("""\
from tpy import ValueType
def g[T](t: tuple[A | T, int32]) -> None:
    xs: list[tuple[A | T, int32]] = [t]
def h[T](t: tuple[T | None, int32]) -> None:
    xs: list[tuple[T | None, int32]] = [t]
def k[T](t: tuple[list[T], int32]) -> None:
    xs: list[tuple[list[T], int32]] = [t]
def v[T: ValueType](t: tuple[T | int32, int32]) -> None:
    xs: list[tuple[T | int32, int32]] = [t]
def ug[T](u: A | T) -> None:
    xs: list[A | T] = [u]
""")
    assert _line_warnings(ctx) == [
        (3, "may copy A | T into owned storage (tuple element 0) if not a "
            "value type"),
        (5, "may copy T | None into owned storage (tuple element 0) if not a "
            "value type"),
        (7, "copies list[T] into owned storage (tuple element 0)"),
        (11, "may copy A | T into owned storage if not a value type")]


def test_open_union_leaf_of_a_name_hedges_at_every_owning_sink() -> None:
    # An `A | T` leaf a name copies takes the hedge at a field store, an
    # append and an `Own[tuple]` argument; the ctor's `Own` parameter moves.
    records, _w, ctx = _leaf_records("""\
class H[T]:
    p: tuple[A | T, int32]
    def __init__(self, p: Own[tuple[A | T, int32]]) -> None:
        self.p = p
def own_arg[T](p: Own[tuple[A | T, int32]]) -> int32:
    return 0
def fld[T](h: H[T], t: tuple[A | T, int32]) -> None:
    h.p = t
def app[T](t: tuple[A | T, int32]) -> None:
    xs: list[tuple[A | T, int32]] = []
    xs.append(t)
def arg[T](t: tuple[A | T, int32]) -> None:
    print(own_arg[T](t))
""")
    assert {key: _perms(rec) for key, rec in records.items()} == {
        (4, "TpyName"): {(0,): P.MOVE}, (8, "TpyName"): {(0,): P.HEDGED},
        (10, "TpyArrayLiteral"): {(): P.MOVE},
        (11, "TpyName"): {(0,): P.HEDGED}, (13, "TpyName"): {(0,): P.HEDGED}}
    hedge = "if not a value type"
    assert _line_warnings(ctx) == [
        (8, f"may copy A | T into field (tuple element 0) {hedge}"),
        (11, f"may copy A | T into owned storage (tuple element 0) {hedge}"),
        (13, f"may copy A | T into owned storage (tuple element 0) {hedge}")]


def test_open_union_leaf_of_a_name_hedges_at_a_yield() -> None:
    _records, _w, ctx = _leaf_records("""\
from typing import Iterator
def gen[T](t: tuple[A | T, int32]) -> Iterator[Own[tuple[A | T, int32]]]:
    yield t
""")
    assert _line_warnings(ctx) == [
        (3, "may copy A | T into owned storage (tuple element 0) if not a "
            "value type")]


def test_async_return_of_a_union_leaf_name_warns() -> None:
    _records, _w, ctx = _leaf_records("""\
async def ar(t: tuple[A | B, int32]) -> Own[tuple[A | B, int32]]:
    return t
""")
    assert _line_warnings(ctx) == [
        (2, "copies A | B into owned storage (tuple element 0)")]


def test_union_leaf_name_moves_at_last_use_and_a_global_warns() -> None:
    # An owned union-leaf tuple local at its last use moves in, silently
    # (the body does not lower yet: `decl.tuple_literal_shape`); a module
    # global holds its elements by reference, so its read copies, warned.
    records, _w, ctx = _leaf_records("""\
def mv(a: A) -> None:
    u: A | B = A(1)
    t = (u, 2)
    xs: list[tuple[A | B, int32]] = [t]
    print(len(xs))
g: tuple[A | B, int32] = (A(1), 2)
def rg() -> None:
    ys: list[tuple[A | B, int32]] = [g]
    print(len(ys))
""")
    assert _perms(records[(4, "TpyName")]) == {(0,): P.MOVE}
    assert _perms(records[(8, "TpyName")]) == {(0,): P.WARNED}
    assert _line_warnings(ctx) == [
        (8, "copies A | B into owned storage (tuple element 0)")]


def test_value_bound_union_payload_is_a_value_leaf() -> None:
    # `T | int32` under `T: ValueType` copies nothing observable: no
    # diagnostic, and its record leaf is VALUE.
    records, _w, ctx = _leaf_records("""\
from tpy import ValueType
def vb[T: ValueType](t: tuple[T | int32, int32]) -> None:
    xs: list[tuple[T | int32, int32]] = [t]
""")
    assert _perms(records[(3, "TpyName")]) == {(0,): P.VALUE}
    assert _line_warnings(ctx) == []


@pytest.mark.parametrize("body", [
    """\
def own_arg[T](p: Own[tuple[N | T, int32]]) -> int32:
    return 0
def arg[T](t: tuple[N | T, int32]) -> None:
    print(own_arg[T](t))
""",
    """\
def ret(t: tuple[A | N, int32]) -> Own[tuple[A | N, int32]]:
    return t
""",
    """\
def ret(t: tuple[N | A, int32]) -> tuple[Own[N | A], int32]:
    return t
""",
    """\
def ret[T](t: tuple[N | T, int32]) -> tuple[Own[N | T], int32]:
    return t
""",
    """\
def ret(t: tuple[A | N, int32]) -> tuple[Own[A | N], int32]:
    return t
"""], ids=["open_own_arg", "own_tuple_return", "own_element_return",
           "open_own_element_return", "own_element_return_a_n"])
def test_union_leaf_name_with_non_copyable_alternative_errors(
        body: str) -> None:
    # A non-copyable alternative leaves the copy nothing to make, at an
    # `Own[tuple]` argument (an open alternative included) and at both
    # return shapes.
    with pytest.raises(SemanticError, match=r"cannot copy non-copyable type "
                       r"'(N \| T|A \| N)' into owned storage \(tuple "
                       r"element 0\)"):
        _compile(LEAF_PRELUDE + body)


def test_captured_tuple_name_return_hedges_an_open_payload() -> None:
    # A returned name a closure captures is copied whole; an open payload's
    # copy is hedged as at every other owning sink, the concrete one warned.
    _records, _w, ctx = _leaf_records("""\
def g[T](y: T) -> Own[tuple[T, int32]]:
    t = (y, 1)
    def f() -> int32:
        return t[1]
    print(f())
    return t
def c(y: A) -> Own[tuple[A, int32]]:
    t = (y, 1)
    def f() -> int32:
        return t[1]
    print(f())
    return t
""")
    assert _copy_warnings(ctx) == [
        (6, "may copy tuple[T, int32] into owned storage if not a value "
         "type"),
        (12, "copies tuple[A, int32] into owned storage")]


def test_returned_tuple_name_takes_one_diagnostic_per_copied_leaf() -> None:
    # A tuple name returned into an owning result is checked per leaf, a
    # generic and a union leaf as its monomorphic twin: one hedge (or
    # warning) per copied leaf, and no whole-tuple hedge beside it.
    _records, _w, ctx = _leaf_records("""\
from tpy import ValueType
def n_elem[T](t: tuple[tuple[T, int32], int32]) -> tuple[Own[tuple[T, int32]], int32]:
    return t
def n_inner[T](t: tuple[tuple[T, int32], int32]) -> tuple[tuple[Own[T], int32], int32]:
    return t
def n_whole[T](t: tuple[tuple[T, int32], int32]) -> Own[tuple[tuple[T, int32], int32]]:
    return t
def own_own[T](t: tuple[T, int32]) -> Own[tuple[Own[T], int32]]:
    return t
def whole[T](t: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return t
def elem[T](t: tuple[T, int32]) -> tuple[Own[T], int32]:
    return t
def u_elem[T](t: tuple[tuple[A | T, int32], int32]) -> tuple[Own[tuple[A | T, int32]], int32]:
    return t
def u_inner[T](t: tuple[tuple[A | T, int32], int32]) -> tuple[tuple[Own[A | T], int32], int32]:
    return t
def u_whole[T](t: tuple[A | T, int32]) -> Own[tuple[A | T, int32]]:
    return t
def u_own_own[T](t: tuple[A | T, int32]) -> Own[tuple[Own[A | T], int32]]:
    return t
def vb[T: ValueType](t: tuple[T | int32, int32]) -> tuple[Own[T | int32], int32]:
    return t
def c_elem(t: tuple[tuple[A, int32], int32]) -> tuple[Own[tuple[A, int32]], int32]:
    return t
def c_inner(t: tuple[tuple[A, int32], int32]) -> tuple[tuple[Own[A], int32], int32]:
    return t
def c_whole(t: tuple[tuple[A, int32], int32]) -> Own[tuple[tuple[A, int32], int32]]:
    return t
def c_own_own(t: tuple[A, int32]) -> Own[tuple[Own[A], int32]]:
    return t
def wal[T](t: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return (y := t)
def n_wal[T](t: tuple[tuple[T, int32], int32]) -> Own[tuple[tuple[T, int32], int32]]:
    return (y := t)
def alias[T](t: tuple[tuple[T, int32], int32]) -> tuple[Own[tuple[T, int32]], int32]:
    u = t
    return u
def own_arg[T](p: Own[tuple[T, int32]]) -> int32:
    return 0
def arg[T](t: tuple[T, int32]) -> None:
    print(own_arg[T](t))
""")
    h = "if not a value type"
    t_0, t_00 = (f"may copy T into owned storage (tuple element {p}) {h}"
                 for p in ("0", "0.0"))
    u_0, u_00 = (f"may copy A | T into owned storage (tuple element {p}) {h}"
                 for p in ("0", "0.0"))
    a_0, a_00 = (f"copies A into owned storage (tuple element {p})"
                 for p in ("0", "0.0"))
    assert _line_warnings(ctx) == [
        (3, t_00), (5, t_00), (7, t_00), (9, t_0), (11, t_0), (13, t_0),
        (15, u_00), (17, u_00), (19, u_0), (21, u_0),
        (25, a_00), (27, a_00), (29, a_00), (31, a_0),
        # A walrus is not the name's per-leaf read: its return keeps the
        # one whole-tuple hedge.
        (33, f"may copy tuple[T, int32] into owned storage {h}"),
        (35, f"may copy tuple[tuple[T, int32], int32] into owned storage "
             f"{h}"),
        (38, t_00), (42, t_0)]


def test_tuple_name_union_leaf_explicit_and_value_copies_stay_silent(
        ) -> None:
    # `copy()` declares the copy, and a value union copies no object.
    _records, _w, ctx = _leaf_records("""\
def explicit(t: tuple[A | B, int32]) -> None:
    xs: list[tuple[A | B, int32]] = [copy(t)]
def value_union(t: tuple[int32 | str, int32]) -> None:
    xs: list[tuple[int32 | str, int32]] = [t]
""")
    assert _line_warnings(ctx) == []


def test_tuple_name_union_leaf_with_open_non_copyable_alternative_errors(
        ) -> None:
    # A non-copyable alternative leaves no copy to make, whatever the open
    # one turns out to be: the error, not the hedge.
    with pytest.raises(SemanticError, match=r"cannot copy non-copyable type "
                       r"'N \| T' into owned storage \(tuple element 0\)"):
        _compile(LEAF_PRELUDE + """\
def f[T](t: tuple[N | T, int32]) -> None:
    xs: list[tuple[N | T, int32]] = [t]
""")


def test_union_leaf_with_non_copyable_alternative_is_rejected() -> None:
    with pytest.raises(SemanticError, match=r"cannot copy non-copyable type "
                       r"'(N \| B|B \| N)' into owned storage \(tuple "
                       r"element 0\)"):
        _compile(LEAF_PRELUDE + """\
def r(x: tuple[N | B, int32], y: tuple[N | B, int32],
      c: bool) -> Own[tuple[N | B, int32]]:
    return x if c else y
""")


def test_select_of_fresh_or_copied_arms_copies_nothing_implicitly() -> None:
    records, warnings, ctx = _leaf_records("""\
def fresh(c: bool) -> Own[tuple[A, int32]]:
    return (A(1), 2) if c else (A(2), 3)
def copied(x: A, y: A, c: bool) -> Own[tuple[A, int32]]:
    return (copy(x), 1) if c else (copy(y), 2)
def mixed_arms(a: A, b: A, c: bool) -> Own[tuple[A, A]]:
    return opair(a) if c else opair(b)
""")
    fresh = records[(2, "TpyIfExpr")]
    assert _perms(fresh) == {(0,): P.MOVE}
    explicit = records[(4, "TpyIfExpr")]
    assert _perms(explicit) == {(0,): P.EXPLICIT}
    # The negative control: each arm lends its element 1.
    assert _perms(records[(6, "TpyIfExpr")]) == {(0,): P.MOVE,
                                                 (1,): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 1); "
                        "use copy() to make this explicit"]
    # The declared copies agree: nothing for the fresh and explicit
    # selects, element 1 for the control.
    assert _copied_by_line(ctx, _LEAF_LINES) == {6: frozenset({1})}


def test_walrus_is_its_target_names_read() -> None:
    # The literal is captured into `t`, which stays live: its leaf copies,
    # with the argument coercion's warning, never a move.
    records, warnings, ctx = _leaf_records("""\
def f() -> int32:
    r = take((t := (A(2), 1)))
    t[0].v = 5
    return r + t[0].v
def own(x: Own[tuple[A, int32]]) -> int32:
    return x[1]
def g() -> int32:
    r = own((t := (A(2), 1)))
    return r + t[0].v
""")
    assert _perms(records[(2, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(8, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert warnings == [
        "copies tuple[A, int32] into owned storage; use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit"]
    assert _copied_by_line(ctx, _LEAF_LINES).get(2) == frozenset({0})


def test_mixed_slot_passes_the_borrowed_leaf_it_does_not_own() -> None:
    records, warnings, _ctx = _leaf_records("""\
def relay(a: A, b: A) -> tuple[Own[A], A]:
    return pair(a, b)
""")
    rec = records[(2, "TpyCall")]
    assert {p: (leaf.borrowed, leaf.slot_owns, leaf.permission)
            for p, leaf in rec.leaves.items()} == {
        (0,): (True, True, P.WARNED), (1,): (True, False, P.PASS)}
    assert warnings == ["copies A into owned storage (tuple element 0); "
                        "use copy() to make this explicit"]


def test_captured_tuple_names_copy_every_owned_leaf() -> None:
    records, warnings, _ctx = _leaf_records("""\
def inline() -> int32:
    t = (A(1), 3)
    def inner() -> tuple[Own[A], int32]:
        return t
    return inner()[1]
def param(t: tuple[Own[A], int32]) -> int32:
    def inner() -> tuple[Own[A], int32]:
        return t
    return inner()[1]
def lam() -> int32:
    t = (A(1), 3)
    f: Callable[[], Own[tuple[A, int32]]] = lambda: t
    return f()[1]
""")
    # A declared `Own` element is no ownership of the closure's.
    for line in (4, 8, 12):
        assert _perms(records[(line, "TpyName")]) == {(0,): P.WARNED}
    assert warnings.count("copies A into owned storage (tuple element 0); "
                          "use copy() to make this explicit") == 3


def test_captured_non_copyable_leaf_is_rejected_without_move_advice() -> None:
    with pytest.raises(SemanticError) as e:
        _compile(LEAF_PRELUDE + """\
def f() -> int32:
    t = (N(1), 3)
    def inner() -> Own[tuple[N, int32]]:
        return t
    return inner()[1]
""")
    msg = e.value.message
    assert msg.startswith("cannot copy non-copyable type 'N' into owned "
                          "storage (tuple element 0)")
    assert "auto-move" not in msg and "copy()" not in msg


def test_nested_name_leaf_is_told_and_lending_call_leaf_hedged() -> None:
    records, _warnings_, _ctx = _leaf_records("""\
def nested(t: tuple[tuple[A, int32], int32]) -> Own[tuple[tuple[A, int32], int32]]:
    return t
def lending[T](a: tuple[T, int32], b: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return min(a, b, key=lambda p: p[1])
""")
    # A borrowed parameter's nested leaf copies, and the return's warning
    # tells it at its path.
    assert _perms(records[(2, "TpyName")]) == {(0, 0): P.WARNED}
    # A lending call's open `T` leaf takes the scalar twin's hedge.
    assert _perms(records[(4, "TpyCall")]) == {(0,): P.HEDGED}


LENDING_DECLS = """\
from typing import Iterator
from tpy import NativeIterable, pure, readonly
from tpy.extern import native
@native(borrows=("a", "b"))
def upick[T](a: tuple[T, int32], b: tuple[T, int32]) -> tuple[T, int32]: ...
@native(element_of=("items",))
def ufirst[T](items: list[tuple[T, int32]]) -> tuple[T, int32]: ...
@native("::Bag", elements=True)
class Bag[T](NativeIterable[T]):
    def __init__(self) -> None: ...
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...
    @native("first", element_of=("self",))
    @readonly
    def first(self) -> T: ...
class HT[T]:
    p: tuple[T, int32]
    def __init__(self, p: Own[tuple[T, int32]]) -> None:
        self.p = p
def gtake[T](p: Own[tuple[T, int32]]) -> int32:
    return 0
"""
_LENDING_LINES = LENDING_DECLS.count("\n")

_HEDGE_0 = ("may copy T into owned storage (tuple element 0) if not a value "
            "type; use copy() to make this explicit")


@pytest.mark.parametrize("call, params", [
    ("min(a, b, key=lambda p: p[1])",
     "a: tuple[T, int32], b: tuple[T, int32]"),
    ('d.get("k", a)', "d: dict[str, tuple[T, int32]], a: tuple[T, int32]"),
    ("upick(a, b)", "a: tuple[T, int32], b: tuple[T, int32]"),
    ("ufirst(items)", "items: list[tuple[T, int32]]"),
    ("bag.first()", "bag: Bag[tuple[T, int32]]"),
])
def test_stamped_generic_lending_call_hedges_each_leaf(call: str,
                                                       params: str) -> None:
    # A call with a lend contract (`borrows=` / `element_of=`, a builtin's
    # or a user binding's) hands its open `T` leaf to every owning sink as
    # its scalar twin does: hedged per leaf, never undecided.
    arg = ("" if call.startswith("min(") else
           f"def arg[T]({params}) -> int32:\n    return gtake({call})\n")
    records, warnings, _ctx = _leaf_records(LENDING_DECLS + f"""\
def app[T]({params}, xs: list[tuple[T, int32]]) -> None:
    xs.append({call})
def fld[T]({params}, h: HT[T]) -> None:
    h.p = {call}
def ret[T]({params}) -> Own[tuple[T, int32]]:
    return {call}
def lit[T]({params}, xs: list[tuple[tuple[T, int32], int32]]) -> None:
    xs.append(({call}, 1))
def gen[T]({params}) -> Iterator[Own[tuple[T, int32]]]:
    yield {call}
def comp[T]({params}) -> Own[list[tuple[T, int32]]]:
    return [{call} for i in range(2)]
""" + arg)
    base = _LENDING_LINES
    kind = "TpyMethodCall" if call.startswith(("d.", "bag.")) else "TpyCall"
    assert _perms(records[(base + 2, kind)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 4, kind)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 6, kind)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 8, "TpyTupleLiteral")]) == {
        (0, 0): P.HEDGED}
    # A yield and a comprehension element are owning sinks too.
    assert _perms(records[(base + 10, kind)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 12, kind)]) == {(0,): P.HEDGED}
    if arg:
        assert _perms(records[(base + 14, kind)]) == {(0,): P.HEDGED}
    hedges = [w for w in warnings if w.startswith("may copy")]
    assert hedges == [
        _HEDGE_0,
        _HEDGE_0.replace("owned storage", "field"),
        _HEDGE_0,
        _HEDGE_0.replace("element 0)", "element 0.0)"),
        _HEDGE_0,
        _HEDGE_0,
    ] + ([_HEDGE_0] if arg else [])


def test_stamped_lending_call_nested_leaf_hedges_at_its_path() -> None:
    records, warnings, _ctx = _leaf_records("""\
def nest[T](a: tuple[tuple[T, int32], int32],
            b: tuple[tuple[T, int32], int32],
            xs: list[tuple[tuple[T, int32], int32]]) -> Own[tuple[tuple[T, int32], int32]]:
    xs.append(min(a, b, key=lambda p: p[1]))
    return min(a, b, key=lambda p: p[1])
def fresh[T](x: T, y: T) -> Own[tuple[T, int32]]:
    return min(mk(x), mk(y), key=lambda p: p[1])
""")
    assert _perms(records[(4, "TpyCall")]) == {(0, 0): P.HEDGED}
    assert _perms(records[(5, "TpyCall")]) == {(0, 0): P.HEDGED}
    # The operands lend `x` / `y` through `mk`'s analyzed bodies.
    assert _perms(records[(7, "TpyCall")]) == {(0,): P.HEDGED}
    assert warnings == [_HEDGE_0.replace("element 0)", "element 0.0)")] * 2 + [
        _HEDGE_0]


def test_uncontracted_iterator_step_takes_the_per_leaf_rule() -> None:
    # One-argument `next(it)` declares no lend contract: its declared return
    # says what it lends, and each leaf takes the per-leaf rule -- a concrete
    # reference leaf is warned, an open `T` leaf hedged.
    records, warnings, _ctx = _leaf_records("""\
from typing import Iterator
def capp(it: Iterator[tuple[A, int32]], xs: list[tuple[A, int32]]) -> None:
    try:
        xs.append(next(it))
    except StopIteration:
        pass
def gapp[T](it: Iterator[tuple[T, int32]], xs: list[tuple[T, int32]]) -> None:
    try:
        xs.append(next(it))
    except StopIteration:
        pass
def gret[T](it: Iterator[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    try:
        return next(it)
    except StopIteration:
        raise ValueError("empty")
def vapp(it: Iterator[tuple[int32, int32]], xs: list[tuple[int32, int32]]) -> None:
    try:
        xs.append(next(it))
    except StopIteration:
        pass
""")
    assert _perms(records[(4, "TpyCall")]) == {(0,): P.WARNED}
    assert _perms(records[(9, "TpyCall")]) == {(0,): P.HEDGED}
    assert _perms(records[(14, "TpyCall")]) == {(0,): P.HEDGED}
    copies = [w for w in warnings if "cop" in w]
    assert copies == [
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit", _HEDGE_0, _HEDGE_0]


_COPIES_A_0 = ("copies A into owned storage (tuple element 0); use copy() to "
               "make this explicit")


def test_uncontracted_next_method_and_native_take_the_per_leaf_rule() -> None:
    # `it.__next__()` and an `@native` declaring no lend contract lend per
    # their declared return, each leaf decided by the per-leaf rule.
    records, warnings, _ctx = _leaf_records(LENDING_DECLS + """\
@native
def cnat(a: A) -> tuple[A, int32]: ...
@native
def gnat[T](a: T) -> tuple[T, int32]: ...
def nxt(it: Iterator[tuple[A, int32]], xs: list[tuple[A, int32]]) -> None:
    try:
        xs.append(it.__next__())
    except StopIteration:
        pass
def gnxt[T](it: Iterator[tuple[T, int32]], xs: list[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    try:
        xs.append(it.__next__())
        return it.__next__()
    except StopIteration:
        raise ValueError("empty")
def unat(a: A, xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    xs.append(cnat(a))
    return cnat(a)
def gunat[T](a: T, xs: list[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    xs.append(gnat(a))
    return gnat(a)
""")
    base = _LENDING_LINES
    assert _perms(records[(base + 7, "TpyMethodCall")]) == {(0,): P.WARNED}
    assert _perms(records[(base + 12, "TpyMethodCall")]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 13, "TpyMethodCall")]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 17, "TpyCall")]) == {(0,): P.WARNED}
    assert _perms(records[(base + 18, "TpyCall")]) == {(0,): P.WARNED}
    assert _perms(records[(base + 20, "TpyCall")]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 21, "TpyCall")]) == {(0,): P.HEDGED}
    assert [w for w in warnings if "cop" in w] == [
        _COPIES_A_0, _HEDGE_0, _HEDGE_0, _COPIES_A_0, _COPIES_A_0,
        _HEDGE_0, _HEDGE_0]


def test_stamped_call_of_fresh_operands_moves() -> None:
    # A stamped call whose lent operands are fresh owners copies nothing an
    # owning sink could tell (`returns_borrow` False): its leaves MOVE, and
    # so do those of an element read off it (`call(...)[0]`); a borrowed
    # operand's projection still hedges.
    records, warnings, _ctx = _leaf_records(LENDING_DECLS + """\
@native
def own_a[T](x: T) -> Own[tuple[T, int32]]: ...
@native
def fresh2[T](x: T) -> Own[tuple[tuple[T, int32], int32]]: ...
@native(borrows=("a", "b"))
def choose[T](a: tuple[tuple[T, int32], int32], b: tuple[tuple[T, int32], int32]) -> tuple[tuple[T, int32], int32]: ...
def fresh_ops[T](x: T, y: T, xs: list[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    xs.append(min(own_a(x), own_a(y), key=lambda p: p[1]))
    return min(own_a(x), own_a(y), key=lambda p: p[1])
def direct[T](x: T, y: T) -> Own[tuple[tuple[T, int32], int32]]:
    return choose(fresh2(x), fresh2(y))
def projected[T](x: T, y: T, xs: list[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    xs.append(choose(fresh2(x), fresh2(y))[0])
    return choose(fresh2(x), fresh2(y))[0]
def held[T](x: T, y: T) -> int32:
    t = choose(fresh2(x), fresh2(y))[0]
    return t[1]
def pborrowed[T](a: tuple[tuple[T, int32], int32], b: tuple[tuple[T, int32], int32]) -> Own[tuple[T, int32]]:
    return choose(a, b)[0]
""")
    base = _LENDING_LINES
    assert _perms(records[(base + 8, "TpyCall")]) == {(0,): P.MOVE}
    assert _perms(records[(base + 9, "TpyCall")]) == {(0,): P.MOVE}
    assert _perms(records[(base + 11, "TpyCall")]) == {(0, 0): P.MOVE}
    assert _perms(records[(base + 13, "TpySubscript")]) == {(0,): P.MOVE}
    assert _perms(records[(base + 14, "TpySubscript")]) == {(0,): P.MOVE}
    assert _perms(records[(base + 19, "TpySubscript")]) == {(0,): P.HEDGED}
    # The local holds a copy nothing else reaches (`value_call_copy_message`
    # reads the same stamp through the projection): silent.
    assert [w for w in warnings if "cop" in w] == [_HEDGE_0]


def test_nested_stamped_call_operand_hedges_at_the_arguments() -> None:
    # A stamped call's COPY result passed on as a tuple argument takes its
    # scalar twin's argument check per leaf; the outer call's sink copies
    # nothing more, as the scalar's does not.
    records, warnings, _ctx = _leaf_records("""\
def nestret[T](a: tuple[T, int32], b: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return min(max(a, b, key=lambda p: p[1]), max(b, a, key=lambda p: p[1]), key=lambda p: p[1])
def scal[T](a: T, b: T) -> Own[T]:
    return min(max(a, b, key=lambda p: 0), max(b, a, key=lambda p: 0), key=lambda p: 0)
""")
    assert _perms(records[(2, "TpyCall")]) == {(0,): P.MOVE}
    assert _perms(records[(4, "TpyCall")]) == {(): P.MOVE}
    arg = ("may copy T into argument '{}' of 'min(...)'{} if not a value "
           "type; use copy() to make this explicit")
    assert warnings == [arg.format("arg1", " (tuple element 0)"),
                        arg.format("arg2", " (tuple element 0)"),
                        arg.format("arg1", ""), arg.format("arg2", "")]


def test_projections_of_calls_take_the_calls_incoming_answer() -> None:
    # A read off a call's result -- an element of an uncontracted call, a
    # field of a record result, an element of a select of stamped calls --
    # lends what the call lends at the read's component.
    records, warnings, _ctx = _leaf_records(LENDING_DECLS + """\
class R:
    pair: tuple[A, int32]
    def __init__(self, a: Own[A]) -> None:
        self.pair = (a, 1)
@native
def make(a: A) -> Own[tuple[tuple[A, int32], int32]]: ...
@native
def peek(a: A) -> tuple[tuple[A, int32], int32]: ...
@native
def gmake[T](a: T) -> Own[tuple[tuple[T, int32], int32]]: ...
@native
def gpeek[T](a: T) -> tuple[tuple[T, int32], int32]: ...
@native
def mkr(a: A) -> Own[R]: ...
@native
def lookr(a: A) -> R: ...
@native
def mkr2(a: A) -> Own[tuple[R, int32]]: ...
@native
def fresh2[T](x: T) -> Own[tuple[tuple[T, int32], int32]]: ...
@native(borrows=("a", "b"))
def choose[T](a: tuple[tuple[T, int32], int32], b: tuple[tuple[T, int32], int32]) -> tuple[tuple[T, int32], int32]: ...
def app(a: A, xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    xs.append(make(a)[0])
    xs.append(peek(a)[0])
    return make(a)[0]
def ret_peek(a: A) -> Own[tuple[A, int32]]:
    return peek(a)[0]
def gapp[T](a: T, xs: list[tuple[T, int32]]) -> Own[tuple[T, int32]]:
    xs.append(gmake(a)[0])
    xs.append(gpeek(a)[0])
    return gmake(a)[0]
def gret_peek[T](a: T) -> Own[tuple[T, int32]]:
    return gpeek(a)[0]
def fld(a: A, xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    xs.append(mkr(a).pair)
    xs.append(lookr(a).pair)
    return mkr2(a)[0].pair
def sel[T](x: T, y: T, c: bool) -> Own[tuple[T, int32]]:
    return (choose(fresh2(x), fresh2(y)) if c else choose(fresh2(y), fresh2(x)))[0]
def sel_lent[T](a: tuple[tuple[T, int32], int32], x: T, c: bool) -> Own[tuple[T, int32]]:
    return (choose(fresh2(x), fresh2(x)) if c else choose(a, a))[0]
""")
    base = _LENDING_LINES
    sub, fa = "TpySubscript", "TpyFieldAccess"
    assert _perms(records[(base + 24, sub)]) == {(0,): P.MOVE}
    assert _perms(records[(base + 25, sub)]) == {(0,): P.WARNED}
    assert _perms(records[(base + 26, sub)]) == {(0,): P.MOVE}
    assert _perms(records[(base + 28, sub)]) == {(0,): P.WARNED}
    assert _perms(records[(base + 30, sub)]) == {(0,): P.MOVE}
    assert _perms(records[(base + 31, sub)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 32, sub)]) == {(0,): P.MOVE}
    assert _perms(records[(base + 34, sub)]) == {(0,): P.HEDGED}
    assert _perms(records[(base + 36, fa)]) == {(0,): P.MOVE}
    assert _perms(records[(base + 37, fa)]) == {(0,): P.WARNED}
    assert _perms(records[(base + 38, fa)]) == {(0,): P.MOVE}
    # Both arms' calls have fresh operands: nothing an owner could tell.
    assert _perms(records[(base + 40, sub)]) == {(0,): P.MOVE}
    # One arm lends a parameter: the read hedges, as that arm alone would.
    assert _perms(records[(base + 42, sub)]) == {(0,): P.HEDGED}
    assert [w for w in warnings if "cop" in w] == [
        _COPIES_A_0, _COPIES_A_0, _HEDGE_0, _HEDGE_0, _COPIES_A_0,
        _HEDGE_0]


def test_value_tuple_through_a_stamped_call_is_silent() -> None:
    records, warnings, _ctx = _leaf_records("""\
def vals(a: tuple[int32, int32], b: tuple[int32, int32], xs: list[tuple[int32, int32]]) -> Own[tuple[int32, int32]]:
    xs.append(min(a, b, key=lambda p: p[1]))
    return min(a, b, key=lambda p: p[1])
""")
    # No reference leaf: nothing to record, nothing to tell.
    assert (2, "TpyCall") not in records and (3, "TpyCall") not in records
    assert warnings == []


def test_nested_yield_member_of_stamped_call_is_hedged() -> None:
    # A yield at `Iterator[Own[tuple[...]]]` checks its literal's members as
    # the return does: the lending call's open `T` member leaf is hedged.
    records, _warnings, _ctx = _leaf_records(LENDING_DECLS + """\
def nyield[T](a: tuple[T, int32], b: tuple[T, int32]) -> Iterator[Own[tuple[tuple[T, int32], int32]]]:
    yield (min(a, b, key=lambda p: p[1]), 1)
""")
    line = _LENDING_LINES + 2
    assert _perms(records[(line, "TpyTupleLiteral")]) == {(0, 0): P.HEDGED}


def test_name_record_is_settled_by_a_loop_back_edge() -> None:
    records, warnings, _ctx = _leaf_records("""\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = (A(1), 1)
    i = 0
    while i < n:
        if i == 1:
            return t
        t = (a, 2)
        i += 1
    return (A(0), 0)
def moved() -> tuple[Own[A], int32]:
    t = (A(1), 1)
    return t
""")
    # The later borrowing rebind turns the return's move into a copy.
    assert _perms(records[(6, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(12, "TpyName")]) == {(0,): P.MOVE}


def test_literal_member_move_settled_into_a_copy() -> None:
    records, warnings, _ctx = _leaf_records("""\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return (t, 1)
        t = a
        i += 1
    return (A(0), 0)
""")
    # The back edge's borrowing rebind turns the member's move into the
    # copy the settled warning names.
    assert _perms(records[(6, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert records[(6, "TpyTupleLiteral")].leaves[(0,)].borrowed
    assert _perms(records[(9, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    assert len(warnings) == 1 and "bound to existing storage" in warnings[0]


def test_owning_yield_and_lambda_parameter_names_publish_records() -> None:
    records, warnings, _ctx = _leaf_records("""\
from typing import Iterator
def fresh() -> Iterator[tuple[Own[A], int32]]:
    t = (A(3), 1)
    yield t
def live() -> Iterator[tuple[Own[A], int32]]:
    t = (A(3), 1)
    yield t
    print(t[0].v)
def lam() -> int32:
    g: Callable[[tuple[Own[A], int32]], tuple[Own[A], int32]] = lambda p: p
    return g((A(3), 1))[1]
""")
    # A last-use yield moves the element the local owns; one read later
    # copies that element, warned by its position.
    assert _perms(records[(4, "TpyName")]) == {(0,): P.MOVE}
    assert _perms(records[(7, "TpyName")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "copies" in w] == [
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit"]
    # A returned parameter hands over what its declared type owns.
    assert _perms(records[(10, "TpyName")]) == {(0,): P.MOVE}


def test_nested_tuple_members_get_per_leaf_verdicts() -> None:
    records, warnings, _ctx = _leaf_records("""\
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
def fresh() -> tuple[Own[tuple[A, int32]], int32]:
    return ((A(1), 2), 3)
def outer(p: tuple[Own[tuple[A, int32]], int32]) -> int32:
    return p[1]
def arg() -> int32:
    return outer(((A(1), 2), 3))
def mk(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 2)
def call(a: A) -> tuple[tuple[Own[A], int32], int32]:
    return (mk(a), 3)
def field(h: H) -> tuple[tuple[Own[A], int32], int32]:
    return (h.p, 3)
""")
    # An `Own[tuple[...]]` member owns every leaf it holds.
    assert _perms(records[(6, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    assert _perms(records[(10, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    # A nested source other than a name takes the owned tuple sink's
    # decision: a fresh element moves, a field's copies.
    assert _perms(records[(14, "TpyTupleLiteral")]) == {(0, 0): P.MOVE}
    assert _perms(records[(16, "TpyTupleLiteral")]) == {(0, 0): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"]


def test_select_of_explicit_copies_is_explicit() -> None:
    records, warnings, _ctx = _leaf_records("""\
def whole(a: A, b: A, c: bool) -> Own[tuple[A, int32]]:
    x = (a, 1)
    y = (b, 2)
    return copy(x) if c else copy(y)
def nested(a: A, b: A, c: bool, d: bool) -> Own[tuple[A, int32]]:
    return ((copy(a), 1) if c else (copy(b), 2)) if d else (copy(a), 3)
""")
    sel = records[(4, "TpyIfExpr")]
    assert _perms(sel) == {(0,): P.EXPLICIT}
    outer = records[(6, "TpyIfExpr")]
    assert _perms(outer) == {(0,): P.EXPLICIT}
    assert not warnings


def test_select_arms_lend_what_their_members_bring() -> None:
    records, warnings, ctx = _leaf_records("""\
def f(a: A, c: bool) -> Own[tuple[A, int32]]:
    return (a, 1) if c else (A(2), 2)
""")
    sel = next(e for e in ctx.source_records.keys()
               if isinstance(e, TpyIfExpr))
    arms = [peel_value_wrappers(a) for a in (sel.then_expr, sel.else_expr)]
    assert [type(a).__name__ for a in arms] == ["TpyTupleLiteral"] * 2
    # An arm is a plain operand of the select: it keeps no capture of its
    # own, and lends what its members do as they arrive.
    assert [a.elem_capture for a in arms] == [[], []]
    rec = records[(2, "TpyIfExpr")]
    assert _perms(rec) == {(0,): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 0); "
                        "use copy() to make this explicit"]


def _probe(source: str) -> tuple[
        dict[tuple[int, str], SourceRecord], list[str], object]:
    """The records (by line and node kind), the warnings and the entry
    module of `source` after the leaf prelude."""
    _compiler, modules = _compile(LEAF_PRELUDE + source)
    entry = _entry(modules)
    records, warnings, _ctx = _records_of(entry.analyzer.ctx)
    return records, warnings, entry


def _leaves(rec: SourceRecord) -> dict[tuple[int, ...], tuple]:
    return {p: (leaf.borrowed, leaf.slot_owns, leaf.permission)
            for p, leaf in rec.leaves.items()}


def test_value_capture_keeps_a_members_incoming_borrow() -> None:
    # Each arm's nested call lends `a` / `b` at (0, 0), although the arm
    # holds that member by value: the select copies it, warned once.
    records, warnings, entry = _probe("""\
def p1(a: A) -> tuple[A, int32]:
    return (a, 1)
def selected(a: A, b: A, c: bool) -> Own[tuple[tuple[A, int32], int32]]:
    return (p1(a), 1) if c else (p1(b), 2)
def stored(a: A) -> int32:
    t = (p1(a), 1)
    return t[1]
""")
    sel = records[(4, "TpyIfExpr")]
    assert _leaves(sel) == {(0, 0): (True, True, P.WARNED)}
    # The select's copy, and the local's: it stores the nested tuple by
    # value.
    assert warnings == ["copies A into owned storage (tuple element 0.0); "
                        "use copy() to make this explicit"] * 2
    # The two questions of one literal: what arrives borrowed (the sink's)
    # and what a binding of it holds as its own (the binding mask's, which
    # holds the nested tuple by value).
    ctx = entry.analyzer.ctx
    stored = next(f for f in entry.ast.functions if f.name == "stored")
    init = next(st.init for st in stored.body if isinstance(st, TpyVarDecl))
    slot = ctx.get_expr_type(init)
    assert entry.analyzer.compat.tuple_lent_paths(init, slot) == {(0, 0)}
    assert tuple_borrowed_paths(ctx, init, slot) == frozenset()
    assert tuple_source_elem_owned(ctx, init, slot) == (True, False)


def test_owned_tuple_member_decides_each_leaf() -> None:
    # An `Own[tuple[...]]` member: a call's leaves by its declared return,
    # a literal's per member, a NAME's by the name rule at its member slot
    # (the leaf its binding borrows warns, the owned one moves at its last
    # use; BUGS.md#tuple-literal-name-member-silent-copy: the C++ copies).
    records, warnings, _a = _probe("""\
def own_first(a: A) -> tuple[Own[A], A]:
    return (A(1), a)
def call(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (own_first(a), 3)
def name(a: A) -> tuple[Own[tuple[A, A]], int32]:
    t = own_first(a)
    return (t, 3)
def explicit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (copy(own_first(a)), 3)
def lit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return ((a, A(2)), 3)
def whole(a: A) -> Own[tuple[tuple[A, int32], int32]]:
    return ((a, 1), 1)
""")
    assert _leaves(records[(4, "TpyTupleLiteral")]) == {
        (0, 0): (False, True, P.MOVE), (0, 1): (True, True, P.WARNED)}
    # A name moves its own storage in: the leaf is borrowed.
    assert _leaves(records[(7, "TpyTupleLiteral")]) == {
        (0, 0): (True, True, P.MOVE), (0, 1): (True, True, P.WARNED)}
    assert _perms(records[(9, "TpyTupleLiteral")]) == {
        (0, 0): P.EXPLICIT, (0, 1): P.EXPLICIT}
    assert _leaves(records[(11, "TpyTupleLiteral")]) == {
        (0, 0): (True, True, P.WARNED), (0, 1): (False, True, P.MOVE)}
    # A whole `Own[tuple[...]]` slot owns the nested literal's leaf too.
    assert _leaves(records[(13, "TpyTupleLiteral")]) == {
        (0, 0): (True, True, P.WARNED)}
    assert warnings == [
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit ('t' is bound to existing storage at line "
        f"{_LEAF_LINES + 6})"] + [
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit"] * 2


def test_optional_select_arms_keep_their_source_ownership() -> None:
    records, warnings, _a = _probe("""\
def mk() -> Own[A | None]:
    return A(1)
def fresh(c: bool) -> Own[tuple[A | None, int32]]:
    return (mk(), 1) if c else (mk(), 2)
def copied(a: A | None, c: bool) -> Own[tuple[A | None, int32]]:
    return (copy(a), 1) if c else (copy(a), 2)
""")
    fresh = records[(4, "TpyIfExpr")]
    assert _leaves(fresh) == {(0,): (False, True, P.MOVE)}
    copied = records[(6, "TpyIfExpr")]
    assert _perms(copied) == {(0,): P.EXPLICIT}
    assert not warnings


def test_yielded_name_is_settled_by_a_loop_back_edge() -> None:
    records, warnings, entry = _probe("""\
from typing import Iterator
def g(a: A, n: int32) -> Iterator[tuple[Own[A], int32]]:
    t = (A(1), 2)
    i = 0
    while i < n:
        if i == 1:
            yield t
        t = (a, 2)
        i += 1
""")
    # The yield is an owning read the settlement sees: the back edge's
    # borrowing rebind turns its move into the copy it warns.
    rec = records[(7, "TpyName")]
    assert _leaves(rec) == {(0,): (True, True, P.WARNED)}
    ctx = entry.analyzer.ctx
    yielded = next(e for e in ctx.source_records.keys()
                   if isinstance(e, TpyName)
                   and e.loc.line - _LEAF_LINES == 7)
    assert _copied_elems(ctx, yielded) == frozenset({0})
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit ('t' is bound to existing storage at line "
        f"{_LEAF_LINES + 8})"]


def test_walrus_and_lambda_parameter_reads_keep_their_context() -> None:
    records, warnings, _a = _probe("""\
from typing import Iterator
def ret() -> tuple[Own[A], int32]:
    return (t := (A(1), 2))
def gen() -> Iterator[tuple[Own[A], int32]]:
    yield (t := (A(1), 2))
    print(t[1])
def lam() -> int32:
    g: Callable[[tuple[Own[A], int32]], tuple[Own[A], int32]] = lambda p: p
    h: Callable[[tuple[A, int32]], Own[tuple[A, int32]]] = lambda q: q
    return g((A(3), 1))[1]
""")
    # A walrus is its target name's read: the return's record follows the
    # name's rule (no verdict for a read nothing declared; the lowering
    # refuses it, BUGS.md#walrus-tuple-owning-sink-rejects), the live
    # yield's copies the leaf under the whole-tuple warning.
    assert _perms(records[(3, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(5, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    # A lambda parameter hands over what its declared type owns.
    assert _leaves(records[(8, "TpyName")]) == {(0,): (True, True, P.MOVE)}
    # The borrowed parameter's copy is not warned yet
    # (BUGS.md#lambda-own-result-borrowed-param-copies-unwarned).
    assert _perms(records[(9, "TpyName")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "tuple element" in w] == []


def test_generic_captured_return_hedges_once_per_leaf() -> None:
    records, warnings, _a = _probe("""\
def gp[T](t: tuple[T, int32]) -> None:
    def inner() -> Own[tuple[T, int32]]:
        return t
    r = inner()
    print(r[1])
""")
    assert _perms(records[(3, "TpyName")]) == {(0,): P.HEDGED}
    assert warnings == ["may copy T into owned storage (tuple element 0) if "
                        "not a value type; use copy() to make this explicit"]


def test_captured_local_at_whole_own_tuple_result_warns_per_leaf() -> None:
    # The runtime face: tests/cases/tuple/tuple_leaf_copy_warnings, s1b.
    records, warnings, _e = _probe("""\
def f() -> int32:
    t = (A(1), 3)
    def inner() -> Own[tuple[A, int32]]:
        return t
    return inner()[1]
""")
    assert _leaves(records[(4, "TpyName")]) == {(0,): (True, True, P.WARNED)}
    assert warnings == ["copies A into owned storage (tuple element 0); use "
                        "copy() to make this explicit"]


def test_captured_name_returned_by_its_owner_warns_each_owned_leaf() -> None:
    # A tuple local a closure captures, returned at an `Own`-marked slot by
    # the function owning it: the whole-tuple warning of
    # `_warn_live_name_return` tells the record's one owned leaf.
    records, warnings, _e = _probe("""\
def f() -> tuple[Own[A], int32]:
    t = (A(1), 2)
    def peek() -> int32:
        return t[1]
    print(peek())
    return t
""")
    assert _leaves(records[(6, "TpyName")]) == {(0,): (True, True, P.WARNED)}
    assert [w.split(";")[0] for w in warnings] == [
        "copies tuple[A, int32] into owned storage"]


def test_consuming_field_member_moves_out_of_self() -> None:
    records, warnings, _e = _probe("""\
from typing import Self
class H:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def take(self: Own[Self]) -> tuple[Own[A], int32]:
        return (self.a, 2)
""")
    # The consuming method's own field moves out, as its scalar read does.
    assert _leaves(records[(7, "TpyTupleLiteral")]) == {
        (0,): (True, True, P.MOVE)}
    assert [w for w in warnings if "tuple element" in w] == []


def test_a_moved_leaf_out_of_existing_storage_is_borrowed() -> None:
    # A move out of a tuple or of `self` takes existing storage: the leaf
    # is borrowed (not `fresh`); a fresh call result's is not.
    records, _w, _e = _probe("""\
from typing import Self
class H2:
    pair: tuple[A, int32]
    def __init__(self) -> None:
        self.pair = (A(1), 2)
    def take(self: Own[Self]) -> tuple[tuple[Own[A], int32], int32]:
        return (self.pair, 3)
def out(t: Own[tuple[A, int32]]) -> Own[A]:
    return t[0]
def mk1() -> tuple[Own[A], int32]:
    return (A(1), 2)
def made() -> tuple[tuple[Own[A], int32], int32]:
    return (mk1(), 3)
""")
    rec = records[(7, "TpyTupleLiteral")]
    assert _leaves(rec) == {(0, 0): (True, True, P.MOVE)}
    assert rec.fresh == frozenset()
    rec = records[(9, "TpySubscript")]
    assert _leaves(rec) == {(): (True, True, P.MOVE)}
    assert rec.fresh == frozenset()
    rec = records[(13, "TpyTupleLiteral")]
    assert _leaves(rec) == {(0, 0): (False, True, P.MOVE)}
    assert rec.fresh == {(0, 0)}


def test_a_tuple_element_moved_out_of_its_tuple_is_borrowed() -> None:
    # The tuple-element branch of the tuple source rule: the last use's
    # element read moves the element out of `t`, existing storage.
    records, _w, _e = _probe("""\
def out(t: tuple[Own[tuple[A, int32]], int32]) -> Own[tuple[A, int32]]:
    return t[0]
""")
    rec = records[(2, "TpySubscript")]
    assert _leaves(rec) == {(0,): (True, True, P.MOVE)}
    assert rec.fresh == frozenset()


def test_only_a_proven_new_value_is_fresh() -> None:
    # Freshness is an allow-list: a call result, a construction, a member
    # of a new value and a select of new values are fresh; a walrus names
    # its binding, existing storage, whatever value it was given.
    records, _w, entry = _probe("""\
class H3:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
def mka() -> Own[A]:
    return A(1)
def mkh() -> Own[H3]:
    return H3()
def call_member() -> tuple[Own[A], int32]:
    return (mka(), 1)
def ctor_member() -> tuple[Own[A], int32]:
    return (A(2), 1)
def field_member() -> tuple[Own[A], int32]:
    return (mkh().a, 1)
def select_member(c: bool) -> tuple[Own[A], int32]:
    return (mka() if c else mka(), 1)
def walrus_member(a: A) -> tuple[Own[A], int32]:
    return ((b := a), b.v)
""")
    fresh = {(0,): (False, True, P.MOVE)}
    for line in (10, 12, 14, 16):
        rec = records[(line, "TpyTupleLiteral")]
        assert _leaves(rec) == fresh, line
        assert rec.fresh == {(0,)}, line
    rec = records[(18, "TpyTupleLiteral")]
    assert _leaves(rec) == {(0,): (True, True, P.WARNED)}
    assert rec.fresh == frozenset()
    compat = entry.analyzer.compat
    members = {
        e.loc.line - _LEAF_LINES: peel_value_wrappers(e.elements[0])
        for e in entry.analyzer.ctx.source_records.keys()
        if isinstance(e, TpyTupleLiteral) and e.loc is not None}
    assert [compat.new_value(members[ln]) for ln in (10, 12, 14, 16, 18)] \
        == [True, True, True, True, False]


def test_a_walrus_is_never_new() -> None:
    # Unpeeled: the walrus is the storage of the name it binds, which the
    # next member still reads, however new its initializer is.
    records, _w, entry = _probe("""\
def walrus_new() -> tuple[Own[A], int32]:
    return ((b := A(1)), b.v)
""")
    rec = records[(2, "TpyTupleLiteral")]
    assert _leaves(rec) == {(0,): (True, True, P.WARNED)}
    assert rec.fresh == frozenset()
    lit = next(e for e in entry.analyzer.ctx.source_records.keys()
               if isinstance(e, TpyTupleLiteral)
               and e.loc.line - _LEAF_LINES == 2)
    walrus = lit.elements[0]
    while isinstance(walrus, TpyCoerce):
        walrus = walrus.expr
    assert isinstance(walrus, TpyNamedExpr)
    compat = entry.analyzer.compat
    assert compat.new_value(walrus.value)
    assert not compat.new_value(walrus)


def test_an_operator_lending_the_pointer_form_is_not_new() -> None:
    # A dunder returning `A | None` / `A | B` hands back the pointer
    # borrow form of its operand's storage, as a call returning it does:
    # an operator, a reflected operator and a `__getitem__` subscript are
    # its dunder's call, never a new value.
    records, warnings, entry = _probe("""\
class H4:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def __neg__(self) -> A | None:
        return self.a
    def __pos__(self) -> A | B:
        return self.a
    def __getitem__(self, i: int32) -> A | None:
        return self.a
    def __add__(self, o: int32) -> A | None:
        return self.a
    def __radd__(self, o: int32) -> A | None:
        return self.a
    def get(self) -> A | None:
        return self.a
def neg(h: H4) -> Own[A | None]:
    return -h
def pos(h: H4) -> Own[A | B]:
    return +h
def ops(h: H4) -> bool:
    return (h[0] is not None and (h + 1) is not None
            and (1 + h) is not None and h.get() is not None)
""")
    for line in (18, 20):
        assert _leaves(records[(line, "TpyUnaryOp")]) == {
            (): (True, True, P.WARNED)}, line
        assert records[(line, "TpyUnaryOp")].fresh == frozenset(), line
    assert sum("copies A | None into owned storage" in w
               for w in warnings) == 1
    assert sum("copies A | B into owned storage" in w for w in warnings) == 1
    compat = entry.analyzer.compat
    ctx = entry.analyzer.ctx
    found: dict[str, bool] = {}

    def visit(e: TpyExpr) -> bool:
        kind = type(e).__name__
        if (kind in ("TpyUnaryOp", "TpyBinOp", "TpySubscript",
                     "TpyMethodCall")
                and str(unwrap_readonly(ctx.get_expr_type(e)))
                in ("A | None", "A | B")):
            found[f"{kind}:{e.loc.line - _LEAF_LINES}"] = compat.new_value(e)
        return True

    for f in entry.ast.functions:
        walk_body_stmts(f.body, lambda n: walk_expr_tree(n, visit),
                        lambda _s: None)
    assert found == {"TpyUnaryOp:18": False, "TpyUnaryOp:20": False,
                     "TpySubscript:22": False, "TpyBinOp:22": False,
                     "TpyBinOp:23": False, "TpyMethodCall:23": False}


def test_an_operator_declaring_an_own_result_is_new() -> None:
    # The control: a dunder declared `-> Own[...]` hands back a new value,
    # so the same operators move without a copy warning.
    records, warnings, _entry = _probe("""\
class H5:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def __neg__(self) -> Own[A | None]:
        return A(2)
    def __pos__(self) -> Own[A | B]:
        return A(3)
def neg(h: H5) -> Own[A | None]:
    return -h
def pos(h: H5) -> Own[A | B]:
    return +h
""")
    for line in (10, 12):
        rec = records[(line, "TpyUnaryOp")]
        assert _leaves(rec) == {(): (False, True, P.MOVE)}, line
        assert rec.fresh == frozenset({()}), line
    assert not any("into owned storage" in w for w in warnings)


def test_an_element_record_is_relative_to_its_element() -> None:
    copied = Leaf(True, True, P.WARNED)
    moved = Leaf(True, True, P.MOVE)
    whole = SourceRecord({(0, 0): copied, (1,): moved})
    # The index prefix is stripped from a non-root path.
    assert whole.under(0).leaves == {(0,): copied}
    assert whole.under(1).leaves == {(): moved}
    # A whole the sink copies with no root leaf: every element is part of
    # the copy, and says so as the whole does.
    copy = SourceRecord({(0,): copied, (1,): copied})
    assert copy.disposition is Disposition.COPY
    elem = copy.under(1)
    assert elem.part_of_copy is True
    assert elem.copies_whole
    assert elem.disposition is Disposition.COPY
    assert () in elem.copy_permitted
    # An element of a whole that moves is no part of a copy, whatever its
    # own leaves say.
    mixed = whole.under(0)
    assert mixed.part_of_copy is False
    assert not mixed.copies_whole
    assert mixed.disposition is Disposition.MIXED


def test_captured_nested_tuple_copies_every_nested_leaf() -> None:
    records, warnings, _e = _probe("""\
def c(a: A) -> None:
    t = ((A(1), a), 1)
    def inner() -> Own[tuple[tuple[A, A], int32]]:
        return t
    inner()
""")
    # The capture is read in place: each nested leaf the slot owns copies,
    # warned per leaf.
    assert _leaves(records[(4, "TpyName")]) == {
        (0, 0): (True, True, P.WARNED), (0, 1): (True, True, P.WARNED)}
    assert [w for w in warnings if "tuple element 0." in w] == [
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0.0); use copy() to "
        "make this explicit",
        "copies A into owned storage (tuple element 0.1); use copy() to "
        "make this explicit"]


def test_captured_tuple_name_into_own_argument_copies_once() -> None:
    records, warnings, _e = _probe("""\
def take(t: Own[tuple[A, int32]]) -> int32:
    return t[0].v
def take2(t: tuple[Own[A], int32]) -> int32:
    return t[0].v
def outer(p: A) -> int32:
    t = (A(1), 3)
    u = (p, 4)
    def f() -> int32:
        a = take(t)
        b = take2(t)
        c = take(u)
        d = take2(u)
        return a + b + c + d
    return f() + t[0].v + u[0].v
""")
    # An owned (`t`) and a borrowed (`u`) capture read in place: the owned
    # leaf copies at both slot spellings, warned once per argument -- per
    # leaf at `Own[tuple[...]]`, whole at the `&&` parameter.
    for line in (9, 10, 11, 12):
        assert _leaves(records[(line, "TpyName")]) == {
            (0,): (True, True, P.WARNED)}
    ctx = _e.analyzer.ctx
    assert [(d.loc.line - _LEAF_LINES, d.message) for d in ctx.diagnostics
            if d.level.value == "warning" and d.loc is not None
            and d.loc.line - _LEAF_LINES > 4] == [
        (9, "copies A into owned storage (tuple element 0); use copy() to "
            "make this explicit"),
        (10, "copies tuple[A, int32] into owned storage; use copy() to make "
             "this explicit"),
        (11, "copies A into owned storage (tuple element 0); use copy() to "
             "make this explicit"),
        (12, "copies tuple[A, int32] into owned storage; use copy() to make "
             "this explicit")]


def _registrations(source: str) -> list[tuple[int, str, object, str]]:
    """Every owning tuple sink registration of `source` (after the leaf
    prelude) that reached the record builder: (line, source node kind,
    sink kind, slot)."""
    kept: list[tuple[int, str, object, str]] = []
    build = TypeCompatibility.build_source_records

    def collecting(self: TypeCompatibility) -> list[TpyExpr]:
        for r in self.ctx.func.tuple_sinks.values():
            line = r.source.loc.line - _LEAF_LINES
            if line > 0:
                kept.append((line, type(r.source).__name__, r.sink,
                             str(r.slot)))
        return build(self)

    TypeCompatibility.build_source_records = collecting
    try:
        _compile(LEAF_PRELUDE + source)
    finally:
        TypeCompatibility.build_source_records = build
    return kept


def test_one_registration_per_source() -> None:
    kept = _registrations("""\
def own(x: Own[tuple[A, int32]]) -> int32:
    return x[1]
def by_name() -> int32:
    t = (A(1), 2)
    r = own(t)
    return r + t[1]
def by_walrus() -> int32:
    r = own((t := (A(2), 1)))
    return r + t[1]
def keep(x: Own[tuple[Own[A], int32]]) -> int32:
    return x[1]
def fresh(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 1)
def nested_slot(a: A) -> int32:
    return keep(fresh(a))
""")
    # The argument's own check stands over its coercion's `Own`-slot check,
    # which runs on the same source after it -- an `Own` result included
    # (`register_owned_result`) -- at its outermost slot; a name passed
    # whole registers at the declared parameter type.
    assert [k for k in kept if k[0] in (5, 8, 15)] == [
        (5, "TpyName", TupleSink.ARG, "Own[tuple[A, int32]]"),
        (8, "TpyNamedExpr", TupleSink.ARG, "Own[tuple[A, int32]]"),
        (15, "TpyCall", TupleSink.ARG, "Own[tuple[Own[A], int32]]")]


def test_walrus_store_warns_its_name_read_once() -> None:
    records, warnings, _e = _probe("""\
class S:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(0), 0)
def stores(b: A) -> None:
    h = S()
    h.p = (u := (b, 2))
    xs = [(A(0), 0)]
    xs[0] = (w := (b, 3))
    print(u[1], w[1])
""")
    # The store copies the walrus's name read; its literal's members took
    # the local's rule where the walrus bound them.
    assert _perms(records[(7, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(9, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "tuple element" in w] == [
        "copies A into field (tuple element 0); use copy() to make this "
        "explicit",
        "copies A into container (tuple element 0); use copy() to make "
        "this explicit"]


def test_records_agree_with_warnings_per_leaf_path() -> None:
    # Per-leaf shapes at every source kind -- nested arguments, `copy()`
    # selects, open `T` storage, nested fields and calls, settled moves,
    # lambda bodies, nested selects, mixed slots, consuming fields,
    # captured nested names, walrus stores -- compiled with the agreement
    # check (`_compile`); together they copy enough leaves to bite.
    entry_count = 0
    for source in _PROBE_SHAPES:
        _compiler, modules = _compile(LEAF_PRELUDE + source)
        ctx = _entry(modules).analyzer.ctx
        entry_count += sum(leaf.permission.copies
                           for rec in ctx.source_records.values()
                           for leaf in rec.leaves.values())
    assert entry_count >= 10


_PROBE_SHAPES = ["""\
def take2(p: tuple[Own[tuple[A, int32]], int32]) -> int32:
    return p[1]
def nested_arg() -> int32:
    return take2(((A(1), 2), 3))
def explicit_tuple(a: A, b: A, c: bool) -> Own[tuple[A, int32]]:
    x = (a, 1)
    y = (b, 2)
    return copy(x) if c else copy(y)
def explicit_nested(a: A, b: A, c: bool, d: bool) -> Own[tuple[A, int32]]:
    return ((copy(a), 1) if c else (copy(b), 2)) if d else (copy(a), 3)
def store(a: A, b: A, c: bool) -> int32:
    x = (a, 1)
    y = (b, 2)
    out = [copy(x) if c else copy(y)]
    return out[0][0].v
""", """\
def push[T](x: T) -> int32:
    xs: list[tuple[T, int32]] = []
    xs.append((x, 1))
    return xs[0][1]
def box[T](x: T) -> Own[list[tuple[T, int32]]]:
    return [(x, 1)]
def direct[T](x: T) -> tuple[Own[T], int32]:
    return (x, 1)
""", """\
class H:
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.p = (A(1), 2)
def field(h: H) -> tuple[tuple[Own[A], int32], int32]:
    return (h.p, 3)
def mk1(a: A) -> tuple[Own[A], int32]:
    return (A(a.v), 2)
def call(a: A) -> tuple[tuple[Own[A], int32], int32]:
    return (mk1(a), 3)
def fresh() -> tuple[Own[tuple[A, int32]], int32]:
    return ((A(1), 2), 3)
def borrower(a: A) -> Own[tuple[A, int32]]:
    return (a, 2)
""", """\
def back(a: A, n: int32) -> tuple[Own[A], int32]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return (t, 1)
        t = a
        i += 1
    return (A(0), 0)
""", """\
def make() -> tuple[Own[A], int32]:
    return (A(1), 1)
def take3(t: tuple[Own[A], int32]) -> int32:
    return t[1]
def take4(t: Own[tuple[A, int32]]) -> int32:
    return t[1]
def relay() -> tuple[Own[A], int32]:
    return make()
def f() -> int32:
    t = make()
    return take3(make()) + take4(make()) + take3(t)
def lit(a: A) -> int32:
    g: Callable[[], Own[tuple[A, int32]]] = lambda: (a, 1)
    return g()[0].v
def lit_fresh() -> int32:
    g: Callable[[], Own[tuple[A, int32]]] = lambda: (A(3), 1)
    return g()[0].v
def nested_lit(a: A) -> int32:
    return take5(((a, 1), 2)) + take5(((A(3), 1), 2))
def take5(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return 7
""", """\
def p1(a: A) -> tuple[A, int32]:
    return (a, 1)
def selected(a: A, b: A, c: bool) -> Own[tuple[tuple[A, int32], int32]]:
    return (p1(a), 1) if c else (p1(b), 2)
def stored(a: A, b: A, c: bool) -> None:
    xs: list[tuple[tuple[A, int32], int32]] = []
    xs.append((p1(a), 1) if c else (p1(b), 2))
def own_first(a: A) -> tuple[Own[A], A]:
    return (A(1), a)
def call(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return (own_first(a), 3)
def lit(a: A) -> tuple[Own[tuple[A, A]], int32]:
    return ((a, A(2)), 3)
def whole(a: A) -> Own[tuple[tuple[A, int32], int32]]:
    return ((a, 1), 1)
def mixed_slot(a: A) -> tuple[Own[tuple[A, int32]], int32]:
    return ((a, 1), 1)
""", """\
from typing import Self
def take_nested(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def nested_arg(a: A) -> int32:
    return take_nested((A(0), (a, 1)))
def lam_fresh() -> int32:
    g: Callable[[], tuple[Own[A], int32]] = lambda: (A(1), 2)
    return g()[1]
class C:
    a: A
    p: tuple[A, int32]
    def __init__(self) -> None:
        self.a = A(1)
        self.p = (A(0), 0)
    def take(self: Own[Self]) -> tuple[Own[A], int32]:
        return (self.a, 2)
def captured(a: A) -> None:
    t = ((A(1), a), 1)
    def inner() -> Own[tuple[tuple[A, A], int32]]:
        return t
    inner()
def walrus_store(b: A, c: C) -> None:
    c.p = (u := (b, 2))
    xs = [(A(0), 0)]
    xs[0] = (w := (b, 3))
    print(u[1], w[1], take((A(1), 2)), take((v := (b, 2))), v[1])
"""]


def test_hedged_leaf_is_an_open_type_parameter() -> None:
    records, warnings, _ctx = _leaf_records("""\
def lt[T](xs: list[T], x: T) -> Own[tuple[list[T], T]]:
    return (xs, x)
""")
    # `list[T]` copies whatever T is; a bare `T` leaf only may.
    assert _perms(records[(2, "TpyTupleLiteral")]) == {(0,): P.WARNED,
                                                      (1,): P.HEDGED}
    assert len(warnings) == 2


def test_global_and_member_init_sinks_publish_the_record() -> None:
    records, warnings, _ctx = _leaf_records("""\
G = (A(1), 2)
def ret() -> Own[tuple[A, int32]]:
    return G
def app() -> None:
    xs: list[tuple[A, int32]] = []
    xs.append(G)
class M:
    p: tuple[A, int32]
    q: tuple[A, A]
    def __init__(self, a: A) -> None:
        self.p = (a, 1)
        self.q = pair(a, a)
""")
    # A global read in a function lends what the global holds.
    assert _perms(records[(3, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(6, "TpyName")]) == {(0,): P.WARNED}
    # Constructor member-init stores: a literal and a borrowing call.
    assert _perms(records[(11, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(12, "TpyCall")]) == {(0,): P.WARNED,
                                               (1,): P.WARNED}
    assert len(warnings) == 5


def test_lookup_peels_the_wrappers_the_record_was_keyed_past() -> None:
    _records_, _w, ctx = _leaf_records("""\
def f() -> int32:
    r = take((t := (A(2), 1)))
    return r + t[1]
""")
    lit = next(e for e in ctx.source_records.keys()
               if isinstance(e, TpyTupleLiteral))
    walrus = TpyNamedExpr(target="u", value=lit)
    assert read_record(ctx, walrus) is read_record(ctx, lit)
    assert read_record(ctx, walrus) is not None
    assert read_record(ctx, TpyName("nothing")) is None


def test_non_name_record_agrees_with_the_declared_copies() -> None:
    _records_, _w, ctx = _leaf_records("""\
def a(x: A, y: A, c: bool) -> Own[tuple[A, A]]:
    return pair(x, y) if c else pair(y, x)
def b[T](x: T) -> Own[tuple[T, int32]]:
    return mk(x)
def c(xs: list[tuple[A | B, int32]]) -> None:
    ys: list[tuple[A | B, int32]] = []
    ys.append(xs[0])
def d(a: A, b: A) -> tuple[Own[A], A]:
    return pair(a, b)
""")
    checked = 0
    for e, rec in ctx.source_records.items():
        if isinstance(e, (TpyName, TpyTupleLiteral)):
            continue
        checked += bool({p[0] for p in rec.copied if p})
    assert checked == 4


def test_every_position_publishes_the_record() -> None:
    # One borrowing call source (`pair(a, a)`, element 0 copies) at each
    # position's owning sink.
    records, _warnings_, _ctx = _leaf_records("""\
from typing import Iterator
from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
class M:
    def meth(self, a: A) -> tuple[Own[A], A]:
        return pair(a, a)
def gen(a: A) -> Iterator[tuple[Own[A], A]]:
    yield pair(a, a)
async def co(a: A) -> tuple[Own[A], A]:
    return pair(a, a)
def comp(a: A) -> list[tuple[A, A]]:
    return [pair(a, a) for _ in range(2)]
def fin(a: A) -> tuple[Own[A], A]:
    try:
        return pair(a, a)
    finally:
        print("f")
def arm(a: A, n: int32) -> tuple[Own[A], A]:
    match n:
        case 1:
            return pair(a, a)
        case _:
            return (A(0), a)
@error_return(E)
def er(a: A) -> tuple[Own[A], A]:
    return pair(a, a)
""")
    for line in (7, 9, 11, 13, 16, 22, 27):
        assert _perms(records[(line, "TpyCall")])[(0,)] is P.WARNED, line


def _copy_warnings(ctx: SemanticContext) -> list[tuple[int, str]]:
    """The copy warnings after the leaf prelude, by line, their remedy cut."""
    return [(d.loc.line - _LEAF_LINES, d.message.split(";")[0])
            for d in ctx.diagnostics
            if d.level.value == "warning" and d.loc is not None
            and d.loc.line > _LEAF_LINES and "cop" in d.message]


_SINKS_BODY = """\
from typing import Iterator
from tpy import Send, readonly
class H{G}:
    p: tuple[{L}, int32]
    def __init__(self, a: {P}) -> None:
        self.p = (a, 0)
def own_arg{G}(p: Own[tuple[{L}, int32]]) -> int32:
    return 0
def elem_arg{G}(p: tuple[Own[{L}], int32]) -> int32:
    return 0
def ret{G}(t: tuple[{L}, int32]) -> tuple[Own[{L}], int32]:
    return t
def gen{G}(a: {P}) -> Iterator[tuple[Own[{L}], int32]]:
    t: tuple[{L}, int32] = (a, 1)
    yield t
    yield (a, 2)
    print(t[1])
def f{G}(a: {P}) -> None:
    t: tuple[{L}, int32] = (a, 1)
    h = H(a)
    h.p = t
    h.p = (a, 2)
    xs: list[tuple[{L}, int32]] = []
    xs.append(t)
    xs.append((a, 3))
    print(own_arg(t))
    print(own_arg((a, 4)))
    print(elem_arg(t))
    print(elem_arg((a, 5)))
    print(t[1])
"""
_N, _L = "TpyName", "TpyTupleLiteral"
_SINKS_BINDINGS = {(14, _L): {(0,): P.PASS}, (19, _L): {(0,): P.PASS},
                   (20, "TpyCall"): {(): P.MOVE},
                   (23, "TpyArrayLiteral"): {(): P.MOVE}}
_W, _H = P.WARNED, P.HEDGED


@pytest.mark.parametrize("leaf, records, warnings", [
    (dict(G="", L="A | B", P="A"),
     {(6, _L): _W, (12, _N): _W, (15, _N): _W, (16, _L): _W, (21, _N): _W,
      (22, _L): _W, (24, _N): _W, (25, _L): _W, (26, _N): _W, (27, _L): _W,
      (28, _N): _W, (29, _L): _W},
     [(6, "copies A | B into field (tuple element 0)"),
      (12, "copies A | B into owned storage (tuple element 0)"),
      (15, "copies A | B into owned storage (tuple element 0)"),
      (16, "copies A | B into owned storage (tuple element 0)"),
      (21, "copies A | B into field (tuple element 0)"),
      (22, "copies A | B into field (tuple element 0)"),
      (24, "copies A | B into owned storage (tuple element 0)"),
      (25, "copies A into owned storage (argument 'object' tuple element 0)"),
      (26, "copies A | B into owned storage (tuple element 0)"),
      (27, "copies A into owned storage (argument 'p' tuple element 0)"),
      (28, "copies tuple[Own[A | B], int32] into owned storage"),
      (28, "copies tuple[A | B, int32] into owned storage"),
      (29, "copies A into owned storage (argument 'p' tuple element 0)")]),
    (dict(G="", L="readonly[A]", P="A"),
     {(6, _L): _W, (12, _N): _W, (15, _N): _W, (16, _L): _W, (21, _N): _W,
      (22, _L): _W, (24, _N): _W, (25, _L): _W, (26, _N): _W, (27, _L): _W,
      (28, _N): _W, (29, _L): _W},
     [(6, "copies A into field (tuple element 0)"),
      (12, "copies readonly[A] into owned storage (tuple element 0)"),
      (15, "copies readonly[A] into owned storage (tuple element 0)"),
      (16, "copies A into owned storage (tuple element 0)"),
      (21, "copies readonly[A] into field (tuple element 0)"),
      (22, "copies A into field (tuple element 0)"),
      (24, "copies readonly[A] into owned storage (tuple element 0)"),
      (25, "copies A into owned storage (argument 'object' tuple element 0)"),
      (26, "copies readonly[A] into owned storage (tuple element 0)"),
      (27, "copies A into owned storage (argument 'p' tuple element 0)"),
      (28, "copies tuple[Own[readonly[A]], int32] into owned storage"),
      (28, "copies tuple[readonly[A], int32] into owned storage"),
      (29, "copies A into owned storage (argument 'p' tuple element 0)")]),
    # An owned `Send[T]` leaf is neither seen as owned nor as an open `T`
    # (BUGS.md#send-own-tuple-leaf-copy-unwarned): the return of the name
    # (12), the yielded literal (16) and the literal at the `Own`-element
    # argument (29) copy with no hedge, each HEDGED by its open type all
    # the same.
    (dict(G="[T]", L="Send[T]", P="Send[T]"),
     {(6, _L): _H, (12, _N): _H, (15, _N): _H, (16, _L): _H, (21, _N): _H,
      (22, _L): _H, (24, _N): _H, (25, _L): _H, (26, _N): _H, (27, _L): _H,
      (28, _N): _H, (29, _L): _H},
     [(6, "may copy Send[T] into field (tuple element 0) if not a value "
       "type"),
      (15, "may copy Send[T] into owned storage (tuple element 0) if not a "
       "value type"),
      (21, "may copy Send[T] into field (tuple element 0) if not a value "
       "type"),
      (22, "may copy Send[T] into field (tuple element 0) if not a value "
       "type"),
      (24, "may copy Send[T] into owned storage (tuple element 0) if not a "
       "value type"),
      (25, "may copy Send[T] into owned storage (argument 'object' tuple "
       "element 0) if not a value type"),
      (26, "may copy Send[T] into owned storage (tuple element 0) if not a "
       "value type"),
      (27, "may copy Send[T] into owned storage (argument 'p' tuple element "
       "0) if not a value type"),
      (28, "may copy tuple[Send[T], int32] into owned storage if not a value "
       "type")]),
], ids=["union", "readonly", "send"])
def test_wrapped_and_union_leaves_at_every_owning_sink(
        leaf: dict[str, str], records: dict[tuple[int, str], LeafPermission],
        warnings: list[tuple[int, str]]) -> None:
    # A name and a literal of one leaf kind at a field, a container, an
    # `Own[tuple]` argument, an `Own`-element argument, a return and a
    # yield. The rule: a record leaf follows the copy rule the sink's
    # warning applies to it. The rows the parameters cite are the known
    # exceptions.
    _compiler, modules = _compile(LEAF_PRELUDE + _SINKS_BODY.format(**leaf))
    got, _w, ctx = _records_of(_entry(modules).analyzer.ctx)
    assert {key: _perms(got[key])[(0,)] for key in records} == records
    # The name bindings of the body are owning sinks of what they own:
    # the literal locals hold `a` by reference, `h` and `xs` take fresh
    # values.
    assert {key: _perms(rec) for key, rec in got.items()
            if key not in records} == _SINKS_BINDINGS
    assert _copy_warnings(ctx) == warnings


def test_nested_members_and_walrus_args_follow_their_sites_rule() -> None:
    _compiler, modules = _compile(LEAF_PRELUDE + """\
from typing import Iterator
from tpy import Send
def take_mixed(p: tuple[Own[A], B]) -> int32:
    return p[1].w
def walrus_arg(a: A, b: B) -> None:
    t = (a, b)
    print(take_mixed((w := t)))
    print(w[0].v, a.v)
def take_whole(p: Own[tuple[A, tuple[B, int32]]]) -> int32:
    return p[1][1]
def nested_arg(b: B) -> None:
    print(take_whole((A(0), (b, 1))))
    print(b.w)
def nested_yield(a: A, b: B) -> Iterator[tuple[Own[tuple[A, B]], int32]]:
    yield ((a, b), 1)
class G[T]:
    p: tuple[Send[Own[T]], int32]
    def __init__(self, a: Send[T]) -> None:
        self.p = (a, 1)
def take(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def elem_nested(a: A) -> None:
    print(take((A(0), (a, 1))))
""")
    records, _w, ctx = _records_of(_entry(modules).analyzer.ctx)
    # The walrus at an `Own`-element parameter copies its owned leaf whole.
    assert _leaves(records[(7, "TpyName")]) == {
        (0,): (True, True, P.WARNED), (1,): (True, False, P.PASS)}
    # A nested value-tuple member of an argument is owned storage, as its
    # warning says.
    assert _perms(records[(12, "TpyTupleLiteral")]) == {
        (0,): P.MOVE, (1, 0): P.WARNED}
    # The members of an `Own`-marked nested tuple member of a yield are
    # each warned, as the return twin's are.
    assert _perms(records[(15, "TpyTupleLiteral")]) == {
        (0, 0): P.WARNED, (0, 1): P.WARNED}
    # The field stores a copy of the borrowed `Send[Own[T]]` member, yet the
    # field's check does not see it as owned and warns nothing: HEDGED by
    # its open type all the same (BUGS.md#send-own-tuple-leaf-copy-unwarned).
    assert _perms(records[(19, "TpyTupleLiteral")]) == {(0,): P.HEDGED}
    # An unmarked nested member of an `Own`-element argument stays a borrow:
    # the record moves the fresh `A(0)` and passes the borrowed `a`, which
    # the slot does not own, and the site warns nothing.
    assert _leaves(records[(23, "TpyTupleLiteral")]) == {
        (0,): (False, True, P.MOVE), (1, 0): (True, False, P.PASS)}
    assert _copy_warnings(ctx) == [
        (7, "copies tuple[A, B] into owned storage"),
        (12, "copies B into owned storage (tuple element 1.0)"),
        (15, "copies A into owned storage (tuple element 0.0)"),
        (15, "copies B into owned storage (tuple element 0.1)")]


def test_own_tuple_yield_takes_the_return_rule() -> None:
    # A yield at `Iterator[Own[tuple[...]]]` is the same owning result sink
    # as the return: a literal's members, a tuple name and a lending call
    # take the rule of the `tuple[Own[A], ...]` twin, per leaf.
    records, _w, ctx = _leaf_records("""\
from typing import Iterator
def ot_live() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    yield (a, 3)
    a.v = 7
def ot_dead() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    yield (a, 3)
def ot_param(p: A) -> Iterator[Own[tuple[A, int32]]]:
    yield (p, 3)
def oaa_live(p: A) -> Iterator[Own[tuple[A, A]]]:
    a = A(1)
    yield (a, p)
    print(a.v)
def ot_name() -> Iterator[Own[tuple[A, int32]]]:
    t = (A(1), 3)
    yield t
    u = (A(2), 4)
    print(t[1])
    yield u
def lend(p: A) -> tuple[A, int32]:
    return (p, 3)
def ot_borrowed(p: A) -> Iterator[Own[tuple[A, int32]]]:
    t = lend(p)
    yield t
def to_borrowed(p: A) -> Iterator[tuple[Own[A], int32]]:
    t = lend(p)
    yield t
def owned() -> Own[tuple[A, int32]]:
    return (A(1), 3)
def ot_results() -> Iterator[Own[tuple[A, int32]]]:
    yield owned()
    t = (A(2), 4)
    yield copy(t)
    print(t[1])
def to_walrus(a: A) -> Iterator[tuple[Own[A], int32]]:
    yield (t := (a, 3))
    print(t[1])
""")
    assert _perms(records[(4, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(8, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    assert _perms(records[(10, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(13, "TpyTupleLiteral")]) == {
        (0,): P.WARNED, (1,): P.WARNED}
    # A live tuple name copies, its last use moves.
    assert _perms(records[(17, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(20, "TpyName")]) == {(0,): P.MOVE}
    # A name bound to a lending call's result copies what it borrows, at
    # its last use too.
    assert _perms(records[(25, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(28, "TpyName")]) == {(0,): P.WARNED}
    assert _perms(records[(32, "TpyCall")]) == {(0,): P.MOVE}
    assert _perms(records[(34, "TpyCall")]) == {(0,): P.EXPLICIT}
    assert _perms(records[(37, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _copy_warnings(ctx) == [
        (4, "copies A into owned storage (tuple element 0)"),
        (10, "copies A into owned storage (tuple element 0)"),
        (13, "copies A into owned storage (tuple element 0)"),
        (13, "copies A into owned storage (tuple element 1)"),
        (17, "copies A into owned storage (tuple element 0)"),
        (25, "copies A into owned storage (tuple element 0)"),
        (28, "copies A into owned storage (tuple element 0)"),
        # One warning for the walrus's one copied leaf, its type spelled
        # as the program does.
        (37, "copies tuple[A, int32] into owned storage")]


def test_nullable_own_tuple_yield_takes_the_return_rule() -> None:
    # `X | None` takes its non-None value as `X` does, at a yield as at the
    # return, in both `Own` spellings.
    records, _w, ctx = _leaf_records("""\
from typing import Iterator
def g1(p: A) -> Iterator[Own[tuple[A, int32]] | None]:
    yield (p, 3)
def g2(p: A) -> Iterator[tuple[Own[A], int32] | None]:
    yield (p, 3)
def r1(p: A) -> Own[tuple[A, int32]] | None:
    return (p, 3)
def r2(p: A) -> tuple[Own[A], int32] | None:
    return (p, 3)
""")
    for line in (3, 5, 7, 9):
        assert _perms(records[(line, "TpyTupleLiteral")]) == {
            (0,): P.WARNED}, line
    assert _copy_warnings(ctx) == [
        (line, "copies A into owned storage (tuple element 0)")
        for line in (3, 5, 7, 9)]


def test_own_tuple_yield_live_names_and_positions() -> None:
    # A live tuple name at `Own[tuple[A, A]]` copies each leaf; a method
    # generator's and a `try` body's yield take the free function's rule:
    # a live member copies, a last-use one moves.
    records, _w, ctx = _leaf_records("""\
from typing import Iterator
def oaa_name() -> Iterator[Own[tuple[A, A]]]:
    t = (A(1), A(2))
    yield t
    print(t[1].v)
class G:
    k: int32
    def __init__(self) -> None:
        self.k = 1
    def live(self) -> Iterator[Own[tuple[A, int32]]]:
        a = A(1)
        yield (a, 3)
        a.v = 7
    def dead(self) -> Iterator[Own[tuple[A, int32]]]:
        a = A(1)
        yield (a, 3)
def tf_live() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    try:
        yield (a, 3)
        a.v = 7
    finally:
        print("done")
def tf_dead() -> Iterator[Own[tuple[A, int32]]]:
    try:
        a = A(1)
        yield (a, 3)
    finally:
        print("done")
""")
    assert _perms(records[(4, "TpyName")]) == {(0,): P.WARNED,
                                               (1,): P.WARNED}
    assert _perms(records[(12, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(16, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    assert _perms(records[(20, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(27, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    # Methods are analyzed after the free functions: compare by line.
    assert sorted(_copy_warnings(ctx)) == [
        (4, "copies A into owned storage (tuple element 0)"),
        (4, "copies A into owned storage (tuple element 1)"),
        (12, "copies A into owned storage (tuple element 0)"),
        (20, "copies A into owned storage (tuple element 0)")]


def test_live_nocopy_tuple_name_yield_errors() -> None:
    # The yield's frame keeps a live name, so its non-copyable leaf would be
    # copied: an error, as at the `tuple[Own[N], ...]` twin.
    with pytest.raises(SemanticError,
                       match=r"cannot copy non-copyable type 'N' into owned "
                             r"storage \(tuple element 0\)"):
        _compile(LEAF_PRELUDE + """\
from typing import Iterator
def gen() -> Iterator[Own[tuple[N, int32]]]:
    t = (N(1), 3)
    yield t
    print(t[1])
""")


def test_nested_own_member_at_return_and_arg() -> None:
    # An `Own`-marked nested member owns each of its literal's leaves, at
    # the return and at the argument alike.
    records, _w, ctx = _leaf_records("""\
def ret_nested(a: A, b: B) -> tuple[Own[tuple[A, B]], int32]:
    return ((a, b), 1)
def take_nested(t: tuple[Own[tuple[A, B]], int32]) -> int32:
    return t[1]
def arg_nested(a: A, b: B) -> int32:
    return take_nested(((a, b), 1))
""")
    for line in (2, 6):
        assert _perms(records[(line, "TpyTupleLiteral")]) == {
            (0, 0): P.WARNED, (0, 1): P.WARNED}, line
    assert _copy_warnings(ctx) == [
        (2, "copies A into owned storage (tuple element 0.0)"),
        (2, "copies B into owned storage (tuple element 0.1)"),
        (6, "copies A into owned storage (argument 't' tuple element 0.0)"),
        (6, "copies B into owned storage (argument 't' tuple element 0.1)")]


@pytest.mark.parametrize("body", [
    "    x: A = (A(1), 3)\n",
    "    if (t := (A(1), 3)):\n        x: A = t\n",
])
def test_type_mismatch_spells_a_literal_bound_tuple(body: str) -> None:
    # A literal's and a walrus-bound literal's tuple type is spelled as the
    # program does, not with its literal element types.
    with pytest.raises(SemanticError,
                       match=r"expected A, got tuple\[A, int32\]$"):
        _compile(LEAF_PRELUDE + "def f() -> None:\n" + body)


# -- scalar sources: a scalar is a one-leaf tuple ------------------------------

def _copy_probe(source: str) -> tuple[
        dict[tuple[int, str], SourceRecord], list[str], object]:
    """`_probe`, its warnings narrowed to the copy warnings."""
    records, warnings, entry = _probe(source)
    return records, [w for w in warnings if "cop" in w], entry


_MATRIX_HEAD = """\
from typing import Any, Self
class H:
    a: A
    b: Any
    def __init__(self, a: A) -> None:
        self.a = a
        self.b = 0
    def eat(self: Own[Self]) -> int32:
        return self.a.v
def borrow(h: H) -> A:
    return h.a
def eat(a: Own[A]) -> int32:
    return a.v
"""

# Each source kind: the lines before the sink, the source, the lines after.
_SOURCES: dict[str, tuple[list[str], str, list[str]]] = {
    "param": ([], "a", []),
    "last_use": (["x = A(1)"], "x", []),
    "live": (["x = A(1)"], "x", ["print(x.v)"]),
    "field": ([], "h.a", []),
    "element": ([], "xs[0]", []),
    "element_move": ([], "t[0]", []),
    "call": ([], "borrow(h)", []),
    "select": ([], "a if c else h.a", []),
    "walrus": ([], "(w := a)", ["print(w.v)"]),
    "copy": ([], "copy(a)", []),
    "fresh": ([], "A(2)", []),
}

_SINKS = {
    "return": "return {src}",
    "field": "h.a = {src}",
    "container": "xs[0] = {src}",
    "own_arg": "print(eat({src}))",
    "any": "h.b = {src}",
}


def _matrix_source() -> tuple[str, dict[tuple[str, str], int]]:
    """One section per owning sink x source kind -- a function per
    section, a class per member-init section -- and the line (after the
    leaf prelude) of each section's sink."""
    out = [_MATRIX_HEAD]
    lines: dict[tuple[str, str], int] = {}

    def here() -> int:
        return "".join(out).count("\n") + 1

    params = "a: A, h: H, xs: list[A], t: Own[tuple[A, int32]], c: bool"
    for sink, stmt in _SINKS.items():
        for src, (setup, expr, after) in _SOURCES.items():
            if sink == "return" and after:
                # Nothing reads a local after the function returns it.
                continue
            ret = "Own[A]" if sink == "return" else "None"
            out.append(f"def {sink}_{src}({params}) -> {ret}:\n")
            out.extend(f"    {s}\n" for s in setup)
            lines[(sink, src)] = here()
            out.append("    " + stmt.format(src=expr) + "\n")
            out.extend(f"    {s}\n" for s in after)
    for src, (setup, expr, after) in _SOURCES.items():
        out.append(f"class MI_{src}:\n    a: A\n"
                   f"    def __init__(self, {params}) -> None:\n")
        out.extend(f"        {s}\n" for s in setup)
        lines[("member_init", src)] = here()
        out.append(f"        self.a = {expr}\n")
        out.extend(f"        {s}\n" for s in after)
    # A consuming receiver is a name.
    out.append("def receiver_param(h: H) -> int32:\n")
    lines[("receiver", "param")] = here()
    out.append("    return h.eat()\n")
    out.append("def receiver_last_use() -> int32:\n    g = H(A(1))\n")
    lines[("receiver", "last_use")] = here()
    out.append("    return g.eat()\n")
    # A closure reads the local it captured in place.
    out.append("def captured_return() -> int32:\n    x = A(1)\n"
               "    def inner() -> Own[A]:\n")
    lines[("return", "captured")] = here()
    out.append("        return x\n    return inner().v + x.v\n")
    for sink, stmt in _SINKS.items():
        if sink == "return":
            continue
        out.append(f"def captured_{sink}(h: H, xs: list[A]) -> None:\n"
                   f"    x = A(1)\n    def inner() -> None:\n")
        lines[(sink, "captured")] = here()
        out.append("        " + stmt.format(src="x") + "\n"
                   "    inner()\n    print(x.v)\n")
    return "".join(out), lines


_X, _M, _E = P.EXPLICIT, P.MOVE, P.WARNED
# The expected leaf per section: a borrowed source copies, told by its
# sink's warning; a fresh one, an owning local at its last use and an
# element a return or an `Own` argument moves out of its tuple move;
# `copy()` copies explicitly. None: the sink checks no such source.
_MATRIX: dict[str, dict[str, LeafPermission | None]] = {
    "return": dict(param=_E, last_use=_M, field=_E, element=_E,
                   element_move=_M, call=_E, select=_E, copy=_X, fresh=_M,
                   captured=_E),
    "field": dict(param=_E, last_use=_M, live=_E, field=_E, element=_E,
                  element_move=_E, call=_E, select=_E, walrus=_E, copy=_X,
                  fresh=_M, captured=_E),
    "member_init": dict(param=_E, last_use=_M, live=_E, field=_E,
                        element=_E, element_move=_E, call=_E, select=_E,
                        walrus=_E, copy=_X, fresh=_M),
    "container": dict(param=_E, last_use=_M, live=_E, field=_E, element=_E,
                      element_move=_E, call=_E, select=_E, walrus=_E,
                      copy=_X, fresh=_M, captured=_E),
    "own_arg": dict(param=_E, last_use=_M, live=_E, field=_E, element=_E,
                    element_move=_M, call=_E, select=_E, walrus=_E, copy=_X,
                    fresh=_M, captured=_E),
    "any": dict(param=_E, last_use=_M, live=_E, field=_E, element=_E,
                element_move=_E, call=_E, select=_E, walrus=_E, copy=_X,
                fresh=_M, captured=_E),
    "receiver": dict(param=_E, last_use=_M),
}
# A borrowing call and a walrus into `Any` copy unwarned
# (BUGS.md#any-store-walrus-copies-silently; the call's store is refused by
# the lowering).
_UNWARNED = {("any", "call"), ("any", "walrus")}


def test_scalar_sources_at_every_owning_sink() -> None:
    # Each section's one scalar record: a borrowed source's copy is WARNED
    # by its sink's warning, and LOCKSTEP holds against the copies the
    # lowering is told (`_compile`).
    src, lines = _matrix_source()
    records, _warnings, entry = _probe(src)
    got: dict[tuple[str, str], LeafPermission | None] = {}
    for (sink, kind), line in lines.items():
        at = [r for (ln, _k), r in records.items() if ln == line]
        assert len(at) <= 1, (sink, kind, at)
        got[(sink, kind)] = _perms(at[0])[()] if at else None
    assert got == {(sink, kind): perm for sink, kinds in _MATRIX.items()
                   for kind, perm in kinds.items()}
    # A leaf it copies is borrowed; one it moves is borrowed only where
    # it comes from existing storage.
    for (ln, _k), rec in records.items():
        if () in rec.leaves:
            leaf = rec.leaves[()]
            assert leaf.borrowed or leaf.permission is P.MOVE, ln
    # Each WARNED leaf is told by a warning at its source's line.
    warned = {d.loc.line - _LEAF_LINES for d in entry.analyzer.ctx.diagnostics
              if d.level.value == "warning" and d.loc is not None}
    assert {lines[k] for k, v in got.items()
            if v is _E and k not in _UNWARNED} <= warned


def test_value_bounded_leaf_is_a_value_leaf() -> None:
    # A `T: ValueType` payload copies harmlessly: its leaf is VALUE (a copy
    # with no diagnostic; a move at a real last use), scalar and tuple
    # alike; the reference leaf beside it in a mixed tuple stays WARNED.
    records, warnings, _e = _copy_probe("""\
from tpy import ValueType
def scalar[T: ValueType](x: T) -> Own[T]:
    return x
def tup[T: ValueType](x: T) -> Own[tuple[T, int32]]:
    return (x, 1)
def mixed[T: ValueType](x: T, a: A) -> Own[tuple[T, A]]:
    return (x, a)
""")
    assert _perms(records[(3, "TpyName")]) == {(): P.VALUE}
    assert _perms(records[(5, "TpyTupleLiteral")]) == {(0,): P.VALUE}
    assert _perms(records[(7, "TpyTupleLiteral")]) == {
        (0,): P.VALUE, (1,): P.WARNED}
    assert warnings == ["copies A into owned storage (tuple element 1); "
                        "use copy() to make this explicit"]


# -- a move is the decision of the check that took the read ---------------------

def _scalar_perms(records: dict[tuple[int, str], SourceRecord]
                  ) -> dict[int, list[LeafPermission]]:
    """Each scalar record's one leaf, by its source's line."""
    out: dict[int, list[LeafPermission]] = {}
    for (line, _k), rec in sorted(records.items()):
        if () in rec.leaves:
            out.setdefault(line, []).append(rec.leaves[()].permission)
    return out


def test_moves_follow_the_binding_and_the_read() -> None:
    # A read moves where its binding owns the leaf and a read rule holds
    # (its last use, its clause's move, the whole return of the name).
    records, warnings, _e = _copy_probe("""\
from typing import Iterable
class S:
    p: A
    def __init__(self, p: A) -> None:
        self.p = p
def eat(a: Own[A]) -> int32:
    return a.v
def own_elements(xs: Iterable[Own[A]]) -> int32:
    n = 0
    for x in xs:
        n += eat(x)
    return n
def shadow(xs: list[A]) -> list[A]:
    x = A(7)
    print(x.v)
    return [x for x in xs]
def twin(xs: list[A]) -> list[A]:
    y = A(7)
    print(y.v)
    return [x for x in xs]
def fresh(s: S) -> None:
    z = A(9)
    s.p = z
s = S(A(0))
ws = [A(1), A(2)]
ys: list[A] = []
for w in ws:
    ys.append(w)
if len(ws) > 0:
    b = ws[0]
    s.p = b
i = 0
while i < 1:
    c = ws[1]
    s.p = c
    i += 1
""")
    perms = _scalar_perms(records)
    # An owned element moves out of the loop that owns it (`eat(x)`): the
    # loop's decision, recorded with its held warning, which it drops.
    assert perms[11] == [P.MOVE]
    # A comprehension variable shadowing an owned local copies as its twin
    # does: the clause decides it, not the local's ownership fact.
    assert perms[16] == perms[20] == [P.WARNED]
    # A last-use owned local moves in.
    assert perms[23] == [P.MOVE]
    # Module level: a loop variable (`ys.append(w)`) and names bound in an
    # `if` and a `while` block copy, as the lowering does.
    assert perms[28] == perms[31] == perms[35] == [P.WARNED]
    assert sorted(w.split(";")[0] for w in warnings) == (
        ["copies A into field"] * 3 + ["copies A into owned storage"] * 3)


def test_moves_of_a_coerced_read_and_a_literal_member() -> None:
    # A coerced name read moves at its last use though its store's check
    # never asks for the move; a literal member moves by the same rule.
    records, warnings, _e = _copy_probe("""\
class S:
    o: A | None
    t: tuple[A, int32]
    def __init__(self) -> None:
        self.o = None
        self.t = (A(0), 0)
def opt(s: S, b: Own[A] | None) -> None:
    s.o = b
def member(s: S) -> None:
    z = A(9)
    s.t = (z, 1)
""")
    assert _scalar_perms(records)[8] == [P.MOVE]
    assert _perms(records[(11, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    assert not [w for w in warnings if "cop" in w]


def test_scalar_select_record() -> None:
    records, warnings, _e = _copy_probe("""\
def explicit(a: A, b: A, c: bool) -> Own[A]:
    return copy(a) if c else copy(b)
def mixed(a: A, c: bool) -> Own[A]:
    return A(1) if c else a
""")
    # Every arm under `copy()`: the select copies explicitly, as a tuple
    # select does.
    rec = records[(2, "TpyIfExpr")]
    assert _perms(rec) == {(): P.EXPLICIT}
    # A fresh arm and a borrowed one: the select's copy is warned.
    rec = records[(4, "TpyIfExpr")]
    assert _perms(rec) == {(): P.WARNED}
    assert [w.split(";")[0] for w in warnings] == [
        "copies A into owned storage"]


def test_sources_of_the_slot_type_register() -> None:
    # A `copy()` or an `Own` result already has the slot's type, so no
    # coercion registers it: the lambda result and the yield do.
    records, warnings, _e = _copy_probe("""\
from typing import Iterator
def own() -> Own[A]:
    return A(5)
def lam(a: A) -> int32:
    f: Callable[[], Own[A]] = lambda: copy(a)
    g: Callable[[], Own[A]] = lambda: own()
    return f().v + g().v
def gen(a: A) -> Iterator[Own[A]]:
    yield copy(a)
    yield own()
""")
    perms = _scalar_perms(records)
    assert perms[5] == perms[9] == [P.EXPLICIT]
    assert perms[6] == perms[10] == [P.MOVE]
    # The closure's capture of `a` is its own copy, not the result's.
    assert [w.split(";")[0] for w in warnings] == [
        "lambda at line 31 copies the object 'a' refers to when it is created"]


def test_scalar_sources_at_every_position() -> None:
    # A borrowed parameter at an `Own` slot at each position's sink.
    records, warnings, _e = _copy_probe("""\
from typing import Iterator
from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
def gen(a: A) -> Iterator[Own[A]]:
    yield a
async def co(a: A) -> Own[A]:
    return a
def arm(a: A, n: int32) -> Own[A]:
    match n:
        case 1:
            return a
        case _:
            return A(0)
def fin(a: A) -> Own[A]:
    try:
        return a
    finally:
        print("f")
@error_return(E)
def er(a: A) -> Own[A]:
    return a
qs = [A(1), A(2)]
ys: list[A] = []
q = qs[0]
ys.append(q)
""")
    perms = _scalar_perms(records)
    for line in (6, 8, 12, 17, 22, 26):
        assert perms[line] == [P.WARNED], line
    assert len([w for w in warnings if w.startswith("copies A")]) == 6


def test_scalar_source_types_at_a_field() -> None:
    records, warnings, _e = _copy_probe("""\
class F:
    o: A | None
    u: A | B
    l: list[A]
    def __init__(self) -> None:
        self.o = None
        self.u = A(0)
        self.l = []
def store(f: F, o: A | None, u: A | B, l: list[A]) -> None:
    f.o = o
    f.u = u
    f.l = l
""")
    perms = _scalar_perms(records)
    for line in (10, 11, 12):
        assert perms[line] == [P.WARNED], line
    assert [w.split(";")[0] for w in warnings] == [
        "copies A | None into field", "copies A | B into field",
        "copies list[A] into field"]


def test_comprehension_receiver_moves_by_its_clause() -> None:
    records, warnings, _e = _copy_probe("""\
from typing import Iterable, Self
class K:
    def __init__(self, v: int32) -> None:
        self.v = v
    def eat(self: Own[Self]) -> int32:
        return self.v
def owned(ks: Iterable[Own[K]]) -> list[int32]:
    return [k.eat() for k in ks]
def borrowed(ks: list[K]) -> list[int32]:
    return [k.eat() for k in ks]
""")
    perms = _scalar_perms(records)
    # The clause owns its elements: the consuming receiver moves each one.
    assert perms[8] == [P.MOVE]
    assert perms[10] == [P.WARNED]
    assert [w.split(";")[0] for w in warnings] == [
        "copies K into owned storage"]


def test_settlement_takes_back_a_claimed_move() -> None:
    # The return claimed the move of `t`; the loop's back edge then binds
    # it to borrowed storage, and settlement copies instead.
    records, warnings, _e = _copy_probe("""\
def back(a: A, n: int32) -> Own[A]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return t
        t = a
        i += 1
    return A(0)
def walrus(a: A) -> Own[A]:
    return (w := a)
""")
    perms = _scalar_perms(records)
    assert perms[6] == [P.WARNED]
    # A walrus is its value's read: the borrowed parameter copies.
    assert perms[11] == [P.WARNED]
    assert [w.split(";")[0] for w in warnings] == [
        "copies A into owned storage", "copies A into owned storage"]


def test_pending_number_list_copy_declares_when_decided() -> None:
    # The copied list's element type is decided after the call, and its
    # warning is emitted then: it still tells the copy.
    records, warnings, _e = _copy_probe("""\
from tpy import int64
def eat_list(xs: Own[list[int64]]) -> int32:
    return len(xs)
def pend() -> int32:
    xs = [1, 2]
    n = eat_list(xs)
    return n + len(xs)
""")
    assert _scalar_perms(records)[6] == [P.WARNED]
    assert [w.split(";")[0] for w in warnings] == [
        "copies list[int64] into owned storage"]


def test_open_payload_hedges_at_a_return_and_a_field() -> None:
    records, warnings, _e = _copy_probe("""\
def ret[T](x: T) -> Own[T]:
    return x
class G[T]:
    v: T
    def __init__(self, x: T) -> None:
        self.v = x
def store[T](g: G[T], x: T) -> None:
    g.v = x
""")
    perms = _scalar_perms(records)
    for line in (2, 6, 8):
        assert perms[line] == [P.HEDGED], line
    assert sorted(w.split(";")[0] for w in warnings) == [
        "may copy T into field if not a value type",
        "may copy T into field if not a value type",
        "may copy T into owned storage if not a value type"]


# -- the lifecycle of a copy diagnostic ----------------------------------------

def test_consuming_loop_drops_its_warning_and_moves() -> None:
    # The loop variable's copy is registered at the read; the consuming
    # loop's element moves, so its record moves and nothing is emitted.
    records, warnings, entry = _copy_probe("""\
def eat(a: Own[A]) -> int32:
    return a.v
def consume(xs: Own[list[A]]) -> int32:
    n = 0
    for x in xs:
        n += eat(x)
    return n
def keep(xs: list[A]) -> int32:
    n = 0
    for x in xs:
        n += eat(x)
    return n
""")
    assert _leaves(records[(6, "TpyName")]) == {(): (True, True, P.MOVE)}
    assert _leaves(records[(11, "TpyName")]) == {(): (True, True, P.WARNED)}
    assert warnings == ["copies A into owned storage; use copy() to make "
                        "this explicit"]


def test_reverted_consuming_loop_reemits_and_copies() -> None:
    # A later borrowing binding of the iterable reverts the consuming loop
    # at settlement: the element copies, and its registered warning stands.
    records, warnings, _e = _copy_probe("""\
def borrow(w: list[A]) -> list[A]:
    return w
def reverted(w: list[A]) -> Own[list[A]]:
    xs: list[A] = [A(1)]
    out: list[A] = []
    i = 0
    while i < 2:
        for x in xs:
            out.append(x)
        xs = borrow(w)
        i += 1
    return out
""")
    assert _leaves(records[(9, "TpyName")]) == {(): (True, True, P.WARNED)}
    assert [w.split(";")[0] for w in warnings] == [
        "copies A into owned storage"]


def test_generic_hedge_is_hedged_until_an_instantiation_rejects_it() -> None:
    body = """\
def ins[T](v: T) -> int32:
    ys: list[T] = []
    ys.append(v)
    return len(ys)
def use() -> int32:
    return ins({arg})
"""
    # The definition-time hedge stands for a copyable instantiation.
    records, warnings, entry = _copy_probe(body.format(arg="3"))
    assert _perms(records[(3, "TpyName")]) == {(): P.HEDGED}
    assert warnings == ["may copy T into owned storage if not a value type; "
                        "use copy() to make this explicit"]
    # A `@nocopy` one replaces it with the located error. The program does
    # not compile, so nothing consumes the record.
    _c, modules = _compile_program(LEAF_PRELUDE + body.format(arg="N(1)"))
    ctx = _entry(modules).analyzer.ctx
    records, _w, _ctx = _records_of(ctx)
    assert _perms(records[(3, "TpyName")]) == {(): P.HEDGED}
    assert [d.message.split(" into")[0] for d in ctx.diagnostics
            if d.level.value == "error"] == [
        "cannot copy non-copyable type 'N'"]


def _clone_perms(entry: object, line: int) -> list[dict]:
    """The permissions of every record whose source sits at `line` (after
    the leaf prelude): one per clone of a body analyzed more than once."""
    return [_perms(rec) for e, rec in entry.analyzer.ctx.source_records.items()
            if e.loc is not None and e.loc.line - _LEAF_LINES == line]


def test_auto_readonly_clones_share_one_warning() -> None:
    # The clone pair analyzes the body twice: each clone's source has its
    # record, each told by its own warning, and the duplicate warnings
    # collapse onto one report.
    records, warnings, entry = _copy_probe("""\
from tpy import auto_readonly, readonly
def eat(a: Own[A]) -> int32:
    return a.v
class K:
    a: A
    def __init__(self) -> None:
        self.a = A(0)
    @auto_readonly
    def pick(self, b: readonly[A]) -> auto_readonly[A]:
        print(eat(b))
        return self.a
""")
    assert _clone_perms(entry, 10) == [{(): P.WARNED}] * 2
    assert warnings == ["copies A into owned storage; use copy() to make "
                        "this explicit"]


def test_deduplicated_handler_clones_keep_their_records() -> None:
    # `except (A, B)` analyzes its handler once per type: each clone's
    # record is told by its own warning, and the repeated one is dropped.
    records, warnings, entry = _copy_probe("""\
def eat(a: Own[A]) -> int32:
    return a.v
def handled(a: A) -> int32:
    try:
        raise ValueError("x")
    except (ValueError, TypeError):
        return eat(a)
""")
    assert _clone_perms(entry, 7) == [{(): P.WARNED}] * 2
    assert len(warnings) == 1


def test_rejected_trial_registers_nothing() -> None:
    # Overload resolution analyzes the lambda body under each candidate's
    # hint in a trial whose function state is rolled back: the copy it
    # registered goes with it, the winner's analysis registers again.
    records, warnings, entry = _copy_probe("""\
from tpy import dispatch, Fn
def keep(x: Own[A]) -> int32:
    return x.v
@dispatch
def m[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return len(xs)
@dispatch
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> int32:
    return 2 * len(xs)
def use(xs: list[A]) -> int32:
    return m(lambda q: keep(q), xs)
""")
    assert _perms(records[(11, "TpyName")]) == {(): P.WARNED}
    assert len(warnings) == 1


def test_late_element_copy_is_emitted_after_settlement() -> None:
    # An element copy deferred on a lending callee analyzed later is
    # emitted after every body is settled, outside any walk: it stands as
    # emitted. Its source fills an `Iterable[Own[A]]` argument, which is no
    # owning slot of the source itself, so no record reads it.
    records, warnings, entry = _copy_probe("""\
from typing import Iterable, Iterator
def sink(xs: Iterable[Own[A]]) -> int32:
    n = 0
    for x in xs:
        n += x.v
    return n
def late(xs: list[A]) -> int32:
    return sink(gen(xs))
def gen(xs: list[A]) -> Iterator[A]:
    for x in xs:
        yield x
""")
    assert not [k for k in records if k[0] == 8]
    assert warnings == ["copies A elements; use copy_iter() to make this "
                        "explicit"]


def test_unowned_nested_member_passes_unwarned() -> None:
    # A nested member at a leaf the parameter does not own stays a borrow:
    # the record passes it and nothing warns.
    records, warnings, _e = _copy_probe("""\
def take(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def elem_nested(a: A) -> None:
    print(take((A(0), (a, 1))))
""")
    assert _leaves(records[(4, "TpyTupleLiteral")]) == {
        (0,): (False, True, P.MOVE), (1, 0): (True, False, P.PASS)}
    assert not warnings


def test_union_leaf_copy_of_a_name_is_warned_and_in_its_record() -> None:
    # A tuple name's union leaf copied into a container: its record leaf is
    # the copy, WARNED by its type, and its warning says so.
    records, warnings, _e = _copy_probe("""\
def f(t: tuple[A | B, int32]) -> int32:
    xs: list[tuple[A | B, int32]] = []
    xs.append(t)
    return t[1]
""")
    assert _leaves(records[(3, "TpyName")]) == {
        (0,): (True, True, P.WARNED)}
    assert warnings == ["copies A | B into owned storage (tuple element 0); "
                        "use copy() to make this explicit"]


# -- blockers to rendering from the record --------------------------------------

_BLOCKERS = """\
from typing import Any
class P:
    def __init__(self, v: int32) -> None:
        self.v = v
class H:
    p: P
    q: P
    x: Any
    u: A | B | None
    def __init__(self, a: P) -> None:
        self.p = a
        self.q = P(0)
        self.x = copy(a)
        self.u = None
def row1(x: Own[P]) -> int32:
    def g() -> Own[P]:
        return x
    y = g()
    return y.v
def row2(x: Own[P | None]) -> int32:
    def g() -> Own[P | None]:
        return x
    y = g()
    return 0 if y is None else y.v
def row3(a: P) -> int32:
    r = P(1)
    f: Callable[[], Own[P]] = lambda: r
    return f().v + r.v + a.v
def row4() -> int32:
    f: Callable[[P], P] = lambda q: q
    p = P(3)
    return f(p).v + p.v
def row5(h: H, a: P) -> None:
    h.p = h.q = a
def row6(h: H) -> int32:
    def inner() -> int32:
        p = P(8)
        h.p = p
        return 1
    return inner()
def row7(b: P) -> int32:
    r = b
    q = P(80)
    def inner() -> None:
        nonlocal r
        r = q
    inner()
    return r.v + b.v
def row8(h: H) -> None:
    another: A | B | None = A(1)
    h.u = another
def row9(h: H) -> None:
    n = P(4)
    h.x = n
def any_walrus(h: H, a: P) -> None:
    h.x = (w := a)
    print(w.v)
def ident(c: P) -> P:
    return c
def cv_ret(f: Fn[[P], P], c: P) -> Own[P]:
    return f(c)
def cv_app(f: Fn[[P], P], c: P, out: list[P]) -> None:
    out.append(f(c))
def lam_param() -> int32:
    g: Callable[[P], Own[P]] = lambda q: q
    p = P(5)
    return g(p).v + p.v
def lam_capture(a: P) -> int32:
    f: Callable[[], Own[P]] = lambda: a
    return f().v
def any_tuple(h: H, a: P) -> None:
    t = (a, 1)
    h.x = t
class HC:
    p: P
    def __init__(self, f: Fn[[P], P], c: P) -> None:
        self.p = f(c)
def cv_field(h: H, f: Fn[[P], P], c: P) -> None:
    h.p = f(c)
def cv_any(h: H, f: Fn[[P], P], c: P) -> None:
    h.x = f(c)
def member_last() -> tuple[tuple[Own[P], int32], int32]:
    t = (P(1), 2)
    return (t, 3)
def nested_scalar(xs: list[tuple[tuple[P, int32], int32]]) -> None:
    a = P(2)
    xs.append(((a, 1), 2))
class HO:
    o: P | None
    def __init__(self) -> None:
        self.o = None
def make_opt(k: int32) -> Own[P] | None:
    if k > 0:
        return P(k)
    return None
def opt_rec(h: HO, k: int32) -> None:
    r = make_opt(k)
    h.o = r
from tpy import error_return, ReturnException
class EE(Exception, ReturnException):
    pass
@error_return(EE)
def make_p(k: int32) -> Own[P]:
    if k < 0:
        raise EE()
    return P(k)
@error_return(EE)
def er(h: H, k: int32) -> None:
    pe = make_p(k)
    h.p = pe
from typing import Iterator
def pairs2(n: int32) -> Iterator[tuple[int32, Own[P]]]:
    yield (n, P(n))
def head_own(h: H) -> None:
    for i, b in pairs2(2):
        h.p = b
from typing import Protocol
from tpy import dynamic
@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...
class Sq:
    def __init__(self, n: int32) -> None:
        self.n = n
    def area(self) -> int32:
        return self.n
def keep(s: Own[Shape]) -> int32:
    return s.area()
def dyn(k: int32) -> int32:
    s: Shape = Sq(k)
    return keep(s)
def loop_fresh(xs: Iterator[Own[P]], b: P, out: list[P]) -> None:
    for a in xs:
        a = P(4)
        out.append(a)
    a = b
    print(a.v)
def frame_consume(fouts: list[P]) -> Iterator[int32]:
    fxs: list[P] = [P(1), P(2)]
    for fa in fxs:
        yield fa.v
        fouts.append(fa)
def mkp(k: int32) -> Own[P]:
    if k > 100:
        raise ValueError("x")
    return P(k)
def try_last(touts: list[P], k: int32) -> None:
    try:
        ty = mkp(k)
        touts.append(ty)
    except ValueError:
        ty = P(7)
def try_last_tuple(tts: list[tuple[P, int32]], k: int32) -> None:
    try:
        tt = mkp(k)
        tts.append((tt, 1))
    except ValueError:
        tt = P(7)
from typing import Iterable
from tpy import own_iter
def mixed_app(a: P, mxs: list[tuple[P, P]]) -> None:
    mt = (P(1), a)
    mxs.append(mt)
def take_pp(t: Own[tuple[P, P]]) -> int32:
    return t[0].v
def mixed_arg(a: P) -> int32:
    at = (P(1), a)
    return take_pp(at)
class HT:
    t: tuple[P, P]
    def __init__(self) -> None:
        self.t = (P(0), P(0))
def mixed_field(a: P, ht: HT) -> None:
    ft = (P(1), a)
    ht.t = ft
def mixed_ret(a: P) -> tuple[Own[P], P]:
    rt = (P(1), a)
    return rt
class HN:
    p: tuple[tuple[P, int32], int32]
    def __init__(self) -> None:
        na = P(2)
        self.p = ((na, 1), 2)
def nested_field(hn: HN) -> None:
    fa = P(2)
    hn.p = ((fa, 1), 2)
def member_loop(n: int32, lout: list[tuple[tuple[P, int32], int32]]) -> None:
    for i in range(n):
        lt = (P(i), 2)
        lout.append((lt, 3))
def member_nested() -> int32:
    def inner() -> tuple[tuple[Own[P], int32], int32]:
        nt = (P(1), 2)
        return (nt, 3)
    r = inner()
    return r[1]
def nested_param(hp: H) -> None:
    def put(pp: Own[P]) -> None:
        hp.p = pp
    put(P(1))
def own_elem_loop(src: Iterable[Own[tuple[P, P]]],
                  oout: list[tuple[P, P]]) -> None:
    for ot in src:
        oout.append(ot)
def own_iter_loop(iout: list[tuple[P, int32]]) -> None:
    isrc: list[tuple[P, int32]] = [(P(1), 2)]
    for it in own_iter(isrc):
        iout.append(it)
def comp_owned(csrc: Iterable[Own[tuple[P, P]]]) -> Own[list[tuple[P, P]]]:
    return [ct for ct in csrc]
"""


def test_blocker_rows_are_recorded() -> None:
    # The copies the switch to the ownership record left filed, and the
    # rows it fixed. A filed one's record says what the source facts say --
    # a silent copy WARNED by its type, a move the binding's ownership and
    # its read make MOVE -- or there is no record where no check takes the
    # source; the rows that disagree with a check are its table's
    # (`_DIAGNOSTIC_DISAGREEMENTS`, `_RENDER_DISAGREEMENTS`).
    records, warnings, _e = _copy_probe(_BLOCKERS)
    lines = _BLOCKERS.splitlines()
    filed = {
        13: ("TpyCall", P.EXPLICIT),     # `copy()` into an `Any` member-init
                                         # (BUGS.md#any-member-init-copy-drops-erasure)
        17: ("TpyName", P.WARNED),    # captured `Own` name returned
        22: ("TpyName", P.WARNED),    # captured `Own[P | None]` returned
                                         # (BUGS.md#nested-def-captured-own-return-copies)
        46: ("TpyName", P.PASS),        # `nonlocal` rebind: the binding
                                         # aliases `q`, owning nothing
                                         # (BUGS.md#nonlocal-rebind-overwrites-in-place)
        56: ("TpyName", P.WARNED),    # walrus into `Any`
        61: ("TpyCall", P.MOVE),         # a call through a `Fn` value that
        63: ("TpyCall", P.MOVE),         # lends: its result reads as fresh
        73: ("TpyName", P.WARNED),    # a tuple local into `Any`
        77: ("TpyCall", P.MOVE),         # the `Fn` call at a member-init,
        79: ("TpyCall", P.MOVE),         # a field
        81: ("TpyCall", P.MOVE),         # and `Any`
                                         # (BUGS.md#callable-value-borrow-return-copies-unwarned)
        131: ("TpyName", P.MOVE),        # a `@dynamic` local
    }
    tables = _DIAGNOSTIC_DISAGREEMENTS | _RENDER_DISAGREEMENTS
    for line, want in filed.items():
        got = [(k, *_perms(r).values()) for (ln, k), r in records.items()
               if ln == line and (want is None or k == want[0])]
        assert got == ([] if want is None else [want]), line
    # The rows the C++ and the copy diagnostics now both follow the record
    # in: a copy warned exactly where the record copies, a move silent.
    fixed = {
        27: ("TpyName", P.WARNED),       # lambda returning its capture
        30: ("TpyName", P.WARNED),       # `lambda q: q` at a by-value result
        34: ("TpyName", P.WARNED),       # chained store (both targets)
        38: ("TpyName", P.MOVE),         # nested def's local into a field
        51: ("TpyName", P.MOVE),         # union local's last-use store
        54: ("TpyName", P.MOVE),         # last-use store into `Any`
        65: ("TpyName", P.WARNED),       # `lambda q: q` at an `Own` result
        69: ("TpyName", P.WARNED),       # a lambda capturing a parameter
        84: ("TpyTupleLiteral", P.MOVE),  # a last-use tuple name member
        87: ("TpyTupleLiteral", P.MOVE),  # a nested scalar member
        98: ("TpyName", P.MOVE),         # an `Own[R] | None` call's local,
        110: ("TpyName", P.MOVE),        # an `@error_return` bind,
        116: ("TpyName", P.MOVE),        # a loop-head `Own` target
        135: ("TpyName", P.MOVE),        # a rebound `Own` loop variable
        142: ("TpyName", P.MOVE),        # a frame's consuming loop variable
        150: ("TpyName", P.MOVE),        # a `try` hoist's last use in the
        156: ("TpyTupleLiteral", P.MOVE),  # body, scalar and tuple
        # A mixed tuple local's last use at an append, an `Own` argument,
        # a field and a return: element 0 moves, element 1 is the borrow.
        163: ("TpyName", P.MOVE, P.WARNED),
        168: ("TpyName", P.MOVE, P.WARNED),
        175: ("TpyName", P.MOVE, P.WARNED),
        178: ("TpyName", P.MOVE, P.PASS),
        # A nested scalar member at a member-init and a field.
        183: ("TpyTupleLiteral", P.MOVE),
        186: ("TpyTupleLiteral", P.MOVE),
        # A last-use tuple name member in a loop and in a nested def.
        190: ("TpyTupleLiteral", P.MOVE),
        194: ("TpyTupleLiteral", P.MOVE),
        199: ("TpyName", P.MOVE),        # a nested def's `Own` parameter
        204: ("TpyName", P.MOVE, P.MOVE),  # an owned tuple loop variable
        208: ("TpyName", P.MOVE),        # an `own_iter` tuple loop variable
        210: ("TpyName", P.MOVE, P.MOVE),  # the moving comprehension element
    }
    for line, want in fixed.items():
        assert (_B, lines[line - 1].strip()) not in tables, line
        got = [(k, *_perms(r).values()) for (ln, k), r in records.items()
               if ln == line and k == want[0]]
        assert got[:1] == [want], line
    assert [w.split(";")[0] for w in warnings] == [
        "copies P into field",
        "lambda at line 53 copies the object 'r' refers to when it is created",
        "copies P into owned storage", "copies P into owned storage",
        "copies P into field",
        "copies P into owned storage",
        "lambda at line 95 copies the object 'a' refers to when it is created",
        "copies P into owned storage",
        "copies P into owned storage (tuple element 1)",
        "copies P into owned storage (tuple element 1)",
        "copies P into field (tuple element 1)"]


def _binding_facts(source: str, monkeypatch: pytest.MonkeyPatch
                   ) -> dict[tuple[str, str], tuple[NonOwningBind, frozenset[int]]]:
    """The ownership fact (`NonOwningBind`) a tuple binding leaves on each
    local, by (function, local), with `nonowning_elems` of it -- the
    top-level index set every reader asks."""
    out: dict[tuple[str, str], tuple[NonOwningBind, frozenset[int]]] = {}
    note = alias_rebind.note_nonowning_bind

    def recording(ctx: SemanticContext, name: str, at: object,
                  elems: object = None) -> None:
        note(ctx, name, at, elems)
        rec = ctx.func.nonowning_bind(name)
        bare = bare_tuple(ctx.func.current_scope.lookup(name))
        if rec is not None and bare is not None:
            out[(ctx.func.current_function.name, name)] = (
                rec, ctx.func.nonowning_elems(name, len(bare.element_types)))

    monkeypatch.setattr(alias_rebind, "note_nonowning_bind", recording)
    _compile_program(PRELUDE + source)
    return out


def test_binding_fact_is_path_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = _binding_facts("""\
def nested_param(p: tuple[tuple[A, int32], int32]) -> None:
    u = p
    print(u[1])
def nested_fresh() -> None:
    u = ((A(1), 3), 4)
    print(u[1])
def top_borrow(a: A) -> None:
    u = (a, 3)
    print(u[1])
def doubly(p: tuple[tuple[tuple[A, int32], int32], A]) -> None:
    u = p
    print(u[1].x)
def value_member(a: A) -> None:
    t = (a, 1)
    u = (t, 2)
    print(u[1])
def own_elem(p: tuple[Own[A], int32]) -> None:
    u = p
    print(u[1])
def own_marked_call(a: A) -> None:
    u = mixed(a)
    print(u[1].x)
def open_elem[T](p: tuple[T, int32]) -> None:
    u = p
    print(u[1])
def call_open(a: A) -> None:
    open_elem((a, 1))
def walrus(p: tuple[tuple[A, int32], int32]) -> None:
    print((u := p)[1])
""", monkeypatch)
    paths = {k: rec.elems for k, (rec, _i) in facts.items()}
    idxs = {k: i for k, (_rec, i) in facts.items()}
    # A name lends every reference path, a nested one included.
    assert paths[("nested_param", "u")] == {(0, 0)}
    assert ("nested_fresh", "u") not in facts
    assert paths[("top_borrow", "u")] == {(0,)}
    assert paths[("doubly", "u")] == {(0, 0, 0), (1,)}
    # The literal member `t` is captured by value: u's storage owns it.
    assert ("value_member", "u") not in facts
    assert paths[("value_member", "t")] == {(0,)}
    # A name owns nothing, an `Own` element included; a call keeps the
    # leaf its declared return marks `Own`.
    assert paths[("own_elem", "u")] == {(0,)}
    assert paths[("own_marked_call", "u")] == {(1,)}
    # An open type parameter may be a reference.
    assert paths[("open_elem", "u")] == {(0,)}
    assert paths[("walrus", "u")] == {(0, 0)}
    # The readers keep the top-level question: an element is borrowed only
    # when it is itself a borrowed path.
    assert idxs[("nested_param", "u")] == frozenset()
    assert idxs[("top_borrow", "u")] == {0}
    assert idxs[("doubly", "u")] == {1}
    assert idxs[("own_marked_call", "u")] == {1}
    assert idxs[("walrus", "u")] == frozenset()


def test_direct_elements_projection() -> None:
    assert direct_elements([]) == frozenset()
    assert direct_elements([(0,), (1, 0), (2, 1, 0)]) == {0}
    assert direct_elements([(0, 0), (0, 1)]) == frozenset()
    assert direct_elements([(0,), (2,)]) == {0, 2}
    assert NonOwningBind(3, None).direct_elems(2) == {0, 1}
    assert NonOwningBind(3, frozenset({(0, 1), (1,)})).direct_elems(2) == {1}
    assert not NonOwningBind(3, frozenset({(0, 1)})).borrows_direct()


def test_ownership_line_names_the_first_direct_borrow(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # A binding borrowing only a nested path makes no top-level element
    # non-owning, so the line every ownership note names is the first
    # binding that borrows directly, whichever comes first.
    source = PRELUDE + """\
def nested_first(p: tuple[tuple[A, int32], int32], q: tuple[A, int32]) -> None:
    u = p  # nested-only
    v = q  # direct
    print(u[1], v[1])
def direct_first(p: tuple[A, int32], q: tuple[tuple[A, int32], int32]) -> None:
    w = p  # direct
    print(w[1])
    z = q  # nested-only
    print(z[1])
"""
    lines: dict[tuple[str, str], int | None] = {}
    note = alias_rebind.note_nonowning_bind

    def recording(ctx: SemanticContext, name: str, at: object,
                  elems: object = None) -> None:
        note(ctx, name, at, elems)
        lines[(ctx.func.current_function.name, name)] = (
            ctx.func.nonowning_line(name))

    monkeypatch.setattr(alias_rebind, "note_nonowning_bind", recording)
    _compile_program(source)
    at = {text.split("#")[1].strip() + ":" + text.split("=")[0].strip(): i
          for i, text in enumerate(source.splitlines(), 1) if "  # " in text}
    assert lines[("nested_first", "u")] is None
    assert lines[("nested_first", "v")] == at["direct:v"]
    assert lines[("direct_first", "w")] == at["direct:w"]
    assert lines[("direct_first", "z")] is None

def test_value_captured_member_copy_is_advised_away() -> None:
    # `t` captures the still-live `Own` member by value, so it owns every
    # leaf: the bare name at its last use would move (`return t;`), and the
    # explicit copy() is advised away.
    _compiler, modules = _compile(PRELUDE + """\
from tpy import copy
def own_str(s: Own[str]) -> Own[tuple[str, A]]:
    t = (s, A(1))
    print(s)
    return copy(t)
def own_int(n: Own[int32]) -> Own[tuple[int32, A]]:
    t = (n, A(1))
    print(n)
    return copy(t)
""")
    assert [w for w in _warnings(_entry(modules).analyzer)
            if "unnecessary copy()" in w] == [
        "unnecessary copy() -- 't' is at its last use and would be moved "
        "automatically"] * 2


# -- the record's MOVE side: fact 1 (what the binding owns) and fact 2 ------------

_FACTS = """\
from typing import Any, Self
class C:
    def __init__(self, v: int32) -> None:
        self.v = v
    def finish(self: Own[Self]) -> int32:
        return self.v
class S:
    a: A
    t: tuple[A, A]
    x: Any
    def __init__(self) -> None:
        self.a = A(0)
        self.t = (A(0), A(0))
        self.x = 0
    def put(self, p: tuple[Own[A], A]) -> None:
        self.t = p
def drain(s: S) -> None:
    xs: list[A] = [A(1), A(2)]
    ys: list[A] = []
    for a in xs:
        if a.v > 1:
            ys.append(a)
        else:
            s.a = a
def walrus(s: S, k: int32) -> None:
    if (w := A(k)).v > 0:
        s.a = w
def ident[T](x: T) -> T:
    return x
def keep[T](xs: list[T], x: T) -> None:
    y = ident(x)
    xs.append(y)
def rev(c: C) -> int32:
    b = C(1)
    r = b.finish()
    b = c
    return r + b.v
def sibling(a: A, xs: list[tuple[tuple[A, int32], A]]) -> None:
    u = ((A(1), 2), a)
    xs.append(u)
def any_store(s: S) -> None:
    s.x = (A(1), 2)
def lam_ret() -> int32:
    g: Callable[[Own[A]], Own[A]] = lambda q: q
    return g(A(4)).v
def lam_tuple(p: A) -> int32:
    g: Callable[[tuple[Own[A], int32]], Own[tuple[A, int32]]] = lambda p: p
    return g((A(3), 1))[1] + p.v
def nested_own(t: Own[tuple[tuple[A, int32], int32]],
               xs: list[tuple[tuple[A, int32], int32]]) -> None:
    xs.append(t)
def nested_live(xs: list[tuple[tuple[A, int32], int32]]) -> int32:
    u = ((A(1), 3), 4)
    xs.append(u)
    return u[1]
def captured(a: A) -> Own[A]:
    b = A(1)
    def inner() -> Own[A]:
        return b
    return inner()
def demoted() -> Own[list[A]]:
    xs: list[A] = []
    for i in range(2):
        x = A(i)
        xs.append(x)
    return xs
def try_after(s: S, k: int32) -> None:
    try:
        x = A(k)
    finally:
        print("f")
    s.a = x
"""


def test_record_moves_where_the_binding_owns_and_a_read_rule_holds() -> None:
    records, warnings, _e = _probe(_FACTS)

    def perms(line: int, kind: str = "TpyName") -> dict:
        return _perms(records[(line, kind)])
    # A mixed tuple parameter owns element 0 only: element 1 is the copy
    # the C++ makes as it builds the storage tuple element by element
    # (`convert.per_element`: element 0 moved, element 1's pointee copied
    # in), the one warned.
    assert perms(16) == {(0,): P.MOVE, (1,): P.WARNED}
    # A consuming loop moves its variable at every sink, the field store
    # included: no warning.
    assert perms(22) == {(): P.MOVE}
    assert perms(24) == {(): P.MOVE}
    # A walrus target owns its object; the C++ copies it (a row).
    assert perms(27) == {(): P.MOVE}
    # A local off a `val_or_ref_t` result owns nothing: its copy is the
    # open payload's, hedged.
    assert perms(32) == {(): P.HEDGED}
    # Settlement revokes a consuming receiver a later binding borrows.
    assert perms(35) == {(): P.WARNED}
    # Only the leaf the local owns moves, never its borrowed sibling.
    assert perms(40) == {(0, 0): P.MOVE, (1,): P.WARNED}
    # A tuple literal into `Any` is fresh.
    assert perms(42, "TpyTupleLiteral") == {(0,): P.MOVE}
    # A lambda returns its owned parameter: the C++ moves the returned
    # parameter, scalar and tuple.
    assert perms(44) == {(): P.MOVE}
    assert perms(47) == {(0,): P.MOVE}
    # A nested ownership-transfer parameter moved whole registers at the
    # declared slot's paths.
    assert perms(51) == {(0, 0): P.MOVE}
    # A live owning local copies its nested leaf, told by its warning.
    assert perms(54) == {(0, 0): P.WARNED}
    # A captured name is read in place: never a move.
    assert perms(59) == {(): P.WARNED}
    # A loop body's local appended at its last use moves.
    assert perms(65) == {(): P.MOVE}
    # A `try`-hoisted local the binding owns moves at its last use: no
    # warning.
    assert perms(72) == {(): P.MOVE}
    assert [w for w in warnings if "cop" in w] == [
        "copies A into field (tuple element 1); use copy() to make this "
        "explicit",
        "may copy T into owned storage if not a value type; use copy() to "
        "make this explicit",
        "copies C into owned storage; use copy() to make this explicit ('b' "
        "is bound to existing storage at line "
        f"{_LEAF_LINES + 36})",
        "copies A into owned storage (tuple element 1); use copy() to make "
        "this explicit ('u' is bound to existing storage at line "
        f"{_LEAF_LINES + 39})",
        "copies A into owned storage (tuple element 0.0); use copy() to make "
        "this explicit",
        "copies A into owned storage; use copy() to make this explicit"]


def test_owning_names_at_partly_owning_and_nested_slots() -> None:
    records, warnings, _e = _probe("""\
def tm(p: tuple[Own[A], A]) -> int32:
    return p[1].v
def own_both() -> int32:
    t = (A(1), A(2))
    return tm(t)
def take_n(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def own_nested() -> int32:
    t = (A(1), (A(2), 3))
    return take_n(t)
def take_deep(p: Own[tuple[tuple[A, int32], int32]]) -> int32:
    return p[1]
def deep_live() -> int32:
    t = ((A(1), 2), 3)
    return take_deep(t) + t[1]
def deep_last() -> int32:
    t = ((A(1), 2), 3)
    return take_deep(t)
def lam_nested() -> int32:
    f: Callable[[Own[tuple[tuple[A, int32], int32]]],
                Own[tuple[tuple[A, int32], int32]]] = lambda t: t
    g: Callable[[tuple[Own[A], tuple[Own[A], int32]]],
                Own[tuple[A, tuple[A, int32]]]] = lambda u: u
    return f(((A(1), 2), 3))[1] + g((A(1), (A(2), 3)))[1][1]
""")
    # An owning name passes the leaves its slot does not own, though the
    # name owns them: the slot's paths come first.
    assert _leaves(records[(5, "TpyName")]) == {
        (0,): (True, True, P.MOVE), (1,): (True, False, P.PASS)}
    assert _leaves(records[(10, "TpyName")]) == {
        (0,): (True, True, P.MOVE), (1, 0): (True, False, P.PASS)}
    # A whole `Own` argument owns its nested leaf: moved at the last use,
    # copied (and warned) while the name stays live.
    assert _perms(records[(15, "TpyName")]) == {(0, 0): P.WARNED}
    assert _perms(records[(18, "TpyName")]) == {(0, 0): P.MOVE}
    # A lambda hands over its parameter at every nested path its type
    # owns.
    assert _perms(records[(21, "TpyName")]) == {(0, 0): P.MOVE}
    assert _perms(records[(23, "TpyName")]) == {(0,): P.MOVE, (1, 0): P.MOVE}
    assert [w for w in warnings if "cop" in w] == [
        "copies A into owned storage (tuple element 0.0); use copy() to make "
        "this explicit"]


def test_a_demoted_read_takes_its_move_back() -> None:
    # The return reads `t` at its last use, then the loop's back edge binds
    # it to borrowed storage: settlement copies, and the record follows the
    # settled verdict whatever the order the checks ran in.
    records, warnings, _e = _probe("""\
def back(a: A, n: int32) -> tuple[Own[tuple[A, int32]], int32]:
    t = (A(1), 2)
    i = 0
    while i < n:
        if i == 1:
            return (t, 1)
        t = (a, 3)
        i += 1
    return ((A(0), 0), 0)
def back_scalar(a: A, n: int32) -> Own[A]:
    t = A(1)
    i = 0
    while i < n:
        if i == 1:
            return t
        t = a
        i += 1
    return A(0)
""")
    assert _perms(records[(6, "TpyTupleLiteral")]) == {(0, 0): P.WARNED}
    assert _perms(records[(15, "TpyName")]) == {(): P.WARNED}
    assert any("copies A into owned storage (tuple element 0.0)" in w
               for w in warnings)
    assert any("copies A into owned storage;" in w for w in warnings)


def test_a_comprehension_receiver_moves_by_its_clause() -> None:
    # The consuming receiver is the variable's sole read: its clause moves
    # it (`comp_moves`), with no last-use mark.
    records, _warnings, _e = _probe("""\
from typing import Iterable, Self
class C:
    def __init__(self, v: int32) -> None:
        self.v = v
    def finish(self: Own[Self]) -> int32:
        return self.v
def owned_inner(src: Iterable[Own[C]]) -> Own[list[int32]]:
    return [c.finish() for c in src]
""")
    assert _perms(records[(8, "TpyName")]) == {(): P.MOVE}


def test_a_moving_dict_comprehension_value_has_its_record() -> None:
    # A dict comprehension's moving value warns nothing and has its record,
    # as the list element's does; the borrowed twin copies, warned.
    records, warnings, _e = _probe("""\
from typing import Iterable
def scalar(src: Iterable[Own[A]]) -> Own[dict[int32, A]]:
    return {a.v: a for a in src}
def pair(src: Iterable[Own[tuple[A, A]]]) -> Own[dict[int32, tuple[A, A]]]:
    return {t[0].v: t for t in src}
def borrowed(src: Iterable[A]) -> Own[dict[int32, A]]:
    return {a.v: a for a in src}
""")
    assert _perms(records[(3, "TpyName")]) == {(): P.MOVE}
    assert _perms(records[(5, "TpyName")]) == {(0,): P.MOVE, (1,): P.MOVE}
    assert _perms(records[(7, "TpyName")]) == {(): P.WARNED}
    assert [w.split(";")[0] for w in warnings if "owned storage" in w] == [
        "copies A into owned storage"]


def _registered_compile(source: str) -> tuple[object, list]:
    """`_compile` without its checks: the entry module and the sources the
    owning sinks registered."""
    registered: list[tuple[SemanticContext, TpyExpr]] = []
    register = TypeCompatibility.register_sink

    def recording(self: TypeCompatibility, src: TpyExpr | None,
                  slot: TpyType | None, *args: object,
                  **kwargs: object) -> None:
        register(self, src, slot, *args, **kwargs)
        if (src is not None
                and peel_value_wrappers(src) in self.ctx.func.tuple_sinks):
            registered.append((self.ctx, src))

    TypeCompatibility.register_sink = recording
    try:
        _compiler, modules = _compile_program(source)
    finally:
        TypeCompatibility.register_sink = register
    return _entry(modules), registered


_CHECKED = LEAF_PRELUDE + """\
def whole(t: tuple[A, A], xs: list[tuple[A, A]]) -> None:
    xs.append(t)
def scalar(a: A, xs: list[A]) -> None:
    xs.append(a)
def element(ts: list[tuple[A, A]], xs: list[tuple[A, A]]) -> None:
    xs.append(ts[0])
"""


def _broken(entry: object, source: TpyExpr, path: tuple[int, ...],
            permission: LeafPermission) -> None:
    ctx = entry.analyzer.ctx
    key = peel_value_wrappers(source)
    rec = ctx.source_records[key]
    leaves = dict(rec.leaves)
    leaves[path] = replace(leaves[path], permission=permission)
    ctx.source_records[key] = SourceRecord(leaves)


def test_the_agreement_checks_fail_on_a_broken_record() -> None:
    entry, registered = _registered_compile(_CHECKED)
    _assert_records_agree_with_warnings(entry, registered, _CHECKED)
    ctx = entry.analyzer.ctx
    by_line = {e.loc.line - _LEAF_LINES: e for _c, e in registered}
    # Every registered sink has a record.
    saved = ctx.source_records[peel_value_wrappers(by_line[4])]
    del ctx.source_records[peel_value_wrappers(by_line[4])]
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(entry, registered, _CHECKED)
    ctx.source_records[peel_value_wrappers(by_line[4])] = saved
    # A leaf a warning names must copy: a MOVE there fails, per element.
    _broken(entry, by_line[2], (0,), LeafPermission.MOVE)
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(entry, registered, _CHECKED)
    _broken(entry, by_line[4], (), LeafPermission.MOVE)
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(entry, registered, _CHECKED)


_UNWARNED_GUARD = LEAF_PRELUDE + """\
def fresh() -> Own[tuple[A, int32]]:
    return (A(1), 2)
def literal(xs: list[tuple[A, int32]]) -> None:
    xs.append((A(3), 4))
def call(xs: list[tuple[A, int32]]) -> None:
    xs.append(fresh())
"""


@pytest.mark.parametrize("line", [4, 6], ids=["literal", "call"])
def test_the_unwarned_copy_guard_fails_on_a_non_name_source(line: int) -> None:
    # A flat leaf of a fresh literal or an owning call moves: a copy no
    # warning names there fails the check.
    entry, registered = _registered_compile(_UNWARNED_GUARD)
    _assert_records_agree_with_warnings(entry, registered, _UNWARNED_GUARD)
    by_line = {e.loc.line - _LEAF_LINES: e for _c, e in registered}
    rec = entry.analyzer.ctx.source_records[
        peel_value_wrappers(by_line[line])]
    assert rec.leaves[(0,)].permission is LeafPermission.MOVE
    _broken(entry, by_line[line], (0,), LeafPermission.WARNED)
    with pytest.raises(AssertionError, match="WARNED"):
        _assert_records_agree_with_warnings(entry, registered,
                                            _UNWARNED_GUARD)


def test_generic_tuple_member_of_a_list_literal_is_unhedged() -> None:
    # An open `T` member of a tuple literal held by a list literal at an
    # owning slot is HEDGED by its record with no hedge emitted, where the
    # concrete twin warns and the scalar twin hedges
    # (BUGS.md#generic-tuple-member-list-literal-unhedged).
    records, warnings, _e = _probe("""\
def box[T](x: T) -> Own[list[tuple[T, int32]]]:
    return [(x, 1)]
def boxa(y: A) -> Own[list[tuple[A, int32]]]:
    return [(y, 1)]
def box1[T](x: T) -> Own[list[T]]:
    return [x]
""")
    assert _perms(records[(2, "TpyTupleLiteral")]) == {(0,): P.HEDGED}
    assert _perms(records[(4, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert _perms(records[(6, "TpyName")]) == {(): P.HEDGED}
    assert [w.split(";")[0] for w in warnings] == [
        "copies A into owned storage (tuple element 0)",
        "may copy T into owned storage if not a value type"]


def test_the_render_gate_fails_on_a_move_the_lowering_copies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    source = LEAF_PRELUDE + """def keep(xs: list[A]) -> None:
    a = A(1)
    xs.append(a)
"""
    compiler, modules = _compile(source)
    # A name read whose record moves its leaf while the lowering copies it
    # fails the render gate.
    original = _lower_expressions.record_disposition

    def copying(value: object, analyzer: object) -> object:
        return (None if isinstance(value, TpyName) and value.name == "a"
                else original(value, analyzer))
    monkeypatch.setattr(_lower_expressions, "record_disposition", copying)
    monkeypatch.setattr(_lower_checks, "record_disposition", copying)
    monkeypatch.setattr(_bindings, "with_movable",
                        lambda rec, own: replace(rec, movable=False))
    # An insert's argument converts at its parameter: the conversion is
    # where the lowering would copy.
    monkeypatch.setattr(_lower_expressions, "_moves_here",
                        lambda name, lc: False)
    monkeypatch.setattr(_lower_convert, "tuple_moves",
                        lambda s, movable=None: False)
    with pytest.raises(AssertionError, match=_MOVE_NOT_RENDERED):
        _assert_move_leaves_render_moves(compiler, _entry(modules), source)


def test_the_render_gate_fails_on_a_copy_the_lowering_moves(
        monkeypatch: pytest.MonkeyPatch) -> None:
    source = LEAF_PRELUDE + """def keep(xs: list[A]) -> None:
    b = A(1)
    xs.append(b)
    print(b.v)
"""
    compiler, modules = _compile(source)
    # A name read whose record copies its leaf while the lowering moves it
    # fails the render gate.
    original = _lower_expressions.record_disposition

    def moving(value: object, analyzer: object) -> object:
        return (Disposition.MOVE
                if isinstance(value, TpyName) and value.name == "b"
                else original(value, analyzer))
    original_verdict = _lower_checks.verdict

    def moving_verdict(ctx: object, value: object) -> SourceRecord:
        if isinstance(value, TpyName) and value.name == "b":
            return SourceRecord({(): Leaf(False, True, LeafPermission.MOVE)})
        return original_verdict(ctx, value)
    monkeypatch.setattr(_lower_expressions, "record_disposition", moving)
    monkeypatch.setattr(_lower_checks, "record_disposition", moving)
    monkeypatch.setattr(_lower_checks, "verdict", moving_verdict)
    monkeypatch.setattr(_lower_expressions, "_copies_owned_leaf",
                        lambda e, lc: False)
    with pytest.raises(AssertionError, match=_COPY_MOVED):
        _assert_move_leaves_render_moves(compiler, _entry(modules), source)


_ONE_ELEMENT_COPIES = LEAF_PRELUDE + """def keep(xs: list[tuple[A, A]], b: A, c: bool) -> None:
    if c:
        t = (A(1), b)
    else:
        t = (A(2), b)
    xs.append(t)
"""


def test_the_render_gate_passes_a_tuple_moved_element_by_element() -> None:
    compiler, modules = _compile(_ONE_ELEMENT_COPIES)
    _assert_move_leaves_render_moves(compiler, _entry(modules),
                                     _ONE_ELEMENT_COPIES)


def test_the_render_gate_fails_on_a_copied_tuple_element_moved(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Element 1 of `t` borrows `b`, so its record copies it while element 0
    # moves; a lowering that moved every element out of the pointer tuple
    # would move out of `b`, which the gate fails.
    monkeypatch.setattr(
        _lower_expressions, "_read_owned_elements",
        lambda e, rec, lc, arity: (True,) * arity)
    compiler, modules = _compile_program(_ONE_ELEMENT_COPIES)
    with pytest.raises(AssertionError, match=_COPY_MOVED):
        _assert_move_leaves_render_moves(compiler, _entry(modules),
                                         _ONE_ELEMENT_COPIES)


_MIXED_LITERAL = LEAF_PRELUDE + """def mixed(b: A) -> None:
    a = A(1)
    xs: list[tuple[A, A]] = [(a, b)]
    print(len(xs))
async def gen_mixed(b: A) -> int32:
    a = A(1)
    xs: list[tuple[A, A]] = [(a, b)]
    return len(xs)
"""


def _copying_members(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container elements lowered as a list-repeat's are: never moved."""
    original = _lower_expressions._elem_maybe_move

    def copying(el: object, e: object, lc: object, **kw: object) -> object:
        return original(el, e, lc, **{**kw, "suppress_move": True})
    monkeypatch.setattr(_lower_expressions, "_elem_maybe_move", copying)


def test_the_render_gate_fails_on_a_mixed_literal_copied_whole(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # A literal whose member `a` moves and `b` copies converts member by
    # member; a member lowering that copied the moving member too is what
    # the gate fails -- in a plain body and in a resumable frame.
    compiler, modules = _compile(_MIXED_LITERAL)
    _copying_members(monkeypatch)
    compiler, modules = _compile_program(_MIXED_LITERAL)
    with pytest.raises(AssertionError, match=_MOVE_NOT_RENDERED):
        _assert_move_leaves_render_moves(compiler, _entry(modules),
                                         _MIXED_LITERAL)


_FRAME_ONLY = LEAF_PRELUDE + """async def gen_mixed(b: A) -> int32:
    a = A(1)
    xs: list[tuple[A, A]] = [(a, b)]
    return len(xs)
"""


def test_the_render_gate_reads_a_frame_that_is_the_only_failing_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The copied-whole literal of a resumable frame alone fails the gate;
    # the frame as it lowers passes it.
    _compile(_FRAME_ONLY)
    _copying_members(monkeypatch)
    compiler, modules = _compile_program(_FRAME_ONLY)
    with pytest.raises(AssertionError, match=_MOVE_NOT_RENDERED):
        _assert_move_leaves_render_moves(compiler, _entry(modules),
                                         _FRAME_ONLY)


# `blocked` is refused (a record yielded into a union slot copies
# undeclared, generators/error_yield_record_into_union).
_FRAME_BLOCKED = LEAF_PRELUDE + """from typing import Iterator
def blocked(a: A) -> Iterator[A | B]:
    yield a
def gen(a: A) -> Iterator[int32]:
    yield a.v
"""


def test_the_render_gate_fails_where_a_reject_hides_a_frame() -> None:
    # A body that does not lower ends emission before the frames: the gate
    # fails rather than pass over them, unless the reject is a registered
    # one (`_UNLOWERED_BODIES`).
    compiler, modules = _compile_program(_FRAME_BLOCKED)
    with pytest.raises(AssertionError, match=_FRAMES_NOT_LOWERED):
        _assert_move_leaves_render_moves(compiler, _entry(modules),
                                         _FRAME_BLOCKED)


@pytest.mark.parametrize("entry", sorted(_UNLOWERED_BODIES))
def test_unlowered_bodies_still_reject(entry: tuple[str, str, str]) -> None:
    # Each excused reject is still reached by the test it names: a reject
    # the lowering lost leaves no stale excuse behind.
    test_name, _tracked = _UNLOWERED_BODIES[entry]
    test = globals()[test_name]
    _UNLOWERED_USED.discard(entry)
    marks = [m for m in getattr(test, "pytestmark", [])
             if m.name == "parametrize"]
    if marks:
        names = [n.strip() for n in marks[0].args[0].split(",")]
        for values in marks[0].args[1]:
            values = getattr(values, "values", values)
            test(**dict(zip(names, values if len(names) > 1
                            else (values,))))
    else:
        test()
    assert entry in _UNLOWERED_USED, entry


_ELEMENT_MOVE = LEAF_PRELUDE + """def keep(a: Own[A]) -> int32:
    return a.v
def give(t: tuple[Own[A], int32]) -> int32:
    return keep(t[0])
"""


def test_the_render_gate_reads_an_element_move(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # An element read whose record moves it out of its tuple, rendered as a
    # copy, fails the gate.
    original = _lower_expressions.verdict

    def no_element_move(ctx: object, e: object) -> object:
        return (SourceRecord({}) if isinstance(e, TpySubscript)
                else original(ctx, e))
    compiler, modules = _compile(_ELEMENT_MOVE)
    monkeypatch.setattr(_lower_expressions, "verdict", no_element_move)
    with pytest.raises(AssertionError, match=_MOVE_NOT_RENDERED):
        _assert_move_leaves_render_moves(compiler, _entry(modules),
                                         _ELEMENT_MOVE)


def _member_copy_warnings(source: str) -> list[tuple[int, str]]:
    """The copy warnings of `source` after the leaf prelude, by line."""
    _compiler, modules = _compile(LEAF_PRELUDE + source)
    return _copy_warnings(_entry(modules).analyzer.ctx)


def test_tuple_name_member_borrowed_leaf_warns_at_return_and_yield() -> None:
    # A tuple name inside a returned / yielded literal takes the name rule at
    # its member slot: the leaf its binding borrows copies, even at its last
    # use, exactly as `xs = [(t, 3)]` warns it.
    assert _member_copy_warnings("""\
from typing import Iterator
def ret(a: A) -> tuple[tuple[Own[A], int32], int32]:
    t = (a, 2)
    return (t, 3)
def gen(a: A) -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    t = (a, 2)
    yield (t, 3)
def stored(a: A) -> None:
    t = (a, 2)
    xs = [(t, 3)]
    print(len(xs))
def par(t: tuple[Own[A], A]) -> tuple[tuple[Own[A], Own[A]], int32]:
    return (t, 3)
def par_alone(t: tuple[Own[A], A]) -> tuple[Own[A], Own[A]]:
    return t
def par_scalar(a: Own[A], b: A) -> tuple[Own[A], Own[A], int32]:
    return (a, b, 3)
""") == [(4, "copies A into owned storage (tuple element 0.0)"),
         (7, "copies A into owned storage (tuple element 0.0)"),
         (10, "copies A into owned storage (tuple element 0.0)"),
         # A parameter's declared borrowed element copies at its last use,
         # as the parameter returned alone and its scalar twin copy it.
         (13, "copies A into owned storage (tuple element 0.1)"),
         (15, "copies A into owned storage (tuple element 1)"),
         (17, "copies A into owned storage (tuple element 1)")]


def test_tuple_name_member_param_owned_leaves() -> None:
    # A tuple parameter as a member: a `@nocopy` leaf the slot borrows does
    # not stop the move of the owned one, as in its scalar twin. A parameter
    # whose `Own` markers are only nested is passed by const reference, so
    # its member copies every owned leaf, at its last use too, under its
    # full path, as it does passed alone (`deep_arg_alone`) and returned
    # alone (`deep_alone`) (BUGS.md#nested-own-param-const-ref-abi).
    assert _member_copy_warnings("""\
def take_mixed(p: tuple[tuple[Own[A], N], int32]) -> None:
    pass
def mixed(t: tuple[Own[A], N]) -> None:
    take_mixed((t, 3))
def take_flat(p: tuple[Own[A], N, int32]) -> None:
    pass
def mixed_scalar(a: Own[A], n: N) -> None:
    take_flat((a, n, 3))
def deep_ret(t: tuple[tuple[Own[A], int32], int32]
             ) -> tuple[tuple[tuple[Own[A], int32], int32], int32]:
    return (t, 3)
def deep_alone(t: tuple[tuple[Own[A], int32], int32]
               ) -> tuple[tuple[Own[A], int32], int32]:
    return t
def deep_scalar(a: Own[A]) -> tuple[tuple[tuple[Own[A], int32], int32], int32]:
    return (((a, 2), 3), 4)
def take_deep(p: tuple[tuple[tuple[Own[A], int32], int32], int32]) -> int32:
    return p[1]
def deep_arg(t: tuple[tuple[Own[A], int32], int32]) -> int32:
    return take_deep((t, 3))
def deep_arg_live(t: tuple[tuple[Own[A], int32], int32]) -> int32:
    n = take_deep((t, 3))
    return n + t[1]
def deep_arg_scalar(a: Own[A]) -> int32:
    return take_deep((((a, 2), 3), 4))
def deep_mixed(t: tuple[tuple[Own[A], A], int32]
               ) -> tuple[tuple[tuple[Own[A], Own[A]], int32], int32]:
    return (t, 3)
def deep_mixed_alone(t: tuple[tuple[Own[A], A], int32]
                     ) -> tuple[tuple[Own[A], Own[A]], int32]:
    return t
class H:
    p: tuple[tuple[tuple[A, int32], int32], int32]
    def __init__(self) -> None:
        self.p = (((A(1), 2), 3), 4)
def deep_field(t: tuple[tuple[Own[A], int32], int32], h: H) -> None:
    h.p = (t, 3)
def deep_append(t: tuple[tuple[Own[A], int32], int32],
                xs: list[tuple[tuple[tuple[A, int32], int32], int32]]) -> None:
    xs.append((t, 3))
def take_one(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return p[1]
def deep_arg_alone(t: tuple[tuple[Own[A], int32], int32]) -> int32:
    return take_one(t)
""") == [(11, "copies A into owned storage (tuple element 0.0.0)"),
         (14, "copies A into owned storage (tuple element 0.0)"),
         (20, "copies A into owned storage (tuple element 0.0.0)"),
         (22, "copies A into owned storage (tuple element 0.0.0)"),
         (28, "copies A into owned storage (tuple element 0.0.0)"),
         (28, "copies A into owned storage (tuple element 0.0.1)"),
         (31, "copies A into owned storage (tuple element 0.0)"),
         (31, "copies A into owned storage (tuple element 0.1)"),
         (37, "copies A into field (tuple element 0.0.0)"),
         (40, "copies A into owned storage (tuple element 0.0.0)"),
         (44, "copies tuple[tuple[Own[A], int32], int32] into owned storage")]


def test_tuple_name_member_last_use_moves_live_copies() -> None:
    # The member's last use moves (no warning); a live or repeated one
    # copies each owned leaf, named by its full path.
    assert _member_copy_warnings("""\
from typing import Iterator
def last() -> tuple[tuple[Own[A], int32], int32]:
    t = (A(1), 2)
    return (t, 3)
def rep() -> tuple[tuple[Own[A], int32], tuple[Own[A], int32]]:
    t = (A(1), 2)
    return (t, t)
def live() -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    t = (A(1), 2)
    yield (t, 3)
    print(t[1])
def nested() -> tuple[tuple[tuple[Own[A], int32], int32], int32]:
    t = (A(1), 2)
    u = ((t, 1), 2)
    print(t[1])
    return u
""") == [(7, "copies A into owned storage (tuple element 0.0)"),
         (7, "copies A into owned storage (tuple element 1.0)"),
         (10, "copies A into owned storage (tuple element 0.0)"),
         (14, "copies A into owned storage (tuple element 0.0.0)")]


def test_tuple_name_member_settled_copy_names_its_path() -> None:
    # A member move a later binding (the loop's back edge) turns into a copy
    # warns the member's full path, as an eager copy does.
    assert _member_copy_warnings("""\
def f(a: A) -> None:
    t = (A(1), 2)
    for i in range(2):
        xs = [(3, t)]
        t = (a, 2)
        print(len(xs))
""") == [(4, "copies A into owned storage (tuple element 1.0)")]


def test_tuple_name_member_arg_slot_owns_what_it_marks() -> None:
    # A literal argument's tuple member copies only the leaves the
    # parameter's `Own` markers own: one a level down warns, and a leaf the
    # parameter borrows (`tuple[A, int32]` beside an `Own[A]`) does not, at
    # any depth -- as `t` passed alone to `tuple[A, int32]` does not.
    assert _member_copy_warnings("""\
def deep(v: tuple[tuple[Own[A], int32], int32]) -> int32:
    return v[1]
def marked(p: tuple[Own[tuple[A, int32]], int32]) -> int32:
    return p[1]
def mixed(p: tuple[Own[A], tuple[A, int32]]) -> int32:
    return p[1][1]
def deeper(p: tuple[tuple[Own[A], tuple[A, int32]], int32]) -> int32:
    return p[1]
def alone(p: tuple[A, int32]) -> int32:
    return p[1]
def deeper_own(p: tuple[tuple[Own[A], tuple[Own[A], int32]], int32]) -> int32:
    return p[1]
def f() -> None:
    t = (A(1), 2)
    a = A(5)
    print(deep((t, 3)))
    print(marked((t, 3)))
    print(mixed((A(3), t)))
    print(deeper(((A(3), t), 4)))
    print(alone(t))
    # A scalar a level deeper takes the same projected slot.
    print(deeper(((A(3), (a, 1)), 4)))
    print(deeper_own(((A(3), (a, 1)), 4)))
    print(t[1], a.v)
""") == [(16, "copies A into owned storage (tuple element 0.0)"),
         (17, "copies A into owned storage (tuple element 0.0)"),
         (23, "copies A into owned storage "
              "(argument 'p' tuple element 0.1.0)")]


def test_tuple_name_member_nocopy_last_use_moves() -> None:
    # A last-use `@nocopy` member moves as its scalar twin does: `return
    # (n, 3)` and `self.p = (n, 3)` render `{std::move(n), 3}`, a tuple name
    # member `{std::move(t), 3}`, at a return, a field and a container
    # alike, so no copy is made and nothing errors.
    for body in ("""\
def f() -> tuple[Own[N], int32]:
    n = N(1)
    return (n, 3)
""", """\
class H:
    p: tuple[N, int32]
    def __init__(self) -> None:
        n = N(1)
        self.p = (n, 3)
""", """\
def f() -> tuple[tuple[Own[N], int32], int32]:
    t = (N(1), 2)
    return (t, 3)
""", """\
class H:
    p: tuple[tuple[N, int32], int32]
    def __init__(self) -> None:
        t = (N(1), 2)
        self.p = (t, 3)
""", """\
def f(xs: list[tuple[tuple[N, int32], int32]]) -> None:
    t = (N(1), 2)
    xs.append((t, 3))
"""):
        _compile(LEAF_PRELUDE + body)


def test_tuple_walrus_member_reads_as_its_name() -> None:
    # A walrus member is its target name's read, which stays live: a tuple
    # one copies each owned leaf, as its scalar twin copies, a member and a
    # whole container element alike.
    assert _member_copy_warnings("""\
def f(a: A) -> int32:
    t = (A(1), 2)
    xs = [((w := t), 3)]
    ys = [(w2 := t)]
    zs = [((v := a), 3)]
    return len(xs) + len(ys) + len(zs) + w[1] + w2[1] + v.v
""") == [(3, "copies A into owned storage (tuple element 0.0)"),
         (4, "copies A into owned storage (tuple element 0)"),
         (5, "copies A into owned storage (tuple element 0)")]


def test_tuple_walrus_member_at_return_and_field() -> None:
    # A walrus member names the binding it just made, which outlives the
    # store: it copies at a return and at a field, a tuple one per owned
    # leaf and its scalar twin alike.
    assert _member_copy_warnings("""\
def ret() -> tuple[tuple[Own[A], int32], int32]:
    return ((t := (A(1), 2)), 3)
def ret_scalar() -> tuple[Own[A], int32]:
    return ((a := A(1)), 3)
class H:
    p: tuple[tuple[A, int32], int32]
    q: tuple[A, int32]
    def __init__(self) -> None:
        self.p = ((t := (A(1), 2)), 3)
        self.q = ((a := A(1)), 3)
        print(t[1], a.v)
""") == [(9, "copies A into field (tuple element 0.0)"),
         (10, "copies A into field (tuple element 0)"),
         (2, "copies A into owned storage (tuple element 0.0)"),
         (4, "copies A into owned storage (tuple element 0)")]


def test_nocopy_member_at_yield_and_own_arg() -> None:
    # A value tuple holding a `@nocopy` leaf cannot be yielded, a tuple
    # member and its scalar twin alike; the scalar twins that move a last
    # use -- into an `Own[tuple[...]]` yield and an `Own`-marked argument
    # element -- compile.
    for body in ("""\
def gen() -> Iterator[tuple[tuple[Own[N], int32], int32]]:
    t = (N(1), 2)
    yield (t, 3)
""", """\
def gen() -> Iterator[tuple[Own[N], int32]]:
    n = N(1)
    yield (n, 3)
"""):
        with pytest.raises(SemanticError,
                           match="Generator cannot yield non-copyable"):
            _compile(LEAF_PRELUDE + "from typing import Iterator\n" + body)
    records, _w, _ctx = _leaf_records("""\
from typing import Iterator
def gen() -> Iterator[Own[tuple[N, int32]]]:
    n = N(1)
    yield (n, 3)
def take_n(p: tuple[Own[N], int32]) -> int32:
    return p[1]
def arg() -> int32:
    n = N(1)
    return take_n((n, 3))
""")
    assert _perms(records[(4, "TpyTupleLiteral")]) == {(0,): P.MOVE}
    # A live one would copy it, as the `Own`-marked member's twin would.
    with pytest.raises(SemanticError,
                       match=r"cannot copy non-copyable type 'N' into owned "
                             r"storage \(tuple element 0\)"):
        _compile(LEAF_PRELUDE + """\
from typing import Iterator
def gen() -> Iterator[Own[tuple[N, int32]]]:
    n = N(1)
    yield (n, 3)
    print(n.v)
""")


def test_tuple_name_member_owning_local_nocopy_sibling() -> None:
    # An owning local's member moves at its last use although a leaf the
    # slot borrows is `@nocopy`; a live one copies the owned leaf.
    assert _member_copy_warnings("""\
def take_nc(p: tuple[tuple[Own[A], N], int32]) -> int32:
    return p[1]
def last() -> int32:
    t = (A(1), N(2))
    return take_nc((t, 3))
def live() -> int32:
    t = (A(1), N(2))
    n = take_nc((t, 3))
    return n + t[0].v
""") == [(8, "copies A into owned storage (tuple element 0.0)")]


def test_tuple_name_borrowing_local_nested_own_slot() -> None:
    # A local borrowing a nested leaf copies it into a nested `Own` slot,
    # returned or passed alone.
    assert _member_copy_warnings("""\
def take_deep(p: tuple[tuple[Own[A], int32], int32]) -> int32:
    return p[1]
def ret(a: A) -> tuple[tuple[Own[A], int32], int32]:
    t = ((a, 1), 2)
    return t
def arg(a: A) -> int32:
    t = ((a, 1), 2)
    return take_deep(t)
""") == [(4, "copies A into owned storage (tuple element 0.0)"),
         (7, "copies A into owned storage (tuple element 0.0)")]


def test_tuple_name_member_settled_nested_leaf() -> None:
    # A later binding borrowing a member's nested leaf turns its move into a
    # copy at settlement, warned under the full path, as the scalar twin's.
    assert _member_copy_warnings("""\
def deep(b: tuple[tuple[A, int32], int32]) -> None:
    t = ((A(1), 2), 3)
    for i in range(2):
        xs = [(4, t)]
        t = b
        print(len(xs))
def scalar(b: A) -> None:
    a = A(1)
    for i in range(2):
        xs = [(4, a)]
        a = b
        print(len(xs))
""") == [(4, "copies A into owned storage (tuple element 1.0.0)"),
         (10, "copies A into owned storage (tuple element 1)")]


def test_tuple_name_settled_nested_leaf_alone() -> None:
    # A standalone name at a container whose later binding borrows a nested
    # leaf copies it at settlement under its path, with the binding that
    # made it non-owning, as the top-level twin names it.
    _compiler, modules = _compile(LEAF_PRELUDE + """\
def deep(b: tuple[tuple[A, int32], int32]) -> None:
    t = ((A(1), 2), 3)
    for i in range(2):
        xs = [t]
        t = b
        print(len(xs))
def flat(b: tuple[A, int32]) -> None:
    t = (A(1), 3)
    for i in range(2):
        xs = [t]
        t = b
        print(len(xs))
""")
    assert [(d.loc.line - _LEAF_LINES, d.message)
            for d in _entry(modules).analyzer.ctx.diagnostics
            if d.level.value == "warning" and "cop" in d.message] == [
        (4, "copies A into owned storage (tuple element 0.0); use copy() "
            "to make this explicit ('t' is bound to existing storage at "
            f"line {_LEAF_LINES + 5})"),
        (10, "copies A into owned storage (tuple element 0); use copy() "
             "to make this explicit ('t' is bound to existing storage at "
             f"line {_LEAF_LINES + 11})")]


def test_tuple_name_member_unowned_nocopy_leaf_param() -> None:
    # A parameter member whose `@nocopy` leaf the slot borrows: its last use
    # is unwarned, though the render copies the tuple
    # (BUGS.md#tuple-literal-name-member-silent-copy, face 1).
    assert _member_copy_warnings("""\
def take(x: tuple[tuple[N, Own[A]], int32]) -> int32:
    return x[1]
def f(t: tuple[N, Own[A]]) -> int32:
    return take((t, 3))
""") == []


_TUPLE_CASES = Path(__file__).resolve().parents[2] / "tests/cases/tuple"


def _case_member_records(case: str) -> dict[int, dict[tuple[int, ...],
                                                      LeafPermission]]:
    """The records of the tuple literals of case `case`, by line."""
    source = (_TUPLE_CASES / case / "src/main.py").read_text()
    _compiler, modules = _compile(source)
    return {e.loc.line: _perms(rec) for e, rec
            in _entry(modules).analyzer.ctx.source_records.items()
            if e.loc is not None
            and isinstance(peel_value_wrappers(e), TpyTupleLiteral)}


def test_tuple_name_member_records_of_the_cases() -> None:
    # The member records of the literal-name-member cases: a live or
    # repeated member copies each leaf its slot owns, as warned; a last-use
    # member and the `u = copy(t)` opt-out move (the C++ still copies:
    # BUGS.md#tuple-literal-name-member-silent-copy).
    W, M = P.WARNED, P.MOVE
    copies = _case_member_records("literal_name_member_copies")
    assert {line: copies[line] for line in (20, 26, 41, 50, 67, 73, 89)} == {
        20: {(0, 0): W, (1, 0): W},
        26: {(0, 0): W, (1, 0): W},
        41: {(0, 0): W, (1, 0): W},
        50: {(0, 0, 0): W, (1, 0): W},
        67: {(0, 0): W},
        73: {(0, 0): W, (1, 0): W},
        89: {(0, 0): W}}
    moves = _case_member_records("literal_name_member_moves")
    assert {line: moves[line] for line in (19, 28, 37, 44)} == {
        19: {(0, 0): M},
        28: {(0, 0): M, (1, 0): M},
        37: {(0, 0): M},
        44: {(0, 0): M, (1, 0): M}}
    # The @nocopy twin errors where its record would copy.
    with pytest.raises(SemanticError, match=re.escape(
            "cannot copy non-copyable type 'N' into owned storage "
            "(tuple element 0.0)")):
        _case_member_records("error_literal_name_member_nocopy")


def test_a_hoisted_local_moves_at_its_last_use() -> None:
    # A `try`-hoisted local is a slot its binds re-point, which the C++
    # moves out of where the binding owns it, scalar and tuple alike: no
    # warning.
    records, warnings, _e = _probe("""\
def mk(k: int32) -> Own[A]:
    if k > 100:
        raise ValueError("x")
    return A(k)
def ptr(out: list[A], k: int32) -> None:
    try:
        y = mk(k)
    except ValueError:
        y = A(7)
    out.append(y)
def ptr_tuple(out: list[tuple[A, int32]], k: int32) -> None:
    try:
        t = (mk(k), 1)
    except ValueError:
        t = (A(7), 2)
    out.append(t)
""")
    assert _perms(records[(10, "TpyName")]) == {(): P.MOVE}
    assert _perms(records[(16, "TpyName")]) == {(0,): P.MOVE}
    assert not [w for w in warnings if "cop" in w]


def test_a_module_loop_variable_rebound_to_borrowed_storage_copies() -> None:
    # The module-level loop site's own rebind borrows: the append copies,
    # warned with the binding inside the loop, as the function twin is.
    records, warnings, _e = _probe("""\
from typing import Iterator
def mk() -> Iterator[Own[A]]:
    yield A(1)
b = A(9)
out: list[A] = []
for a in mk():
    a = b
    out.append(a)
""")
    assert _perms(records[(8, "TpyName")]) == {(): P.WARNED}
    assert [w for w in warnings if "cop" in w] == [
        "copies A into owned storage; use copy() to make this explicit "
        f"('a' is bound to existing storage at line {_LEAF_LINES + 7})"]


def test_the_agreement_check_fails_a_warning_on_a_passing_record() -> None:
    # A warning whose source's record copies nothing contradicts it: the
    # check fails, a binding spanning the statement or not.
    source = LEAF_PRELUDE + """\
def keep(a: A, xs: list[A]) -> None:
    ys = xs
    ys.append(a)
"""
    _compiler, modules = _compile_program(source)
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    warned = [e for e, rec in ctx.source_records.items()
              if any(leaf.permission is P.WARNED
                     for leaf in rec.leaves.values())]
    assert warned
    for e in warned:
        ctx.source_records[e] = SourceRecord({
            p: replace(leaf, permission=P.PASS)
            for p, leaf in ctx.source_records[e].leaves.items()})
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(entry, [], source)


def test_a_local_literal_warns_the_lending_call_it_copies() -> None:
    # A local holds a call member by value: the copy of a lent result is
    # its record's WARNED leaf, and the warning says so -- the twin of
    # `xs.append(k.get())`.
    records, warnings, _ctx = _leaf_records("""\
class K:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def get(self) -> A:
        return self.a
def f(k: K) -> None:
    t = (k.get(), 1)
    t[0].v = 2
    xs: list[A] = []
    xs.append(k.get())
""")
    assert _perms(records[(8, "TpyTupleLiteral")]) == {(0,): P.WARNED}
    assert [w for w in warnings if "cop" in w] == [
        "copies A into owned storage (tuple element 0); use copy() to make "
        "this explicit",
        "copies A into owned storage; use copy() to make this explicit"]


def test_the_agreement_check_fails_a_copied_leaf_no_warning_names() -> None:
    # A record copying a leaf no diagnostic names contradicts it, as a
    # warning on a passing record does.
    source = LEAF_PRELUDE + """\
def keep(a: A, xs: list[A]) -> None:
    ys = xs
    ys.append(a)
"""
    _compiler, modules = _compile_program(source)
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    ctx.diagnostics[:] = [d for d in ctx.diagnostics
                          if "cop" not in d.message]
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(entry, [], source)


_FILED_SILENT_HEDGES = """\
from typing import Iterator
from tpy import int32
def ident[T](a: T) -> T:
    return a
def mix[T](items: list[T], extra: Iterator[T]) -> Iterator[T]:
    for x in items:
        yield x
    for y in extra:
        yield y
def keep_rebound[T](xs: list[T], x: T, z: T) -> None:
    y = ident(x)
    xs.append(y)
    y = ident(z)
    xs.append(y)
"""


def test_filed_hedged_copies_without_a_diagnostic_are_the_only_exemptions(
        ) -> None:
    # Each record here copies a leaf (HEDGED) that no diagnostic names: the
    # filed silent copies `_DIAGNOSTIC_DISAGREEMENTS` names. Without that
    # entry the agreement check fails them, as any other silent copy.
    compiler, modules = _compile(_FILED_SILENT_HEDGES)
    ctx = _entry(modules).analyzer.ctx
    hedged = sorted(e.loc.line for e, rec in ctx.source_records.items()
                    if e.loc is not None and any(
                        leaf.permission is P.HEDGED
                        for leaf in rec.leaves.values()))
    assert hedged == [7, 9, 11, 13]
    assert not [d for d in ctx.diagnostics if "cop" in d.message]
    renamed = _FILED_SILENT_HEDGES.replace("def mix[T]", "def mix2[T]")
    _c, modules2 = _compile_program(renamed)
    with pytest.raises(AssertionError):
        _assert_records_agree_with_warnings(_entry(modules2), [], renamed)


def test_a_none_member_records_no_copied_leaf() -> None:
    # A `None` holds no object: neither a local's binding of a `None`
    # member, an owning result's member, nor a whole `None` at an Optional
    # tuple result records a copy.
    records, _warnings, _ctx = _leaf_records("""\
def f() -> None:
    t: tuple[A | None, int32] = (None, 1)
    print(t[1])
def g() -> tuple[Own[A] | None, int32]:
    return (None, 1)
def h(a: A, k: bool) -> tuple[Own[A], A] | None:
    if k:
        return None
    return (A(1), a)
""")
    for key in ((2, "TpyTupleLiteral"), (5, "TpyTupleLiteral"),
                (8, "TpyNoneLiteral")):
        assert not [p for p, leaf in records[key].leaves.items()
                    if leaf.permission.copies], key


def test_a_value_leaf_does_not_demote_a_move() -> None:
    # A VALUE leaf's copy and move are one: beside a moved `Own[A]` leaf
    # it leaves the record a whole move, not an element-by-element one.
    rec = SourceRecord({(0,): Leaf(False, True, P.MOVE),
                        (1,): Leaf(True, True, P.VALUE)})
    assert rec.disposition is Disposition.MOVE
    assert rec.moves_whole and rec.element_moves(1)
    assert not rec.copied and rec.copies_at((1,)) is False

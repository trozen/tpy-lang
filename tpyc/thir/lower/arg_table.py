"""Table-driven argument admission for the THIR call gates.

Each callee family used to spell its own `or`-ladder of source-shape rows, so
the same shape was written once per family and a widening landed in one ladder
while silently missing every sibling that carried the same row. This module
holds the shapes those ladders fold into: a family is an ordered tuple of
cells, and a `(row, family)` pair is the one place that shape's admission is
decided.

Same idea as `_MethodRecvFamily` one level up, which pairs a family's SHAPE
gate with its ARG gate so the two cannot drift; this extends it from *which
gate* to *which rows*.

ROW ORDER IS LOAD-BEARING. `witness()` fires as a side effect while the gate
walks, and the journal is per-attempt, so reordering rows changes the
recorded face census even when admission is identical. `_ArgSink.rows` is an
ordered tuple, never a set or a dict -- and the walk is a linear scan of that
tuple with the predicates' own short-circuit, never a dispatch keyed on a
computed source classifier, which would have to classify every argument
before it could dispatch.

`note` is the family's verbatim `note_detail` tail: it composes into the
reject tag the diagnostic reports, so the string is user-visible and a family
keeps the tag it had.

THE TABLES LIVE WITH THEIR PREDICATES, not here: every row's predicate is
defined in `checks.py` / `predicates.py`, and `checks.py` needs the finished
sinks at module level for `_METHOD_RECV_FAMILY_TABLE`. Holding the tables
here would close an import cycle. Only the shapes, the walk and the reach
tally are family-agnostic, so only they live in this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, NamedTuple

from ..faces import witness
from ..reject import note_detail
from ...compilation_context import get_current_compiler

if TYPE_CHECKING:
    from ...compiler import Compiler
    from ...parse.nodes import TpyExpr
    from ...typesys import TpyType


class _ArgReq(NamedTuple):
    """One argument's admission question, built once per gate call and passed
    positionally to every row -- the reason a row predicate can have a single
    uniform signature while the underlying predicates keep their own."""
    a: 'TpyExpr'
    ptype: 'TpyType | None'
    locals_: dict
    analyzer: object
    param_names: 'frozenset[str] | set[str]'
    narrowed: 'frozenset[str] | set[str]'
    # `mutated_slots`: THIS position's slot is a mutated `T&` and the family
    # declines a temp/prvalue source there -- read by the pass-through rows.
    # The family flag (`_ArgSink.mutated_slots`) says whether the family holds
    # the rule at all; `arg_ok` folds it with the caller's per-argument
    # `is_mutated`, since which slot is mutated is a property of the CALL, not
    # of the family. Only the ctor ladder holds the rule today; extending it
    # to the rest moves which bodies route, so it is a change of its own
    # rather than part of the fold.
    # `temps_ok` is NOT a family constant -- it says whether THIS position has
    # a flush slot -- so the walk takes it per call.
    mutated_slots: bool
    temps_ok: bool
    # The enclosing body's owning-tuple locals. Per-call context like
    # `locals_` / `narrowed`, not a capability: the borrow-tuple rows read it
    # to tell a storage-form tuple name from a borrow-form one.
    storage_tuple_locals: 'frozenset[str] | set[str]' = frozenset()
    # Whether a captured `self` in a lambda arg has a receiver handle to
    # copy at THIS call site. Per-call context, not a capability: it is a
    # property of the enclosing body's receiver, and only the plain
    # free-call site knows it.
    self_capturable: bool = False
    # The slot BEFORE type-param substitution, for the generic free-call
    # family: its rows decide against the substituted slot (which is what
    # `ptype` carries there), but its prologue and its borrow-tuple row ask
    # what the UNSUBSTITUTED slot was. Per-call context like `locals_`.
    open_ptype: 'TpyType | None' = None
    # This argument's POSITION and the resolved callee, for the rows that
    # decide against the signature rather than the slot alone (the
    # deep-const-borrow verdict, the same-record ctor rvalue, the generic
    # record method's T-slot temp). Per-call context like `locals_`, not a
    # capability: the position is a property of the call, not the family.
    index: int = -1
    overload: object = None
    # Whether the callee is a generator/coro FACTORY, whose ref-slot args
    # are borrowed by the frame and so hoist a scope-local. Per-call
    # context: the enclosing call's resolved fi decides it.
    frame_capturing: bool = False
    # The callee's mutation facts are UNKNOWN (a resolved fi that carries no
    # `mutated_params`), which the ctor family refuses at a `String&` slot
    # rather than reproduce the known miscompile for it. Distinct from
    # `mutated_slots=False`, which asserts the slot is not mutated.
    mutation_unknown: bool = False
    # Four facts the LOWERING CONTEXT holds and the move-slice rows read.
    # Extracted as discrete facts, deliberately, rather than carried as the
    # context itself: this module has no `_LowerCtx` import (it would close an
    # import cycle) and a row that could reach the whole context could reach
    # anything on it. `inline_narrowed` / `movable_locals` / `pointers` are
    # name sets exactly like `narrowed`; `func_name` names the enclosing
    # body.
    inline_narrowed: 'frozenset[str] | set[str] | dict' = frozenset()
    movable_locals: 'frozenset[str] | set[str]' = frozenset()
    pointers: 'frozenset[str] | set[str]' = frozenset()
    func_name: 'str | None' = None


class _ArgRow(NamedTuple):
    """One (row, family) cell.

    `row` is the SHAPE identity and is the cross-family join key: two
    families naming the same row must hold the same `fn`, which `register_sink`
    enforces at import time. That is the whole invariant -- a shape's flush
    requirement, Own-slot requirement and guards are stated once and cannot
    drift between callee families except where `extra` records an explicit,
    commented override.

    `extra` is a PRE-guard, evaluated before `fn`. `face` is the witness tag
    the cell fires on admission, once `fn` has passed.

    `decisive` marks the cell whose `extra` gate makes `fn` the WHOLE verdict:
    a False from `fn` ends the walk instead of falling through to the rows
    below. That is the `if <slot gate>: ...; return <shape check>` block a
    ladder spells at a POSITION in its chain -- `pre` cannot express it
    (`pre` runs ahead of every row) and a plain row cannot (a plain row only
    ever admits). A decisive reject is the family's verdict, so it does not
    reach `note` either, exactly as the ladder's bare `return False` did not.
    """
    row: str
    fn: Callable[[_ArgReq], bool]
    extra: 'Callable[[_ArgReq], bool] | None' = None
    face: 'str | None' = None
    decisive: bool = False


class _ArgSink(NamedTuple):
    """One callee family's ordered row tuple plus its capability flags.

    `note` is the family's reject tail. A few families derive it from the
    argument instead of spelling a constant (the native/template callees rank
    their reject mass by slot shape), so it takes a callable too. None means
    the CALLER owns the reject detail: the ctor gate is reached from three
    call sites that tag their own rejects (`note_detail` is set-if-empty, so
    a tail spelled here would take the tag those sites record).

    `pre` is a family PROLOGUE: a slot-keyed dispatch that runs ahead of the
    rows and, when it fires, is the whole verdict -- the shape a ladder spells
    as an early `return` before its `or`-chain. It returns None to fall
    through to the rows. It is deliberately family-level: unlike `extra` it
    can REJECT, so a row tuple (whose cells only ever admit) cannot express
    it. It lives inside the walk rather than in the caller so that a family's
    WHOLE verdict is one call: a prologue hoisted to the gate would leave the
    legs that never reach a row outside anything reading `_ArgSink`.
    """
    family: str
    rows: tuple[_ArgRow, ...]
    note: 'str | Callable[[_ArgReq], str] | None'
    mutated_slots: bool = False
    pre: 'Callable[[_ArgReq], bool | None] | None' = None


_ROW_FNS: dict[str, Callable[[_ArgReq], bool]] = {}
# Every sink built at import time, in registration order. An IMPORT-time
# registry, not per-compilation state: it is the set of families that exist,
# which the reach floor reads so no family list has to be maintained by hand
# when a sink is added.
_SINKS: list[_ArgSink] = []


def register_sink(sink: _ArgSink) -> _ArgSink:
    """Record a family's rows and enforce the one-cell-per-shape invariant.

    Without this the table is just the ladders relocated: two families could
    name the same row and reach different predicates, which is the drift the
    fold exists to remove. The check is an `assert`, so it evaporates under
    `python -O` and is not what keeps a bad table out of the tree. What does
    is that the check runs on IMPORT of the two table modules, and
    `tests/conftest.py` imports them -- so it fires during collection of any
    test session, before a single case runs.
    """
    seen: set[str] = set()
    for row in sink.rows:
        assert row.row not in seen, (
            f"{sink.family}: duplicate row {row.row!r} -- a family decides a "
            f"shape in exactly one cell")
        seen.add(row.row)
        prev = _ROW_FNS.get(row.row)
        assert prev is None or prev is row.fn, (
            f"row {row.row!r} reaches two different predicates "
            f"({prev} vs {row.fn}) -- same row name means same shape, so "
            f"either share the adapter or give this cell its own row name")
        _ROW_FNS[row.row] = row.fn
    _SINKS.append(sink)
    return sink


def registered_families() -> tuple[str, ...]:
    """Every family that has a sink, in registration order."""
    return tuple(sink.family for sink in _SINKS)


def registered_cells() -> frozenset[tuple[str, str]]:
    """Every `(family, row)` cell in the table -- the coverage denominator."""
    return frozenset((sink.family, row.row)
                     for sink in _SINKS for row in sink.rows)


def arg_ok(sink: _ArgSink, a: 'TpyExpr', ptype: 'TpyType | None',
           locals_: dict, analyzer, *,
           param_names: 'frozenset[str] | set[str]',
           narrowed: 'frozenset[str] | set[str]',
           temps_ok: bool,
           storage_tuple_locals: 'frozenset[str] | set[str]' = frozenset(),
           self_capturable: bool = False,
           open_ptype: 'TpyType | None' = None,
           index: int = -1,
           overload: object = None,
           frame_capturing: bool = False,
           is_mutated: bool = False,
           mutation_unknown: bool = False,
           inline_narrowed: 'frozenset[str] | set[str] | dict' = frozenset(),
           movable_locals: 'frozenset[str] | set[str]' = frozenset(),
           pointers: 'frozenset[str] | set[str]' = frozenset(),
           func_name: 'str | None' = None
           ) -> bool:
    """Walk `sink`'s rows in order; `note_detail(sink.note)` on no match."""
    req = _ArgReq(a, ptype, locals_, analyzer, param_names, narrowed,
                  sink.mutated_slots and is_mutated, temps_ok,
                  storage_tuple_locals, self_capturable, open_ptype, index,
                  overload, frame_capturing, mutation_unknown,
                  inline_narrowed, movable_locals, pointers, func_name)
    return _walk(sink, req)


def _walk(sink: _ArgSink, req: _ArgReq) -> bool:
    if sink.pre is not None:
        pre = sink.pre(req)
        if pre is not None:
            _reach(sink.family, PROLOGUE_CELL)
            return pre
    for row in sink.rows:
        if row.extra is not None and not row.extra(req):
            continue
        if not row.fn(req):
            if row.decisive:
                _reach(sink.family, "!" + row.row)
                return False
            continue
        _reach(sink.family, row.row)
        if row.face is not None:
            witness(row.face)
        return True
    _reach(sink.family, NO_CELL)
    note = sink.note
    if note is None:
        return False
    return note_detail(note if isinstance(note, str) else note(req))


# ---------------------------------------------------------------------------
# The reach tally.
#
# Nothing is replayed and nothing is compared: this answers only whether a
# corpus reaches the table at all. A family no corpus reaches has no witness,
# and reads exactly like a family that is always right.
#
# The unit is the DECIDING cell: the row that admitted, the decisive row that
# rejected on its own
# verdict (`!row`), the prologue leg (`<prologue>`), or the family tail with
# no cell reached (`<none>`). So the keys a run produces are a subset of
# `registered_cells()` plus the two family-level sentinels, and the families
# it names are the families the run reached.
#
# NOT journalled per lowering attempt the way the face census is: a verdict
# recorded in a body that then REJECTS still decided something -- often it is
# the very rejection -- so rolling it back would hide the reach that matters
# most.
# ---------------------------------------------------------------------------

PROLOGUE_CELL = "<prologue>"
NO_CELL = "<none>"


def _reach(family: str, cell: str) -> None:
    compiler = get_current_compiler()
    if compiler is not None:
        t = compiler._thir_arg_reached
        key = (family, cell)
        t[key] = t.get(key, 0) + 1


def reached(compiler: 'Compiler') -> dict[tuple[str, str], int]:
    """This compilation's deciding-cell counts. Kept on the Compiler like
    every other per-compilation tally, so a harness reads one run's numbers
    rather than a process-wide accumulation."""
    return dict(compiler._thir_arg_reached)


def reached_families(compiler: 'Compiler') -> set[str]:
    """The families this compilation's arg gates actually reached."""
    return {family for family, _ in compiler._thir_arg_reached}

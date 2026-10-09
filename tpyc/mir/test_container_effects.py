"""Container places in the analyses: an element borrow, an iterator or a
Span lies inside its container; a shape write (`[structure]`) reaches all of
it, an elements write what lies in `[elements]`, a move out of owned storage
everything in it; the container object itself, which every holder of it
keeps, is never reached by its own shape or elements write."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from ..compilation_context import activate_compiler
from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import BOOL, INT32, STR, NominalType, OwnType, RefType, VoidType, return_representation
from .call_contract import (
    MIRCallSummary, MIRParameterBinding, MIRParameterWrite, MIRReturnOrigin, MIRSummaryState, stub_summary,
)
from .coverage import call_write_places
from .collect import MIRBodyVerdict, MIRVerdictStatus, analyze_body, line_facts
from .definitions import MIRDefinitions, MIROwnedLeafDefinition
from .dependencies import MIRReferent, analyze_dependencies, resolve_referents
from .liveness import analyze_liveness
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRCallStmt, MIRConstant,
    MIRConstruct, MIRContainerElements, MIRContainerLayout, MIRContainerStructure, MIRDeref, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMove,
    MIRNotCovered, MIRPlace, MIRPoint, MIRRead, MIRRecordLayout, MIRRecordWrite, MIRRecordWriteMode, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRTupleElement, MIRValueKind,
)
from .presence import _analyze_presence
from .retention import MIRRetention, affects, analyze_retention, may_overlap
from .storage import analyze_storage, storage_destination
from .storage_evidence import certify_storage_origins
from .summaries import summarize_function
from .validate import MIRPresenceError, MIRValidationError, body_may_raise

B = MIRBodyId("containers", "effects")
CELL = NominalType("Cell", _module_qname="containers.Cell")
BAG = NominalType("Bag", _module_qname="containers.Bag")
VALUE = MIRField(MIRFieldId(CELL, "value"), INT32)
CELLS = NominalType("list", (CELL,), _module_qname="builtins.list")
INTS = NominalType("list", (INT32,), _module_qname="builtins.list")
SPAN = NominalType("Span", (INT32,), _module_qname="tpy.Span")
ITEMS = MIRField(MIRFieldId(BAG, "items"), CELLS)
OTHER = MIRField(MIRFieldId(BAG, "other"), CELL)
OTHERS = MIRField(MIRFieldId(BAG, "others"), CELLS)
RECORD = MIRRecordLayout(CELL, (VALUE,), True, True)
BAG_RECORD = MIRRecordLayout(BAG, (ITEMS, OTHERS), True, True)
CELL_LAYOUT = MIRContainerLayout(MIRTupleElement(CELL, MIRValueKind.BORROWED))
INT_LAYOUT = MIRContainerLayout(MIRTupleElement(INT32))
ELEMENTS, STRUCTURE = MIRContainerElements(), MIRContainerStructure()

SOURCE = '''from tpy import int32, Span

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def grow(ps: list[Cell]) -> None:
    ps.append(Cell(1))

def first(ps: list[Cell]) -> Cell:
    return ps[0]

class Bag:
    items: list[Cell]
    def __init__(self):
        self.items = []

def push(b: Bag) -> None:
    b.items.append(Cell(2))

def span_sum(s: Span[int32]) -> int32:
    t = 0
    for v in s:
        t += v
    return t
'''


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return compiler, modules, ctx


@pytest.fixture(autouse=True)
def active(compiled):
    compiler, modules, _ = compiled
    # The per-test reset drops the stub flags latched on the static TypeDefs
    # (`borrowing_view`, which makes a Span a view): latch them again.
    for module in modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(compiler):
        yield


def sid(index: int) -> MIRSlotId:
    return MIRSlotId(B, index)


def bid(index: int) -> MIRBlockId:
    return MIRBlockId(B, index)


def param(index: int, typ=CELLS, layout=CELL_LAYOUT, *, readonly: bool = False, name: str | None = None) -> MIRSlot:
    return MIRSlot(sid(index), typ, MIRSlotKind.PARAMETER, name or f"p{index}", form=th.Form.BORROW,
                   value_kind=MIRValueKind.BORROWED_CONTAINER, readonly=readonly, container_layout=layout,
                   passing=ParamPassing.CONST_REF if readonly else ParamPassing.MUT_REF)


def holder(index: int, typ=CELL, kind: MIRSlotKind = MIRSlotKind.LOCAL, *, name: str | None = None) -> MIRSlot:
    # A Span holder carries the layout of the region it views.
    return MIRSlot(sid(index), typ, kind, name, form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED,
                   container_layout=INT_LAYOUT if typ == SPAN else None)


def owned(index: int, typ=CELL, layout: MIRContainerLayout | None = None, *, name: str | None = None) -> MIRSlot:
    return MIRSlot(sid(index), typ, MIRSlotKind.LOCAL if name else MIRSlotKind.TEMPORARY, name,
                   form=th.Form.STORAGE, value_kind=MIRValueKind.OWNED, container_layout=layout,
                   storage_duration=MIRStorageDuration.BODY)


def scalar(index: int, typ=INT32, kind: MIRSlotKind = MIRSlotKind.TEMPORARY) -> MIRSlot:
    return MIRSlot(sid(index), typ, kind)


def assign(target, value, *projections, write: MIRRecordWrite | None = None) -> MIRAssign:
    return MIRAssign(MIRPlace(target if isinstance(target, MIRSlotId) else sid(target), projections),
                     value, storage_write=write)


def place(index: int, *projections) -> MIRPlace:
    return MIRPlace(sid(index), projections)


def ref(index: int, *projections, external: bool = True) -> MIRReferent:
    return MIRReferent(place(index, *projections), external)


def append_summary(container=CELLS, element=CELL, passing: ParamPassing = ParamPassing.OWN) -> MIRCallSummary:
    """`list.append` as its stub derives it: a mutable receiver that does
    not preserve references writes its shape; the element is moved in."""
    signature = th.THIRCallableSignature((container, OwnType(element)), VoidType(),
                                         passings=(ParamPassing.MUT_REF, passing),
                                         return_representation=return_representation(VoidType()))
    callee = th.THIRStubCallee(th.THIRStubIdentity("builtins.list.append", signature.param_types),
                               signature, None, (False, False), receiver=True)
    summary = stub_summary(callee)
    assert isinstance(summary, MIRCallSummary), summary
    assert summary.writes == frozenset({MIRParameterWrite(0, (STRUCTURE,))})
    return summary


def body(slots, *blocks, records=(RECORD,), borrowed_result=None) -> MIRFunction:
    calls = [call for block in blocks for stmt in block.statements
             if (call := stmt.call if isinstance(stmt, MIRCallStmt) else stmt.value if isinstance(stmt, MIRAssign)
                 and isinstance(stmt.value, MIRCall) else None) is not None]
    summaries = tuple({id(c.summary): c.summary for c in calls}.values())
    return MIRFunction(B, INT32 if borrowed_result is None else borrowed_result.type, tuple(slots), tuple(blocks),
                       bid(0), records=records, call_summaries=summaries,
                       exceptional_exits=body_may_raise(blocks, {s.id: s for s in slots}, None),
                       borrowed_result=borrowed_result)


def analyze(fn: MIRFunction) -> MIRRetention:
    live = analyze_liveness(fn)
    result = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn))
    assert isinstance(result, MIRRetention), result
    return result


def conflicts(fn: MIRFunction) -> set[tuple[MIRPlace, MIRPlace]]:
    """(holder, retained place) of every conflict."""
    return {(c.holder, c.retained.place) for c in analyze(fn).conflicts}


# --- the affects rule over referents --------------------------------------------

@pytest.mark.parametrize(("written", "retained", "expected"), [
    # A shape write reaches everything inside the container ...
    (ref(0, STRUCTURE), ref(0, ELEMENTS), True),
    (ref(0, STRUCTURE), ref(0, STRUCTURE), True),
    (ref(0, STRUCTURE), ref(0, ELEMENTS, VALUE), True),
    # ... but not the container object every holder of it keeps.
    (ref(0, STRUCTURE), ref(0), False),
    # An elements write reaches the elements region only.
    (ref(0, ELEMENTS), ref(0, ELEMENTS), True),
    (ref(0, ELEMENTS), ref(0, ELEMENTS, VALUE), True),
    (ref(0, ELEMENTS), ref(0, STRUCTURE), False),
    (ref(0, ELEMENTS), ref(0), False),
    # A container field's region: siblings stay disjoint, the record is kept.
    (ref(0, ITEMS, STRUCTURE), ref(0, ITEMS, ELEMENTS), True),
    (ref(0, ITEMS, STRUCTURE), ref(0, OTHER), False),
    (ref(0, ITEMS, STRUCTURE), ref(0, ITEMS), False),
    (ref(0, ITEMS, STRUCTURE), ref(0), False),
    # Replacing a container field reaches its holders and what is inside.
    (ref(0, ITEMS), ref(0, ITEMS), True),
    (ref(0, ITEMS), ref(0, ITEMS, ELEMENTS), True),
    (ref(0, ITEMS), ref(0, OTHER), False),
    # Whole replacement (a rebind or a move of owned storage) reaches all of it.
    (ref(0, external=False), ref(0, ELEMENTS, external=False), True),
    (ref(0, external=False), ref(0, external=False), True),
    # Private storage is distinct from external storage and from other roots.
    (ref(0, STRUCTURE, external=False), ref(0, ELEMENTS), False),
    (ref(0, STRUCTURE, external=False), ref(1, ELEMENTS, external=False), False),
    # Distinct external roots may alias: any interior holder is reached ...
    (ref(0, STRUCTURE), ref(1, ELEMENTS), True),
    (ref(0, ELEMENTS), ref(1, STRUCTURE), True),
    (ref(0, STRUCTURE), ref(2), True),
    # ... except a whole container, which no container holds as an element.
    (ref(0, STRUCTURE), ref(1), False),
    (ref(0, ELEMENTS), ref(1), False),
])
def test_affects_container_regions(written: MIRReferent, retained: MIRReferent, expected: bool) -> None:
    slots = {s.id: s for s in (param(0), param(1), holder(2, kind=MIRSlotKind.PARAMETER))}
    assert affects(written, retained, slots) is expected


def test_affects_spares_a_static_literal_and_owned_leaf_buffers_spare_containers() -> None:
    slots = {s.id: s for s in (param(0), param(1), holder(2, BAG, MIRSlotKind.PARAMETER))}
    static = MIRReferent(place(0, ELEMENTS), True, static=True)
    assert not affects(ref(0, STRUCTURE), static, slots)
    # A str field's buffer holds no container: a whole container across
    # origins is not reached, an elements region of one may be (it may view it).
    name = MIRField(MIRFieldId(BAG, "name"), STR)
    assert not affects(ref(2, name), ref(1), slots)
    assert affects(ref(2, name), ref(1, ELEMENTS), slots)


def test_container_regions_of_one_origin_are_disjoint_siblings() -> None:
    assert not may_overlap(ref(0, STRUCTURE), ref(0, ELEMENTS))
    assert may_overlap(ref(0, ELEMENTS), ref(0, ELEMENTS, VALUE))
    assert may_overlap(ref(0, ELEMENTS), ref(1, ELEMENTS))


# An inline record member (`OTHER`) is replaced whole in place; a scalar
# field (`VALUE`) is overwritten, never storage a borrow points into.
@pytest.mark.parametrize(("target", "expected"), [
    (place(0, ELEMENTS), True), (place(0, STRUCTURE), True), (place(2, ITEMS), True),
    (place(2, ITEMS, ELEMENTS), True), (place(2, OTHER), True), (place(3, VALUE), False), (place(0), False),
])
def test_storage_destination_counts_container_regions_and_fields(target: MIRPlace, expected: bool) -> None:
    slots = {s.id: s for s in (param(0), holder(2, BAG), holder(3))}
    assert storage_destination(target, slots) is expected


def test_call_write_places_follow_the_argument_holder() -> None:
    slots = {s.id: s for s in (param(0), owned(1, INTS, INT_LAYOUT), holder(2, BAG), holder(3, SPAN))}
    identity = th.THIRFieldIdentity(BAG, "items", CELLS)
    summary = replace(append_summary(), writes=frozenset({MIRParameterWrite(0, (STRUCTURE,))}))
    for argument, path, expected in (
            (sid(0), (STRUCTURE,), place(0, STRUCTURE)),          # a borrowed container: directly
            (sid(1), (ELEMENTS,), place(1, ELEMENTS)),            # owned container storage: directly
            (sid(2), (identity, STRUCTURE), place(2, MIRDeref(), ITEMS, STRUCTURE)),  # through the record
            (sid(3), (ELEMENTS,), place(3, ELEMENTS))):           # a Span: its own region
        call = MIRCall(replace(summary, writes=frozenset({MIRParameterWrite(0, path)})), (argument, sid(4)), True)
        assert call_write_places(call, slots) == (expected,)


# --- dependencies ---------------------------------------------------------------

def element_borrow(*tail, live_after: bool = True, container_live: bool = False, cell=CELL) -> MIRFunction:
    """`p = ps[0]; <tail>; return p.value` over a borrowed `list[Cell]`, or
    with `p.value` read before the tail when `p` is not live after it."""
    value = MIRField(MIRFieldId(cell, "value"), INT32)
    cells = NominalType("list", (cell,), _module_qname="builtins.list")
    slots = [param(0, cells, MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED)), name="ps"),
             holder(1, cell, name="p"), owned(2, cell), scalar(3), scalar(4), holder(5, cell), scalar(6)]
    read = assign(4, MIRRead(place(1, MIRDeref(), value)))
    head = (assign(1, MIRBorrow(place(0, ELEMENTS))),) + (() if live_after else (read,))
    use = (read,) if live_after else ()
    if container_live:
        use += (assign(6, MIRRead(place(0, ELEMENTS, value))),)
    return body(slots, MIRBlock(bid(0), (*head, *tail, *use), MIRReturn(sid(4))),
                records=(MIRRecordLayout(cell, (value,), True, True),))


CONSTRUCT = (assign(3, MIRConstant(1)), assign(2, MIRConstruct((sid(3),)),
                                               write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)))
APPEND = MIRCallStmt(MIRCall(append_summary(), (sid(0), sid(2)), True))
SETITEM = assign(0, MIRMove(sid(2)), ELEMENTS, write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, sid(0)))


def test_element_borrow_resolves_to_the_elements_region() -> None:
    fn = element_borrow()
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 1)][place(1)] == {ref(0, ELEMENTS)}


def test_elements_write_is_a_weak_update() -> None:
    # `ps[0] = Cell(1)` writes under the root: the root and every other
    # holder keep their referents, and a later element borrow still resolves.
    fn = element_borrow(*CONSTRUCT, SETITEM, assign(5, MIRBorrow(place(0, ELEMENTS))), live_after=False)
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    after = deps.referents[MIRPoint(bid(0), 6)]
    assert after[place(0)] == {ref(0)}
    assert after[place(1)] == {ref(0, ELEMENTS)}
    assert after[place(5)] == {ref(0, ELEMENTS)}


def test_owned_container_is_its_own_referent() -> None:
    slots = [owned(0, CELLS, CELL_LAYOUT, name="xs"), holder(1, name="p")]
    point = MIRPlace(sid(0), (ELEMENTS,))
    assert resolve_referents(point, {}, {s.id: s for s in slots}) == {ref(0, ELEMENTS, external=False)}


def span_body(*, source_param: bool) -> MIRFunction:
    """A Span over a list's elements, a Span of that Span, and a cursor over it."""
    if source_param:
        slots = [holder(0, SPAN, MIRSlotKind.PARAMETER, name="s")]
        slots[0] = replace(slots[0], passing=ParamPassing.VALUE)
        first = ()
    else:
        slots = [param(0, INTS, INT_LAYOUT, name="xs")]
        first = (assign(1, MIRBorrow(place(0, ELEMENTS))),)
    slots += [holder(1, SPAN, name="t"), holder(2, SPAN, name="u"),
              MIRSlot(sid(3), SPAN, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                      value_kind=MIRValueKind.NATIVE_ITERATOR, container_layout=INT_LAYOUT),
              scalar(4, BOOL), scalar(5, kind=MIRSlotKind.LOCAL)]
    source = sid(1) if not source_param else sid(0)
    return body(slots,
                MIRBlock(bid(0), (*first, assign(2, MIRBorrow(MIRPlace(source, (ELEMENTS,)))),
                                  assign(3, MIRIteratorInit(sid(2))), assign(4, MIRIteratorHasNext(sid(3)))),
                         MIRBranch(sid(4), bid(1), bid(2))),
                MIRBlock(bid(1), (assign(5, MIRIteratorRead(sid(3))), assign(3, MIRIteratorAdvance(sid(3))),
                                  assign(4, MIRIteratorHasNext(sid(3)))), MIRBranch(sid(4), bid(1), bid(2))),
                MIRBlock(bid(2), (assign(5, MIRConstant(0)),), MIRReturn(sid(5))))


def test_a_view_of_a_view_is_the_same_region() -> None:
    fn = span_body(source_param=False)
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    state = deps.referents[MIRPoint(bid(0), 3)]
    # `u = t[...]` holds exactly what `t` holds, never `xs[elements][elements]`.
    assert state[place(1)] == state[place(2)] == {ref(0, ELEMENTS)}
    # A cursor over a view depends on the view's region only.
    assert state[place(3)] == {ref(0, ELEMENTS)}


def test_a_span_parameter_names_the_elements_region_it_views() -> None:
    fn = span_body(source_param=True)
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 0)][place(0)] == {ref(0, ELEMENTS)}
    assert deps.referents[MIRPoint(bid(0), 2)][place(3)] == {ref(0, ELEMENTS)}


def test_a_cursor_over_a_view_needs_the_same_has_next_proof() -> None:
    fn = span_body(source_param=False)
    assert not _analyze_presence(fn).issues
    unguarded = replace(fn, blocks=(replace(fn.blocks[0], terminator=MIRGoto(bid(1))), *fn.blocks[1:]))
    with pytest.raises(MIRPresenceError):
        analyze_liveness(unguarded)


def test_a_cursor_over_owned_container_storage_depends_on_its_regions() -> None:
    slots = [owned(0, INTS, INT_LAYOUT, name="xs"),
             MIRSlot(sid(1), INTS, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                     value_kind=MIRValueKind.NATIVE_ITERATOR, container_layout=INT_LAYOUT), scalar(2, BOOL),
             scalar(3)]
    built = assign(0, MIRConstruct((), True), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    fn = body(slots, MIRBlock(bid(0), (built, assign(1, MIRIteratorInit(sid(0))), assign(2, MIRIteratorHasNext(sid(1))),
                                       assign(3, MIRConstant(0))), MIRReturn(sid(3))), records=())
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 2)][place(1)] == {ref(0, STRUCTURE, external=False),
                                                              ref(0, ELEMENTS, external=False)}


def element_call(result: MIRSlot, argument: MIRSlot, record: bool = True) -> MIRFunction:
    signature = th.THIRCallableSignature((argument.type,), RefType(result.type),
                                         borrowed_result=th.THIRBorrowedRecord(result.type, False),
                                         passings=(ParamPassing.MUT_REF,))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("containers", "first"), signature)
    # A record argument is lent as the record it is.
    lent = th.THIRBorrowedRecord(argument.type, False) if argument.value_kind is MIRValueKind.BORROWED else None
    summary = MIRCallSummary(callee, (MIRParameterBinding(argument.type, ParamPassing.MUT_REF, False, lent),),
                             frozenset({0}), frozenset(), frozenset(), frozenset({MIRReturnOrigin(0)}), frozenset(), True)
    return body((argument, result, scalar(2)), MIRBlock(bid(0), (assign(1, MIRCall(summary, (sid(0),))),
                                                             assign(2, MIRConstant(0))), MIRReturn(sid(2))))


def test_a_call_result_borrowed_from_a_container_lies_in_its_elements() -> None:
    # `p = first(ps)`: the summary names the whole parameter; what a
    # container lends besides itself lies in its elements region.
    fn = element_call(holder(1, name="p"), param(0, name="ps"))
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 1)][place(1)] == {ref(0, ELEMENTS)}
    # A record result of a record argument stays the argument itself.
    fn = element_call(holder(1, name="q"), holder(0, kind=MIRSlotKind.PARAMETER, name="r"))
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 1)][place(1)] == {ref(0)}


def test_a_container_result_of_a_record_argument_stays_uncovered() -> None:
    # `xs = items_of(bag)` with a whole-record origin: the container would be
    # the record itself, whose elements resolve where no write lands. The
    # field path a body publishes names the container (test_return_origins).
    result = MIRSlot(sid(1), CELLS, MIRSlotKind.LOCAL, "xs", form=th.Form.BORROW,
                     value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=CELL_LAYOUT)
    fn = element_call(result, holder(0, BAG, MIRSlotKind.PARAMETER, name="bag"))
    with pytest.raises(MIRValidationError, match="unsupported return origin type or access"):
        analyze_dependencies(fn, analyze_liveness(fn))
    # From a container argument it is that container.
    fn = element_call(result, param(0, name="ps"))
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 1)][place(1)] == {ref(0)}


# --- the worked examples: replacement conflicts ------------------------------------

def test_shape_write_under_a_live_element_borrow_conflicts() -> None:
    fn = element_borrow(*CONSTRUCT, APPEND)
    assert analyze_storage(fn).call_writes == {MIRPoint(bid(0), 3): (place(0, STRUCTURE),)}
    assert conflicts(fn) == {(place(1), place(0, ELEMENTS))}


def test_container_holder_is_never_reached_by_its_own_writes() -> None:
    # `ps` is live across the append: the list object survives its growth.
    fn = element_borrow(*CONSTRUCT, APPEND, SETITEM, container_live=True, live_after=False)
    assert analyze_liveness(fn).points[MIRPoint(bid(0), 6)] >= {sid(0)}
    assert not conflicts(fn)


def test_elements_write_under_a_live_element_borrow_conflicts() -> None:
    fn = element_borrow(*CONSTRUCT, SETITEM)
    writes = analyze_storage(fn).writes
    assert {p for p, s in writes.items() if s.storage_write.mode is MIRRecordWriteMode.IN_PLACE} == {
        MIRPoint(bid(0), 3)}
    assert conflicts(fn) == {(place(1), place(0, ELEMENTS))}


def test_elements_write_after_the_last_use_is_no_conflict() -> None:
    fn = element_borrow(*CONSTRUCT, SETITEM, live_after=False)
    assert not conflicts(fn)


def iterate(*loop_tail) -> MIRFunction:
    """`for x in xs: <tail>` over a borrowed list of ints."""
    iterator = MIRSlot(sid(5), INTS, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                       value_kind=MIRValueKind.NATIVE_ITERATOR, container_layout=INT_LAYOUT)
    slots = (param(0, INTS, INT_LAYOUT, name="xs"), scalar(1, kind=MIRSlotKind.LOCAL), scalar(2), scalar(3),
             scalar(4), iterator, scalar(6, BOOL))
    return body(slots,
                MIRBlock(bid(0), (assign(5, MIRIteratorInit(sid(0))), assign(6, MIRIteratorHasNext(sid(5)))),
                         MIRBranch(sid(6), bid(1), bid(2))),
                MIRBlock(bid(1), (assign(1, MIRIteratorRead(sid(5))), *loop_tail,
                                  assign(5, MIRIteratorAdvance(sid(5))), assign(6, MIRIteratorHasNext(sid(5)))),
                         MIRBranch(sid(6), bid(1), bid(2))),
                MIRBlock(bid(2), (assign(4, MIRConstant(0)),), MIRReturn(sid(4))))


# `xs.append(1)` and `xs[0] = 1` over the list of ints `iterate` walks.
INT_APPEND = (assign(3, MIRConstant(1)),
              MIRCallStmt(MIRCall(append_summary(INTS, INT32, ParamPassing.VALUE), (sid(0), sid(3)), True)))
INT_SETITEM = (assign(3, MIRConstant(1)),
               assign(0, MIRRead(place(3)), ELEMENTS, write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, sid(0))))


def test_shape_write_inside_a_loop_reaches_the_live_iterator() -> None:
    fn = iterate(*INT_APPEND)
    found = conflicts(fn)
    assert (place(5), place(0, STRUCTURE)) in found and (place(5), place(0, ELEMENTS)) in found
    # The loop variable is dead at the write: only the cursor retains the list.
    assert {holder for holder, _ in found} == {place(5)}


def test_elements_write_inside_a_loop_reaches_the_live_iterator() -> None:
    # Conservative: an element write leaves the cursor valid in C++, but the
    # iterator retains `[elements]`, so the write is reported.
    fn = iterate(*INT_SETITEM)
    assert conflicts(fn) == {(place(5), place(0, ELEMENTS))}


def test_an_elements_write_through_a_span_reaches_the_live_iterator() -> None:
    # `s = xs[0:2]; for x in xs: s[0] = 1`: the write lands in the region
    # the Span views, which the cursor over `xs` retains.
    fn = iterate(assign(3, MIRConstant(1)),
                 assign(7, MIRRead(place(3)), ELEMENTS, write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, sid(7))))
    view = assign(7, MIRBorrow(place(0, ELEMENTS)))
    fn = replace(fn, slots=(*fn.slots, holder(7, SPAN, name="s")),
                 blocks=(replace(fn.blocks[0], statements=(view, *fn.blocks[0].statements)), *fn.blocks[1:]))
    assert conflicts(fn) == {(place(5), place(0, ELEMENTS))}


def bag_body(*, sibling: bool) -> MIRFunction:
    """`q = bag.others[0]` (or `q = bag.items[0]`); `bag.items.append(..)`; `q.value`."""
    temp = MIRSlot(sid(6), CELLS, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                   value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=CELL_LAYOUT)
    slots = (holder(0, BAG, MIRSlotKind.PARAMETER, name="bag"), holder(1, name="q"), owned(2), scalar(3),
             scalar(4), holder(5), temp)
    source = place(0, MIRDeref(), OTHERS, ELEMENTS) if sibling else place(0, MIRDeref(), ITEMS, ELEMENTS)
    stmts = (assign(1, MIRBorrow(source)), *CONSTRUCT, assign(6, MIRBorrow(place(0, MIRDeref(), ITEMS))),
             MIRCallStmt(MIRCall(append_summary(), (sid(6), sid(2)), True)),
             assign(4, MIRRead(place(1, MIRDeref(), VALUE))))
    fn = body(slots, MIRBlock(bid(0), stmts, MIRReturn(sid(4))), records=(RECORD, BAG_RECORD))
    slot = fn.slots[0]
    return replace(fn, slots=(replace(slot, passing=ParamPassing.MUT_REF), *fn.slots[1:]))


def test_a_field_shape_write_spares_the_sibling_field() -> None:
    assert analyze_storage(bag_body(sibling=True)).call_writes == {
        MIRPoint(bid(0), 4): (place(6, STRUCTURE),)}
    assert not conflicts(bag_body(sibling=True))
    assert conflicts(bag_body(sibling=False)) == {(place(1), place(0, ITEMS, ELEMENTS))}


def two_lists(*, element: bool) -> MIRFunction:
    """`q = ys[0]` (or nothing); `xs.append(..)`; `ys` and `q` read after."""
    slots = (param(0, name="xs"), param(1, name="ys"), owned(2), scalar(3), scalar(4), holder(5, name="q"),
             scalar(6))
    head = (assign(5, MIRBorrow(place(1, ELEMENTS))),) if element else ()
    tail = (assign(4, MIRRead(place(5, MIRDeref(), VALUE))),) if element else (assign(4, MIRConstant(0)),)
    append = MIRCallStmt(MIRCall(append_summary(), (sid(0), sid(2)), True))
    return body(slots, MIRBlock(bid(0), (*head, *CONSTRUCT, append, *tail,
                                         assign(6, MIRRead(place(1, ELEMENTS, VALUE)))), MIRReturn(sid(4))))


def test_external_roots_may_alias_but_a_whole_container_is_never_an_element() -> None:
    # `ys` may be `xs`: its element borrow is reached by `xs`'s growth.
    assert conflicts(two_lists(element=True)) == {(place(5), place(1, ELEMENTS))}
    # The parameter `ys` itself survives, whatever it aliases.
    assert not conflicts(two_lists(element=False))


def moved(*, live: bool) -> MIRFunction:
    """`xs = [..]` owned; `s = xs[...]` (a view); `ys = move xs`; `len(s)`."""
    slots = (owned(0, INTS, INT_LAYOUT, name="xs"), holder(1, SPAN, name="s"),
             owned(2, INTS, INT_LAYOUT, name="ys"), scalar(3), scalar(4))
    stmts = (assign(3, MIRConstant(1)),
             assign(0, MIRConstruct((sid(3),), True), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
             assign(1, MIRBorrow(place(0, ELEMENTS))),
             assign(4, MIRRead(place(1, ELEMENTS))) if not live else assign(4, MIRConstant(0)),
             assign(2, MIRMove(sid(0)), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)))
    if live:
        stmts += (assign(4, MIRRead(place(1, ELEMENTS))),)
    return body(slots, MIRBlock(bid(0), stmts, MIRReturn(sid(4))), records=())


def test_moving_owned_container_storage_replaces_it() -> None:
    fn = moved(live=True)
    assert analyze_storage(fn).moves == {MIRPoint(bid(0), 4): place(0)}
    assert conflicts(fn) == {(place(1), place(0, ELEMENTS))}
    assert not conflicts(moved(live=False))


# --- summaries ---------------------------------------------------------------------

def declared(compiled, name: str) -> th.THIRFunction:
    return next(fn for node, fn in compiled[2].thir_functions.items() if node.name == name)


def definitions(compiled) -> MIRDefinitions:
    return MIRDefinitions(tuple(compiled[2].thir_constructors.values()))


def cell_type(compiled) -> NominalType:
    return declared(compiled, "first").params[0].native_container.element.type


def grow_body(compiled) -> MIRFunction:
    """`grow(ps)`: `ps.append(Cell(1))`, built by hand."""
    decl = declared(compiled, "grow")
    container = decl.params[0].native_container
    cell = container.element.type
    record = MIRRecordLayout(cell, (MIRField(MIRFieldId(cell, "value"), INT32),), True, True)
    ps = MIRSlot(sid(0), container.type, MIRSlotKind.PARAMETER, "ps", form=th.Form.BORROW,
                 value_kind=MIRValueKind.BORROWED_CONTAINER, readonly=container.readonly,
                 container_layout=MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED)),
                 passing=decl.params[0].passing)
    slots = (ps, owned(1, cell), scalar(2))
    call = MIRCall(append_summary(container.type, cell), (sid(0), sid(1)), True)
    return MIRFunction(B, VoidType(), slots, (MIRBlock(bid(0), (
        assign(2, MIRConstant(1)),
        assign(1, MIRConstruct((sid(2),)), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
        MIRCallStmt(call)), MIRReturn(None)),), bid(0), records=(record,), call_summaries=(call.summary,),
        exceptional_exits=True)


def test_a_callee_shape_write_is_published_and_reaches_the_caller(compiled) -> None:
    decl = declared(compiled, "grow")
    assert decl.params[0].passing is ParamPassing.MUT_REF
    result = summarize_function(decl, grow_body(compiled), definitions(compiled))
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == {MIRParameterWrite(0, (STRUCTURE,))}
    assert result.summary.returns == frozenset()
    # The caller's element holder is live across `grow(ps)`.
    caller = element_borrow(*CONSTRUCT, MIRCallStmt(MIRCall(result.summary, (sid(0),), True)),
                            cell=cell_type(compiled))
    assert conflicts(caller) == {(place(1), place(0, ELEMENTS))}


def test_a_private_container_write_keeps_the_summary_known(compiled) -> None:
    # `xs = [..]; xs.append(Cell(1))` on owned storage: nothing of the caller's.
    decl = declared(compiled, "grow")
    fn = grow_body(compiled)
    cell = cell_type(compiled)
    xs = owned(3, NominalType("list", (cell,), _module_qname="builtins.list"),
               MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED)), name="xs")
    call = MIRCall(append_summary(xs.type, cell), (sid(3), sid(1)), True)
    block = replace(fn.blocks[0], statements=(
        assign(3, MIRConstruct((), True), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
        *fn.blocks[0].statements[:2], MIRCallStmt(call)))
    result = summarize_function(decl, replace(fn, slots=(*fn.slots, xs), blocks=(block,), call_summaries=(call.summary,)),
                                definitions(compiled))
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == frozenset()


def test_an_argument_temporary_the_body_reads_keeps_the_summary_opaque(compiled) -> None:
    # Storage handed over to the container is private only when its holder's
    # borrow is its one other read; a direct field read beside the hand-over is not.
    decl = declared(compiled, "grow")
    fn = grow_body(compiled)
    cell = cell_type(compiled)
    read = assign(2, MIRRead(place(1, MIRField(MIRFieldId(cell, "value"), INT32))))
    block = replace(fn.blocks[0], statements=(*fn.blocks[0].statements, read))
    result = summarize_function(decl, replace(fn, blocks=(block,)), definitions(compiled))
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary storage or value shape"


def test_an_element_result_returns_as_the_whole_parameter(compiled) -> None:
    decl = declared(compiled, "first")
    container = decl.params[0].native_container
    cell = container.element.type
    ps = MIRSlot(sid(0), container.type, MIRSlotKind.PARAMETER, "ps", form=th.Form.BORROW,
                 value_kind=MIRValueKind.BORROWED_CONTAINER, readonly=container.readonly,
                 container_layout=MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED)),
                 passing=decl.params[0].passing)
    result_slot = MIRSlot(sid(1), cell, MIRSlotKind.TEMPORARY, form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED)
    returned = decl.resolved_callee.signature.return_type
    fn = MIRFunction(B, returned, (ps, result_slot), (MIRBlock(bid(0), (assign(1, MIRBorrow(place(0, ELEMENTS))),),
                                                     MIRReturn(sid(1))),), bid(0),
                     records=(MIRRecordLayout(cell, (MIRField(MIRFieldId(cell, "value"), INT32),), True, True),),
                     borrowed_result=decl.resolved_callee.signature.borrowed_result)
    result = summarize_function(decl, fn, definitions(compiled))
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.returns == {MIRReturnOrigin(0)} and result.summary.writes == frozenset()


def test_an_element_field_write_keeps_the_summary_opaque(compiled) -> None:
    # A write of an element's scalar field is no published path shape.
    decl = declared(compiled, "first")
    container = decl.params[0].native_container
    cell = container.element.type
    ps = MIRSlot(sid(0), container.type, MIRSlotKind.PARAMETER, "ps", form=th.Form.BORROW,
                 value_kind=MIRValueKind.BORROWED_CONTAINER, readonly=False,
                 container_layout=MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED)),
                 passing=decl.params[0].passing)
    field = MIRField(MIRFieldId(cell, "value"), INT32)
    result_slot = MIRSlot(sid(1), cell, MIRSlotKind.TEMPORARY, form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED)
    returned = decl.resolved_callee.signature.return_type
    fn = MIRFunction(B, returned, (ps, result_slot, scalar(2)), (MIRBlock(bid(0), (
        assign(2, MIRConstant(3)), assign(0, MIRRead(place(2)), ELEMENTS, field),
        assign(1, MIRBorrow(place(0, ELEMENTS)))), MIRReturn(sid(1))),), bid(0),
        records=(MIRRecordLayout(cell, (field,), True, True),), exceptional_exits=True,
        borrowed_result=decl.resolved_callee.signature.borrowed_result)
    result = summarize_function(decl, fn, definitions(compiled))
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary unsupported write origin"


class _WithBag:
    """The compiled definitions with the Bag layout replaced, so a test
    names the fields the definition declares."""

    def __init__(self, inner: MIRDefinitions, layout: MIRRecordLayout) -> None:
        self.inner, self.layout = inner, layout

    def get(self, node, typ):
        if typ == self.layout.type:
            return MIROwnedLeafDefinition(self.layout)
        return self.inner.get(node, typ)


def test_a_container_field_shape_write_is_published_with_its_field(compiled) -> None:
    decl = declared(compiled, "push")
    bag = decl.params[0].borrowed_record.type
    cell = cell_type(compiled)
    cells = NominalType("list", (cell,), _module_qname="builtins.list")
    items = MIRField(MIRFieldId(bag, "items"), cells)
    layout = MIRContainerLayout(MIRTupleElement(cell, MIRValueKind.BORROWED))
    b = MIRSlot(sid(0), bag, MIRSlotKind.PARAMETER, "b", form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED,
                passing=decl.params[0].passing)
    temp = MIRSlot(sid(3), cells, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                   value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=layout)
    call = MIRCall(append_summary(cells, cell), (sid(3), sid(1)), True)
    record = MIRRecordLayout(cell, (MIRField(MIRFieldId(cell, "value"), INT32),), True, True)
    fn = MIRFunction(B, VoidType(), (b, owned(1, cell), scalar(2), temp), (MIRBlock(bid(0), (
        assign(2, MIRConstant(2)),
        assign(1, MIRConstruct((sid(2),)), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
        assign(3, MIRBorrow(place(0, MIRDeref(), items))), MIRCallStmt(call)), MIRReturn(None)),), bid(0),
        records=(record, MIRRecordLayout(bag, (items,), True, True)), call_summaries=(call.summary,),
        exceptional_exits=True)
    defs = _WithBag(definitions(compiled), MIRRecordLayout(bag, (items,), True, True))
    result = summarize_function(decl, fn, defs)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == {MIRParameterWrite(0, (th.THIRFieldIdentity(bag, "items", cells), STRUCTURE))}
    # A field the definition does not declare is refused.
    other = _WithBag(definitions(compiled), MIRRecordLayout(bag, (), True, True))
    result = summarize_function(decl, fn, other)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary write field differs from definition"


# --- line facts ---------------------------------------------------------------------

def test_call_writes_are_write_events_of_their_line() -> None:
    fn = bag_body(sibling=True)
    stmts = list(fn.blocks[0].statements)
    stmts[4] = replace(stmts[4], loc=SourceLocation(7, 4))
    fn = replace(fn, blocks=(replace(fn.blocks[0], statements=tuple(stmts)),))
    verdict = MIRBodyVerdict(B, "f", 1, None, MIRVerdictStatus.COVERED, None, (), True, None, None, fn,
                             analyze_body(fn), None)
    facts = line_facts(verdict)
    # Written through an unnamed borrow of `bag.items`: spelled by what it borrows.
    assert facts.events[(7, "bag.items[structure]")] == ("call",)


def _located(fn: MIRFunction) -> MIRFunction:
    return replace(fn, blocks=tuple(replace(b, statements=tuple(
        replace(s, loc=SourceLocation(10 * b.id.index + i + 1, 4)) for i, s in enumerate(b.statements)))
        for b in fn.blocks))


def _facts(fn: MIRFunction):
    return line_facts(MIRBodyVerdict(B, "f", 1, None, MIRVerdictStatus.COVERED, None, (), True, None, None, fn,
                                     analyze_body(fn), None))


def test_element_writes_and_moves_are_line_facts() -> None:
    # `ps[0] = Cell(1)` (statement 4) writes `ps[elements]`, owned element storage.
    facts = _facts(_located(element_borrow(*CONSTRUCT, SETITEM)))
    assert facts.events[(4, "ps[elements]")] == ("in_place",)
    assert [w.kind for w in facts.writes[(4, "ps[elements]")]] == [MIRValueKind.OWNED]
    # `ys = move xs` (statement 5) empties `xs`.
    facts = _facts(_located(moved(live=False)))
    assert facts.events[(5, "xs")] == ("move",)


def test_owned_container_storage_needs_its_container_definition() -> None:
    fn = moved(live=False)

    class Defs:
        def __init__(self, layout):
            self.layout = layout

        def get(self, node, typ):
            return SimpleNamespace(layout=self.layout)
    reasons = lambda layout: [g.reason for g in certify_storage_origins(fn, frozenset({sid(0)}), Defs(layout)).gaps]
    assert "storage record layout differs from its definition" not in reasons(INT_LAYOUT)
    assert "storage record layout differs from its definition" in reasons(CELL_LAYOUT)

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
from .definitions import declared_fields
from .call_contract import (
    signature_result,
    MIRCallSummary, MIRParameterWrite, MIRReturnOrigin, MIRSummaryState, path_walk, stub_summary, write_problem,
)
from .coverage import call_write_places
from .collect import MIRBodyVerdict, MIRVerdictStatus, MIRWriteKind, analyze_body, enumerate_bodies, line_facts
from .definitions import MIRDefinitions, MIROwnedLeafDefinition
from .dependencies import MIRReferent, analyze_dependencies, resolve_referents
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRCallStmt, MIRConstant, MIRConstruct,
    MIRFunction, MIRGoto, MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMove,
    MIRNotCovered, MIRPlace, MIRPoint, MIRRead, MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration,
)
from .shapes import MIRContainerElements, MIRContainerStructure, MIRDeref
from ..thir.scalar_leaves import MIRClass
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRRecords, MIRValidationError
from .presence import _analyze_presence
from .retention import MIRRetention, affects, analyze_retention, may_overlap
from .storage import analyze_storage, storage_destination
from .storage_evidence import certify_borrow_operations, certify_storage_origins
from .summaries import summarize_function
from .validate import MIRPresenceError, body_may_raise, validate_function
from .testutil import (
    no_layouts, records_of,
    hand_record,
    bind_parameters, bindings_of, borrowed_shape, bound, cursor_shape, leaf_shape, owned_shape,
)

B = MIRBodyId("containers", "effects")
CELL = hand_record("Cell", "containers.Cell", value=INT32)
VALUE = MIRField(MIRFieldId(CELL, "value"), INT32)
CELLS = NominalType("list", (CELL,), _module_qname="builtins.list")
BAG = hand_record("Bag", "containers.Bag", items=CELLS, other=CELL, others=CELLS, name=STR)
INTS = NominalType("list", (INT32,), _module_qname="builtins.list")
SPAN = NominalType("Span", (INT32,), _module_qname="tpy.Span")
ITEMS = MIRField(MIRFieldId(BAG, "items"), CELLS)
OTHER = MIRField(MIRFieldId(BAG, "other"), CELL)
OTHERS = MIRField(MIRFieldId(BAG, "others"), CELLS)
RECORD = MIRRecordLayout(CELL, (VALUE,), True, True)
BAG_RECORD = MIRRecordLayout(BAG, (ITEMS, OTHERS), True, True)
CELL_LAYOUT = (owned_shape(CELL),)
INT_LAYOUT = (leaf_shape(INT32),)
ELEMENTS, STRUCTURE, DEREF = MIRContainerElements(), MIRContainerStructure(), MIRDeref()

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
    return MIRSlot(sid(index), borrowed_shape(typ, readonly=readonly, members=layout), MIRSlotKind.PARAMETER,
                   name or f"p{index}")


def holder(index: int, typ=CELL, kind: MIRSlotKind = MIRSlotKind.LOCAL, *, name: str | None = None) -> MIRSlot:
    # A Span holder carries the layout of the region it views.
    return MIRSlot(sid(index), borrowed_shape(typ, members=INT_LAYOUT if typ == SPAN else None), kind, name)


def owned(index: int, typ=CELL, layout: tuple | None = None, *, name: str | None = None) -> MIRSlot:
    return MIRSlot(sid(index), owned_shape(typ, members=layout), MIRSlotKind.LOCAL if name else MIRSlotKind.TEMPORARY,
                   name, storage_duration=MIRStorageDuration.BODY)


def scalar(index: int, typ=INT32, kind: MIRSlotKind = MIRSlotKind.TEMPORARY) -> MIRSlot:
    return MIRSlot(sid(index), leaf_shape(typ), kind)


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
    return bind_parameters(MIRFunction(B, INT32 if borrowed_result is None else borrowed_result.type, tuple(slots), tuple(blocks),
                       bid(0), records=records, call_summaries=summaries,
                       # A forged body registers the container definitions its storage has.
                       containers=tuple({s.type: None for s in slots if s.shape.cls is MIRClass.CONTAINER
                                         and s.shape.is_storage}),
                       exceptional_exits=body_may_raise(blocks, {s.id: s for s in slots}, {r.type: r for r in records},
                                                         None),
                       borrowed_result=borrowed_result))


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
    assert affects(written, retained, slots, {}) is expected


def test_affects_spares_a_static_literal_and_owned_leaf_buffers_spare_containers() -> None:
    slots = {s.id: s for s in (param(0), param(1), holder(2, BAG, MIRSlotKind.PARAMETER))}
    static = MIRReferent(place(0, ELEMENTS), True, static=True)
    assert not affects(ref(0, STRUCTURE), static, slots, {})
    # A str field's buffer holds no container: a whole container across
    # origins is not reached, an elements region of one may be (it may view it).
    name = MIRField(MIRFieldId(BAG, "name"), STR)
    assert not affects(ref(2, name), ref(1), slots, {})
    assert affects(ref(2, name), ref(1, ELEMENTS), slots, {})


def test_container_regions_of_one_origin_are_disjoint_siblings() -> None:
    assert not may_overlap(ref(0, STRUCTURE), ref(0, ELEMENTS))
    assert may_overlap(ref(0, ELEMENTS), ref(0, ELEMENTS, VALUE))
    assert may_overlap(ref(0, ELEMENTS), ref(1, ELEMENTS))


# An inline record member (`OTHER`) is replaced whole in place; a scalar
# field (`VALUE`) is overwritten, never storage a borrow points into.
@pytest.mark.parametrize(("target", "expected"), [
    (place(0, DEREF, ELEMENTS), True), (place(0, DEREF, STRUCTURE), True), (place(2, ITEMS), True),
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
            (sid(0), (STRUCTURE,), place(0, DEREF, STRUCTURE)),   # a borrowed container: its object
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
    slots = [param(0, cells, (owned_shape(cell),), name="ps"),
             holder(1, cell, name="p"), owned(2, cell), scalar(3), scalar(4), holder(5, cell), scalar(6)]
    read = assign(4, MIRRead(place(1, MIRDeref(), value)))
    head = (assign(1, MIRBorrow(place(0, DEREF, ELEMENTS))),) + (() if live_after else (read,))
    use = (read,) if live_after else ()
    if container_live:
        use += (assign(6, MIRRead(place(0, DEREF, ELEMENTS, value))),)
    return body(slots, MIRBlock(bid(0), (*head, *tail, *use), MIRReturn(sid(4))),
                records=(MIRRecordLayout(cell, (value,), True, True),))


CONSTRUCT = (assign(3, MIRConstant(1)), assign(2, MIRConstruct((sid(3),)),
                                               write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)))
APPEND = MIRCallStmt(MIRCall(append_summary(), (sid(0), sid(2)), True))
SETITEM = assign(0, MIRMove(sid(2)), DEREF, ELEMENTS, write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, sid(0)))


def test_element_borrow_resolves_to_the_elements_region() -> None:
    fn = element_borrow()
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(bid(0), 1)][place(1)] == {ref(0, ELEMENTS)}


def test_elements_write_is_a_weak_update() -> None:
    # `ps[0] = Cell(1)` writes under the root: the root and every other
    # holder keep their referents, and a later element borrow still resolves.
    fn = element_borrow(*CONSTRUCT, SETITEM, assign(5, MIRBorrow(place(0, DEREF, ELEMENTS))), live_after=False)
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    after = deps.referents[MIRPoint(bid(0), 6)]
    assert after[place(0)] == {ref(0)}
    assert after[place(1)] == {ref(0, ELEMENTS)}
    assert after[place(5)] == {ref(0, ELEMENTS)}


def test_owned_container_is_its_own_referent() -> None:
    slots = [owned(0, CELLS, CELL_LAYOUT, name="xs"), holder(1, name="p")]
    point = MIRPlace(sid(0), (ELEMENTS,))
    assert resolve_referents(point, {}, {s.id: s for s in slots}, {}) == {ref(0, ELEMENTS, external=False)}


def span_body(*, source_param: bool) -> MIRFunction:
    """A Span over a list's elements, a Span of that Span, and a cursor over it."""
    if source_param:
        slots = [holder(0, SPAN, MIRSlotKind.PARAMETER, name="s")]
        first = ()
    else:
        slots = [param(0, INTS, INT_LAYOUT, name="xs")]
        first = (assign(1, MIRBorrow(place(0, DEREF, ELEMENTS))),)
    slots += [holder(1, SPAN, name="t"), holder(2, SPAN, name="u"),
              MIRSlot(sid(3), cursor_shape(SPAN, members=INT_LAYOUT), MIRSlotKind.TEMPORARY),
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
             MIRSlot(sid(1), cursor_shape(INTS, members=INT_LAYOUT), MIRSlotKind.TEMPORARY), scalar(2, BOOL),
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
                                         passings=(ParamPassing.MUT_REF,),
                                         return_representation=return_representation(RefType(result.type)))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("containers", "first"), signature)
    # A record argument is lent as the record it is.
    lent = th.THIRBorrowedRecord(argument.type, False) if argument.shape.cls is MIRClass.RECORD else None
    summary = MIRCallSummary(callee, (bound(argument.type, ParamPassing.MUT_REF, ref=lent),),
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
    result = MIRSlot(sid(1), borrowed_shape(CELLS, members=CELL_LAYOUT), MIRSlotKind.LOCAL, "xs")
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
    assert analyze_storage(fn).call_writes == {MIRPoint(bid(0), 3): (place(0, DEREF, STRUCTURE),)}
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
    iterator = MIRSlot(sid(5), cursor_shape(INTS, members=INT_LAYOUT), MIRSlotKind.TEMPORARY)
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
               assign(0, MIRRead(place(3)), DEREF, ELEMENTS, write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, sid(0))))


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
    view = assign(7, MIRBorrow(place(0, DEREF, ELEMENTS)))
    fn = replace(fn, slots=(*fn.slots, holder(7, SPAN, name="s")),
                 blocks=(replace(fn.blocks[0], statements=(view, *fn.blocks[0].statements)), *fn.blocks[1:]))
    assert conflicts(fn) == {(place(5), place(0, ELEMENTS))}


def bag_body(*, sibling: bool) -> MIRFunction:
    """`q = bag.others[0]` (or `q = bag.items[0]`); `bag.items.append(..)`; `q.value`."""
    temp = MIRSlot(sid(6), borrowed_shape(CELLS, members=CELL_LAYOUT), MIRSlotKind.TEMPORARY)
    slots = (holder(0, BAG, MIRSlotKind.PARAMETER, name="bag"), holder(1, name="q"), owned(2), scalar(3),
             scalar(4), holder(5), temp)
    source = place(0, MIRDeref(), OTHERS, ELEMENTS) if sibling else place(0, MIRDeref(), ITEMS, ELEMENTS)
    stmts = (assign(1, MIRBorrow(source)), *CONSTRUCT, assign(6, MIRBorrow(place(0, MIRDeref(), ITEMS))),
             MIRCallStmt(MIRCall(append_summary(), (sid(6), sid(2)), True)),
             assign(4, MIRRead(place(1, MIRDeref(), VALUE))))
    return body(slots, MIRBlock(bid(0), stmts, MIRReturn(sid(4))), records=(RECORD, BAG_RECORD))


def test_a_field_shape_write_spares_the_sibling_field() -> None:
    assert analyze_storage(bag_body(sibling=True)).call_writes == {
        MIRPoint(bid(0), 4): (place(6, DEREF, STRUCTURE),)}
    assert not conflicts(bag_body(sibling=True))
    assert conflicts(bag_body(sibling=False)) == {(place(1), place(0, ITEMS, ELEMENTS))}


def two_lists(*, element: bool) -> MIRFunction:
    """`q = ys[0]` (or nothing); `xs.append(..)`; `ys` and `q` read after."""
    slots = (param(0, name="xs"), param(1, name="ys"), owned(2), scalar(3), scalar(4), holder(5, name="q"),
             scalar(6))
    head = (assign(5, MIRBorrow(place(1, DEREF, ELEMENTS))),) if element else ()
    tail = (assign(4, MIRRead(place(5, MIRDeref(), VALUE))),) if element else (assign(4, MIRConstant(0)),)
    append = MIRCallStmt(MIRCall(append_summary(), (sid(0), sid(2)), True))
    return body(slots, MIRBlock(bid(0), (*head, *CONSTRUCT, append, *tail,
                                         assign(6, MIRRead(place(1, DEREF, ELEMENTS, VALUE)))), MIRReturn(sid(4))))


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
    ps = MIRSlot(sid(0), borrowed_shape(container.type, readonly=container.readonly, members=(owned_shape(cell),)),
                 MIRSlotKind.PARAMETER, "ps")
    slots = (ps, owned(1, cell), scalar(2))
    call = MIRCall(append_summary(container.type, cell), (sid(0), sid(1)), True)
    return MIRFunction(B, VoidType(), slots, (MIRBlock(bid(0), (
        assign(2, MIRConstant(1)),
        assign(1, MIRConstruct((sid(2),)), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
        MIRCallStmt(call)), MIRReturn(None)),), bid(0), records=(record,), call_summaries=(call.summary,),
        exceptional_exits=True, parameter_bindings=bindings_of(decl), record_fields=declared_fields())


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
    # `xs = [..]; xs.append(Cell(1))` on owned storage, lent through the
    # local's holder: nothing of the caller's.
    decl = declared(compiled, "grow")
    fn = grow_body(compiled)
    cell = cell_type(compiled)
    cells = NominalType("list", (cell,), _module_qname="builtins.list")
    storage = owned(3, cells, (owned_shape(cell),))
    xs = MIRSlot(sid(4), borrowed_shape(cells, members=(owned_shape(cell),)), MIRSlotKind.LOCAL, "xs")
    call = MIRCall(append_summary(cells, cell), (sid(4), sid(1)), True)
    block = replace(fn.blocks[0], statements=(
        assign(3, MIRConstruct((), True), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
        assign(4, MIRBorrow(place(3))), *fn.blocks[0].statements[:2], MIRCallStmt(call)))
    result = summarize_function(decl, replace(fn, slots=(*fn.slots, storage, xs), blocks=(block,),
                                              call_summaries=(call.summary,), containers=(cells,)),
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
    ps = MIRSlot(sid(0), borrowed_shape(container.type, readonly=container.readonly, members=(owned_shape(cell),)),
                 MIRSlotKind.PARAMETER, "ps")
    result_slot = MIRSlot(sid(1), borrowed_shape(cell), MIRSlotKind.TEMPORARY)
    returned = signature_result(decl.resolved_callee.signature)
    fn = MIRFunction(B, returned, (ps, result_slot), (MIRBlock(bid(0), (assign(1, MIRBorrow(place(0, DEREF, ELEMENTS))),),
                                                     MIRReturn(sid(1))),), bid(0),
                     records=(MIRRecordLayout(cell, (MIRField(MIRFieldId(cell, "value"), INT32),), True, True),),
                     borrowed_result=decl.resolved_callee.signature.borrowed_result,
                     return_representation=decl.resolved_callee.signature.return_representation,
                     parameter_bindings=bindings_of(decl))
    result = summarize_function(decl, fn, definitions(compiled))
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.returns == {MIRReturnOrigin(0)} and result.summary.writes == frozenset()


def test_an_element_field_write_keeps_the_summary_opaque(compiled) -> None:
    # A write of an element's scalar field is no published path shape.
    decl = declared(compiled, "first")
    container = decl.params[0].native_container
    cell = container.element.type
    ps = MIRSlot(sid(0), borrowed_shape(container.type, members=(owned_shape(cell),)), MIRSlotKind.PARAMETER, "ps")
    field = MIRField(MIRFieldId(cell, "value"), INT32)
    result_slot = MIRSlot(sid(1), borrowed_shape(cell), MIRSlotKind.TEMPORARY)
    returned = signature_result(decl.resolved_callee.signature)
    fn = MIRFunction(B, returned, (ps, result_slot, scalar(2)), (MIRBlock(bid(0), (
        assign(2, MIRConstant(3)), assign(0, MIRRead(place(2)), DEREF, ELEMENTS, field),
        assign(1, MIRBorrow(place(0, DEREF, ELEMENTS)))), MIRReturn(sid(1))),), bid(0),
        records=(MIRRecordLayout(cell, (field,), True, True),), exceptional_exits=True,
        borrowed_result=decl.resolved_callee.signature.borrowed_result, parameter_bindings=bindings_of(decl),
        return_representation=decl.resolved_callee.signature.return_representation,
        record_fields=declared_fields())
    result = summarize_function(decl, fn, definitions(compiled))
    assert result.state is MIRSummaryState.OPAQUE and result.reason == (
        "summary unsupported write origin: invalid call write path")


class _WithBag:
    """The compiled definitions with the Bag layout replaced, so a test
    names the fields the definition declares."""

    def __init__(self, inner: MIRDefinitions, layout: MIRRecordLayout) -> None:
        self.inner, self.layout = inner, layout

    def get(self, node, typ):
        if typ == self.layout.type:
            return MIROwnedLeafDefinition(self.layout)
        return self.inner.get(node, typ)

    @property
    def layouts(self) -> MIRRecords:
        typ = self.layout.type
        return MIRRecords({**self.inner.layouts, typ: self.layout}, self.inner.fields)


def test_a_container_field_shape_write_is_published_with_its_field(compiled) -> None:
    decl = declared(compiled, "push")
    bag = decl.params[0].borrowed_record.type
    cell = cell_type(compiled)
    cells = NominalType("list", (cell,), _module_qname="builtins.list")
    items = MIRField(MIRFieldId(bag, "items"), cells)
    layout = (owned_shape(cell),)
    b = MIRSlot(sid(0), borrowed_shape(bag), MIRSlotKind.PARAMETER, "b")
    temp = MIRSlot(sid(3), borrowed_shape(cells, members=layout), MIRSlotKind.TEMPORARY)
    call = MIRCall(append_summary(cells, cell), (sid(3), sid(1)), True)
    record = MIRRecordLayout(cell, (MIRField(MIRFieldId(cell, "value"), INT32),), True, True)

    def body(field: MIRField) -> MIRFunction:
        return MIRFunction(B, VoidType(), (b, owned(1, cell), scalar(2), temp), (MIRBlock(bid(0), (
            assign(2, MIRConstant(2)),
            assign(1, MIRConstruct((sid(2),)), write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
            assign(3, MIRBorrow(place(0, MIRDeref(), field))), MIRCallStmt(call)), MIRReturn(None)),), bid(0),
            records=(record, MIRRecordLayout(bag, (field,), True, True)), call_summaries=(call.summary,),
            exceptional_exits=True, parameter_bindings=bindings_of(decl), record_fields=declared_fields())
    defs = _WithBag(definitions(compiled), MIRRecordLayout(bag, (items,), True, True))
    result = summarize_function(decl, body(items), defs)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == {MIRParameterWrite(0, (th.THIRFieldIdentity(bag, "items", cells), STRUCTURE))}
    # A field the record does not declare is refused, whatever a layout lists.
    missing = MIRField(MIRFieldId(bag, "missing"), cells)
    other = _WithBag(definitions(compiled), MIRRecordLayout(bag, (missing,), True, True))
    with pytest.raises(MIRValidationError, match="field missing from declared fields"):
        summarize_function(decl, body(missing), other)


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
    assert [w.kind for w in facts.writes[(4, "ps[elements]")]] == [MIRWriteKind.OWNED]
    # `ys = move xs` (statement 5) empties `xs`.
    facts = _facts(_located(moved(live=False)))
    assert facts.events[(5, "xs")] == ("move",)


def test_owned_container_storage_needs_its_container_definition() -> None:
    fn = moved(live=False)

    class Defs:
        def __init__(self, members):
            self.members = members

        def get(self, node, typ):
            return SimpleNamespace(members=self.members)
    reasons = lambda layout: [g.reason for g in certify_storage_origins(fn, frozenset({sid(0)}), Defs(layout)).gaps]
    assert "storage record layout differs from its definition" not in reasons(INT_LAYOUT)
    assert "storage record layout differs from its definition" in reasons(CELL_LAYOUT)


def test_owned_container_storage_needs_its_registered_definition() -> None:
    # Container storage is defined only by the definition the body
    # registered beside its record layouts, as record storage is by its layout.
    fn = moved(live=False)
    validate_function(fn)
    with pytest.raises(MIRValidationError, match="unsupported record storage type or form"):
        validate_function(replace(fn, containers=()))


def test_a_container_holder_without_members_is_refused() -> None:
    # A borrowed container's or an iterator's members come from its type: none is no layout.
    for slot in (param(0, INTS, ()), MIRSlot(sid(0), cursor_shape(INTS), MIRSlotKind.TEMPORARY)):
        with pytest.raises(MIRValidationError, match="container members disagree with its type"):
            validate_function(body([slot], MIRBlock(bid(0), (), MIRReturn(None))))


def test_no_field_is_read_through_a_borrowed_container() -> None:
    # `(*xs).fabricated` on a `list[int32]`: a container's object has elements, not fields.
    xs = param(0, INTS, INT_LAYOUT)
    fake = MIRField(MIRFieldId(INTS, "fabricated"), INT32)
    fn = body([xs, scalar(1)], MIRBlock(bid(0), (assign(1, MIRRead(place(0, DEREF, fake))),), MIRReturn(sid(1))),
              records=())
    with pytest.raises(MIRValidationError, match="field needs record storage"):
        validate_function(fn)
    # A summary path walks through the same step.
    assert path_walk(xs.shape, (th.THIRFieldIdentity(INTS, "fabricated", INT32),), no_layouts()) == (
        "field needs record storage")
    # A summary path's fields are read from a record parameter only: a
    # container parameter's never reaches the walk.
    write = MIRParameterWrite(0, (th.THIRFieldIdentity(INTS, "fabricated", INT32),))
    assert write_problem(write, (bound(INTS, ParamPassing.MUT_REF),), no_layouts()) == "invalid call write path"


def test_no_field_is_read_from_a_container_field() -> None:
    # `b.items.fabricated`, the field owned by the container's own type.
    items = th.THIRFieldIdentity(BAG, ITEMS.id.name, CELLS)
    path = (items, th.THIRFieldIdentity(CELLS, "fabricated", INT32))
    layouts = records_of(BAG_RECORD)
    assert path_walk(borrowed_shape(BAG), path, layouts) == "field needs record storage"
    write = MIRParameterWrite(0, path)
    binding = bound(BAG, ParamPassing.MUT_REF, ref=th.THIRBorrowedRecord(BAG, False))
    assert write_problem(write, (binding,), layouts) == "field needs record storage"


def test_a_borrowed_container_holder_is_a_demanded_borrow() -> None:
    # `r = ys` demanded as a borrow: a container holder is a borrow like a
    # record holder, whose origin is the parameter it aliases.
    holder = MIRSlot(sid(1), borrowed_shape(INTS, members=INT_LAYOUT), MIRSlotKind.LOCAL, "r")
    fn = body([param(0, INTS, INT_LAYOUT, name="ys"), holder, scalar(2)],
              MIRBlock(bid(0), (assign(1, MIRAlias(sid(0))), assign(2, MIRConstant(0))), MIRReturn(sid(2))),
              records=())
    evidence = certify_borrow_operations(fn, frozenset({MIRPoint(bid(0), 0)}), frozenset(), MIRDefinitions(()))
    assert "demanded operation is not a supported record borrow" not in [g.reason for g in evidence.gaps]
    assert evidence.origins[MIRPoint(bid(0), 0)] == frozenset({ref(0)})


# --- borrowed containers from source: the verdicts each shape keeps ------------------

BORROWED = '''from tpy import int32

class Point:
    def __init__(self, x: int32) -> None:
        self.x = x

class Copied:
    def __init__(self, xs: list[int32]) -> None:
        self.items = xs

class Grower:
    items: list[int32]
    def __init__(self, xs: list[int32]) -> None:
        self.items = xs
    def grow(self) -> None:
        for x in self.items:
            self.items.append(x)

def alias_other(xs: list[int32], ys: list[int32], flag: bool) -> int32:
    r = ys
    if flag:
        xs.append(1)
    return r[0]

def return_other(xs: list[int32], ys: list[int32], flag: bool) -> list[int32]:
    if flag:
        xs.append(1)
    return ys

def same(ps: list[Point]) -> list[Point]:
    return ps

def grow_held(flag: bool) -> int32:
    a = [Point(1)]
    p = a[0]
    ys = same(a)
    if flag:
        ys.append(Point(2))
    return p.x

def grow_iterated(xs: list[int32]) -> None:
    for x in xs:
        xs.append(x)
'''


@pytest.fixture(scope="module")
def borrowed_verdicts():
    compiler, modules = _compile(BORROWED)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                                    compiler.thir_reject_by_node, program.workspace)
    return {v.body.declaration.split("@")[0]: v for v in verdicts}


@pytest.mark.parametrize(("name", "expected"), [
    # Two container parameters: growing one leaves a borrow of the other alone.
    ("alias_other", ()), ("return_other", ()),
    # A borrowed result of an owned list, grown while an element is held.
    ("grow_held", ("replacement",)),
    # Iterator invalidation over a borrowed parameter and a borrowed field.
    ("grow_iterated", ("replacement",)), ("Grower.grow", ("replacement",)),
    # The constructor copies a borrowed list into its field.
    ("Copied.__init__", ()),
])
def test_borrowed_container_verdicts(borrowed_verdicts, name: str, expected: tuple) -> None:
    verdict = borrowed_verdicts[name]
    assert verdict.status is MIRVerdictStatus.COVERED, verdict.reason
    assert verdict.conflicts == expected

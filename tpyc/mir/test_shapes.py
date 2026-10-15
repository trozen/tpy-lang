"""The value description MIR reads everywhere: `value_class` of a type, the
shape `from_thir` gives a slot, `field_shape` a record field, and what
`step` reaches from each class and holding -- the illegal steps included."""

from collections.abc import Iterator
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, modeled_field, storage_leaf, value_class
from ..thir.testutil import _compile, _entry
from ..typesys import (
    INT32, STR, NominalType, OptionalType, ReadonlyType, Representation, TupleType, TpyType, UnionType,
)
from .nodes import (
    MIRContainerElements, MIRContainerStructure, MIRDeref, MIRField, MIRFieldId, MIRHolding, MIROptionalPayload,
    MIRAssign, MIRBorrow, MIRFunction, MIRTupleIndex, MIRUnionPayload,
    MIRPlace, MIRRecordLayout, MIRShape, MIRSlot, MIRSlotId, MIRSlotKind, MIRBodyId, MIRValidationError,
)
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .dump import dump_function
from .shapes import field_shape, from_thir, place_shape, step
from .validate import validate_function

SOURCE = """\
from tpy import int32, StrView, Span, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    n: int32
    s: str
    v: StrView
    p: P | None
    inner: P
    xs: list[int32]
    ro: readonly[list[int32]]

    def __init__(self, s: str, v: StrView, inner: P, xs: list[int32], ro: list[int32]) -> None:
        self.n = 0
        self.s = s
        self.v = v
        self.p = None
        self.inner = inner
        self.xs = xs
        self.ro = ro


def spans(s: Span[int32]) -> int32:
    return len(s)
"""


@dataclass(frozen=True)
class _Program:
    types: dict[str, NominalType]
    fields: dict[str, TpyType]
    records: dict[TpyType, object]
    span: TpyType


@pytest.fixture
def program() -> Iterator[_Program]:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with activate_compiler(compiler):
        layouts = {c.record_name: c.record_layout for c in ctx.thir_constructors.values()}
        types = {name: layout.type for name, layout in layouts.items()}
        # The layouts as a body carries them (no definition is verified here).
        records = {layout.type: MIRRecordLayout(layout.type, tuple(
            MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields), True, True)
            for layout in layouts.values()}
        records[STR] = MIRRecordLayout(STR, (), True, True, opaque=True)
        span = next(fn for node, fn in ctx.thir_functions.items() if node.name == "spans").params[0].type
        yield _Program(types, {f"{name}.{f.name}": f.type for name, layout in layouts.items() for f in layout.fields},
                       records, span)


def _field(program: _Program, name: str) -> MIRField:
    owner = program.types[name.split(".")[0]]
    return MIRField(MIRFieldId(owner, name.split(".")[1]), program.fields[name])


BODY = MIRBodyId("shapes", "example")


def _slot(index: int, shape: MIRShape) -> MIRSlot:
    return MIRSlot(MIRSlotId(BODY, index), shape, MIRSlotKind.PARAMETER)


# --- the class of a type --------------------------------------------------------------

def test_value_class_reads_each_class_once_through_its_access_wrapper(program: _Program) -> None:
    p, ints = program.types["P"], program.fields["H.xs"]
    assert value_class(INT32) is MIRClass.LEAF
    assert value_class(STR) is MIRClass.OWNED_LEAF
    assert value_class(program.fields["H.v"]) is MIRClass.VIEW
    assert value_class(ints) is MIRClass.CONTAINER
    assert value_class(program.span) is MIRClass.CONTAINER_VIEW
    assert value_class(TupleType((INT32, p))) is MIRClass.TUPLE
    assert value_class(OptionalType(p)) is MIRClass.OPTIONAL
    assert value_class(UnionType((p, INT32))) is MIRClass.UNION
    assert value_class(p) is MIRClass.RECORD
    # `readonly[...]` is unwrapped once: a readonly container field is a container.
    assert value_class(program.fields["H.ro"]) is MIRClass.CONTAINER
    assert value_class(ReadonlyType(p)) is MIRClass.RECORD


def test_a_leaf_is_one_only_at_a_representation_that_keeps_it_inert(program: _Program) -> None:
    # The class follows `storage_leaf` at the representation; a type MIR does
    # not model has no class.
    for rep in Representation:
        assert (value_class(INT32, rep) is MIRClass.LEAF) == storage_leaf(INT32, rep)
    assert value_class(NominalType("Box", (INT32,), _module_qname="shapes.Box")) is None


def test_modeled_fields_are_a_class_in_the_admitted_set(program: _Program) -> None:
    p = program.types["P"]
    for name in ("H.n", "H.s", "H.v", "H.p", "H.inner", "H.xs", "H.ro"):
        assert modeled_field(program.fields[name]), name
    # A `readonly[...]` leaf member is no place MIR models; an Optional of a
    # leaf has no record payload.
    assert not modeled_field(ReadonlyType(INT32))
    assert not modeled_field(ReadonlyType(STR))
    assert not modeled_field(OptionalType(INT32))
    assert not modeled_field(TupleType((INT32, p)))


# --- the shape a slot gets from THIR ----------------------------------------------------

def test_from_thir_holds_each_value_by_its_position(program: _Program) -> None:
    p = program.types["P"]
    ref = th.THIRBorrowedRecord(p, True)
    assert from_thir(p, True, reference=ref) == MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True)
    assert from_thir(p, False, storage=True) == MIRShape(p, MIRClass.RECORD, MIRHolding.VALUE)
    assert from_thir(STR, True, reference=th.THIRBorrowedRecord(STR, True)).holding is MIRHolding.POINTER
    # A tuple's record member is owned or borrowed by THIR's selection, never by its type.
    layout = th.THIRTupleLayout((INT32, ref, th.THIROwnedRecord(p)))
    tup = from_thir(TupleType((INT32, p, p)), False, tuple_layout=layout)
    assert [(m.cls, m.holding) for m in tup.elements] == [
        (MIRClass.LEAF, MIRHolding.VALUE), (MIRClass.RECORD, MIRHolding.POINTER), (MIRClass.RECORD, MIRHolding.VALUE)]
    assert tup.form is th.Form.STORAGE
    # An Optional record payload at a slot is a pointer at the record.
    opt = from_thir(OptionalType(p), False, optional_layout=th.THIROptionalLayout(ref))
    assert (opt.payload.cls, opt.payload.holding) == (MIRClass.RECORD, MIRHolding.POINTER) and opt.record_payload
    union = from_thir(UnionType((p, INT32)), False, union_layout=th.THIRUnionLayout(UnionType((p, INT32)), (ref, None)))
    assert union.alternatives[1] is None and union.alternatives[0].holding is MIRHolding.POINTER
    assert from_thir(INT32, True, alias=True) == MIRShape(INT32, MIRClass.LEAF, MIRHolding.ALIAS, True)
    assert from_thir(INT32, False).is_scalar


def test_containers_are_regions_storage_or_cursors(program: _Program) -> None:
    ints = program.fields["H.xs"]
    members = (MIRShape(INT32, MIRClass.LEAF),)
    borrowed = from_thir(ints, False, members=members, borrowed_container=True)
    assert (borrowed.cls, borrowed.holding) == (MIRClass.CONTAINER, MIRHolding.REGION) and borrowed.whole_container
    assert from_thir(ints, False, storage=True, members=members).container_storage
    cursor = from_thir(ints, False, members=members, borrowed_container=True, iterator=True)
    assert cursor.holding is MIRHolding.CURSOR and not cursor.region and cursor.is_holder
    view = from_thir(program.span, True, reference=th.THIRBorrowedRecord(program.span, True), members=members)
    assert (view.cls, view.holding) == (MIRClass.CONTAINER_VIEW, MIRHolding.REGION) and view.container_view_holder


# --- what a projection reaches ------------------------------------------------------------

def test_a_dereference_follows_a_pointer_only(program: _Program) -> None:
    p = program.types["P"]
    holder = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True)
    assert step(holder, MIRDeref(), program.records) == MIRShape(p, MIRClass.RECORD, MIRHolding.VALUE, True)
    with pytest.raises(MIRValidationError, match="dereference needs reference holder"):
        step(replace_holding(holder, MIRHolding.VALUE), MIRDeref(), program.records)
    # A container view is its region: never dereferenced.
    view = MIRShape(program.span, MIRClass.CONTAINER_VIEW, MIRHolding.REGION, True, (MIRShape(INT32, MIRClass.LEAF),))
    with pytest.raises(MIRValidationError, match="dereference needs reference holder"):
        step(view, MIRDeref(), program.records)
    assert step(view, MIRContainerElements(), program.records).cls is MIRClass.LEAF
    with pytest.raises(MIRValidationError, match="analysis places"):
        step(view, MIRContainerStructure(), program.records)


def replace_holding(shape: MIRShape, holding: MIRHolding) -> MIRShape:
    return MIRShape(shape.type, shape.cls, holding, shape.readonly, shape.members)


def test_fields_are_classed_by_their_declared_type(program: _Program) -> None:
    h = program.types["H"]
    storage = MIRShape(h, MIRClass.RECORD, MIRHolding.VALUE)
    shapes = {name: step(storage, _field(program, name), program.records)
              for name in ("H.n", "H.s", "H.v", "H.p", "H.inner", "H.xs")}
    assert {name: (s.cls, s.holding) for name, s in shapes.items()} == {
        "H.n": (MIRClass.LEAF, MIRHolding.VALUE), "H.s": (MIRClass.OWNED_LEAF, MIRHolding.VALUE),
        "H.v": (MIRClass.VIEW, MIRHolding.POINTER), "H.p": (MIRClass.OPTIONAL, MIRHolding.VALUE),
        "H.inner": (MIRClass.RECORD, MIRHolding.VALUE), "H.xs": (MIRClass.CONTAINER, MIRHolding.VALUE)}
    # An Optional record field stores its payload inline.
    assert step(shapes["H.p"], MIROptionalPayload(), program.records) == MIRShape(
        program.types["P"], MIRClass.RECORD, MIRHolding.VALUE)
    assert field_shape(ReadonlyType(INT32), False, program.records) is None


def test_nothing_projects_through_a_view_member(program: _Program) -> None:
    h = program.types["H"]
    holder = _slot(0, MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER))
    slots = {holder.id: holder}
    view = MIRPlace(holder.id, (MIRDeref(), _field(program, "H.v")))
    assert place_shape(view, slots, program.records).cls is MIRClass.VIEW
    with pytest.raises(MIRValidationError, match="projection through a view member"):
        place_shape(MIRPlace(holder.id, (*view.projections, MIRDeref())), slots, program.records)


def test_access_reaches_the_elements_of_a_readonly_container_field(program: _Program) -> None:
    h = program.types["H"]
    mutable = _slot(0, MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER))
    readonly = _slot(1, MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER, True))
    slots = {mutable.id: mutable, readonly.id: readonly}

    def element(slot: MIRSlot, field: str) -> MIRShape:
        place = MIRPlace(slot.id, (MIRDeref(), _field(program, field), MIRContainerElements()))
        return place_shape(place, slots, program.records)
    # A `readonly[list[...]]` field's elements are readonly however its record is reached ...
    assert element(mutable, "H.ro").readonly and not element(mutable, "H.xs").readonly
    # ... and every field of a record reached readonly (`@readonly self`) is too.
    assert element(readonly, "H.xs").readonly
    assert place_shape(MIRPlace(readonly.id, (MIRDeref(), _field(program, "H.inner"))), slots,
                       program.records).readonly


def test_wrapper_members_are_reached_only_by_their_own_projection(program: _Program) -> None:
    p = program.types["P"]
    leaf, pointer = MIRShape(INT32, MIRClass.LEAF), MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER)
    pair = MIRShape(TupleType((INT32, p)), MIRClass.TUPLE, members=(leaf, pointer))
    assert step(pair, MIRTupleIndex(1), program.records) == pointer
    with pytest.raises(MIRValidationError, match="tuple index out of range"):
        step(pair, MIRTupleIndex(2), program.records)
    with pytest.raises(MIRValidationError, match="tuple projection needs tuple payload"):
        step(leaf, MIRTupleIndex(0), program.records)
    union = MIRShape(UnionType((p, INT32)), MIRClass.UNION, members=(pointer, None))
    assert step(union, MIRUnionPayload(0), program.records) == pointer
    with pytest.raises(MIRValidationError, match="invalid union alternative"):
        step(union, MIRUnionPayload(2), program.records)
    with pytest.raises(MIRValidationError, match="absent union alternative has no payload"):
        step(union, MIRUnionPayload(1), program.records)
    with pytest.raises(MIRValidationError, match="union projection needs union payload"):
        step(pair, MIRUnionPayload(0), program.records)
    with pytest.raises(MIRValidationError, match="optional projection needs optional payload"):
        step(pair, MIROptionalPayload(), program.records)


# --- a readonly container field, end to end: built, never written or lent mutably ------

READONLY_SOURCE = """\
from tpy import int32, readonly


class H:
    xs: list[int32]
    ro: readonly[list[int32]]

    def __init__(self, xs: list[int32], ro: list[int32]) -> None:
        self.xs = xs
        self.ro = ro


class G:
    xs: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        self.xs = xs


def make(a: list[int32], b: list[int32]) -> int32:
    h = H(a, b)
    return len(h.ro)


def make_g(a: list[int32]) -> int32:
    g = G(a)
    return len(g.xs)


def put(h: H) -> None:
    h.xs[0] = 1


def grow(h: H) -> None:
    h.xs.append(1)
"""


@pytest.fixture
def readonly_bodies() -> Iterator[dict[str, MIRBodyVerdict]]:
    compiler, modules = _compile(READONLY_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir, activate_compiler(compiler):
        yield {v.body.declaration.split("@")[0]: v for v in enumerate_bodies(
            entry.ast, entry.analyzer, ctx, entry.name, mir.definitions, compiler.thir_reject_by_node, mir.workspace)}


def _swap_field(fn: MIRFunction, old: str, new: MIRField) -> MIRFunction:
    """The body with every projection of field `old` naming `new` instead."""
    def place(p: MIRPlace) -> MIRPlace:
        return MIRPlace(p.root, tuple(new if isinstance(x, MIRField) and x.id.name == old else x
                                      for x in p.projections))

    def stmt(s: object) -> object:
        if not isinstance(s, MIRAssign):
            return s
        value = replace(s.value, source=place(s.value.source)) if isinstance(s.value, MIRBorrow) else s.value
        return replace(s, target=place(s.target), value=value)
    return replace(fn, blocks=tuple(replace(b, statements=tuple(map(stmt, b.statements))) for b in fn.blocks))


def test_a_readonly_container_field_is_built_like_any_container_field(readonly_bodies) -> None:
    # The constructor copies both container parameters into its fields.
    init = readonly_bodies["H.__init__"]
    assert init.status is MIRVerdictStatus.COVERED, init.reason
    assert "initialize-receiver %0 (copy %1 may-raise, copy %2 may-raise)" in dump_function(init.function)
    # A caller's construct refuses a container parameter alike for a readonly field and a mutable one.
    assert readonly_bodies["make"].reason == readonly_bodies["make_g"].reason == "constructor parameter type"


def test_a_readonly_container_field_is_never_written_or_lent_mutably(readonly_bodies) -> None:
    put, grow = readonly_bodies["put"].function, readonly_bodies["grow"].function
    validate_function(put)
    validate_function(grow)
    ro = next(f for r in readonly_bodies["H.__init__"].function.records for f in r.fields if f.id.name == "ro")
    # Storing an element through it, or borrowing it for a mutating call.
    with pytest.raises(MIRValidationError, match="store through readonly container"):
        validate_function(_swap_field(put, "xs", ro))
    with pytest.raises(MIRValidationError, match="borrow increases access"):
        validate_function(_swap_field(grow, "xs", ro))

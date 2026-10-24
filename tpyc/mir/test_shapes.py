"""The value description MIR reads everywhere: `value_class` of a type, the
shape the one composer (`value_shape`) gives a value at each position and
role, and what `step` reaches from each class and holding -- the illegal
steps included."""

from collections.abc import Iterator
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, modeled_field, storage_leaf, value_class
from ..thir.testutil import _compile, _entry
from ..typesys import (
    BYTES, BYTESVIEW, INT32, NONE, STR, STRVIEW, NominalType, OptionalType, OwnType, ReadonlyType,
    Representation, TupleType, TpyType, UnionType,
)
from ..type_def_registry import ParamPassing
from .definitions import lends_view
from .testutil import bound, records_of, with_fields
from .nodes import MIRAssign, MIRBorrow, MIRFunction, MIRPlace, MIRSlot, MIRSlotId, MIRSlotKind, MIRBodyId, place_shape
from .value import (
    MIRField, MIRFieldId, MIRHolding, MIRPosition, MIRRecordLayout, MIRRecords, MIRRole, MIRShape, MIRValidationError,
    lent_record, member_compatible, member_shape, shape_problem, value_shape,
)
from .dependencies import object_keys
from .reach import MIROrigin, origin
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .dump import dump_function
from .shapes import (
    MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload, MIRWalk, field_member, step,
    store_problem, walk, lent_members, lent_prefix, object_path, object_prefix, object_selector, sole_object,
)
from .prepare import validate_function
from .call_contract import path_projections, summary_path


def origin_shape(slot: MIRSlot) -> MIRShape:
    """The shape a referent rooted at `slot` steps from (`origin`)."""
    return origin(MIRPlace(slot.id), {slot.id: slot}).shape


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
    records: MIRRecords
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
        records = records_of(*records.values())
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


# --- the shape the composer gives a value --------------------------------------------

def test_the_composer_holds_each_value_by_its_role(program: _Program) -> None:
    p = program.types["P"]
    ref = th.THIRBorrowedRecord(p, True)
    assert value_shape(p, access=True, role=MIRRole.LOAN) == MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True)
    assert value_shape(p) == MIRShape(p, MIRClass.RECORD, MIRHolding.VALUE)
    assert value_shape(STR, access=True, role=MIRRole.LOAN).holding is MIRHolding.POINTER
    # A tuple's record member is owned or borrowed by THIR's selection, never by its type.
    layout = th.THIRTupleLayout((INT32, ref, th.THIROwnedRecord(p)))
    tup = value_shape(TupleType((INT32, p, p)), facts=layout)
    assert [(m.cls, m.holding) for m in tup.elements] == [
        (MIRClass.LEAF, MIRHolding.VALUE), (MIRClass.RECORD, MIRHolding.POINTER), (MIRClass.RECORD, MIRHolding.VALUE)]
    assert tup.form is th.Form.STORAGE
    # An Optional record payload at a slot is a pointer at the record.
    opt = value_shape(OptionalType(p), facts=th.THIROptionalLayout(ref))
    assert (opt.payload.cls, opt.payload.holding) == (MIRClass.RECORD, MIRHolding.POINTER) and opt.record_payload
    union = value_shape(UnionType((p, INT32)), facts=th.THIRUnionLayout(UnionType((p, INT32)), (ref, None)))
    assert union.alternatives[1] is None and union.alternatives[0].holding is MIRHolding.POINTER
    alias = value_shape(INT32, MIRClass.LEAF, access=True, role=MIRRole.ALIAS)
    assert alias == MIRShape(INT32, MIRClass.LEAF, MIRHolding.ALIAS, True)
    assert value_shape(INT32, MIRClass.LEAF).is_scalar


def test_containers_are_pointers_storage_or_cursors(program: _Program) -> None:
    ints = program.fields["H.xs"]
    members = (MIRShape(INT32, MIRClass.LEAF),)
    borrowed = value_shape(ints, role=MIRRole.LOAN)
    assert (borrowed.cls, borrowed.holding) == (MIRClass.CONTAINER, MIRHolding.POINTER) and borrowed.whole_container
    # The members come from the builder, at the holder's access.
    assert borrowed.members == members
    assert value_shape(ints).is_storage and value_shape(ints).cls is MIRClass.CONTAINER
    cursor = value_shape(ints, role=MIRRole.CURSOR)
    assert cursor.holding is MIRHolding.CURSOR and cursor.is_holder and cursor.members == members
    assert not (cursor.is_storage or cursor.container_view_holder)
    view = value_shape(program.span, access=True, role=MIRRole.LOAN)
    assert (view.cls, view.holding) == (MIRClass.CONTAINER_VIEW, MIRHolding.REGION) and view.container_view_holder
    assert view.members == (MIRShape(INT32, MIRClass.LEAF, readonly=True),)


def test_holder_and_object_questions_of_a_borrowed_container(program: _Program) -> None:
    # A borrowed container is held by a borrow holder, and what it reaches
    # is a whole container.
    ints = program.fields["H.xs"]
    borrowed = value_shape(ints, role=MIRRole.LOAN)
    assert borrowed.borrow_holder and borrowed.borrowed_aggregate and borrowed.whole_container
    storage = value_shape(ints)
    assert storage.whole_container and not storage.borrowed_aggregate and not storage.borrow_holder
    cursor = value_shape(ints, role=MIRRole.CURSOR)
    assert not (cursor.whole_container or cursor.borrowed_aggregate or cursor.borrow_holder)
    view = value_shape(program.span, access=True, role=MIRRole.LOAN)
    assert view.borrow_holder and not (view.whole_container or view.borrowed_aggregate)
    # A cursor over a view has the view's class, and is no view holder.
    view_cursor = value_shape(program.span, access=True, role=MIRRole.CURSOR)
    assert view_cursor.cls is MIRClass.CONTAINER_VIEW and not view_cursor.container_view_holder


def test_a_wrapper_keeps_its_layout_members_whatever_its_holding(program: _Program) -> None:
    p = program.types["P"]
    lent = th.THIRBorrowedRecord(p, True)
    # `readonly[P] | None` lent: the Optional's own slot is storage at its
    # own access, its pointer payload carries the loan's.
    maybe = value_shape(OptionalType(ReadonlyType(p)), role=MIRRole.LOAN, facts=th.THIROptionalLayout(lent))
    assert (maybe.holding, maybe.readonly) == (MIRHolding.VALUE, False)
    assert maybe.payload == MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True)
    # An access given to the Optional's own slot is refused, never dropped.
    with pytest.raises(MIRValidationError, match="readonly Optional slot"):
        value_shape(OptionalType(ReadonlyType(p)), access=True, role=MIRRole.LOAN, facts=th.THIROptionalLayout(lent))
    # A wrapper's members come from its layout facts: none given, it has no shape.
    for wrapper in (OptionalType(INT32), TupleType((INT32, INT32)), UnionType((p, NONE))):
        with pytest.raises(MIRValidationError, match="wrapper without its layout facts"):
            value_shape(wrapper)
    # `int32 | None` keeps its scalar payload.
    scalar = value_shape(OptionalType(INT32), role=MIRRole.LOAN,
                         facts=th.THIROptionalLayout(INT32))
    assert scalar.payload == MIRShape(INT32, MIRClass.LEAF) and scalar.scalar_wrapper
    # A tuple lent at CONST_REF is held by value, its members kept.
    pair = value_shape(TupleType((INT32, p)), role=MIRRole.LOAN,
                       facts=th.THIRTupleLayout((INT32, lent)))
    assert pair.holding is MIRHolding.VALUE and pair.members == (
        MIRShape(INT32, MIRClass.LEAF), MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True))


def test_an_optional_record_payload_is_a_pointer_at_a_parameter_and_inline_at_a_field(program: _Program) -> None:
    p, maybe = program.types["P"], program.fields["H.p"]
    parameter = value_shape(maybe, role=MIRRole.LOAN,
                            facts=th.THIROptionalLayout(th.THIRBorrowedRecord(p, False)))
    assert parameter.payload.holding is MIRHolding.POINTER
    field = value_shape(maybe, position=MIRPosition.FIELD, layouts=program.records)
    assert field.payload == MIRShape(p, MIRClass.RECORD, MIRHolding.VALUE)
    # A field MIR does not model has no shape.
    with pytest.raises(MIRValidationError, match="unsupported field type"):
        value_shape(ReadonlyType(INT32), position=MIRPosition.FIELD, layouts=program.records)


def test_the_lent_record_is_a_record_pointer_an_optional_lends_its_payload(program: _Program) -> None:
    p = program.types["P"]
    pointer = value_shape(p, access=True, role=MIRRole.LOAN)
    assert lent_record(pointer) is pointer
    maybe = value_shape(OptionalType(p), facts=th.THIROptionalLayout(th.THIRBorrowedRecord(p, True)))
    # A wrapper lends its pointer members, an Optional its payload.
    assert lent_record(maybe) is None and lent_members(maybe) == ((MIRUnionPayload(1), pointer),)
    assert maybe.record_payload
    # A field's inline payload is the holder's own storage: it lends nothing.
    inline = value_shape(program.fields["H.p"], position=MIRPosition.FIELD, layouts=program.records)
    assert lent_record(inline) is None and not inline.record_payload
    scalar = value_shape(OptionalType(INT32), facts=th.THIROptionalLayout(INT32))
    for shape in (value_shape(p), value_shape(INT32, MIRClass.LEAF), scalar,
                  value_shape(STR, access=True, role=MIRRole.LOAN)):
        assert lent_record(shape) is None, shape
    assert not scalar.record_payload


def test_a_parameters_lent_record_is_read_from_its_slot_shape(program: _Program) -> None:
    # The stored-loan keys and the origin of a parameter come from the
    # record its shape lends; a local holding the same shape lends nothing
    # of the caller's.
    h = program.types["H"]
    pointer = _slot(0, value_shape(h, role=MIRRole.LOAN))
    maybe = _slot(0, value_shape(OptionalType(h), facts=th.THIROptionalLayout(th.THIRBorrowedRecord(h, False))))
    keys = object_keys(pointer, program.records)
    # The Optional's payload object is keyed under its selector.
    assert keys and object_keys(maybe, program.records) == tuple(
        MIRPlace(k.root, (MIRUnionPayload(1), *k.projections)) for k in keys)
    assert object_keys(replace(maybe, kind=MIRSlotKind.LOCAL), program.records) == ()
    payload = origin(MIRPlace(maybe.id, (MIRUnionPayload(1),)), {maybe.id: maybe}).shape
    assert payload == origin_shape(pointer) == value_shape(h)
    local = replace(maybe, kind=MIRSlotKind.LOCAL)
    assert origin_shape(local) == local.shape


# --- the one shape check ----------------------------------------------------------------

def test_the_shape_check_accepts_what_the_composer_builds(program: _Program) -> None:
    p, ints, view = program.types["P"], program.fields["H.xs"], program.fields["H.v"]
    lent = th.THIRBorrowedRecord(p, True)
    slots = [
        (value_shape(p), p),
        # MIR's types are bare: a loan is the holding, not a `Ref[...]`.
        (value_shape(p, access=True, role=MIRRole.LOAN), p),
        (value_shape(p), OwnType(p)),
        (value_shape(STR, access=True, role=MIRRole.LOAN), STR),
        (value_shape(view, access=True), view),
        (value_shape(ints, role=MIRRole.LOAN), ints),
        (value_shape(ints, role=MIRRole.CURSOR), ints),
        (value_shape(program.span, access=True, role=MIRRole.LOAN), program.span),
        (value_shape(INT32, MIRClass.LEAF, access=True, role=MIRRole.ALIAS), INT32),
        (value_shape(TupleType((INT32, ReadonlyType(p))), facts=th.THIRTupleLayout((INT32, lent))),
         TupleType((INT32, ReadonlyType(p)))),
        (value_shape(OptionalType(ReadonlyType(p)), facts=th.THIROptionalLayout(lent)), OptionalType(ReadonlyType(p))),
        (value_shape(UnionType((ReadonlyType(p), NONE)),
                     facts=th.THIRUnionLayout(UnionType((ReadonlyType(p), NONE)), (lent, None))),
         UnionType((ReadonlyType(p), NONE))),
    ]
    for shape, declared in slots:
        assert shape_problem(shape, declared) is None, (shape, declared)
    for name in ("H.n", "H.s", "H.v", "H.p", "H.inner", "H.xs", "H.ro"):
        typ = program.fields[name]
        assert shape_problem(member_shape(typ, False, program.records), typ, MIRPosition.FIELD) is None, name


def test_the_shape_check_refuses_each_forgery_by_its_rule(program: _Program) -> None:
    p, h, ints, view = program.types["P"], program.types["H"], program.fields["H.xs"], program.fields["H.v"]
    pointer, storage = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER), MIRShape(p, MIRClass.RECORD)
    region = value_shape(ints, role=MIRRole.LOAN)
    pair = TupleType((INT32, p))
    either = UnionType((p, NONE))
    ro_pair, ro_either = TupleType((INT32, ReadonlyType(p))), UnionType((ReadonlyType(p), NONE))
    cases = [
        # A lent owned leaf and a view read only.
        (MIRShape(STR, MIRClass.OWNED_LEAF, MIRHolding.POINTER), STR, "mutable loan of a readonly value"),
        (MIRShape(view, MIRClass.VIEW, MIRHolding.POINTER), view, "mutable loan of a readonly value"),
        # A slot's own container or owned-leaf buffer is written in place: readonly is a loan's.
        (replace(value_shape(ints), readonly=True), ints, "readonly owned storage"),
        (MIRShape(STR, MIRClass.OWNED_LEAF, readonly=True), STR, "readonly owned storage"),
        # A slot's tuple or union is its own mutable storage.
        (MIRShape(pair, MIRClass.TUPLE, readonly=True, members=(MIRShape(INT32, MIRClass.LEAF), pointer)), pair,
         "readonly wrapper slot"),
        (MIRShape(either, MIRClass.UNION, readonly=True, members=(pointer, None)), either,
         "readonly wrapper slot"),
        # A union points at its record member, never holds it by value.
        (MIRShape(either, MIRClass.UNION, members=(storage, None)), either, "unsupported union record holding"),
        # A holding the composer never gives the class: a stored view, a record cursor.
        (MIRShape(view, MIRClass.VIEW, readonly=True), view, "invalid holding"),
        (MIRShape(p, MIRClass.RECORD, MIRHolding.CURSOR), p, "invalid holding"),
        # An `Own[R]` hands the record over: no pointer at it.
        (pointer, OwnType(p), "reference type mismatch"),
        # A member never has more access than its declaration grants.
        (MIRShape(ro_pair, MIRClass.TUPLE, members=(MIRShape(INT32, MIRClass.LEAF), pointer)), ro_pair,
         "tuple layout increases access"),
        (MIRShape(pair, MIRClass.TUPLE, members=(MIRShape(INT32, MIRClass.LEAF), pointer)), ReadonlyType(pair),
         "tuple layout increases access"),
        (MIRShape(ro_either, MIRClass.UNION, members=(pointer, None)), ro_either,
         "union layout increases access"),
        (MIRShape(OptionalType(ReadonlyType(p)), MIRClass.OPTIONAL, members=(None, pointer)),
         OptionalType(ReadonlyType(p)), "optional layout increases access"),
        # A wrapper member holds no borrow, which the wrapper would have to select.
        (MIRShape(TupleType((INT32, h)), MIRClass.TUPLE,
                  members=(MIRShape(INT32, MIRClass.LEAF), MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER))),
         TupleType((INT32, h)), "wrapper holds a borrow"),
        # The class is the type's; a container's members are its type's.
        (MIRShape(STR, MIRClass.LEAF), STR, "value class mismatch"),
        (replace(region, members=(MIRShape(STR, MIRClass.OWNED_LEAF),)), ints,
         "container members disagree with its type"),
    ]
    for shape, declared, reason in cases:
        assert shape_problem(shape, declared) == reason, (shape, declared)
    # A slot's Optional record payload is a pointer, a field's stored inline.
    maybe = program.fields["H.p"]
    inline = MIRShape(maybe, MIRClass.OPTIONAL, members=(None, storage))
    assert shape_problem(inline, maybe) == "unsupported optional record holding"
    assert shape_problem(inline, maybe, MIRPosition.FIELD) is None
    # A field stores its record: no loan of it, and no scalar payload.
    assert shape_problem(pointer, p, MIRPosition.FIELD) == "invalid holding"
    assert shape_problem(replace(inline, members=(None, pointer)), maybe, MIRPosition.FIELD) == (
        "unsupported optional record holding")
    assert shape_problem(replace(inline, members=(None, MIRShape(INT32, MIRClass.LEAF))), maybe, MIRPosition.FIELD) == (
        "unsupported optional payload")
    # A tuple or union lent at CONST_REF is held by value: no loan form to refuse.
    held = value_shape(pair, role=MIRRole.LOAN, facts=th.THIRTupleLayout((INT32, th.THIRBorrowedRecord(p, False))))
    assert held.holding is MIRHolding.VALUE and shape_problem(held, pair) is None


def test_the_shape_check_refuses_a_field_the_composer_refuses(program: _Program) -> None:
    # A tuple, a container view and a `readonly[...]` leaf are no field MIR models.
    ints = TupleType((INT32, INT32))
    fields = [
        (value_shape(ints, facts=th.THIRTupleLayout((INT32, INT32))), ints),
        (value_shape(program.span, access=True, role=MIRRole.LOAN), program.span),
        (MIRShape(INT32, MIRClass.LEAF), ReadonlyType(INT32)),
    ]
    for shape, declared in fields:
        with pytest.raises(MIRValidationError, match="unsupported field type"):
            value_shape(declared, position=MIRPosition.FIELD, layouts=program.records)
        assert shape_problem(shape, declared, MIRPosition.FIELD) == "unsupported field type", declared


def test_a_holder_declared_readonly_is_never_mutable(program: _Program) -> None:
    # The access a `readonly[...]` declaration gives every loan (`loan_access`) bounds the holder's.
    p, ints = program.types["P"], program.fields["H.xs"]
    mutable = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER)
    declared = ReadonlyType(p)
    assert shape_problem(mutable, declared) == "mutable loan of a readonly value"
    assert shape_problem(replace(mutable, readonly=True), declared) is None
    region = value_shape(ints, role=MIRRole.LOAN)
    assert shape_problem(region, ReadonlyType(ints)) == "mutable loan of a readonly value"
    # The callee's own storage lends nothing: no loan access to bound.
    assert shape_problem(MIRShape(p, MIRClass.RECORD), OwnType(p)) is None


def test_a_member_copies_into_the_same_member_never_gaining_access(program: _Program) -> None:
    p = program.types["P"]
    lent, mutable = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER, True), MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER)
    assert member_compatible(mutable, lent) and member_compatible(lent, lent)
    assert not member_compatible(lent, mutable)
    # The same value held another way, or another value, is no copy of it.
    assert not member_compatible(MIRShape(p, MIRClass.RECORD), mutable)
    assert not member_compatible(MIRShape(STRVIEW, MIRClass.VIEW, MIRHolding.POINTER, True),
                                 MIRShape(BYTESVIEW, MIRClass.VIEW, MIRHolding.POINTER, True))
    assert member_compatible(MIRShape(INT32, MIRClass.LEAF), MIRShape(INT32, MIRClass.LEAF))
    # Members are part of the value: the same container with other members is another value.
    ints = program.fields["H.xs"]
    region = value_shape(ints, role=MIRRole.LOAN)
    assert not member_compatible(region, replace(region, members=()))
    # A non-record member, too, copies into a readonly one, never back.
    readonly_leaf = MIRShape(INT32, MIRClass.LEAF, readonly=True)
    assert member_compatible(MIRShape(INT32, MIRClass.LEAF), readonly_leaf)
    assert not member_compatible(readonly_leaf, MIRShape(INT32, MIRClass.LEAF))


def test_a_view_member_stores_only_a_loan_of_its_own_family(program: _Program) -> None:
    # A `StrView` member holds a borrow of a `str` (or `StrView`) parameter, never of bytes.
    assert lends_view(bound(STR, ParamPassing.VIEW, True), STRVIEW)
    assert lends_view(bound(STRVIEW, ParamPassing.VALUE, True), STRVIEW)
    assert not lends_view(bound(BYTES, ParamPassing.VIEW, True), STRVIEW)
    assert not lends_view(bound(BYTESVIEW, ParamPassing.VALUE, True), STRVIEW)


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
    assert step(shapes["H.p"], MIRUnionPayload(1), program.records) == MIRShape(
        program.types["P"], MIRClass.RECORD, MIRHolding.VALUE)


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
    with pytest.raises(MIRValidationError, match="absent alternative has no payload"):
        step(union, MIRUnionPayload(1), program.records)
    with pytest.raises(MIRValidationError, match="payload projection needs a sum"):
        step(pair, MIRUnionPayload(0), program.records)
    with pytest.raises(MIRValidationError, match="payload projection needs a sum"):
        step(pair, MIRUnionPayload(1), program.records)


def test_an_optional_fields_inline_payload_is_stored_at_the_fields_access(program: _Program) -> None:
    # The composer places the holder's access on the inline payload; `step` reads it as stored.
    readonly = member_shape(program.fields["H.p"], True, program.records)
    mutable = member_shape(program.fields["H.p"], False, program.records)
    assert readonly.payload.readonly and not mutable.payload.readonly
    assert step(readonly, MIRUnionPayload(1), program.records) is readonly.payload
    holder = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.VALUE, True)
    assert step(holder, _field(program, "H.p"), program.records) == readonly
    # The shape check holds the payload to the field's access, as a container's members.
    typ = program.fields["H.p"]
    assert shape_problem(readonly, typ, MIRPosition.FIELD) is None
    forged = replace(readonly, members=mutable.members)
    assert shape_problem(forged, typ, MIRPosition.FIELD) == "optional layout increases access"


def test_a_field_is_a_member_of_its_holders_declared_fields(program: _Program) -> None:
    h, p = program.types["H"], program.types["P"]
    storage = MIRShape(h, MIRClass.RECORD, MIRHolding.VALUE)
    n = _field(program, "H.n")
    # A field the holder does not declare is refused, an own-type one included.
    fabricated = MIRField(MIRFieldId(h, "nope"), INT32)
    with pytest.raises(MIRValidationError, match="field missing from declared fields"):
        step(storage, fabricated, program.records)
    with pytest.raises(MIRValidationError, match="field missing from declared fields"):
        step(storage, MIRField(n.id, STR), program.records)
    # With no layout for the holder the answer is the same: membership is the declaration's.
    declared_only = MIRRecords({}, program.records.fields)
    assert step(storage, n, declared_only).cls is MIRClass.LEAF
    with pytest.raises(MIRValidationError, match="field missing from declared fields"):
        step(storage, fabricated, declared_only)
    with pytest.raises(MIRValidationError, match="field missing from declared fields"):
        step(storage, MIRField(MIRFieldId(p, "x"), INT32), declared_only)
    # An ancestor's field is a member of a record that declares it, keyed by its owner.
    inherited = MIRField(MIRFieldId(p, "x"), INT32)
    derived = {h: program.records.fields[h] | {inherited}}
    assert field_member(derived, h, p, "x", INT32) is None
    assert step(storage, inherited, MIRRecords({}, derived)).cls is MIRClass.LEAF
    assert field_member(program.records.fields, h, p, "x", INT32) == "field missing from declared fields"
    # A field is named by a non-empty string, whatever a declaration lists.
    for name in (123, ""):
        listed = {h: program.records.fields[h] | {MIRField(MIRFieldId(h, name), INT32)}}
        assert field_member(listed, h, h, name, INT32) == "field owner mismatch", name
        assert field_member({}, h, h, name, INT32) == "field owner mismatch", name


def test_the_object_prefix_follows_the_holding(program: _Program) -> None:
    # A pointer's object is behind its dereference; a wrapper lends one
    # object per pointer member, behind its selector and a dereference;
    # storage is its own object.
    p = program.types["P"]
    pointer = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER)
    leaf = MIRShape(INT32, MIRClass.LEAF)
    optional = MIRShape(OptionalType(p), MIRClass.OPTIONAL, members=(None, pointer))
    union = MIRShape(UnionType((p, NONE)), MIRClass.UNION, members=(pointer, None))
    pair = MIRShape(TupleType((p, INT32)), MIRClass.TUPLE, members=(pointer, leaf))
    storage = MIRShape(p, MIRClass.RECORD, MIRHolding.VALUE)
    assert object_prefix(pointer) == (MIRDeref(),)
    assert object_prefix(optional) == object_prefix(storage) == ()
    assert lent_members(optional) == ((MIRUnionPayload(1), pointer),)
    assert lent_members(pair) == ((MIRTupleIndex(0), pointer),)
    assert lent_members(storage) == ()
    field = MIRField(MIRFieldId(p, "x"), INT32)
    # The translation: a dereference after the root and after a selector
    # reaching a pointer member, never after a field or a scalar member.
    assert object_path(pointer, (field,)) == (MIRDeref(), field)
    assert object_path(optional, (MIRUnionPayload(1), field)) == (MIRUnionPayload(1), MIRDeref(), field)
    assert object_path(union, (MIRUnionPayload(0),)) == (MIRUnionPayload(0), MIRDeref())
    assert object_path(pair, (MIRTupleIndex(1),)) == (MIRTupleIndex(1),)
    assert object_path(storage, (field,)) == (field,)
    assert lent_prefix(pair, (MIRTupleIndex(0), MIRDeref(), field)) == (MIRTupleIndex(0), MIRDeref())
    assert lent_prefix(pair, (MIRTupleIndex(1),)) is None
    assert sole_object(optional) == (MIRUnionPayload(1), MIRDeref())
    # An Optional of a scalar lends nothing.
    scalar = MIRShape(OptionalType(INT32), MIRClass.OPTIONAL, members=(None, leaf))
    assert lent_members(scalar) == () and sole_object(scalar) == ()


def test_one_translation_maps_each_wrappers_summary_paths_to_body_places(program: _Program) -> None:
    # Optional, union and tuple alike: a dereference after the selector of
    # a pointer member, none after a field hop (an inline record member is
    # its holder's storage), and a selector alone names the whole object.
    h, p = program.types["H"], program.types["P"]
    pointer = MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER)
    other = MIRShape(p, MIRClass.RECORD, MIRHolding.POINTER)
    leaf = MIRShape(INT32, MIRClass.LEAF)
    inner, x = _field(program, "H.inner"), _field(program, "P.x")
    wrappers = {
        MIRUnionPayload(1): MIRShape(OptionalType(h), MIRClass.OPTIONAL, members=(None, pointer)),
        MIRUnionPayload(0): MIRShape(UnionType((h, p)), MIRClass.UNION, members=(pointer, other)),
        MIRTupleIndex(0): MIRShape(TupleType((h, INT32)), MIRClass.TUPLE, members=(pointer, leaf)),
    }
    for selector, wrapper in wrappers.items():
        assert object_path(wrapper, (selector,)) == (selector, MIRDeref())
        through = object_path(wrapper, (selector, inner, x))
        assert through == (selector, MIRDeref(), inner, x)
        assert summary_path(through) == (selector, *(th.THIRFieldIdentity(f.id.owner, f.id.name, f.type)
                                                      for f in (inner, x)))
        assert path_projections(wrapper, summary_path(through)) == through
        assert walk(wrapper, through, program.records).end.type == INT32
        assert lent_prefix(wrapper, through) == (selector, MIRDeref())
        # The one answer to which lent object a path starts in; a path entering none has none.
        assert object_selector(wrapper, (selector, inner)) == (selector,)
        assert object_selector(wrapper, (inner,)) is None
    assert object_selector(pointer, (inner,)) == ()
    # A union's other pointer member is its own object; a tuple's scalar member lends none.
    union = wrappers[MIRUnionPayload(0)]
    assert object_path(union, (MIRUnionPayload(1), x)) == (MIRUnionPayload(1), MIRDeref(), x)
    assert sole_object(union) == ()
    assert object_path(wrappers[MIRTupleIndex(0)], (MIRTupleIndex(1),)) == (MIRTupleIndex(1),)


# --- the walk: each hop's holder and what it reaches, and the end -------------------------

def _hops(walked: MIRWalk) -> list[tuple[MIRShape, MIRShape | None]]:
    return [(hop.holder, hop.reached) for hop in walked.hops]


def test_a_walk_ends_at_its_root_for_an_empty_path(program: _Program) -> None:
    root = MIRShape(program.types["P"], MIRClass.RECORD, MIRHolding.POINTER)
    walked = walk(root, (), program.records)
    assert walked.hops == () and walked.end is root


def test_a_deref_hop_holds_the_pointer_and_reaches_the_pointee(program: _Program) -> None:
    root = MIRShape(program.types["P"], MIRClass.RECORD, MIRHolding.POINTER, True)
    walked = walk(root, (MIRDeref(),), program.records)
    assert _hops(walked) == [(root, replace_holding(root, MIRHolding.VALUE))]
    assert walked.end == replace_holding(root, MIRHolding.VALUE)


def test_a_field_hop_holds_the_record_and_reaches_the_member(program: _Program) -> None:
    root = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.VALUE)
    walked = walk(root, (_field(program, "H.inner"),), program.records)
    assert _hops(walked) == [(root, MIRShape(program.types["P"], MIRClass.RECORD, MIRHolding.VALUE))]


def test_an_optional_payload_hop_holds_the_optional_and_reaches_its_payload(program: _Program) -> None:
    root = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.VALUE)
    walked = walk(root, (_field(program, "H.p"), MIRUnionPayload(1)), program.records)
    field = member_shape(program.fields["H.p"], False, program.records)
    assert _hops(walked)[1] == (field, field.payload)


def test_a_tuple_hop_holds_the_tuple_and_reaches_the_member(program: _Program) -> None:
    pointer = MIRShape(program.types["P"], MIRClass.RECORD, MIRHolding.POINTER)
    pair = MIRShape(TupleType((INT32, program.types["P"])), MIRClass.TUPLE,
                    members=(MIRShape(INT32, MIRClass.LEAF), pointer))
    assert _hops(walk(pair, (MIRTupleIndex(1),), program.records)) == [(pair, pointer)]


def test_a_union_hop_holds_the_union_and_reaches_the_alternative(program: _Program) -> None:
    pointer = MIRShape(program.types["P"], MIRClass.RECORD, MIRHolding.POINTER)
    union = MIRShape(UnionType((program.types["P"], INT32)), MIRClass.UNION, members=(pointer, None))
    assert _hops(walk(union, (MIRUnionPayload(0),), program.records)) == [(union, pointer)]


def test_an_elements_hop_holds_the_container_and_reaches_the_stored_member(program: _Program) -> None:
    root = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.VALUE, True)
    walked = walk(root, (_field(program, "H.xs"), MIRContainerElements()), program.records)
    container = member_shape(program.fields["H.xs"], True, program.records)
    assert _hops(walked)[1] == (container, container.subscript) and walked.end.readonly


def test_a_structure_hop_ends_the_walk_at_the_terminal_region(program: _Program) -> None:
    root = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.VALUE)
    xs = _field(program, "H.xs")
    walked = walk(root, (xs, MIRContainerStructure()), program.records)
    container = member_shape(program.fields["H.xs"], False, program.records)
    assert _hops(walked)[1] == (container, None) and walked.end is None
    with pytest.raises(MIRValidationError, match="analysis places"):
        walk(root, (xs, MIRContainerStructure(), MIRContainerElements()), program.records)


# --- the write rule over a walk (`store_problem`) ------------------------------------------

def _with_field(program: _Program, field: MIRField) -> MIRRecords:
    """The records with `field` added to H's layout and declared fields."""
    h = program.types["H"]
    layout = replace(program.records[h], fields=(*program.records[h].fields, field))
    records = records_of(*(layout if r.type == h else r for r in program.records.values()))
    return MIRRecords(records, with_fields(records.fields, (field,)))


def _frozen_items(program: _Program) -> tuple[MIRField, MIRRecords]:
    """`H.frozen: list[readonly[P]]`, added to H's layout."""
    h, p = program.types["H"], program.types["P"]
    frozen = MIRField(MIRFieldId(h, "frozen"), NominalType("list", (ReadonlyType(p),), _module_qname="builtins.list"))
    return frozen, _with_field(program, frozen)


def test_a_mutable_container_replaces_a_readonly_element_never_writes_into_it(program: _Program) -> None:
    frozen, records = _frozen_items(program)
    root = MIRShape(program.types["H"], MIRClass.RECORD, MIRHolding.POINTER)
    element = (MIRDeref(), frozen, MIRContainerElements())
    assert walk(root, element, records).end.readonly
    assert store_problem(walk(root, element, records)) is None
    assert store_problem(walk(root, (*element, _field(program, "P.x")), records)) == "store through readonly storage"


def test_a_readonly_field_is_never_replaced(program: _Program) -> None:
    h = program.types["H"]
    ro = _field(program, "H.ro")
    root = MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER)
    assert store_problem(walk(root, (MIRDeref(), ro), program.records)) == "store through readonly storage"
    # Its elements are readonly through it, and so is its structure.
    assert store_problem(walk(root, (MIRDeref(), ro, MIRContainerElements()), program.records)) == (
        "store through readonly container")
    assert store_problem(walk(root, (MIRDeref(), _field(program, "H.xs")), program.records)) is None
    # A `readonly[P]` record member alike (the validator's member write:
    # test_nested_records, `test_member_write_validator_negatives`).
    frozen = MIRField(MIRFieldId(h, "frozen"), ReadonlyType(program.types["P"]))
    records = _with_field(program, frozen)
    assert store_problem(walk(root, (MIRDeref(), frozen), records)) == "store through readonly storage"
    assert store_problem(walk(root, (MIRDeref(), _field(program, "H.inner")), records)) is None


def test_no_store_goes_through_a_readonly_holder(program: _Program) -> None:
    h = program.types["H"]
    frozen = MIRShape(h, MIRClass.RECORD, MIRHolding.POINTER, True)
    assert store_problem(walk(frozen, (MIRDeref(), _field(program, "H.n")), program.records)) == (
        "store through readonly reference")
    storage = MIRShape(h, MIRClass.RECORD, MIRHolding.VALUE, True)
    assert store_problem(walk(storage, (_field(program, "H.inner"), _field(program, "P.x")), program.records)) == (
        "store through readonly storage")


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
    assert "initialize-receiver %0 (copy (*%1) may-raise, copy (*%2) may-raise)" in dump_function(init.function)
    # A caller's construct lends a container argument alike for a readonly
    # field and a mutable one; `len(h.ro)` then stops at a readonly
    # container field read as a stub protocol argument, a gap of its own.
    assert readonly_bodies["make_g"].function is not None, readonly_bodies["make_g"].reason
    assert readonly_bodies["make"].reason == "stub protocol argument is not a builtin leaf"


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


def test_one_origin_names_a_leafs_place_and_the_shape_its_paths_step_from(program: _Program) -> None:
    p, h = program.types["P"], program.types["H"]
    # A Span names the elements region it views, from its own wrapper shape.
    span = _slot(0, value_shape(program.span, access=True, role=MIRRole.LOAN))
    assert origin(MIRPlace(span.id), {span.id: span}) == MIROrigin(
        MIRPlace(span.id, (MIRContainerElements(),)), span.shape)
    # An Optional record parameter's payload is its own origin, from the record it lends.
    maybe = _slot(0, value_shape(OptionalType(h), facts=th.THIROptionalLayout(th.THIRBorrowedRecord(h, False))))
    assert origin(MIRPlace(maybe.id, (MIRUnionPayload(1),)), {maybe.id: maybe}) == MIROrigin(
        MIRPlace(maybe.id, (MIRUnionPayload(1),)), value_shape(h))
    # Each borrowed member of a tuple parameter is its own origin.
    lent = th.THIRBorrowedRecord(p, False)
    pair = _slot(0, value_shape(TupleType((p, p)), role=MIRRole.LOAN, facts=th.THIRTupleLayout((lent, lent))))
    first, second = (origin(MIRPlace(pair.id, (MIRTupleIndex(i),)), {pair.id: pair}) for i in (0, 1))
    assert (first.place, second.place) == (MIRPlace(pair.id, (MIRTupleIndex(0),)), MIRPlace(pair.id, (MIRTupleIndex(1),)))
    assert first.shape == second.shape == value_shape(p)


def test_a_view_never_changes_its_containers_shape(program: _Program) -> None:
    view = value_shape(program.span, role=MIRRole.LOAN)
    assert not view.readonly
    assert store_problem(walk(view, (MIRContainerStructure(),), program.records)) == (
        "store changes the shape of a viewed container")
    assert store_problem(walk(view, (MIRContainerElements(),), program.records)) is None
    # Container storage changes its own shape.
    owned = value_shape(program.fields["H.xs"])
    assert store_problem(walk(owned, (MIRContainerStructure(),), program.records)) is None

"""The one member step for stored values (`stored_members`): what each kind
of value stores inline, one step deep, and the walks that step with it --
records read through wrapper fields, a container holding another through
a wrapper member, a referent under a tuple field, stored-loan keys, copy
facts, layouts and may-hold through tuples, unions and Optionals."""

from collections.abc import Iterator
from dataclasses import dataclass, replace
from types import MappingProxyType, SimpleNamespace

import pytest

import tpyc.mir.definitions as definitions_module
import tpyc.mir.shapes as shapes
from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.scalar_leaves import (
    UNEXPANDED, UNSUPPORTED_ELEMENT, MemberKind, MemberSelector, StoredMembers, members_problem,
    optional_record_field, plain_record_element, stored_members, stored_types,
)
from ..thir.testutil import _compile, _entry
from ..typesys import (
    INT32, NONE, STR, STRVIEW, NominalType, OptionalType, OwnType, ReadonlyType, TpyType, TupleType, UnionType,
    make_union,
)
from .collect import enumerate_bodies
from .coverage import MIRUnsupported
from .definitions import (
    MIRAggregateFacts, MIRDefinitions, _member_definitions, _read_records, aggregate_facts, container_definition,
    layout_copy_may_raise,
)
from .dependencies import _member_keys, resolve_referents
from .lower import _Lowering
from .nodes import MIRBodyId, MIRFunction, MIRPlace, MIRSlot, MIRSlotId, MIRSlotKind
from .prepare import validate_function
from .reach import MIRReferent, _container_holds, _may_hold, reaches, storage_exclusions
from .shapes import MIRDeref, MIRTupleIndex, MIRUnionPayload
from .summaries import _member_records
from .testutil import (
    borrowed_shape, hand_record, leaf_shape, lower_constructor, no_layouts, optional_shape, owned_shape, records_of,
    tuple_shape, union_shape,
)
from .validate import field_layout_problem
from .value import (
    MIRField, MIRFieldId, MIRRecordLayout, MIRRecords, MIRValidationError, _layouts_problem, inline_members,
    nested_members, no_fields, shape_problem, storage_shape,
)

SOURCE = """\
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Pair:
    pair: tuple[A, int32]

    def __init__(self, a: A) -> None:
        self.pair = (a, 1)


class Deep:
    pair: tuple[tuple[A, int32], int32]

    def __init__(self, a: A) -> None:
        self.pair = ((a, 1), 2)


class Maybe:
    o: A | None

    def __init__(self) -> None:
        self.o = None


class Rows:
    rows: list[tuple[A, int32]]
    bs: list[B]

    def __init__(self) -> None:
        self.rows = []
        self.bs = []


class Grid:
    grid: list[list[B]]

    def __init__(self) -> None:
        self.grid = []


class Opts:
    bs: list[B | None]

    def __init__(self) -> None:
        self.bs = []


class Twice:
    first: B
    second: B
    bs: list[B]

    def __init__(self, b: B) -> None:
        self.first = b
        self.second = b
        self.bs = []


type Tree = int32 | list[Tree]


def use(t: Tree) -> None:
    pass
"""

# A record behind a field the certificate refuses (a pointer Optional, the
# forward reference closing a cycle) keeps the refusal its definition has.
CYCLE = """\
from tpy import Own


class A:
    b: "B | None"

    def __init__(self) -> None:
        self.b = None


class B:
    a: A

    def __init__(self, a: A) -> None:
        self.a = a


def make_b(a: A) -> Own[B]:
    return B(a)
"""


@dataclass
class _Program:
    types: dict[str, NominalType]
    constructors: dict[str, th.THIRConstructor]
    tree: TpyType


@pytest.fixture
def program() -> Iterator[_Program]:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with activate_compiler(compiler):
        constructors = {c.record_name: c for c in ctx.thir_constructors.values()}
        use = next(fn for node, fn in ctx.thir_functions.items() if node.name == "use")
        yield _Program({name: c.record_layout.type for name, c in constructors.items()}, constructors,
                       use.params[0].type)


def _list(*args: object) -> NominalType:
    return NominalType("list", tuple(args), _module_qname="builtins.list")


def _dict(*args: object) -> NominalType:
    return NominalType("dict", tuple(args), _module_qname="builtins.dict")


ALT, ELEM, HELD = MemberKind.ALTERNATIVE, MemberKind.ELEMENT, MemberKind.CONTAINED


# --- the step ---------------------------------------------------------------------

def test_each_kind_stores_its_members_one_step_deep(program: _Program) -> None:
    a = program.types["A"]
    # An Optional's payload is alternative 1, beside the absent 0, scalar or owned leaf alike.
    assert stored_members(OptionalType(STR), no_fields) == StoredMembers(((MemberSelector(ALT, 1), STR),), 2)
    assert stored_members(OptionalType(INT32), no_fields) == StoredMembers(((MemberSelector(ALT, 1), INT32),), 2)
    assert stored_members(TupleType((a, INT32)), no_fields) == StoredMembers(
        ((MemberSelector(ELEM, 0), a), (MemberSelector(ELEM, 1), INT32)), 2)
    # One step: a list of tuples stores the tuple, not the tuple's members.
    row = TupleType((_list(INT32), INT32))
    assert stored_members(_list(row), no_fields) == StoredMembers(((MemberSelector(HELD, 0), row),), 1)
    # A dict stores its key and its value.
    assert stored_members(_dict(STR, a), no_fields) == StoredMembers(
        ((MemberSelector(HELD, 0), STR), (MemberSelector(HELD, 1), a)), 2)
    # A member keeps its access wrapper; the value's own `readonly` is seen through, never an `Own`.
    assert stored_types(TupleType((a, ReadonlyType(INT32))), no_fields) == (a, ReadonlyType(INT32))
    assert stored_types(ReadonlyType(OptionalType(a)), no_fields) == (a,)
    assert stored_types(OwnType(_list(a)), no_fields) is UNEXPANDED
    # A leaf stores nothing; a record its fields as the reader's source gives them.
    assert stored_members(INT32, no_fields) == StoredMembers((), 0)
    assert stored_members(STR, no_fields) == StoredMembers((), 0)
    assert stored_members(a, no_fields) is UNEXPANDED
    assert stored_members(a, lambda _: (("x", INT32),)) == StoredMembers((("x", INT32),), 1)


def test_an_owned_member_is_read_as_declared(program: _Program) -> None:
    a = program.types["A"]
    # Storage never holds an `Own[...]`: the step keeps the member as declared...
    assert stored_types(TupleType((OwnType(_list(INT32)), INT32)), no_fields) == (OwnType(_list(INT32)), INT32)
    assert stored_types(a, lambda _: (("x", OwnType(STR)),)) == (OwnType(STR),)
    # ...and the walks that collect what storage holds do not enter it. (The may-hold answer for an
    # `Own` member is left unpinned: it answers "cannot" as the base did, which is not the
    # conservative answer -- TODO.md, step D's precondition.)
    inner = _list(INT32)
    for owned in (OwnType(inner), OwnType(ReadonlyType(OwnType(inner)))):
        assert inner not in set(nested_members([TupleType((owned,))], lambda _: ()))
    assert a not in set(nested_members([TupleType((OwnType(a), INT32))], lambda _: ()))
    # The composer stores no `Own` member, nor an `Own` value asked for directly.
    assert storage_shape(TupleType((OwnType(a), INT32))) == "unsupported stored members"
    assert storage_shape(OwnType(a)) == "unsupported stored members"


def test_a_wrapper_member_declared_owned_is_checked_as_declared(program: _Program) -> None:
    a = program.types["A"]
    pointer = borrowed_shape(a)
    # An `Own[R]` member hands the record over: no pointer at it, in any wrapper.
    for typ, shape in ((TupleType((OwnType(a), INT32)), lambda t: tuple_shape(t, (pointer, leaf_shape(INT32)))),
                       (OptionalType(OwnType(a)), lambda t: optional_shape(t, pointer)),
                       (UnionType((OwnType(a), NONE)), lambda t: union_shape(t, (pointer, None)))):
        assert shape_problem(shape(typ), typ) == "reference type mismatch", typ
    # An inert scalar is of exactly its declared type: an `Own[int32]` is none.
    owned_scalar = TupleType((OwnType(INT32), INT32))
    assert shape_problem(tuple_shape(owned_scalar, (leaf_shape(INT32), leaf_shape(INT32))), owned_scalar) == (
        "unsupported tuple scalar")


def test_admission_refuses_a_declared_owned_member(program: _Program) -> None:
    a = program.types["A"]
    # Admission reads the declared member: an `Own[...]` one is not modeled, at any depth.
    owned_rows = _list(OwnType(_list(INT32)))
    assert members_problem(owned_rows) == UNSUPPORTED_ELEMENT
    assert storage_shape(owned_rows) == UNSUPPORTED_ELEMENT
    assert storage_shape(_list(_list(OwnType(INT32)))) == UNSUPPORTED_ELEMENT
    # A body's layout whose field is declared `Own[...]` is no leaf layout a container admits.
    forged = MIRRecordLayout(a, (MIRField(MIRFieldId(a, "x"), OwnType(INT32)),), True, True)
    assert _layouts_problem(storage_shape(_list(a)).members, {a: forged}) == UNSUPPORTED_ELEMENT
    assert storage_shape(_list(a), layouts={a: forged}) == UNSUPPORTED_ELEMENT
    # A record whose declared field is `Own[...]` is no plain element; an Optional of an `Own[...]` record no
    # record MIR models in place.
    assert plain_record_element(hand_record("PlainText", "stored.PlainText", x=STR))
    assert not plain_record_element(hand_record("OwnText", "stored.OwnText", x=OwnType(STR)))
    assert optional_record_field(OptionalType(a)) == a
    assert optional_record_field(OptionalType(OwnType(a))) is None

def test_union_alternatives_keep_their_canonical_index(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    # Selected by the index in the canonical member order (None, A, B); the void member stores nothing.
    assert make_union(b, NONE, a).members == (NONE, a, b)
    assert stored_members(make_union(b, NONE, a), no_fields) == StoredMembers(
        ((MemberSelector(ALT, 1), a), (MemberSelector(ALT, 2), b)), 3)
    assert storage_shape(make_union(b, NONE, a)).members == (None, owned_shape(a), owned_shape(b))


def test_a_pointer_pinned_optional_still_stores_its_payload_inline(program: _Program) -> None:
    pinned = OptionalType(INT32, force_pointer_repr=True)
    assert stored_members(pinned, no_fields) == StoredMembers(((MemberSelector(ALT, 1), INT32),), 2)
    assert storage_shape(pinned).payload == leaf_shape(INT32)


def test_a_recursive_alias_union_is_unexpanded(program: _Program) -> None:
    assert program.tree.needs_wrapper()
    assert stored_members(program.tree, no_fields) is UNEXPANDED


# --- storage shapes -------------------------------------------------------------------

def test_storage_shapes_compose_from_the_step(program: _Program) -> None:
    a = program.types["A"]
    # An Optional stores its payload inline; a container its members, at every depth.
    assert storage_shape(OptionalType(a)).members == (None, owned_shape(a))
    nested = storage_shape(_list(_list(INT32)), True)
    assert nested.members == (owned_shape(_list(INT32), True, (leaf_shape(INT32, True),)),)
    assert storage_shape(_list(ReadonlyType(a))).members == (owned_shape(a, True),)
    # A wrapper inside a wrapper is stored inline at every depth.
    pair = TupleType((OptionalType(a), INT32))
    stored = storage_shape(pair)
    assert stored.members == (storage_shape(OptionalType(a)), leaf_shape(INT32))
    assert stored.owns_storage and stored.members[0].owns_storage and not stored.members[0].is_storage
    assert list(inline_members(stored.members)) == [owned_shape(a), leaf_shape(INT32)]


def test_storage_of_a_readonly_container_is_refused(program: _Program) -> None:
    # Storage is of its own type: `readonly[...]` is a loan's access, never storage's.
    assert storage_shape(ReadonlyType(_list(INT32))) == "unsupported native container type"
    assert storage_shape(_list(INT32)).members == (leaf_shape(INT32),)


def test_a_slot_optional_stores_no_owned_leaf_payload(program: _Program) -> None:
    # A slot's Optional stores a record or a container inline, not an owned leaf.
    shape = storage_shape(OptionalType(STR))
    assert shape.payload == owned_shape(STR)
    assert shape_problem(shape, OptionalType(STR)) == "unsupported optional scalar"


def test_layouts_are_checked_through_a_wrapper_member(program: _Program) -> None:
    a = program.types["A"]
    members = storage_shape(TupleType((OptionalType(a), STR))).members
    assert _layouts_problem(members, {}) == "missing record layout"
    layout = MIRRecordLayout(a, (MIRField(MIRFieldId(a, "x"), INT32),), True, True)
    assert _layouts_problem(members, {a: layout}) == "missing owned-leaf layout"


# --- walks over records and layouts -------------------------------------------------

def test_nested_members_step_through_every_stored_member(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    row = TupleType((a, INT32))
    # A storage walk: a container's wrapper element and the record inside it...
    assert set(nested_members([_list(row)], lambda _: ())) == {_list(row), row, a, INT32}
    # ...a record's fields as the source gives them, wrappers and records among them.
    holder = hand_record("Through", "stored.Through", t=TupleType((OptionalType(a), INT32)), b=b)
    fields = {holder: (MIRField(MIRFieldId(holder, "t"), TupleType((OptionalType(a), INT32))),
                       MIRField(MIRFieldId(holder, "b"), b))}
    reached = set(nested_members([holder], lambda r: fields.get(r, ())))
    assert {OptionalType(a), a, b} <= reached


def test_records_are_read_through_what_the_certificate_admits(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    # A modeled Optional field's payload record is read with the record's definition...
    assert a in _read_records(program.constructors["Maybe"])
    # ...as is a tuple field's record member; a tuple holding a tuple, which the
    # certificate refuses, is read through by none...
    assert a in _read_records(program.constructors["Pair"])
    assert a not in _read_records(program.constructors["Deep"])
    # ...and a container's wrapper element's record, the last field first.
    assert _read_records(program.constructors["Rows"]) == [b, a]
    # Through nested containers, at any depth; a record two members reach is read once.
    assert _read_records(program.constructors["Grid"]) == [b]
    assert _read_records(program.constructors["Twice"]) == [b]
    # A container's Optional element stores its payload record inline: it is read.
    assert _read_records(program.constructors["Opts"]) == [b]


def test_a_refused_cyclic_member_keeps_its_definition_refusal() -> None:
    compiler, modules = _compile(CYCLE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as analysis:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, analysis.definitions,
                                    compiler.thir_reject_by_node, analysis.workspace)
    make_b = next(v for v in verdicts if v.name == "make_b")
    assert make_b.reason == "member definition: unsupported record fields"


def test_member_definitions_verify_through_a_wrapper_field(program: _Program) -> None:
    a = program.types["A"]
    ctor = program.constructors["Maybe"]
    # The Optional field's payload record verifies as a record field would, an owned one too.
    with pytest.raises(MIRUnsupported, match="member definition: refused"):
        _member_definitions(ctor, ctor.record_layout, {a: "refused"})
    # An `Own[...]` member is no class a walk enters: nothing behind it is read.
    owned = SimpleNamespace(fields=(SimpleNamespace(type=TupleType((OwnType(a), INT32))),))
    _member_definitions(ctor, owned, {a: "refused"})


def test_a_container_refuses_an_element_layout_before_reading_behind_it(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    # The element's layout stores a record field: refused as the container's element, B's definition unread.
    forged = MIRRecordLayout(a, (MIRField(MIRFieldId(a, "x"), b),), True, True)
    read: list[TpyType] = []

    def definition_of(typ: NominalType) -> object:
        read.append(typ)
        return SimpleNamespace(layout=forged) if typ == a else "refused"

    with pytest.raises(MIRUnsupported, match=UNSUPPORTED_ELEMENT):
        container_definition(None, _list(a), definition_of)
    assert read == [a]


def _owner(name: str, **fields: TpyType) -> tuple[NominalType, MIRRecordLayout]:
    typ = hand_record(name, f"stored.{name}", **fields)
    return typ, MIRRecordLayout(typ, tuple(MIRField(MIRFieldId(typ, n), t) for n, t in fields.items()), True, True)


def test_copy_may_raise_through_a_wrapper_field(program: _Program) -> None:
    _, plain = _owner("Ints", t=TupleType((INT32, INT32)))
    _, text = _owner("Texts", t=TupleType((STR, INT32)))
    _, owned = _owner("Owned", t=TupleType((OwnType(STR), INT32)))
    assert not layout_copy_may_raise(plain, lambda _: None)
    # An owned leaf inside a wrapper copies its buffer; an `Own` member is no class the walk enters.
    assert layout_copy_may_raise(text, lambda _: None)
    assert not layout_copy_may_raise(owned, lambda _: None)


def test_aggregate_facts_read_records_stored_through_a_wrapper(program: _Program, monkeypatch) -> None:
    a = program.types["A"]
    held = _list(TupleType((a, INT32)))
    # The container shape a wrapper element would have once admitted: its record inline.
    monkeypatch.setattr(definitions_module, "storage_shape",
                        lambda typ: owned_shape(held, members=(tuple_shape(typ.type_args[0], (
                            owned_shape(a), leaf_shape(INT32))),)))
    layout = MIRRecordLayout(a, (), False, True)
    assert aggregate_facts(held, lambda t: layout if t == a else None) == MIRAggregateFacts(False, True, True)
    assert aggregate_facts(held, lambda _: None) is None


def test_member_records_of_a_container_through_wrappers(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    row = TupleType((a, OptionalType(b)))
    # The shape a wrapper element would have once admitted: its records stored inline.
    elements = (tuple_shape(row, (owned_shape(a), optional_shape(OptionalType(b), owned_shape(b)))),)
    assert _member_records(borrowed_shape(_list(row), members=elements)) == (a, b)
    scalars = TupleType((INT32, STR))
    assert _member_records(borrowed_shape(_list(scalars), members=(storage_shape(scalars),))) == ()


class _Closing:
    """The part of a lowering `close_layouts` reads: the body's layouts and
    the definitions they come from."""
    close_layouts = _Lowering.close_layouts
    close_member = _Lowering.close_member
    leaf_layout = _Lowering.leaf_layout

    def __init__(self, records: dict[TpyType, object]) -> None:
        self.node = None
        self.records = records
        self.definitions = self

    def get(self, _node: object, typ: TpyType) -> object:
        return _Definition(MIRRecordLayout(typ, (), True, True, opaque=typ in (STR, STRVIEW)))


@dataclass(frozen=True)
class _Definition:
    layout: MIRRecordLayout


def test_layouts_close_over_wrapper_fields(program: _Program) -> None:
    inner, _ = _owner("Inner", x=INT32)
    outer, layout = _owner("Outer", t=TupleType((inner, ReadonlyType(STR))), o=OptionalType(inner))
    closing = _Closing({outer: _Definition(layout)})
    closing.close_layouts()
    # The tuple field's record and owned leaf -- seen through its `readonly` -- come along.
    assert set(closing.records) == {outer, inner, STR}


def test_layouts_close_over_owned_wrapper_members(program: _Program) -> None:
    inner, _ = _owner("OwnedInner", x=INT32)
    outer, layout = _owner("OwnedOuter", t=TupleType((OwnType(inner), OwnType(STR))))
    closing = _Closing({outer: _Definition(layout)})
    closing.close_layouts()
    # An `Own[...]` member is read as declared: nothing behind it comes along.
    assert set(closing.records) == {outer}


@pytest.mark.parametrize("kind", ["tree", "leaf", "tuple", "list"])
def test_a_layout_of_no_record_is_refused(program: _Program, kind: str) -> None:
    a = program.types["A"]
    typ = {"tree": program.tree, "leaf": INT32, "tuple": TupleType((a, INT32)), "list": _list(STRVIEW)}[kind]
    # Its fields would be read as no record's: a layout is a record's, or an owned leaf's opaque buffer.
    layout = MIRRecordLayout(typ, (MIRField(MIRFieldId(a, "x"), INT32),), True, True)
    closing = _Closing({typ: _Definition(layout)})
    with pytest.raises(MIRValidationError, match="layout of no record"):
        closing.close_layouts()
    slot = MIRSlotId(MIRBodyId("stored", "l"), 0)
    with pytest.raises(MIRValidationError, match="layout of no record"):
        _member_keys(MIRPlace(slot), layout, records_of(layout))


def test_an_owned_leaf_layout_holds_no_members(program: _Program) -> None:
    text = MIRRecordLayout(STR, (), True, True, opaque=True)
    closing = _Closing({STR: _Definition(text)})
    closing.close_layouts()
    assert set(closing.records) == {STR}
    assert _member_keys(MIRPlace(MIRSlotId(MIRBodyId("stored", "o"), 0)), text, records_of(text)) == ()


# --- stored-loan keys, may-hold, referents -----------------------------------------------

def test_a_loan_holding_record_under_a_tuple_field_keys_its_loans(program: _Program) -> None:
    viewing, view_layout = _owner("Viewing", s=STRVIEW)
    pair_t = TupleType((viewing, INT32))
    holder, holder_layout = _owner("Holding", pair=pair_t)
    layouts = records_of(view_layout, holder_layout)
    slot = MIRSlotId(MIRBodyId("stored", "k"), 0)
    pair, s = holder_layout.fields[0], view_layout.fields[0]
    assert _member_keys(MIRPlace(slot), holder_layout, layouts) == (
        MIRPlace(slot, (pair, MIRTupleIndex(0), s)),)
    # An `Own[...]` member is read as declared: no key lies under it.
    _, owned_layout = _owner("OwnedHolding", pair=TupleType((OwnType(viewing), INT32)))
    assert _member_keys(MIRPlace(slot), owned_layout, records_of(view_layout)) == ()


def test_may_hold_looks_through_wrapper_fields(program: _Program) -> None:
    p, p_layout = _owner("Held", x=INT32)
    target = p_layout.fields[0].id
    wrappers = {"tuple": TupleType((p, INT32)), "union": make_union(p, INT32), "optional": OptionalType(p),
                "pinned": OptionalType(p, force_pointer_repr=True)}
    for name, field_type in wrappers.items():
        holder, holder_layout = _owner(f"Via{name}", w=field_type)
        fields = MappingProxyType({holder: frozenset(holder_layout.fields), p: frozenset(p_layout.fields)})
        assert _may_hold(holder, target, fields), name
    scalars, scalars_layout = _owner("Scalars", w=TupleType((INT32, INT32)))
    assert not _may_hold(scalars, target, MappingProxyType({scalars: frozenset(scalars_layout.fields)}))
    owned, owned_layout = _owner("ViaOwned", w=TupleType((OwnType(p), INT32)))
    fields = MappingProxyType({owned: frozenset(owned_layout.fields), p: frozenset(p_layout.fields)})
    assert _may_hold(owned, target, fields)


def test_container_holds_looks_through_a_wrapper_member(program: _Program) -> None:
    inner = _list(INT32)
    assert _container_holds(_list(inner), inner, no_layouts())
    assert _container_holds(_list(TupleType((inner, INT32))), inner, no_layouts())
    assert _container_holds(_list(OptionalType(inner)), inner, no_layouts())
    assert not _container_holds(_list(TupleType((INT32, INT32))), inner, no_layouts())


def _field_world(name: str, field_type: TpyType) -> tuple[MIRField, MIRRecords, MIRSlotId, MIRSlotId,
                                                            dict[MIRSlotId, MIRSlot]]:
    p = hand_record("P", "stored.P", x=INT32)
    h = hand_record(name, f"stored.{name}", pair=field_type)
    x = MIRField(MIRFieldId(p, "x"), INT32)
    pair = MIRField(MIRFieldId(h, "pair"), field_type)
    records = records_of(MIRRecordLayout(p, (x,), True, True), MIRRecordLayout(h, (pair,), True, True))
    body = MIRBodyId("stored", "f")
    first, second = MIRSlotId(body, 0), MIRSlotId(body, 1)
    slots = {first: MIRSlot(first, borrowed_shape(h), MIRSlotKind.PARAMETER),
             second: MIRSlot(second, borrowed_shape(h), MIRSlotKind.PARAMETER)}
    return pair, records, first, second, slots


@pytest.mark.parametrize("nested", [False, True])
def test_a_referent_under_a_wrapper_field_keeps_its_path(program: _Program, monkeypatch, nested: bool) -> None:
    p = hand_record("P", "stored.P", x=INT32)
    # `tuple[P, int32]`, or `tuple[P | None, int32]`, whose payload lies in the Optional inside the tuple.
    field_type = TupleType((OptionalType(p) if nested else p, INT32))
    pair, records, first, second, slots = _field_world("NestedH" if nested else "H", field_type)
    real = shapes.member_shape

    # The storage form a wrapper field would have once admitted (`storage_shape`).
    def member_shape(typ: TpyType, holder_readonly: bool, layouts: MIRRecords) -> object:
        return storage_shape(typ, holder_readonly) if typ == field_type else real(typ, holder_readonly, layouts)

    monkeypatch.setattr(shapes, "member_shape", member_shape)
    state = {MIRPlace(first): frozenset({MIRReferent(MIRPlace(first), external=True)}),
             MIRPlace(second): frozenset({MIRReferent(MIRPlace(second), external=True)})}
    path = (pair, MIRTupleIndex(0), *((MIRUnionPayload(1),) if nested else ()))
    got = resolve_referents(MIRPlace(first, (MIRDeref(), *path)), state, slots, records)
    assert got == frozenset({MIRReferent(MIRPlace(first, path), external=True)})
    # So a replacement of the other holder's field (it may be the same object) reaches it.
    written = MIRReferent(MIRPlace(second, (pair,)), external=True)
    assert any(reaches(written, r, storage_exclusions(slots, records)) for r in got)


def test_a_scalar_member_of_an_owned_tuple_resolves_to_nothing(program: _Program) -> None:
    p = hand_record("Q", "stored.Q", x=INT32)
    records = records_of(MIRRecordLayout(p, (MIRField(MIRFieldId(p, "x"), INT32),), True, True))
    pair_t = TupleType((p, INT32))
    slot = MIRSlotId(MIRBodyId("stored", "g"), 0)
    slots = {slot: MIRSlot(slot, tuple_shape(pair_t, (owned_shape(p), leaf_shape(INT32))), MIRSlotKind.LOCAL)}
    assert resolve_referents(MIRPlace(slot, (MIRTupleIndex(1),)), {}, slots, records) == frozenset()
    assert resolve_referents(MIRPlace(slot, (MIRTupleIndex(0),)), {}, slots, records) == frozenset(
        {MIRReferent(MIRPlace(slot, (MIRTupleIndex(0),)), False)})


# --- a record reaching itself through an Optional field ----------------------------------
# No admitted program has one (`element_problem` / `modeled_field` refuse it first),
# so each walk is driven with forged inputs: it must end, answering as for the
# record's other members.

def _node(name: str, **extra: TpyType) -> tuple[NominalType, MIRRecordLayout]:
    self_type = NominalType(name, _module_qname=f"stored.{name}")
    return _owner(name, v=INT32, nxt=OptionalType(self_type), **extra)


def test_walks_end_on_a_record_reaching_itself(program: _Program) -> None:
    node, layout = _node("Node")
    texts, texts_layout = _node("TextNode", s=STR)
    layout_of = {node: layout, texts: texts_layout}.get
    assert set(nested_members([node], lambda r: layout.fields if r == node else ())) == {
        node, OptionalType(node), INT32}
    assert not layout_copy_may_raise(layout, layout_of)
    assert layout_copy_may_raise(texts_layout, layout_of)
    assert aggregate_facts(node, layout_of) == MIRAggregateFacts(True, True, False)
    with pytest.raises(MIRUnsupported, match="member definition: cyclic record definition"):
        _member_definitions(None, layout, {node: "cyclic record definition"})
    closing = _Closing({node: _Definition(layout)})
    closing.close_layouts()
    assert set(closing.records) == {node}


def test_may_hold_ends_on_a_record_reaching_itself(program: _Program) -> None:
    node, layout = _node("HoldNode")
    other = hand_record("Other", "stored.Other", x=INT32)
    fields = MappingProxyType({node: frozenset(layout.fields)})
    assert not _may_hold(node, MIRFieldId(other, "x"), fields)
    assert _may_hold(node, MIRFieldId(node, "v"), fields)
    assert not _container_holds(_list(node), _list(INT32), records_of(layout))


def test_stored_loan_keys_refuse_a_record_holding_itself(program: _Program) -> None:
    node, layout = _node("KeyNode")
    slot = MIRSlotId(MIRBodyId("stored", "n"), 0)
    with pytest.raises(MIRValidationError, match="record storage holds itself"):
        _member_keys(MIRPlace(slot), layout, records_of(layout))


def test_layouts_check_ends_on_a_record_reaching_itself(program: _Program) -> None:
    # A record with no TypeDef reads as a plain element, so only the supplied layout closes the cycle.
    loose = NominalType("Loose", _module_qname="stored.loose.Loose")
    layout = MIRRecordLayout(loose, (MIRField(MIRFieldId(loose, "xs"), _list(loose)),), True, True)
    assert _layouts_problem(storage_shape(_list(loose)).members, {loose: layout}) is None


# --- the validator ----------------------------------------------------------------------

def test_a_field_needs_every_layout_it_stores_inline(program: _Program) -> None:
    a = program.types["A"]
    a_layout = MIRRecordLayout(a, (MIRField(MIRFieldId(a, "x"), INT32),), True, True)
    text = MIRRecordLayout(STR, (), True, True, opaque=True)
    full = {a: a_layout, STR: text}
    wrong = ({STR: text}, {a: a_layout}, {a: replace(a_layout, opaque=True), STR: text},
             {a: a_layout, STR: replace(text, opaque=False)})
    # One walk from the field's own type, named by the field's class: a record, an owned leaf...
    assert field_layout_problem(a, full) is None
    assert field_layout_problem(a, {}) == "record field needs its layout"
    assert field_layout_problem(a, {a: replace(a_layout, opaque=True)}) == "record field needs its layout"
    assert field_layout_problem(STR, full) is None
    assert field_layout_problem(STR, {STR: replace(text, opaque=False)}) == "owned-leaf field needs its opaque layout"
    assert field_layout_problem(INT32, {}) is None
    assert field_layout_problem(_list(a), {}) is None
    # ...and through the wrapper a modeled field may be, an Optional record field: its payload's layout.
    assert field_layout_problem(OptionalType(a), full) is None
    for missing in ({}, {STR: text}, {a: replace(a_layout, opaque=True), STR: text}):
        assert field_layout_problem(OptionalType(a), missing) == "optional record field needs its payload layout"


def test_the_validator_checks_a_wrapper_field_s_layouts(program: _Program) -> None:
    a = program.types["A"]
    definitions = MIRDefinitions(tuple(program.constructors.values()))
    fn = lower_constructor(program.constructors["Maybe"], MIRBodyId("stored", "Maybe.__init__"),
                           definitions=definitions)
    assert isinstance(fn, MIRFunction)
    validate_function(fn)
    without_a = tuple(r for r in fn.records if r.type != a)
    with pytest.raises(MIRValidationError, match="optional record field needs its payload layout"):
        validate_function(replace(fn, records=without_a))

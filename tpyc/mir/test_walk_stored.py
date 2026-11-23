"""The one walk over stored members (`walk_stored`) and its readers: each
reader's answers, order, refusal and laziness pinned over hand-built types
-- repeated types, both member orders, unknown roots, `Own[...]` and
readonly members, records reaching themselves, a recursive alias union."""

from collections.abc import Callable
from types import SimpleNamespace


from ..thir.scalar_leaves import UNEXPANDED, MemberKind, MemberSelector, MIRClass, StoredStep, stored_members, walk_stored
from ..typesys import (
    INT32, STR, NominalType, OptionalType, OwnType, ReadonlyType, TpyType, TupleType, make_union,
)
from .coverage import MIRUnsupported
from .definitions import _member_definitions, _read_records, layout_copy_may_raise
from .reach import _container_holds, _may_hold
from . import test_stored_members as stored
from .test_stored_members import _list, _node, _owner, _Program
from .testutil import records_of
from .validate import field_layout_problem
from .value import MIRField, MIRFieldId, MIRRecordLayout, nested_members, no_fields


program = stored.program


# --- the hand-built world ----------------------------------------------------------------

class _World:
    """Records and member types the readers walk: `WLeafy` (an int32 and a
    str), `WInner` (an `A` and a `B | None`), `WNode` (reaching itself
    through `nxt`), a record no field source knows and the recursive alias
    union `Tree`."""

    def __init__(self, program: _Program) -> None:
        a, b = program.types["A"], program.types["B"]
        self.leafy, leafy = _owner("WLeafy", x=INT32, s=STR)
        self.inner, inner = _owner("WInner", p=a, o=OptionalType(b))
        self.node, node = _node("WNode", s=STR)
        self.unknown = NominalType("WUnknown", _module_qname="walk.WUnknown")
        self.a, self.b, self.tree = a, b, program.tree
        a_layout = MIRRecordLayout(a, (MIRField(MIRFieldId(a, "x"), INT32),), True, True)
        b_layout = MIRRecordLayout(b, (MIRField(MIRFieldId(b, "y"), INT32),), True, True)
        self.layouts = {layout.type: layout for layout in (leafy, inner, node, a_layout, b_layout)}
        self.fields = {layout.type: frozenset(layout.fields) for layout in self.layouts.values()}

    def record_fields(self, record: NominalType) -> tuple[MIRField, ...]:
        layout = self.layouts.get(record)
        return () if layout is None else layout.fields

    def field(self, record: NominalType, name: str) -> MIRFieldId:
        return MIRFieldId(record, name)


def _outcome(call: Callable[[], object]) -> object:
    try:
        return call()
    except MIRUnsupported as e:
        return ("refused", e.reason)


def _layout(*types: TpyType) -> MIRRecordLayout:
    holder = NominalType("WHolder", _module_qname="walk.WHolder")
    return MIRRecordLayout(holder, tuple(MIRField(MIRFieldId(holder, f"f{i}"), t) for i, t in enumerate(types)),
                           True, True)


def _names(types: object) -> list[str]:
    return [str(t) for t in types]


# --- nested_members --------------------------------------------------------------------

def test_nested_members_yields_each_type_once_last_member_first(program: _Program) -> None:
    w = _World(program)
    a, b = w.a, w.b

    def walk(*roots: TpyType) -> list[str]:
        return _names(nested_members(roots, w.record_fields))
    # A record before its fields, its last field first; `int32` once although both `A` and `B` store one.
    assert walk(w.inner) == ["WInner", "B | None", "B", "int32", "A"]
    assert walk(TupleType((a, TupleType((b, a))))) == ["tuple[A, tuple[B, A]]", "tuple[B, A]", "A", "int32", "B"]
    # A readonly root is seen through.
    assert walk(ReadonlyType(TupleType((w.inner, STR)))) == [
        "tuple[WInner, str]", "str", "WInner", "B | None", "B", "int32", "A"]
    # A record reaching itself is entered once.
    assert walk(w.node) == ["WNode", "str", "WNode | None", "int32"]
    assert walk(_list(TupleType((a, b)))) == ["list[tuple[A, B]]", "tuple[A, B]", "B", "int32", "A"]
    # A recursive alias union is yielded and not expanded; a repeated root is yielded once; an unknown record has
    # no fields.
    assert walk(w.tree) == ["Tree"]
    assert walk(w.unknown, a, a) == ["A", "int32", "WUnknown"]


def test_nested_members_asks_a_records_fields_once_right_after_yielding_it(program: _Program) -> None:
    w = _World(program)
    log: list[tuple[str, str]] = []

    def fields_of(record: NominalType) -> tuple[MIRField, ...]:
        log.append(("fields", str(record)))
        return w.record_fields(record)
    for typ in nested_members((w.inner, w.node), fields_of):
        log.append(("yield", str(typ)))
    # A reader registering a record on its yield is in time for its fields.
    assert log == [("yield", "WNode"), ("fields", "WNode"), ("yield", "str"), ("yield", "WNode | None"),
                   ("yield", "int32"), ("yield", "WInner"), ("fields", "WInner"), ("yield", "B | None"),
                   ("yield", "B"), ("fields", "B"), ("yield", "A"), ("fields", "A")]


# --- _read_records ---------------------------------------------------------------------

def test_read_records_lists_bases_then_each_field_record_once(program: _Program) -> None:
    w = _World(program)
    a, b = w.a, w.b

    def reads(*types: TpyType, bases: tuple[TpyType, ...] = ()) -> list[str]:
        ctor = SimpleNamespace(base_inits=tuple(SimpleNamespace(base=t) for t in bases),
                               record_layout=_layout(*types))
        return _names(_read_records(ctor))
    # A field's record, an Optional field's payload record and a container's element record, each once.
    assert reads(w.inner, _list(a), OptionalType(b), a) == ["A", "B", "WInner"]
    # Only modeled fields are read: a tuple field and an `Own[...]` field are not.
    assert reads(TupleType((_list(w.inner), b))) == []
    assert reads(OwnType(a), w.node) == ["WNode"]
    # Nominal bases come first, as written, and are not deduplicated against the fields.
    assert reads(w.inner, b, bases=(b, INT32)) == ["B", "int32", "B", "WInner"]


# --- _container_holds ------------------------------------------------------------------

def test_container_holds_finds_the_target_among_members_at_any_depth(program: _Program) -> None:
    w = _World(program)
    a, b = w.a, w.b
    records = records_of(*w.layouts.values())
    targets = (a, b, _list(b), _list(a), OwnType(a))

    def holds(container: TpyType) -> list[bool]:
        return [_container_holds(container, t, records) for t in targets]
    assert holds(_list(a)) == [True, False, False, False, False]
    # Through a container element, a tuple element and a record's fields (an Optional field's payload too).
    assert holds(_list(_list(b))) == [False, True, True, False, False]
    assert holds(_list(TupleType((a, b)))) == [True, True, False, False, False]
    assert holds(_list(w.inner)) == [True, True, False, False, False]
    assert holds(TupleType((_list(a), _list(b)))) == [True, True, True, True, False]
    # An `Own[...]` member is only itself, never entered.
    assert holds(_list(OwnType(a))) == [False, False, False, False, True]
    # A container never holds itself; a non-container root, a recursive alias union and an unknown record hold
    # nothing.
    for root in (w.tree, a, INT32, w.unknown):
        assert holds(root) == [False] * len(targets), root


# --- _may_hold -------------------------------------------------------------------------

def test_may_hold_answers_no_only_where_the_member_is_proven_absent(program: _Program) -> None:
    w = _World(program)
    a, b = w.a, w.b
    ax, by = w.field(a, "x"), w.field(b, "y")
    inner_p, inner_o = w.field(w.inner, "p"), w.field(w.inner, "o")
    node_nxt, leafy_s = w.field(w.node, "nxt"), w.field(w.leafy, "s")
    stranger = MIRFieldId(NominalType("WStranger", _module_qname="walk.WStranger"), "x")
    members = (ax, by, inner_p, inner_o, node_nxt, leafy_s, stranger)

    def may(typ: TpyType | None) -> list[bool]:
        return [_may_hold(typ, m, w.fields) for m in members]
    # A leaf and an owned leaf hold no record member.
    assert may(INT32) == may(STR) == [False] * len(members)
    # A record holds its own fields and what they hold, an Optional field's payload included.
    assert may(a) == [True, False, False, False, False, False, False]
    assert may(w.inner) == [True, True, True, True, False, False, False]
    assert may(w.node) == [False, False, False, False, True, False, False]
    assert may(w.leafy) == [False, False, False, False, False, True, False]
    # Wrappers hold what their members hold, a readonly root seen through.
    assert may(OptionalType(a)) == [True, False, False, False, False, False, False]
    assert may(make_union(a, b)) == may(TupleType((a, b))) == [True, True, False, False, False, False, False]
    assert may(ReadonlyType(TupleType((w.inner, STR)))) == may(w.inner)
    # Unknown storage may hold anything: no type, an unknown record, a container, an `Own[...]`, a recursive
    # alias union.
    for typ in (None, w.unknown, _list(a), OwnType(a), w.tree):
        assert may(typ) == [True] * len(members), typ


# --- layout_copy_may_raise -------------------------------------------------------------

def test_copy_may_raise_where_a_member_copy_may(program: _Program) -> None:
    w = _World(program)
    a = w.a
    cases = ((INT32,), (STR,), (a,), (OptionalType(a),), (TupleType((INT32, STR)),), (w.leafy,), (w.inner,),
             (_list(INT32),), (OwnType(a),), (w.tree,), (make_union(a, INT32),), (w.node,))

    def raises(layout_of: Callable[[NominalType], MIRRecordLayout | None]) -> list[bool]:
        return [layout_copy_may_raise(_layout(*types), layout_of) for types in cases]
    # An owned leaf's copy allocates, through any wrapper or record; a record of scalar fields does not.
    assert raises(w.layouts.get) == [False, True, False, False, True, True, False, True, False, True, False, True]
    # A record member whose layout is missing may raise; an `Own[...]` member is not copied.
    assert raises(lambda _: None) == [False, True, True, True, True, True, True, True, False, True, True, True]


# --- _member_definitions ---------------------------------------------------------------

def test_member_definitions_refuse_at_the_first_member_in_field_order(program: _Program) -> None:
    w = _World(program)
    a, b = w.a, w.b
    definitions = {a: "refused A", b: "refused B", w.inner: SimpleNamespace(layout=w.layouts[w.inner])}

    def refusal(*types: TpyType) -> object:
        return _outcome(lambda: _member_definitions(None, _layout(*types), definitions))
    assert refusal(a, b) == ("refused", "member definition: refused A")
    assert refusal(b, a) == ("refused", "member definition: refused B")
    # Into a tuple's and an Optional's members; a member record's own fields are its definition's.
    assert refusal(TupleType((b, a))) == ("refused", "member definition: refused B")
    assert refusal(OptionalType(b), a) == ("refused", "member definition: refused B")
    assert refusal(w.inner, b) == ("refused", "member definition: refused B")
    # A container's element refusal is the container definition's own text.
    assert refusal(_list(b), a) == ("refused", "refused B")
    assert refusal(w.unknown) == ("refused", "member definition: missing constructor definition")
    assert refusal(w.tree) == ("refused", "unsupported record fields")
    assert refusal(INT32, w.inner) is None


# --- field_layout_problem --------------------------------------------------------------

def test_field_layout_problem_names_the_first_missing_layout(program: _Program) -> None:
    w = _World(program)
    a = w.a
    text = MIRRecordLayout(STR, (), True, True, opaque=True)
    roots = (INT32, STR, a, OptionalType(a), _list(a), OwnType(a), w.unknown)

    def problems(records: dict[TpyType, MIRRecordLayout]) -> list[str | None]:
        return [field_layout_problem(typ, records) for typ in roots]
    record, leaf = "record field needs its layout", "owned-leaf field needs its opaque layout"
    optional = "optional record field needs its payload layout"
    assert problems({}) == [None, leaf, record, optional, None, None, record]
    assert problems({STR: text}) == [None, None, record, optional, None, None, record]
    # A record laid out as a leaf, or a leaf laid out as a record, is still missing its layout.
    assert problems({**w.layouts, STR: text}) == [None, None, None, None, None, None, record]
    assert field_layout_problem(STR, {STR: w.layouts[a]}) == leaf
    assert field_layout_problem(a, {a: text}) == record
    # A tuple or union field whose members are all laid out has no problem.
    assert field_layout_problem(TupleType((a, STR)), {**w.layouts, STR: text}) is None
    assert field_layout_problem(make_union(a, INT32), w.layouts) is None


# --- the walk itself --------------------------------------------------------------------------

def test_the_walk_yields_repeats_and_enters_once(program: _Program) -> None:
    a, b = program.types["A"], program.types["B"]
    entered: list[TpyType] = []

    def enter(typ: TpyType, cls: MIRClass | None) -> bool:
        entered.append(typ)
        return cls is MIRClass.TUPLE
    pair = TupleType((a, b))
    steps = list(walk_stored([TupleType((pair, pair))], no_fields, enter))
    element = MemberKind.ELEMENT
    assert steps == [
        StoredStep(TupleType((pair, pair)), MIRClass.TUPLE, None, True),
        StoredStep(pair, MIRClass.TUPLE, MemberSelector(element, 0), True),
        StoredStep(a, MIRClass.RECORD, MemberSelector(element, 0), True),
        StoredStep(b, MIRClass.RECORD, MemberSelector(element, 1), True),
        # The second member is the same type: yielded for its selector, never entered again.
        StoredStep(pair, MIRClass.TUPLE, MemberSelector(element, 1), False),
    ]
    assert entered == [TupleType((pair, pair)), pair, a, b]
    # The stack's own order: the last member first.
    assert [s.type for s in walk_stored([pair], no_fields, enter, last_first=True)] == [pair, b, a]


def test_the_walk_marks_an_entered_type_it_cannot_expand(program: _Program) -> None:
    a = program.types["A"]
    # A record no field source knows, a readonly root seen through, an `Own` entered by its reader's choice.
    steps = list(walk_stored([ReadonlyType(a), OwnType(a)], no_fields, lambda _t, _c: True))
    assert steps == [StoredStep(a, MIRClass.RECORD, None, True), StoredStep(a, MIRClass.RECORD, None, False, True),
                     StoredStep(OwnType(a), None, None, True), StoredStep(OwnType(a), None, None, False, True)]
    assert stored_members(OwnType(a), no_fields) is UNEXPANDED

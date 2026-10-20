"""MIR over Optional record fields (`p: P | None`, a plain loan-free payload
stored inline): the field predicate, the layout of a field place, THIR's
identity rule for the field's accesses, the lowered reads, and the presence
kill rule at writes and calls that may reach the field."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, modeled_field, optional_record_field
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import INT32, NominalType, OptionalType, ReadonlyType, TupleType
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .dump import dump_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRConstant, MIRFunction, MIRIsPresent, MIROptionalConstruct, MIRPlace, MIRRead, MIRSlot,
    MIRSlotId, MIRSlotKind, place_shape,
)
from .value import MIRField, MIRFieldId
from .coverage import MIRUnsupported
from .definitions import constructor_initialization
from .presence import _State, _may_hold, _reaches, _transfer
from .shapes import MIRDeref, MIROptionalPayload
from .testutil import bind_parameters, borrowed_shape, cursor_shape, leaf_shape, owned_shape, reslot, tuple_shape
from .validate import validate_function
from .value import MIRValidationError

SOURCE = """\
from tpy import int32, StrView, copy, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class V:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s


class Deep:
    p: P

    def __init__(self, p: P) -> None:
        self.p = copy(p)


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Shapes:
    k: int32 | None
    v: V | None
    d: Deep | None
    r: readonly[P | None]
    g: Box[int32]

    def __init__(self) -> None:
        self.k = None
        self.v = None
        self.d = None
        self.r = None
        self.g = Box(1)


class H:
    p: P | None
    n: int32

    def __init__(self, p: P | None, n: int32) -> None:
        self.p = p
        self.n = n


class Empty:
    p: P | None

    def __init__(self) -> None:
        self.p = None


class Built:
    p: P | None

    def __init__(self, x: int32) -> None:
        self.p = P(x)


def clear(h: H) -> None:
    h.p = None


def bump_n(h: H) -> None:
    h.n += 1


def read(h: H) -> int32:
    if h.p is not None:
        return h.p.x
    return 0


def kill_alias(a: H, b: H) -> int32:
    if a.p is not None:
        b.p = None
        return a.p.x
    return 0


def keep_scalar(a: H, b: H) -> int32:
    if a.p is not None:
        b.n = 2
        return a.p.x
    return 0


def kill_call(a: H, b: H) -> int32:
    if a.p is not None:
        clear(b)
        return a.p.x
    return 0


def keep_call(a: H, b: H) -> int32:
    if a.p is not None:
        bump_n(b)
        return a.p.x
    return 0
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    types: dict[str, NominalType]
    fields: dict[str, object]
    verdicts: dict[str, MIRBodyVerdict]
    functions: dict[str, th.THIRFunction]
    constructors: dict[str, th.THIRConstructor]
    definitions: object


# Function scope: the per-test reset drops the records' TypeDefs, which the
# field predicate reads.
@pytest.fixture
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        # A generic record's constructor has no one layout.
        layouts = {c.record_name: c.record_layout for c in ctx.thir_constructors.values()
                   if c.record_layout is not None}
        yield _Program(compiler, {name: layout.type for name, layout in layouts.items()},
                       {f"{name}.{f.name}": f.type for name, layout in layouts.items() for f in layout.fields},
                       {v.body.declaration.split("@")[0]: v for v in verdicts},
                       {node.name: fn for node, fn in ctx.thir_functions.items()},
                       {c.record_name: c for c in ctx.thir_constructors.values()}, mir.definitions)


@pytest.fixture(autouse=True)
def active(program):
    with activate_compiler(program.compiler):
        yield


# --- the field predicate ------------------------------------------------------------

def test_an_optional_of_a_plain_loan_free_record_is_a_modeled_field(program: _Program) -> None:
    typ = program.fields["H.p"]
    assert optional_record_field(typ) == program.types["P"]
    assert modeled_field(typ)


@pytest.mark.parametrize("name", [
    # An Optional of a scalar stores no record: a payload place MIR does not model yet.
    "Shapes.k",
    # A payload holding a loan would key a stored loan under the payload.
    "Shapes.v",
    # A payload with a record field is no plain record: its places run deeper than one field.
    "Shapes.d",
])
def test_other_optional_fields_stay_unmodeled(program: _Program, name: str) -> None:
    assert optional_record_field(program.fields[name]) is None
    assert not modeled_field(program.fields[name])


def test_a_readonly_optional_field_keeps_its_payload(program: _Program) -> None:
    typ = program.fields["Shapes.r"]
    assert isinstance(typ, ReadonlyType)
    assert optional_record_field(typ) == program.types["P"]


# --- the layout of a field place ------------------------------------------------------

BODY = MIRBodyId("optional_fields", "example")


def _holder(program: _Program, index: int, kind: MIRSlotKind = MIRSlotKind.PARAMETER,
            owned: bool = False) -> MIRSlot:
    typ = program.types["H"]
    return MIRSlot(MIRSlotId(BODY, index), owned_shape(typ) if owned else borrowed_shape(typ), kind)


def _member(program: _Program, name: str) -> MIRField:
    owner = program.types[name.split(".")[0]]
    return MIRField(MIRFieldId(owner, name.split(".")[1]), program.fields[name])


def test_a_field_place_carries_an_owned_payload_layout(program: _Program) -> None:
    holder = _holder(program, 0)
    shapes = MIRSlot(MIRSlotId(BODY, 1), borrowed_shape(program.types["Shapes"]), MIRSlotKind.PARAMETER)
    slots = {holder.id: holder, shapes.id: shapes}

    def layout(place: MIRPlace) -> object:
        shape = place_shape(place, slots, {})
        return shape.payload if shape.cls is MIRClass.OPTIONAL else None

    field = MIRPlace(holder.id, (MIRDeref(), _member(program, "H.p")))
    assert layout(field) == owned_shape(program.types["P"])
    readonly = MIRPlace(shapes.id, (MIRDeref(), _member(program, "Shapes.r")))
    assert layout(readonly) == owned_shape(program.types["P"], True)
    # A scalar field, the payload itself and the root holder have no wrapper layout.
    assert layout(MIRPlace(holder.id, (MIRDeref(), _member(program, "H.n")))) is None
    assert layout(MIRPlace(field.root, (*field.projections, MIROptionalPayload()))) is None
    assert layout(MIRPlace(holder.id)) is None


@pytest.mark.parametrize("name,init", [
    ("H.__init__", "initialize-receiver %0 (copy %1, %2)"),
    ("Empty.__init__", "initialize-receiver %0 (absent)"),
    ("Built.__init__", "initialize-receiver %0 (move {%1})"),
])
def test_the_dump_spells_each_optional_member_initializer(program: _Program, name: str, init: str) -> None:
    verdict = program.verdicts[name]
    assert verdict.status is MIRVerdictStatus.COVERED, verdict.describe()
    assert init in dump_function(verdict.function).splitlines()


# --- THIR's identity rule for the field's accesses --------------------------------------

def _accesses(fn: th.THIRFunction) -> tuple[th.THIRIf, th.THIRFieldAccess, th.THIRFieldAccess]:
    branch = fn.body[0]
    assert isinstance(branch, th.THIRIf)
    whole = branch.condition.operand
    narrowed = branch.then_body[0].value.receiver
    assert isinstance(whole, th.THIRFieldAccess) and isinstance(narrowed, th.THIRFieldAccess)
    return branch, whole, narrowed


def test_thir_publishes_the_identity_of_both_accesses(program: _Program) -> None:
    _, whole, narrowed = _accesses(program.functions["read"])
    p = program.types["P"]
    assert whole.field_identity == narrowed.field_identity == th.THIRFieldIdentity(
        program.types["H"], "p", OptionalType(p))
    assert whole.form is narrowed.form is th.Form.STORAGE
    assert not whole.narrowed_deref and narrowed.narrowed_deref
    assert whole.result_type == OptionalType(p) and narrowed.result_type == p


def _with_access(fn: th.THIRFunction, *, whole: th.THIRFieldAccess | None = None,
                 narrowed: th.THIRFieldAccess | None = None) -> th.THIRFunction:
    branch, old_whole, old_narrowed = _accesses(fn)
    ret = branch.then_body[0]
    condition = replace(branch.condition, operand=whole or old_whole)
    value = replace(ret.value, receiver=narrowed or old_narrowed)
    return replace(fn, body=(replace(branch, condition=condition,
                                     then_body=(replace(ret, value=value), *branch.then_body[1:])),
                             *fn.body[1:]))


def test_the_identity_rule_admits_the_assign_target_spelling(program: _Program) -> None:
    fn = program.functions["read"]
    _, whole, _ = _accesses(fn)
    # An assign target inside a narrowed region keeps the payload's type, unflagged.
    validate_thir(_with_access(fn, whole=replace(whole, result_type=program.types["P"])))


@pytest.mark.parametrize("change", ["narrowed_whole_type", "whole_value_form", "whole_wrong_type"])
def test_the_identity_rule_refuses_a_mismatched_access(program: _Program, change: str) -> None:
    fn = program.functions["read"]
    _, whole, narrowed = _accesses(fn)
    match change:
        case "narrowed_whole_type":
            changed = _with_access(fn, narrowed=replace(narrowed, result_type=whole.result_type))
        case "whole_value_form":
            changed = _with_access(fn, whole=replace(whole, form=th.Form.VALUE))
        case _:
            changed = _with_access(fn, whole=replace(whole, result_type=INT32))
    with pytest.raises(THIRValidationError, match="field identity disagrees with its access"):
        validate_thir(changed)


# --- reads --------------------------------------------------------------------------------

def test_a_narrowed_read_projects_into_the_payload(program: _Program) -> None:
    fn = program.verdicts["read"].function
    assert isinstance(fn, MIRFunction)
    holder = next(s.id for s in fn.slots if s.name == "h")
    field = MIRPlace(holder, (MIRDeref(), _member(program, "H.p")))
    values = [stmt.value for block in fn.blocks for stmt in block.statements if isinstance(stmt, MIRAssign)]
    assert MIRIsPresent(field) in values
    payload_x = MIRPlace(holder, (*field.projections, MIROptionalPayload(), _member(program, "P.x")))
    assert MIRRead(payload_x) in values


# --- the presence kill rule ---------------------------------------------------------------

@pytest.mark.parametrize("name,covered", [
    # `b` may be `a`: emptying b.p ends the proof that a.p is engaged.
    ("kill_alias", False),
    # A callee that may empty b.p, through its published write of param0.p.
    ("kill_call", False),
    # Writing another field, directly or through a callee, keeps the proof.
    ("keep_scalar", True),
    ("keep_call", True),
])
def test_a_field_selection_dies_at_a_write_that_may_reach_it(program: _Program, name: str, covered: bool) -> None:
    verdict = program.verdicts[name]
    if covered:
        assert verdict.status is MIRVerdictStatus.COVERED and not verdict.conflicts, verdict.describe()
    else:
        assert verdict.status is MIRVerdictStatus.UNCOVERED
        assert verdict.reason == "optional payload access without current presence proof"


def test_reaches_decides_overlap_on_places(program: _Program) -> None:
    a, b = _holder(program, 0), _holder(program, 1)
    own_a = _holder(program, 2, MIRSlotKind.LOCAL, owned=True)
    own_b = _holder(program, 3, MIRSlotKind.LOCAL, owned=True)
    slots = {s.id: s for s in (a, b, own_a, own_b)}
    layouts: dict = {}
    p, n = _member(program, "H.p"), _member(program, "H.n")
    fact = MIRPlace(a.id, (MIRDeref(), p))
    # Under one root: a prefix either way.
    assert _reaches(MIRPlace(a.id, (MIRDeref(),)), fact, slots, layouts)
    assert _reaches(MIRPlace(a.id, (MIRDeref(), p, MIROptionalPayload())), fact, slots, layouts)
    assert not _reaches(MIRPlace(a.id, (MIRDeref(), n)), fact, slots, layouts)
    # Rebinding the holder retargets the fact's place.
    assert _reaches(MIRPlace(a.id), fact, slots, layouts)
    # Under another holder: the same member, or storage that may hold it.
    assert _reaches(MIRPlace(b.id, (MIRDeref(), p)), fact, slots, layouts)
    assert not _reaches(MIRPlace(b.id, (MIRDeref(), n)), fact, slots, layouts)
    assert _reaches(MIRPlace(own_a.id), fact, slots, layouts)
    # Rebinding another holder writes no storage.
    assert not _reaches(MIRPlace(b.id), fact, slots, layouts)
    # A root write of anything but a borrowed holder may write storage: an
    # owned tuple or a generic record may hold the member, a scalar may not.
    tup = MIRSlot(MIRSlotId(BODY, 4), tuple_shape(TupleType((INT32, program.types["P"])),
                                                  (leaf_shape(INT32), owned_shape(program.types["P"]))),
                  MIRSlotKind.LOCAL)
    gen = MIRSlot(MIRSlotId(BODY, 5), owned_shape(program.fields["Shapes.g"]), MIRSlotKind.LOCAL)
    num = MIRSlot(MIRSlotId(BODY, 6), leaf_shape(INT32), MIRSlotKind.LOCAL)
    slots.update({s.id: s for s in (tup, gen, num)})
    assert _reaches(MIRPlace(tup.id), fact, slots, layouts)
    assert _reaches(MIRPlace(gen.id), fact, slots, layouts)
    assert not _reaches(MIRPlace(num.id), fact, slots, layouts)
    # A generic record member may hold it too (a place MIR never builds:
    # the field is unmodeled, so the question is the type's alone).
    assert _may_hold(program.fields["Shapes.g"], p.id, layouts)
    # Rebinding an iterator's cursor writes no storage.
    cursor = MIRSlot(MIRSlotId(BODY, 7), cursor_shape(program.fields["Shapes.g"]), MIRSlotKind.LOCAL)
    slots[cursor.id] = cursor
    assert not _reaches(MIRPlace(cursor.id), fact, slots, layouts)
    # Two of the body's own objects are distinct storage.
    assert not _reaches(MIRPlace(own_b.id, (p,)), MIRPlace(own_a.id, (p,)), slots, layouts)
    assert _reaches(MIRPlace(b.id, (MIRDeref(), p)), MIRPlace(own_a.id, (p,)), slots, layouts)


def test_a_whole_field_write_selects_what_it_writes(program: _Program) -> None:
    holder = _holder(program, 0)
    slots = {holder.id: holder}
    field = MIRPlace(holder.id, (MIRDeref(), _member(program, "H.p")))
    state = _State(frozenset({(field, frozenset({1}))}))
    emptied = _transfer(state, MIRAssign(field, MIROptionalConstruct()), set(), slots=slots, layouts={})
    assert emptied.present == frozenset({(field, frozenset({0}))})
    scalar = MIRPlace(holder.id, (MIRDeref(), _member(program, "H.n")))
    kept = _transfer(state, MIRAssign(scalar, MIRConstant(1)), set(), slots=slots, layouts={})
    assert kept.present == state.present



def test_may_hold_looks_through_aggregates(program: _Program) -> None:
    layouts = program.definitions.layouts
    member = _member(program, "H.p").id
    p, h = program.types["P"], program.types["H"]
    # Leaves and records that do not declare the member cannot hold it.
    assert not _may_hold(INT32, member, layouts)
    assert not _may_hold(program.fields["V.s"], member, layouts)
    assert not _may_hold(p, member, layouts)
    # A tuple, an Optional and a union hold it only through an element.
    assert not _may_hold(TupleType((INT32, INT32)), member, layouts)
    assert not _may_hold(TupleType((ReadonlyType(INT32), INT32)), member, layouts)
    assert not _may_hold(OptionalType(p), member, layouts)
    assert _may_hold(TupleType((INT32, h)), member, layouts)
    assert _may_hold(OptionalType(h), member, layouts)
    assert _may_hold(h, member, layouts)
    # A record with no layout here may.
    assert _may_hold(program.fields["Shapes.g"], member, layouts)


def test_a_composed_payload_needs_a_movable_record(program: _Program) -> None:
    records = dict(program.definitions.records)
    p = program.types["P"]
    records[p] = replace(records[p], layout=replace(records[p].layout, movable=False))
    with pytest.raises(MIRUnsupported, match="constructor moves a nonmovable record"):
        constructor_initialization(program.constructors["Built"], records)


# --- writes ---------------------------------------------------------------------------------

def test_a_payload_field_write_through_a_readonly_holder_is_refused(program: _Program) -> None:
    # `h.p.x = n` under the presence proof `read` already holds: a store
    # into the inline payload. Through `read`'s own readonly holder it is
    # refused for the holder's access; through a mutable one by the payload
    # rule (lowering builds no such write: "optional field payload write").
    fn = program.verdicts["read"].function
    holder = next(s for s in fn.slots if s.name == "h")
    payload_x = MIRPlace(holder.id, (MIRDeref(), _member(program, "H.p"), MIROptionalPayload(), _member(program, "P.x")))
    blocks = list(fn.blocks)
    index, block = next((i, b) for i, b in enumerate(blocks)
                        if any(isinstance(s, MIRAssign) and s.value == MIRRead(payload_x) for s in b.statements))
    read = next(s for s in block.statements if isinstance(s, MIRAssign) and s.value == MIRRead(payload_x))
    store = MIRAssign(payload_x, MIRRead(read.target))
    blocks[index] = replace(block, statements=(*block.statements, store))
    written = replace(fn, blocks=tuple(blocks))
    assert holder.readonly
    mutable = bind_parameters(replace(written, slots=tuple(
        reslot(s, readonly=False) if s.id == holder.id else s for s in written.slots)))
    with pytest.raises(MIRValidationError, match="optional payload replacement is forbidden"):
        validate_function(mutable)
    with pytest.raises(MIRValidationError, match="^store through readonly reference$"):
        validate_function(written)

"""Summaries of bodies over records with inline record fields: reads, writes
and member holders through field chains under a borrowed record or the
body's private storage, published as full parameter paths; record members
returned as borrowed results and replaced whole; and record temporaries a
construct moves into the record or element it builds, which are the body's
own storage."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import INT32, NominalType
from .call_contract import MIRSummaryState
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies
from .dump import _write_step
from .liveness import analyze_liveness
from .nodes import (
    MIRAssign, MIRBorrow, MIRConstruct, MIRContainerElements, MIRDeref, MIRField, MIRFieldId, MIRFunction,
    MIRMove, MIROptionalPayload, MIRPlace, MIRRead, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
)
from .summaries import _member_chain, _private_records, summarize_function
from .validate import validate_function

SOURCE = """\
from tpy import int32, Own, copy, readonly


class Point:
    x: int32
    name: str

    def __init__(self, x: int32, name: str) -> None:
        self.x = x
        self.name = name


class Line:
    a: Point
    b: Point
    tag: int32

    def __init__(self, a: Point, b: Own[Point], tag: int32) -> None:
        self.a = copy(a)
        self.b = b
        self.tag = tag

    def bump(self) -> None:
        self.a.x += 1

    def total(self) -> int32:
        return self.a.x + self.b.x

    @readonly
    def first(self) -> Point:
        return self.a

    def reset(self, x: int32) -> None:
        self.a = Point(x, "r")

    def swap(self) -> None:
        t = copy(self.a)
        self.a = copy(self.b)
        self.b = copy(t)


class Frame:
    line: Line
    depth: int32

    def __init__(self, line: Own[Line], depth: int32) -> None:
        self.line = line
        self.depth = depth


class Base:
    a: Point

    def __init__(self, x: int32, name: str) -> None:
        self.a = Point(x, name)


class Derived(Base):
    n: int32

    def __init__(self, x: int32, name: str, n: int32) -> None:
        super().__init__(x, name)
        self.n = n


class Built:
    p: Point
    n: int32

    def __init__(self, x: int32, name: str) -> None:
        self.p = Point(x, name)
        self.n = x


def read_path(ln: Line) -> int32:
    return ln.a.x + ln.b.x


def read_deep(f: Frame) -> int32:
    return f.line.a.x + f.depth


def write_scalar(ln: Line) -> None:
    ln.a.x = 7


def write_deep(f: Frame) -> None:
    f.line.a.x = 3


def write_name(ln: Line) -> None:
    ln.a.name = "z"


def through_line(f: Frame) -> None:
    held = f.line
    held.a.x = 1


def mutate_through_member(ln: Line) -> int32:
    held = ln.a
    held.x = 50
    return ln.a.x


def inherited_member(d: Derived) -> None:
    d.a.x = 4


def local_member(x: int32) -> int32:
    ln = Line(Point(x, "a"), Point(x, "b"), 0)
    held = ln.a
    held.x = 5
    return ln.a.x


def leak(ln: Line) -> Point:
    held = ln.a
    return held


def take(ln: Own[Line]) -> int32:
    return ln.a.x + ln.b.x


def build(x: int32) -> Own[Line]:
    p = Point(x, "p")
    return Line(p, Point(x + 1, "q"), 0)


def build_frame(x: int32) -> int32:
    f = Frame(build(x), 2)
    return f.line.b.x + f.depth


def composed(x: int32, s: str) -> int32:
    b = Built(x, s)
    return b.p.x + b.n


def literal(n: int32) -> int32:
    ps: list[Point] = [Point(n, "l")]
    return len(ps)


class Shelf:
    p: readonly[Point]

    def __init__(self, p: Point) -> None:
        self.p = copy(p)


def shelf_point(s: Shelf) -> readonly[Point]:
    return s.p


def whole_line(f: Frame) -> Line:
    return f.line


def make(x: int32) -> Own[Point]:
    return Point(x, "m")


def replace_member(ln: Line) -> None:
    ln.a = Point(9, "n")


def member_from_param(ln: Line, p: Point) -> None:
    ln.a = copy(p)


def member_from_call(ln: Line, x: int32) -> None:
    ln.a = make(x)


def adopt(f: Frame, ln: Own[Line]) -> None:
    f.line = ln


def keep_and_store(dst: Frame, src: Own[Line]) -> int32:
    held = src.a
    dst.line = copy(src)
    return len(held.name)


def private_member_write(x: int32) -> int32:
    ln = Line(Point(x, "a"), Point(x, "b"), 0)
    ln.a = Point(x + 1, "c")
    return ln.a.x


def replace_live(ln: Line) -> bool:
    held = ln.a
    ln.a = Point(9, "new")
    return held.x > 0


def reset_live(ln: Line) -> bool:
    held = ln.a
    ln.reset(4)
    return held.x > 0


def member_receiver(f: Frame) -> bool:
    held = f.line.first()
    f.line.reset(9)
    return held.x > 0


def replace_ancestor(f: Frame) -> bool:
    held = f.line.first()
    f.line = Line(Point(1, "a"), Point(2, "b"), 0)
    return held.x > 0


def member_result(ln: Line) -> int32:
    p = ln.first()
    return p.x


def owned_local_path() -> int32:
    ln = Line(Point(1, "a"), Point(2, "b"), 3)
    ln.reset(4)
    ln.swap()
    return ln.a.x + ln.b.x
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    functions = {node.name: fn for node, fn in ctx.thir_functions.items() if fn.receiver is None}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, functions, mir.definitions,
                       {v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.fixture
def active(program):
    with activate_compiler(program.compiler):
        yield program


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _spelled(paths) -> set[str]:
    return {f"param{p.parameter}" + "".join(_write_step(s) for s in p.path) for p in paths}


def _private(fn: MIRFunction, facts_of: MIRFunction | None = None) -> frozenset[MIRSlotId]:
    # A hand-built body that would not validate takes the dependency facts of
    # the body it was built from.
    base = facts_of or fn
    return _private_records(fn, analyze_dependencies(base, analyze_liveness(base)))


def _construct_operands(fn: MIRFunction) -> list[MIRSlot]:
    """The owned record temporaries constructs take as operands."""
    return [fn.slots[f.index] for b in fn.blocks for s in b.statements
            if isinstance(s, MIRAssign) and isinstance(s.value, MIRConstruct) for f in s.value.fields
            if fn.slots[f.index].value_kind is MIRValueKind.OWNED and fn.slots[f.index].container_layout is None]


# --- reads, writes and member holders through field chains --------------------------

@pytest.mark.parametrize(("name", "writes"), [
    ("read_path", set()),
    ("read_deep", set()),
    ("Line.total", set()),
    ("take", set()),
    # A scalar or owned-leaf endpoint through one or two inline members.
    ("write_scalar", {"param0.a.x"}),
    ("Line.bump", {"param0.a.x"}),
    ("write_deep", {"param0.line.a.x"}),
    ("write_name", {"param0.a.name"}),
    # Through a holder of a member: the dependency pass resolves the holder
    # to the member's place under the parameter.
    ("mutate_through_member", {"param0.a.x"}),
    ("through_line", {"param0.line.a.x"}),
    # A member write into the body's own storage publishes nothing.
    ("local_member", set()),
])
def test_field_chains_summarize_known_with_their_full_write_paths(active, name: str, writes: set[str]) -> None:
    result = active.verdicts[name].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert _spelled(result.summary.writes) == writes and result.summary.returns == frozenset()


def test_an_inherited_member_hop_reads_the_parameter_storage_layout(active) -> None:
    # `d.a.x`: hop 0 reads the Derived storage, whose layout carries Base::a.
    result = active.verdicts["inherited_member"].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    (write,) = result.summary.writes
    assert [f.owner.name for f in write.path] == ["Base", "Point"]


class _Without:
    """The compiled definitions with one field left out of one record's
    layout, so the per-hop membership check has a field to miss."""

    def __init__(self, inner: MIRDefinitions, typ_name: str, field: str) -> None:
        self.inner, self.typ_name, self.field = inner, typ_name, field

    def get(self, node, typ):
        definition = self.inner.get(node, typ)
        if not (isinstance(typ, NominalType) and typ.name == self.typ_name):
            return definition
        layout = definition.layout
        return replace(definition, layout=replace(
            layout, fields=tuple(f for f in layout.fields if f.id.name != self.field)))


@pytest.mark.parametrize(("name", "record", "field"), [
    # Hop 0: the field must be in the parameter storage's layout, an
    # inherited one included.
    ("write_scalar", "Line", "a"),
    ("inherited_member", "Derived", "a"),
    # Hop 1: in the layout of the member record the previous field is.
    ("write_scalar", "Point", "x"),
    ("write_deep", "Line", "a"),
])
def test_a_write_field_missing_from_its_hop_layout_is_refused(active, name: str, record: str, field: str) -> None:
    fn = _lowered(active, name)
    result = summarize_function(active.functions[name], fn, _Without(active.definitions, record, field))
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary write field differs from definition"


def test_a_returned_member_holder_meets_the_return_rule(active) -> None:
    # The holder is tracked to `ln.a`: a record member of the result's type,
    # published as the one-hop return origin.
    assert isinstance(active.verdicts["leak"].function, MIRFunction)
    result = active.verdicts["leak"].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert _spelled(result.summary.returns) == {"param0.a"} and result.summary.writes == frozenset()


# --- record return origins and member storage operations ---------------------------

@pytest.mark.parametrize(("name", "writes", "returns"), [
    # `return self.a` / `return f.line`: a record member of exactly the
    # result's type; `s.p` is a `readonly[Point]` member lent to a readonly result.
    ("Line.first", set(), {"param0.a"}),
    ("whole_line", set(), {"param0.line"}),
    ("shelf_point", set(), {"param0.p"}),
    # A member replaced whole by a construct, a copy of a borrowed parameter,
    # a handed-over call result, or a move of an `Own` parameter.
    ("Line.reset", {"param0.a"}, set()),
    ("replace_member", {"param0.a"}, set()),
    ("member_from_param", {"param0.a"}, set()),
    ("member_from_call", {"param0.a"}, set()),
    ("adopt", {"param0.line"}, set()),
    # Member to member, out of a member into the body's own storage, and back.
    ("Line.swap", {"param0.a", "param0.b"}, set()),
    # A copy of an `Own` parameter (private storage) stored into a member
    # while a holder of one of its members lives on.
    ("keep_and_store", {"param0.line"}, set()),
    # A body with a conflict still publishes its effects.
    ("replace_live", {"param0.a"}, set()),
    # A member of the body's private storage replaced: nothing to publish.
    ("private_member_write", set(), set()),
])
def test_member_storage_operations_summarize_known(active, name: str, writes: set[str], returns: set[str]) -> None:
    result = active.verdicts[name].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert _spelled(result.summary.writes) == writes and _spelled(result.summary.returns) == returns


@pytest.mark.parametrize(("name", "record", "field"), [
    ("leak", "Line", "a"),
    ("whole_line", "Frame", "line"),
])
def test_a_return_field_missing_from_its_hop_layout_is_refused(active, name: str, record: str,
                                                                field: str) -> None:
    fn = _lowered(active, name)
    result = summarize_function(active.functions[name], fn, _Without(active.definitions, record, field))
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary return field differs from definition"


_COVERED = (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED)


@pytest.mark.parametrize(("name", "conflicts"), [
    # `reset`'s published `param0.a` replaces the member `held` borrows.
    ("reset_live", {"replacement"}),
    # The write lands under the receiver `f.line`, at `f.line.a`, which `held`
    # borrows through `first`'s return origin.
    ("member_receiver", {"replacement"}),
    # Replacing the ancestor `f.line` replaces the member `held` borrows.
    ("replace_ancestor", {"replacement"}),
    # `p` holds `ln.a` through `first`'s return origin; nothing replaces it.
    ("member_result", set()),
    ("owned_local_path", set()),
])
def test_callers_lower_through_member_summaries(active, name: str, conflicts: set[str]) -> None:
    verdict = active.verdicts[name]
    assert verdict.status in _COVERED and set(verdict.conflicts) == conflicts, verdict.describe()


def _adopt_with_member_holder(program: _Program, *, read_after: bool) -> MIRFunction:
    """`adopt` (`f.line = ln`, `ln: Own[Line]`) with a holder of `ln.a`,
    borrowed through the parameter's own holder before the move, read
    before or after it. THIR spells a copy while a borrow is live, so the
    MIR is extended by hand."""
    fn = _lowered(program, "adopt")
    block = fn.blocks[0]
    move = next(s for s in block.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIRMove))
    storage = move.value.source
    (ln,) = (s.target.root for s in block.statements
             if isinstance(s, MIRAssign) and s.value == MIRBorrow(MIRPlace(storage)))
    point = next(s.type for s in _lowered(program, "leak").slots if s.type.name == "Point")
    a = MIRField(MIRFieldId(fn.slots[storage.index].type, "a"), point)
    x = MIRField(MIRFieldId(point, "x"), INT32)
    held = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), a.type, MIRSlotKind.LOCAL, "held", form=th.Form.BORROW,
                   value_kind=MIRValueKind.BORROWED, residence=block.region)
    out = MIRSlot(MIRSlotId(fn.id, len(fn.slots) + 1), INT32, MIRSlotKind.TEMPORARY, residence=block.region)
    borrow = MIRAssign(MIRPlace(held.id), MIRBorrow(MIRPlace(ln, (MIRDeref(), a))))
    read = MIRAssign(MIRPlace(out.id), MIRRead(MIRPlace(held.id, (MIRDeref(), x))))
    index = block.statements.index(move)
    statements = (*block.statements[:index], borrow, *((move, read) if read_after else (read, move)),
                  *block.statements[index + 1:])
    return replace(fn, slots=(*fn.slots, held, out), blocks=(replace(block, statements=statements), *fn.blocks[1:]))


@pytest.mark.parametrize("read_after", [True, False])
def test_a_record_moved_into_a_member_is_a_transfer(active, read_after: bool) -> None:
    # The `Own` parameter moved into `f.line` is handed over: private only
    # when no holder of it (here of its member) is live at the move.
    fn = _adopt_with_member_holder(active, read_after=read_after)
    validate_function(fn)
    storage = next(s.id for s in fn.slots if s.name == "ln")
    assert (storage in _private(fn)) is not read_after
    result = summarize_function(active.functions["adopt"], fn, active.definitions)
    if read_after:
        assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary storage or value shape"
    else:
        assert result.state is MIRSummaryState.KNOWN, result.reason
        assert _spelled(result.summary.writes) == {"param0.line"}


@pytest.mark.parametrize("shape", ["borrowed", "private", "deep"])
def test_member_chains_admitted(active, shape: str) -> None:
    fn = _lowered(active, "local_member")
    slots = {s.id: s for s in fn.slots}
    private = _private(fn)
    (storage,) = (i for i in private if slots[i].type.name == "Line")
    line = slots[storage].type
    point = next(s.type for s in fn.slots if s.type.name == "Point")
    holder = next(s for s in fn.slots if s.name == "ln")
    a, x = MIRField(MIRFieldId(line, "a"), point), MIRField(MIRFieldId(point, "x"), INT32)
    place = {"borrowed": MIRPlace(holder.id, (MIRDeref(), a)), "private": MIRPlace(storage, (a,)),
             "deep": MIRPlace(holder.id, (MIRDeref(), a, x))}[shape]
    assert _member_chain(place, slots, private)


@pytest.mark.parametrize("shape", [
    "no_fields", "no_deref", "foreign_root", "scalar_hop", "payload", "projection",
])
def test_member_chains_refused(active, shape: str) -> None:
    fn = _lowered(active, "local_member")
    slots = {s.id: s for s in fn.slots}
    private = _private(fn)
    (storage,) = (i for i in private if slots[i].type.name == "Line")
    line = slots[storage].type
    point = next(s.type for s in fn.slots if s.type.name == "Point")
    holder = next(s for s in fn.slots if s.name == "ln")
    a, x = MIRField(MIRFieldId(line, "a"), point), MIRField(MIRFieldId(point, "x"), INT32)
    tag = MIRField(MIRFieldId(line, "tag"), INT32)
    place = {
        "no_fields": MIRPlace(holder.id, (MIRDeref(),)),
        # A borrowed holder's fields are read through its dereference.
        "no_deref": MIRPlace(holder.id, (a, x)),
        "foreign_root": MIRPlace(storage, (a, x)),
        # A field before the last must be a record member.
        "scalar_hop": MIRPlace(holder.id, (MIRDeref(), tag, x)),
        "payload": MIRPlace(holder.id, (MIRDeref(), a, MIROptionalPayload())),
        "projection": MIRPlace(holder.id, (MIRDeref(), a, MIRContainerElements())),
    }[shape]
    assert not _member_chain(place, slots, frozenset() if shape == "foreign_root" else private)


# --- record temporaries moved into a construct ------------------------------------

@pytest.mark.parametrize("name", ["build", "build_frame", "composed", "literal"])
def test_a_temporary_a_construct_moves_in_is_private(active, name: str) -> None:
    # `Line(p, Point(..), 0)` (an `Own[Point]` member), `Frame(build(x), 2)`
    # (a handed-over call result), `Built(x, s)` (a composed member built
    # over the operands), `[Point(n, "l")]` (an element).
    fn = _lowered(active, name)
    moved = _construct_operands(fn)
    assert moved and all(s.kind is MIRSlotKind.TEMPORARY for s in moved)
    assert {s.id for s in moved} <= _private(fn)
    result = active.verdicts[name].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == frozenset() and result.summary.returns == frozenset()


def test_a_temporary_lent_to_a_construct_is_kept_private(active) -> None:
    # `Line(Point(x, "a"), Point(x, "b"), 0)`: the first temporary is lent
    # through its holder (a `const Point&` member copies it), the second
    # moved in; with the line itself every record storage is the body's own.
    fn = _lowered(active, "local_member")
    lent = [s for s in fn.slots if s.value_kind is MIRValueKind.OWNED and s.type.name == "Point"
            and s not in _construct_operands(fn)]
    assert lent and any(isinstance(st.value, MIRBorrow) and st.value.source == MIRPlace(lent[0].id)
                        for b in fn.blocks for st in b.statements if isinstance(st, MIRAssign))
    owned = {s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED and s.container_layout is None}
    assert len(owned) == 3 and _private(fn) == owned


def _with_after_construct(fn: MIRFunction, temporary: MIRSlot, *extra: MIRAssign,
                          before: tuple[MIRAssign, ...] = ()) -> MIRFunction:
    blocks = []
    for block in fn.blocks:
        statements = []
        for stmt in block.statements:
            moves = isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRConstruct) and (
                temporary.id in stmt.value.fields)
            if moves:
                statements.extend(before)
            statements.append(stmt)
            if moves:
                statements.extend(extra)
        blocks.append(replace(block, statements=tuple(statements)))
    return replace(fn, blocks=tuple(blocks))


def test_a_temporary_read_beside_the_construct_stays_foreign(active) -> None:
    # Hand-built: a field read of the moved temporary, which no source spells.
    fn = _lowered(active, "build")
    (temporary,) = _construct_operands(fn)
    point = temporary.type
    value = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), INT32, MIRSlotKind.TEMPORARY, residence=temporary.residence)
    read = MIRAssign(MIRPlace(value.id), MIRRead(MIRPlace(temporary.id, (MIRField(MIRFieldId(point, "x"), INT32),))))
    damaged = replace(_with_after_construct(fn, temporary, read), slots=(*fn.slots, value))
    assert temporary.id not in _private(damaged, fn)


def test_a_holder_live_at_the_construct_keeps_the_temporary_out(active) -> None:
    # Hand-built: a holder borrows the temporary before the construct moves it
    # and is read after.
    fn = _lowered(active, "build")
    (temporary,) = _construct_operands(fn)
    point = temporary.type
    holder = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), point, MIRSlotKind.TEMPORARY, form=th.Form.BORROW,
                     value_kind=MIRValueKind.BORROWED, residence=temporary.residence)
    value = MIRSlot(MIRSlotId(fn.id, len(fn.slots) + 1), INT32, MIRSlotKind.TEMPORARY,
                    residence=temporary.residence)
    borrow = MIRAssign(MIRPlace(holder.id), MIRBorrow(MIRPlace(temporary.id)))
    read = MIRAssign(MIRPlace(value.id), MIRRead(MIRPlace(holder.id, (
        MIRDeref(), MIRField(MIRFieldId(point, "x"), INT32)))))
    held = replace(_with_after_construct(fn, temporary, read, before=(borrow,)), slots=(*fn.slots, holder, value))
    validate_function(held)
    assert temporary.id not in _private(held)
    result = summarize_function(active.functions["build"], held, active.definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary storage or value shape"


def test_only_a_temporary_operand_is_moved_in(active) -> None:
    # Hand-built, never validated: a construct names no other owned record
    # storage as an operand (the validator moves only temporaries), so any
    # other is a read that leaves the slot foreign.
    fn = _lowered(active, "build")
    (temporary,) = _construct_operands(fn)
    local = replace(temporary, kind=MIRSlotKind.LOCAL)
    named = replace(fn, slots=tuple(local if s.id == temporary.id else s for s in fn.slots))
    assert temporary.id in _private(fn)
    assert temporary.id not in _private(named, fn)

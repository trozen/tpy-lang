"""MIR over records with inline record fields: a definition composing its
member records' verified definitions (and its container fields' elements),
the member initializer's copy, move and composed construct, the record
copy's exceptional exit read from the whole layout, `Own[R]` parameters as
the body's owned storage, and callers constructing such records."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.scalar_leaves import record_type
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import NominalType, ReadonlyType, unwrap_readonly, unwrap_ref_type
from .call_contract import (
    MIRCallSummary, MIRGlobalId, MIRParameterBinding, MIRParameterWrite, MIRReturnOrigin, MIRSummaryResult,
    MIRSummaryState, summary_problem,
)
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .coverage import MIRUnsupported
from .definitions import (
    MIRComposedConstruct, MIRConstructorDefinition, MIRDefinitions, constructor_initialization,
    layout_copy_may_raise,
)
from .dependencies import MIRReferent, analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBorrow, MIRCall, MIRConstant, MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFieldId,
    MIRFunction, MIRMemberInit, MIRMemberInitMode, MIRMemberInits, MIRMove, MIRNotCovered, MIRPlace, MIRRead,
    MIRRecordLayout, MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRValueKind,
)
from .retention import affects, analyze_retention
from .storage import analyze_storage, storage_destination
from .testutil import Reference, execute
from .validate import MIRValidationError, validate_function

SOURCE = """\
from tpy import int32, Own, ValueType, copy, readonly


class Point:
    x: int32
    name: str

    def __init__(self, x: int32, name: str) -> None:
        self.x = x
        self.name = name

    def shift(self) -> None:
        self.x += 1

    @readonly
    def get(self) -> int32:
        return self.x


class Flat:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Line:
    a: Point
    b: Point

    def __init__(self, a: Point, b: Own[Point]) -> None:
        self.a = a
        self.b = b

    @readonly
    def first(self) -> Point:
        return self.a

    def reset(self, x: int32) -> None:
        self.a = Point(x, "r")

    def bump(self) -> None:
        self.a.x += 1

    def swap(self) -> None:
        t = copy(self.a)
        self.a = copy(self.b)
        self.b = copy(t)

    def adopt(self, p: Own[Point]) -> None:
        self.a = p

    def take(self, p: Point) -> None:
        self.a = p


class Frame:
    line: Line
    depth: int32

    def __init__(self, line: Own[Line], depth: int32) -> None:
        self.line = line
        self.depth = depth


class Copied:
    a: Point

    def __init__(self, a: Point) -> None:
        self.a = copy(a)


class Built:
    p: Point
    n: int32

    def __init__(self, x: int32, name: str) -> None:
        self.p = Point(x, name)
        self.n = x


class Literal:
    p: Point

    def __init__(self, name: str) -> None:
        self.p = Point(3, name)


class Corner:
    f: Flat

    def __init__(self, x: int32) -> None:
        self.f = Flat(x, 5)


class Tagged:
    x: int32
    name: str

    def __init__(self, x: int32, name: Own[str]) -> None:
        self.x = x
        self.name = name


class MoveLeg:
    t: Tagged

    def __init__(self, name: Own[str]) -> None:
        self.t = Tagged(1, name)


class Deep:
    b: Built

    def __init__(self, s: str) -> None:
        self.b = Built(7, s)


class Listed:
    items: list[int32]
    n: int32

    def __init__(self, n: int32) -> None:
        self.items = [n, n]
        self.n = n


class HasListed:
    l: Listed

    def __init__(self, k: int32) -> None:
        self.l = Listed(k)


class Names:
    first: str
    second: str

    def __init__(self, first: str, second: str) -> None:
        self.first = first
        self.second = second


class Twice:
    names: Names

    def __init__(self, s: str) -> None:
        self.names = Names(s, s)


class FlatHolder:
    f: Flat

    def __init__(self, f: Flat) -> None:
        self.f = f


class Mutated:
    a: Point

    def __init__(self, p: Point) -> None:
        p.x = 1
        self.a = p


def mk(x: int32) -> Own[Point]:
    return Point(x, "m")


class Called:
    a: Point

    def __init__(self, x: int32) -> None:
        self.a = mk(x)


class Nested:
    line: Line

    def __init__(self, a: Point, b: Own[Point]) -> None:
        self.line = Line(a, b)


class Hooked:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        print("bye")


class HasHooked:
    h: Hooked

    def __init__(self, n: int32) -> None:
        self.h = Hooked(n)


class Effect:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        print(n)


class HasEffect:
    e: Effect

    def __init__(self, n: int32) -> None:
        self.e = Effect(n)


class Meters(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class HasMeters:
    m: Meters

    def __init__(self, v: int32) -> None:
        self.m = Meters(v)


class Err(Exception):
    code: int32

    def __init__(self, code: int32) -> None:
        self.code = code


class HasErr:
    e: Err

    def __init__(self, code: int32) -> None:
        self.e = Err(code)


class Loud:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Loud]:
        return Loud(self.n)


class Bag:
    items: list[Loud]

    def __init__(self) -> None:
        self.items = []


class Plain:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]


def duplicate(b: Bag) -> Own[Bag]:
    return copy(b)


def duplicate_plain(b: Plain) -> Own[Plain]:
    return copy(b)


def duplicate_flat(f: FlatHolder) -> Own[FlatHolder]:
    return copy(f)


def duplicate_point(p: Point) -> Own[Point]:
    return copy(p)


def consume(p: Own[Point]) -> int32:
    return p.x


def hand_over(x: int32) -> int32:
    return consume(Point(x, "h"))


def give(p: Own[Point]) -> Own[Point]:
    return p


def keep(p: Own[Point]) -> int32:
    p.x = 3
    return p.x


def take_hooked(h: Own[Hooked]) -> int32:
    return h.n


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0


def stamp(c: Counter) -> Own[Point]:
    c.n += 1
    return Point(c.n, "s")


def lent_name(p: Point, x: int32) -> int32:
    ln = Line(p, Point(x, "q"))
    return ln.a.x + ln.b.x


def lent_temporary(x: int32) -> int32:
    ln = Line(Point(x, "a"), mk(x))
    return ln.a.x + ln.b.x


def copied_argument(p: Point) -> int32:
    ln = Line(p, copy(p))
    return ln.b.x


def composed_callers(x: int32, s: str) -> int32:
    b = Built(x, s)
    t = Twice(s)
    lit = Literal(s)
    return b.p.x + b.n + len(t.names.second) + lit.p.x


def member_place(ln: Line, x: int32) -> int32:
    other = Line(ln.a, mk(x))
    return other.a.x


def effectful(c: Counter) -> int32:
    ln = Line(Point(1, "a"), stamp(c))
    return ln.b.x


def moved_local(x: int32) -> int32:
    q = Point(x, "q")
    ln = Line(Point(1, "a"), q)
    return ln.b.x


def in_tuple(x: int32) -> int32:
    t = (Built(x, "t"), 1)
    return t[0].n


def hooked_member(n: int32) -> int32:
    h = HasHooked(n)
    return h.h.n


def listed(x: int32) -> int32:
    xs = [Built(x, "e")]
    return xs[0].n


def escape_member(flag: bool, fallback: Point) -> int32:
    held = fallback
    if flag:
        local = Line(Point(1, "a"), Point(2, "b"))
        held = local.a
    return held.x


def replace_member(ln: Line) -> None:
    ln.a = Point(9, "n")


def replace_live(ln: Line) -> bool:
    held = ln.a
    ln.a = Point(9, "new")
    return held.x > 0


def alias_external(left: Line, right: Line) -> bool:
    held = left.a
    right.a = Point(9, "new")
    return held.x > 0


def sibling_survives(ln: Line) -> int32:
    held = ln.b
    ln.a = Point(9, "new")
    return held.x


def name_then_replace(ln: Line) -> int32:
    n = ln.a.name
    ln.a = Point(1, "z")
    return len(n)


def replace_enclosing(f: Frame) -> int32:
    held = f.line
    f.line = Line(Point(1, "a"), Point(2, "b"))
    return held.a.x


def member_copy(ln: Line) -> None:
    ln.a = copy(ln.b)


def implicit_copy(ln: Line, p: Point) -> None:
    ln.a = p


def called_member(ln: Line, x: int32) -> None:
    ln.a = mk(x)


def adopt(f: Frame, ln: Own[Line]) -> None:
    f.line = ln


def keep_and_store(dst: Frame, src: Own[Line]) -> int32:
    held = src.a
    dst.line = copy(src)
    return len(held.name)


def copy_then_replace(ln: Line) -> int32:
    p = copy(ln.a)
    ln.a = Point(5, "c")
    return p.x


def copy_does_not_replace_source(ln: Line) -> int32:
    held = ln.a
    independent = copy(ln.a)
    return held.x + independent.x


def copied_elements(ln: Line, ps: list[Point]) -> int32:
    qs = [copy(ln.b), copy(ln.a)]
    ps[0] = copy(ln.a)
    return qs[0].x


def read_point(p: Point) -> int32:
    return p.x


def member_arguments(ln: Line) -> int32:
    ln.a.shift()
    return read_point(ln.b) + ln.a.get()


def readonly_member(ln: readonly[Line]) -> int32:
    return ln.a.get()


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def grow(self) -> None:
        self.n += 1


class Sub(Base):
    m: int32

    def __init__(self, n: int32) -> None:
        super().__init__(n)
        self.m = n


class HasSub:
    s: Sub

    def __init__(self, n: int32) -> None:
        self.s = Sub(n)


def via_member(h: HasSub) -> None:
    h.s.grow()


class HoldsBase:
    b: Base

    def __init__(self, n: int32) -> None:
        self.b = Base(n)


def sliced_copy(h: HoldsBase, s: Sub) -> None:
    h.b = copy(s)


class OwnLeg:
    p: Point

    def __init__(self, s: Own[str]) -> None:
        self.p = Point(1, s)


def own_leg(s: Own[str]) -> int32:
    o = OwnLeg(s)
    return o.p.x


class Labeled:
    n: int32
    label: str

    def __init__(self, n: int32) -> None:
        self.n = n
        self.label = "k"


class HasLabeled:
    l: Labeled

    def __init__(self, n: int32) -> None:
        self.l = Labeled(n)


def has_labeled(n: int32) -> int32:
    h = HasLabeled(n)
    return h.l.n


def scalar_call(ln: Line) -> int32:
    held = ln.b
    ln.bump()
    return held.x


def reset_live(ln: Line) -> bool:
    held = ln.a
    ln.reset(4)
    return held.x > 0


def reset_sibling(ln: Line) -> int32:
    held = ln.b
    ln.reset(4)
    return held.x


def member_receiver(f: Frame) -> bool:
    held = f.line.first()
    f.line.reset(9)
    return held.x > 0


def replace_ancestor(f: Frame) -> bool:
    held = f.line.first()
    f.line = Line(Point(1, "a"), Point(2, "b"))
    return held.x > 0
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    constructors: dict[str, th.THIRConstructor]
    definitions: MIRDefinitions
    verdicts: dict[str, MIRBodyVerdict]
    functions: dict[str, th.THIRFunction]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, {c.record_name: c for c in ctx.thir_constructors.values()}, mir.definitions,
                       {v.body.declaration.split("@")[0]: v for v in verdicts},
                       {node.name: fn for node, fn in ctx.thir_functions.items() if fn.receiver is None})


@pytest.fixture(autouse=True)
def active(program):
    with activate_compiler(program.compiler):
        yield


def _record(program: _Program, name: str) -> NominalType:
    return program.constructors[name].record_layout.type


def _definition(program: _Program, name: str) -> MIRConstructorDefinition | str:
    return program.definitions.records[_record(program, name)]


def _fields(initializers) -> list[tuple[str, object, str, bool]]:
    return [(i.field.id.name, _fields(i.source.initializers) if isinstance(i.source, MIRComposedConstruct)
             else i.source, i.mode.name, i.may_raise) for i in initializers]


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _refusal(program: _Program, name: str) -> str:
    verdict = program.verdicts[name]
    assert verdict.function is None, f"{name} lowered"
    return verdict.reason


def _initialization_refusal(ctor: th.THIRConstructor, definitions) -> str:
    try:
        constructor_initialization(ctor, definitions)
    except MIRUnsupported as failure:
        return failure.reason
    raise AssertionError("initialized")


# --- the definitions chain -----------------------------------------------------------

@pytest.mark.parametrize("name,reason", [
    # A member with a custom special member: the outer storage runs it.
    ("HasHooked", "member definition: custom record special member"),
    # A member whose constructor has effects has no verified definition.
    ("HasEffect", "member definition: constructor body effects"),
    # Record kinds MIR models no layout for: a value type, an exception.
    ("HasMeters", "member definition: missing constructor definition"),
    ("HasErr", "member definition: missing constructor definition"),
    # A container field whose element runs a user copy hook.
    ("Bag", "unsupported native container element"),
])
def test_a_definition_composes_its_members(program: _Program, name: str, reason: str) -> None:
    assert _definition(program, name) == reason


def test_a_missing_member_definition_refuses_by_member(program: _Program) -> None:
    assert _initialization_refusal(program.constructors["Built"], {}) == (
        "member definition: missing constructor definition")


def test_a_cyclic_member_refuses_neutrally(program: _Program) -> None:
    point = _record(program, "Point")
    ctor = program.constructors["Point"]
    # A record holding itself inline: C++ forbids it, so only malformed input.
    looped = replace(ctor, record_layout=replace(ctor.record_layout, fields=(
        *ctor.record_layout.fields, th.THIRFieldIdentity(point, "again", point))))
    assert MIRDefinitions((looped,)).records[point] == "member definition: cyclic record definition"


# --- member initializers -------------------------------------------------------------

def test_member_initializer_modes(program: _Program) -> None:
    # A record parameter lent readonly is copied (its str makes the copy
    # raise); an `Own[R]` parameter is moved.
    assert _fields(_definition(program, "Line").initializers) == [
        ("a", "a", "COPY", True), ("b", "b", "MOVE", False)]
    assert _fields(_definition(program, "Copied").initializers) == [("a", "a", "COPY", True)]
    # An all-scalar member copies with no allocation.
    assert _fields(_definition(program, "FlatHolder").initializers) == [("f", "f", "COPY", False)]


def test_an_explicit_copy_is_the_same_member_copy(program: _Program) -> None:
    ctor = program.constructors["Copied"]
    mil = ctor.mil_inits[0]
    explicit = replace(ctor, mil_inits=(replace(mil, value=th.THIRCopy(
        mil.value.result_type, value=mil.value, cpp_type="Point", form=th.Form.STORAGE)),))
    assert _fields(constructor_initialization(explicit, program.definitions.records).initializers) == [
        ("a", "a", "COPY", True)]


def test_composed_members(program: _Program) -> None:
    # The member's own definition composed with one leg per argument.
    assert _fields(_definition(program, "Built").initializers) == [
        ("p", [("x", "x", "SCALAR", False), ("name", "name", "COPY", True)], "MOVE", True),
        ("n", "x", "SCALAR", False)]
    # A literal leg takes the member's mode.
    assert _fields(_definition(program, "Literal").initializers) == [
        ("p", [("x", MIRConstant(3), "SCALAR", False), ("name", "name", "COPY", True)], "MOVE", True)]
    # One parameter in two borrowed legs: two copies.
    assert _fields(_definition(program, "Twice").initializers) == [
        ("names", [("first", "s", "COPY", True), ("second", "s", "COPY", True)], "MOVE", True)]


@pytest.mark.parametrize("name,reason", [
    # A call result: no parameter or literal to build from.
    ("Called", "constructor initializer needs parameter or literal"),
    # A record argument to the member's constructor: a lend no leg models.
    ("Nested", "member argument needs matching parameter or literal"),
    # A record lent mutably (MUT_REF).
    ("Mutated", "constructor parameter type"),
])
def test_member_initializer_refusals(program: _Program, name: str, reason: str) -> None:
    assert _definition(program, name) == reason


@pytest.mark.parametrize("name,reason", [
    # An `Own[str]` parameter passed to the member's `str` parameter: a leg
    # binds a parameter at its own passing, so none models the lend.
    ("OwnLeg", "member argument needs matching parameter or literal"),
    # A member whose definition materializes an owned-leaf constant: a
    # caller's construct has no operand for it.
    ("Labeled", "constructor owned-leaf constant"),
    ("HasLabeled", "member definition: constructor owned-leaf constant"),
])
def test_kept_composed_member_refusals(program: _Program, name: str, reason: str) -> None:
    assert _definition(program, name) == reason


@pytest.mark.parametrize("name,reason", [
    ("OwnLeg.__init__", "member argument needs matching parameter or literal"),
    ("own_leg", "member argument needs matching parameter or literal"),
    ("HasLabeled.__init__", "member definition: constructor owned-leaf constant"),
    ("has_labeled", "member definition: constructor owned-leaf constant"),
])
def test_kept_composed_member_refusals_reach_bodies(program: _Program, name: str, reason: str) -> None:
    # The constructor body and its callers refuse with the definition.
    assert _refusal(program, name) == reason


def _with_member_layout(program: _Program, name: str, **changes) -> dict[NominalType, object]:
    """The verified definitions with `name`'s layout changed."""
    records = dict(program.definitions.records)
    typ = _record(program, name)
    records[typ] = replace(records[typ], layout=replace(records[typ].layout, **changes))
    return records


def test_member_initializer_capability_refusals(program: _Program) -> None:
    # A lent member is copied and an `Own[R]` one moved: each needs the
    # member layout's capability.
    assert _initialization_refusal(program.constructors["Copied"], _with_member_layout(
        program, "Point", copyable=False)) == "constructor copies a noncopyable record"
    assert _initialization_refusal(program.constructors["Line"], _with_member_layout(
        program, "Point", movable=False)) == "constructor moves a nonmovable record"
    # An explicit copy into a member yields storage; THIR spells no other form.
    ctor = program.constructors["Copied"]
    mil = ctor.mil_inits[0]
    borrowed = replace(ctor, mil_inits=(replace(mil, value=th.THIRCopy(
        mil.value.result_type, value=mil.value, cpp_type="Point", form=th.Form.BORROW)),))
    assert _initialization_refusal(borrowed, program.definitions.records) == "constructor copy form"


def test_a_borrowed_parameter_is_never_moved(program: _Program) -> None:
    ctor = program.constructors["Line"]
    a, b, *_ = ctor.mil_inits
    moved = replace(ctor, mil_inits=(replace(a, value=th.THIRMove(a.value.result_type, value=a.value)), b))
    assert _initialization_refusal(moved, program.definitions.records) == "constructor move needs an owned parameter"


# --- record copies may raise ---------------------------------------------------------

def test_layout_copy_may_raise(program: _Program) -> None:
    layouts = {t: d.layout for t, d in program.definitions.records.items()
               if isinstance(d, MIRConstructorDefinition)}
    may_raise = {name: layout_copy_may_raise(layouts[_record(program, name)], layouts.get)
                 for name in ("Flat", "FlatHolder", "Point", "Line", "Plain")}
    # All-scalar storage copies with no allocation; a str one level down, or
    # a container field, allocates.
    assert may_raise == {"Flat": False, "FlatHolder": False, "Point": True, "Line": True, "Plain": True}
    # A member whose layout is unknown may.
    assert layout_copy_may_raise(layouts[_record(program, "FlatHolder")], {}.get)


@pytest.mark.parametrize("name,raises", [
    ("duplicate_plain", True), ("duplicate_point", True), ("duplicate_flat", False)])
def test_a_record_copy_reads_its_whole_layout(program: _Program, name: str, raises: bool) -> None:
    fn = _lowered(program, name)
    copy, = (s.value for b in fn.blocks for s in b.statements if isinstance(getattr(s, "value", None), MIRCopy))
    assert copy.may_raise is raises and fn.exceptional_exits is raises
    # The validator re-derives the fact from the layouts.
    with pytest.raises(MIRValidationError, match="record copy source or eligibility"):
        validate_function(_replace_copy(fn, replace(copy, may_raise=not raises)))


def _replace_copy(fn: MIRFunction, value: MIRCopy) -> MIRFunction:
    blocks = tuple(replace(b, statements=tuple(
        replace(s, value=value) if isinstance(getattr(s, "value", None), MIRCopy) else s for s in b.statements))
        for b in fn.blocks)
    return replace(fn, blocks=blocks, exceptional_exits=value.may_raise)


def test_a_hooked_element_refuses_the_copy(program: _Program) -> None:
    # A copy of `Bag` runs `Loud.__copy__`: no definition, so no copy fact.
    assert _refusal(program, "duplicate") == "unsupported native container element"


# --- constructor bodies --------------------------------------------------------------

@pytest.mark.parametrize("name,receiver,raises", [
    # A composed member is initialized field by field, like the receiver.
    ("Built", "(move {%1, copy (*%2) may-raise}, %1)", True),
    # One lent parameter read by both copies.
    ("Twice", "(move {copy (*%1) may-raise, copy (*%1) may-raise})", True),
    # A literal leg is the field's constant, a scalar's by value.
    ("Literal", "(move {3, copy (*%1) may-raise})", True),
    ("Corner", "(move {%1, 5})", False),
    # A move leg moves the parameter's own storage into the field.
    ("MoveLeg", "(move {1, move %1})", False),
    # A composed member of a composed member, and one building a container.
    ("Deep", "(move {move {7, copy (*%1) may-raise}, 7})", True),
    ("HasListed", "(move {move construct (%1, %1), %1})", True),
    ("Copied", "(copy (*%1) may-raise)", True),
    ("FlatHolder", "(copy (*%1))", False),
])
def test_composed_member_bodies_lower(program: _Program, name: str, receiver: str, raises: bool) -> None:
    fn = _lowered(program, f"{name}.__init__")
    assert f"initialize-receiver %0 {receiver}" in dump_function(fn)
    # A raising field anywhere in the initialization makes the body raise.
    assert fn.exceptional_exits is raises
    with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
        validate_function(replace(fn, exceptional_exits=not raises))


def test_nested_entry_operands(program: _Program) -> None:
    fn = _lowered(program, "Deep.__init__")
    s, = (slot.id for slot in fn.slots if slot.name == "s")
    # The parameter a nested field copies from is live at entry.
    assert s in analyze_liveness(fn).entry_live
    corner = _lowered(program, "Corner.__init__")
    flat = _record(program, "Flat")
    heap = {1: {}}
    # Entry initialization builds the member's storage in place.
    execute(corner, Reference(1), 4, heap=heap)
    assert heap[1] == {MIRFieldId(_record(program, "Corner"), "f"): {
        MIRFieldId(flat, "x"): 4, MIRFieldId(flat, "y"): 5}}


def _receiver_member(fn: MIRFunction, index: int, **changes) -> MIRFunction:
    init = fn.receiver_init
    fields = list(init.fields)
    fields[index] = replace(fields[index], **changes)
    return replace(fn, receiver_init=replace(init, fields=tuple(fields)))


def _nested_member(fn: MIRFunction, path: tuple[int, ...], **changes) -> MIRFunction:
    """`fn` with the member initializer at `path` (outer index, then one
    index per nested level) changed, every enclosing level kept."""
    def change(inits: tuple[MIRMemberInit, ...], path: tuple[int, ...]) -> tuple[MIRMemberInit, ...]:
        fields = list(inits)
        index, *rest = path
        fields[index] = (replace(fields[index], **changes) if not rest else replace(
            fields[index], source=MIRMemberInits(change(fields[index].source.fields, tuple(rest)))))
        return tuple(fields)
    return replace(fn, receiver_init=replace(fn.receiver_init, fields=change(fn.receiver_init.fields, path)))


def test_receiver_record_member_validation(program: _Program) -> None:
    built = _lowered(program, "Built.__init__")
    nested = built.receiver_init.fields[0].source
    assert isinstance(nested, MIRMemberInits)
    # A composed member is built, then moved in.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer construct"):
        validate_function(_receiver_member(built, 0, mode=MIRMemberInitMode.COPY))
    # One initializer per field of the member's layout.
    with pytest.raises(MIRValidationError, match="incomplete receiver initialization"):
        validate_function(_receiver_member(built, 0, source=MIRMemberInits(nested.fields[:1])))
    # The member's exit fact is its fields'.
    with pytest.raises(MIRValidationError, match="receiver initializer exit fact mismatch"):
        validate_function(_receiver_member(built, 0, may_raise=False))
    with pytest.raises(MIRValidationError, match="receiver initializer exit fact mismatch"):
        validate_function(_nested_member(built, (0, 1), may_raise=False))
    # Each nested field follows its own field's rules: a scalar by value,
    # a lent owned leaf copied, never moved out of the caller's storage.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer mode"):
        validate_function(_nested_member(built, (0, 0), mode=MIRMemberInitMode.COPY))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_nested_member(built, (0, 1), mode=MIRMemberInitMode.MOVE, may_raise=False))
    # A constant takes its field's type.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer constant"):
        validate_function(_nested_member(_lowered(program, "Literal.__init__"), (0, 0), source=MIRConstant("3")))
    # Only a record field is initialized field by field.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer$"):
        validate_function(_receiver_member(built, 1, source=nested))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer$"):
        validate_function(_nested_member(built, (0, 0), source=nested))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer$"):
        validate_function(_nested_member(built, (0, 1), source=nested))
    deep = _lowered(program, "Deep.__init__")
    # Two levels down: the rules recurse into the member's own layout.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_nested_member(deep, (0, 0, 1), mode=MIRMemberInitMode.MOVE, may_raise=False))
    with pytest.raises(MIRValidationError, match="receiver initializer exit fact mismatch"):
        validate_function(_nested_member(deep, (0, 0), may_raise=False))
    # A move leg moves the body's own storage; a constant is materialized,
    # never moved.
    move_leg = _lowered(program, "MoveLeg.__init__")
    with pytest.raises(MIRValidationError, match="invalid receiver initializer constant"):
        validate_function(_nested_member(move_leg, (0, 1), source=MIRConstant("n")))
    listed = _lowered(program, "HasListed.__init__")
    # A nested container literal is moved in and allocates.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer literal"):
        validate_function(_nested_member(listed, (0, 0), mode=MIRMemberInitMode.COPY))
    with pytest.raises(MIRValidationError, match="receiver initializer exit fact mismatch"):
        validate_function(_nested_member(listed, (0, 0), may_raise=False))
    copied = _lowered(program, "Copied.__init__")
    # A lent record is copied, never moved out of the caller's storage.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_receiver_member(copied, 0, mode=MIRMemberInitMode.MOVE, may_raise=False))
    with pytest.raises(MIRValidationError, match="receiver initializer exit fact mismatch"):
        validate_function(_receiver_member(copied, 0, may_raise=False))
    # A constant is no record source.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer"):
        validate_function(_receiver_member(copied, 0, source=MIRConstant(1)))


def test_a_record_field_needs_its_member_layout(program: _Program) -> None:
    fn = _lowered(program, "Copied.__init__")
    point = _record(program, "Point")
    with pytest.raises(MIRValidationError, match="record field needs its layout"):
        validate_function(replace(fn, records=tuple(r for r in fn.records if r.type != point)))
    opaque = MIRRecordLayout(point, (), True, True, opaque=True)
    with pytest.raises(MIRValidationError, match="invalid opaque leaf layout|record field needs its layout"):
        validate_function(replace(fn, records=tuple(opaque if r.type == point else r for r in fn.records)))


def test_record_member_initializer_capabilities(program: _Program) -> None:
    point = _record(program, "Point")
    copied = _lowered(program, "Copied.__init__")
    validate_function(copied)
    # A member copy needs a copyable layout ...
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_with_layout(copied, point, copyable=False))
    # ... and reads through a readonly lend.
    lent = copied.slots[copied.receiver_init.fields[0].source.index]
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_with_slot(copied, lent, readonly=False))
    # A move out of the `Own[R]` parameter, and a member built field by
    # field then moved in, need a movable layout.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_with_layout(_lowered(program, "Line.__init__"), point, movable=False))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer construct"):
        validate_function(_with_layout(_lowered(program, "Built.__init__"), point, movable=False))


# --- `Own[R]` parameters -------------------------------------------------------------

def _handed(fn: MIRFunction) -> MIRSlot:
    slot, = (s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.value_kind is MIRValueKind.OWNED)
    return slot


def _with_slot(fn: MIRFunction, slot: MIRSlot, **changes) -> MIRFunction:
    return replace(fn, slots=tuple(replace(s, **changes) if s.id == slot.id else s for s in fn.slots))


@pytest.mark.parametrize("name", ["consume", "give", "keep"])
def test_an_owned_record_parameter_is_body_storage(program: _Program, name: str) -> None:
    fn = _lowered(program, name)
    storage = _handed(fn)
    assert (storage.passing is ParamPassing.OWN and storage.storage_duration is MIRStorageDuration.BODY
            and storage.name == "p" and not storage.readonly)
    # The body reaches it through a holder borrowed at entry, as an owned local.
    entry = fn.blocks[0].statements[0]
    assert isinstance(entry.value, MIRBorrow) and entry.value.source == MIRPlace(storage.id)
    holder = fn.slots[entry.target.root.index]
    assert holder.value_kind is MIRValueKind.BORROWED and not holder.readonly


def test_an_owned_record_parameter_is_returned_as_storage(program: _Program) -> None:
    fn = _lowered(program, "give")
    returned, = (b.terminator.value for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    assert returned == _handed(fn).id


def _returning(fn: MIRFunction, value: MIRSlotId) -> MIRFunction:
    return replace(fn, blocks=tuple(replace(b, terminator=replace(b.terminator, value=value))
                                    if isinstance(b.terminator, MIRReturn) else b for b in fn.blocks))


def test_an_owned_record_return_moves_the_storage_itself(program: _Program) -> None:
    give = _lowered(program, "give")
    holder = give.blocks[0].statements[0].target.root
    # The holder of the `Own[R]` parameter's storage is a borrow: returning
    # it would hand over storage the caller destroys.
    with pytest.raises(MIRValidationError, match="owned record return needs movable owned storage"):
        validate_function(_returning(give, holder))
    # A lent record parameter is the caller's storage.
    duplicated = _lowered(program, "duplicate_point")
    lent, = (s.id for s in duplicated.slots if s.kind is MIRSlotKind.PARAMETER)
    with pytest.raises(MIRValidationError, match="owned record return needs movable owned storage"):
        validate_function(_returning(duplicated, lent))


def test_a_caller_hands_a_temporary_over(program: _Program) -> None:
    fn = _lowered(program, "hand_over")
    call, = (s.value for b in fn.blocks for s in b.statements if isinstance(getattr(s, "value", None), MIRCall))
    argument = fn.slots[call.arguments[0].index]
    assert argument.value_kind is MIRValueKind.OWNED and argument.kind is MIRSlotKind.TEMPORARY


def test_an_owned_record_parameter_needs_its_definition(program: _Program) -> None:
    # The storage is moved from and destroyed around the body: a hooked
    # record has no definition.
    assert _refusal(program, "take_hooked") == "custom record special member"


@pytest.mark.parametrize("change,message", [
    ({"passing": ParamPassing.CONST_REF}, "invalid storage duration fact"),
    ({"passing": ParamPassing.VALUE}, "invalid storage duration fact"),
    ({"readonly": True}, "invalid storage duration fact"),
    ({"storage_duration": None}, "unsupported record storage type or form"),
])
def test_owned_record_parameter_slot_negatives(program: _Program, change: dict, message: str) -> None:
    fn = _lowered(program, "keep")
    validate_function(fn)
    with pytest.raises(MIRValidationError, match=message):
        validate_function(_with_slot(fn, _handed(fn), **change))


def test_a_member_moves_from_the_parameter_storage(program: _Program) -> None:
    fn = _lowered(program, "Line.__init__")
    storage = _handed(fn)
    assert "initialize-receiver %0 (copy (*%1) may-raise, move %2)" in dump_function(fn)
    assert fn.receiver_init.fields[1].source == storage.id
    # The holder is a borrow: moving out of it would move the caller's storage.
    holder = fn.blocks[0].statements[0].target.root
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(_receiver_member(fn, 1, source=holder))


# --- callers' constructs -------------------------------------------------------------

def _constructs(fn: MIRFunction) -> list[MIRAssign]:
    return [s for b in fn.blocks for s in b.statements
            if isinstance(s, MIRAssign) and isinstance(s.value, MIRConstruct)]


def _outer(fn: MIRFunction, typ: NominalType) -> MIRAssign:
    stmt, = (s for s in _constructs(fn) if fn.slots[s.target.root.index].type == typ)
    return stmt


def _operands(fn: MIRFunction, stmt: MIRAssign) -> list[tuple[str, str]]:
    return [(fn.slots[o.index].value_kind.name, fn.slots[o.index].kind.name) for o in stmt.value.fields]


def test_a_lent_parameter_takes_a_holder(program: _Program) -> None:
    fn = _lowered(program, "lent_name")
    line = _outer(fn, _record(program, "Line"))
    # The named record is copied through its own holder; a construct handed
    # to the `Own[Point]` parameter is a temporary the member moves from.
    assert _operands(fn, line) == [("BORROWED", "PARAMETER"), ("OWNED", "TEMPORARY")]
    # Copying Point (a str field) may raise.
    assert line.value.may_raise


def test_a_temporary_bound_to_a_lent_parameter_is_borrowed(program: _Program) -> None:
    fn = _lowered(program, "lent_temporary")
    line = _outer(fn, _record(program, "Line"))
    # C++ binds the temporary to `const Point&` and the member copies it.
    assert _operands(fn, line) == [("BORROWED", "TEMPORARY"), ("OWNED", "TEMPORARY")]
    holder = line.value.fields[0]
    borrow, = (s for s in fn.blocks[1].statements if s.target.root == holder)
    assert isinstance(borrow.value, MIRBorrow)
    assert fn.slots[borrow.value.source.root.index].value_kind is MIRValueKind.OWNED
    copied = _lowered(program, "copied_argument")
    assert _operands(copied, _outer(copied, _record(program, "Line"))) == [
        ("BORROWED", "PARAMETER"), ("OWNED", "TEMPORARY")]


def test_a_composed_member_is_built_then_moved_in(program: _Program) -> None:
    fn = _lowered(program, "composed_callers")
    point, built = _record(program, "Point"), _record(program, "Built")
    member, literal = (s for s in _constructs(fn) if fn.slots[s.target.root.index].type == point)
    # The member's construct carries its copy; the outer construct only moves.
    assert member.value.may_raise and member.storage_write.mode is MIRRecordWriteMode.INITIALIZE_REGION
    outer = _outer(fn, built)
    assert outer.value.fields[0] == member.target.root and not outer.value.may_raise
    names = _outer(fn, _record(program, "Names"))
    # One lent operand read by both copies.
    assert names.value.fields[0] == names.value.fields[1]
    # A literal leg is a constant operand of the member's construct.
    constant = literal.value.fields[0]
    assert any(isinstance(s, MIRAssign) and s.target.root == constant and s.value == MIRConstant(3)
               for b in fn.blocks for s in b.statements)


def test_a_member_place_is_lent_through_a_holder(program: _Program) -> None:
    fn = _lowered(program, "member_place")
    line = _outer(fn, _record(program, "Line"))
    # `Line(ln.a, mk(x))`: a temporary holder borrowing the member place,
    # copied through by the member initializer.
    assert _operands(fn, line) == [("BORROWED", "TEMPORARY"), ("OWNED", "TEMPORARY")]
    borrow, = (s for b in fn.blocks for s in b.statements if s.target.root == line.value.fields[0])
    assert isinstance(borrow.value, MIRBorrow) and isinstance(borrow.value.source.projections[-1], MIRField)


@pytest.mark.parametrize("name,reason", [
    ("effectful", "effectful constructor argument"),
    # THIR spells the last-use move of a local with forms the record move refuses.
    ("moved_local", "move needs fixed movable owned local"),
    # A tuple member's construct is built outside a full expression.
    ("in_tuple", "temporary needs full-expression boundary"),
    # The member's storage runs a hook: no definition, no owned local.
    ("hooked_member", "member definition: custom record special member"),
])
def test_construct_refusals(program: _Program, name: str, reason: str) -> None:
    assert _refusal(program, name) == reason


def test_a_container_element_record_has_leaf_fields_only(program: _Program) -> None:
    assert _refusal(program, "listed") == "unsupported native container element"


def test_a_member_borrow_outliving_its_owner_is_a_scope_end(program: _Program) -> None:
    verdict = program.verdicts["escape_member"]
    assert verdict.function is not None, verdict.reason
    assert "scope_end" in verdict.describe()


def _with_construct(fn: MIRFunction, stmt: MIRAssign, value: MIRConstruct) -> MIRFunction:
    blocks = tuple(replace(b, statements=tuple(replace(s, value=value) if s is stmt else s for s in b.statements))
                   for b in fn.blocks)
    return replace(fn, blocks=blocks)


def test_construct_validator_negatives(program: _Program) -> None:
    fn = _lowered(program, "lent_name")
    line = _outer(fn, _record(program, "Line"))
    lent, handed = line.value.fields
    point = _record(program, "Point")
    bad = "incomplete or mistyped record construction"
    # Arity.
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_construct(fn, line, replace(line.value, fields=(lent,))))
    # The exit fact is the member copies'.
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_construct(fn, line, replace(line.value, may_raise=False)))
    # A borrowed operand of a noncopyable record.
    layouts = tuple(replace(r, copyable=False) if r.type == point else r for r in fn.records)
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(replace(fn, records=layouts))
    # Owned storage that is no temporary (the moved operand must be handed over).
    local = replace(fn.slots[handed.index], kind=MIRSlotKind.LOCAL)
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(replace(fn, slots=tuple(local if s.id == handed else s for s in fn.slots)))
    # The member layout must be present.
    with pytest.raises(MIRValidationError, match="record field needs its layout|record place needs layout"):
        validate_function(replace(fn, records=tuple(r for r in fn.records if r.type != point)))


def test_construct_record_operand_negatives(program: _Program) -> None:
    fn = _lowered(program, "lent_name")
    line_type, point = _record(program, "Line"), _record(program, "Point")
    line = _outer(fn, line_type)
    lent, handed = line.value.fields
    bad = "incomplete or mistyped record construction"
    # A lent operand of another record type.
    other, = (s.id for s in fn.slots if s.type == line_type and s.value_kind is MIRValueKind.BORROWED)
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_construct(fn, line, replace(line.value, fields=(other, handed))))
    # The handed-over temporary is moved from: never readonly, and movable.
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_slot(fn, fn.slots[handed.index], readonly=True))
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_layout(fn, point, movable=False))
    # The same rule holds for a construct written into a member (`f.line = Line(..)`).
    enclosing = _lowered(program, "replace_enclosing")
    write, = _member_writes(enclosing)
    frame = write.target.root
    with pytest.raises(MIRValidationError, match=bad):
        validate_function(_with_statement(enclosing, write, value=replace(
            write.value, fields=(frame, *write.value.fields[1:]))))


def test_three_construct_destinations_agree(program: _Program) -> None:
    # An owned local (record destination) takes the record-member operands;
    # a tuple member and a container element hold leaf-field records only,
    # so their constructs never reach the record-member rule.
    validate_function(_lowered(program, "lent_name"))
    assert _refusal(program, "in_tuple") == "temporary needs full-expression boundary"
    assert _refusal(program, "listed") == "unsupported native container element"


# --- whole-member writes -------------------------------------------------------------

_COVERED = (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED)


@pytest.mark.parametrize("name", [
    "replace_member", "sibling_survives", "name_then_replace", "member_copy", "implicit_copy", "called_member",
    "adopt", "keep_and_store", "copy_then_replace", "copy_does_not_replace_source", "copied_elements",
    "Line.reset", "Line.swap", "Line.adopt", "Line.take",
])
def test_member_writes_lower_without_conflict(program: _Program, name: str) -> None:
    verdict = program.verdicts[name]
    assert verdict.status in _COVERED and not verdict.conflicts, verdict.describe()


@pytest.mark.parametrize("name", [
    # `held = ln.a; ln.a = Point(..)`: held reads the replaced member.
    "replace_live",
    # `held = left.a; right.a = Point(..)`: the two parameters may alias.
    "alias_external",
    # `held = f.line; f.line = Line(..)`: the member itself is replaced.
    "replace_enclosing",
])
def test_a_member_write_reaches_a_live_borrow_of_it(program: _Program, name: str) -> None:
    verdict = program.verdicts[name]
    assert verdict.status in _COVERED and set(verdict.conflicts) == {"replacement"}, verdict.describe()


def _member_writes(fn: MIRFunction) -> list[MIRAssign]:
    return [s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and s.target.projections
            and isinstance(s.target.projections[-1], MIRField) and isinstance(s.storage_write, MIRRecordWrite)]


@pytest.mark.parametrize("name,kind", [
    ("replace_member", MIRConstruct),
    # A member read into the member: a copy of the member place.
    ("member_copy", MIRCopy),
    # A borrowed parameter read into the member: a copy through its holder.
    ("implicit_copy", MIRCopy),
    ("called_member", MIRCall),
    # An `Own[R]` parameter at its last use: moved out of its storage.
    ("adopt", MIRMove),
])
def test_a_member_write_is_an_in_place_replacement(program: _Program, name: str, kind: type) -> None:
    fn = _lowered(program, name)
    write, = _member_writes(fn)
    assert write.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE) and isinstance(write.value, kind)
    assert write in analyze_storage(fn).writes.values()
    if name == "member_copy":
        assert write.value.source.projections[-1].id.name == "b" and write.value.may_raise
    if name == "implicit_copy":
        assert write.value.source.projections == (MIRDeref(),)
    if name == "adopt":
        assert fn.slots[write.value.source.index].kind is MIRSlotKind.PARAMETER


def test_copies_out_of_and_into_members(program: _Program) -> None:
    fn = _lowered(program, "Line.swap")
    copies = [s.value for b in fn.blocks for s in b.statements
              if isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy)]
    # `t = copy(self.a)` and `self.a = copy(self.b)` copy member places;
    # `self.b = copy(t)` copies through t's holder. Point's str copy may raise.
    assert [c.source.projections[-1].id.name if isinstance(c.source.projections[-1], MIRField) else "deref"
            for c in copies] == ["a", "b", "deref"]
    assert all(c.may_raise for c in copies) and fn.exceptional_exits


def test_member_copies_as_elements(program: _Program) -> None:
    fn = _lowered(program, "copied_elements")
    statements = [s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign)]
    # `ps[0] = copy(ln.a)`: the element write copies the member place.
    element, = (s for s in statements if isinstance(s.value, MIRCopy))
    assert element.value.source.projections[-1].id.name == "a"
    # `[copy(ln.b), copy(ln.a)]`: each element is copied through a holder of its member.
    literal, = (s for s in statements if isinstance(s.value, MIRConstruct))
    borrows = {s.target.root: s.value.source.projections[-1].id.name for s in statements
               if isinstance(s.value, MIRBorrow) and s.value.source.projections}
    assert [borrows[o] for o in literal.value.fields] == ["b", "a"]


def test_a_scalar_member_write_is_no_replacement(program: _Program) -> None:
    # `self.a.x += 1` overwrites a scalar: no storage event at all.
    assert not analyze_storage(_lowered(program, "Line.bump")).writes
    line, point = _record(program, "Line"), _record(program, "Point")
    a, = (f for f in _definition(program, "Line").layout.fields if f.id.name == "a")
    x, = (f for f in _definition(program, "Point").layout.fields if f.id.name == "x")
    holder = MIRSlotId(MIRBodyId("main", "rows"), 0)
    assert storage_destination(MIRPlace(holder, (MIRDeref(), a)), {})
    assert not storage_destination(MIRPlace(holder, (MIRDeref(), a, x)), {})
    assert line != point


def _fields_of(program: _Program, record: str) -> dict[str, MIRField]:
    return {f.id.name: f for f in _definition(program, record).layout.fields}


@pytest.mark.parametrize("written,retained,expected", [
    # A member write reaches holders at or under the member ...
    (("ln", "a"), ("ln", "a"), True),
    (("ln", "a"), ("ln", "a", "name"), True),
    # ... not a sibling, nor the record around it, which keeps its identity.
    (("ln", "a"), ("ln", "b"), False),
    (("ln", "a"), ("ln",), False),
    # Two levels: replacing `f.line` reaches a holder of `f.line.a`; replacing
    # `f.line.a` spares `f.line` and `f.line.b`.
    (("f", "line"), ("f", "line", "a"), True),
    (("f", "line", "a"), ("f", "line"), False),
    (("f", "line", "a"), ("f", "line", "b"), False),
    # Distinct external origins may alias: any holder may lie inside.
    (("other", "a"), ("ln", "b"), True),
    (("other", "a"), ("ln",), True),
    # Distinct private roots never overlap; one root keeps its paths apart.
    (("ln!", "a"), ("other!", "a"), False),
    (("ln!", "a"), ("ln!", "b"), False),
    (("ln!", "a"), ("ln!", "a"), True),
])
def test_member_write_reach(program: _Program, written: tuple[str, ...], retained: tuple[str, ...],
                            expected: bool) -> None:
    line, frame, point = (_fields_of(program, name) for name in ("Line", "Frame", "Point"))
    roots = {"ln": 0, "other": 1, "f": 2}
    body = MIRBodyId("main", "rows")

    def referent(path: tuple[str, ...]) -> MIRReferent:
        root, *steps = path
        external = not root.endswith("!")
        fields, record = [], frame if root.startswith("f") else line
        for step in steps:
            fields.append(record[step])
            record = line if step == "line" else point
        return MIRReferent(MIRPlace(MIRSlotId(body, roots[root.rstrip("!")]), tuple(fields)), external)
    assert affects(referent(written), referent(retained), {}) is expected


# --- a record move empties its source ---------------------------------------------------

def _moved_with_member_holder(program: _Program, *, read_after: bool) -> MIRFunction:
    """`adopt` (`f.line = ln`, `ln: Own[Line]`) with a holder of `ln.a`
    borrowed before the move and read before or after it. THIR never
    spells this (sema keeps the assignment a copy while a borrow is live),
    so the MIR is extended by hand."""
    fn = _lowered(program, "adopt")
    move, = _member_writes(fn)
    storage = move.value.source
    a = _fields_of(program, "Line")["a"]
    x = _fields_of(program, "Point")["x"]
    region = fn.blocks[0].region
    held = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), a.type, MIRSlotKind.LOCAL, "held", form=th.Form.BORROW,
                   value_kind=MIRValueKind.BORROWED, residence=region)
    out = MIRSlot(MIRSlotId(fn.id, len(fn.slots) + 1), x.type, MIRSlotKind.TEMPORARY, residence=region)
    borrow = MIRAssign(MIRPlace(held.id), MIRBorrow(MIRPlace(storage, (a,))))
    read = MIRAssign(MIRPlace(out.id), MIRRead(MIRPlace(held.id, (MIRDeref(), x))))
    block = fn.blocks[0]
    index = block.statements.index(move)
    statements = (*block.statements[:index], borrow, *((move, read) if read_after else (read, move)),
                  *block.statements[index + 1:])
    return replace(fn, slots=(*fn.slots, held, out), blocks=(replace(block, statements=statements), *fn.blocks[1:]))


@pytest.mark.parametrize("read_after", [True, False])
def test_a_record_move_replaces_its_source(program: _Program, read_after: bool) -> None:
    fn = _moved_with_member_holder(program, read_after=read_after)
    validate_function(fn)
    events = analyze_storage(fn)
    move, = events.moves.values()
    assert fn.slots[move.root.index].kind is MIRSlotKind.PARAMETER and not move.projections
    live = analyze_liveness(fn)
    retention = analyze_retention(fn, live, analyze_dependencies(fn, live), events)
    held = next(s.id for s in fn.slots if s.name == "held")
    # Read after the move, `held` reads the moved-from member: a replacement
    # of the source storage; read before it, nothing retains the source.
    assert [(c.holder, c.affected.place) for c in retention.conflicts] == (
        [(MIRPlace(held), move)] if read_after else [])


def _with_statement(fn: MIRFunction, stmt: MIRAssign, **changes) -> MIRFunction:
    blocks = tuple(replace(b, statements=tuple(replace(s, **changes) if s is stmt else s for s in b.statements))
                   for b in fn.blocks)
    return replace(fn, blocks=blocks)


def _with_layout(fn: MIRFunction, typ: NominalType, **changes) -> MIRFunction:
    return replace(fn, records=tuple(replace(r, **changes) if r.type == typ else r for r in fn.records))


def test_member_write_validator_negatives(program: _Program) -> None:
    fn = _lowered(program, "replace_member")
    write, = _member_writes(fn)
    point, line = _record(program, "Point"), _record(program, "Line")
    fact = "record member write needs a replacement fact"
    # No fact: the write would be no storage event at all.
    with pytest.raises(MIRValidationError, match=fact):
        validate_function(_with_statement(fn, write, storage_write=None))
    with pytest.raises(MIRValidationError, match=fact):
        validate_function(_with_statement(fn, write, storage_write=MIRRecordWrite(
            MIRRecordWriteMode.INITIALIZE_REGION)))
    with pytest.raises(MIRValidationError, match=fact):
        validate_function(_with_statement(fn, write, storage_write=MIRRecordWrite(
            MIRRecordWriteMode.IN_PLACE, write.target.root)))
    # A readonly receiver, or a member declared `readonly[...]`.
    holder = fn.slots[write.target.root.index]
    with pytest.raises(MIRValidationError, match="store through readonly"):
        validate_function(replace(fn, slots=tuple(replace(s, readonly=True) if s.id == holder.id else s
                                                  for s in fn.slots)))
    a = write.target.projections[-1]
    fixed = replace(a, type=ReadonlyType(point))
    readonly_member = _with_statement(_with_layout(fn, line, fields=tuple(
        fixed if f == a else f for f in _definition(program, "Line").layout.fields)), write,
        target=replace(write.target, projections=(*write.target.projections[:-1], fixed)))
    with pytest.raises(MIRValidationError, match="store through readonly storage"):
        validate_function(readonly_member)
    # C++ move-assigns: a nonmovable member is never replaced.
    with pytest.raises(MIRValidationError, match="record member replacement needs movable record"):
        validate_function(_with_layout(fn, point, movable=False))
    # A source of another type, and a copy of a noncopyable member.
    copied = _lowered(program, "member_copy")
    copy_write, = _member_writes(copied)
    source = copy_write.value.source
    with pytest.raises(MIRValidationError, match="record copy source or eligibility"):
        validate_function(_with_statement(copied, copy_write, value=replace(
            copy_write.value, source=MIRPlace(source.root, (MIRDeref(),)))))
    with pytest.raises(MIRValidationError, match="record copy source or eligibility"):
        validate_function(_with_layout(copied, point, copyable=False))
    # The copy's exit fact is the layout's.
    with pytest.raises(MIRValidationError, match="record copy source or eligibility"):
        validate_function(_with_statement(copied, copy_write, value=replace(copy_write.value, may_raise=False)))
    # A global is read into a local first, never a member's operand.
    scalar = fn.slots[write.value.fields[0].index]
    assert scalar.value_kind is MIRValueKind.SCALAR
    global_slot = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), scalar.type, MIRSlotKind.GLOBAL, "g",
                          global_id=MIRGlobalId("main", "g"))
    with pytest.raises(MIRValidationError, match="global value needs explicit read"):
        validate_function(_with_statement(replace(fn, slots=(*fn.slots, global_slot)), write, value=replace(
            write.value, fields=(global_slot.id, *write.value.fields[1:]))))


def test_a_sliced_member_copy_refuses(program: _Program) -> None:
    # `h.b = copy(s)` with `s: Sub` and `b: Base`: the copy's source is
    # storage of another record type.
    assert _refusal(program, "sliced_copy") == "record source type mismatch"


# --- member results, receivers and arguments ------------------------------------------

def test_a_member_result_is_a_borrow_of_the_member_place(program: _Program) -> None:
    fn = _lowered(program, "Line.first")
    borrow, = (s for b in fn.blocks for s in b.statements
               if isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow))
    assert borrow.value.source.projections[-1].id.name == "a"
    returned, = (b.terminator.value for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    # The receiver is readonly, so the result holder is too.
    assert returned == borrow.target.root and fn.slots[returned.index].readonly


def _holders(fn: MIRFunction) -> dict[MIRSlotId, MIRPlace]:
    return {s.target.root: s.value.source for b in fn.blocks for s in b.statements
            if isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow)}


@pytest.mark.parametrize("name,expected", [
    # `ln.a.shift()`, `read_point(ln.b)`, `ln.a.get()`: each member lent
    # through a holder of its place, at the access the place has.
    ("member_arguments", [("a", False), ("b", False), ("a", False)]),
    ("readonly_member", [("a", True)]),
    # An inherited method binds at the member's own storage (`Sub`).
    ("via_member", [("s", False)]),
])
def test_a_member_is_lent_to_a_call_through_a_holder(program: _Program, name: str,
                                                     expected: list[tuple[str, bool]]) -> None:
    verdict = program.verdicts[name]
    assert verdict.status in _COVERED and not verdict.conflicts, verdict.describe()
    fn = verdict.function
    holders = _holders(fn)
    lent = [call.arguments[0] for b in fn.blocks for s in b.statements
            if (call := s.call if hasattr(s, "call") else s.value if isinstance(getattr(s, "value", None), MIRCall)
                else None) is not None]
    assert [(holders[h].projections[-1].id.name, fn.slots[h.index].readonly) for h in lent] == expected


def _callee(fn: th.THIRFunction, method: str) -> th.THIRResolvedCallee:
    callee, = {call.resolved_callee for call in _thir_nodes(fn)
               if isinstance(call, th.THIRMethodCall) and call.resolved_callee.identity.name == method}
    return callee


def _thir_nodes(node: object) -> list[th.THIRNode]:
    found = [node] if isinstance(node, th.THIRNode) else []
    for name in getattr(node, "__dataclass_fields__", ()):
        value = getattr(node, name)
        for child in value if isinstance(value, tuple) else (value,):
            if isinstance(child, (th.THIRNode, th.THIRStmt)):
                found.extend(_thir_nodes(child))
    return found


def _summary(callee: th.THIRResolvedCallee, *, writes=(), returns=()) -> MIRSummaryResult:
    """A KNOWN summary of a method whose own summary depends on nested
    record paths (their summaries are not published yet)."""
    bindings = []
    for typ, passing in zip(callee.signature.param_types, callee.signature.passings):
        bare = unwrap_readonly(unwrap_ref_type(typ))
        if record_type(bare):
            readonly = passing is ParamPassing.CONST_REF
            bindings.append(MIRParameterBinding(bare, passing, readonly, th.THIRBorrowedRecord(bare, readonly)))
        else:
            bindings.append(MIRParameterBinding(bare, passing, False))
    summary = MIRCallSummary(callee, tuple(bindings), frozenset(range(len(bindings))), frozenset(writes),
                             frozenset(), frozenset(returns), frozenset(), True)
    assert summary_problem(summary) is None, summary_problem(summary)
    return MIRSummaryResult(MIRSummaryState.KNOWN, summary)


def _path(program: _Program, *steps: tuple[str, str]) -> tuple[th.THIRFieldIdentity, ...]:
    return tuple(_field_identity(program, record, name) for record, name in steps)


def _field_identity(program: _Program, record: str, name: str) -> th.THIRFieldIdentity:
    field = _fields_of(program, record)[name]
    return th.THIRFieldIdentity(field.id.owner, field.id.name, field.type)


def _with_summaries(program: _Program, name: str, **paths) -> MIRFunction | MIRNotCovered:
    """Lower `name` with hand summaries: `first` returns `param0.a`,
    `reset` replaces `param0.a`, `bump` writes the scalar `param0.a.x`
    (or the paths given)."""
    fn = program.functions[name]
    defaults = {"first": ("returns", [("Line", "a")]), "reset": ("writes", [("Line", "a")]),
                "bump": ("writes", [("Line", "a"), ("Point", "x")])}
    summaries = {}
    for method, (kind, steps) in defaults.items():
        try:
            callee = _callee(fn, method)
        except ValueError:
            continue
        path = paths.get(method, _path(program, *steps))
        effect = (MIRReturnOrigin(0, path) if kind == "returns" else MIRParameterWrite(0, path))
        summaries[callee.identity] = _summary(callee, **{kind: (effect,)})
    return lower_function(fn, MIRBodyId("main", name), definitions=program.definitions, summaries=summaries)


def _conflicts(fn: MIRFunction) -> list[tuple[str, str]]:
    live = analyze_liveness(fn)
    retention = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn))
    return [(fn.slots[c.holder.root.index].name, _dump_place(c.retained.place)) for c in retention.conflicts]


def _dump_place(place: MIRPlace) -> str:
    return ".".join(p.id.name for p in place.projections if isinstance(p, MIRField))


@pytest.mark.parametrize("name,conflicts", [
    # `ln.bump()` writes the scalar `ln.a.x`: no replacement event.
    ("scalar_call", []),
    # `ln.reset(4)` replaces `ln.a`, which `held` borrows ...
    ("reset_live", [("held", "a")]),
    # ... but not `ln.b`.
    ("reset_sibling", []),
    # `held = f.line.first()` borrows `f.line.a`: replaced two levels down,
    # through the receiver's member place or by replacing `f.line` itself.
    ("member_receiver", [("held", "line.a")]),
    ("replace_ancestor", [("held", "line.a")]),
])
def test_call_summaries_reach_through_members(program: _Program, name: str,
                                              conflicts: list[tuple[str, str]]) -> None:
    fn = _with_summaries(program, name)
    assert isinstance(fn, MIRFunction), fn
    assert _conflicts(fn) == conflicts
    if name == "scalar_call":
        events = analyze_storage(fn)
        assert not events.call_writes and not events.writes


@pytest.mark.parametrize("path", [
    # A second hop keyed by an owner that is not the member's record.
    (("Line", "a"), ("Flat", "x")),
    # A second-hop field missing from the member's layout.
    (("Line", "a"), ("Point", "missing")),
])
def test_a_call_path_hop_reads_the_previous_members_layout(program: _Program,
                                                           path: tuple[tuple[str, str], ...]) -> None:
    steps = [_field_identity(program, *path[0])]
    record, name = path[1]
    steps.append(th.THIRFieldIdentity(_record(program, record), name, _fields_of(program, "Point")["x"].type)
                 if name == "missing" else _field_identity(program, record, name))
    result = _with_summaries(program, "scalar_call", bump=tuple(steps))
    assert isinstance(result, MIRNotCovered) and result.reason == "call write field does not match record layout"
    # The validator walks the same hops.
    fn = _with_summaries(program, "scalar_call")
    good, = fn.call_summaries
    bad = replace(good, writes=frozenset({MIRParameterWrite(0, tuple(steps))}))
    with pytest.raises(MIRValidationError, match="call write field does not match record layout"):
        validate_function(_with_call_summary(fn, good, bad))


def _with_call_summary(fn: MIRFunction, good: MIRCallSummary, bad: MIRCallSummary) -> MIRFunction:
    """`fn` with every call bound to `good` bound to `bad` instead."""
    def swap(s: object) -> object:
        if hasattr(s, "call") and s.call.summary is good:
            return replace(s, call=replace(s.call, summary=bad))
        if isinstance(getattr(s, "value", None), MIRCall) and s.value.summary is good:
            return replace(s, value=replace(s.value, summary=bad))
        return s
    return replace(fn, call_summaries=tuple(bad if s is good else s for s in fn.call_summaries),
                   blocks=tuple(replace(b, statements=tuple(swap(s) for s in b.statements)) for b in fn.blocks))


@pytest.mark.parametrize("path", [
    # A first hop missing from the layout of the record the receiver binds.
    (("Frame", "a"),),
    # A right first hop, then a field missing from the member's layout.
    (("Line", "a"), ("Point", "missing")),
])
def test_a_call_return_path_hop_reads_the_bound_records_layout(program: _Program,
                                                               path: tuple[tuple[str, str], ...]) -> None:
    # Every field is typed as the result, so the summary's own contract
    # (the endpoint) admits the path and only layout membership refuses it.
    point = _fields_of(program, "Line")["a"].type
    steps = tuple(th.THIRFieldIdentity(_record(program, record), name, point) for record, name in path)
    result = _with_summaries(program, "member_receiver", first=steps)
    assert isinstance(result, MIRNotCovered) and result.reason == "call write field does not match record layout"
    fn = _with_summaries(program, "member_receiver")
    good, = (s for s in fn.call_summaries if s.callee.identity.name == "first")
    bad = replace(good, returns=frozenset({MIRReturnOrigin(0, steps)}))
    with pytest.raises(MIRValidationError, match="call write field does not match record layout"):
        validate_function(_with_call_summary(fn, good, bad))

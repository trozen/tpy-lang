"""A native container's storage members come from its stub's declarations
(`scalar_leaves.declared_members`): a user `@native(..., elements=True)`
binding gets the MIR coverage a builtin has, and a dict view walks its own
declared member."""

import re
from dataclasses import replace

import pytest

from ..codegen_cpp.forms import loop_binding_kind
from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import INT32, STR, NominalType, ReadonlyType, unwrap_readonly, unwrap_ref_type
from .call_contract import MIRSummaryState
from .collect import MIRVerdictStatus, enumerate_bodies, line_facts
from .coverage import MIRUnsupported
from .nodes import MIRContainerLayout, MIRTupleElement, MIRValueKind
from .validate import MIRValidationError, expected_container_layout, validate_function

SOURCE = """\
from typing import Iterator
from tpy import int32, Own, NativeIterable, readonly, pure
from tpy.extern import native


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Same:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __eq__(self, other: Same) -> bool:
        return self.x == other.x


@native("my::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @native("at")
    @pure
    @readonly
    def __getitem__(self, i: int32) -> T: ...

    @native("put", mutates="elements")
    def __setitem__(self, i: int32, v: Own[T]) -> None: ...

    @native("put", mutates="elements")
    def put(self, i: int32, v: Own[T]) -> None: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...


# A subscript keyed by its only member: no value member for the key to reach.
@native("my::Index", elements=True)
class Index[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("at")
    @pure
    @readonly
    def __getitem__(self, key: T) -> T: ...


# An element owner whose iteration yields both members at once.
@native("my::Pairs", elements=True)
class Pairs[K, V](NativeIterable[tuple[K, V]]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[tuple[K, V]]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...


def first_x(r: Ring[Point]) -> int32:
    return r[0].x


def grow(r: Ring[int32]) -> None:
    r.push(1)  # structure


def store(r: Ring[int32]) -> None:
    r.put(0, 1)  # elements


def total(r: Ring[int32]) -> int32:
    t = 0
    for v in r:
        t += v
    return t


def total_x(r: Ring[Point]) -> int32:
    t = 0
    for p in r:
        t += p.x
    return t


def grow_in_loop(r: Ring[int32]) -> int32:
    t = 0
    for v in r:
        if v < 0:
            r.push(0)
        t += v
    return t


def put_in_loop(r: Ring[int32]) -> int32:
    t = 0
    for v in r:
        if v < 0:
            r.put(0, 0)
        t += v
    return t


def put_after_loop(r: Ring[int32]) -> int32:
    t = 0
    for v in r:
        t += v
    r.put(0, 5)
    return t


def values_total(d: dict[str, int32]) -> int32:
    t = 0
    for v in d.values():
        t += v
    return t


def keys_total(d: dict[str, str]) -> int32:
    n = 0
    for k in d.keys():
        n += len(k)
    for v in d.values():
        n += len(v)
    return n


def keys_param(ks: dict_keys[str, int32]) -> int32:
    n = 0
    for k in ks:
        n += len(k)
    return n


def items_len(d: dict[str, int32]) -> int32:
    return len(d.items())


def items_iter(d: dict[str, int32]) -> int32:
    n = 0
    for k, v in d.items():
        n += v
    return n


def keyed(ix: Index[Point], p: Point) -> int32:
    return ix[p].x


def table(d: dict[str, Point]) -> int32:
    return len(d)


def equal_elements(xs: list[Same]) -> int32:
    return len(xs)


def pair_count(ps: Pairs[str, int32]) -> int32:
    return len(ps)


def pair_walk(ps: Pairs[str, int32]) -> int32:
    n = 0
    for kv in ps:
        n += kv[1]
    return n


def pair_points(ps: Pairs[Point, int32]) -> int32:
    return len(ps)


def pack_bump(*items: Point) -> None:
    for p in items:
        p.x += 1


def pack_total(*xs: readonly[int32]) -> int32:
    t = 0
    for x in xs:
        t += x
    return t


def pack_read(*items: Point) -> int32:
    t = 0
    for p in items:
        t += p.x
    return t
"""


def _line(marker: str) -> int:
    return next(i for i, text in enumerate(SOURCE.splitlines(), 1) if marker in text)


def _program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as analysis:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, analysis.definitions,
                                    compiler.thir_reject_by_node, analysis.workspace)
        definitions = analysis.definitions
    thir = {fn.name: fn for fn in ctx.thir_functions.values()}
    return compiler, {v.name: v for v in verdicts}, thir, definitions


@pytest.fixture(scope="module")
def program():
    return _program()


# The per-test reset clears the type facts a compilation latched, so a test
# that reads them compiles its own program.
@pytest.fixture
def active():
    program = _program()
    with activate_compiler(program[0]):
        yield program


def _param(program, fn: str, name: str = "") -> th.THIRParam:
    params = program[2][fn].params
    return next(p for p in params if p.name == name) if name else params[0]


def _bare(typ):
    return unwrap_readonly(unwrap_ref_type(typ))


def _nominal(qname: str, *args) -> NominalType:
    return NominalType(qname.rsplit(".", 1)[-1], tuple(args), _module_qname=qname)


def _covered(verdict) -> bool:
    return verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED) and not verdict.conflicts


# --- verdicts ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["first_x", "grow", "store", "total", "total_x", "put_after_loop",
                                  "values_total", "keys_total", "keys_param", "items_len", "pair_count",
                                  # A varargs parameter is a view holder of its caller's elements.
                                  "pack_bump", "pack_total"])
def test_bodies_over_declared_members_are_covered(program, name) -> None:
    verdict = program[1][name]
    assert _covered(verdict), verdict.describe()


@pytest.mark.parametrize("name", ["grow_in_loop", "put_in_loop"])
def test_a_live_cursor_conflicts_with_any_write_of_its_container(program, name) -> None:
    # A structure write, and an element write by the index-blind rule.
    verdict = program[1][name]
    assert verdict.function is not None and any("replacement" in c for c in verdict.conflicts), verdict.describe()


@pytest.mark.parametrize("name, region", [("grow", "r[structure]"), ("store", "r[elements]")])
def test_a_stub_method_writes_the_region_its_declaration_names(program, name, region) -> None:
    verdict = program[1][name]
    assert verdict.summary is not None and verdict.summary.state is MIRSummaryState.KNOWN
    facts = line_facts(verdict)
    line = _line("# structure" if region.endswith("[structure]") else "# elements")
    assert facts.events_gap is None and facts.events.get((line, region)), facts.events


@pytest.mark.parametrize("name, reason", [
    # An items view binds no single element: THIR attaches no iteration fact.
    ("items_iter", "missing or invalid native iteration facts"),
    ("pair_walk", "missing or invalid native iteration facts"),
    # A subscript keyed by the only member reaches no value member.
    ("keyed", "unsupported keyed container"),
    # A type with a value member holds leaves only; an element running user code is out.
    ("table", "unsupported native container element"),
    ("equal_elements", "unsupported native container element"),
    # Sema infers a read-only pack `varargs[readonly[Point]]` and types its
    # name `varargs[Point]`: the two types of one slot disagree.
    ("pack_read", "container name type mismatch"),
])
def test_refusals(program, name, reason) -> None:
    verdict = program[1][name]
    assert (verdict.status, verdict.reason) == (MIRVerdictStatus.UNCOVERED, reason)


# --- layouts -----------------------------------------------------------------------

def _layout(program, typ) -> MIRContainerLayout:
    return program[3].container(None, _bare(typ)).layout


def test_layouts_are_the_declared_members_at_the_type_arguments(active) -> None:
    point = _bare(_param(active, "keyed", "p").type)
    owned_str = MIRTupleElement(STR, MIRValueKind.OWNED, False)
    assert _layout(active, _param(active, "first_x").type) == MIRContainerLayout(
        MIRTupleElement(point, MIRValueKind.BORROWED, False))
    # Views: keys walk the key, values the value, an items view keeps both.
    assert _layout(active, _param(active, "keys_param").type) == MIRContainerLayout(owned_str)
    assert _layout(active, _nominal("builtins.dict_values", STR, INT32)) == MIRContainerLayout(MIRTupleElement(INT32))
    assert _layout(active, _nominal("builtins.dict_values", STR, STR)) == MIRContainerLayout(owned_str)
    assert _layout(active, _nominal("builtins.dict_items", STR, INT32)) == MIRContainerLayout(
        owned_str, MIRTupleElement(INT32))
    # Two members of one type are two positions.
    assert _layout(active, _nominal("builtins.dict", STR, STR)) == MIRContainerLayout(owned_str, owned_str)
    assert _layout(active, _nominal("tpy.varargs", INT32)) == MIRContainerLayout(MIRTupleElement(INT32))
    # A keyed subscript is refused where it is read, not where the layout is built.
    assert _layout(active, _param(active, "keyed").type) == MIRContainerLayout(
        MIRTupleElement(point, MIRValueKind.BORROWED, False))


def test_layout_refusals(active) -> None:
    definitions = active[3]
    point = _bare(_param(active, "keyed", "p").type)
    for typ, reason in ((_nominal("builtins.dict", STR, point), "unsupported native container element"),
                        (_bare(_param(active, "pair_points").type), "unsupported native container element"),
                        (_nominal("tpy.SpanIter", INT32), "unsupported native container type"),
                        (_nominal("builtins.list", _nominal("builtins.list", INT32)),
                         "unsupported native container element")):
        with pytest.raises(MIRUnsupported, match=reason):
            definitions.container(None, typ)
    # The validator re-derives the same refusal: no layout for a record value member.
    records = {point: definitions.get(None, point).layout}
    assert expected_container_layout(_nominal("builtins.dict", STR, point), False, records) is None
    assert expected_container_layout(_nominal("builtins.list", point), False, records) == MIRContainerLayout(
        MIRTupleElement(point, MIRValueKind.BORROWED, False))


# --- THIR validator ----------------------------------------------------------------

def _thir_rejects(fn: th.THIRFunction, message: str) -> None:
    with pytest.raises(THIRValidationError, match=message):
        validate_thir(fn)


def _loop(fn: th.THIRFunction) -> th.THIRForEach:
    return next(s for s in fn.body if isinstance(s, th.THIRForEach))


def _with_loop(fn: th.THIRFunction, loop: th.THIRForEach) -> th.THIRFunction:
    return replace(fn, body=tuple(loop if isinstance(s, th.THIRForEach) else s for s in fn.body))


def test_thir_publishes_a_view_fact_for_its_own_declared_element(active) -> None:
    fn = active[2]["values_total"]
    validate_thir(fn)
    source = _loop(fn).iteration.source
    assert source.type.qualified_name() == "builtins.dict_values" and source.element == INT32
    keys = _param(active, "keys_param")
    assert keys.native_container == th.THIRNativeContainer(_bare(keys.type), STR, keys.native_container.readonly)
    # Neither an items view (no single element) nor a record value member gets a fact.
    assert _param(active, "pair_points").native_container is None and _param(active, "table").native_container is None


def test_thir_validator_rejects_a_view_fact_that_binds_no_element(active) -> None:
    fn = active[2]["keys_param"]
    param = _param(active, "keys_param")
    items = _nominal("builtins.dict_items", STR, INT32)
    damaged = replace(param, type=items, native_container=th.THIRNativeContainer(items, STR, True))
    _thir_rejects(replace(fn, params=(damaged,)), "invalid native container fact")


def test_thir_validator_rejects_a_record_element_beside_a_value_member(active) -> None:
    fn = active[2]["pair_points"]
    param = _param(active, "pair_points")
    typ = _bare(param.type)
    point = typ.type_args[0]
    damaged = replace(param, native_container=th.THIRNativeContainer(typ, th.THIRBorrowedRecord(point, True), True))
    _thir_rejects(replace(fn, params=(damaged,)), "invalid native element fact")


def test_thir_validator_rejects_a_stub_result_loop_over_no_view(active) -> None:
    fn = active[2]["values_total"]
    loop = _loop(fn)
    lst = _nominal("builtins.list", INT32)
    iterable = replace(loop.iterable, result_type=lst)
    iteration = replace(loop.iteration, source=th.THIRNativeContainer(lst, INT32, loop.iteration.source.readonly))
    _thir_rejects(_with_loop(fn, replace(loop, iterable=iterable, iteration=iteration)),
                  "native iteration fact disagrees with emitted binding")


def test_thir_validator_rejects_a_cursor_over_an_owner_that_binds_none(active) -> None:
    fn = active[2]["pair_walk"]
    loop = _loop(fn)
    assert loop.iteration is None
    typ = _bare(loop.iterable.result_type)
    readonly = _param(active, "pair_walk").native_container.readonly
    iteration = th.THIRNativeIteration(th.THIRNativeContainer(typ, STR, readonly), loop_binding_kind(
        loop.elem_type, loop.const_loop_var, hoisted=loop.hoist_loop_var))
    _thir_rejects(_with_loop(fn, replace(loop, iteration=iteration)), "native iteration source binds no element")


# --- MIR validator -----------------------------------------------------------------

def _mir_rejects(fn, message: str) -> None:
    with pytest.raises(MIRValidationError, match=re.escape(message)):
        validate_function(fn)


def test_mir_validator_rejects_a_cursor_over_a_view_that_binds_none(active) -> None:
    fn = active[1]["values_total"].function
    validate_function(fn)
    cursor = next(s for s in fn.slots if s.value_kind is MIRValueKind.NATIVE_ITERATOR)
    items = _nominal("builtins.dict_items", STR, INT32)
    layout = MIRContainerLayout(MIRTupleElement(STR, MIRValueKind.OWNED, cursor.readonly), MIRTupleElement(INT32))
    # The layout is the items view's own: only the missing cursor is wrong.
    assert expected_container_layout(items, cursor.readonly, {r.type: r for r in fn.records}) == layout
    damaged = replace(fn, slots=tuple(replace(s, type=items, container_layout=layout) if s is cursor else s
                                      for s in fn.slots))
    _mir_rejects(damaged, "unsupported native element")


def test_mir_validator_reads_a_view_holder_access_from_its_own_element(active) -> None:
    fn = active[1]["keys_param"].function
    validate_function(fn)
    holder = next(s for s in fn.slots if s.name == "ks")
    assert not holder.readonly
    # A values view whose element argument (its second) is readonly lends readonly access only.
    readonly_values = _nominal("builtins.dict_values", STR, ReadonlyType(INT32))
    layout = MIRContainerLayout(MIRTupleElement(INT32))
    assert expected_container_layout(readonly_values, False, {}) == layout
    damaged = replace(fn, slots=tuple(replace(s, type=readonly_values, container_layout=layout) if s is holder else s
                                      for s in fn.slots))
    _mir_rejects(damaged, "invalid container view holder")

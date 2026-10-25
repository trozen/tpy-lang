"""Native containers as MIR places: owned container storage, borrowed
containers and Span holders reach one `[elements]` region. Element reads,
borrows and copies, unstepped slices, in-place element writes, literals,
container fields and container results lower against the layout the type
derives; the validator re-derives it and rejects every damaged shape."""

import re
from dataclasses import dataclass, fields, is_dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import analyze_call_workspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import (
    BOOL, INT32, STR, NominalType, OwnType, ReadonlyType, TpyType, VoidType, make_list, make_span,
    return_representation, unwrap_readonly, unwrap_ref_type,
)
from .call_contract import MIRCallSummary
from ..thir.scalar_leaves import MIRClass, declared_members, native_container_type
from .collect import call_definitions, dump_codegen_mir
from .definitions import MIRDefinitions
from .dump import dump_function
from .liveness import MIRPoint
from .lower import lower_constructor, lower_function
from .retention import analyze_retention
from .storage import analyze_storage
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBorrow, MIRCall, MIRConstant, MIRConstruct, MIRCopy, MIRFunction,
    MIRIteratorInit, MIRIteratorRead, MIRMemberInit, MIRMemberInits, MIRMove, MIRNotCovered, MIRPlace, MIRRead,
    MIRStorageWrite, MIRStorageWriteMode, MIRReturn, MIRSlot, MIRSlotKind, MIRStorageDuration,
)
from .shapes import MIRContainerElements, MIRDeref, object_prefix
from .value import MIRHolding, MIRValidationError, container_members, with_access
from .testutil import (
    compiled_definitions, ContainerValue, bind_parameters, Reference, bound, execute, leaf_shape, owned_shape,
    Redeclared, repass, reslot, prepared, workspace_functions,
)
from .prepare import validate_function

SOURCE = """\
from tpy import int32, Span, Array, readonly, StrView, Own


class Point:
    def __init__(self, x: int32) -> None:
        self.x = x


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def head(self) -> int32:
        return self.items[0]

    def sum_items(self) -> int32:
        t = 0
        for v in self.items:
            t += v
        return t

    def alias_push(self, v: int32) -> None:
        x = self.items
        x.append(v)


class Pair:
    xs: list[int32]

    def __init__(self, a: int32) -> None:
        self.xs = [a, a]


def first(ps: list[Point]) -> int32:
    p = ps[0]
    return p.x


def at(xs: list[int32], i: int32) -> int32:
    t = 0
    t += xs[i]
    return t


def field_of(ps: list[Point]) -> int32:
    return ps[1].x


def own() -> int32:
    xs = [1, 2, 3]
    s = xs[1:3]
    return s[0] + len(xs)


def set_scalar(xs: list[int32]) -> int32:
    xs[0] = 5
    return xs[0]


def set_under_iterator(xs: list[int32]) -> int32:
    t = 0
    for x in xs:
        xs[0] = 1
        t += x
    return t


def span_set(xs: list[int32]) -> int32:
    s = xs[0:2]
    s[0] = 1
    return s[0]


def array_set(a: Array[int32, 3]) -> int32:
    a[1] = 9
    return a[1]


def replace_after_use(ps: list[Point]) -> int32:
    p = ps[0]
    x = p.x
    ps[0] = Point(3)
    return x


def tail(xs: list[int32]) -> Span[readonly[int32]]:
    return xs[1:]


def span_first(s: Span[int32]) -> int32:
    return s[0]


def span_sum(s: Span[int32]) -> int32:
    t = 0
    for v in s:
        t += v
    return t


def strs(ns: list[str]) -> int32:
    v: StrView = ns[0]
    return len(v)


def str_copy(ns: list[str]) -> str:
    return ns[0]


def walk_strs(ns: list[str]) -> int32:
    n = 0
    for s in ns:
        n += len(s)
    return n


def items_of(b: Bag) -> list[int32]:
    return b.items


def make(n: int32) -> Own[list[int32]]:
    xs = [n, n]
    return xs


def own_param(xs: Own[list[int32]]) -> int32:
    return len(xs)


def points(a: int32) -> int32:
    ps = [Point(a), Point(2)]
    return ps[0].x


def holds(vs: list[StrView]) -> int32:
    return len(vs)


def nested(xss: list[list[int32]]) -> int32:
    return xss[0][0]


def grow(xs: list[int32], v: int32) -> None:
    xs.append(v)


def build() -> int32:
    b = Bag()
    return 0


def keys_len(d: dict[str, int32]) -> int:
    n = 0
    for k in d.keys():
        n += len(k)
    return n


def values_total(d: dict[str, int32]) -> int32:
    n = 0
    for v in d.values():
        n += v
    return n


def values_len(d: dict[str, int32]) -> int:
    return len(d.values())


def main() -> None:
    b = Bag()
    print(build(), len(b.items))


main()
"""


@dataclass(frozen=True)
class _Unit:
    compiler: object
    modules: list
    functions: dict
    constructors: dict
    definitions: MIRDefinitions


def _unit() -> _Unit:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    constructors = {c.record_name: c for c in ctx.thir_constructors.values()}
    return _Unit(compiler, modules, functions, constructors,
                 compiled_definitions(compiler, tuple(constructors.values())))


@pytest.fixture(scope="module")
def unit():
    return _unit()


@pytest.fixture
def fresh():
    # The builtin stubs' records (a dict view's declared iteration) live on
    # the static TypeDefs only for the compilation that attached them.
    unit = _unit()
    with activate_compiler(unit.compiler):
        yield unit


@pytest.fixture
def active(unit):
    # The per-test state reset clears the stub facts latched onto the static
    # TypeDefs (`borrowing_view`, which marks a Span a view); latch them again.
    for module in unit.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(unit.compiler):
        yield unit


# --- the facts THIR publishes for containers, reproduced over this source ---------

def _bare(typ: TpyType) -> TpyType:
    return unwrap_readonly(unwrap_ref_type(typ))


def _element_fact(typ: NominalType, readonly: bool) -> TpyType | th.THIRBorrowedRecord:
    element = _bare(declared_members(typ)[0])
    return element if element.name in ("int32", "str", "bool") else th.THIRBorrowedRecord(element, readonly)


def _setitem_stub(receiver: NominalType, preserves: bool = True) -> th.THIRStubCallee:
    # The real stub takes the value as `Own[T]`.
    element = OwnType(declared_members(receiver)[0])
    params = (receiver, INT32, element)
    signature = th.THIRCallableSignature(params, VoidType(), None,
                                         (ParamPassing.MUT_REF, ParamPassing.VALUE, ParamPassing.OWN),
                                         return_representation(VoidType()))
    return th.THIRStubCallee(th.THIRStubIdentity(f"{receiver.qualified_name()}.__setitem__", params), signature,
                             None, (False, True, False), mutates_elements=preserves, receiver=True)


def _rewrite(node, change):
    """`node` with `change` applied bottom-up to every THIR node under it."""
    if isinstance(node, tuple):
        new = tuple(_rewrite(n, change) for n in node)
        return node if all(a is b for a, b in zip(new, node)) else new
    if not (is_dataclass(node) and not isinstance(node, type) and type(node).__module__ == th.__name__):
        return node
    updates = {}
    for f in fields(node):
        if not f.init:
            continue
        value = getattr(node, f.name)
        new = _rewrite(value, change)
        if new is not value:
            updates[f.name] = new
    return change(replace(node, **updates) if updates else node)


def _published(fn: th.THIRFunction, *, readonly_iteration: bool = False, preserves: bool | None = None,
               setitem: bool = True) -> th.THIRFunction:
    """`fn` with the container facts the THIR producer attaches: owned
    container locals, container field identities, iteration over any
    container place, element-write stubs, container parameters' facts."""
    def change(node):
        match node:
            case th.THIRVarDecl(native_container=None, init=th.THIRContainerLiteral()) if (
                    native_container_type(_bare(node.resolved_type))):
                typ = _bare(node.resolved_type)
                return replace(node, native_container=th.THIRNativeContainer(typ, _element_fact(typ, False), False))
            case th.THIRFieldAccess(field_identity=None) if (native_container_type(_bare(node.result_type))
                                                             or isinstance(node.receiver, th.THIRSubscript)):
                # A container field, or a field of a container's record element.
                owner = _bare(node.receiver.result_type)
                identity = th.THIRFieldIdentity(owner, node.field_cpp, _bare(node.result_type))
                return replace(node, field_identity=identity)
            case th.THIRForEach(iteration=None):
                typ = _bare(node.iterable.result_type)
                source = th.THIRNativeContainer(typ, _element_fact(typ, readonly_iteration), readonly_iteration)
                return replace(node, iteration=th.THIRNativeIteration(source))
            case th.THIRSetItem():
                if not setitem:
                    return replace(node, stub_callee=None)
                if node.stub_callee is not None:
                    # The fact THIR published (the real stub's declaration), with
                    # only the element-write bit toggled when asked.
                    stub = node.stub_callee if preserves is None else replace(node.stub_callee, mutates_elements=preserves)
                    return replace(node, stub_callee=stub)
                return replace(node, stub_callee=_setitem_stub(_bare(node.target.receiver.result_type), bool(preserves)))
        return node
    params = tuple(replace(p, native_container=th.THIRNativeContainer(
        _bare(p.type), _element_fact(_bare(p.type), p.passing is ParamPassing.CONST_REF),
        p.passing is ParamPassing.CONST_REF))
        if p.native_container is None and native_container_type(_bare(p.type)) and _bare(p.type).type_args
        and _bare(declared_members(_bare(p.type))[0]).name == "str" else p for p in fn.params)
    return replace(fn, params=params, body=_rewrite(fn.body, change))


def _lower(unit: _Unit, name: str, **facts) -> MIRFunction | MIRNotCovered:
    fn = unit.functions[name]
    return lower_function(_published(fn, **facts), MIRBodyId("containers", name), definitions=unit.definitions)


def _fn(unit: _Unit, name: str, **facts) -> MIRFunction:
    fn = _lower(unit, name, **facts)
    assert isinstance(fn, MIRFunction), fn
    return fn


def _lines(fn: MIRFunction) -> list[str]:
    return [re.sub(r" @ \d+:\d+$", "", re.sub(r" residence=r\d+| duration=\w+", "", line.strip()))
            for line in dump_function(fn).splitlines()]


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _storage(fn: MIRFunction, name: str) -> MIRSlot:
    """The storage an owning local holds through its holder: the backing
    its declaration borrows."""
    holder = _slot(fn, name).id
    backing, = {s.value.source.root for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and s.target == MIRPlace(holder) and isinstance(s.value, MIRBorrow)
                and not s.value.source.projections}
    return fn.slots[backing.index]


def _replace_first(fn: MIRFunction, match, change) -> MIRFunction:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements) if match(s))
    stmt = change(block.statements[index])
    return replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], stmt, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))


def _reslot(fn: MIRFunction, name: str, **changes) -> MIRFunction:
    return replace(fn, slots=tuple(reslot(s, **changes) if s.name == name else s for s in fn.slots))


def _rejects(fn: MIRFunction, message: str) -> None:
    with pytest.raises(MIRValidationError, match=re.escape(message)):
        validate_function(fn)


def _assign(kind):
    return lambda s: isinstance(s, MIRAssign) and isinstance(s.value, kind)


def _elements(place: MIRPlace) -> bool:
    return bool(place.projections) and isinstance(place.projections[-1], MIRContainerElements)


# --- element places ------------------------------------------------------------------

def test_a_record_element_is_borrowed_from_the_region_after_its_index(active) -> None:
    fn = _fn(active, "first")
    lines = _lines(fn)
    assert "%0: list[Point] readonly-ref element=Point:record:readonly parameter ps" in lines
    assert lines.index("%1 = 0") < lines.index("%2 = borrow (*%0)[elements] may-raise")
    assert any(line.startswith("%2: Point readonly-ref local p") for line in lines)
    cell = next(r.fields[0].id for r in fn.records if r.type.name == "Point")
    heap = {1: {cell: 3}, 2: {cell: 5}}
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), heap=heap) == 3


def test_a_scalar_element_is_read_after_its_index(active) -> None:
    fn = _fn(active, "at")
    lines = _lines(fn)
    assert lines.index("%5 = read %1") < lines.index("%6 = read (*%0)[elements] may-raise")
    assert fn.exceptional_exits
    assert execute(fn, ContainerValue((3, 5)), 1) == 5


def test_a_field_of_an_element_is_read_in_place(active) -> None:
    fn = _fn(active, "field_of")
    assert "%2 = read (*%0)[elements].__main__.Point::x may-raise" in _lines(fn)
    cell = next(r.fields[0].id for r in fn.records if r.type.name == "Point")
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), heap={1: {cell: 3}, 2: {cell: 5}}) == 5


def test_owned_and_viewed_elements_of_str(active) -> None:
    # A view holder borrows the element (spelled StrView: sema's one view rule
    # owns an inferred element read); an owning sink copies it.
    strs = _lines(_fn(active, "strs"))
    assert any(line.startswith("%2: StrView readonly-ref local v") for line in strs)
    assert "%2 = borrow (*%0)[elements] may-raise" in strs
    copy = _lines(_fn(active, "str_copy"))
    assert any(re.fullmatch(r"%\d+ = copy \(\*%0\)\[elements\] may-raise \[initialize\]", line) for line in copy)
    # Iteration reads an owned-leaf element through a view of it.
    walk = _fn(active, "walk_strs", readonly_iteration=True)
    s = _slot(walk, "s")
    assert (s.type.name, s.shape.holding, s.readonly) == ("StrView", MIRHolding.POINTER, True)
    assert any(isinstance(st, MIRAssign) and isinstance(st.value, MIRIteratorRead) and st.target.root == s.id
               for b in walk.blocks for st in b.statements)


# --- owned containers, literals, Span -------------------------------------------------

def test_an_owned_container_local_is_built_from_its_literal(active) -> None:
    lines = _lines(_fn(active, "own"))
    # The local holds its own storage through a holder, as an owned record local does.
    assert "%3: Array[int32, 3] owned-storage element=int32:leaf temporary" in lines
    assert "%4: Array[int32, 3] mutable-ref element=int32:leaf local xs" in lines
    assert lines.index("%3 = construct (%0, %1, %2) may-raise [initialize]") < lines.index("%4 = borrow %3")
    # An unstepped slice reads its bounds, then borrows the whole region.
    assert "%7: Span[int32] mutable-ref element=int32:leaf local s" in lines
    assert lines.index("%6 = 3") < lines.index("%7 = borrow (*%4)[elements]")
    assert "%9 = read %7[elements] may-raise" in lines
    # `len` reads the container whole.
    assert any(re.fullmatch(r"%10 = call stub tpy\._builtins\._funcs\.len\[Sized\]\(%4\) .*", line) for line in lines)


def test_a_literal_of_records_moves_built_temporaries(active) -> None:
    fn = _fn(active, "points")
    ps = _storage(fn, "ps").id
    construct = next(s for b in fn.blocks for s in b.statements
                     if isinstance(s, MIRAssign) and isinstance(s.value, MIRConstruct) and s.target.root == ps)
    for operand in construct.value.fields:
        slot = fn.slots[operand.index]
        assert slot.shape.is_storage and slot.type.name == "Point"
    assert construct.value.may_raise


def test_an_owned_container_result_returns_its_storage(active) -> None:
    fn = _fn(active, "make")
    lines = _lines(fn)
    assert "%4: list[int32] mutable-ref element=int32:leaf local xs" in lines
    # The local's own storage leaves the body.
    assert fn.blocks[-1].terminator == MIRReturn(_storage(fn, "xs").id, fn.blocks[-1].terminator.loc)
    assert execute(fn, 4) is not None


def test_an_owned_container_parameter_is_the_body_storage(active) -> None:
    # The parameter's name keeps THIR's `Own[...]` wrapper and reads in storage
    # form; the fact and the slot are the bare container it owns, reached
    # through a holder borrowed at entry, as an `Own[R]` record parameter is.
    lines = _lines(_fn(active, "own_param"))
    assert "%0: list[int32] owned-storage element=int32:leaf parameter xs" in lines
    assert lines.index("%1 = borrow %0") < next(
        i for i, line in enumerate(lines) if line.startswith("%2 = call stub tpy._builtins._funcs.len[Sized](%1)"))


def test_a_span_parameter_and_result_view_a_region(active) -> None:
    tail = _fn(active, "tail")
    lines = _lines(tail)
    assert "result borrowed readonly" in lines
    assert "%2: Span[readonly[int32]] readonly-ref element=int32:leaf:readonly temporary" in lines
    assert "%2 = borrow (*%0)[elements]" in lines
    first = _lines(_fn(active, "span_first"))
    assert "%0: Span[int32] mutable-ref element=int32:leaf parameter s" in first
    assert "%2 = read %0[elements] may-raise" in first
    span_sum = _fn(active, "span_sum")
    assert "%3: Span[int32] native-iterator mutable element=int32:leaf temporary" in _lines(span_sum)
    assert execute(span_sum, ContainerValue((1, 2, 3))) == 6


# --- element writes ------------------------------------------------------------------

def test_an_element_write_replaces_the_region_in_place(active) -> None:
    fn = _fn(active, "set_scalar")
    assert "(*%0)[elements] = read %1 [in_place]" in _lines(fn)
    write = next(s for b in fn.blocks for s in b.statements if _elements(s.target))
    assert write.storage_write == MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, _slot(fn, "xs").id)
    values = ContainerValue((1, 2))
    assert execute(fn, values) == 5 and values.elements == [5, 2]
    record = _fn(active, "replace_after_use")
    assert "(*%0)[elements] = construct (%5) [in_place]" in _lines(record)
    cell = next(r.fields[0].id for r in record.records if r.type.name == "Point")
    heap = {1: {cell: 1}}
    assert execute(record, ContainerValue((Reference(1),)), heap=heap) == 1 and heap[1][cell] == 3


def test_a_scalar_element_write_under_a_live_iterator_is_a_replacement(active) -> None:
    # `xs[0] = 1` in `for x in xs`: the storage-destination write is a
    # retention event whatever the element type, and the cursor retains the region.
    fn = _fn(active, "set_under_iterator")
    point, write = next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                        if _elements(s.target))
    assert write.storage_write == MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, _slot(fn, "xs").id)
    events = analyze_storage(prepared(fn))
    assert point in events.writes
    retention = analyze_retention(prepared(fn))
    assert [c.point for c in retention.conflicts] == [point]
    assert retention.conflicts[0].affected.place == MIRPlace(_slot(fn, "xs").id, (MIRContainerElements(),))


def test_an_element_write_without_a_reference_preserving_stub_is_the_call(active) -> None:
    assert _lower(active, "set_scalar", setitem=False).reason == "element write needs a stub contract"
    # A `__setitem__` that may move elements is the stub call: a structure write.
    moving = _lower(active, "set_scalar", preserves=False)
    assert isinstance(moving, MIRFunction)
    assert any(line.startswith("call stub builtins.list.__setitem__[list[int32], int32, Own[int32]](%0, ")
               and "[writes={param0[structure]}, may-raise]" in line for line in _lines(moving))
    assert not any("[elements] = " in line for line in _lines(moving))
    # A view cannot change its source's shape, whatever its stub declares.
    assert isinstance(_lower(active, "span_set", preserves=False), MIRFunction)


def test_an_element_write_through_a_span_or_on_an_array(active) -> None:
    lines = _lines(_fn(active, "span_set"))
    assert "%3 = borrow (*%0)[elements]" in lines and "%3[elements] = read %4 [in_place]" in lines
    # The Array stub does not declare that it preserves references: the stub call, a structure write.
    array = _lines(_fn(active, "array_set"))
    assert any(line.startswith("call stub tpy.Array.__setitem__[Array[int32, 3], int32, Own[int32]](%0, ")
               and "[writes={param0[structure]}, may-raise]" in line for line in array)


# --- container fields ----------------------------------------------------------------

def test_a_container_field_is_a_place_of_its_record(active) -> None:
    lines = _lines(_fn(active, "head"))
    assert "%2 = read (*%0).__main__.Bag::items[elements] may-raise" in lines
    lines = _lines(_fn(active, "sum_items", readonly_iteration=True))
    assert any(re.fullmatch(r"%3 = borrow \(\*%0\)\.__main__\.Bag::items", line) for line in lines)
    assert "%4 = iterator-init %3" in lines
    items = _fn(active, "items_of")
    lines = _lines(items)
    assert "result borrowed mutable" in lines
    assert "%1: list[int32] mutable-ref element=int32:leaf temporary" in lines
    assert "%1 = borrow (*%0).__main__.Bag::items" in lines


def test_a_local_alias_of_a_container_field_borrows_the_field(active) -> None:
    lines = _lines(_fn(active, "alias_push"))
    assert "%2: list[int32] mutable-ref element=int32:leaf local x" in lines
    assert "%2 = borrow (*%0).__main__.Bag::items" in lines
    assert any(line.startswith("call stub builtins.list.append[list[int32], Own[int32]](%2, ") for line in lines)


def test_container_members_are_initialized_at_entry(active) -> None:
    bag = lower_constructor(active.constructors["Bag"], MIRBodyId("containers", "Bag"), definitions=active.definitions)
    pair = lower_constructor(active.constructors["Pair"], MIRBodyId("containers", "Pair"),
                             definitions=active.definitions)
    assert "initialize-receiver %0 (construct () may-raise)" in _lines(bag) and bag.exceptional_exits
    assert "initialize-receiver %0 (construct (%1, %1) may-raise)" in _lines(pair)
    heap: dict = {}
    execute(pair, Reference(1), 4, heap=heap)
    assert [v.elements for v in heap[1].values()] == [[4, 4]]
    # A caller's construct builds a container member's literal into storage
    # of the full expression and moves it in.
    lines = _lines(_fn(active, "build"))
    assert lines.index("%0 = construct () may-raise [initialize]") < lines.index(
        "%1 = construct (%0) [initialize]")


# --- refusals --------------------------------------------------------------------------

def test_kept_refusals(active) -> None:
    assert _lower(active, "holds").reason == "container holds a borrow"
    assert _lower(active, "nested").reason == "unsupported native container element"
    # A container local is classed by its type: THIR's fact, or none, changes nothing.
    fn = active.functions["own"]
    bare = replace(fn, body=_rewrite(fn.body, lambda n: replace(n, native_container=None)
                                     if isinstance(n, th.THIRVarDecl) else n))
    assert lower_function(bare, MIRBodyId("containers", "own"), definitions=active.definitions) == \
        lower_function(fn, MIRBodyId("containers", "own"), definitions=active.definitions)
    # A container a parameter cannot write.
    fn = _published(active.functions["set_scalar"])
    readonly = replace(fn, params=tuple(replace(p, passing=ParamPassing.CONST_REF, native_container=replace(
        p.native_container, readonly=True)) for p in fn.params))
    assert lower_function(readonly, MIRBodyId("containers", "ro"), definitions=active.definitions).reason \
        == "readonly element store"


# --- method calls ----------------------------------------------------------------------

def test_a_method_call_lends_its_receiver_first(active) -> None:
    fn = _fn(active, "grow")
    line = next(line for line in _lines(fn) if line.startswith("call stub"))
    assert re.fullmatch(r"call stub builtins\.list\.append\[list\[int32\], .*\]\(%0, %\d+\) "
                        r"\[writes=\{param0\[structure\]\}, may-raise\]", line)
    values = ContainerValue((1,))
    execute(fn, values, 7)
    assert values.elements == [1, 7]


# --- the validator ---------------------------------------------------------------------

def test_validator_rejects_damaged_container_slots(active) -> None:
    first, own, tail = _fn(active, "first"), _fn(active, "own"), _fn(active, "tail")
    span_first = _fn(active, "span_first")
    for fn in (first, own, tail, span_first):
        validate_function(fn)
    _rejects(_reslot(first, "ps", members=(leaf_shape(INT32),)),
             "container members disagree with its type")
    _rejects(_reslot(first, "ps", type=INT32), "value class mismatch")
    # A container lent at CONST_REF is the caller's region, never storage of the body's:
    # readonly storage is no shape at all, mutable storage not at a borrowing passing.
    _rejects(_reslot(first, "ps", holding=MIRHolding.VALUE), "slot readonly owned storage")
    ps = next(s for s in first.slots if s.name == "ps")
    # Placed and well formed, so only the binding refuses it: its passing lends a region.
    _rejects(_reslot(first, "ps", holding=MIRHolding.VALUE, readonly=False, storage_duration=MIRStorageDuration.BODY,
                     members=container_members(ps.type, False)),
             "parameter call parameter binding differs from its derivation")
    xs = _storage(own, "xs")
    _rejects(replace(own, slots=tuple(reslot(s, readonly=True, members=with_access(xs.shape.members, True))
                                      if s.id == xs.id else s for s in own.slots)),
             "slot readonly owned storage")
    _rejects(replace(own, slots=tuple(reslot(s, members=(leaf_shape(BOOL),)) if s.id == xs.id else s
                                      for s in own.slots)),
             "container members disagree with its type")
    holder = next(s for s in tail.slots if s.shape.cls is MIRClass.CONTAINER_VIEW)
    _rejects(replace(tail, slots=tuple(reslot(s, readonly=False) if s is holder else s for s in tail.slots)),
             "mutable loan of a readonly value")
    _rejects(repass(span_first, "s", ParamPassing.CONST_REF),
             "parameter call parameter passing differs from its type")
    _rejects(_reslot(first, "p", members=(leaf_shape(INT32),)), "members on a memberless value")
    # A container's record element needs the layout the body supplies for it.
    point = next(s for s in first.slots if s.name == "ps").shape.element.type
    _rejects(replace(first, records=tuple(r for r in first.records if r.type != point)), "unsupported native element")
    # A record lent at CONST_REF is the caller's storage, never the body's.
    items_of = _fn(active, "items_of")
    validate_function(items_of)
    _rejects(_reslot(items_of, "b", holding=MIRHolding.VALUE, readonly=False),
             "unsupported record storage type or form")


def test_validator_rejects_damaged_element_places(active) -> None:
    first, at, own = _fn(active, "first"), _fn(active, "at"), _fn(active, "own")
    t = _slot(at, "t").id
    is_element_read = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRRead) and _elements(s.value.source)
    # A scalar is no container.
    _rejects(_replace_first(at, is_element_read, lambda s: replace(
        s, value=MIRRead(MIRPlace(t, (MIRContainerElements(),))))), "element projection needs container storage")
    # Only an element access may raise.
    _rejects(_replace_first(at, is_element_read, lambda s: replace(
        s, value=MIRRead(MIRPlace(t), may_raise=True))), "only an element access may raise")
    is_borrow = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow)
    _rejects(_replace_first(first, is_borrow, lambda s: replace(
        s, value=MIRBorrow(MIRPlace(_slot(first, "ps").id), may_raise=True))), "only an element access may raise")
    # A Span views the region of its own element type, and never raises.
    span = _slot(own, "s").id
    _rejects(_replace_first(own, lambda s: is_borrow(s) and s.target.root == span, lambda s: replace(
        s, value=replace(s.value, may_raise=True))), "container view borrow type mismatch")
    _rejects(_reslot(own, "s", members=(leaf_shape(BOOL),), type=make_span(BOOL)),
             "container view borrow type mismatch")
    # A borrowed container holds a whole container place, not an element.
    items = _fn(active, "items_of")
    _rejects(_replace_first(items, is_borrow, lambda s: replace(
        s, value=MIRBorrow(MIRPlace(s.value.source.root, (*s.value.source.projections, MIRContainerElements()))))),
        "storage borrow type mismatch")
    # A whole container field is replaced by a member write, as a record
    # member is: in place, under the record, never as a rebind of a holder.
    def field_write(fact: MIRStorageWrite) -> MIRFunction:
        write = MIRAssign(items.blocks[0].statements[0].value.source, MIRConstruct((), True), None, fact)
        return replace(items, exceptional_exits=True, blocks=(replace(
            items.blocks[0], statements=(write, *items.blocks[0].statements)), *items.blocks[1:]))
    validate_function(field_write(MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)))
    receiver = items.blocks[0].statements[0].value.source.root
    _rejects(field_write(MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, receiver)),
             "record member write needs a replacement fact")
    # A container field whose members MIR does not model.
    nested = make_list(make_list(INT32))
    def nested_field(s: MIRAssign) -> MIRAssign:
        *head, field = s.value.source.projections
        return replace(s, value=MIRBorrow(MIRPlace(s.value.source.root, (*head, replace(field, type=nested)))))
    borrow = next(st for b in items.blocks for st in b.statements if is_borrow(st))
    field = borrow.value.source.projections[-1]
    # Declared so, or the field is no member of its record at all.
    declared = Redeclared(items.record_fields, lambda typ, fields: frozenset(
        replace(f, type=nested) if f == field else f for f in fields) if typ == field.id.owner else fields)
    _rejects(replace(_replace_first(items, is_borrow, nested_field), record_fields=declared),
             "unsupported container field")
    _rejects(_replace_first(items, is_borrow, nested_field), "field missing from declared fields")
    # A Span aliases only a Span holder of its own layout.
    _rejects(_replace_first(own, lambda s: is_borrow(s) and s.target.root == span, lambda s: replace(
        s, value=MIRAlias(_slot(own, "xs").id))), "alias type mismatch")
    # A readonly container is never written through.
    xs = _slot(first, "ps").id
    _rejects(_replace_first(first, is_borrow, lambda s: MIRAssign(
        MIRPlace(xs, (MIRDeref(), MIRContainerElements())), MIRConstruct((), False), s.loc,
        MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, xs))), "store through readonly reference")
    # A whole container a pointer reaches is rebuilt in place only as its
    # holder's referent, as a record is.
    scalar = _fn(active, "set_scalar")
    whole = MIRAssign(MIRPlace(_slot(scalar, "xs").id, (MIRDeref(),)), MIRConstruct((), True), None,
                      MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))
    _rejects(replace(scalar, blocks=(replace(scalar.blocks[0], statements=(whole, *scalar.blocks[0].statements)),
                                     *scalar.blocks[1:])), "invalid in-place rebind owner or target")


def test_validator_rejects_damaged_element_writes(active) -> None:
    scalar, record = _fn(active, "set_scalar"), _fn(active, "replace_after_use")
    is_write = lambda s: isinstance(s, MIRAssign) and _elements(s.target)
    validate_function(scalar)
    validate_function(record)
    _rejects(_replace_first(scalar, is_write, lambda s: replace(
        s, storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))), "element write needs an in-place fact")
    _rejects(_replace_first(scalar, is_write, lambda s: replace(s, value=MIRConstant(True))),
             "constant type or range mismatch")
    _rejects(_replace_first(scalar, is_write, lambda s: replace(s, value=MIRAlias(s.target.root))),
             "element write type mismatch")
    p = _slot(record, "p").id
    _rejects(_replace_first(record, is_write, lambda s: replace(
        s, value=MIRCopy(MIRPlace(p, (MIRDeref(),)), may_raise=True))), "record copy source or eligibility")
    _rejects(_replace_first(record, is_write, lambda s: replace(s, value=MIRMove(p))),
             "record move source or eligibility")
    # An element write may raise (its index), so the body has exceptional exits.
    _rejects(replace(scalar, exceptional_exits=False), "exceptional exit fact mismatch")
    copy = _fn(active, "str_copy")
    _rejects(_replace_first(copy, _assign(MIRCopy), lambda s: replace(s, value=replace(s.value, may_raise=False))),
             "owned leaf copy exit fact mismatch")


def test_a_mutable_container_replaces_a_readonly_element(active) -> None:
    # `ps[0] = Point(3)` over `ps: list[readonly[Point]]`: the write rule
    # keys an element's replacement on its container's access.
    record = _fn(active, "replace_after_use")
    ps = _slot(record, "ps")
    point = ps.type.type_args[0]
    frozen = NominalType("list", (ReadonlyType(point),), _module_qname="builtins.list")
    members = container_members(frozen, False)
    assert all(m.readonly for m in members)
    slots = tuple(reslot(s, type=frozen, members=members) if s.id == ps.id
                  else reslot(s, readonly=True) if s.name == "p" else s for s in record.slots)
    validate_function(bind_parameters(replace(record, slots=slots)))


def test_validator_rejects_damaged_container_storage(active) -> None:
    own, make = _fn(active, "own"), _fn(active, "make")
    xs = _storage(own, "xs").id
    is_literal = lambda s: isinstance(s, MIRAssign) and s.target == MIRPlace(xs)
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=replace(s.value, fields=s.value.fields[:2]))),
             "incomplete or mistyped container literal")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=replace(s.value, may_raise=False))),
             "incomplete or mistyped container literal")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRConstant(1))),
             "record write on non-record operation")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRMove(xs))), "record move source or eligibility")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRCopy(MIRPlace(_slot(own, "s").id), True))),
             "record copy source or eligibility")
    # The owned result is the body's container of the result type.
    n = _slot(make, "n").id
    ret = make.blocks[-1]
    _rejects(replace(make, blocks=(*make.blocks[:-1], replace(ret, terminator=MIRReturn(n, ret.terminator.loc)))),
             "owned record return needs movable owned storage")


def _container_call(fn: MIRFunction, target: str, ret: TpyType, argument: str) -> MIRFunction:
    """`fn` with the literal building `target` replaced by a call of a user
    callee taking `argument`'s int32 by value and returning `ret`."""
    signature = th.THIRCallableSignature((INT32,), ret, None, (ParamPassing.VALUE,), return_representation(ret))
    summary = MIRCallSummary(th.THIRResolvedCallee(th.THIRFunctionIdentity("containers", "made"), signature),
                             (bound(INT32, ParamPassing.VALUE),), frozenset({0}),
                             frozenset(), frozenset(), frozenset(), frozenset(), False)
    built = _replace_first(fn, lambda s: _assign(MIRConstruct)(s) and s.target == MIRPlace(_storage(fn, target).id),
                           lambda s: replace(s, value=MIRCall(summary, (_slot(fn, argument).id,), True)))
    return replace(built, call_summaries=(*built.call_summaries, summary))


def test_validator_rejects_a_container_call_result_of_another_type(active) -> None:
    make = _fn(active, "make")
    xs = _slot(make, "xs").type
    # An `Own[...]` container result is the target's own storage.
    validate_function(_container_call(make, "xs", OwnType(xs), "n"))
    for ret in (OwnType(make_list(BOOL)), INT32):
        _rejects(_container_call(make, "xs", ret, "n"), "call record result type mismatch")


def test_validator_rejects_damaged_results_and_iterators(active) -> None:
    items, tail, span_sum = _fn(active, "items_of"), _fn(active, "tail"), _fn(active, "span_sum")
    _rejects(replace(tail, borrowed_result=replace(tail.borrowed_result, readonly=False)),
             "unsupported return type or access")
    holder = next(s for s in items.slots if s.shape.whole_container and s.shape.is_pointer)
    _rejects(replace(items, slots=tuple(reslot(s, readonly=True, members=with_access(s.shape.members, True))
                                        if s is holder else s for s in items.slots)),
             "borrowed return type or access mismatch")
    iterator = next(s for s in span_sum.slots if s.shape.holding is MIRHolding.CURSOR)
    t = _slot(span_sum, "t").id
    _rejects(_replace_first(span_sum, _assign(MIRIteratorInit), lambda s: replace(s, value=MIRIteratorInit(t))),
             "iterator source or access mismatch")
    walk = _fn(active, "walk_strs", readonly_iteration=True)
    n = _slot(walk, "n").id
    _rejects(_replace_first(walk, _assign(MIRIteratorRead), lambda s: replace(s, target=MIRPlace(n))),
             "iterator element type or access mismatch")
    assert iterator.shape.members == (leaf_shape(INT32),)


def test_validator_rejects_damaged_container_member_init(active) -> None:
    pair = lower_constructor(active.constructors["Pair"], MIRBodyId("containers", "Pair"),
                             definitions=active.definitions)
    validate_function(pair)
    init = pair.receiver_init
    member = init.members.fields[0]
    receiver = init.receiver
    param = member.value.fields[0]

    def damaged(value: object) -> MIRFunction:
        return replace(pair, receiver_init=replace(init, members=MIRMemberInits((replace(member, value=value),))))
    # The receiver is the storage entry builds, never an operand.
    _rejects(damaged(MIRConstruct((receiver, receiver), True)), "invalid receiver initializer parameter")
    # A literal allocates: its exit fact is the value's own.
    _rejects(damaged(MIRConstruct((param, param), False)), "invalid receiver initializer literal")
    _rejects(damaged(MIRCopy(MIRPlace(param))), "invalid receiver initializer parameter")
    _rejects(damaged(MIRConstant(1)), "invalid receiver initializer")
    assert member == MIRMemberInit(MIRConstruct((param, param), True), member.loc)


# --- dict views -------------------------------------------------------------------

def test_a_dict_view_loop_walks_the_member_its_iteration_declares(fresh) -> None:
    lines = _lines(_fn(fresh, "keys_len"))
    # The view holds the dict's region, typed by its own declared member (the
    # key); its cursor walks the keys, viewed by the loop variable.
    assert "%3: dict_keys[str, int32] readonly-ref element=str:owned_leaf:readonly temporary" in lines
    assert "%3 = call stub builtins.dict.keys[dict[str, int32]](%0) [pure, reader, returns={param0}, may-raise]" in lines
    assert "%4: dict_keys[str, int32] native-iterator readonly element=str:owned_leaf:readonly temporary" in lines
    assert "%4 = iterator-init %3" in lines and "%6 = iterator-read %4" in lines
    lines = _lines(_fn(fresh, "values_total"))
    # A values view walks the values.
    assert "%4: dict_values[str, int32] native-iterator readonly element=int32:leaf:readonly temporary" in lines


def test_a_pure_stub_reads_a_dict_view_whole(active) -> None:
    lines = _lines(_fn(active, "values_len"))
    assert "%1 = call stub builtins.dict.values[dict[str, int32]](%0) [pure, reader, returns={param0}, may-raise]" in lines
    assert "%2 = call stub tpy._builtins._funcs.len[Sized](%1) [pure, reader, may-raise]" in lines


def test_validator_rejects_damaged_view_cursors(fresh) -> None:
    fn = _fn(fresh, "values_total")
    cursor = next(s for s in fn.slots if s.shape.holding is MIRHolding.CURSOR)
    holder = next(s for s in fn.slots if s.type == cursor.type and s is not cursor)
    assert cursor.shape.members == holder.shape.members == (leaf_shape(INT32, True),)
    # A cursor over a values view walks the value member, never the key.
    key = (owned_shape(STR, True),)
    damaged = replace(fn, slots=tuple(reslot(s, members=key) if s is cursor else s for s in fn.slots))
    _rejects(damaged, "container members disagree with its type")


def test_only_a_pure_stub_binds_a_readonly_container_mutably(active) -> None:
    fn = _fn(active, "grow")
    # `append` writes its receiver: a readonly holder cannot be lent to it.
    _rejects(bind_parameters(_reslot(fn, "xs", readonly=True,
                                     members=(leaf_shape(INT32, True),))),
             "call record argument mismatch")


# --- calls over real lowered MIR ---------------------------------------------------

CALLS = """\
from tpy import int32, Own, Span, readonly


def consume(xs: Own[list[int32]]) -> int32:
    return len(xs)


def give() -> int32:
    xs = [1, 2]
    return consume(xs)


def after_slice() -> int32:
    xs = [1, 2]
    s = xs[0:1]
    consume(xs)
    return s[0]


def ro_values(d: readonly[dict[str, int32]]) -> int:
    return len(d.values())


def inferred_values(d: dict[str, int32]) -> int:
    return len(d.values())


def span_sum(s: Span[int32]) -> int32:
    t = 0
    for v in s:
        t += v
    return t


def pass_span(s: Span[int32]) -> int32:
    xs = [1, 2, 3]
    w = xs[0:2]
    return span_sum(w) + span_sum(s)


def same(xs: list[int32]) -> list[int32]:
    return xs


def first_of(xs: list[int32]) -> int32:
    return same(xs)[0]
"""


@pytest.fixture(scope="module")
def calls():
    """Each body's MIR dump lines and lowered body, lowered against its
    callees' summaries."""
    compiler, modules = _compile(CALLS)
    entry = _entry(modules)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        dump = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions,
                                compiler.thir_reject_by_node, workspace)
    bodies: dict[str, list[str]] = {}
    for line in dump.splitlines():
        if line.startswith("fn "):
            name = line.split("::", 1)[1].split("@", 1)[0]
            bodies[name] = [line]
        elif bodies:
            bodies[name].append(re.sub(r" @ \d+:\d+$", "", re.sub(r" residence=r\d+| duration=\w+", "", line.strip())))
    functions = {bid.declaration.split("@")[0]: fn for bid, fn in workspace_functions(workspace).items()}
    return bodies, functions


def test_a_local_moved_into_an_own_parameter_is_replaced(calls) -> None:
    lines = calls[0]["give"]
    # `xs` holds its own storage (%2) through its holder.
    assert "%2: list[int32] owned-storage element=int32:leaf temporary" in lines
    assert "%3: list[int32] mutable-ref element=int32:leaf local xs" in lines
    assert "%3 = borrow %2" in lines
    # The argument is the caller's storage moved into a temporary the callee owns.
    assert "%4: list[int32] owned-storage element=int32:leaf temporary" in lines
    assert "%4 = move %2 [initialize]" in lines
    assert "%5 = call main::consume(%4) [reader, may-raise]" in lines
    # The move empties `xs`: a replacement event on the source place, with no holder of it live.
    assert "bb1 before 0: move-out %2" in lines
    assert "no conflicts in covered replacement events" in lines


def test_a_container_copied_into_an_own_parameter_is_refused(calls) -> None:
    # A live slice keeps `xs` borrowed, so the argument is an implicit copy.
    assert calls[0]["after_slice"][0].endswith("<MIR not covered: constructor argument type>")


def test_a_pure_stub_reads_a_readonly_receiver(calls) -> None:
    dumps, functions = calls
    for name in ("ro_values", "inferred_values"):
        lines = dumps[name]
        assert ("%0: dict[str, int32] readonly-ref element=str:owned_leaf:readonly "
                "value=int32:leaf:readonly parameter d") in lines, name
        assert any(line.startswith("%1 = call stub builtins.dict.values[dict[str, int32]](%0) "
                                   "[pure, reader, returns={param0}, may-raise]") for line in lines), name
    # The const-inferred receiver reaches the @auto_readonly mutable clone: a
    # pure stub binding its receiver mutably, which reads the readonly holder.
    assert any(line.startswith("%1: dict_values[str, int32] readonly-ref") for line in dumps["inferred_values"])
    assert any(line.startswith("%1: dict_values[str, readonly[int32]] readonly-ref") for line in dumps["ro_values"])
    clone, = (s for s in functions["inferred_values"].call_summaries if s.callee.identity.qualified_name.endswith(
        ".values"))
    assert clone.callee.contract is th.THIRStubContract.PURE and not clone.parameters[0].readonly
    # The declared-readonly receiver resolves the plain readonly overload instead.
    declared, = (s for s in functions["ro_values"].call_summaries if s.callee.identity.qualified_name.endswith(
        ".values"))
    assert declared.parameters[0].readonly


def test_a_span_holder_is_lent_at_a_span_parameter(calls) -> None:
    # A Span binding is not storage: the local and the parameter lend the region they view.
    lines = calls[0]["pass_span"]
    assert "%8: Span[int32] mutable-ref element=int32:leaf local w" in lines
    assert "%9 = call main::span_sum(%8) [reader, may-raise]" in lines
    assert "%10 = call main::span_sum(%0) [reader, may-raise]" in lines


def test_an_element_of_a_container_a_call_returns_is_read_through_its_holder(calls) -> None:
    # `same(xs)[0]`: the call's holder is a borrowed container; the element
    # place is under the object it holds, whatever its holding.
    fn = calls[1]["first_of"]
    validate_function(fn)
    read = next(s.value for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and isinstance(s.value, MIRRead) and _elements(s.value.source))
    holder = next(s for s in fn.slots if s.id == read.source.root)
    assert holder.shape.whole_container and holder.shape.is_pointer and holder.kind is not MIRSlotKind.PARAMETER
    assert read.source.projections == (*object_prefix(holder.shape), MIRContainerElements())


def _with_summary(fn: MIRFunction, old: MIRCallSummary, new: MIRCallSummary) -> MIRFunction:
    def change(s):
        return replace(s, value=replace(s.value, summary=new)) if isinstance(s, MIRAssign) and isinstance(
            s.value, MIRCall) and s.value.summary is old else s
    return replace(fn, call_summaries=tuple(new if s is old else s for s in fn.call_summaries), blocks=tuple(
        replace(b, statements=tuple(change(s) for s in b.statements)) for b in fn.blocks))


def test_validator_rejects_a_readonly_span_lent_mutably(calls) -> None:
    fn = calls[1]["pass_span"]
    validate_function(fn)
    _rejects(_reslot(fn, "w", readonly=True, members=(leaf_shape(INT32, True),)),
             "call container argument mismatch")
    # A view binding is by value by construction: the summary check refuses any other passing.
    summary, = fn.call_summaries
    damaged = replace(summary, parameters=(replace(summary.parameters[0], passing=ParamPassing.CONST_REF),))
    _rejects(_with_summary(fn, summary, damaged), "call parameter passing differs from its type")

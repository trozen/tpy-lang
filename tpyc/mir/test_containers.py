"""Native containers as MIR places: owned container storage, borrowed
containers and Span holders reach one `[elements]` region. Element reads,
borrows and copies, unstepped slices, in-place element writes, literals,
container fields and container results lower against the layout the type
derives; the validator re-derives it and rejects every damaged shape."""

import re
from dataclasses import dataclass, fields, is_dataclass, replace

import pytest

from ..codegen_cpp.forms import loop_binding_kind
from ..compilation_context import activate_compiler
from ..mir_workspace import analyze_call_workspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import (
    BOOL, INT32, STR, NominalType, OwnType, TpyType, VoidType, make_list, make_span, return_representation,
    unwrap_readonly, unwrap_ref_type,
)
from .call_contract import MIRCallSummary, MIRParameterBinding
from ..thir.scalar_leaves import declared_members, native_container_type
from .collect import call_definitions, dump_codegen_mir
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBorrow, MIRCall, MIRConstant, MIRConstruct, MIRContainerElements, MIRContainerLayout,
    MIRCopy, MIRDeref, MIRFunction, MIRIteratorInit, MIRIteratorRead, MIRMemberInit,
    MIRMemberInitMode, MIRMove, MIRNotCovered, MIRPlace, MIRRead, MIRRecordWrite, MIRRecordWriteMode, MIRReturn,
    MIRSlot, MIRTupleElement, MIRValueKind,
)
from .testutil import ContainerValue, Reference, execute
from .validate import MIRValidationError, validate_function

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
    return _Unit(compiler, modules, functions, constructors, MIRDefinitions(tuple(constructors.values())))


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
                binding = loop_binding_kind(node.elem_type, node.const_loop_var, hoisted=node.hoist_loop_var)
                return replace(node, iteration=th.THIRNativeIteration(source, binding))
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


def _replace_first(fn: MIRFunction, match, change) -> MIRFunction:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements) if match(s))
    stmt = change(block.statements[index])
    return replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], stmt, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))


def _reslot(fn: MIRFunction, name: str, **changes) -> MIRFunction:
    return replace(fn, slots=tuple(replace(s, **changes) if s.name == name else s for s in fn.slots))


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
    assert "%0: list[Point] container-ref readonly element=Point:borrowed:readonly parameter ps" in lines
    assert lines.index("%2 = 0") < lines.index("%1 = borrow %0[elements] may-raise")
    assert any(line.startswith("%1: Point readonly-ref local p") for line in lines)
    cell = next(r.fields[0].id for r in fn.records if r.type.name == "Point")
    heap = {1: {cell: 3}, 2: {cell: 5}}
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), heap=heap) == 3


def test_a_scalar_element_is_read_after_its_index(active) -> None:
    fn = _fn(active, "at")
    lines = _lines(fn)
    assert lines.index("%5 = read %1") < lines.index("%6 = read %0[elements] may-raise")
    assert fn.exceptional_exits
    assert execute(fn, ContainerValue((3, 5)), 1) == 5


def test_a_field_of_an_element_is_read_in_place(active) -> None:
    fn = _fn(active, "field_of")
    assert "%2 = read %0[elements].__main__.Point::x may-raise" in _lines(fn)
    cell = next(r.fields[0].id for r in fn.records if r.type.name == "Point")
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), heap={1: {cell: 3}, 2: {cell: 5}}) == 5


def test_owned_and_viewed_elements_of_str(active) -> None:
    # A view holder borrows the element (spelled StrView: sema's one view rule
    # owns an inferred element read); an owning sink copies it.
    strs = _lines(_fn(active, "strs"))
    assert any(line.startswith("%1: StrView readonly-ref local v") for line in strs)
    assert "%1 = borrow %0[elements] may-raise" in strs
    copy = _lines(_fn(active, "str_copy"))
    assert any(re.fullmatch(r"%\d+ = copy %0\[elements\] may-raise \[initialize_once\]", line) for line in copy)
    # Iteration reads an owned-leaf element through a view of it.
    walk = _fn(active, "walk_strs", readonly_iteration=True)
    s = _slot(walk, "s")
    assert (s.type.name, s.value_kind, s.readonly) == ("StrView", MIRValueKind.BORROWED, True)
    assert any(isinstance(st, MIRAssign) and isinstance(st.value, MIRIteratorRead) and st.target.root == s.id
               for b in walk.blocks for st in b.statements)


# --- owned containers, literals, Span -------------------------------------------------

def test_an_owned_container_local_is_built_from_its_literal(active) -> None:
    lines = _lines(_fn(active, "own"))
    assert "%0: Array[int32, 3] owned element=int32:scalar local xs" in lines
    assert "%0 = construct (%1, %2, %3) may-raise [initialize_once]" in lines
    # An unstepped slice reads its bounds, then borrows the whole region.
    assert "%4: Span[int32] mutable-ref element=int32:scalar local s" in lines
    assert lines.index("%6 = 3") < lines.index("%4 = borrow %0[elements]")
    assert "%8 = read %4[elements] may-raise" in lines
    # `len` reads the container whole.
    assert any(re.fullmatch(r"%9 = call stub tpy\._builtins\._funcs\.len\[Sized\]\(%0\) .*", line) for line in lines)


def test_a_literal_of_records_moves_built_temporaries(active) -> None:
    fn = _fn(active, "points")
    ps = _slot(fn, "ps").id
    construct = next(s for b in fn.blocks for s in b.statements
                     if isinstance(s, MIRAssign) and isinstance(s.value, MIRConstruct) and s.target.root == ps)
    for operand in construct.value.fields:
        slot = fn.slots[operand.index]
        assert slot.value_kind is MIRValueKind.OWNED and slot.type.name == "Point"
    assert construct.value.may_raise


def test_an_owned_container_result_returns_its_storage(active) -> None:
    fn = _fn(active, "make")
    lines = _lines(fn)
    assert "%1: list[int32] owned element=int32:scalar local xs" in lines
    assert fn.blocks[-1].terminator == MIRReturn(_slot(fn, "xs").id, fn.blocks[-1].terminator.loc)
    assert execute(fn, 4) is not None


def test_an_owned_container_parameter_is_the_body_storage(active) -> None:
    # The parameter's name keeps THIR's `Own[...]` wrapper and reads in storage
    # form; the fact and the slot are the bare container it owns.
    lines = _lines(_fn(active, "own_param"))
    assert "%0: list[int32] owned element=int32:scalar parameter xs" in lines
    assert any(line.startswith("%1 = call stub tpy._builtins._funcs.len[Sized](%0)") for line in lines)


def test_a_span_parameter_and_result_view_a_region(active) -> None:
    tail = _fn(active, "tail")
    lines = _lines(tail)
    assert "result borrowed readonly" in lines
    assert "%1: Span[readonly[int32]] readonly-ref element=int32:scalar temporary" in lines
    assert "%1 = borrow %0[elements]" in lines
    first = _lines(_fn(active, "span_first"))
    assert "%0: Span[int32] mutable-ref element=int32:scalar parameter s" in first
    assert "%2 = read %0[elements] may-raise" in first
    span_sum = _fn(active, "span_sum")
    assert "%3: Span[int32] native-iterator mutable element=int32:scalar temporary" in _lines(span_sum)
    assert execute(span_sum, ContainerValue((1, 2, 3))) == 6


# --- element writes ------------------------------------------------------------------

def test_an_element_write_replaces_the_region_in_place(active) -> None:
    fn = _fn(active, "set_scalar")
    assert "%0[elements] = read %1 [in_place]" in _lines(fn)
    write = next(s for b in fn.blocks for s in b.statements if _elements(s.target))
    assert write.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, _slot(fn, "xs").id)
    values = ContainerValue((1, 2))
    assert execute(fn, values) == 5 and values.elements == [5, 2]
    record = _fn(active, "replace_after_use")
    assert "%0[elements] = construct (%5) [in_place]" in _lines(record)
    cell = next(r.fields[0].id for r in record.records if r.type.name == "Point")
    heap = {1: {cell: 1}}
    assert execute(record, ContainerValue((Reference(1),)), heap=heap) == 1 and heap[1][cell] == 3


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
    assert "%1 = borrow %0[elements]" in lines and "%1[elements] = read %4 [in_place]" in lines
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
    assert "%1: list[int32] container-ref mutable element=int32:scalar temporary" in lines
    assert "%1 = borrow (*%0).__main__.Bag::items" in lines


def test_a_local_alias_of_a_container_field_borrows_the_field(active) -> None:
    lines = _lines(_fn(active, "alias_push"))
    assert "%2: list[int32] container-ref mutable element=int32:scalar local x" in lines
    assert "%2 = borrow (*%0).__main__.Bag::items" in lines
    assert any(line.startswith("call stub builtins.list.append[list[int32], Own[int32]](%2, ") for line in lines)


def test_container_members_are_initialized_at_entry(active) -> None:
    bag = lower_constructor(active.constructors["Bag"], MIRBodyId("containers", "Bag"), definitions=active.definitions)
    pair = lower_constructor(active.constructors["Pair"], MIRBodyId("containers", "Pair"),
                             definitions=active.definitions)
    assert "initialize-receiver %0 (move construct ())" in _lines(bag) and bag.exceptional_exits
    assert "initialize-receiver %0 (move construct (%1, %1))" in _lines(pair)
    heap: dict = {}
    execute(pair, Reference(1), 4, heap=heap)
    assert [v.elements for v in heap[1].values()] == [[4, 4]]
    # A caller's construct has no operand for a container member.
    assert _lower(active, "build").reason == "constructor container field"


# --- refusals --------------------------------------------------------------------------

def test_kept_refusals(active) -> None:
    assert _lower(active, "holds").reason == "container holds a borrow"
    assert _lower(active, "nested").reason == "unsupported native container element"
    # A container local THIR published no fact for.
    fn = active.functions["own"]
    fn = replace(fn, body=_rewrite(fn.body, lambda n: replace(n, native_container=None)
                                   if isinstance(n, th.THIRVarDecl) else n))
    assert lower_function(fn, MIRBodyId("containers", "own"), definitions=active.definitions).reason \
        == "missing native container fact"
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
    _rejects(_reslot(first, "ps", container_layout=MIRContainerLayout(MIRTupleElement(INT32))),
             "unsupported native element")
    _rejects(_reslot(first, "ps", type=INT32), "invalid container or iterator layout")
    _rejects(_reslot(own, "xs", readonly=True), "unsupported container storage")
    _rejects(_reslot(own, "xs", container_layout=MIRContainerLayout(MIRTupleElement(BOOL))),
             "unsupported container storage")
    holder = next(s for s in tail.slots if s.value_kind is MIRValueKind.BORROWED)
    _rejects(replace(tail, slots=tuple(replace(s, readonly=False) if s is holder else s for s in tail.slots)),
             "invalid container view holder")
    _rejects(_reslot(span_first, "s", passing=ParamPassing.CONST_REF), "view parameter needs a by-value passing")
    _rejects(_reslot(first, "p", container_layout=MIRContainerLayout(MIRTupleElement(INT32))),
             "container layout on unrelated slot")


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
    _rejects(_reslot(own, "s", container_layout=MIRContainerLayout(MIRTupleElement(BOOL)), type=make_span(BOOL)),
             "container view borrow type mismatch")
    # A borrowed container holds a whole container place, not an element.
    items = _fn(active, "items_of")
    _rejects(_replace_first(items, is_borrow, lambda s: replace(
        s, value=MIRBorrow(MIRPlace(s.value.source.root, (*s.value.source.projections, MIRContainerElements()))))),
        "container borrow type mismatch")
    # A whole container field is never replaced.
    write = MIRAssign(items.blocks[0].statements[0].value.source, MIRConstruct((), True), None,
                      MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
    _rejects(replace(items, blocks=(replace(items.blocks[0], statements=(write, *items.blocks[0].statements)),
                                    *items.blocks[1:])), "container replacement is unsupported")
    # A container field whose members MIR does not model.
    nested = make_list(make_list(INT32))
    def nested_field(s: MIRAssign) -> MIRAssign:
        *head, field = s.value.source.projections
        return replace(s, value=MIRBorrow(MIRPlace(s.value.source.root, (*head, replace(field, type=nested)))))
    _rejects(_replace_first(items, is_borrow, nested_field), "unsupported container field")
    # A Span aliases only a Span holder of its own layout.
    _rejects(_replace_first(own, lambda s: is_borrow(s) and s.target.root == span, lambda s: replace(
        s, value=MIRAlias(_slot(own, "xs").id))), "alias type mismatch")
    # A readonly container is never written through.
    xs = _slot(first, "ps").id
    _rejects(_replace_first(first, is_borrow, lambda s: MIRAssign(
        MIRPlace(xs, (MIRContainerElements(),)), MIRConstruct((), False), s.loc,
        MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, xs))), "store through readonly container")


def test_validator_rejects_damaged_element_writes(active) -> None:
    scalar, record = _fn(active, "set_scalar"), _fn(active, "replace_after_use")
    is_write = lambda s: isinstance(s, MIRAssign) and _elements(s.target)
    validate_function(scalar)
    validate_function(record)
    _rejects(_replace_first(scalar, is_write, lambda s: replace(
        s, storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))), "element write needs an in-place fact")
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


def test_validator_rejects_damaged_container_storage(active) -> None:
    own, make = _fn(active, "own"), _fn(active, "make")
    xs = _slot(own, "xs").id
    is_literal = lambda s: isinstance(s, MIRAssign) and s.target == MIRPlace(xs)
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=replace(s.value, fields=s.value.fields[:2]))),
             "incomplete or mistyped container literal")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=replace(s.value, may_raise=False))),
             "incomplete or mistyped container literal")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRConstant(1))),
             "container write needs a literal, move, copy or call")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRMove(xs))), "container move source mismatch")
    _rejects(_replace_first(own, is_literal, lambda s: replace(s, value=MIRCopy(MIRPlace(_slot(own, "s").id), True))),
             "container copy source mismatch")
    # The owned result is the body's container of the result type.
    n = _slot(make, "n").id
    ret = make.blocks[-1]
    _rejects(replace(make, blocks=(*make.blocks[:-1], replace(ret, terminator=MIRReturn(n, ret.terminator.loc)))),
             "return type mismatch")


def _container_call(fn: MIRFunction, target: str, ret: TpyType, argument: str) -> MIRFunction:
    """`fn` with the literal building `target` replaced by a call of a user
    callee taking `argument`'s int32 by value and returning `ret`."""
    signature = th.THIRCallableSignature((INT32,), ret, None, (ParamPassing.VALUE,), return_representation(ret))
    summary = MIRCallSummary(th.THIRResolvedCallee(th.THIRFunctionIdentity("containers", "made"), signature),
                             (MIRParameterBinding(INT32, ParamPassing.VALUE, False),), frozenset({0}),
                             frozenset(), frozenset(), frozenset(), frozenset(), False)
    built = _replace_first(fn, lambda s: _assign(MIRConstruct)(s) and s.target == MIRPlace(_slot(fn, target).id),
                           lambda s: replace(s, value=MIRCall(summary, (_slot(fn, argument).id,), True)))
    return replace(built, call_summaries=(*built.call_summaries, summary))


def test_validator_rejects_a_container_call_result_of_another_type(active) -> None:
    make = _fn(active, "make")
    xs = _slot(make, "xs").type
    # An `Own[...]` container result is the target's own storage.
    validate_function(_container_call(make, "xs", OwnType(xs), "n"))
    for ret in (OwnType(make_list(BOOL)), INT32):
        _rejects(_container_call(make, "xs", ret, "n"), "call container result type mismatch")


def test_validator_rejects_damaged_results_and_iterators(active) -> None:
    items, tail, span_sum = _fn(active, "items_of"), _fn(active, "tail"), _fn(active, "span_sum")
    _rejects(replace(tail, borrowed_result=replace(tail.borrowed_result, readonly=False)),
             "unsupported return type or access")
    holder = next(s for s in items.slots if s.value_kind is MIRValueKind.BORROWED_CONTAINER)
    _rejects(replace(items, slots=tuple(replace(s, readonly=True, container_layout=s.container_layout)
                                        if s is holder else s for s in items.slots)),
             "borrowed return type or access mismatch")
    iterator = next(s for s in span_sum.slots if s.value_kind is MIRValueKind.NATIVE_ITERATOR)
    t = _slot(span_sum, "t").id
    _rejects(_replace_first(span_sum, _assign(MIRIteratorInit), lambda s: replace(s, value=MIRIteratorInit(t))),
             "iterator source or access mismatch")
    walk = _fn(active, "walk_strs", readonly_iteration=True)
    n = _slot(walk, "n").id
    _rejects(_replace_first(walk, _assign(MIRIteratorRead), lambda s: replace(s, target=MIRPlace(n))),
             "iterator element type or access mismatch")
    assert iterator.container_layout == MIRContainerLayout(MIRTupleElement(INT32))


def test_validator_rejects_damaged_container_member_init(active) -> None:
    pair = lower_constructor(active.constructors["Pair"], MIRBodyId("containers", "Pair"),
                             definitions=active.definitions)
    validate_function(pair)
    init = pair.receiver_init
    member = init.fields[0]
    receiver = init.receiver

    def damaged(**changes) -> MIRFunction:
        return replace(pair, receiver_init=replace(init, fields=(replace(member, **changes),)))
    _rejects(damaged(source=MIRConstruct((receiver,), True)), "invalid receiver initializer literal")
    _rejects(damaged(source=member.source.fields[0], mode=MIRMemberInitMode.COPY),
             "invalid receiver initializer parameter")
    _rejects(damaged(may_raise=False), "receiver initializer exit fact mismatch")
    _rejects(damaged(source=MIRConstant(1)), "invalid receiver initializer")
    assert member == MIRMemberInit(member.source, MIRMemberInitMode.MOVE, True, member.loc)


# --- dict views -------------------------------------------------------------------

def test_a_dict_view_loop_walks_the_member_its_iteration_declares(fresh) -> None:
    lines = _lines(_fn(fresh, "keys_len"))
    # The view holds the dict's region, typed by its own declared member (the
    # key); its cursor walks the keys, viewed by the loop variable.
    assert "%3: dict_keys[str, int32] readonly-ref element=str:owned:readonly temporary" in lines
    assert "%3 = call stub builtins.dict.keys[dict[str, int32]](%0) [pure, reader, returns={param0}, may-raise]" in lines
    assert "%4: dict_keys[str, int32] native-iterator readonly element=str:owned:readonly temporary" in lines
    assert "%4 = iterator-init %3" in lines and "%6 = iterator-read %4" in lines
    lines = _lines(_fn(fresh, "values_total"))
    # A values view walks the values.
    assert "%4: dict_values[str, int32] native-iterator readonly element=int32:scalar temporary" in lines


def test_a_pure_stub_reads_a_dict_view_whole(active) -> None:
    lines = _lines(_fn(active, "values_len"))
    assert "%2 = call stub builtins.dict.values[dict[str, int32]](%0) [pure, reader, returns={param0}, may-raise]" in lines
    assert "%3 = call stub tpy._builtins._funcs.len[Sized](%2) [pure, reader, may-raise]" in lines


def test_validator_rejects_damaged_view_cursors(fresh) -> None:
    fn = _fn(fresh, "values_total")
    cursor = next(s for s in fn.slots if s.value_kind is MIRValueKind.NATIVE_ITERATOR)
    holder = next(s for s in fn.slots if s.type == cursor.type and s is not cursor)
    assert cursor.container_layout == holder.container_layout == MIRContainerLayout(MIRTupleElement(INT32))
    # A cursor over a values view walks the value member, never the key.
    key = MIRContainerLayout(MIRTupleElement(STR, MIRValueKind.OWNED, True))
    damaged = replace(fn, slots=tuple(replace(s, container_layout=key) if s is cursor else s for s in fn.slots))
    _rejects(damaged, "unsupported native element")


def test_only_a_pure_stub_binds_a_readonly_container_mutably(active) -> None:
    fn = _fn(active, "grow")
    # `append` writes its receiver: a readonly holder cannot be lent to it.
    _rejects(_reslot(fn, "xs", readonly=True, passing=ParamPassing.CONST_REF,
                     container_layout=MIRContainerLayout(MIRTupleElement(INT32))),
             "call container argument mismatch")


# --- calls over real lowered MIR ---------------------------------------------------

CALLS = """\
from tpy import int32, Own, readonly


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
"""


@pytest.fixture(scope="module")
def calls():
    """Each body's MIR dump lines and lowered body, lowered against its
    callees' summaries."""
    compiler, modules = _compile(CALLS)
    entry = _entry(modules)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
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
    functions = {bid.declaration.split("@")[0]: fn for bid, fn in workspace.bodies.items()}
    return bodies, functions


def test_a_local_moved_into_an_own_parameter_is_replaced(calls) -> None:
    lines = calls[0]["give"]
    assert "%0: list[int32] owned element=int32:scalar local xs" in lines
    # The argument is the caller's storage moved into a temporary the callee owns.
    assert "%4: list[int32] owned element=int32:scalar temporary" in lines
    assert "%4 = move %0 [initialize_region]" in lines
    assert "%5 = call main::consume(%4) [reader, may-raise]" in lines
    # The move empties `xs`: a replacement event on the source place, with no holder of it live.
    assert "bb1 before 0: move-out %0" in lines
    assert "no conflicts in covered replacement events" in lines


def test_a_container_copied_into_an_own_parameter_is_refused(calls) -> None:
    # A live slice keeps `xs` borrowed, so the argument is an implicit copy.
    assert calls[0]["after_slice"][0].endswith("<MIR not covered: container argument needs an owned move>")


def test_a_pure_stub_reads_a_readonly_receiver(calls) -> None:
    dumps, functions = calls
    for name in ("ro_values", "inferred_values"):
        lines = dumps[name]
        assert ("%0: dict[str, int32] container-ref readonly element=str:owned:readonly value=int32:scalar "
                "parameter d") in lines, name
        assert any(line.startswith("%2 = call stub builtins.dict.values[dict[str, int32]](%0) "
                                   "[pure, reader, returns={param0}, may-raise]") for line in lines), name
    # The const-inferred receiver reaches the @auto_readonly mutable clone: a
    # pure stub binding its receiver mutably, which reads the readonly holder.
    assert any(line.startswith("%2: dict_values[str, int32] readonly-ref") for line in dumps["inferred_values"])
    assert any(line.startswith("%2: dict_values[str, readonly[int32]] readonly-ref") for line in dumps["ro_values"])
    clone, = (s for s in functions["inferred_values"].call_summaries if s.callee.identity.qualified_name.endswith(
        ".values"))
    assert clone.callee.contract is th.THIRStubContract.PURE and not clone.parameters[0].readonly
    # The declared-readonly receiver resolves the plain readonly overload instead.
    declared, = (s for s in functions["ro_values"].call_summaries if s.callee.identity.qualified_name.endswith(
        ".values"))
    assert declared.parameters[0].readonly

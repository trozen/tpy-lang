"""A summary's return origin is a path into a parameter, resolved by the caller like a write place."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.test_method_stubs import _replace_node, nodes as _thir_nodes
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import (
    INT32, STR, NominalType, OptionalType, OwnType, ReadonlyType, RefType, Representation, VoidType, return_representation,
    unwrap_ref_type,
)
from .call_contract import (
    MIRCallSummary, MIRParameterWrite, MIRReturnOrigin, MIRSummaryResult, MIRSummaryState, call_result,
    endpoint_admitted, parameter_contract_problem, path_step, path_walk, resolve_path, result_problem,
    return_endpoint_problem, return_origin_problem, stub_summary, summary_problem,
)
from .shapes import (
    MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload, store_problem, walk,
)
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, call_return_problem, resolve_call_returns
from .dump import _call as _dump_call, _write_step, dump_function
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRCall, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRReturn,
    MIRPresentCall, MIRSlot, MIRSlotId, MIRSlotKind,
)
from . import summaries
from .summaries import summarize_function
from .testutil import (
    as_declared, Redeclared, no_layouts, borrowed_shape, bound, leaf_shape, reaccess, prepared, workspace_functions,
)
from .value import MIRRecords, MIRField, MIRFieldId, MIRHolding, MIRShape


SOURCE = '''\
from tpy import int32, readonly, auto_readonly, StrView, Span


class Inner:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Bag:
    n: int32
    name: str
    items: list[int32]
    more: list[int32]

    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name
        self.items = []
        self.more = []

    @property
    def elems(self) -> list[int32]:
        return self.items

    @elems.setter
    def elems(self, v: list[int32]) -> None:
        self.items = v

    @property
    def count(self) -> int32:
        return self.n

    @count.setter
    def count(self, v: int32) -> None:
        self.n = v

    @property
    def label(self) -> str:
        return self.name

    @property
    def tag(self) -> StrView:
        return self.name

    @auto_readonly
    def me(self) -> "Bag":
        return self

    @auto_readonly
    def view_items(self) -> list[int32]:
        return self.items

    def other_items(self) -> list[int32]:
        return self.more

    def grab(self) -> list[int32]:
        self.n += 1
        return self.items

    def push(self, v: int32) -> None:
        self.items.append(v)

    def rename(self, v: str) -> None:
        self.name = v

    def span_items(self) -> Span[int32]:
        return self.items[0:]


class Box:
    inner: Inner

    def __init__(self, inner: Inner) -> None:
        self.inner = inner


class Shelf:
    bag: Bag

    def __init__(self, bag: Bag) -> None:
        self.bag = bag


class Room:
    shelf: Shelf

    def __init__(self, shelf: Shelf) -> None:
        self.shelf = shelf


def shelf_count(s: Shelf) -> int32:
    return s.bag.n


def room_count(r: Room) -> int32:
    return r.shelf.bag.n


def via_getter(b: Bag) -> int32:
    xs = b.elems
    return xs[0]


def via_field(b: Bag) -> int32:
    ys = b.items
    return ys[0]


def read_count(b: Bag) -> int32:
    return b.count


def read_label(b: Bag) -> int:
    s = b.label
    return len(s)


def read_readonly(b: readonly[Bag]) -> int32:
    d = b.me()
    return b.count + d.n


def read_inferred(b: Bag) -> int32:
    t = 0
    for x in b.view_items():
        t += x
    return t


def write_through_me(b: Bag) -> None:
    d = b.me()
    d.n = 5


def set_count(b: Bag) -> None:
    b.count = 3


def iterate(b: Bag) -> int32:
    t = 0
    for x in b.elems:
        t += x
    return t


def view_across(b: Bag) -> int:
    v: StrView = b.tag
    b.rename("z")
    return len(v)


def iterate_grab(b: Bag) -> int32:
    t = 0
    for x in b.grab():
        t += x
    return t


def grow_alias(b: Bag) -> int32:
    xs = b.view_items()
    b.push(4)
    return xs[0]


def inner_of(b: Box | None, q: Inner) -> Inner:
    if b is not None:
        return b.inner
    return q


def main() -> None:
    b = Bag("b")
    b.push(1)
    print(via_getter(b), via_field(b), read_count(b), read_label(b), read_readonly(b), iterate(b), grow_alias(b))
    set_count(b)
    write_through_me(b)
    print(read_inferred(b), view_across(b), b.count, len(b.span_items()), Box(Inner(1)).inner.x, iterate_grab(b))
    i, bx = Inner(3), Box(Inner(2))
    print(inner_of(bx, i).x, inner_of(None, i).x)


main()
'''

INTS = NominalType("list", (INT32,), _module_qname="builtins.list")


@dataclass(frozen=True)
class _Program:
    compiler: object
    modules: list
    # THIR bodies by body declaration (`Bag.elems@23:4`, `...#2` for a twin).
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        functions = {body: fn for body, fn in mir.workspace.definitions.values()}
        functions.update({body: fn for body, fn in mir.workspace.twins.values()})
        result = _Program(compiler, modules, {k.declaration: v for k, v in functions.items()}, mir.definitions,
                       mir.workspace, {v.body.declaration.split("@")[0] + ("#2" if "#2" in v.body.declaration
                                                                          else ""): v for v in verdicts})
    return result


@pytest.fixture(autouse=True)
def active(program):
    # The per-test state reset clears the stub facts latched onto the static TypeDefs.
    for module in program.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(program.compiler):
        yield


def _definition(program: _Program, name: str) -> th.THIRFunction:
    owner, _, member = name.partition(".")
    return next(fn for key, (_, fn) in program.workspace.definitions.items()
                if key.name == member and key.owner is not None and key.owner.endswith(owner)
                and key.accessor in (None, "fget"))


def _summary(program: _Program, name: str):
    result = program.workspace.summaries[_definition(program, name).resolved_callee.identity]
    return result


def _known(program: _Program, name: str):
    result = _summary(program, name)
    assert result.state is MIRSummaryState.KNOWN, (name, result.reason)
    return result.summary


def _bag(program: _Program) -> NominalType:
    return _definition(program, "Bag.push").receiver.type


def _field(program: _Program, name: str, typ) -> th.THIRFieldIdentity:
    return th.THIRFieldIdentity(_bag(program), name, typ)


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _end_referents(fn: MIRFunction, name: str) -> frozenset[MIRReferent]:
    deps = prepared(fn).dependencies
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    return deps.referents[MIRPoint(block.id, len(block.statements))][MIRPlace(_slot(fn, name).id)]


# --- the callee side: a path into the parameter ------------------------------------

def test_a_container_field_returns_as_its_path(program: _Program) -> None:
    items = _field(program, "items", INTS)
    assert _known(program, "Bag.elems").returns == {MIRReturnOrigin(0, (items,))}
    assert _known(program, "Bag.view_items").returns == {MIRReturnOrigin(0, (items,))}
    more = _field(program, "more", INTS)
    assert _known(program, "Bag.other_items").returns == {MIRReturnOrigin(0, (more,))}


def test_a_view_of_an_owned_leaf_field_returns_as_its_path(program: _Program) -> None:
    name = _field(program, "name", next(f.type for f in _bag_fields(program) if f.name == "name"))
    summary = _known(program, "Bag.tag")
    assert summary.returns == {MIRReturnOrigin(0, (name,))}
    assert summary.borrowed_result.readonly


def _bag_fields(program: _Program) -> tuple[th.THIRFieldIdentity, ...]:
    return tuple(th.THIRFieldIdentity(_bag(program), f.id.name, f.type)
                 for f in program.definitions.get(None, _bag(program)).layout.fields)


def test_the_whole_receiver_returns_with_an_empty_path(program: _Program) -> None:
    assert _known(program, "Bag.me").returns == {MIRReturnOrigin(0)}


def test_unspellable_origins_refuse(program: _Program) -> None:
    # A Span over a field: the origin is a region under the field, which the grammar does not return.
    assert _summary(program, "Bag.span_items").state is MIRSummaryState.OPAQUE
    assert _summary(program, "Bag.span_items").reason == (
        "summary unsupported return origin: unsupported return origin type or access")
    elems = _known(program, "Bag.elems")
    items = _field(program, "items", INTS)
    n = _field(program, "n", INT32)
    for returns, reason in (
        # A container result needs the container: a scalar field, the whole record or a region refuse.
        ({MIRReturnOrigin(0, (n,))}, "unsupported return origin type or access"),
        ({MIRReturnOrigin(0)}, "unsupported return origin type or access"),
        ({MIRReturnOrigin(0, (items, MIRContainerElements()))}, "unsupported return origin type or access"),
        # Only a record member continues a field chain: a container field ends it.
        ({MIRReturnOrigin(0, (items, items))}, "field needs record storage"),
        ({MIRReturnOrigin(1, (items,))}, "invalid return parameter"),
    ):
        assert summary_problem(replace(elems, returns=frozenset(returns)), no_layouts()) == reason, returns
    # A record result reaches a record member of its own type, never a scalar field.
    me = _known(program, "Bag.me")
    assert summary_problem(replace(me, returns=frozenset({MIRReturnOrigin(0, (n,))})), no_layouts()) == (
        "unsupported return origin type or access")


def test_a_return_path_names_a_field_of_the_bound_storage(program: _Program) -> None:
    # A path names its field by the declaring owner; its walk checks it is
    # a member of the storage it is read from (an ancestor's field is a
    # member of a descendant's layout), in the summary and at each call.
    elems = _known(program, "Bag.elems")
    layouts = program.definitions.layouts
    stranger = NominalType("Other", _module_qname="main.Other")
    foreign = th.THIRFieldIdentity(stranger, "items", INTS)
    damaged = replace(elems, returns=frozenset({MIRReturnOrigin(0, (foreign,))}))
    assert summary_problem(damaged, layouts) == "field missing from declared fields"
    assert summary_problem(replace(elems, returns=frozenset({
        MIRReturnOrigin(0, (th.THIRFieldIdentity(INT32, "items", INTS),))})), layouts) == (
        "field missing from declared fields")
    summaries = dict(program.workspace.summaries)
    summaries[elems.callee.identity] = MIRSummaryResult(MIRSummaryState.KNOWN, damaged)
    result = lower_function(_free(program, "via_getter"), MIRBodyId("main", "via_getter"),
                            definitions=program.definitions, summaries=summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "call summary signature or contract mismatch"


# --- nested paths: one resolver for writes and return origins -------------------------

@dataclass(frozen=True)
class _Nested:
    shelf: NominalType
    room: NominalType
    bag: NominalType
    # Shelf::bag, Room::shelf, and Bag's n / name / items.
    bag_f: th.THIRFieldIdentity
    shelf_f: th.THIRFieldIdentity
    n: th.THIRFieldIdentity
    name: th.THIRFieldIdentity
    items: th.THIRFieldIdentity
    view: object


@pytest.fixture
def nested(program: _Program) -> _Nested:
    shelf = _free(program, "shelf_count").params[0].borrowed_record.type
    room = _free(program, "room_count").params[0].borrowed_record.type
    bag = _bag(program)
    fields = {f.name: f for f in _bag_fields(program)}
    return _Nested(shelf, room, bag, th.THIRFieldIdentity(shelf, "bag", bag), th.THIRFieldIdentity(room, "shelf", shelf),
                   fields["n"], fields["name"], fields["items"],
                   _definition(program, "Bag.tag").resolved_callee.signature.return_type)


def _record_summary(param: NominalType, readonly: bool, *, ret=None, borrowed=None, writes=(), returns=()
                    ) -> MIRCallSummary:
    """A hand-built free function over one borrowed record parameter."""
    ret = ret if ret is not None else VoidType()
    passing = param.param_passing(readonly)
    signature = th.THIRCallableSignature((param,), ret, borrowed, (passing,), return_representation(ret))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("main", "nested"), signature)
    binding = bound(param, passing, ref=th.THIRBorrowedRecord(param, readonly))
    return MIRCallSummary(callee, (binding,), frozenset({0}), frozenset(MIRParameterWrite(0, p) for p in writes),
                          frozenset(), frozenset(MIRReturnOrigin(0, p) for p in returns), frozenset(), True)


def _record_result(typ: NominalType, readonly: bool) -> dict:
    """A record result by reference: its access is its declared type's."""
    return {"ret": RefType(ReadonlyType(typ) if readonly else typ)}


def _field_holders(walked) -> list[NominalType]:
    return [hop.holder.type for hop in walked.hops if isinstance(hop.projection, MIRField)]


def _retyped(layouts: MIRRecords, record: NominalType, name: str, typ) -> MIRRecords:
    """`layouts` with `record`'s field `name` declared at `typ` (added when missing)."""
    layout = layouts[record]
    kept = tuple(f for f in layout.fields if f.id.name != name)
    return as_declared(layouts, replace(layout, fields=(*kept, MIRField(MIRFieldId(record, name), typ))))


def _redeclared(field: th.THIRFieldIdentity) -> MIRRecords:
    """No layout, the declared fields with `field` declared at its type."""
    declared = no_layouts()
    owner = field.owner
    kept = frozenset(f for f in declared.fields[owner] if f.id.name != field.name)
    changed = kept | {MIRField(MIRFieldId(owner, field.name), field.type)}
    return MIRRecords({}, Redeclared(declared.fields, lambda typ, fields: changed if typ == owner else fields))


def test_a_path_walks_from_the_storage_each_field_is_read_from(program: _Program, nested: _Nested) -> None:
    layouts = program.definitions.layouts
    room = _record_summary(nested.room, False).parameters
    deep = (nested.shelf_f, nested.bag_f, nested.items, MIRContainerStructure())
    resolved = resolve_path(0, deep, room, layouts)
    assert _field_holders(resolved) == [nested.room, nested.shelf, nested.bag]
    # A trailing region is terminal: its hop's holder is the container.
    assert resolved.hops[-1].projection == MIRContainerStructure() and resolved.hops[-1].holder.type == INTS
    # At a call, the walk starts at the record the argument binds (a descendant's storage).
    other = NominalType("Wing", _module_qname="main.Wing")
    wing = replace(layouts[nested.room], type=other, ancestors=(nested.room,))
    walked = path_walk(replace(room[0].lent_record, type=other), (nested.shelf_f,), as_declared(layouts, wing))
    assert walked.hops[1].holder.type == other
    # A region is a container's: a record parameter has none.
    assert resolve_path(0, (MIRContainerStructure(),), room, layouts) == "element projection needs container storage"
    # A field is read from the previous field's member: one its layout does not carry is refused.
    stray = (nested.shelf_f, nested.shelf_f)
    assert resolve_path(0, stray, room, layouts) == "field missing from declared fields"
    assert summary_problem(_record_summary(nested.room, True, **_record_result(nested.shelf, True),
                                           returns=[stray]), layouts) == "field missing from declared fields"
    shelf = _record_summary(nested.shelf, False).parameters
    for malformed, reason in (((nested.n, nested.bag_f), "field missing from declared fields"),
                              ((nested.bag_f, nested.items, nested.n), "field needs record storage"),
                              ((MIRContainerElements(), nested.bag_f), "element projection needs container storage"),
                              ((MIRContainerStructure(), MIRContainerElements()),
                               "element projection needs container storage")):
        assert resolve_path(0, malformed, shelf, layouts) == reason, malformed
    # Fields need record storage: the same step refuses one under a container parameter.
    assert resolve_path(0, (nested.items,), (bound(INTS, ParamPassing.MUT_REF),), layouts) == (
        "field needs record storage")


def test_the_access_at_a_path_end_accumulates(program: _Program, nested: _Nested) -> None:
    layouts = program.definitions.layouts
    parameters = _record_summary(nested.shelf, False).parameters

    def readonly(path, params=parameters, laid=layouts) -> bool:
        return resolve_path(0, path, params, laid).end.readonly
    assert not readonly((nested.bag_f, nested.n))
    frozen_bag = replace(nested.bag_f, type=ReadonlyType(nested.bag))
    frozen_items = replace(nested.items, type=ReadonlyType(INTS))
    assert readonly((frozen_bag, nested.n), laid=_retyped(layouts, nested.shelf, "bag", frozen_bag.type))
    assert readonly((nested.bag_f, frozen_items), laid=_retyped(layouts, nested.bag, "items", frozen_items.type))
    resolved = resolve_path(0, (nested.bag_f, nested.items, MIRContainerElements()),
                            _record_summary(nested.shelf, True).parameters, layouts)
    assert resolved.hops[-1].holder.readonly and _field_holders(resolved) == [nested.shelf, nested.bag]
    assert resolved.hops[-1].projection == MIRContainerElements()


def test_an_element_write_keys_on_its_containers_access(program: _Program, nested: _Nested) -> None:
    # `(Bag::frozen, elements)` with `frozen: list[readonly[Inner]]`: the
    # elements are readonly, the container is mutable, so replacing an
    # element is admitted -- by the summary endpoint and by the full walk.
    inner = unwrap_ref_type(_free(program, "inner_of").params[1].type)
    frozen = NominalType("list", (ReadonlyType(inner),), _module_qname="builtins.list")
    laid = _retyped(program.definitions.layouts, nested.bag, "frozen", frozen)
    field = th.THIRFieldIdentity(nested.bag, "frozen", frozen)
    binding, = _record_summary(nested.bag, False).parameters
    resolved = resolve_path(0, (field, MIRContainerElements()), (binding,), laid)
    assert not resolved.hops[-1].holder.readonly and endpoint_admitted(binding, resolved)
    element = walk(binding.lent_record, (MIRDeref(), path_step(field), MIRContainerElements()), laid)
    assert element.end.type == inner and element.end.readonly and store_problem(element) is None


def test_a_reference_typed_field_is_no_member_the_walk_reaches(program: _Program, nested: _Nested) -> None:
    # No field is reference-typed: the walk refuses one.
    ref = th.THIRFieldIdentity(nested.shelf, "bag", RefType(ReadonlyType(nested.bag)))
    laid = _retyped(program.definitions.layouts, nested.shelf, "bag", ref.type)
    assert resolve_path(0, (ref, nested.n), _record_summary(nested.shelf, False).parameters, laid) == (
        "unsupported field type")


def test_nested_write_paths(program: _Program, nested: _Nested) -> None:
    structure = MIRContainerStructure()
    for path in (
        (nested.bag_f, nested.n),                   # a scalar of a member, written in place
        (nested.bag_f, nested.name),                # an owned leaf of a member, its buffer replaced
        (nested.bag_f,),                            # the member replaced whole
        (nested.bag_f, nested.items, structure),    # a container field of a member
        (nested.bag_f, nested.items),               # a container field replaced whole, as a record member is
    ):
        assert summary_problem(_record_summary(nested.shelf, False, writes=[path]), no_layouts()) is None, path
    for path in ((nested.shelf_f, nested.bag_f, nested.n), (nested.shelf_f, nested.bag_f), (nested.shelf_f,)):
        assert summary_problem(_record_summary(nested.room, False, writes=[path]), no_layouts()) is None, path
    frozen_bag = replace(nested.bag_f, type=ReadonlyType(nested.bag))
    for param, readonly, path, reason in (
        # A field the holder does not declare, a field after a projection, under a container.
        (nested.shelf, False, (nested.n, nested.bag_f), "field missing from declared fields"),
        (nested.shelf, False, (MIRContainerElements(), nested.bag_f), "element projection needs container storage"),
        (nested.shelf, False, (nested.bag_f, nested.items, nested.n), "field needs record storage"),
        # Never through a readonly receiver or a `readonly[...]` member.
        (nested.shelf, True, (nested.bag_f, nested.n), "unsupported call write field or access"),
        (nested.shelf, False, (frozen_bag, nested.n), "unsupported call write field or access"),
        (nested.shelf, False, (frozen_bag,), "unsupported call write field or access"),
        # A first field whose owner is no record: no member the walk reaches.
        (nested.shelf, False, (th.THIRFieldIdentity(INT32, "bag", nested.bag), nested.n),
         "field missing from declared fields"),
        # A projection names a container's elements or structure, never a record's.
        (nested.shelf, False, (nested.bag_f, structure), "element projection needs container storage"),
    ):
        records = _redeclared(frozen_bag) if frozen_bag in path else no_layouts()
        assert summary_problem(_record_summary(param, readonly, writes=[path]), records) == reason, path


def test_a_field_is_named_by_a_string(program: _Program, nested: _Nested) -> None:
    # With no layout to read, the holder's own field is still named by a
    # string: an identity with another name is no field of it.
    for name in (123, ""):
        nameless = th.THIRFieldIdentity(nested.shelf, name, INT32)
        assert summary_problem(_record_summary(nested.shelf, False, writes=[(nameless,)]), no_layouts()) == (
            "field owner mismatch"), name


def test_nested_return_origins(program: _Program, nested: _Nested) -> None:
    for readonly, result_readonly, param, path, result in (
        # `return self.bag` at a readonly and at a mutable receiver.
        (True, True, nested.shelf, (nested.bag_f,), nested.bag),
        (False, False, nested.shelf, (nested.bag_f,), nested.bag),
        (False, True, nested.shelf, (nested.bag_f,), nested.bag),
        # `return self.shelf.bag`, two members down.
        (False, False, nested.room, (nested.shelf_f, nested.bag_f), nested.bag),
    ):
        summary = _record_summary(param, readonly, **_record_result(result, result_readonly), returns=[path])
        assert summary_problem(summary, no_layouts()) is None, path
    container = _record_summary(nested.shelf, False, ret=RefType(INTS), returns=[(nested.bag_f, nested.items)])
    assert summary_problem(container, no_layouts()) is None
    view = _record_summary(nested.shelf, True, ret=nested.view, returns=[(nested.bag_f, nested.name)])
    assert summary_problem(view, no_layouts()) is None
    frozen_bag = replace(nested.bag_f, type=ReadonlyType(nested.bag))
    assert summary_problem(_record_summary(nested.shelf, False, **_record_result(nested.bag, True),
                                           returns=[(frozen_bag,)]), _redeclared(frozen_bag)) is None
    for readonly, result_readonly, param, path, result, reason in (
        # A record member of another type than the result's.
        (False, False, nested.room, (nested.shelf_f,), nested.bag, "unsupported return origin type or access"),
        # A mutable result through a readonly receiver or a readonly member.
        (True, False, nested.shelf, (nested.bag_f,), nested.bag, "unsupported return origin type or access"),
        (False, False, nested.shelf, (frozen_bag,), nested.bag, "unsupported return origin type or access"),
        (False, False, nested.room, (replace(nested.shelf_f, type=ReadonlyType(nested.shelf)), nested.bag_f),
         nested.bag, "unsupported return origin type or access"),
        # A projection ends no return path.
        (False, False, nested.shelf, (nested.bag_f, MIRContainerStructure()), nested.bag,
         "element projection needs container storage"),
        # A field the holder does not declare, a field after a projection.
        (False, False, nested.shelf, (nested.n, nested.bag_f), nested.bag, "field missing from declared fields"),
        (False, False, nested.shelf, (MIRContainerElements(), nested.bag_f), nested.bag,
         "element projection needs container storage"),
    ):
        summary = _record_summary(param, readonly, **_record_result(result, result_readonly), returns=[path])
        frozen = next((f for f in path if isinstance(f, th.THIRFieldIdentity) and isinstance(f.type, ReadonlyType)),
                      None)
        assert summary_problem(summary, no_layouts() if frozen is None else _redeclared(frozen)) == reason, path


def test_one_endpoint_table_serves_writes_and_returns(program: _Program, nested: _Nested) -> None:
    binding, = _record_summary(nested.shelf, False).parameters
    member = resolve_path(0, (nested.bag_f,), (binding,), program.definitions.layouts)
    assert endpoint_admitted(binding, member)
    assert endpoint_admitted(binding, member, th.THIRBorrowedRecord(nested.bag, False))
    assert not endpoint_admitted(binding, member, th.THIRBorrowedRecord(nested.shelf, False))
    frozen, = _record_summary(nested.shelf, True).parameters
    member = resolve_path(0, (nested.bag_f,), (frozen,), program.definitions.layouts)
    assert not endpoint_admitted(frozen, member)
    assert endpoint_admitted(frozen, member, th.THIRBorrowedRecord(nested.bag, True))
    assert not endpoint_admitted(frozen, member, th.THIRBorrowedRecord(nested.bag, False))


def test_one_return_endpoint_rule_serves_the_summary_and_the_caller(program: _Program, nested: _Nested) -> None:
    # `return_endpoint_problem` is the rule the summary's endpoint and
    # every caller's type check read: a region, a mismatched place, a view
    # of no leaf, and a place at or under an Optional record field's
    # payload (whatever produced the referent) end no published return.
    inner = unwrap_ref_type(_free(program, "inner_of").params[1].type)
    maybe = th.THIRFieldIdentity(nested.shelf, "maybe", OptionalType(inner))
    x = th.THIRFieldIdentity(inner, "x", INT32)
    payload = MIRUnionPayload(1)
    assert return_endpoint_problem((nested.bag_f, nested.items), lambda: INTS, INTS) is None
    assert return_endpoint_problem((nested.bag_f, nested.name), lambda: STR, nested.view) is None
    for path, end, result, reason in (
            ((nested.bag_f, nested.items, MIRContainerElements()), None, INTS, "call result of a container region"),
            ((nested.bag_f, nested.n), INT32, nested.bag, "call result of a mismatched place"),
            ((nested.bag_f, nested.items), INTS, nested.view, "view result of a non-leaf place"),
            ((maybe, payload), inner, inner, "call result under an Optional field's payload"),
            ((maybe, payload, x), INT32, nested.view, "call result under an Optional field's payload")):
        assert return_endpoint_problem(path, lambda: end, result) == reason, path
    # The summary refuses the same payload return, so no caller sees it.
    laid = _retyped(program.definitions.layouts, nested.shelf, "maybe", maybe.type)
    binding, = _record_summary(nested.shelf, False).parameters
    walked = resolve_path(0, (maybe, payload), (binding,), laid)
    assert walked.end.type == inner
    assert not endpoint_admitted(binding, walked, th.THIRBorrowedRecord(inner, False))


def test_dump_spells_tuple_and_payload_steps() -> None:
    assert _write_step(MIRTupleIndex(1)) == "[1]" and _write_step(MIRUnionPayload(1)) == ".alternative[1]"
    assert _write_step(MIRContainerElements()) == "[elements]"


def test_an_owned_record_parameter_is_handed_over(program: _Program, nested: _Nested) -> None:
    for record in (nested.bag, nested.shelf):
        typ = OwnType(record)
        handed = bound(typ, typ.param_passing(False))
        assert handed.declared == typ
        assert parameter_contract_problem(handed) is None, record
        # Owned storage is never readonly and never a borrow of the caller's (a POINTER on `Own[R]`).
        assert parameter_contract_problem(reaccess(handed, True)) == (
            "readonly owned storage")
        borrowed = replace(handed, value=replace(handed.value, holding=MIRHolding.POINTER))
        assert parameter_contract_problem(borrowed) == (
            "reference type mismatch")


def test_dump_spells_every_field_of_a_path(program: _Program, nested: _Nested) -> None:
    summary = _record_summary(nested.room, False, writes=[(nested.shelf_f, nested.bag_f, nested.n)],
                              **_record_result(nested.bag, False), returns=[(nested.shelf_f, nested.bag_f)])
    text = _dump_call(MIRCall(summary, (MIRSlotId(MIRBodyId("main", "dump"), 0),), True))
    assert "writes={param0.shelf.bag.n}" in text and "returns={param0.shelf.bag}" in text


# --- the caller side: the origin's place, resolved like a direct borrow -------------

def test_a_getter_result_has_the_direct_alias_referent(program: _Program) -> None:
    getter, direct = _lowered(program, "via_getter"), _lowered(program, "via_field")

    def shape(fn: MIRFunction, name: str) -> set[tuple]:
        slots = {s.id: s for s in fn.slots}
        return {(slots[r.place.root].name, r.place.projections, r.external) for r in _end_referents(fn, name)}
    referents = shape(getter, "xs")
    assert referents == shape(direct, "ys")
    (root, projections, external), = referents
    assert root == "b" and external and [p.id.name for p in projections] == ["items"]


def test_call_return_problem_checks_the_endpoint_type(program: _Program) -> None:
    fn = _lowered(program, "via_getter")
    stmt = next(s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    slots = {s.id: s for s in fn.slots}
    deps = prepared(fn).dependencies
    point = next(MIRPoint(b.id, i) for b in fn.blocks for i, s in enumerate(b.statements) if s is stmt)
    state = deps.referents[point]
    holder = slots[stmt.target.root]
    records = fn.record_layouts
    assert call_return_problem(_present(stmt.value), holder, state, slots, records) is None
    n = _field(program, "n", INT32)
    scalar = replace(stmt.value, summary=replace(stmt.value.summary, returns=frozenset({MIRReturnOrigin(0, (n,))})))
    assert call_return_problem(_present(scalar), holder, state, slots, records) == "call result of a mismatched place"
    whole = replace(stmt.value, summary=replace(stmt.value.summary, returns=frozenset({MIRReturnOrigin(0)})))
    assert call_return_problem(_present(whole), holder, state, slots, records) == "container result of a non-container argument"


def _present(call: MIRCall) -> MIRPresentCall:
    """`call` with no argument proven absent: its whole summary."""
    return MIRPresentCall(call, call.summary, ())


def _call_site(program: _Program, name: str) -> tuple[MIRAssign, MIRSlot, dict, dict, dict]:
    """The first call assigned to a holder in `name`, its holder, and the referents before it."""
    fn = _lowered(program, name)
    slots = {s.id: s for s in fn.slots}
    point, stmt = next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                       if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) and s.value.summary.returns)
    state = prepared(fn).dependencies.referents[point]
    return stmt, slots[stmt.target.root], state, slots, fn.record_layouts


def _returning(call: MIRCall, *path: th.THIRFieldIdentity | MIRContainerStructure | MIRContainerElements) -> MIRCall:
    return replace(call, summary=replace(call.summary, returns=frozenset({MIRReturnOrigin(0, path)})))


def test_call_return_problem_checks_record_and_view_endpoints(program: _Program) -> None:
    """Hand-built origins: `summary_problem` refuses each before any caller sees it."""
    items, n = _field(program, "items", INTS), _field(program, "n", INT32)
    view, holder, state, slots, records = _call_site(program, "view_across")
    assert call_return_problem(_present(view.value), holder, state, slots, records) is None
    assert call_return_problem(_present(_returning(view.value, items)), holder, state, slots, records) == (
        "view result of a non-leaf place")
    assert call_return_problem(_present(_returning(view.value, items, MIRContainerElements())), holder, state, slots,
                               records) == (
        "call result of a container region")
    record, holder, state, slots, records = _call_site(program, "write_through_me")
    assert call_return_problem(_present(record.value), holder, state, slots, records) is None
    assert call_return_problem(_present(_returning(record.value, n)), holder, state, slots, records) == (
        "call result of a mismatched place")


def test_a_return_field_outside_the_certified_layout_is_opaque(program: _Program) -> None:
    """Hand-built definitions: the body lowers against the layout it is summarized with."""
    identity = _definition(program, "Bag.elems").resolved_callee.identity
    body, fn = program.workspace.definitions[identity]
    lowered = workspace_functions(program.workspace)[body]
    assert summarize_function(fn, lowered, prepared(lowered), program.definitions).state is MIRSummaryState.KNOWN
    bag = program.definitions.get(None, _bag(program))
    fewer = replace(bag, layout=replace(bag.layout, fields=tuple(
        f for f in bag.layout.fields if f.id.name != "items")))
    definitions = object.__new__(MIRDefinitions)
    object.__setattr__(definitions, "records", {**program.definitions.records, _bag(program): fewer})
    object.__setattr__(definitions, "layouts", as_declared(program.definitions.layouts, fewer.layout))
    assert summarize_function(fn, lowered, prepared(lowered), definitions).reason == (
        "summary unsupported return origin: field missing from declared fields")


def test_a_return_origin_inside_a_record_is_read_from_the_parameters_slot(
        program: _Program, monkeypatch: pytest.MonkeyPatch) -> None:
    # Whether a result returns as the whole parameter is what the
    # parameter's slot lends (`lent_record`): a slot lending none spells a
    # region-only path as the whole parameter; any other path is the walk
    # itself, published as it is.
    identity = _definition(program, "Bag.elems").resolved_callee.identity
    body, fn = program.workspace.definitions[identity]
    lowered = workspace_functions(program.workspace)[body]
    receiver = next(s for s in lowered.slots if s.name == "self").shape
    asked: list[MIRShape] = []

    def lends_nothing(shape: MIRShape) -> MIRShape | None:
        asked.append(shape)
        return None
    known = summarize_function(fn, lowered, prepared(lowered), program.definitions)
    assert known.state is MIRSummaryState.KNOWN and all(origin.path for origin in known.summary.returns)
    monkeypatch.setattr(summaries, "lent_record", lends_nothing)
    assert summarize_function(fn, lowered, prepared(lowered), program.definitions) == known
    assert asked == [receiver]


def test_an_optional_record_parameter_returns_inside_its_payload(program: _Program) -> None:
    # `b: Box | None` lends its payload record: a field path into it is a
    # record origin, checked as a borrowed record parameter's is
    # (`return_origin_problem`, the body's layouts), never the whole parameter.
    fn = _free(program, "inner_of")
    result = program.workspace.summaries[fn.resolved_callee.identity]
    assert result.state is MIRSummaryState.KNOWN, result.reason
    box = fn.params[0].optional_layout.payload.type
    inner = th.THIRFieldIdentity(box, "inner", unwrap_ref_type(fn.params[1].type))
    assert result.summary.returns == {MIRReturnOrigin(0, (inner,)), MIRReturnOrigin(1)}
    parameters, borrowed = result.summary.parameters, call_result(fn.resolved_callee, False)
    layouts = program.definitions.layouts
    assert return_origin_problem(MIRReturnOrigin(0, (inner,)), borrowed, parameters, layouts) is None
    # The empty path is the payload record itself: an `Inner` result never reaches it.
    assert return_origin_problem(MIRReturnOrigin(0), borrowed, parameters, layouts) == (
        "unsupported return origin type or access")
    assert return_origin_problem(MIRReturnOrigin(0), th.THIRBorrowedRecord(box, True), parameters, layouts) is None
    # A field outside the payload's layout keeps the summary opaque.
    body, declaration = program.workspace.definitions[fn.resolved_callee.identity]
    lowered = workspace_functions(program.workspace)[body]
    definition = program.definitions.get(None, box)
    fewer = replace(definition, layout=replace(definition.layout, fields=tuple(
        f for f in definition.layout.fields if f.id.name != "inner")))
    definitions = object.__new__(MIRDefinitions)
    object.__setattr__(definitions, "records", {**program.definitions.records, box: fewer})
    object.__setattr__(definitions, "layouts", as_declared(program.definitions.layouts, fewer.layout))
    assert summarize_function(declaration, lowered, prepared(lowered), definitions).reason == (
        "summary unsupported return origin: field missing from declared fields")


def test_a_container_result_may_be_more_readonly_than_its_type(program: _Program) -> None:
    # Returned by reference (its published representation), at the bare type.
    ref = Representation.REFERENCE
    mutable, frozen = INTS, ReadonlyType(INTS)
    assert result_problem(mutable, th.THIRBorrowedRecord(INTS, False), ref) is None
    assert result_problem(mutable, th.THIRBorrowedRecord(INTS, True), ref) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(INTS, True), ref) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(INTS, False), ref) == "invalid borrowed result"
    strs = NominalType("list", (STR,), _module_qname="builtins.list")
    assert result_problem(mutable, th.THIRBorrowedRecord(strs, False), ref) == "invalid borrowed result"
    # A nested container has no region MIR models: refused, never raised.
    nested = NominalType("list", (INTS,), _module_qname="builtins.list")
    assert result_problem(nested, th.THIRBorrowedRecord(nested, False), ref) == "invalid borrowed result"
    # A container returned as storage borrows nothing.
    assert result_problem(mutable, th.THIRBorrowedRecord(INTS, False), Representation.STORAGE) == (
        "invalid borrowed result")


def test_a_record_result_declared_readonly_is_never_lent_mutably(program: _Program) -> None:
    # The record's shape check refuses it: no guard of its own.
    bag = _bag(program)
    ref = Representation.REFERENCE
    frozen = ReadonlyType(bag)
    assert result_problem(bag, th.THIRBorrowedRecord(bag, False), ref) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(bag, True), ref) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(bag, False), ref) == "invalid borrowed result"
    # A borrowed record result is returned by reference, never as storage.
    assert result_problem(bag, th.THIRBorrowedRecord(bag, False), Representation.STORAGE) == "invalid borrowed result"


def test_a_whole_container_rooted_in_an_elements_region_is_refused() -> None:
    body = MIRBodyId("main", "region")
    layout = (leaf_shape(INT32),)
    xs = MIRSlot(MIRSlotId(body, 0), borrowed_shape(INTS, members=layout), MIRSlotKind.PARAMETER, "xs")
    ys = MIRSlot(MIRSlotId(body, 1), borrowed_shape(INTS, members=layout), MIRSlotKind.LOCAL, "ys")
    signature = th.THIRCallableSignature((INTS,), INTS, None, (ParamPassing.MUT_REF,), return_representation(INTS))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("main", "ident"), signature)
    summary = MIRCallSummary(callee, (bound(INTS, ParamPassing.MUT_REF),), frozenset({0}),
                             frozenset(), frozenset(), frozenset({MIRReturnOrigin(0)}), frozenset(), True)
    call = MIRCall(summary, (xs.id,), False)
    slots = {xs.id: xs, ys.id: ys}
    whole = {MIRPlace(xs.id): frozenset({MIRReferent(MIRPlace(xs.id), True)})}
    assert call_return_problem(_present(call), ys, whole, slots, {}) is None
    region = {MIRPlace(xs.id): frozenset({MIRReferent(MIRPlace(xs.id, (MIRContainerElements(),)), True)})}
    assert call_return_problem(_present(call), ys, region, slots, {}) == "container result rooted in an elements region"


def test_a_span_argument_is_projected_once() -> None:
    # `u = ident(tail(xs))`: the Span argument already holds xs's elements
    # region; resolving the origin's place never projects it again.
    span = NominalType("Span", (INT32,), _module_qname="tpy.Span")
    body = MIRBodyId("main", "chain")
    layout = (leaf_shape(INT32),)
    t = MIRSlot(MIRSlotId(body, 0), borrowed_shape(span, members=layout), MIRSlotKind.LOCAL, "t")
    u = MIRSlot(MIRSlotId(body, 1), borrowed_shape(span, members=layout), MIRSlotKind.LOCAL, "u")
    signature = th.THIRCallableSignature((span,), span, None, (ParamPassing.VALUE,), return_representation(span))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("main", "ident"), signature)
    xs_elements = MIRReferent(MIRPlace(MIRSlotId(MIRBodyId("main", "caller"), 0), (MIRContainerElements(),)), True)
    state = {MIRPlace(t.id): frozenset({xs_elements})}
    slots = {t.id: t, u.id: u}
    for path in ((), (MIRContainerElements(),)):
        summary = MIRCallSummary(callee, (bound(span, ParamPassing.VALUE, True),), frozenset({0}),
                                 frozenset(), frozenset(), frozenset({MIRReturnOrigin(0, path)}), frozenset(), True)
        assert resolve_call_returns(_present(MIRCall(summary, (t.id,), False)), state, slots, {}, u) == {xs_elements}


def test_dump_spells_return_paths(program: _Program) -> None:
    text = dump_function(_lowered(program, "via_getter"))
    assert "returns={param0.items}" in text
    assert "returns={param0}" in dump_function(_lowered(program, "read_readonly"))


def test_a_writing_call_is_no_container_place(program: _Program) -> None:
    # The builder reads a place's operands before its container; a call that writes could reorder them.
    assert _known(program, "Bag.grab").writes
    verdict = program.verdicts["iterate_grab"]
    assert (verdict.status, verdict.reason) == (MIRVerdictStatus.UNCOVERED, "order-sensitive eager operands")


# --- accessor and twin calls at the caller -------------------------------------------

def test_accessor_calls_lower_at_every_result_kind(program: _Program) -> None:
    for name in ("read_count", "read_label", "set_count", "iterate", "via_getter", "grow_alias"):
        verdict = program.verdicts[name]
        assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), (name, verdict.reason)
        assert not verdict.conflicts, name
    # A view of a field from a getter, live across the method replacing the field.
    assert program.verdicts["view_across"].conflicts == ("replacement",)


def test_a_readonly_receiver_binds_a_readonly_result(program: _Program) -> None:
    verdict = program.verdicts["read_readonly"]
    assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.reason
    fn = _lowered(program, "read_readonly")
    assert _slot(fn, "d").readonly
    call = next(s.value for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) and s.value.summary.returns)
    assert call.summary.result_at(True).readonly and not call.summary.result_at(False).readonly


def _free(program: _Program, name: str) -> th.THIRFunction:
    return next(fn for key, (_, fn) in program.workspace.definitions.items()
                if key.owner is None and key.name == name)


def test_a_follows_receiver_result_takes_the_receiver_access(program: _Program) -> None:
    callee = _definition(program, "Bag.view_items").resolved_callee
    assert callee.signature.result_follows_receiver
    assert call_result(callee, True).readonly and not call_result(callee, False).readonly
    # Calls at a readonly and at a mutable receiver publish the definition's one signature.
    me = _definition(program, "Bag.me").resolved_callee
    calls = [node for name in ("read_readonly", "write_through_me")
             for node in _thir_nodes(_free(program, name), th.THIRMethodCall) if node.method_cpp == "me"]
    assert len(calls) == 2 and all(call.resolved_callee == me for call in calls)


@pytest.mark.parametrize("name,receiver,readonly", [
    ("read_readonly", ParamPassing.CONST_REF, True),     # b: readonly[Bag]
    # b: Bag, inferred const (no `readonly[...]`): C++ picks the const clone. A
    # mutable local bound to the result would make `b` mutable, so the result
    # is walked in place.
    ("read_inferred", ParamPassing.CONST_REF, True),
    ("write_through_me", ParamPassing.MUT_REF, False),   # d.n = 5 writes through the mutable clone's result
])
def test_a_twin_result_is_bound_at_the_receiver_binding(program: _Program, name: str, receiver: ParamPassing,
                                                        readonly: bool) -> None:
    assert _free(program, name).resolved_callee.signature.passings[0] is receiver
    verdict = program.verdicts[name]
    assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.reason
    _, holder, _, _, _ = _call_site(program, name)
    assert holder.readonly is readonly


@pytest.mark.parametrize("name", ["read_readonly", "read_inferred", "write_through_me"])
def test_a_receiver_access_unlike_its_binding_is_refused(program: _Program, name: str) -> None:
    fn = _free(program, name)
    body = MIRBodyId("main", name)
    assert isinstance(lower_function(fn, body, definitions=program.definitions,
                                     summaries=program.workspace.summaries), MIRFunction)
    calls = _thir_nodes(fn, th.THIRMethodCall)
    assert calls
    for call in calls:
        # The call's fact is the access C++ picks the overload by; a binding
        # MIR reads otherwise is no evidence of either, so it never widens.
        flipped = replace(call.receiver_access, readonly=not call.receiver_access.readonly)
        result = lower_function(_replace_node(fn, call, replace(call, receiver_access=flipped)), body,
                                definitions=program.definitions, summaries=program.workspace.summaries)
        assert isinstance(result, MIRNotCovered) and result.reason == "call receiver access disagrees with its binding"


# --- twins ---------------------------------------------------------------------------

def test_twin_bodies_summarize_alike(program: _Program) -> None:
    for name in ("Bag.elems", "Bag.view_items", "Bag.me"):
        identity = _definition(program, name).resolved_callee.identity
        assert identity in program.workspace.twins, name
        assert program.workspace.summaries[identity].state is MIRSummaryState.KNOWN, name


def test_a_differing_twin_makes_the_summary_opaque(program: _Program) -> None:
    # A hand-built twin of `view_items` whose body returns another field.
    definition = _definition(program, "Bag.view_items")
    identity = definition.resolved_callee.identity
    other = _definition(program, "Bag.other_items")
    fake = replace(other, resolved_callee=definition.resolved_callee, access_twin=True,
                   receiver=replace(other.receiver, readonly=False))
    functions = [(body, fn) for body, fn in program.workspace.definitions.values()]
    functions.insert(0, (MIRBodyId("main", "fake_twin"), fake))
    workspace = analyze_call_workspace(tuple(functions), program.definitions)
    assert workspace.summaries[identity] == workspace.summaries[identity].opaque("twin bodies differ")
    # The real twin agrees.
    functions[0] = program.workspace.twins[identity]
    workspace = analyze_call_workspace(tuple(functions), program.definitions)
    assert workspace.summaries[identity].state is MIRSummaryState.KNOWN


# --- stubs ---------------------------------------------------------------------------

def test_stub_summaries_keep_whole_argument_origins(program: _Program) -> None:
    fn = _lowered(program, "read_label")
    stubs = [s for s in fn.call_summaries if isinstance(s.callee, th.THIRStubCallee)]
    assert stubs
    for summary in stubs:
        assert summary == stub_summary(summary.callee) and summary_problem(summary, no_layouts()) is None
        assert all(not origin.path for origin in summary.returns)

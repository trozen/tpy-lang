"""One rule for record and container bindings: a local's value is its
declared type at its declaration's access; whether it owns its storage is
THIR's slot decision; an alias is chosen by its source; a rebind is a
positive fact. Records and containers lower alike."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..parse import RebindStorage
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import Representation
from .call_contract import call_result
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRConstructorDefinition, MIRDefinitions
from .lower import lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBorrow, MIRConstruct, MIRFunction, MIRNotCovered, MIRPlace, MIRStorageWrite,
    MIRStorageWriteMode, MIRSlotKind,
)
from .shapes import MIRContainerElements, MIRDeref
from .value import MIRField

SOURCE = '''\
from tpy import int32, auto_readonly, readonly, Own


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Child(Base):
    def bump(self) -> None:
        self.n += 1


class Shadow(Base):
    n: int32

    def bump(self) -> None:
        self.n += 1


class Box:
    r: R
    items: list[int32]

    def __init__(self) -> None:
        self.r = R(1)
        self.items = [1]

    @auto_readonly
    def rec(self) -> R:
        return self.r

    @auto_readonly
    def lst(self) -> list[int32]:
        return self.items


def total(rows: list[readonly[R]]) -> int32:
    n = 0
    for r in rows:
        n += r.x
    return n


def pick(c: bool, a: list[R], b: list[R]) -> int32:
    v = a if c else b
    return len(v)


def pick_record(c: bool, a: R, b: R) -> int32:
    v = a if c else b
    return v.x


def owned_pick(c: bool) -> int32:
    xs: list[R] = [R(1)]
    ys: list[R] = [R(2)]
    v = xs if c else ys
    return len(v)


def owned_pick_grows(c: bool) -> int32:
    xs: list[R] = [R(1)]
    ys: list[R] = [R(2)]
    v = xs if c else ys
    r = v[0]
    xs.append(R(5))
    return r.x


def element_reseat(xs: list[R], ys: list[R]) -> int32:
    v = xs[0]
    v = ys[0]
    return v.x


def element_reseat_grows(xs: list[R], ys: list[R]) -> int32:
    v = xs[0]
    v = ys[0]
    ys.append(R(5))
    return v.x


def element_to_name(xs: list[R], y: R) -> int32:
    v = xs[0]
    v = y
    return v.x


def element_to_name_grows(ys: list[R], y: R) -> int32:
    v = ys[0]
    v = y
    ys.append(R(5))
    return v.x


def name_to_element(x: R, ys: list[R]) -> int32:
    v = x
    v = ys[0]
    return v.x


def name_to_element_grows(x: R, ys: list[R]) -> int32:
    v = x
    v = ys[0]
    ys.append(R(5))
    return v.x


def local_alias() -> int32:
    xs: list[int32] = [1]
    ys = xs
    ys.append(2)
    return len(xs)


def owned_list() -> int32:
    xs: list[int32] = [1]
    return len(xs)


def record_alias() -> int32:
    x = R(1)
    y = x
    y.x = 2
    return x.x


def swap(xs: dict[str, int32], ys: dict[str, int32]) -> int32:
    d = xs
    d = ys
    return len(d)


def alias_grow(xs: list[R], ys: list[R]) -> int32:
    b = xs
    b[0] = R(1)
    b = ys
    return len(b)


def control(xs: list[R], ys: list[R]) -> int32:
    b = xs
    r = b[0]
    xs.append(R(5))
    return r.x


def reseat_alias(xs: list[R], ys: list[R]) -> int32:
    b = xs
    r = b[0]
    b = ys
    xs.append(R(5))
    return r.x


def loop_reseat(xs: list[R], ys: list[R]) -> int32:
    b = xs
    t = 0
    for r in b:
        b = ys
        xs.append(R(5))
        t += r.x
    return t


def branch_reseat(c: bool, xs: list[R], ys: list[R]) -> int32:
    b = xs
    r = b[0]
    if c:
        b = ys
    xs.append(R(5))
    return r.x


def reassigned_literal() -> int32:
    items: list[int32] = [1, 2, 3]
    n = len(items)
    items = [4, 5]
    return n + len(items)


def first_record(a: R) -> R:
    return a


def first_list(xs: list[int32]) -> list[int32]:
    return xs


def call_results(a: R, xs: list[int32]) -> int32:
    r = first_record(a)
    v = first_list(xs)
    return r.x + len(v)


class Bin:
    items: list[R]
    rec: R

    def __init__(self) -> None:
        self.items = [R(1)]
        self.rec = R(1)


def replace_held(b: Bin) -> int32:
    r = b.items[0]
    b.items = [R(3)]
    return r.x


def replace_released(b: Bin) -> int32:
    n = b.items[0].x
    b.items = [R(3)]
    return n


def replace_held_record(b: Bin) -> int32:
    r = b.rec
    b.rec = R(3)
    return r.x
'''


@dataclass(frozen=True)
class _Program:
    compiler: object
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program() -> _Program:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(entry)
        with compiler.mir_analysis(((entry, ctx),)) as mir:
            verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                        compiler.thir_reject_by_node, mir.workspace)
            functions = {(f"{fn.receiver.type.name}." if fn.receiver is not None else "") + node.name: fn
                         for node, fn in ctx.thir_functions.items() if not fn.access_twin}
            result = _Program(compiler, functions, mir.definitions,
                              {v.body.declaration.split("@")[0]: v for v in verdicts})
    return result


@pytest.fixture(autouse=True)
def _active(program: _Program):
    # MIR admits a record by the TypeDef its compilation registered.
    with activate_compiler(program.compiler):
        yield


def _body(program: _Program, name: str) -> MIRFunction:
    verdict = program.verdicts[name]
    assert isinstance(verdict.function, MIRFunction), verdict.reason
    return verdict.function


def _local_writes(fn: MIRFunction, name: str) -> list[object]:
    slot = next(s.id for s in fn.slots if s.name == name and s.kind is MIRSlotKind.LOCAL)
    return [stmt.value for b in fn.blocks for stmt in b.statements
            if getattr(stmt, "target", None) is not None and stmt.target.root == slot
            and not stmt.target.projections]


@pytest.mark.parametrize("name", ["total", "pick", "pick_record", "local_alias", "record_alias", "swap",
                                  "Child.bump", "Shadow.bump", "call_results", "owned_pick", "element_reseat",
                                  "element_to_name", "name_to_element"])
def test_record_and_container_bindings_lower_alike(program: _Program, name: str) -> None:
    # `list[readonly[R]]` binds by its type; a container conditional, an
    # owned container's alias and a reseated container alias lower as their
    # record twins; a derived record's field is a member it declares.
    assert program.verdicts[name].status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), (
        program.verdicts[name].reason)


def test_a_conditional_alias_holds_either_arm(program: _Program) -> None:
    # `v = a if c else b` over containers, as over records: one holder, aliased in each arm.
    for name in ("pick", "pick_record"):
        fn = _body(program, name)
        arms = {v.source for v in (stmt.value for b in fn.blocks for stmt in b.statements)
                if isinstance(v, MIRAlias)}
        params = {s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.name in ("a", "b")}
        assert params <= arms, name


def test_a_conditional_arm_is_bound_by_its_holding(program: _Program) -> None:
    # `v = xs if c else ys` over owned container locals: each arm names the
    # local's holder over its own storage, aliased as a record name source
    # is; the holder aliases the arm. Growing an arm's container while an
    # element bound through the holder is live keeps the retention conflict.
    fn = _body(program, "owned_pick")
    holders = {s.id for s in fn.slots if s.name in ("xs", "ys") and s.kind is MIRSlotKind.LOCAL}
    # Every arm alias, `v`'s own alias of the chosen arm aside, names a holder.
    arms = {stmt.value.source for b in fn.blocks for stmt in b.statements
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRAlias)
            and fn.slots[stmt.target.root.index].name != "v"}
    assert holders and all(fn.slots[h.index].shape.is_pointer for h in holders)
    assert holders == arms
    assert [type(v) for v in _local_writes(fn, "v")] == [MIRAlias]
    assert "replacement" in program.verdicts["owned_pick_grows"].conflicts


def test_an_element_source_binds_a_rebind_as_a_declaration(program: _Program) -> None:
    # `v = ys[0]` rebinding a holder borrows the element region in place,
    # as the declaration `v = xs[0]` does; any other source rebinds it as
    # its own kind. The element bound last is the one growth invalidates.
    for name, kinds in (("element_reseat", [MIRBorrow, MIRBorrow]), ("element_to_name", [MIRBorrow, MIRAlias]),
                        ("name_to_element", [MIRAlias, MIRBorrow])):
        writes = _local_writes(_body(program, name), "v")
        assert [type(v) for v in writes] == kinds, name
        assert all(v.source.projections[-1:] == (MIRContainerElements(),) for v in writes if isinstance(v, MIRBorrow))
    for name in ("element_reseat_grows", "name_to_element_grows"):
        assert "replacement" in program.verdicts[name].conflicts, name
    # Rebinding to a name moves the loan off the element: growth after it conflicts with nothing.
    moved = program.verdicts["element_to_name_grows"]
    assert isinstance(moved.function, MIRFunction), moved.reason
    assert "replacement" not in moved.conflicts


def test_a_shadowed_field_is_a_member_its_record_declares(program: _Program) -> None:
    # `Shadow` redeclares `n` and has no verified definition of its own:
    # `self.n` is the field Shadow declares, a member of its declared fields.
    fn = _body(program, "Shadow.bump")
    receiver = next(s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    assert not isinstance(program.definitions.records.get(receiver.type), MIRConstructorDefinition)
    owners = {p.id.owner.name for b in fn.blocks for stmt in b.statements
              for p in getattr(getattr(stmt, "target", None), "projections", ()) if isinstance(p, MIRField)}
    assert owners == {"Shadow"}
    declared = {(f.id.owner.name, f.id.name) for f in program.definitions.fields[receiver.type]}
    assert declared == {("Base", "n"), ("Shadow", "n")}


def test_mir_types_are_bare(program: _Program) -> None:
    # Borrowing is a binding's passing or the published return
    # representation: no type MIR holds spells it, though THIR's
    # by-reference parameters and results here are `Ref[...]`.
    assert any("RefType(" in repr(p.type) for fn in program.functions.values() for p in fn.params)
    lowered = [v.function for v in program.verdicts.values() if isinstance(v.function, MIRFunction)]
    assert {"first_record", "first_list", "Box.rec"} <= {
        name for name, v in program.verdicts.items() if isinstance(v.function, MIRFunction)}
    for fn in lowered:
        types = [fn.return_type, *(b.declared for b in fn.parameter_bindings),
                 *(b.type for b in fn.parameter_bindings),
                 *([fn.borrowed_result.type] if fn.borrowed_result is not None else [])]
        shapes = [s.shape for s in fn.slots]
        while shapes:
            shape = shapes.pop()
            if shape is not None:
                types.append(shape.type)
                shapes.extend(shape.members)
        assert not [t for t in types if "RefType(" in repr(t)], fn.id


def test_a_name_source_is_aliased_by_its_holding(program: _Program) -> None:
    # An owned record or container local is a holder over its backing: its
    # alias aliases the holder.
    record = _body(program, "record_alias")
    assert [type(v) for v in _local_writes(record, "y")] == [MIRAlias]
    container = _body(program, "local_alias")
    assert [type(v) for v in _local_writes(container, "ys")] == [MIRAlias]


def test_a_reseated_container_holder_keeps_its_retention_conflict(program: _Program) -> None:
    # A holder reseat is followed flow-sensitively: the element borrowed
    # through the old referent is still invalidated by the old container's
    # growth -- straight-line, inside a loop iterating the holder, and when
    # the reseat is on one branch only.
    assert "replacement" in program.verdicts["control"].conflicts
    for name in ("reseat_alias", "loop_reseat", "branch_reseat"):
        verdict = program.verdicts[name]
        assert isinstance(verdict.function, MIRFunction), verdict.reason
        assert "replacement" in verdict.conflicts, name


def test_a_rebind_is_a_positive_fact(program: _Program) -> None:
    # `d = ys` is a pointer local's rebind; the same statement spelled as an
    # assignment to the bare holder name is the same reseat where THIR's
    # binding says the slot is a pointer. One to a reference holder (`T&`),
    # or through the holder's pointer, is no reseat at all.
    fn = program.functions["swap"]
    decl, rebind, ret = fn.body
    assert isinstance(rebind, th.THIRPtrLocalRebind) and rebind.binding.pointer
    target = th.THIRName(rebind.value.result_type, "d", form=th.Form.BORROW)
    expected = lower_function(fn, MIRBodyId("locals", "swap"), definitions=program.definitions)
    assert isinstance(expected, MIRFunction), expected
    assigned = lower_function(replace(fn, body=(decl, th.THIRAssign(target, rebind.value, binding=rebind.binding),
                                                ret)), MIRBodyId("locals", "swap"), definitions=program.definitions)
    assert isinstance(assigned, MIRFunction), assigned
    assert [type(v) for v in _local_writes(assigned, "d")] == [type(v) for v in _local_writes(expected, "d")] == [
        MIRAlias, MIRAlias]
    through = replace(fn, body=(decl, th.THIRAssign(replace(target, deref=True), rebind.value,
                                                    binding=rebind.binding), ret))
    result = lower_function(through, MIRBodyId("locals", "swap"), definitions=program.definitions)
    assert isinstance(result, MIRNotCovered) and result.reason == "assignment through a holder"
    reference = replace(rebind.binding, pointer=False, rebind_slot=False, ref_alias=True)
    for binding in (reference, None):
        to_reference = replace(fn, body=(decl, th.THIRAssign(target, rebind.value, binding=binding), ret))
        result = lower_function(to_reference, MIRBodyId("locals", "swap"), definitions=program.definitions)
        assert isinstance(result, MIRNotCovered) and result.reason == "assignment to a reference holder"


def test_a_whole_field_replacement_conflicts_with_a_held_member(program: _Program) -> None:
    # `b.items = [...]` replaces the whole container field, as `b.rec = ...`
    # replaces a record field: an element (or the record) bound before it
    # and read after it is retained across the replacement.
    for name in ("replace_held", "replace_held_record"):
        verdict = program.verdicts[name]
        assert isinstance(verdict.function, MIRFunction) and verdict.conflicts == ("replacement",), (
            verdict.describe())
    released = program.verdicts["replace_released"]
    assert released.conflicts == () and isinstance(released.function, MIRFunction), released.describe()


def test_a_container_alias_takes_its_access_from_the_binding(program: _Program) -> None:
    # `b = xs` reseated later is a pointer declaration: the holder's access
    # is the binding's const verdict, not the declaration's spelling. A const
    # binding makes the holder readonly, so `b[0] = ...` is refused;
    # `is_const` alone changes nothing.
    fn = program.functions["alias_grow"]
    decl, *rest = fn.body
    assert isinstance(decl, th.THIRPtrLocalDecl) and not decl.is_const and not decl.binding.const

    def lowered(forged: th.THIRStmt) -> MIRFunction | MIRNotCovered:
        return lower_function(replace(fn, body=(forged, *rest)), MIRBodyId("locals", "alias_grow"),
                              definitions=program.definitions)

    spelled = lowered(replace(decl, is_const=True))
    assert isinstance(spelled, MIRFunction), spelled
    assert not next(s for s in spelled.slots if s.name == "b").shape.readonly
    const = lowered(replace(decl, binding=replace(decl.binding, const=True)))
    assert isinstance(const, MIRNotCovered) and const.reason == "readonly element store", const


def test_an_owned_container_is_rebuilt_in_place_only(program: _Program) -> None:
    # An owned container local's C++ slot is its storage, rebuilt in place
    # through its holder, as a record holder's referent is (`IN_PLACE`); a
    # fresh referent in storage of its own (`OWN`) is refused for a fixed
    # owned local, record or container alike.
    fn = program.functions["owned_list"]
    decl, ret = fn.body
    target = th.THIRName(decl.resolved_type, "xs", form=th.Form.STORAGE)
    for storage in (None, RebindStorage.OWN, RebindStorage.IN_PLACE):
        forged = replace(fn, body=(decl, th.THIRAssign(target, decl.init, rebind_storage=storage), ret))
        result = lower_function(forged, MIRBodyId("locals", "owned_list"), definitions=program.definitions)
        if storage is RebindStorage.OWN:
            assert isinstance(result, MIRNotCovered) and result.reason == "fixed owned local reseated", storage
        else:
            assert isinstance(result, MIRFunction), result
            xs = next(s.id for s in result.slots if s.name == "xs")
            rebuilt = [stmt for b in result.blocks for stmt in b.statements
                       if isinstance(stmt, MIRAssign) and stmt.target == MIRPlace(xs, (MIRDeref(),))]
            assert [stmt.storage_write for stmt in rebuilt] == [MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, xs)]


def test_a_container_bound_to_a_temporary_owns_it(program: _Program) -> None:
    # `items = [1, 2, 3]; ...; items = [4, 5]`: a BORROW local THIR builds
    # over a rebind slot owns the fresh value it binds, as its record twin
    # does; the rebind builds the new list into storage of its own and
    # repoints the holder. `n` was read before, so nothing conflicts.
    verdict = program.verdicts["reassigned_literal"]
    fn = verdict.function
    assert isinstance(fn, MIRFunction), verdict.reason
    items = next(s.id for s in fn.slots if s.name == "items")
    writes = [stmt for b in fn.blocks for stmt in b.statements if isinstance(stmt, MIRAssign)]
    built = [stmt for stmt in writes if isinstance(stmt.value, MIRConstruct)]
    assert [stmt.storage_write.mode for stmt in built] == [MIRStorageWriteMode.INITIALIZE,
                                                           MIRStorageWriteMode.OWN_SITE]
    assert [stmt.value.source for stmt in writes if stmt.target == MIRPlace(items)] == [
        MIRPlace(stmt.target.root) for stmt in built]
    assert verdict.conflicts == ()


def test_record_and_container_call_results_derive_alike(program: _Program) -> None:
    # A result returned by reference is its declared type at the access
    # its type and receiver give: no published borrow is read.
    for name, readonly in (("first_record", False), ("first_list", False)):
        callee = program.functions[name].resolved_callee
        result = call_result(callee, None)
        assert result == th.THIRBorrowedRecord(_body(program, name).return_type, readonly), name
        assert result == call_result(replace(callee, signature=replace(callee.signature, borrowed_result=None)), None)
    # A result following its receiver takes the receiver's access, record and container alike.
    for name in ("Box.rec", "Box.lst"):
        callee = program.functions[name].resolved_callee
        assert callee.signature.result_follows_receiver, name
        for receiver in (False, True):
            result = call_result(callee, receiver)
            assert result is not None and result.readonly is receiver, (name, receiver)
    # A method stub's aggregate returned by reference binds alike, at the
    # access the stub binds its receiver at; a receiverless stub's binds
    # none (no mutable borrow of the declared type).
    append = next(stmt.expr.stub_callee for stmt in program.functions["local_alias"].body
                  if isinstance(getattr(stmt, "expr", None), th.THIRMethodCall))
    for typ in (_body(program, "first_record").return_type, _body(program, "first_list").return_type):
        for readonly in (False, True):
            stub = replace(append, readonly=(readonly, *append.readonly[1:]), signature=replace(
                append.signature, return_type=typ, return_representation=Representation.REFERENCE))
            assert call_result(stub, False) == th.THIRBorrowedRecord(typ, readonly), (typ, readonly)
            assert call_result(replace(stub, receiver=False), False) is None, (typ, readonly)

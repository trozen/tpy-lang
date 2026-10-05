"""A record returned by value (`Own[R]`): the body's own record storage moved
out, and the callers that take it as a record value."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import latch_declared_native_flags
from ..typesys import INT32, OwnType
from .call_contract import MIRSummaryState, owned_record_result, result_problem
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRConstruct, MIRContainerElements,
    MIRCopy, MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRMove, MIRNotCovered, MIRPlace, MIRRead, MIRRecordWrite,
    MIRRecordWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRValueKind,
)
from .scope_lifetime import analyze_scope_ends
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins
from .summaries import _private_records, summarize_function
from .validate import MIRValidationError, validate_function


SOURCE = '''\
from tpy import int32, Own, StrView, copy
from tpy.extern import native


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def bump(self) -> None:
        self.x += 1


class Hooked:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        print("bye")


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0


def ident(p: Point) -> Point:
    return p


def make(n: int32) -> Own[Point]:
    return Point(n, n + 1)


def make_local(n: int32) -> Own[Point]:
    p = Point(n, 0)
    p.bump()
    return p


def duplicate(p: Point) -> Own[Point]:
    return copy(p)


def pick(flag: bool, n: int32) -> Own[Point]:
    if flag:
        return Point(n, 1)
    return Point(1, n)


def forward(n: int32) -> Own[Point]:
    return make(n)


def reassigned(flag: bool, n: int32) -> Own[Point]:
    p = Point(n, 0)
    if flag:
        p = Point(0, n)
    return p


def from_borrow(p: Point) -> Own[Point]:
    return ident(p)


def from_param(p: Point) -> Own[Point]:
    return p


def make_hooked(n: int32) -> Own[Hooked]:
    return Hooked(n)


def stamp(c: Counter) -> Own[Point]:
    c.n += 1
    return Point(c.n, 0)


def use_result(n: int32) -> int32:
    p = make(n)
    p.bump()
    return p.x


def reseat(n: int32) -> int32:
    p = make(n)
    p = make(n + 1)
    return p.x


def element(n: int32) -> int32:
    ps: list[Point] = [Point(0, 0)]
    ps[0] = make(n)
    return ps[0].x


def collect(n: int32) -> int32:
    ps: list[Point] = []
    ps.append(make(n))
    return ps[0].y


def read(p: Point) -> int32:
    return p.y


def lend_temp(n: int32) -> int32:
    return read(make(n))


def stamped(c: Counter) -> int32:
    ps = [stamp(c), stamp(c)]
    return ps[1].x


def borrow_then(n: int32) -> int32:
    p = make(n)
    q = ident(p)
    return q.x


def use_temp(n: int32) -> int32:
    return make(n).x


def discard_temp(n: int32) -> None:
    make(n)


def stamp_order(c: Counter) -> int32:
    return c.n + stamp(c).x


class Named:
    name: str
    n: int32

    def __init__(self, name: str, n: int32) -> None:
        self.name = name
        self.n = n


def make_named(s: str) -> Own[Named]:
    return Named(s, 1)


def keep_named(s: str) -> int32:
    v: StrView = make_named(s).name
    return len(v)


def lend_stamp(c: Counter) -> int32:
    return read(stamp(c))


def reseat_ctor(s: str, t: str) -> int32:
    m = Named(s, 1)
    m = Named(t + "x", 2)
    return m.n


def make_fixed(n: int32) -> Own[Point]:
    p = Point(n, 0)
    return p


class Copied:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Copied]:
        return Copied(self.n)


@native("probe_make")
def native_make(n: int32) -> Own[Point]: ...


def use_native(n: int32) -> int32:
    p = native_make(n)
    return p.x


def temp_native(n: int32) -> int32:
    return native_make(n).x
'''


@dataclass(frozen=True)
class _Program:
    compiler: object
    modules: list
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
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
        yield _Program(compiler, modules, functions, mir.definitions, mir.workspace,
                       {v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.fixture
def active(program):
    for module in program.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(program.compiler):
        yield program


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _summary(program: _Program, name: str):
    return program.workspace.summaries[program.functions[name].resolved_callee.identity]


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _writes(fn: MIRFunction, value_type: type) -> list[MIRAssign]:
    return [s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and isinstance(s.value, value_type)]


def _returns(fn: MIRFunction) -> list[MIRReturn]:
    return [b.terminator for b in fn.blocks if isinstance(b.terminator, MIRReturn)]


def _initialization(fn: MIRFunction, slot: MIRSlotId) -> MIRAssign:
    return next(s for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and s.target == MIRPlace(slot) and isinstance(s.storage_write, MIRRecordWrite))


# --- the definition side --------------------------------------------------------------

def test_an_owned_record_result_is_storage_by_value(active) -> None:
    signature = active.functions["make"].resolved_callee.signature
    point = owned_record_result(signature.return_type)
    assert isinstance(signature.return_type, OwnType) and point is not None and point.name == "Point"
    assert result_problem(signature.return_type, None) is None
    # A bare record result is a borrow, never an owned record result.
    assert owned_record_result(active.functions["ident"].return_type) is None
    fn = _lowered(active, "make")
    assert fn.borrowed_result is None
    (ret,) = _returns(fn)
    slot = fn.slots[ret.value.index]
    assert slot.value_kind is MIRValueKind.OWNED and slot.type == point and not slot.readonly


def test_a_fixed_local_returns_its_own_backing(active) -> None:
    fn = _lowered(active, "make_local")
    holder = _slot(fn, "p")
    borrow = next(s for s in _writes(fn, MIRBorrow) if s.target == MIRPlace(holder.id))
    (ret,) = _returns(fn)
    # The returned slot is the backing the holder borrows, never the holder;
    # the move is C++'s, not an event of the body.
    assert ret.value == borrow.value.source.root and ret.value != holder.id
    assert not _writes(fn, MIRMove)
    assert f"return %{ret.value.index}" in dump_function(fn)


@pytest.mark.parametrize("name, value_type", [
    ("make", MIRConstruct), ("duplicate", MIRCopy), ("forward", MIRCall),
])
def test_anything_else_is_built_into_a_result_slot(active, name: str, value_type: type) -> None:
    fn = _lowered(active, name)
    (ret,) = _returns(fn)
    write = _initialization(fn, ret.value)
    assert isinstance(write.value, value_type)
    assert write.storage_write == MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)
    if value_type is MIRCopy:
        # `return copy(p)` copies through the parameter's holder: C++ `Point(p)`.
        assert write.value.source.projections == (MIRDeref(),)


def test_each_branch_builds_its_own_result_slot(active) -> None:
    fn = _lowered(active, "pick")
    returns = _returns(fn)
    assert len(returns) == 2 and len({r.value for r in returns}) == 2
    modes = {_initialization(fn, r.value).storage_write.mode for r in returns}
    assert MIRRecordWriteMode.INITIALIZE_REGION in modes


@pytest.mark.parametrize("name, reason", [
    ("reassigned", "reassigned local returned"),
    ("from_borrow", "owned result from a borrowed call"),
    ("from_param", "owned record return needs fixed owned local"),
    # The caller destroys the result: its definition must be hook-free.
    ("make_hooked", "custom record special member"),
    # A factory writing its argument beside another operand: the order rule.
    ("stamped", "order-sensitive eager operands"),
    # A lent temporary may be built before the other operands are read: a
    # call building it must write nothing.
    ("lend_stamp", "named temporary needs stable scalar operands"),
    # A `__copy__` body returning `Own[R]`: its record has a custom special member.
    ("Copied.__copy__", "custom record special member"),
    # A native factory has no body to say its result is fresh storage.
    ("use_native", "unsupported local type or form"),
    ("temp_native", "reference needs local name"),
])
def test_refused_shapes(active, name: str, reason: str) -> None:
    verdict = active.verdicts[name]
    assert not isinstance(verdict.function, MIRFunction) and verdict.reason == reason, verdict.reason


def _relowered(active, fn: th.THIRFunction) -> MIRFunction | MIRNotCovered:
    return lower_function(fn, MIRBodyId("hand", fn.name), definitions=active.definitions,
                          summaries=active.workspace.summaries)


def test_a_readonly_local_is_not_returned_as_the_result(active) -> None:
    # Sema never gives a readonly local owned storage; C++ would copy it out.
    fn = active.functions["make_fixed"]
    assert isinstance(_relowered(active, fn), MIRFunction)
    decl = fn.body[0]
    readonly = replace(decl, is_const=True, owned_storage=replace(decl.owned_storage, readonly=True))
    result = _relowered(active, replace(fn, body=(readonly, *fn.body[1:])))
    assert isinstance(result, MIRNotCovered) and result.reason == "readonly local returned", result


def test_a_call_result_not_handed_over_as_storage_is_refused(active) -> None:
    # The callee hands the record over; THIR must spell the call in STORAGE form.
    fn = active.functions["use_result"]
    decl = fn.body[0]
    assert isinstance(decl.init, th.THIRCall) and decl.init.form is th.Form.STORAGE
    value = replace(decl, init=replace(decl.init, form=th.Form.VALUE))
    result = _relowered(active, replace(fn, body=(value, *fn.body[1:])))
    assert isinstance(result, MIRNotCovered) and result.reason == "call record result mismatch", result


def _rebuilt(fn: MIRFunction, slot: MIRSlot) -> MIRFunction:
    return replace(fn, slots=tuple(slot if s.id == slot.id else s for s in fn.slots))


def test_the_validator_returns_only_mutable_movable_record_storage(active) -> None:
    fn = _lowered(active, "make_local")
    validate_function(fn)
    (ret,) = _returns(fn)
    backing = fn.slots[ret.value.index]
    built = _lowered(active, "make")
    (result,) = _returns(built)
    with pytest.raises(MIRValidationError, match="owned record return needs movable owned storage"):
        validate_function(_rebuilt(built, replace(built.slots[result.value.index], readonly=True)))
    layout = next(r for r in fn.records if r.type == backing.type)
    pinned = replace(fn, records=tuple(replace(r, movable=False) if r is layout else r for r in fn.records))
    with pytest.raises(MIRValidationError, match="owned record return needs movable owned storage"):
        validate_function(pinned)
    # The holder is a borrow of the backing: returning it is no owned result.
    holder = _slot(fn, "p").id
    blocks = tuple(replace(b, terminator=replace(b.terminator, value=holder))
                   if isinstance(b.terminator, MIRReturn) else b for b in fn.blocks)
    with pytest.raises(MIRValidationError, match="owned record return needs movable owned storage"):
        validate_function(replace(fn, blocks=blocks))


# --- the callers ----------------------------------------------------------------------

def test_an_owned_local_takes_the_call_result(active) -> None:
    fn = _lowered(active, "use_result")
    holder = _slot(fn, "p")
    borrow = next(s for s in _writes(fn, MIRBorrow) if s.target == MIRPlace(holder.id))
    write = _initialization(fn, borrow.value.source.root)
    assert isinstance(write.value, MIRCall) and write.value.summary.callee.identity.name == "make"
    assert write.storage_write.mode is MIRRecordWriteMode.INITIALIZE_ONCE
    assert write.value.may_raise is not _summary(active, "make").summary.normal_return_only


def test_a_reseat_and_an_element_write_replace_in_place(active) -> None:
    reseat = _lowered(active, "reseat")
    holder = _slot(reseat, "p").id
    (replacement,) = [s for s in _writes(reseat, MIRCall) if s.target == MIRPlace(holder, (MIRDeref(),))]
    assert replacement.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder)
    element = _lowered(active, "element")
    (write,) = [s for s in _writes(element, MIRCall) if s.target.projections[-1:] == (MIRContainerElements(),)]
    assert write.storage_write.mode is MIRRecordWriteMode.IN_PLACE


def test_a_constructor_reseat_with_built_leaf_arguments_is_covered(active) -> None:
    # The reseat is one full expression: it owns the temporary its built
    # `str` argument needs.
    fn = _lowered(active, "reseat_ctor")
    holder = _slot(fn, "m").id
    (replacement,) = [s for s in _writes(fn, MIRConstruct) if s.target == MIRPlace(holder, (MIRDeref(),))]
    assert replacement.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder)


def test_a_handed_over_result_is_the_full_expression_temporary(active) -> None:
    fn = _lowered(active, "collect")
    append = next(s for b in fn.blocks for s in b.statements if isinstance(s, MIRCallStmt))
    temporary = fn.slots[append.call.arguments[1].index]
    assert temporary.value_kind is MIRValueKind.OWNED and temporary.kind is MIRSlotKind.TEMPORARY
    write = _initialization(fn, temporary.id)
    assert isinstance(write.value, MIRCall)
    assert write.storage_write.mode is MIRRecordWriteMode.INITIALIZE_REGION
    # Handed over with no borrow of it: private storage, so a known summary.
    assert _summary(active, "collect").state is MIRSummaryState.KNOWN


def test_a_readonly_argument_temporary_takes_the_call_result(active) -> None:
    fn = _lowered(active, "lend_temp")
    call = next(s.value for s in _writes(fn, MIRCall) if s.value.summary.callee.identity.name == "read")
    holder = fn.slots[call.arguments[0].index]
    assert holder.value_kind is MIRValueKind.BORROWED and holder.readonly
    borrow = next(s for s in _writes(fn, MIRBorrow) if s.target == MIRPlace(holder.id))
    assert isinstance(_initialization(fn, borrow.value.source.root).value, MIRCall)


def _retargeted(fn: MIRFunction, callee: str) -> MIRFunction:
    """`fn` with the `callee` call's result written into fresh record storage
    of the body's `Point` type, as an initialization."""
    stmt = next(s for s in _writes(fn, MIRCall) if s.value.summary.callee.identity.name == callee)
    point = next(s.type for s in fn.slots if s.value_kind is MIRValueKind.OWNED)
    storage = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), point, MIRSlotKind.TEMPORARY, form=th.Form.STORAGE,
                      value_kind=MIRValueKind.OWNED, storage_duration=MIRStorageDuration.BODY,
                      residence=fn.regions[0].id)
    write = replace(stmt, target=MIRPlace(storage.id),
                    storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    blocks = tuple(replace(b, statements=tuple(write if s is stmt else s for s in b.statements)) for b in fn.blocks)
    return replace(fn, slots=(*fn.slots, storage), blocks=blocks)


@pytest.mark.parametrize("name, callee", [
    # A borrowed result is no fresh storage; a scalar result is no record.
    ("borrow_then", "ident"), ("lend_temp", "read"),
])
def test_record_storage_takes_only_an_owned_record_result(active, name: str, callee: str) -> None:
    with pytest.raises(MIRValidationError, match="call record result type mismatch"):
        validate_function(_retargeted(_lowered(active, name), callee))


# --- summaries ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["make", "make_local", "duplicate", "pick", "forward", "collect"])
def test_returned_or_handed_over_storage_is_private(active, name: str) -> None:
    result = _summary(active, name)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.returns == frozenset() and result.summary.writes == frozenset()


def test_storage_the_body_keeps_stays_out_of_summaries(active) -> None:
    result = _summary(active, "use_result")
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary storage or value shape"


def test_a_borrow_live_past_the_hand_over_keeps_storage_out(active) -> None:
    fn = _lowered(active, "collect")
    append = next(s for b in fn.blocks for s in b.statements if isinstance(s, MIRCallStmt))
    temporary = fn.slots[append.call.arguments[1].index]
    assert _private_records(fn, analyze_dependencies(fn, analyze_liveness(fn))) == {temporary.id}
    # A holder borrowing the temporary, read after the append moved from it.
    holder = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), temporary.type, MIRSlotKind.TEMPORARY,
                     form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED, residence=temporary.residence)
    value = MIRSlot(MIRSlotId(fn.id, len(fn.slots) + 1), INT32, MIRSlotKind.TEMPORARY,
                    residence=temporary.residence)
    layout = next(r for r in fn.records if r.type == temporary.type)
    field = next(f for f in layout.fields if f.id.name == "y")
    blocks = []
    for block in fn.blocks:
        statements = []
        for stmt in block.statements:
            if stmt is append:
                statements.append(MIRAssign(MIRPlace(holder.id), MIRBorrow(MIRPlace(temporary.id)), stmt.loc))
            statements.append(stmt)
            if stmt is append:
                statements.append(MIRAssign(MIRPlace(value.id),
                                            MIRRead(MIRPlace(holder.id, (MIRDeref(), MIRField(field.id, field.type)))),
                                            stmt.loc))
        blocks.append(replace(block, statements=tuple(statements)))
    held = replace(fn, slots=(*fn.slots, holder, value), blocks=tuple(blocks))
    validate_function(held)
    assert _private_records(held, analyze_dependencies(held, analyze_liveness(held))) == frozenset()
    result = summarize_function(active.functions["collect"], held, active.definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary storage or value shape"


# --- the call result as a full-expression temporary ------------------------------------

def _temporary(fn: MIRFunction) -> MIRSlot:
    """The full-expression storage a call result is materialized in."""
    region = MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION)
    (write,) = [s for s in _writes(fn, MIRCall) if s.storage_write == region]
    return fn.slots[write.target.root.index]


@pytest.mark.parametrize("name", ["use_temp", "discard_temp"])
def test_a_call_temporary_is_storage_of_its_full_expression(active, name: str) -> None:
    verdict = active.verdicts[name]
    assert verdict.status is MIRVerdictStatus.CERTIFIED, verdict.reason
    fn = verdict.function
    temporary = _temporary(fn)
    assert temporary.kind is MIRSlotKind.TEMPORARY and temporary.value_kind is MIRValueKind.OWNED
    assert temporary.storage_duration != fn.regions[0].id
    ends = analyze_scope_ends(fn).ends
    assert any(end.storage == MIRPlace(temporary.id) for group in ends.values() for end in group)


def test_the_adapter_binds_the_call_backing(active) -> None:
    fn = active.functions["use_temp"]
    (backing,) = fn.storage_facts.backings
    access = fn.body[0].value
    assert backing.node is access.receiver and isinstance(backing.node, th.THIRCall)
    assert access.field_identity is not None
    bound = certify_thir_storage(MIRStorageRequest(fn, active.verdicts["use_temp"].body, MIRBodyKind.FREE_FUNCTION,
                                                  active.definitions, active.workspace.summaries))
    assert bound.requires_proof and bound.verdict is MIRStorageVerdict.CERTIFIED
    assert bound.backings[backing.node] == MIRPlace(_temporary(bound.function).id)
    # Without the THIR backing the call result is no temporary MIR can name.
    bare = replace(fn, body=(replace(fn.body[0], value=replace(
        access, receiver=replace(access.receiver, full_expression_storage=None))),))
    result = lower_function(bare, MIRBodyId("bare", "use_temp"), definitions=active.definitions,
                            summaries=active.workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "reference needs local name"


def test_a_borrow_of_the_call_temporary_kept_past_its_statement_conflicts(active) -> None:
    fn = _lowered(active, "use_temp")
    temporary = _temporary(fn)
    result = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    holder = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), temporary.type, MIRSlotKind.LOCAL, form=th.Form.BORROW,
                     value_kind=MIRValueKind.BORROWED, residence=result.region)
    member = MIRField(MIRFieldId(temporary.type, "x"), INT32)
    blocks = []
    for block in fn.blocks:
        statements = list(block.statements)
        if block.region == temporary.storage_duration:
            statements.append(MIRAssign(MIRPlace(holder.id), MIRBorrow(MIRPlace(temporary.id))))
        if block.id == result.id:
            statements.append(MIRAssign(MIRPlace(result.terminator.value),
                                        MIRRead(MIRPlace(holder.id, (MIRDeref(), member)))))
        blocks.append(replace(block, statements=tuple(statements)))
    held = replace(fn, slots=(*fn.slots, holder), blocks=tuple(blocks))
    evidence = certify_storage_origins(held, frozenset((temporary.id,)), active.definitions)
    assert evidence.verdict is MIRStorageVerdict.CONFLICT
    (conflict,) = evidence.conflicts
    assert conflict.kind is MIRStorageConflictKind.SCOPE_END and conflict.origin == MIRPlace(temporary.id)


@pytest.mark.parametrize("name, reason", [
    # The factory writes `c` beside the operand reading it.
    ("stamp_order", "order-sensitive eager operands"),
    # A record with an owned-leaf field has no full-expression storage, so
    # its field cannot be borrowed past the statement either.
    ("keep_named", "reference needs local name"),
])
def test_refused_call_temporaries(active, name: str, reason: str) -> None:
    verdict = active.verdicts[name]
    assert verdict.function is None and verdict.reason == reason, verdict.reason

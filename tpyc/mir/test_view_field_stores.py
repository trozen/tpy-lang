"""MIR over stores into view members: a write rebinds the stored loan of every
object its place reaches (replaced for one object the body owns, joined
otherwise), a loan stored in an object the caller reaches lives to every
exit and may not be the body's own (`store_escape`), a callee publishes
what it stores as loan transfers its callers apply, and a replacement is
checked against the state the statement leaves as well as the one it
enters."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import NominalType
from .call_contract import (
    MIR_RESULT, MIR_STATIC, MIRLoanTransfer, MIRParameterBinding, MIRSummaryState, summary_problem,
    transfer_problem,
)
from .collect import MIRBodyVerdict, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import (
    MIRReferent, MIRReferents, MIRUnseededLoan, _DependencyRefusal, analyze_dependencies, path_step, record_fill,
    seeded_loan, written_record,
)
from .liveness import MIRPoint, analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRCall, MIRCallStmt, MIRConstant, MIRCopy, MIRDeref, MIREdge, MIRField, MIRFunction,
    MIRPlace, MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRTupleIndex, MIRValueKind,
)
from .retention import analyze_retention
from .storage import analyze_storage, owned_field
from .storage_evidence import MIRStorageConflict, MIRStorageConflictKind, analyze_store_escapes, dump_escapes
from .summaries import summarize_function
from .validate import MIRValidationError, validate_function

SOURCE = """\
from tpy import Own, StrView, int32, copy


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

    def reset(self, s: str) -> None:
        self.s = s


class Buf:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text


def mk(k: int32) -> str:
    return "x" * (4 + k)


def direct(k: int32) -> int32:
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.s = b
    return len(t.s)


def either(k: int32, f: bool) -> int32:
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    w = Tok(a, 2)
    u = t
    if f:
        u = w
    u.s = b
    return len(t.s) + len(w.s)


def fill(t: Tok, k: int32) -> None:
    buf = mk(k)
    t.s = buf


def store(t: Tok, s: str, b: Buf) -> None:
    t.s = s
    b.text = mk(3)


def literal(t: Tok) -> None:
    t.s = "lit"


def write_copy(a: Tok, b: Tok, s: str) -> Own[Tok]:
    a.s = s
    return copy(b)


def put_read(a: Tok, b: Tok, s: str) -> StrView:
    a.s = s
    return b.s


def store_return(t: Tok, s: str) -> str:
    t.s = s
    return mk(10)


def alias_result(k: int32) -> int32:
    t = Tok("static", 1)
    s = mk(k)
    u = write_copy(t, t, s)
    t.s = "static"
    s = mk(k + 1)
    return len(u.s)


def borrowed(k: int32) -> int32:
    t = Tok("static", 1)
    buf = mk(k)
    v: StrView = put_read(t, t, buf)
    t.s = "static"
    return len(v)


def call_fill(k: int32) -> int32:
    buf = mk(k)
    t = Tok("static", 1)
    buf = store_return(t, buf)
    return len(t.s)


def retained(k: int32) -> int32:
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.reset(b)
    b = mk(k + 2)
    return len(t.s)


def ident(r: Own[Tok]) -> Own[Tok]:
    return r


def maybe_own(r: Own[Tok], s: str, f: bool) -> Own[Tok]:
    if f:
        r.s = s
    return r


def own_store(t: Own[Tok], s: str) -> int32:
    t.s = s
    return t.n


def own_local(t: Own[Tok], k: int32) -> int32:
    buf = mk(k)
    t.s = buf
    return t.n


class R:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s


class Holder:
    inner: R

    def __init__(self, inner: R) -> None:
        self.inner = copy(inner)


class Wrap:
    t: Tok

    def __init__(self, t: Own[Tok]) -> None:
        self.t = t


def reseat(k: int32, f: bool) -> int32:
    r = R(mk(k), "static")
    if f:
        r = R(mk(k + 1), r.buf)
    return len(r.s)


def member(k: int32) -> int32:
    o = Holder(R(mk(k), "static"))
    b = mk(k + 1)
    o.inner = R(mk(k + 2), b)
    return len(o.inner.s)


def member_into(o: Holder, s: str) -> None:
    o.inner = R(s, s)


def nested_fill(o: Holder, s: str) -> None:
    o.inner.s = s


def own_member(o: Own[Holder], s: str) -> int32:
    o.inner = R(s, s)
    return len(o.inner.s)


def wrapped(k: int32) -> int32:
    b = Buf(mk(k))
    t = Tok(b.text, 1)
    w = Wrap(copy(t))
    return len(w.t.s) + t.n
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    verdicts: dict[str, MIRBodyVerdict]
    type_defs: dict
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, {v.body.declaration.split("@")[0]: v for v in verdicts},
                       dict(compiler.dynamic_type_defs),
                       {node.name: fn for node, fn in ctx.thir_functions.items() if fn.receiver is None},
                       mir.definitions)


@pytest.fixture(autouse=True)
def active(program):
    program.compiler.dynamic_type_defs.update(program.type_defs)
    with activate_compiler(program.compiler):
        yield


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _view_write(fn: MIRFunction) -> tuple[MIRPoint, MIRAssign]:
    return next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                if isinstance(s, MIRAssign) and s.target.projections
                and isinstance(s.target.projections[-1], MIRField) and isinstance(s.value, MIRAlias))


def _after(fn: MIRFunction, point: MIRPoint):
    dependencies = analyze_dependencies(fn, analyze_liveness(fn))
    return dependencies, dependencies.referents[MIRPoint(point.block, point.index + 1)]


def _named(fn: MIRFunction, name: str) -> MIRPlace:
    return MIRPlace(next(s.id for s in fn.slots if s.name == name))


def _owned(fn: MIRFunction) -> list:
    return [s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED and s.type.name == "Tok"]


# --- the write ----------------------------------------------------------------------------

def test_a_view_member_write_is_no_storage_event(program: _Program) -> None:
    fn = _lowered(program, "direct")
    point, write = _view_write(fn)
    assert write.storage_write is None and not owned_field(write.target.projections[-1])
    events = analyze_storage(fn)
    assert point not in events.writes and point not in events.call_writes


def test_a_write_into_the_one_owned_object_replaces_its_loan(program: _Program) -> None:
    fn = _lowered(program, "direct")
    point, write = _view_write(fn)
    _, state = _after(fn, point)
    (storage,) = _owned(fn)
    assert state[MIRPlace(storage, (write.target.projections[-1],))] == frozenset({MIRReferent(_named(fn, "b"))})
    assert program.verdicts["direct"].conflicts == ()


def test_a_write_reaching_several_objects_joins_each(program: _Program) -> None:
    fn = _lowered(program, "either")
    point, write = _view_write(fn)
    _, state = _after(fn, point)
    a, b = MIRReferent(_named(fn, "a")), MIRReferent(_named(fn, "b"))
    member = write.target.projections[-1]
    assert {state[MIRPlace(sid, (member,))] for sid in _owned(fn)} == {frozenset({a, b})}


def test_a_write_into_a_parameter_joins_the_caller_s_loan(program: _Program) -> None:
    fn = _lowered(program, "literal")
    point, write = _view_write(fn)
    _, state = _after(fn, point)
    key = MIRPlace(fn.slots[0].id, (write.target.projections[-1],))
    loans = state[key]
    assert MIRReferent(key, external=True, held=True) in loans and any(ref.static for ref in loans)


def test_a_view_member_write_carries_no_storage_fact(program: _Program) -> None:
    fn = _lowered(program, "direct")
    _, write = _view_write(fn)
    fact = next(s.storage_write for b in _lowered(program, "store").blocks for s in b.statements
                if isinstance(s, MIRAssign) and s.storage_write is not None)
    with pytest.raises(MIRValidationError, match="view member write needs no storage fact"):
        validate_function(replace(fn, blocks=tuple(replace(b, statements=tuple(
            replace(s, storage_write=fact) if s is write else s for s in b.statements)) for b in fn.blocks)))


def test_a_view_member_write_needs_mutable_access(program: _Program) -> None:
    # The place walk refuses a readonly holder (or a readonly member record)
    # before the view member write's own access check is reached.
    fn = _lowered(program, "literal")
    receiver = fn.slots[0]
    with pytest.raises(MIRValidationError, match="store through readonly reference"):
        validate_function(replace(fn, slots=(replace(receiver, readonly=True), *fn.slots[1:])))


def test_a_view_member_write_needs_a_view_holder(program: _Program) -> None:
    fn = _lowered(program, "direct")
    _, write = _view_write(fn)
    storage = next(s.id for s in fn.slots if s.name == "b")
    with pytest.raises(MIRValidationError, match="view member write needs a view holder"):
        validate_function(replace(fn, blocks=tuple(replace(b, statements=tuple(
            replace(s, value=MIRAlias(storage)) if s is write else s for s in b.statements)) for b in fn.blocks)))


# --- the escape obligation ----------------------------------------------------------------

def test_a_body_loan_stored_in_the_caller_s_object_escapes(program: _Program) -> None:
    fn = _lowered(program, "fill")
    dependencies = analyze_dependencies(fn, analyze_liveness(fn))
    (escape,) = analyze_store_escapes(fn, dependencies)
    _, write = _view_write(fn)
    assert escape.kind is MIRStorageConflictKind.STORE_ESCAPE
    assert escape.origin == _named(fn, "buf")
    assert escape.holder == MIRPlace(fn.slots[0].id, (write.target.projections[-1],))
    assert program.verdicts["fill"].conflicts == ("store_escape",)
    # The loan has no transfer form: the summary is not published.
    assert program.verdicts["fill"].summary.state is MIRSummaryState.OPAQUE


def test_a_body_loan_stored_in_an_own_parameter_s_object_escapes(program: _Program) -> None:
    # The handed-over object is the callee's, but the caller's temporary
    # outlives the call; a parameter's loan stored there is no transfer.
    fn = _lowered(program, "own_local")
    (escape,) = analyze_store_escapes(fn, analyze_dependencies(fn, analyze_liveness(fn)))
    assert escape.origin == _named(fn, "buf") and escape.holder.root == fn.slots[0].id
    assert program.verdicts["own_local"].summary.reason == "summary stores a loan of the body's storage"
    assert _summary(program, "own_store").transfers == frozenset()
    assert program.verdicts["own_store"].conflicts == ()


def test_the_dump_prints_the_escapes(program: _Program) -> None:
    analyses = program.verdicts["fill"].analyses
    text = dump_escapes(analyses.escapes, analyses.store_escapes)
    assert "store_escape storage:%" in text and "held by %0." in text
    direct = program.verdicts["direct"].analyses
    assert dump_escapes(direct.escapes, direct.store_escapes).endswith("  no escapes\n")
    fn = _lowered(program, "direct")
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    returned = MIRStorageConflict(MIRStorageConflictKind.RETURN_ESCAPE, _named(fn, "a"), _named(fn, "b"),
                                  MIREdge(block.id))
    assert f"bb{block.id.index} return: return_escape storage:" in dump_escapes((returned,), ())


def test_a_stored_loan_lives_to_every_exit(program: _Program) -> None:
    # `t` is dead after its write, but what was stored there outlives the call.
    fn = _lowered(program, "store")
    retention = analyze_retention(fn, analyze_liveness(fn), analyze_dependencies(fn, analyze_liveness(fn)),
                                  analyze_storage(fn))
    (conflict,) = retention.conflicts
    assert conflict.retained == MIRReferent(_named(fn, "s"), external=True)
    assert conflict.holder.root == fn.slots[0].id


# --- the transfer contract ----------------------------------------------------------------

def _summary(program: _Program, name: str):
    result = program.verdicts[name].summary
    assert result.state is MIRSummaryState.KNOWN, result.reason
    return result.summary


def _path(summary, holder) -> tuple:
    return next(t.path for t in summary.transfers if t.holder == holder)


def test_a_callee_publishes_what_it_stores(program: _Program) -> None:
    reset = _summary(program, "Tok.reset")
    path = _path(reset, 0)
    assert reset.transfers == frozenset({MIRLoanTransfer(0, path, 1)}) and not reset.writes
    assert _summary(program, "literal").transfers == frozenset({MIRLoanTransfer(0, path, MIR_STATIC)})
    # A result's member is published from the record the body returns.
    copied = _summary(program, "write_copy")
    assert copied.transfers == frozenset({MIRLoanTransfer(0, path, 2), MIRLoanTransfer(MIR_RESULT, path, 1, path)})


def test_an_owned_result_keeps_the_argument_s_own_loan(program: _Program) -> None:
    # The handed-over record's own stored loan is the result's on a path
    # that does not store; only a lent object's own loan stays unpublished.
    path = _path(_summary(program, "Tok.reset"), 0)
    assert _summary(program, "ident").transfers == frozenset({MIRLoanTransfer(MIR_RESULT, path, 0, path)})
    assert _summary(program, "maybe_own").transfers == frozenset({MIRLoanTransfer(MIR_RESULT, path, 0, path),
                                                                  MIRLoanTransfer(MIR_RESULT, path, 1)})
    fn = _lowered(program, "ident")
    dependencies = analyze_dependencies(fn, analyze_liveness(fn))
    key = MIRPlace(fn.slots[0].id, (path_step(path[0]),))
    stores = [s for s in dependencies.caller_stores if s.result]
    assert [(s.key, s.loans) for s in stores] == [(key, frozenset({seeded_loan(key)}))]
    assert all(isinstance(next(b for b in fn.blocks if b.id == s.point.block).terminator, MIRReturn)
               and s.point.index == len(next(b for b in fn.blocks if b.id == s.point.block).statements)
               for s in stores)


class _Without:
    """The compiled definitions with one field left out of one record's
    layout, so the transfer's membership check has a field to miss."""

    def __init__(self, inner: MIRDefinitions, typ_name: str, field: str) -> None:
        self.inner, self.typ_name, self.field = inner, typ_name, field

    def get(self, node, typ):
        definition = self.inner.get(node, typ)
        if not (isinstance(typ, NominalType) and typ.name == self.typ_name):
            return definition
        layout = definition.layout
        return replace(definition, layout=replace(
            layout, fields=tuple(f for f in layout.fields if f.id.name != self.field)))


@pytest.mark.parametrize("name", ["literal", "ident"])
def test_a_transfer_field_missing_from_its_layout_is_refused(program: _Program, name: str) -> None:
    result = summarize_function(program.functions[name], _lowered(program, name),
                                _Without(program.definitions, "Tok", "s"))
    assert result.state is MIRSummaryState.OPAQUE
    assert result.reason == "summary transfer field differs from definition"


def test_transfer_contract_validation(program: _Program) -> None:
    copied = _summary(program, "write_copy")
    path = _path(copied, 0)
    params, result = copied.parameters, copied.callee.signature.return_type
    assert all(transfer_problem(t, params, result) is None for t in copied.transfers)
    holder = "invalid loan transfer holder"
    source = "invalid loan transfer source"
    # The holder ends at a view member of a record lent mutably, or of the owned result.
    assert transfer_problem(MIRLoanTransfer(0, (), 2), params, result) == holder
    assert transfer_problem(MIRLoanTransfer(2, path, 1), params, result) == holder
    readonly = (replace(params[0], readonly=True,
                        borrowed_record=th.THIRBorrowedRecord(params[0].borrowed_record.type, True)), *params[1:])
    assert transfer_problem(MIRLoanTransfer(0, path, 2), readonly, result) == holder
    assert transfer_problem(MIRLoanTransfer(MIR_RESULT, path, 2), params, params[0].type) == holder
    # The source is static storage, a lent leaf of the family, or a view member's loan.
    assert transfer_problem(MIRLoanTransfer(0, path, MIR_STATIC, path), params, result) == source
    assert transfer_problem(MIRLoanTransfer(0, path, 1), params, result) == source
    owned = (*params[:2], MIRParameterBinding(params[2].type, ParamPassing.VALUE, False))
    assert transfer_problem(MIRLoanTransfer(0, path, 2), owned, result) == source
    assert summary_problem(replace(copied, transfers=frozenset({MIRLoanTransfer(0, (), 2)}))) == holder


# --- the caller ---------------------------------------------------------------------------

def _call_point(fn: MIRFunction, callee: str) -> MIRPoint:
    return next(MIRPoint(b.id, i) for b in fn.blocks for i, s in enumerate(b.statements)
                if isinstance(s, (MIRAssign, MIRCallStmt))
                and isinstance(call := (s.value if isinstance(s, MIRAssign) else s.call), MIRCall)
                and call.summary.callee.identity.name == callee)


def _statement(fn: MIRFunction, point: MIRPoint) -> MIRAssign:
    return next(b for b in fn.blocks if b.id == point.block).statements[point.index]


def test_a_caller_joins_what_the_callee_stores(program: _Program) -> None:
    fn = _lowered(program, "retained")
    point = _call_point(fn, "reset")
    _, state = _after(fn, point)
    (storage,) = _owned(fn)
    member = path_step(_path(_summary(program, "Tok.reset"), 0)[0])
    # A may-store: the loan the object held stays beside the stored one.
    assert state[MIRPlace(storage, (member,))] == frozenset({MIRReferent(_named(fn, "a")),
                                                             MIRReferent(_named(fn, "b"))})
    assert program.verdicts["retained"].conflicts == ("replacement",)


def test_parameter_transfers_apply_before_the_result_fills(program: _Program) -> None:
    # `write_copy(t, t, s)`: the store into `t` lands before the result copies `t`.
    fn = _lowered(program, "alias_result")
    point = _call_point(fn, "write_copy")
    _, state = _after(fn, point)
    member = path_step(_path(_summary(program, "write_copy"), 0)[0])
    result = _statement(fn, point).target.root
    loans = state[MIRPlace(result, (member,))]
    assert MIRReferent(_named(fn, "s")) in loans and any(ref.static for ref in loans)
    assert program.verdicts["alias_result"].conflicts == ("replacement",)


def test_a_borrowed_result_resolves_after_the_transfers(program: _Program) -> None:
    fn = _lowered(program, "borrowed")
    point = _call_point(fn, "put_read")
    _, state = _after(fn, point)
    holder = _statement(fn, point).target
    assert MIRReferent(_named(fn, "buf")) in state[holder]


def test_a_replacement_is_checked_against_the_state_it_leaves(program: _Program) -> None:
    # `buf = store_return(t, buf)`: the call stores buf's loan in t and its
    # result replaces buf, in one statement.
    fn = _lowered(program, "call_fill")
    point = _call_point(fn, "store_return")
    dependencies, state = _after(fn, point)
    # Only the state the call leaves has t's object holding buf.
    member = path_step(_path(_summary(program, "store_return"), 0)[0])
    key = MIRPlace(_owned(fn)[0], (member,))
    assert MIRReferent(_named(fn, "buf")) not in dependencies.referents[point][key]
    assert MIRReferent(_named(fn, "buf")) in state[key]
    retention = analyze_retention(fn, analyze_liveness(fn), dependencies, analyze_storage(fn))
    assert [c.point for c in retention.conflicts] == [point]
    assert retention.conflicts[0].affected == MIRReferent(_named(fn, "buf"))


# --- record writes through a place ----------------------------------------------------------

def _in_place(fn: MIRFunction) -> tuple[MIRPoint, MIRAssign]:
    return next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                if isinstance(s, MIRAssign) and isinstance(s.storage_write, MIRRecordWrite)
                and s.storage_write.mode is MIRRecordWriteMode.IN_PLACE and s.target.projections)


def _field(program: _Program, record: str, name: str) -> MIRField:
    layout = next(layout for typ, layout in program.definitions.layouts.items() if typ.name == record)
    return next(f for f in layout.fields if f.id.name == name)


def _identity(member: MIRField) -> th.THIRFieldIdentity:
    return th.THIRFieldIdentity(member.id.owner, member.id.name, member.type)


def _object(state: MIRReferents, holder: MIRPlace) -> MIRPlace:
    (ref,) = state[holder]
    return ref.place


def test_an_in_place_reseat_fills_the_one_object(program: _Program) -> None:
    # `r = R(mk(k + 1), r.buf)` through the rebind-slot holder: the one
    # object it reaches stores the new record's loan, a view of the buffer
    # the same write replaces.
    fn = _lowered(program, "reseat")
    point, write = _in_place(fn)
    assert write.target.projections == (MIRDeref(),)
    _, state = _after(fn, point)
    storage = _object(state, _named(fn, "r"))
    buf, s = _field(program, "R", "buf"), _field(program, "R", "s")
    assert state[MIRPlace(storage.root, (s,))] == frozenset({MIRReferent(MIRPlace(storage.root, (buf,)))})
    assert program.verdicts["reseat"].conflicts == ("replacement",)


def test_a_member_replacement_fills_the_member_s_entries(program: _Program) -> None:
    # One owned object: the member's entry holds the new record's loan only.
    fn = _lowered(program, "member")
    point, write = _in_place(fn)
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    assert write.target.projections[-1] == inner
    _, state = _after(fn, point)
    storage = _object(state, _named(fn, "o"))
    assert state[MIRPlace(storage.root, (inner, s))] == frozenset({MIRReferent(_named(fn, "b"))})
    assert program.verdicts["member"].conflicts == ()


def test_a_loan_holding_write_no_entry_keys_refuses(program: _Program) -> None:
    # A whole record written through a member is filled; a loan-free member
    # write fills nothing; a loan-holding write under a place no stored-loan
    # entry keys (a tuple member here) refuses rather than storing nothing.
    fn = _lowered(program, "member")
    slots = {s.id: s for s in fn.slots}
    o = _named(fn, "o")
    inner, buf = _field(program, "Holder", "inner"), _field(program, "R", "buf")
    assert written_record(MIRPlace(o.root, (inner,)), slots) == inner.type
    assert written_record(MIRPlace(o.root, (inner, buf)), slots) is None
    with pytest.raises(_DependencyRefusal, match="loan-holding write under an unmodeled place"):
        written_record(MIRPlace(o.root, (MIRTupleIndex(0),)), slots)
    # A value naming no stored loan refuses the fill, whatever the statement.
    layouts = {r.type: r for r in fn.records}
    with pytest.raises(MIRUnseededLoan, match="record storage filled with no stored loan"):
        record_fill(MIRPlace(o.root, (inner,)), inner.type, MIRConstant(1), {}, slots, layouts)


def test_a_member_replacement_through_a_parameter_joins_and_transfers(program: _Program) -> None:
    fn = _lowered(program, "member_into")
    point, _ = _in_place(fn)
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    key = MIRPlace(fn.slots[0].id, (inner, s))
    _, state = _after(fn, point)
    assert state[key] == frozenset({seeded_loan(key), MIRReferent(_named(fn, "s"), external=True)})
    summary = _summary(program, "member_into")
    assert summary.transfers == frozenset({MIRLoanTransfer(0, (_identity(inner), _identity(s)), 1)})
    # The weak join keeps the caller's own loan under the member the write
    # replaces: a replacement conflict C++ would not have (fail-closed).
    assert program.verdicts["member_into"].conflicts == ("replacement",)


def test_a_nested_member_write_through_a_parameter_transfers(program: _Program) -> None:
    # The parameter's layout is registered by the write, so its nested entry exists.
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    summary = _summary(program, "nested_fill")
    transfer = MIRLoanTransfer(0, (_identity(inner), _identity(s)), 1)
    assert summary.transfers == frozenset({transfer})
    assert transfer_problem(transfer, summary.parameters, summary.callee.signature.return_type) is None
    assert program.verdicts["nested_fill"].conflicts == ()


def test_a_member_replacement_of_an_own_parameter_replaces_its_loan(program: _Program) -> None:
    # The handed-over object is one the body owns: a strong update, never published.
    fn = _lowered(program, "own_member")
    point, _ = _in_place(fn)
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    _, state = _after(fn, point)
    assert state[MIRPlace(fn.slots[0].id, (inner, s))] == frozenset({MIRReferent(_named(fn, "s"), external=True)})
    assert _summary(program, "own_member").transfers == frozenset()
    assert program.verdicts["own_member"].conflicts == ()


def test_a_copy_handed_to_a_constructor_carries_the_source_s_loan(program: _Program) -> None:
    fn = _lowered(program, "wrapped")
    s = _field(program, "Tok", "s")
    point = next(MIRPoint(b.id, i) for b in fn.blocks for i, st in enumerate(b.statements)
                 if isinstance(st, MIRAssign) and isinstance(st.value, MIRCopy) and not st.target.projections)
    copy = _statement(fn, point)
    _, state = _after(fn, point)
    source = _object(state, MIRPlace(copy.value.source.root))
    assert state[MIRPlace(copy.target.root, (s,))] == state[MIRPlace(source.root, (s,))]
    assert program.verdicts["wrapped"].conflicts == ()

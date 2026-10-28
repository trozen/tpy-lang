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
from ..thir.testutil import _compile, _entry, force_in_place
from ..type_def_registry import ParamPassing
from ..typesys import INT32, STRVIEW, NominalType, OwnType, ReadonlyType, TupleType
from .call_contract import (
    MIR_RESULT, MIR_STATIC, MIRAliasObligation, MIRLoanTransfer, MIRParameterWrite, MIRSummaryState, summary_problem,
    transfer_problem, path_step, summary_path,
)
from .collect import MIRBodyVerdict, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import (
    MIRReferent, MIRReferents, MIRUnseededLoan, _DependencyRefusal, apply_transfers, record_fill, resolve_referents, seeded_loan, written_record,
)
from .shapes import MIRContainerStructure, MIRDeref, MIRTupleIndex, owned_field
from .liveness import MIRPoint
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRCall, MIRCallStmt, MIRConstant, MIRCopy, MIREdge, MIRFunction, MIRPlace,
    MIRPresentCall, MIRStorageWrite, MIRStorageWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
)
from .value import MIRRecords, MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError, container_members
from .retention import analyze_retention
from .storage import analyze_storage
from .storage_evidence import MIRStorageConflict, MIRStorageConflictKind, analyze_escapes, dump_escapes
from .summaries import summarize_function
from .testutil import (
    as_declared, Redeclared, records_of, no_layouts, bind_parameters, borrowed_shape, bound, leaf_shape, reaccess,
    reslot, tuple_shape, prepared,
)
from .prepare import validate_function

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


class Views:
    s: StrView
    views: list[StrView]
    ns: list[int32]

    def __init__(self, s: StrView, views: list[StrView], ns: list[int32]) -> None:
        self.s = s
        self.views = copy(views)
        self.ns = copy(ns)


def views_of(v: Views, views: list[StrView], ns: list[int32]) -> None:
    pass


class E:
    items: list[str]
    s: StrView

    def __init__(self, items: list[str], s: StrView) -> None:
        self.items = copy(items)
        self.s = s

    def setv(self, v: StrView) -> None:
        self.s = v


def elem(r: E, k: int32) -> int32:
    r.setv(r.items[0])
    r.items[0] = mk(k)
    return len(r.s)


def elem_count(r: E, k: int32) -> int32:
    r.items[0] = mk(k)
    return len(r.items)
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
    # The in-place reseat MIR models; sema leaves every rebind OWN.
    force_in_place(modules, {"reseat": None})
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        result = _Program(compiler, {v.body.declaration.split("@")[0]: v for v in verdicts},
                       dict(compiler.dynamic_type_defs),
                       {node.name: fn for node, fn in ctx.thir_functions.items() if fn.receiver is None},
                       mir.definitions)
    return result


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
    dependencies = prepared(fn).dependencies
    return dependencies, dependencies.referents[MIRPoint(point.block, point.index + 1)]


def _named(fn: MIRFunction, name: str) -> MIRPlace:
    return MIRPlace(next(s.id for s in fn.slots if s.name == name))


def _owned(fn: MIRFunction) -> list:
    return [s.id for s in fn.slots if s.shape.is_storage and s.type.name == "Tok"]


# --- the write ----------------------------------------------------------------------------

def test_a_view_member_write_is_no_storage_event(program: _Program) -> None:
    fn = _lowered(program, "direct")
    point, write = _view_write(fn)
    assert write.storage_write is None and not owned_field(write.target.projections[-1])
    events = analyze_storage(prepared(fn))
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
    with pytest.raises(MIRValidationError, match="field write of no storage needs no storage fact"):
        validate_function(replace(fn, blocks=tuple(replace(b, statements=tuple(
            replace(s, storage_write=fact) if s is write else s for s in b.statements)) for b in fn.blocks)))


def test_a_view_member_write_needs_mutable_access(program: _Program) -> None:
    # The place walk refuses a readonly holder (or a readonly member record)
    # before the view member write's own access check is reached.
    fn = _lowered(program, "literal")
    receiver = fn.slots[0]
    with pytest.raises(MIRValidationError, match="store through readonly reference"):
        validate_function(bind_parameters(replace(fn, slots=(
            reslot(receiver, readonly=True), *fn.slots[1:]))))


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
    (escape,) = analyze_escapes(prepared(fn)).stores
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
    (escape,) = analyze_escapes(prepared(fn)).stores
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
    retention = analyze_retention(prepared(fn))
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
    dependencies = prepared(fn).dependencies
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

    def _without(self, typ, layout):
        if not (isinstance(typ, NominalType) and typ.name == self.typ_name):
            return layout
        return replace(layout, fields=tuple(f for f in layout.fields if f.id.name != self.field))

    def get(self, node, typ):
        definition = self.inner.get(node, typ)
        return replace(definition, layout=self._without(typ, definition.layout))

    @property
    def layouts(self) -> MIRRecords:
        # The record lacks the field in its declaration as in its layout.
        fields = Redeclared(self.inner.fields, lambda typ, declared: frozenset(
            f for f in declared if not (isinstance(typ, NominalType) and typ.name == self.typ_name
                                        and f.id.name == self.field)))
        return MIRRecords({typ: self._without(typ, layout) for typ, layout in self.inner.layouts.items()}, fields)


@pytest.mark.parametrize("name", ["literal", "ident"])
def test_a_transfer_field_missing_from_its_layout_is_refused(program: _Program, name: str) -> None:
    body = _lowered(program, name)
    result = summarize_function(program.functions[name], body, prepared(body), _Without(program.definitions, "Tok", "s"))
    assert result.state is MIRSummaryState.OPAQUE
    assert result.reason == "field missing from declared fields"


def test_transfer_contract_validation(program: _Program) -> None:
    copied = _summary(program, "write_copy")
    path = _path(copied, 0)
    params, result = copied.parameters, copied.callee.signature.return_type
    assert all(transfer_problem(t, params, result, no_layouts()) is None for t in copied.transfers)
    holder = "invalid loan transfer holder"
    source = "invalid loan transfer source"
    # The holder ends at a view member of a record lent mutably, or of the owned result.
    assert transfer_problem(MIRLoanTransfer(0, (), 2), params, result, no_layouts()) == holder
    assert transfer_problem(MIRLoanTransfer(2, path, 1), params, result, no_layouts()) == holder
    readonly = (reaccess(params[0], True), *params[1:])
    assert transfer_problem(MIRLoanTransfer(0, path, 2), readonly, result, no_layouts()) == holder
    assert transfer_problem(MIRLoanTransfer(MIR_RESULT, path, 2), params, params[0].type, no_layouts()) == holder
    # The source is static storage, a lent leaf of the family, or a view member's loan.
    assert transfer_problem(MIRLoanTransfer(0, path, MIR_STATIC, path), params, result, no_layouts()) == source
    assert transfer_problem(MIRLoanTransfer(0, path, 1), params, result, no_layouts()) == source
    owned = (*params[:2], bound(OwnType(params[2].type), ParamPassing.VALUE))
    assert transfer_problem(MIRLoanTransfer(0, path, 2), owned, result, no_layouts()) == source
    assert summary_problem(replace(copied, transfers=frozenset({MIRLoanTransfer(0, (), 2)})), no_layouts()) == holder


def test_the_contract_records_a_walk_of_every_path_with_a_field(program: _Program) -> None:
    # `store` writes `param2.text` and its transfer holds at `param0.s`; an
    # obligation to keep `param0.n` apart from that write is added. The
    # walks a call reads are exactly the paths with a field, every kind.
    stored = _summary(program, "store")
    write, = stored.writes
    kept = MIRAliasObligation(0, (_identity(_field(program, "Tok", "n")),), write)
    summary = replace(stored, obligations=frozenset({kept}))
    assert len(_checked_walks(program, summary)) == 3
    # `put_read` returns `b.s` (a return origin) and `write_copy` hands
    # `b.s`'s loan to the owned result's `s` (a source path and the result
    # holder): each kind must be walked and recorded too.
    read, copied = _summary(program, "put_read"), _summary(program, "write_copy")
    (origin,) = read.returns
    assert (origin.parameter, origin.path) in _checked_walks(program, read)
    copy_walks = _checked_walks(program, copied)
    assert any(t.holder == MIR_RESULT and (MIR_RESULT, t.path) in copy_walks for t in copied.transfers)
    assert any(t.source_path and (t.source, t.source_path) in copy_walks for t in copied.transfers)


def _checked_walks(program: _Program, summary) -> dict:
    """The walks `summary_problem` records for `summary`, asserted to be
    exactly the summary's paths with a field, of every kind."""
    walks: dict = {}
    assert summary_problem(summary, program.definitions.layouts, walks) is None
    paths = {(w.parameter, w.path) for w in summary.writes}
    paths |= {(r.parameter, r.path) for r in summary.returns}
    paths |= {(t.holder, t.path) for t in summary.transfers}
    paths |= {(t.source, t.source_path) for t in summary.transfers if t.source_path}
    paths |= {(o.parameter, o.path) for o in summary.obligations}
    assert set(walks) == {(end, path) for end, path in paths if path}
    return walks


def _retyped_field(layouts: MIRRecords, field: MIRField, typ) -> tuple[MIRField, MIRRecords]:
    """`field` declared at `typ` in its owner's layout and declaration."""
    owner = field.id.owner
    retyped = replace(field, type=typ)
    layout = layouts[owner]
    return retyped, as_declared(layouts, replace(layout, fields=tuple(
        retyped if f == field else f for f in layout.fields)))


def test_no_transfer_rebinds_a_member_declared_readonly(program: _Program) -> None:
    # A transfer is a store into its holder's view member (`store_problem`):
    # never through a `readonly[R]` record member. A `readonly[StrView]`
    # member is no field the walk reaches at all (MIR models no such type).
    filled = _summary(program, "nested_fill")
    (transfer,), layouts = filled.transfers, program.definitions.layouts
    params, result = filled.parameters, filled.callee.signature.return_type
    assert transfer_problem(transfer, params, result, layouts) is None
    inner, s = (path_step(item) for item in transfer.path)
    for member, typ, reason in ((inner, ReadonlyType(inner.type), "invalid loan transfer holder"),
                                (s, ReadonlyType(s.type), "unsupported field type")):
        retyped, laid = _retyped_field(layouts, member, typ)
        path = summary_path(tuple(retyped if p == member else p for p in (inner, s)))
        assert transfer_problem(replace(transfer, path=path), params, result, laid) == reason, member


def test_a_transfer_through_a_refused_definition_never_publishes(program: _Program) -> None:
    # `o.inner.s = s`: the transfer's second field is read from an R, which
    # no slot of the body holds; R's definition refused, its layout kept.
    assert _summary(program, "nested_fill").transfers
    r = next(t for t in program.definitions.records if t.name == "R")
    refused = object.__new__(MIRDefinitions)
    object.__setattr__(refused, "records", {**program.definitions.records, r: "refused for the test"})
    object.__setattr__(refused, "layouts", program.definitions.layouts)
    body = _lowered(program, "nested_fill")
    result = summarize_function(program.functions["nested_fill"], body, prepared(body), refused)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary record: refused for the test"


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


def test_a_transfer_through_a_widened_path_step_fails_closed(program: _Program) -> None:
    # The transfer grammar walks any step a summary path takes, but a
    # container whose elements hold a loan is refused upstream, so no
    # summary stores a loan under an elements region.
    tok = next(t for t in program.definitions.records if t.name == "Tok")
    assert container_members(NominalType("list", (tok,), _module_qname="builtins.list"), False) == (
        "container holds a borrow")
    # A forged transfer under a step the caller's object has no member for
    # (a tuple member of a record) raises rather than storing nowhere.
    fn = _lowered(program, "alias_result")
    point = _call_point(fn, "write_copy")
    call = _statement(fn, point).value
    path = _path(call.summary, 0)
    forged = replace(call, summary=replace(call.summary, transfers=frozenset({
        MIRLoanTransfer(0, (MIRTupleIndex(0), *path), MIR_STATIC)})))
    slots = {slot.id: slot for slot in fn.slots}
    dependencies, _ = _after(fn, point)
    with pytest.raises(MIRValidationError, match="tuple projection needs tuple payload"):
        apply_transfers(MIRPresentCall(forged, forged.summary, ()), dict(dependencies.referents[point]), slots,
                        fn.record_layouts)
    # One the caller keys no stored loan at raises too.
    unkeyed = replace(call, summary=replace(call.summary, transfers=frozenset({
        MIRLoanTransfer(0, (th.THIRFieldIdentity(tok, "n", INT32),), MIR_STATIC)})))
    with pytest.raises(MIRUnseededLoan):
        apply_transfers(MIRPresentCall(unkeyed, unkeyed.summary, ()), dict(dependencies.referents[point]), slots,
                        fn.record_layouts)


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
    retention = analyze_retention(prepared(fn))
    assert [c.point for c in retention.conflicts] == [point]
    assert retention.conflicts[0].affected == MIRReferent(_named(fn, "buf"))


# --- record writes through a place ----------------------------------------------------------

def _in_place(fn: MIRFunction) -> tuple[MIRPoint, MIRAssign]:
    return next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                if isinstance(s, MIRAssign) and isinstance(s.storage_write, MIRStorageWrite)
                and s.storage_write.mode is MIRStorageWriteMode.IN_PLACE and s.target.projections)


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
    records = fn.record_layouts
    o = _named(fn, "o")
    inner, buf = _field(program, "Holder", "inner"), _field(program, "R", "buf")
    assert written_record(MIRPlace(o.root, (MIRDeref(), inner)), slots, records) == inner.type
    assert written_record(MIRPlace(o.root, (MIRDeref(), inner, buf)), slots, records) is None
    pair = MIRSlotId(fn.id, len(fn.slots))
    slots[pair] = MIRSlot(pair, tuple_shape(TupleType((slots[o.root].type, INT32)),
                                            (slots[o.root].shape, leaf_shape(INT32))), MIRSlotKind.LOCAL)
    with pytest.raises(_DependencyRefusal, match="loan-holding write under an unmodeled place"):
        written_record(MIRPlace(pair, (MIRTupleIndex(0),)), slots, records)
    # A value naming no stored loan refuses the fill, whatever the statement.
    layouts = fn.record_layouts
    with pytest.raises(MIRUnseededLoan, match="record storage filled with no stored loan"):
        record_fill(MIRPlace(o.root, (inner,)), inner.type, MIRConstant(1), {}, slots, layouts)


def test_a_place_ending_at_a_containers_structure(program: _Program) -> None:
    # `(*v).ns[structure]` under a loan-holding record: the referent is the
    # field's region under the caller's object, and a write there replaces
    # no record whole. `(*v).views[structure]`, a container of loans, refuses:
    # no stored-loan entry keys its elements.
    holder, views_type, ns_type = (p.type.wrapped for p in program.functions["views_of"].params)
    s, views, ns = (MIRField(MIRFieldId(holder, name), typ)
                    for name, typ in (("s", STRVIEW), ("views", views_type), ("ns", ns_type)))
    records = records_of(MIRRecordLayout(holder, (s, views, ns), True, True))
    body = MIRBodyId("view_field_stores", "views")
    v = MIRSlot(MIRSlotId(body, 0), borrowed_shape(holder), MIRSlotKind.PARAMETER)
    slots, structure = {v.id: v}, MIRContainerStructure()
    state = {MIRPlace(v.id): frozenset({MIRReferent(MIRPlace(v.id), True)})}
    field_structure = MIRPlace(v.id, (MIRDeref(), ns, structure))
    assert resolve_referents(field_structure, state, slots, records) == {
        MIRReferent(MIRPlace(v.id, (ns, structure)), True)}
    assert written_record(field_structure, slots, records) is None
    with pytest.raises(_DependencyRefusal, match="loan-holding write under an unmodeled place"):
        written_record(MIRPlace(v.id, (MIRDeref(), views, structure)), slots, records)


def test_a_member_replacement_through_a_parameter_joins_and_transfers(program: _Program) -> None:
    fn = _lowered(program, "member_into")
    point, _ = _in_place(fn)
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    key = MIRPlace(fn.slots[0].id, (inner, s))
    _, state = _after(fn, point)
    assert state[key] == frozenset({seeded_loan(key), MIRReferent(_named(fn, "s"), external=True)})
    summary = _summary(program, "member_into")
    assert summary.transfers == frozenset({MIRLoanTransfer(0, (_identity(inner), _identity(s)), 1)})
    # The new member stores `s`'s loan while the old member is replaced: a
    # conflict only when `s` views storage inside `o.inner`, so it is a
    # no-alias obligation, checked at callers MIR analyzes and kept by the
    # body as a conflict of its own kind.
    assert summary.obligations == frozenset({
        MIRAliasObligation(1, (), MIRParameterWrite(0, (_identity(inner),)))})
    assert program.verdicts["member_into"].conflicts == ("alias_obligation",)


def test_a_nested_member_write_through_a_parameter_transfers(program: _Program) -> None:
    # The parameter's layout is registered by the write, so its nested entry exists.
    inner, s = _field(program, "Holder", "inner"), _field(program, "R", "s")
    summary = _summary(program, "nested_fill")
    transfer = MIRLoanTransfer(0, (_identity(inner), _identity(s)), 1)
    assert summary.transfers == frozenset({transfer})
    assert transfer_problem(transfer, summary.parameters, summary.callee.signature.return_type, no_layouts()) is None
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


# --- element stores under the rebinding holder's own object -------------------------------

def test_an_element_store_is_checked_against_its_owner_s_view_members(program: _Program) -> None:
    # `r.setv(r.items[0]); r.items[0] = mk(k)`: the rebinding holder `r`
    # repoints, but `r.s`, a view member of the object it lends, still
    # retains the element the store replaces.
    fn = _lowered(program, "elem")
    point, write = _in_place(fn)
    r = _named(fn, "r")
    assert write.storage_write.rebind_owner == r.root
    items, s = _field(program, "E", "items"), _field(program, "E", "s")
    retention = analyze_retention(prepared(fn))
    element = MIRReferent(MIRPlace(r.root, (items, write.target.projections[-1])), True)
    assert {(c.point, c.holder, c.affected) for c in retention.conflicts} == {
        (point, MIRPlace(r.root, (s,)), element)}
    assert element in {c.retained for c in retention.conflicts}
    assert program.verdicts["elem"].conflicts == ("replacement",)


def test_an_element_store_skips_only_the_rebinding_holder(program: _Program) -> None:
    fn = _lowered(program, "elem_count")
    _, write = _in_place(fn)
    assert write.storage_write.rebind_owner == _named(fn, "r").root
    assert program.verdicts["elem_count"].conflicts == ()

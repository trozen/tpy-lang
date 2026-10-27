"""Owned-leaf record fields as MIR places: reads borrow or copy the field's
buffer, writes replace it in place, constructors copy or move it in, and a
callee's summarized field write is a replacement event at the call."""

import re
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import latch_declared_native_flags
from ..typesys import INT32, STR, NominalType
from .call_contract import MIRParameterWrite, MIRSummaryState
from .definitions import MIRConstructorDefinition, MIRDefinitions
from .dependencies import MIRReferent
from .dump import dump_function
from .liveness import MIRPoint
from .testutil import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBorrow, MIRCall, MIRCallStmt, MIRConstant, MIRCopy, MIRFunction,
    MIRMove, MIRPlace, MIRRead, MIRStorageWrite, MIRStorageWriteMode,
)
from .shapes import MIRDeref
from .value import MIRField, MIRFieldId, MIRValidationError, loan_field
from .retention import MIRRetention, analyze_retention
from .storage import analyze_storage, dump_storage
from .storage_evidence import MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins
from .test_constructors import field_write, rewritten, statements
from .testutil import prepared
from .prepare import validate_function
from .reach import reaches, storage_exclusions

SOURCE = """\
from tpy import String, StrView, Own, int32


class Rec:
    def __init__(self, name: str, n: int32) -> None:
        self.name = name
        self.n = n

    def rename_m(self, s: str) -> None:
        self.name = s


class Holder:
    def __init__(self, v: StrView) -> None:
        self.v = v


class OwnRec:
    def __init__(self, name: Own[str]) -> None:
        self.name = name


class Twice:
    def __init__(self, a: Own[str]) -> None:
        self.x = a
        self.y = a


class Lit:
    name: str
    total: int
    data: bytes
    n: int32

    def __init__(self, n: int32) -> None:
        self.name = "x"
        self.total = 0
        self.data = b"ab"
        self.n = n


class Cond:
    name: str

    def __init__(self, name: str, flag: bool) -> None:
        self.name = ""
        if flag:
            self.name = name


class Big:
    def __init__(self, total: int) -> None:
        self.total = total


class Byt:
    def __init__(self, data: bytes) -> None:
        self.data = data


class Strg:
    def __init__(self, s: String) -> None:
        self.s = s


def read_len(r: Rec) -> int:
    return len(r.name)


def read_copy(r: Rec) -> str:
    return r.name


def write_field(r: Rec, s: str) -> None:
    r.name = s


def append_field(r: Rec, s: str) -> None:
    r.name += s


def rename(r: Rec, s: str) -> None:
    r.name = s


def renumber(r: Rec, n: int32) -> None:
    r.n = n


def forwarded_write(r: Rec, s: str) -> None:
    rename(r, s)


def two_hop(r: Rec, s: str) -> None:
    forwarded_write(r, s)


def local_plain(s: str) -> int:
    r = Rec(s, 1)
    r.name = s
    return len(r.name)


def local_literal() -> int:
    r = Rec("lit", 1)
    return len(r.name)


def ctor_from_view(s: str) -> int:
    v = s[1:]
    r = Rec(v, 1)
    return len(r.name)


def ctor_from_string(x: String) -> int:
    r = Rec(x, 1)
    return len(r.name)


def lit_then_write(q: Rec) -> int:
    v = "lit"
    q.name = "x"
    return len(v)


def sibling(r: Rec, s: str) -> int:
    return len(r.name) + r.n


def method_write(r: Rec, s: str) -> None:
    r.rename_m(s)


def copy_then_write(r: Rec, s: str) -> int:
    t = r.name
    rename(r, s)
    return len(t)


def name_after_rename(r: Rec, s: str) -> None:
    rename(r, s)
    print(r.name)


def name_after_renumber(r: Rec, n: int32) -> None:
    renumber(r, n)
    print(r.name)


def name_after_other(p: Rec, q: Rec, s: str) -> None:
    rename(q, s)
    print(p.name)


def local_after_rename(s: str) -> None:
    r = Rec(s, 1)
    rename(r, s)
    print(r.name)


def big_add(b: Big, x: int) -> None:
    b.total += x


def byt_write(b: Byt, d: bytes) -> None:
    b.data = d


def strg_read(s: Strg) -> int:
    return len(s.s)


def own_local(s: str) -> int:
    o = OwnRec(s)
    return len(o.name)


def twice_local(s: str) -> int:
    t = Twice(s)
    return len(t.x)


def make(s: str) -> str:
    return s


def local_call(s: str) -> int:
    r = Rec(make(s), 1)
    return len(r.name)


def lit_local() -> int:
    v = Lit(1)
    return v.n
"""


@dataclass(frozen=True)
class _Lowered:
    compiler: object
    modules: list
    definitions: MIRDefinitions
    bodies: dict
    constructors: dict
    summaries: dict
    record: NominalType
    # By callee identity: every constructor's is named `__init__`.
    by_identity: dict


@pytest.fixture(scope="module")
def lowered():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        summaries = mir.workspace.summaries
        bodies = {fn.name: lower_function(fn, MIRBodyId("fields", fn.name), definitions=mir.definitions,
                                          summaries=summaries)
                  for fn in ctx.thir_functions.values()}
        constructors = {ctor.record_name: lower_constructor(ctor, MIRBodyId("fields", f"{ctor.record_name}.__init__"),
                                                            definitions=mir.definitions, summaries=summaries)
                        for ctor in ctx.thir_constructors.values()}
        record = next(c.record_layout.type for c in ctx.thir_constructors.values() if c.record_name == "Rec")
        result = _Lowered(compiler, modules, mir.definitions, bodies, constructors,
                       {identity.name: result for identity, result in summaries.items()}, record, dict(summaries))
    return result


@pytest.fixture(autouse=True)
def active(lowered):
    # The per-test state reset clears the stub facts latched onto the static
    # TypeDefs (`borrowing_view` included, which every view rule reads).
    for module in lowered.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(lowered.compiler):
        yield lowered


def _lines(fn: MIRFunction) -> list[str]:
    """Dump lines without source locations, which only restate SOURCE's layout."""
    return [re.sub(r" @ \d+:\d+$", "", line.strip()) for line in dump_function(fn).splitlines()]


def _body(active: _Lowered, name: str) -> MIRFunction:
    fn = active.bodies[name]
    assert isinstance(fn, MIRFunction), fn
    return fn


def _ctor(active: _Lowered, name: str) -> MIRFunction:
    fn = active.constructors[name]
    assert isinstance(fn, MIRFunction), fn
    return fn


def _retention(fn: MIRFunction) -> MIRRetention:
    result = analyze_retention(prepared(fn))
    assert isinstance(result, MIRRetention), result
    return result


def _name_field(active: _Lowered) -> MIRField:
    return MIRField(MIRFieldId(active.record, "name"), STR)


# --- constructors ----------------------------------------------------------------


INITIALIZE = MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)


def _initialization(fn: MIRFunction, field: str) -> tuple[object, MIRStorageWrite | None]:
    write = field_write(fn, field)
    return write.value, write.storage_write


def test_constructor_copies_a_borrowed_parameter_into_the_field(active) -> None:
    fn = _ctor(active, "Rec")
    # The view parameter's buffer is copied into the field: an allocation that may raise.
    assert _initialization(fn, "name") == (MIRCopy(MIRPlace(fn.slots[0].id, (MIRDeref(),)), True), INITIALIZE)
    assert "exceptional exits" in _lines(fn) and fn.exceptional_exits
    value, fact = _initialization(fn, "n")
    assert isinstance(value, MIRRead) and fact is None
    assert field_write(fn, "name").loc.line == 6


def test_constructor_initialization_modes(active) -> None:
    # A by-value parameter is moved in and cannot raise.
    own = _ctor(active, "OwnRec")
    assert _initialization(own, "name") == (MIRMove(own.slots[0].id), INITIALIZE) and not own.exceptional_exits
    # A by-value parameter initializing two fields is copied, then moved.
    twice = _ctor(active, "Twice")
    assert _initialization(twice, "x") == (MIRCopy(MIRPlace(twice.slots[0].id), True), INITIALIZE)
    assert _initialization(twice, "y") == (MIRMove(twice.slots[0].id), INITIALIZE)
    # Constants materialize (a BigInt's cannot raise: its allocation failure is a panic).
    lit = _ctor(active, "Lit")
    assert [_initialization(lit, f) for f in ("name", "total", "data")] == [
        (MIRConstant("x"), INITIALIZE), (MIRConstant(0), INITIALIZE), (MIRConstant(b"ab"), INITIALIZE)]
    assert lit.exceptional_exits
    # A bytes view is materialized, a BigInt and a String are copied from their const references.
    for name, field, raises in (("Byt", "data", True), ("Big", "total", False), ("Strg", "s", True)):
        fn = _ctor(active, name)
        assert _initialization(fn, field) == (MIRCopy(MIRPlace(fn.slots[0].id, (MIRDeref(),)), raises),
                                              INITIALIZE), name


def test_constructor_body_write_replaces_the_initialized_field(active) -> None:
    fn = _ctor(active, "Cond")
    assert _initialization(fn, "name") == (MIRConstant(""), INITIALIZE)
    # The body's later write replaces the initialized field in place.
    replace_name = MIRCopy(MIRPlace(fn.slots[0].id, (MIRDeref(),)), True)
    assert any(isinstance(s, MIRAssign) and s.value == replace_name
               and s.storage_write == MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)
               for s in statements(fn))


def test_a_record_holding_a_view_stores_its_parameters_loan(active) -> None:
    # The view member stores the loan the view parameter holds: no copy, no exit.
    holder = _ctor(active, "Holder")
    assert _initialization(holder, "v") == (MIRAlias(holder.slots[0].id), None) and not holder.exceptional_exits
    record = next(t for t in active.definitions.records if t.name == "Holder")
    result = active.by_identity[active.definitions.records[record].callee.identity]
    # A caller's construction stores its argument's loan into the record.
    transfer, = result.summary.transfers
    assert loan_field(transfer.path[-1].type) and transfer.source == 0


def test_callers_call_constructors_whatever_their_members(active) -> None:
    records = {t.name: r for t, r in active.definitions.records.items()}
    # A constant member, and a parameter both copied and moved, are the
    # constructor body's own: a caller calls it with its arguments.
    for name in ("Lit", "Twice", "Cond"):
        assert isinstance(records[name], MIRConstructorDefinition), name
    assert "%1 = call __main__.Lit::__init__(%0) [reader, may-raise] [initialize]" in _lines(_body(active, "lit_local"))
    lines = _lines(_body(active, "twice_local"))
    # The `Own[str]` argument is the full expression's copy.
    assert "%1 = copy (*%0) may-raise [initialize]" in lines
    assert "%2 = call __main__.Twice::__init__(%1) [reader, may-raise] [initialize]" in lines


def test_a_declaration_owns_its_constructor_argument_temporaries(active) -> None:
    # The by-value argument is the full expression's copy, moved into the field.
    lines = _lines(_body(active, "own_local"))
    assert "%1: str owned-storage temporary duration=r1 residence=r1" in lines
    assert "%1 = copy (*%0) may-raise [initialize]" in lines
    assert "%2 = call __main__.OwnRec::__init__(%1) [reader, normal-return] [initialize]" in lines
    # A call's fresh result is lent to the constructor that copies it, and dies with the statement.
    fn = _body(active, "local_call")
    lines = _lines(fn)
    assert "%2 = call main::make(%1) [reader, may-raise] [initialize]" in lines
    assert "%3 = borrow %2" in lines and "%5 = call __main__.Rec::__init__(%3, %4) [reader, may-raise] [initialize]" in lines
    storage = frozenset(s.id for s in fn.slots if s.shape.is_storage)
    assert certify_storage_origins(prepared(fn), storage, active.definitions).verdict is MIRStorageVerdict.CERTIFIED


def test_a_caller_construct_copies_a_lent_argument(active) -> None:
    lines = _lines(_body(active, "local_plain"))
    assert "%1 = borrow (*%0)" in lines
    assert "%3 = call __main__.Rec::__init__(%1, %2) [reader, may-raise] [initialize]" in lines
    # A static literal is lent the same way.
    lines = _lines(_body(active, "local_literal"))
    assert "%0 = 'lit'" in lines and "%2 = call __main__.Rec::__init__(%0, %1) [reader, may-raise] [initialize]" in lines


def test_a_caller_construct_copies_through_a_view_argument(active) -> None:
    # A `str` member copies out of a view of its family exactly as out of its
    # own borrow: the inferred view local is lent as it is, the member init is
    # the copy.
    for name, lent, construct in (
            ("ctor_from_view", "%2 = borrow (*%0)", "%4 = call __main__.Rec::__init__(%2, %3) [reader, may-raise] [initialize]"),
            ("ctor_from_string", "%1 = borrow (*%0)", "%3 = call __main__.Rec::__init__(%1, %2) [reader, may-raise] [initialize]")):
        fn = _body(active, name)
        lines = _lines(fn)
        assert lent in lines and construct in lines, name
        assert _retention(fn).conflicts == ()
        storage = frozenset(s.id for s in fn.slots if s.shape.is_storage)
        assert certify_storage_origins(prepared(fn), storage, active.definitions).verdict is MIRStorageVerdict.CERTIFIED
    assert "%1: StrView readonly-ref temporary residence=r0" in _lines(_body(active, "ctor_from_string"))


# --- reads and writes ---------------------------------------------------------------


def test_a_field_read_borrows_or_copies_the_field_place(active) -> None:
    lines = _lines(_body(active, "read_len"))
    assert "%1 = borrow (*%0).__main__.Rec::name" in lines
    assert "%2 = call stub tpy._builtins._funcs.len[Sized](%1) [pure, reader, may-raise]" in lines
    assert "%1 = copy (*%0).__main__.Rec::name may-raise [initialize]" in _lines(_body(active, "read_copy"))
    deps = prepared(_body(active, "read_len")).dependencies
    holder = MIRPlace(_body(active, "read_len").slots[1].id)
    assert deps.referents[MIRPoint(_body(active, "read_len").blocks[0].id, 1)][holder] == frozenset({
        MIRReferent(MIRPlace(_body(active, "read_len").slots[0].id, (_name_field(active),)), external=True)})


def test_a_field_write_is_an_in_place_replacement_event(active) -> None:
    fn = _body(active, "write_field")
    assert "(*%0).__main__.Rec::name = copy (*%1) may-raise [in_place]" in _lines(fn)
    assert "bb0 before 0: in_place (*%0).__main__.Rec::name" in [
        re.sub(r" @ \d+:\d+$", "", line.strip()) for line in dump_storage(analyze_storage(prepared(fn))).splitlines()]
    # The record holder around the field keeps its identity: no conflict.
    assert _retention(fn).conflicts == ()
    lines = _lines(_body(active, "append_field"))
    assert "%3 = borrow (*%0).__main__.Rec::name" in lines
    assert "(*%0).__main__.Rec::name = op += (%3, %2) may-raise [in_place]" in lines
    # A BigInt field's `+=` is an operation replacing the field.
    lines = _lines(_body(active, "big_add"))
    assert "(*%0).__main__.Big::total = op + (%2, %3) may-raise [in_place]" in lines
    assert "(*%0).__main__.Byt::data = copy (*%1) may-raise [in_place]" in _lines(_body(active, "byt_write"))
    assert "%1 = borrow (*%0).__main__.Strg::s" in _lines(_body(active, "strg_read"))


def test_a_local_record_field_write_keeps_its_storage_certified(active) -> None:
    fn = _body(active, "local_plain")
    lines = _lines(fn)
    assert "(*%4).__main__.Rec::name = copy (*%0) may-raise [in_place]" in lines
    assert "%5 = borrow (*%4).__main__.Rec::name" in lines
    assert _retention(fn).conflicts == ()
    storage = frozenset(s.id for s in fn.slots if s.shape.is_storage)
    assert certify_storage_origins(prepared(fn), storage, active.definitions).verdict is MIRStorageVerdict.CERTIFIED


def test_a_field_write_never_reaches_a_static_literal(active) -> None:
    # The literal is an external origin like the parameter's field, but its
    # storage is never written, so it cannot alias the replaced buffer.
    fn = _body(active, "lit_then_write")
    lines = _lines(fn)
    assert "%1 = 'lit'" in lines and "(*%0).__main__.Rec::name = 'x' [in_place]" in lines
    assert _retention(fn).conflicts == ()
    storage = frozenset(s.id for s in fn.slots if s.shape.is_storage)
    assert certify_storage_origins(prepared(fn), storage, active.definitions).verdict is MIRStorageVerdict.CERTIFIED


def test_sibling_fields_and_copies_do_not_conflict(active) -> None:
    assert _retention(_body(active, "sibling")).conflicts == ()
    # The copy is the body's own: a later write to the field cannot reach it.
    fn = _body(active, "copy_then_write")
    assert "%2 = copy (*%0).__main__.Rec::name may-raise [initialize]" in _lines(fn)
    assert _retention(fn).conflicts == ()


def test_a_method_call_lowers_through_its_summary(active) -> None:
    assert "(*%0).__main__.Rec::name = copy (*%1) may-raise [in_place]" in _lines(_body(active, "rename_m"))
    # The receiver is parameter 0: the method's field write lands on the caller's record.
    fn = _body(active, "method_write")
    assert "call __main__.Rec.rename_m(%0, %2) [writes={param0.name}, may-raise]" in _lines(fn)
    events = analyze_storage(prepared(fn))
    written = MIRPlace(fn.slots[0].id, (MIRDeref(), _name_field(active)))
    assert events.call_writes == {MIRPoint(fn.blocks[0].id, 1): (written,)}


# --- summaries and call writes ------------------------------------------------------


def test_a_field_write_is_a_published_may_write(active) -> None:
    result = active.summaries["rename"]
    assert result.state is MIRSummaryState.KNOWN
    assert result.summary.writes == frozenset({MIRParameterWrite(0, (th.THIRFieldIdentity(active.record, "name", STR),))})
    assert not result.summary.normal_return_only
    # The write propagates through a forwarding body, two hops deep.
    for name in ("forwarded_write", "two_hop"):
        assert active.summaries[name].state is MIRSummaryState.KNOWN
        assert active.summaries[name].summary.writes == result.summary.writes


def test_a_call_write_is_a_replacement_event_at_the_call(active) -> None:
    fn = _body(active, "forwarded_write")
    assert "call main::rename(%0, %2) [writes={param0.name}, may-raise]" in _lines(fn)
    events = analyze_storage(prepared(fn))
    point = MIRPoint(fn.blocks[0].id, 1)
    assert events.call_writes == {point: (MIRPlace(fn.slots[0].id, (MIRDeref(), _name_field(active))),)}
    assert "call-write (*%0).__main__.Rec::name" in dump_storage(events)
    # A scalar field write is shared mutation, never a replacement event.
    fn = _body(active, "name_after_renumber")
    assert analyze_storage(prepared(fn)).call_writes == {}


def _borrow_before_call(fn: MIRFunction) -> MIRFunction:
    """The lowered body with its field borrow moved just above the call, so
    the holder is live across the call -- the shape a view holder will have."""
    block = fn.blocks[0]
    borrow = next(s for s in block.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow)
                  and s.value.source.projections and isinstance(s.value.source.projections[-1], MIRField))
    rest = [s for s in block.statements if s is not borrow]
    call = next(i for i, s in enumerate(rest) if isinstance(s, MIRCallStmt))
    rest.insert(call, borrow)
    return replace(fn, blocks=(replace(block, statements=tuple(rest)), *fn.blocks[1:]))


def _call_point(fn: MIRFunction) -> MIRPoint:
    block = fn.blocks[0]
    index = next(i for i, s in enumerate(block.statements) if isinstance(s, MIRCallStmt))
    return MIRPoint(block.id, index)


def test_a_holder_of_the_written_field_conflicts_at_the_call(active) -> None:
    fn = _body(active, "name_after_rename")
    # As lowered, the field is borrowed after the call: no conflict.
    assert _retention(fn).conflicts == ()
    fn = _borrow_before_call(fn)
    conflict, = _retention(fn).conflicts
    written = MIRReferent(MIRPlace(fn.slots[0].id, (_name_field(active),)), external=True)
    assert (conflict.point, conflict.affected, conflict.retained) == (_call_point(fn), written, written)
    assert conflict.holder == fn.blocks[0].statements[conflict.point.index - 1].target


def test_a_holder_of_a_sibling_field_does_not_conflict(active) -> None:
    fn = _borrow_before_call(_body(active, "name_after_renumber"))
    assert _retention(fn).conflicts == ()


def test_distinct_external_roots_may_alias(active) -> None:
    fn = _borrow_before_call(_body(active, "name_after_other"))
    p, q = fn.slots[0].id, fn.slots[1].id
    conflict, = _retention(fn).conflicts
    assert conflict.point == _call_point(fn)
    assert conflict.affected == MIRReferent(MIRPlace(q, (_name_field(active),)), external=True)
    assert conflict.retained == MIRReferent(MIRPlace(p, (_name_field(active),)), external=True)


def test_a_call_write_into_private_storage_is_a_replacement_conflict(active) -> None:
    fn = _borrow_before_call(_body(active, "local_after_rename"))
    storage = frozenset(s.id for s in fn.slots if s.shape.is_storage and s.type == active.record)
    evidence = certify_storage_origins(prepared(fn), storage, active.definitions)
    assert evidence.verdict is MIRStorageVerdict.CONFLICT
    conflict, = evidence.conflicts
    assert (conflict.kind, conflict.site) == (MIRStorageConflictKind.REPLACEMENT, _call_point(fn))
    assert conflict.origin == MIRPlace(next(iter(storage)), (_name_field(active),))


def test_a_field_replacement_reaches_only_holders_at_or_under_it(active) -> None:
    name, n = _name_field(active), MIRField(MIRFieldId(active.record, "n"), INT32)
    fn = _body(active, "name_after_other")
    slots = {s.id: s for s in fn.slots}
    records = fn.record_layouts
    p, q, s = (MIRPlace(slot.id) for slot in fn.slots[:3])
    field = MIRReferent(MIRPlace(p.root, (name,)), True)
    # Under one origin: the field itself, never the record around it or a sibling.
    assert reaches(field, field, storage_exclusions(slots, records))
    assert not reaches(field, MIRReferent(p, True), storage_exclusions(slots, records))
    assert not reaches(field, MIRReferent(MIRPlace(p.root, (n,)), True), storage_exclusions(slots, records))
    # Across external roots that may alias: any owned-leaf holder, never a record.
    assert reaches(field, MIRReferent(MIRPlace(q.root, (name,)), True), storage_exclusions(slots, records))
    assert reaches(field, MIRReferent(s, True), storage_exclusions(slots, records))
    assert not reaches(field, MIRReferent(q, True), storage_exclusions(slots, records))
    # A whole-storage replacement reaches everything under its place.
    assert reaches(MIRReferent(p, True), field, storage_exclusions(slots, records))


# --- validator ------------------------------------------------------------------------


def test_validator_rejects_a_projection_inside_an_owned_leaf(active) -> None:
    fn = _body(active, "read_len")
    stmt = fn.blocks[0].statements[0]
    inner = MIRField(MIRFieldId(STR, "data"), INT32)
    damaged = replace(stmt, value=MIRBorrow(MIRPlace(stmt.value.source.root, (*stmt.value.source.projections, inner))))
    with pytest.raises(MIRValidationError, match="field needs record storage"):
        validate_function(replace(fn, blocks=(replace(fn.blocks[0], statements=(
            damaged, *fn.blocks[0].statements[1:])),)))


def test_validator_rejects_field_initializations_without_their_facts(active) -> None:
    fn = _ctor(active, "Rec")
    name, n = field_write(fn, "name"), field_write(fn, "n")
    param, scalar = name.value.source.root, n.value.source.root
    for old, value, message in (
            (name, replace(name.value, may_raise=False), "exit fact mismatch"),
            (name, MIRRead(MIRPlace(param)), "owned leaf write needs a copy"),
            (name, MIRConstant(3), "constant type or range mismatch"),
            # Lowering never initializes a field from a value of another type.
            (name, MIRCopy(MIRPlace(scalar), True), "owned leaf copy source mismatch"),
            (n, MIRCopy(MIRPlace(scalar)), "record destination type")):
        with pytest.raises(MIRValidationError, match=message):
            validate_function(rewritten(fn, old, replace(old, value=value)))
    # The body's exit fact follows the copy.
    with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
        validate_function(replace(fn, exceptional_exits=False))
    moved = _ctor(active, "OwnRec")
    member = field_write(moved, "name")
    with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
        validate_function(rewritten(moved, member, replace(member, value=MIRCopy(MIRPlace(member.value.source), True))))


def test_validator_checks_construct_and_field_write_facts(active) -> None:
    fn = _body(active, "local_plain")
    block = fn.blocks[0]

    def patched(match, change) -> MIRFunction:
        return replace(fn, blocks=(replace(block, statements=tuple(
            change(s) if match(s) else s for s in block.statements)),))

    is_construct = lambda s: (isinstance(s, MIRAssign) and isinstance(s.value, MIRCall)
                              and getattr(s.value.summary.callee.identity, "name", None) == "__init__")
    with pytest.raises(MIRValidationError, match="call exit fact mismatch"):
        validate_function(patched(is_construct, lambda s: replace(s, value=replace(s.value, may_raise=False))))
    is_write = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy) and s.target.projections
    with pytest.raises(MIRValidationError, match="field write needs its initialization or replacement fact"):
        validate_function(patched(is_write, lambda s: replace(
            s, storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))))
    with pytest.raises(MIRValidationError, match="copy exit fact mismatch"):
        validate_function(patched(is_write, lambda s: replace(s, value=replace(s.value, may_raise=False))))


def test_layouts_carry_the_owned_leaf_field_storage(active) -> None:
    fn = _body(active, "local_plain")
    layouts = fn.record_layouts
    assert layouts[STR].opaque and _name_field(active) in layouts[active.record].fields
    with pytest.raises(MIRValidationError, match="owned-leaf field needs its opaque layout"):
        validate_function(replace(fn, records=tuple(r for r in fn.records if r.type != STR)))


UNMODELED_SIBLING_SOURCE = """\
from tpy import StrView, int32


class Holder:
    view: StrView
    pair: tuple[int32, int32]

    def __init__(self, view: StrView) -> None:
        self.view = view
        self.pair = (1, 2)


def write(h: Holder, view: StrView) -> None:
    h.view = view
"""


def test_a_malformed_access_refuses_before_its_record_is_registered() -> None:
    compiler, modules = _compile(UNMODELED_SIBLING_SOURCE)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
        fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "write")
        stmt = fn.body[0]
        # The record's tuple field is unmodeled: registering the view member's record refuses it.
        result = lower_function(fn, MIRBodyId("fields", "write"), definitions=definitions)
        assert getattr(result, "reason", None) == "unsupported record fields"
        # A view member spelled at the wrong form is the access's own defect, reported first.
        malformed = replace(fn, body=(replace(stmt, target=replace(stmt.target, form=th.Form.STORAGE)),
                                      *fn.body[1:]))
        result = lower_function(malformed, MIRBodyId("fields", "write"), definitions=definitions)
        assert getattr(result, "reason", None) == "unsupported field type or form"

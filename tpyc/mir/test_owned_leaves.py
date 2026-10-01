"""Owned leaves (BigInt, str, String, bytes) as MIR values: owned storage with
an opaque layout, and readonly borrows of storage outside the body.

The probe section hand-builds bodies over an opaque leaf and over a record in
the same shape and asks every storage analysis for its answer: an owned leaf
is the record model with no interior, so the answers must agree."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.nodes import Form
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..type_def_registry import ParamPassing
from ..typesys import BIGINT, BOOL, BYTES, INT32, STR, STRING, NominalType, VoidType
from .call_contract import MIRGlobalId, MIRSummaryState
from .dependencies import MIRReferent, analyze_dependencies
from .definitions import MIRDefinitions, MIROwnedLeafDefinition
from .dump import dump_function
from .liveness import MIRPoint, analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRCompare, MIRConstant,
    MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRGoto, MIRMove, MIRNotCovered,
    MIROp,
    MIRPlace, MIRRead,
    MIRRecordLayout, MIRRecordWrite, MIRRecordWriteMode, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration, MIRValueKind,
)
from .retention import analyze_retention, may_overlap
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage
from .storage_evidence import MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins
from .validate import MIRValidationError, statement_may_raise, validate_function

RECORD_SOURCE = """\
from tpy import int32

class Rec:
    x: int32
    def __init__(self, x: int32):
        self.x = x

def make(x: int32) -> int32:
    r = Rec(x)
    return r.x
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    definitions: MIRDefinitions
    record: NominalType


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(RECORD_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        ctor, = ctx.thir_constructors.values()
        yield _Program(compiler, mir.definitions, ctor.record_layout.type)


@pytest.fixture
def active(program):
    with activate_compiler(program.compiler):
        yield program


# --- the storage probe --------------------------------------------------------

B = MIRBodyId("owned", "probe")
R0, R1 = MIRRegionId(B, 0), MIRRegionId(B, 1)


def _sid(i: int) -> MIRSlotId:
    return MIRSlotId(B, i)


def _bid(i: int) -> MIRBlockId:
    return MIRBlockId(B, i)


# Slot roles shared by every probe body, so the record and leaf variants
# number their slots identically.
STORAGE, HOLDER, RESULT, FIELD_VALUE, WRITER, FLAG, OPERAND = (_sid(i) for i in range(7))


def _layout(program: _Program, leaf: bool) -> MIRRecordLayout:
    if leaf:
        return MIRRecordLayout(STR, (), True, True, opaque=True)
    return program.definitions.get(None, program.record).layout


def _storage(typ, duration, kind=MIRSlotKind.LOCAL, **extra) -> MIRSlot:
    residence = None if kind is MIRSlotKind.PARAMETER else duration if isinstance(duration, MIRRegionId) else R0
    return MIRSlot(STORAGE, typ, kind, "x", form=Form.STORAGE, value_kind=MIRValueKind.OWNED,
                   storage_duration=duration, residence=residence, **extra)


def _holder(sid: MIRSlotId, typ, *, readonly: bool = True, residence=R0,
            kind: MIRSlotKind = MIRSlotKind.LOCAL) -> MIRSlot:
    return MIRSlot(sid, typ, kind, form=Form.BORROW, value_kind=MIRValueKind.BORROWED,
                   readonly=readonly, residence=residence)


class _Variant:
    """What differs between a record and an owned leaf in one probe shape:
    how the storage is filled, how a holder is read, and how the storage is
    replaced. Everything else is the same body."""

    def __init__(self, program: _Program, leaf: bool) -> None:
        self.leaf = leaf
        self.layout = _layout(program, leaf)
        self.type = self.layout.type

    def extra_slots(self) -> tuple[MIRSlot, ...]:
        if self.leaf:
            return (MIRSlot(RESULT, BOOL, MIRSlotKind.TEMPORARY, residence=R0),)
        field_slot = MIRSlot(FIELD_VALUE, INT32, MIRSlotKind.PARAMETER, "v", passing=ParamPassing.VALUE)
        return (MIRSlot(RESULT, INT32, MIRSlotKind.TEMPORARY, residence=R0), field_slot,
                _holder(WRITER, self.type, readonly=False))

    def init(self) -> MIRConstant | MIRConstruct:
        return MIRConstant("a") if self.leaf else MIRConstruct((FIELD_VALUE,))

    def use(self) -> MIRAssign:
        if self.leaf:
            return MIRAssign(MIRPlace(RESULT), MIRCompare("==", HOLDER, HOLDER))
        member = self.layout.fields[0]
        return MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(HOLDER, (MIRDeref(), member))))

    def replacement(self) -> tuple[MIRAssign, ...]:
        if self.leaf:
            return (MIRAssign(MIRPlace(STORAGE), MIRConstant("b"), storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE)),)
        # A record is replaced through a holder that rebinds to it (the lowering's in-place form).
        return (MIRAssign(MIRPlace(WRITER), MIRBorrow(MIRPlace(STORAGE))),
                MIRAssign(MIRPlace(WRITER, (MIRDeref(),)), MIRConstruct((FIELD_VALUE,)),
                          storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, WRITER)))

    def return_type(self):
        return BOOL if self.leaf else INT32


def _with_exits(fn: MIRFunction) -> MIRFunction:
    """A hand-built body with the exit fact its statements imply (a str
    buffer's allocation can throw), which these probes do not test."""
    slots = {s.id: s for s in fn.slots}
    return replace(fn, exceptional_exits=any(statement_may_raise(stmt, slots)
                                             for block in fn.blocks for stmt in block.statements))


def _function(variant: _Variant, slots, blocks, regions) -> MIRFunction:
    return _with_exits(MIRFunction(B, variant.return_type(), tuple(sorted(slots, key=lambda s: s.id.index)),
                                   tuple(blocks), _bid(0), (variant.layout,), regions=tuple(regions)))


def scope_end_body(variant: _Variant) -> MIRFunction:
    """A holder in the outer region borrows storage of an inner region and is
    read after the inner region ends."""
    slots = [_storage(variant.type, R1), _holder(HOLDER, variant.type), *variant.extra_slots()]
    blocks = [
        MIRBlock(_bid(0), (), MIRGoto(_bid(1)), R0),
        MIRBlock(_bid(1), (MIRAssign(MIRPlace(STORAGE), variant.init(),
                                     storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION)),
                           MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE)))), MIRGoto(_bid(2)), R1),
        MIRBlock(_bid(2), (variant.use(),), MIRReturn(RESULT), R0),
    ]
    return _function(variant, slots, blocks, (MIRRegion(R0, None, _bid(0)), MIRRegion(R1, R0, _bid(1))))


def replacement_body(variant: _Variant, *, retained: bool) -> MIRFunction:
    """Body storage replaced while a holder of it is (or is not) still read."""
    slots = [_storage(variant.type, MIRStorageDuration.BODY), _holder(HOLDER, variant.type), *variant.extra_slots()]
    init = MIRAssign(MIRPlace(STORAGE), variant.init(), storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    borrow = MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE)))
    statements = ((init, borrow, *variant.replacement(), variant.use()) if retained
                  else (init, borrow, variant.use(), *variant.replacement()))
    blocks = [MIRBlock(_bid(0), statements, MIRReturn(RESULT), R0)]
    return _function(variant, slots, blocks, (MIRRegion(R0, None, _bid(0)),))


def _answers(fn: MIRFunction, definitions: MIRDefinitions) -> dict:
    """Every storage analysis's answer, in slot roles rather than dumps."""
    validate_function(fn)
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    retention = analyze_retention(fn, liveness, dependencies, events)
    ends = analyze_scope_ends(fn)
    inspection = inspect_scope_lifetimes(fn)
    evidence = certify_storage_origins(fn, frozenset({STORAGE}), definitions)
    holders = {point: dict(refs) for point, refs in dependencies.active.items()
               if MIRPlace(HOLDER) in refs}
    return {
        "holder origins": {frozenset(refs[MIRPlace(HOLDER)]) for refs in holders.values()},
        "holder live at": frozenset(point for point, live in liveness.points.items() if HOLDER in live
                                    and point.block == _bid(2)),
        "storage writes": [(p.block.index, s.storage_write.mode) for p, s in
                           sorted(events.writes.items(), key=lambda item: (item[0].block.index, item[0].index))],
        "retention": [(c.affected, c.holder, c.retained) for c in retention.conflicts],
        "scope ends": {(edge.source.index, event.storage) for edge, events_ in ends.ends.items() for event in events_},
        "scope conflicts": [(c.ended, c.holder, c.retained) for c in inspection.conflicts],
        "verdict": evidence.verdict,
        "conflicts": [(c.kind, c.origin, c.holder) for c in evidence.conflicts],
        "gaps": [g.reason for g in evidence.gaps],
    }


def test_probe_scope_end_answers_as_a_record(active) -> None:
    leaf = _answers(scope_end_body(_Variant(active, True)), active.definitions)
    record = _answers(scope_end_body(_Variant(active, False)), active.definitions)
    assert leaf == record
    assert leaf["holder origins"] == {frozenset({MIRReferent(MIRPlace(STORAGE))})}
    assert leaf["scope ends"] == {(1, MIRPlace(STORAGE))}
    assert leaf["scope conflicts"] == [(MIRPlace(STORAGE), MIRPlace(HOLDER), MIRReferent(MIRPlace(STORAGE)))]
    assert leaf["verdict"] is MIRStorageVerdict.CONFLICT
    assert leaf["conflicts"] == [(MIRStorageConflictKind.SCOPE_END, MIRPlace(STORAGE), MIRPlace(HOLDER))]


@pytest.mark.parametrize("retained", [True, False])
def test_probe_replacement_answers_as_a_record(active, retained: bool) -> None:
    leaf = _answers(replacement_body(_Variant(active, True), retained=retained), active.definitions)
    record = _answers(replacement_body(_Variant(active, False), retained=retained), active.definitions)
    assert leaf == record
    assert [mode for _, mode in leaf["storage writes"]] == [MIRRecordWriteMode.INITIALIZE_ONCE,
                                                           MIRRecordWriteMode.IN_PLACE]
    if retained:
        assert leaf["retention"] == [(MIRReferent(MIRPlace(STORAGE)), MIRPlace(HOLDER), MIRReferent(MIRPlace(STORAGE)))]
        assert leaf["conflicts"] == [(MIRStorageConflictKind.REPLACEMENT, MIRPlace(STORAGE), MIRPlace(HOLDER))]
    else:
        assert leaf["retention"] == [] and leaf["verdict"] is MIRStorageVerdict.CERTIFIED


# --- owned-leaf producers and positions ----------------------------------------

def _leaf_body(statements, slots, *, blocks=None, regions=None, ret=RESULT, return_type=BOOL) -> MIRFunction:
    layout = MIRRecordLayout(STR, (), True, True, opaque=True)
    blocks = blocks or [MIRBlock(_bid(0), tuple(statements), MIRReturn(ret), R0)]
    return _with_exits(MIRFunction(B, return_type, tuple(sorted(slots, key=lambda s: s.id.index)), tuple(blocks),
                                   _bid(0), (layout,), regions=tuple(regions or (MIRRegion(R0, None, _bid(0)),))))


def _param_borrow(sid: MIRSlotId, typ=STR, passing=ParamPassing.VIEW) -> MIRSlot:
    return MIRSlot(sid, typ, MIRSlotKind.PARAMETER, "p", form=Form.BORROW,
                   value_kind=MIRValueKind.BORROWED, readonly=True, passing=passing)


def _write(target, value, mode=MIRRecordWriteMode.INITIALIZE_ONCE) -> MIRAssign:
    return MIRAssign(MIRPlace(target), value, storage_write=MIRRecordWrite(mode))


def _compare_holder() -> MIRAssign:
    return MIRAssign(MIRPlace(RESULT), MIRCompare("==", HOLDER, HOLDER))


def _result() -> MIRSlot:
    return MIRSlot(RESULT, BOOL, MIRSlotKind.TEMPORARY, residence=R0)


@pytest.mark.parametrize("producer", ["constant", "operation", "copy"])
def test_owned_storage_initializes_from_every_producer(active, producer: str) -> None:
    param, other = _param_borrow(OPERAND), _holder(FIELD_VALUE, STR)
    slots = [_storage(STR, MIRStorageDuration.BODY), _holder(HOLDER, STR), _result(), param, other]
    match producer:
        case "constant":
            prelude, value = [], MIRConstant("abc")
        case "operation":
            prelude = [MIRAssign(MIRPlace(FIELD_VALUE), MIRBorrow(MIRPlace(OPERAND, (MIRDeref(),))))]
            value = MIROp("+", (FIELD_VALUE, FIELD_VALUE), may_raise=True)
        case _:
            prelude, value = [], MIRCopy(MIRPlace(OPERAND, (MIRDeref(),)), may_raise=True)
    fn = _leaf_body([*prelude, _write(STORAGE, value), MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE))),
                     _compare_holder()], slots)
    answers = _answers(fn, active.definitions)
    assert answers["verdict"] is MIRStorageVerdict.CERTIFIED, answers
    assert answers["storage writes"] == [(0, MIRRecordWriteMode.INITIALIZE_ONCE)]


def test_a_call_result_needs_the_bodys_own_summary(active) -> None:
    # A call initializes owned storage only under a summary the body consumes.
    fn = _leaf_body([_write(STORAGE, MIRConstant("abc"))], [_storage(STR, MIRStorageDuration.BODY), _result()])
    call = MIRCall(None, ())
    broken = replace(fn, blocks=(replace(fn.blocks[0], statements=(_write(STORAGE, call),)),))
    with pytest.raises(MIRValidationError, match="call summary does not belong"):
        validate_function(broken)


def test_direct_reassignment_reading_itself_is_safe(active) -> None:
    # `s = s + "x"`: the borrow of the old value dies at the replacing operation.
    slots = [_storage(STR, MIRStorageDuration.BODY), _holder(HOLDER, STR),
             _holder(FIELD_VALUE, STR, kind=MIRSlotKind.TEMPORARY), _result()]
    fn = _leaf_body([
        _write(STORAGE, MIRConstant("a")),
        MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE))),
        MIRAssign(MIRPlace(FIELD_VALUE), MIRConstant("x")),
        _write(STORAGE, MIROp("+", (HOLDER, FIELD_VALUE), may_raise=True), MIRRecordWriteMode.IN_PLACE),
        MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE))),
        _compare_holder(),
    ], slots)
    answers = _answers(fn, active.definitions)
    assert answers["retention"] == [] and answers["verdict"] is MIRStorageVerdict.CERTIFIED
    # The literal holder borrows static storage: an external origin, never ended.
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert deps.referents[MIRPoint(_bid(0), 3)][MIRPlace(FIELD_VALUE)] == frozenset(
        {MIRReferent(MIRPlace(FIELD_VALUE), external=True)})


def test_replacement_needs_initialized_storage(active) -> None:
    fn = _leaf_body([_write(STORAGE, MIRConstant("a"), MIRRecordWriteMode.IN_PLACE)],
                    [_storage(STR, MIRStorageDuration.BODY), _result()], ret=STORAGE, return_type=STR)
    with pytest.raises(MIRValidationError, match="replacement before storage initialization"):
        validate_function(fn)


@pytest.mark.parametrize("replaced", [False, True])
def test_entry_owned_parameter_is_body_storage(active, replaced: bool) -> None:
    param = _storage(STR, MIRStorageDuration.BODY, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)
    replacement = [_write(STORAGE, MIRConstant("b"), MIRRecordWriteMode.IN_PLACE)] if replaced else []
    fn = _leaf_body([MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE))), *replacement, _compare_holder()],
                    [param, _holder(HOLDER, STR), _result()])
    answers = _answers(fn, active.definitions)
    # Initialized before entry: materialized, and ended at the body's exit.
    assert answers["scope ends"] == {(0, MIRPlace(STORAGE))}
    assert "never materialized" not in " ".join(answers["gaps"])
    if replaced:
        assert answers["conflicts"] == [(MIRStorageConflictKind.REPLACEMENT, MIRPlace(STORAGE), MIRPlace(HOLDER))]
    else:
        assert answers["verdict"] is MIRStorageVerdict.CERTIFIED


def _loop_body(mode: MIRRecordWriteMode, duration) -> MIRFunction:
    flag = MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, "flag", passing=ParamPassing.VALUE)
    inner = R1 if isinstance(duration, MIRRegionId) else R0
    slots = [_storage(STR, duration), _holder(HOLDER, STR, residence=inner),
             replace(_result(), residence=inner), flag]
    blocks = [
        MIRBlock(_bid(0), (), MIRGoto(_bid(1)), R0),
        MIRBlock(_bid(1), (), MIRBranch(FLAG, _bid(2), _bid(3)), R0),
        MIRBlock(_bid(2), (_write(STORAGE, MIRConstant("a"), mode),
                           MIRAssign(MIRPlace(HOLDER), MIRBorrow(MIRPlace(STORAGE))), _compare_holder()),
                 MIRGoto(_bid(1)), R1),
        MIRBlock(_bid(3), (), MIRReturn(), R0),
    ]
    return _leaf_body((), slots, blocks=blocks, regions=(MIRRegion(R0, None, _bid(0)), MIRRegion(R1, R0, _bid(2))),
                      return_type=VoidType())


def test_loop_activation_initializes_each_iteration(active) -> None:
    fn = _loop_body(MIRRecordWriteMode.INITIALIZE_REGION, R1)
    answers = _answers(fn, active.definitions)
    assert answers["verdict"] is MIRStorageVerdict.CERTIFIED, answers
    # Each activation ends at the back edge.
    assert answers["scope ends"] == {(2, MIRPlace(STORAGE))}


def test_body_storage_cannot_initialize_in_a_loop(active) -> None:
    with pytest.raises(MIRValidationError, match="owning operation in cycle"):
        validate_function(_loop_body(MIRRecordWriteMode.INITIALIZE_ONCE, MIRStorageDuration.BODY))


def test_opaque_definitions_are_a_builtin_certificate(active) -> None:
    for typ in (BIGINT, STR, STRING, BYTES):
        definition = active.definitions.get(None, typ)
        assert isinstance(definition, MIROwnedLeafDefinition)
        assert definition.layout == MIRRecordLayout(typ, (), True, True, opaque=True)
    # Nothing else earns it: a record still needs its verified constructor.
    assert not isinstance(active.definitions.get(None, active.record), MIROwnedLeafDefinition)


# --- lowering ---------------------------------------------------------------------

SOURCE = """\
from tpy import int32, char, String, Own

G: str = "glob"
N: int = 7
B: bytes = b"gb"
COUNT: int32 = 0

def param_borrow(s: str, n: int, t: String, b: bytes) -> bool:
    # Borrowing parameters: a view, two const refs and a bytes view, read in place.
    return s == "x" and n > 0 and t != t and b == b

def by_value(s: Own[str]) -> str:
    # A by-value parameter is the body's storage and returns itself.
    return s

def local(n: int) -> int:
    # A local copies the const-ref parameter, then is replaced in place.
    total = n
    total = total * 2
    return total

def temporary(a: int, b: int) -> bool:
    # `a * b` is a temporary its comparison's full expression ends.
    return a * b > a

def ret_copy(s: str) -> str:
    # The view parameter is copied into the result.
    return s

def global_read() -> int:
    x = N
    return x + N

def global_print() -> None:
    print(G, N, B)

def prints(s: str, n: int, b: bytes) -> None:
    print(s, n, b, "lit")

def element(s: str, b: bytes, i: int32) -> bool:
    return s[i] == 'a' and b[i] == 1

def param_copy(s: str) -> str:
    # The reassigned view parameter gets an owned copy; `s = s + "x"` appends to it.
    s = s + "x"
    return s

def append(a: str) -> str:
    s: str = a
    s += "y"
    s += a
    return s

def convert(c: char, i: int32) -> str:
    x: int = i
    s: str = c
    return s

def literal() -> str:
    return "abc"

def promoted(total: int, i: int32) -> int:
    # `int32.__int__` promotes `i` into BigInt's `+`: a conversion feeding the operator.
    return total + i

def promoted_compare(i: int32, total: int) -> bool:
    return i == total

def big_copy(n: int) -> int:
    # A BigInt copy's allocation failure is a panic: no exceptional exit.
    return n

def loop(n: int) -> int:
    k = n
    while k < n * 2:
        k = k + 1
    return k

def mixed(n: int, i: int32) -> bool:
    return i > n

def neg(n: int) -> int:
    return -n

def takes(s: str) -> int32:
    return 1

def takes_big(n: int) -> int32:
    return 1

def takes_own(s: Own[str]) -> int32:
    return 1

def calls_with_str(s: str) -> int32:
    # Lent for the call: a borrow of the view parameter's storage.
    return takes(s)

def lends(s: str, n: int) -> int32:
    return takes(s) + takes_big(n)

def copies(s: str) -> int32:
    # A by-value parameter gets its own copy, built in the full expression's storage.
    return takes_own(s)

def literal_arguments() -> int32:
    # A borrowed literal is static storage; an owning one is materialized.
    return takes("lit") + takes_own("own")

def fresh_result(s: str) -> bool:
    # The callee returns by value: a fresh temporary, not a borrow of `s`.
    return ret_copy(s) == "x"

def temporary_argument(s: str) -> int32:
    return takes(ret_copy(s))

def result_local(s: str) -> str:
    t = ret_copy(s)
    return t

def global_arguments() -> int32:
    return takes(G) + takes_big(N)

def overlap(s: str) -> bool:
    # `s` may borrow G itself: two readonly borrows of one storage never conflict.
    return s == G and takes(G) == 1

def overlap_write(s: str) -> bool:
    global COUNT
    COUNT = 1
    return s == G

def scaled(x: float, n: int) -> int32:
    return 1

def expression_arguments(x: float, n: int) -> int32:
    # Admitted expressions are arguments: each is evaluated into a temporary before the call,
    # and the int literal converts exactly into the float operator's parameter.
    return scaled(x * 1000, n + 1)

def print_call(s: str) -> None:
    # The call's owned result is a temporary of the print call's full expression.
    print(ret_copy(s), end="")

def bytes_literal() -> bytes:
    return b"ab"
"""


@dataclass(frozen=True)
class _Lowered:
    compiler: object
    definitions: MIRDefinitions
    thir: dict
    bodies: dict
    summaries: dict


@pytest.fixture(scope="module")
def lowered():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        bodies = {name: lower_function(fn, MIRBodyId("owned", name), definitions=mir.definitions,
                                       summaries=mir.workspace.summaries)
                  for name, fn in functions.items()}
        summaries = {identity.name: result for identity, result in mir.workspace.summaries.items()}
        yield _Lowered(compiler, mir.definitions, functions, bodies, summaries)


@pytest.fixture
def lowered_active(lowered):
    with activate_compiler(lowered.compiler):
        yield lowered


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _storage_roots(fn: MIRFunction) -> frozenset[MIRSlotId]:
    return frozenset(s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED)


def test_owned_leaf_analyses_complete_without_conflicts(lowered_active) -> None:
    # A loop joins owned storage replaced in place with per-iteration temporaries.
    fn = lowered_active.bodies["loop"]
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    assert not isinstance(dependencies, MIRNotCovered) and not isinstance(events, MIRNotCovered)
    assert analyze_retention(fn, liveness, dependencies, events).conflicts == ()
    assert not isinstance(analyze_scope_ends(fn), MIRNotCovered)
    assert inspect_scope_lifetimes(fn).conflicts == ()


def test_owned_leaf_storage_roots_are_certified(lowered_active) -> None:
    # Every lowered body with owned storage: the snippet cases cannot pin this
    # (the harness's `certified` rung is the THIR storage certificate, which
    # has nothing to prove for an owned leaf), so the unit keeps the matrix.
    rooted = {name: fn for name, fn in lowered_active.bodies.items()
              if isinstance(fn, MIRFunction) and _storage_roots(fn)}
    assert "loop" in rooted and len(rooted) > 1
    for name, fn in rooted.items():
        evidence = certify_storage_origins(fn, _storage_roots(fn), lowered_active.definitions)
        assert evidence.verdict is MIRStorageVerdict.CERTIFIED, (name, evidence.gaps)


def test_owned_leaf_slots_take_the_opaque_layout(lowered_active) -> None:
    # `param_borrow` borrows all four leaf types; `append` owns its str.
    for name in ("param_borrow", "append"):
        fn = lowered_active.bodies[name]
        leaves = [s for s in fn.slots if s.value_kind in (MIRValueKind.OWNED, MIRValueKind.BORROWED)]
        assert leaves
        for slot in leaves:
            layout, = (r for r in fn.records if r.type == slot.type)
            assert layout.opaque and layout.fields == ()


def _lines(fn: MIRFunction) -> list[str]:
    """The dump's lines without source locations, which only restate the SOURCE layout."""
    return [re.sub(r" @ \d+:\d+$", "", line.strip()) for line in dump_function(fn).splitlines()]


def test_parameters_borrow_or_own_by_their_passing(lowered_active) -> None:
    fn = lowered_active.bodies["param_borrow"]
    for name, passing in (("s", ParamPassing.VIEW), ("n", ParamPassing.CONST_REF),
                          ("t", ParamPassing.CONST_REF), ("b", ParamPassing.VIEW)):
        slot = _slot(fn, name)
        assert (slot.value_kind, slot.readonly, slot.form, slot.passing) == (
            MIRValueKind.BORROWED, True, Form.BORROW, passing)
    entry = analyze_dependencies(fn, analyze_liveness(fn)).entry_active
    assert all(refs == frozenset({MIRReferent(leaf, external=True)}) for leaf, refs in entry.items())
    owned = _slot(lowered_active.bodies["by_value"], "s")
    assert (owned.value_kind, owned.storage_duration, owned.passing) == (
        MIRValueKind.OWNED, MIRStorageDuration.BODY, ParamPassing.VALUE)
    # The by-value parameter returns itself, with no copy.
    assert _lines(lowered_active.bodies["by_value"])[-1] == f"return %{owned.id.index}"


def test_locals_initialize_once_then_replace_in_place(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["local"])
    assert "%0: int readonly-ref parameter n" in lines
    assert "%1: int owned-storage local total duration=body residence=r0" in lines
    # A BigInt copy cannot raise: its allocation failure is a panic.
    assert "%1 = copy (*%0) [initialize_once]" in lines
    assert any(line.startswith("%1 = op * (") and line.endswith("may-raise [in_place]") for line in lines)


def test_operand_temporaries_live_in_their_full_expression(lowered_active) -> None:
    fn = lowered_active.bodies["temporary"]
    temporary, = (s for s in fn.slots if s.value_kind is MIRValueKind.OWNED)
    assert temporary.kind is MIRSlotKind.TEMPORARY and isinstance(temporary.storage_duration, MIRRegionId)
    region = next(r for r in fn.regions if r.id == temporary.storage_duration)
    assert region.parent is not None
    ends = analyze_scope_ends(fn)
    assert {event.storage for events in ends.ends.values() for event in events} == {MIRPlace(temporary.id)}


def test_a_borrowed_return_is_copied_into_the_result(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["ret_copy"])
    assert "%1 = copy (*%0) may-raise [initialize_once]" in lines and lines[-1] == "return %1"


def test_owned_leaf_globals_are_readonly_external_handles(lowered_active) -> None:
    fn = lowered_active.bodies["global_print"]
    handles = [s for s in fn.slots if s.kind is MIRSlotKind.GLOBAL]
    assert sorted(s.global_id.name for s in handles) == ["B", "G", "N"]
    assert all(s.value_kind is MIRValueKind.BORROWED and s.readonly for s in handles)
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert {leaf.root for leaf in deps.entry_active} <= {s.id for s in handles}
    point = MIRPoint(fn.blocks[0].id, 3)
    for holder, refs in deps.active[point].items():
        # Each borrow of a global names that global's handle as its external origin.
        (origin,) = refs
        assert origin.external and fn.slots[origin.place.root.index].kind is MIRSlotKind.GLOBAL
    # THIR publishes the binding the handle comes from.
    ret = lowered_active.thir["global_read"].body[-1]
    names = [node for node in (ret.value.left, ret.value.right) if isinstance(node, th.THIRName)]
    assert any(n.global_binding is not None and n.global_binding.name == "N" for n in names)


def test_print_reads_owned_leaves_through_holders(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["prints"])
    assert lines[-3:-1] == ["%6 = 'lit'", "print (%3, %4, %5, %6)"]


def test_element_reads_are_raising_operations(lowered_active) -> None:
    ops = [s.value for b in lowered_active.bodies["element"].blocks for s in b.statements
           if isinstance(s, MIRAssign) and isinstance(s.value, MIROp)]
    assert [op.op for op in ops] == ["getitem", "getitem"] and all(op.may_raise for op in ops)


def test_param_copy_and_append(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["param_copy"])
    assert "%0: str readonly-ref parameter s" in lines and "%1: str owned-storage local s duration=body residence=r0" in lines
    assert "%1 = copy (*%0) may-raise [initialize_once]" in lines
    assert any(line.startswith("%1 = op += (") and "[in_place]" in line for line in lines)
    appends = [line for line in _lines(lowered_active.bodies["append"]) if line.startswith("%1 = op += (")]
    assert len(appends) == 2
    # `s: str = a` is the form conversion of a borrowed read: a copy.
    assert "%1 = copy (*%0) may-raise [initialize_once]" in _lines(lowered_active.bodies["append"])


def test_conversions_and_mixed_operations(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["convert"])
    assert any("op coerce:fixed_int_to_bigint" in line and "[initialize_once]" in line for line in lines)
    assert any("op coerce:char_to_str" in line for line in lines)
    compare = next(s.value for b in lowered_active.bodies["mixed"].blocks for s in b.statements
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIRCompare))
    fn = lowered_active.bodies["mixed"]
    assert (fn.slots[compare.left.index].type, fn.slots[compare.right.index].type) == (INT32, BIGINT)
    assert any("op __neg__" in line for line in _lines(lowered_active.bodies["neg"]))


def test_promotion_lowers_as_a_conversion_feeding_the_operator(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["promoted"])
    assert "%4 = read %1" in lines
    assert "%5 = op coerce:__int__ (%4) may-raise [initialize_region]" in lines
    assert "%6 = borrow %5" in lines and "%2 = op + (%3, %6) may-raise [initialize_once]" in lines
    fn = lowered_active.bodies["promoted_compare"]
    compare, = (s.value for s in _statements(fn) if isinstance(s, MIRAssign) and isinstance(s.value, MIRCompare))
    # Both operands are BigInt holders once the promotion converted the int32.
    assert {fn.slots[compare.left.index].type, fn.slots[compare.right.index].type} == {BIGINT}


def test_literal_return_materializes_an_owned_constant(lowered_active) -> None:
    assert _lines(lowered_active.bodies["literal"])[-2:] == ["%0 = 'abc' [initialize_once]", "return %0"]


def test_exit_fact_follows_allocation_and_raising_operations(lowered_active) -> None:
    bodies = lowered_active.bodies
    # A BigInt copy cannot throw; a str copy and a str constant allocate, and a buffer allocation can.
    assert bodies["big_copy"].exceptional_exits is False
    assert bodies["ret_copy"].exceptional_exits is True and bodies["literal"].exceptional_exits is True
    assert bodies["local"].exceptional_exits is True  # `total * 2` is a raising operation
    assert bodies["param_borrow"].exceptional_exits is False  # comparisons of borrowed leaves
    assert "exceptional exits" in _lines(bodies["ret_copy"]) and "exceptional exits" not in _lines(bodies["big_copy"])
    for name in ("big_copy", "ret_copy"):
        fn = bodies[name]
        for wrong in (not fn.exceptional_exits, int(fn.exceptional_exits)):
            with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
                validate_function(replace(fn, exceptional_exits=wrong))


def test_an_owned_leaf_operation_cannot_claim_it_never_raises(lowered_active) -> None:
    # No fact says a BigInt operation cannot raise, so the owned-leaf arm refuses the claim like the inert one.
    fn = lowered_active.bodies["local"]
    damaged = replace(fn, blocks=tuple(replace(b, statements=tuple(
        replace(s, value=replace(s.value, may_raise=False))
        if isinstance(s, MIRAssign) and isinstance(s.value, MIROp) else s for s in b.statements))
        for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="primitive operation must be a possible exceptional exit"):
        validate_function(damaged)


def _statements(fn: MIRFunction) -> list:
    return [stmt for block in fn.blocks for stmt in block.statements]


def _calls(fn: MIRFunction) -> list[MIRCall]:
    return [stmt.value for stmt in _statements(fn) if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)]


def test_owned_leaf_arguments_are_lent_at_a_borrowing_passing(lowered_active) -> None:
    fn = lowered_active.bodies["lends"]
    slots = {s.id: s for s in fn.slots}
    arguments = [slots[a] for call in _calls(fn) for a in call.arguments]
    assert [(a.type, a.value_kind, a.readonly, a.kind) for a in arguments] == [
        (STR, MIRValueKind.BORROWED, True, MIRSlotKind.TEMPORARY),
        (BIGINT, MIRValueKind.BORROWED, True, MIRSlotKind.TEMPORARY)]
    # No copy: each holder borrows the parameter's own external storage.
    assert not any(isinstance(stmt.value, MIRCopy) for stmt in _statements(fn) if isinstance(stmt, MIRAssign))
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    params = {s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall):
                refs = deps.active[MIRPoint(block.id, index)]
                for argument in stmt.value.arguments:
                    (origin,) = refs[MIRPlace(argument)]
                    assert origin.external and origin.place.root in params
    summary = lowered_active.bodies["calls_with_str"].call_summaries[0]
    assert summary.parameters[0].passing is ParamPassing.VIEW and summary.parameters[0].readonly


def test_owned_leaf_arguments_are_copied_at_a_by_value_passing(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["copies"])
    assert "%2 = copy (*%0) may-raise [initialize_region]" in lines
    assert any(line.startswith("%3 = call main::takes_own(%2)") for line in lines)
    fn = lowered_active.bodies["copies"]
    copy, = (s for s in fn.slots if s.value_kind is MIRValueKind.OWNED)
    assert copy.kind is MIRSlotKind.TEMPORARY and isinstance(copy.storage_duration, MIRRegionId)
    assert fn.call_summaries[0].parameters[0].passing is ParamPassing.VALUE
    assert not fn.call_summaries[0].parameters[0].readonly


def test_literal_arguments_are_static_or_materialized(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["literal_arguments"])
    # The view parameter borrows the literal; the owning one gets a materialized buffer.
    assert any(re.fullmatch(r"%\d+ = 'lit'", line) for line in lines)
    assert any(re.fullmatch(r"%\d+ = 'own' \[initialize_region\]", line) for line in lines)


def test_owned_leaf_results_are_fresh_owned_temporaries(lowered_active) -> None:
    fn = lowered_active.bodies["fresh_result"]
    call, = (stmt for stmt in _statements(fn) if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall))
    result = fn.slots[call.target.root.index]
    assert result.value_kind is MIRValueKind.OWNED and result.kind is MIRSlotKind.TEMPORARY
    assert call.storage_write == MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION)
    # The result's holder names the temporary, never the argument's parameter.
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    origins = {ref for refs in deps.referents.values() for leaf, values in refs.items() for ref in values
               if fn.slots[leaf.root.index].kind is MIRSlotKind.TEMPORARY and ref.place.root == result.id}
    assert origins == {MIRReferent(MIRPlace(result.id))}
    assert call.value.may_raise and fn.exceptional_exits  # the callee's copy may raise
    local = _slot(lowered_active.bodies["result_local"], "t")
    assert local.value_kind is MIRValueKind.OWNED and local.kind is MIRSlotKind.LOCAL
    assert "%1 = call main::ret_copy(%2) [reader, may-raise] [initialize_once]" in _lines(
        lowered_active.bodies["result_local"])
    temporary = lowered_active.bodies["temporary_argument"]
    produced, consumed = _calls(temporary)
    assert produced.summary.callee.identity.name == "ret_copy" and consumed.summary.callee.identity.name == "takes"


def test_global_arguments_are_lent_or_copied(lowered_active) -> None:
    fn = lowered_active.bodies["global_arguments"]
    handles = {s.id for s in fn.slots if s.kind is MIRSlotKind.GLOBAL}
    borrows = [stmt.value.source for stmt in _statements(fn)
               if isinstance(stmt, MIRAssign) and isinstance(stmt.value, (MIRBorrow, MIRCopy))]
    assert [(p.root in handles, p.projections) for p in borrows] == [(True, (MIRDeref(),))] * 2
    summaries = lowered_active.summaries
    assert summaries["global_arguments"].summary.global_reads == frozenset(
        {MIRGlobalId("main", "G"), MIRGlobalId("main", "N")})


def test_parameter_overlapping_a_read_global_is_no_conflict(lowered_active) -> None:
    fn = lowered_active.bodies["overlap"]
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    retention = analyze_retention(fn, liveness, dependencies, analyze_storage(fn))
    assert retention.conflicts == () and inspect_scope_lifetimes(fn).conflicts == ()
    # Both are external origins: the analyses must treat them as possibly one storage.
    param, = (s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    handle, = (s for s in fn.slots if s.kind is MIRSlotKind.GLOBAL)
    assert handle.name == "G" and "global main::G" in dump_function(fn)
    assert {leaf.root for leaf in dependencies.entry_active} <= {param, handle.id}
    # `s == G` compares a borrow of each; their referents are both external and may overlap.
    compare = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements)
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIRCompare))
    refs = dependencies.referents[MIRPoint(compare[0].id, compare[1])]
    left, right = (refs[MIRPlace(o)] for o in (compare[0].statements[compare[1]].value.left,
                                               compare[0].statements[compare[1]].value.right))
    (s_ref,), (g_ref,) = left, right
    assert s_ref.external and s_ref.place.root == param
    assert g_ref.external and g_ref.place.root == handle.id
    assert may_overlap(s_ref, g_ref)
    summaries = lowered_active.summaries
    assert summaries["overlap"].state is MIRSummaryState.KNOWN, summaries["overlap"].reason
    assert summaries["overlap"].summary.global_reads == frozenset({MIRGlobalId("main", "G")})
    # Writing a global (here a scalar one) keeps the same shape opaque.
    assert isinstance(lowered_active.bodies["overlap_write"], MIRFunction)
    assert summaries["overlap_write"].state is MIRSummaryState.OPAQUE
    assert summaries["overlap_write"].reason == "summary global access"


def test_validator_rejects_mismatched_owned_leaf_arguments_and_results(lowered_active) -> None:
    # A borrowed parameter where the callee takes its own copy of a temporary.
    copies = lowered_active.bodies["copies"]
    param = next(s.id for s in copies.slots if s.kind is MIRSlotKind.PARAMETER)
    damaged = _replace_first(copies, lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCall),
                             lambda s: replace(s, value=replace(s.value, arguments=(param,))))
    with pytest.raises(MIRValidationError, match="call owned-leaf argument mismatch"):
        validate_function(damaged)
    # Owned storage where the callee borrows: `takes(ret_copy(s))` handing the result itself.
    temporary = lowered_active.bodies["temporary_argument"]
    produced = next(s for s in _statements(temporary) if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    damaged = _replace_first(temporary, lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCall)
                             and s.value.summary.callee.identity.name == "takes",
                             lambda s: replace(s, value=replace(s.value, arguments=(produced.target.root,))))
    with pytest.raises(MIRValidationError, match="call owned-leaf argument mismatch"):
        validate_function(damaged)
    # An owned call result must be the callee's by-value owned leaf.
    fresh = lowered_active.bodies["fresh_result"]
    call_stmt = next(s for s in _statements(fresh) if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    retyped = replace(fresh, slots=tuple(replace(s, type=BIGINT) if s.id == call_stmt.target.root else s
                                         for s in fresh.slots),
                      records=(*fresh.records, MIRRecordLayout(BIGINT, (), True, True, opaque=True)))
    with pytest.raises(MIRValidationError, match="call owned result type mismatch"):
        validate_function(retyped)


def test_expression_arguments_are_evaluated_before_the_call(lowered_active) -> None:
    lines = _lines(lowered_active.bodies["expression_arguments"])
    call = next(i for i, line in enumerate(lines) if "call main::scaled(" in line)
    # Both arguments are built before the call reads them; the literal is the float it converts to.
    assert any(re.fullmatch(r"%\d+ = 1000\.0", line) for line in lines[:call])
    assert sum(" = op " in line for line in lines[:call]) == 2


def test_print_arguments_share_one_full_expression_region(lowered_active) -> None:
    fn = lowered_active.bodies["print_call"]
    lines = _lines(fn)
    result = next(s for s in fn.slots if s.value_kind is MIRValueKind.OWNED)
    # The call result lives in the region the print call opens, and ends with it.
    assert isinstance(result.storage_duration, MIRRegionId) and result.storage_duration.index != 0
    call, printed = (next(i for i, line in enumerate(lines) if marker in line)
                     for marker in ("call main::ret_copy(", "print ("))
    assert call < printed
    assert lowered_active.summaries["print_call"].reason == "summary output effect"


def test_loop_temporaries_initialize_per_activation(lowered_active) -> None:
    fn = lowered_active.bodies["loop"]
    writes = analyze_storage(fn).writes
    modes = sorted(stmt.storage_write.mode.name for stmt in writes.values())
    assert modes.count("INITIALIZE_REGION") == 3 and modes.count("IN_PLACE") == 1


# --- damage -----------------------------------------------------------------------

def _replace_slot(fn: MIRFunction, name: str, **changes) -> MIRFunction:
    return replace(fn, slots=tuple(replace(s, **changes) if s.name == name else s for s in fn.slots))


def _replace_first(fn: MIRFunction, match, change) -> MIRFunction:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements) if match(s))
    stmt = change(block.statements[index])
    return replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], stmt, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))


def test_validator_rejects_damaged_owned_leaf_facts(lowered_active) -> None:
    local = lowered_active.bodies["local"]
    ret_copy = lowered_active.bodies["ret_copy"]
    validate_function(local)
    # A str slot claiming to be a scalar leaf.
    with pytest.raises(MIRValidationError, match="unsupported slot type or form"):
        validate_function(_replace_slot(ret_copy, "s", value_kind=MIRValueKind.SCALAR, form=Form.VALUE))
    # A view claiming owned storage.
    view = NominalType("StrView", (), _module_qname="tpy.StrView")
    with pytest.raises(MIRValidationError, match="unsupported record storage type or form"):
        validate_function(_replace_slot(local, "total", type=view))
    # A field projection under an opaque layout.
    field = MIRField(MIRFieldId(BIGINT, "digits"), INT32)
    projected = _replace_first(local, lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy),
                               lambda s: replace(s, value=replace(s.value, source=MIRPlace(
                                   s.value.source.root, (*s.value.source.projections, field)))))
    with pytest.raises(MIRValidationError, match="field projection under an opaque layout"):
        validate_function(projected)
    # A write to owned storage without its storage event.
    unmarked = _replace_first(local, lambda s: isinstance(s, MIRAssign) and s.storage_write is not None,
                              lambda s: replace(s, storage_write=None))
    with pytest.raises(MIRValidationError, match="initialization or replacement fact"):
        validate_function(unmarked)
    # A str copy claiming it cannot raise.
    silent = _replace_first(ret_copy, lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy),
                            lambda s: replace(s, value=replace(s.value, may_raise=False)))
    with pytest.raises(MIRValidationError, match="copy exit fact mismatch"):
        validate_function(silent)
    # An owned-leaf borrow that could write.
    with pytest.raises(MIRValidationError, match="unsupported reference slot type or form"):
        validate_function(_replace_slot(ret_copy, "s", readonly=False))


def _rewrite_thir(node: object, match: Callable[[object], bool], change: Callable[[object], object]) -> object:
    """`node` with every sub-node `match` accepts replaced by `change(sub)`."""
    if match(node):
        return change(node)
    if isinstance(node, tuple):
        return tuple(_rewrite_thir(item, match, change) for item in node)
    if not isinstance(node, th.THIRNode):
        return node
    updates = {}
    for name in node.__dataclass_fields__:
        value = getattr(node, name)
        rewritten = _rewrite_thir(value, match, change)
        if rewritten is not value:
            updates[name] = rewritten
    return replace(node, **updates) if updates else node


def _refused(lowered_active, name: str, match, change) -> str:
    source = lowered_active.thir[name]
    damaged = replace(source, body=_rewrite_thir(source.body, match, change))
    # None of the damaged bodies calls a user function, so no summaries are needed.
    result = lower_function(damaged, MIRBodyId("owned", name), definitions=lowered_active.definitions)
    assert isinstance(result, MIRNotCovered), result
    return result.reason


def test_hand_built_shapes_no_source_reaches_refuse(lowered_active) -> None:
    # A move out of an owned leaf: the by-value parameter handed back through a move.
    assert _refused(lowered_active, "by_value", lambda n: isinstance(n, th.THIRName),
                    lambda n: th.THIRMove(result_type=n.result_type, value=n, form=Form.STORAGE)) == "owned-leaf move"
    # A form conversion whose source is not a borrowed name.
    assert _refused(lowered_active, "ret_copy", lambda n: isinstance(n, th.THIRFormConvert),
                    lambda n: replace(n, value=replace(n.value, form=Form.STORAGE))) == "unsupported form conversion"
    # A unary operator with no resolved runtime method.
    assert _refused(lowered_active, "neg", lambda n: isinstance(n, th.THIRUnaryArith),
                    lambda n: replace(n, resolved=None)) == "uncertified unary operation"
    # A str indexing a str (`s[s]`): the index is not an inert leaf.
    assert _refused(lowered_active, "element", lambda n: isinstance(n, th.THIRSubscript),
                    lambda n: replace(n, index=n.receiver)) == "uncertified element read"
    # A static bytes span at an owning sink, which would own no buffer.
    assert _refused(lowered_active, "bytes_literal", lambda n: isinstance(n, th.THIRBytesLiteral),
                    lambda n: replace(n, form=Form.BORROW)) == "borrowed bytes literal at an owning sink"


def test_validator_rejects_damaged_owned_leaf_slots_and_layouts(lowered_active) -> None:
    local = lowered_active.bodies["local"]
    # A borrowed owned-leaf parameter the callee could write through.
    with pytest.raises(MIRValidationError, match="owned leaf parameter borrow needs a readonly passing"):
        validate_function(_replace_slot(local, "n", passing=ParamPassing.MUT_REF))
    # Owned storage placed as a readonly slot.
    with pytest.raises(MIRValidationError, match="owned leaf storage needs a mutable placement"):
        validate_function(_replace_slot(local, "total", readonly=True))
    # An opaque layout that is not freely copyable.
    damaged = replace(local, records=tuple(replace(r, copyable=False) if r.opaque else r for r in local.records))
    with pytest.raises(MIRValidationError, match="invalid opaque leaf layout"):
        validate_function(damaged)


def test_validator_rejects_damaged_owned_leaf_values(lowered_active) -> None:
    local = lowered_active.bodies["local"]
    param, total, _, owned_temporary, _ = (s.id for s in local.slots)
    is_copy = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy)
    is_op = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIROp)
    # A copy reading a borrowed holder itself rather than the storage it borrows.
    with pytest.raises(MIRValidationError, match="owned leaf copy source mismatch"):
        validate_function(_replace_first(local, is_copy, lambda s: replace(s, value=replace(
            s.value, source=MIRPlace(param)))))
    # A move out of a borrowed parameter, which owns no storage.
    with pytest.raises(MIRValidationError, match="owned leaf move source mismatch"):
        validate_function(_replace_first(local, is_copy, lambda s: replace(s, value=MIRMove(param))))
    # An operation reading owned storage directly where it needs a borrowed holder.
    with pytest.raises(MIRValidationError, match="primitive operation needs leaf operands and result"):
        validate_function(_replace_first(local, is_op, lambda s: replace(s, value=replace(
            s.value, operands=(s.value.operands[0], owned_temporary)))))
    # A static literal whose value is not of its holder's type.
    literals = lowered_active.bodies["literal_arguments"]
    is_static = lambda s: (isinstance(s, MIRAssign) and isinstance(s.value, MIRConstant)
                           and s.value.value == "lit")
    with pytest.raises(MIRValidationError, match="static literal needs a borrowed holder of its type"):
        validate_function(_replace_first(literals, is_static, lambda s: replace(s, value=MIRConstant(b"lit"))))


def test_thir_rejects_a_global_binding_at_a_non_leaf_type(lowered_active) -> None:
    source = lowered_active.thir["global_read"]
    validate_thir(source)
    view = NominalType("StrView", (), _module_qname="tpy.StrView")

    def retype(n):
        return replace(n, result_type=view, global_binding=replace(n.global_binding, type=view))

    damaged = replace(source, body=_rewrite_thir(
        source.body, lambda n: isinstance(n, th.THIRName) and n.global_binding is not None, retype))
    with pytest.raises(THIRValidationError, match="invalid leaf global binding"):
        validate_thir(damaged)

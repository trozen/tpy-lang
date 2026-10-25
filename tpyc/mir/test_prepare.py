"""The one preparation of a body (`prepare.prepare`): its outcome, the
contracts its field-selection kill relies on, and the verdicts it gives
compiled bodies."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, STR, OptionalType, TupleType, UnionType
from . import prepare as prepare_module
from .collect import MIRBodyVerdict, MIRVerdictStatus, analyze_body, enumerate_bodies
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIRCopy, MIRFunction,
    MIRIsAlternative, MIRNotCovered, MIRPlace, MIRRead, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration, MIRStorageWrite, MIRStorageWriteMode, MIRConstruct, MIRWrap,
)
from .prepare import MIRPrepared, prepare, validate_function, validated
from .shapes import MIRDeref, MIRUnionPayload
from .testutil import (
    alias_shape, bind_parameters, borrowed_shape, hand_record, leaf_shape, optional_shape, owned_shape, tuple_shape,
    union_shape,
)
from .retention import analyze_retention
from .scope_lifetime import analyze_scope_ends
from .payload_lifetime import analyze_payload_ends
from .storage import analyze_storage
from .validate import MIRPresenceError
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError

P = hand_record("P", "prepare.P", x=INT32)
H = hand_record("H", "prepare.H", p=OptionalType(P))
S = hand_record("S", "prepare.S", s=STR)
BASE = hand_record("Base", "prepare.Base", n=INT32)
SUB = hand_record("Sub", "prepare.Sub", BASE, p=OptionalType(P))
PX = MIRField(MIRFieldId(P, "x"), INT32)
HP = MIRField(MIRFieldId(H, "p"), OptionalType(P))
SS = MIRField(MIRFieldId(S, "s"), STR)
BN = MIRField(MIRFieldId(BASE, "n"), INT32)
SP = MIRField(MIRFieldId(SUB, "p"), OptionalType(P))


def _guarded(body: MIRBodyId, slots: tuple[MIRSlot, ...], field: MIRPlace, before: tuple, inside: tuple,
             result: MIRSlotId, records: tuple, **facts: object) -> MIRFunction:
    """`if <field> is not None: <inside>; return <field payload>.x` else 0."""
    entry, yes, no = (MIRBlockId(body, i) for i in range(3))
    read = MIRAssign(MIRPlace(result), MIRRead(MIRPlace(field.root, (*field.projections, MIRUnionPayload(1), PX))))
    cond = next(s.id for s in slots if s.type == BOOL)
    return bind_parameters(MIRFunction(body, INT32, slots, (
        MIRBlock(entry, (*before, MIRAssign(MIRPlace(cond), MIRIsAlternative(field, (1,)))), MIRBranch(cond, yes, no)),
        MIRBlock(yes, (*inside, read), MIRReturn(result)),
        MIRBlock(no, (MIRAssign(MIRPlace(result), MIRConstant(0)),), MIRReturn(result)),
    ), entry, records, **facts))


def _initialized_parameter() -> MIRFunction:
    """An owned parameter initialized again under a live borrow of it."""
    body = MIRBodyId("prepare", "initialize_parameter")
    owner, other, alias, cond, result = (MIRSlotId(body, i) for i in range(5))
    slots = (MIRSlot(owner, owned_shape(H), MIRSlotKind.PARAMETER, storage_duration=MIRStorageDuration.BODY),
             MIRSlot(other, borrowed_shape(H), MIRSlotKind.PARAMETER),
             MIRSlot(alias, borrowed_shape(H), MIRSlotKind.LOCAL),
             MIRSlot(cond, leaf_shape(BOOL), MIRSlotKind.TEMPORARY),
             MIRSlot(result, leaf_shape(INT32), MIRSlotKind.LOCAL))
    return _guarded(body, slots, MIRPlace(alias, (MIRDeref(), HP)),
                    (MIRAssign(MIRPlace(alias), MIRBorrow(MIRPlace(owner))),),
                    (MIRAssign(MIRPlace(owner), MIRCopy(MIRPlace(other, (MIRDeref(),))),
                               storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),),
                    result, (MIRRecordLayout(P, (PX,), True, True), MIRRecordLayout(H, (HP,), True, True)))


def test_a_second_initialization_of_an_owned_parameter_is_rejected() -> None:
    # The caller materialized it: initializing it again would replace
    # storage the live borrow points into, which the kill skips.
    with pytest.raises(MIRValidationError, match="repeated storage initialization"):
        validate_function(_initialized_parameter())


def _base_replacement() -> MIRFunction:
    """`*base = copy *other` while a selection on `sub.p` is held."""
    body = MIRBodyId("prepare", "base_replacement")
    base, sub, other, cond, result = (MIRSlotId(body, i) for i in range(5))
    slots = (MIRSlot(base, borrowed_shape(BASE), MIRSlotKind.PARAMETER),
             MIRSlot(sub, borrowed_shape(SUB), MIRSlotKind.PARAMETER),
             MIRSlot(other, borrowed_shape(BASE), MIRSlotKind.PARAMETER),
             MIRSlot(cond, leaf_shape(BOOL), MIRSlotKind.TEMPORARY),
             MIRSlot(result, leaf_shape(INT32), MIRSlotKind.LOCAL))
    return _guarded(body, slots, MIRPlace(sub, (MIRDeref(), SP)), (),
                    (MIRAssign(MIRPlace(base, (MIRDeref(),)), MIRCopy(MIRPlace(other, (MIRDeref(),))),
                               storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, base)),),
                    result, (MIRRecordLayout(P, (PX,), True, True), MIRRecordLayout(BASE, (BN,), True, True),
                             MIRRecordLayout(SUB, (BN, SP), True, True, ancestors=(BASE,))))


def test_a_whole_object_write_through_a_base_ends_a_subclass_field_selection() -> None:
    # `base` may name the very `Sub` object `sub` names.
    with pytest.raises(MIRPresenceError, match="payload access without current presence proof"):
        validate_function(_base_replacement())


def _dependency_gap(write_same_member: bool) -> MIRFunction:
    """A body the first dependency run refuses (an unplaced union alias),
    with a write between the selection and the payload read: of another
    record's str, or of the very field."""
    body = MIRBodyId("prepare", "dependency_gap")
    owner, other, cond, result, union, alias = (MIRSlotId(body, i) for i in range(6))
    slots = (MIRSlot(owner, borrowed_shape(H), MIRSlotKind.PARAMETER),
             MIRSlot(other, borrowed_shape(S), MIRSlotKind.PARAMETER),
             MIRSlot(cond, leaf_shape(BOOL), MIRSlotKind.TEMPORARY),
             MIRSlot(result, leaf_shape(INT32), MIRSlotKind.LOCAL),
             MIRSlot(union, union_shape(UnionType((INT32, BOOL)), (leaf_shape(INT32), leaf_shape(BOOL))),
                     MIRSlotKind.PARAMETER),
             MIRSlot(alias, alias_shape(INT32, readonly=True), MIRSlotKind.LOCAL,
                     alias_source=MIRPlace(union, (MIRUnionPayload(0),))))
    write = (MIRAssign(MIRPlace(owner, (MIRDeref(), HP)), MIRWrap(0),
                       storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)) if write_same_member
             else MIRAssign(MIRPlace(other, (MIRDeref(), SS)), MIRConstant("changed"),
                            storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)))
    return _guarded(body, slots, MIRPlace(owner, (MIRDeref(), HP)), (), (write,), result,
                    (MIRRecordLayout(P, (PX,), True, True), MIRRecordLayout(H, (HP,), True, True),
                     MIRRecordLayout(S, (SS,), True, True), MIRRecordLayout(STR, (), True, True, opaque=True)),
                    exceptional_exits=not write_same_member)


@pytest.mark.parametrize("write_same_member", [False, True])
def test_a_first_dependency_refusal_is_the_bodys_verdict(write_same_member: bool) -> None:
    fn = _dependency_gap(write_same_member)
    outcome = prepare(fn)
    assert isinstance(outcome, MIRNotCovered) and outcome.reason == "missing storage duration for %4"
    # The verdict keeps the dependency reason: every analysis is its gap.
    gaps = analyze_body(fn, outcome).gaps
    assert gaps[0] == ("dependencies", outcome) and all(gap is outcome for _, gap in gaps)
    # Presence holds, but no field selection is proven -- a genuinely
    # unproven read (the field emptied before it) included.
    assert validated(fn) == outcome
    with pytest.raises(MIRPresenceError, match="field selections not proven: dependencies: missing storage"):
        validate_function(fn)


@pytest.mark.parametrize("failing", [1, 2])
def test_an_exception_in_either_dependency_run_propagates(monkeypatch, failing: int) -> None:
    fn = _base_replacement()
    calls: list[int] = []
    real = prepare_module._dependencies

    def dependencies(presence, liveness):
        calls.append(len(calls) + 1)
        if len(calls) == failing:
            raise MIRValidationError("broken dependency fact")
        return real(presence, liveness)
    monkeypatch.setattr(prepare_module, "_dependencies", dependencies)
    # A replacement of the base's storage that ends no selection, so both runs happen.
    harmless = _guarded(fn.id, fn.slots, MIRPlace(fn.slots[1].id, (MIRDeref(), SP)), (), (), fn.slots[4].id,
                        fn.records)
    with pytest.raises(MIRValidationError, match="broken dependency fact"):
        prepare(harmless)
    assert calls == list(range(1, failing + 1))


# --- compiled bodies ---------------------------------------------------------------------

SOURCE = """\
from tpy import int32, Own, StrView


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    p: P | None
    n: int32

    def __init__(self, p: P | None, n: int32) -> None:
        self.p = p
        self.n = n


class S:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


class SH:
    p: S | None

    def __init__(self, p: S | None) -> None:
        self.p = p


def own_field_write(a: H) -> int32:
    q = P(1)
    h = H(q, 1)
    if a.p is not None:
        h.p = None
        return a.p.x + h.n
    return 0


def holders(x: Own[H], y: Own[H]) -> int32:
    hx = x
    hy = y
    if hx.p is not None:
        hy.p = None
        return hx.p.x
    return 0


def fresh_alias(a: H, b: H) -> int32:
    if a.p is not None:
        b.p = None
        return a.p.x
    return 0


def pick_view(o: S | None, fallback: str) -> StrView:
    if o is not None:
        return o.s
    return fallback


def absent_view(h: SH, fallback: str) -> StrView:
    h.p = None
    o = h.p
    return pick_view(o, fallback)


def forward_absent(h: SH, fallback: str) -> StrView:
    return absent_view(h, fallback)
"""


@dataclass(frozen=True)
class _Program:
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir, activate_compiler(compiler):
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program({v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.mark.parametrize("name", ["own_field_write", "holders"])
def test_a_write_of_the_bodys_own_record_keeps_a_parameters_field_selection(program: _Program, name: str) -> None:
    verdict = program.verdicts[name]
    assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.describe()


def test_a_write_through_a_parameter_that_may_alias_ends_it(program: _Program) -> None:
    verdict = program.verdicts["fresh_alias"]
    assert verdict.status is MIRVerdictStatus.UNCOVERED
    assert verdict.reason == "payload access without current presence proof"


def test_later_passes_read_dependencies_over_the_final_presence(program: _Program) -> None:
    # `h.p = None; o = h.p` makes `o` absent only with field selections: its
    # call's effects, the summary and the caller's coverage follow from that.
    for name in ("absent_view", "forward_absent"):
        verdict = program.verdicts[name]
        assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.describe()
    lowered = program.verdicts["absent_view"].function
    assert isinstance(prepare(lowered), MIRPrepared)


# --- a missing write fact, scoped by each reader ---------------------------------------

def _missing_fact(name: str, kind: MIRSlotKind | None, inside: bool) -> MIRFunction:
    """A statement with no storage write fact -- into a scalar wrapper of
    `kind` (a temporary or a local), or (`kind` None) a whole-record
    construction through a borrowed holder -- in the arm that reads a held
    `owner.p` payload (`inside`) or before its proof."""
    body = MIRBodyId("prepare", name)
    root = MIRRegionId(body, 0)
    owner, cond, result, target, value = (MIRSlotId(body, i) for i in range(5))
    if kind is None:
        subject = MIRSlot(target, borrowed_shape(P), MIRSlotKind.PARAMETER)
        statements = (MIRAssign(MIRPlace(value), MIRConstant(0)),
                      MIRAssign(MIRPlace(target, (MIRDeref(),)), MIRConstruct((value,))))
        other = MIRSlot(value, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=root)
    else:
        duration = MIRStorageDuration.BODY if kind is MIRSlotKind.LOCAL else None
        subject = MIRSlot(target, optional_shape(OptionalType(INT32), leaf_shape(INT32)), kind, residence=root,
                          storage_duration=duration)
        statements = (MIRAssign(MIRPlace(target), MIRWrap(0)),)
        other = MIRSlot(value, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=root)
    initialize = ((MIRAssign(MIRPlace(target), MIRWrap(0), storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),)
                  if kind is MIRSlotKind.LOCAL else ())
    slots = (MIRSlot(owner, borrowed_shape(H), MIRSlotKind.PARAMETER),
             MIRSlot(cond, leaf_shape(BOOL), MIRSlotKind.TEMPORARY, residence=root),
             MIRSlot(result, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=root), subject, other)
    entry, yes, no = (MIRBlockId(body, i) for i in range(3))
    field = MIRPlace(owner, (MIRDeref(), HP))
    read = MIRAssign(MIRPlace(result), MIRRead(MIRPlace(owner, (MIRDeref(), HP, MIRUnionPayload(1), PX))))
    return bind_parameters(MIRFunction(body, INT32, slots, (
        MIRBlock(entry, (*initialize, *(() if inside else statements),
                         MIRAssign(MIRPlace(cond), MIRIsAlternative(field, (1,)))), MIRBranch(cond, yes, no), root),
        MIRBlock(yes, (*(statements if inside else ()), read), MIRReturn(result), root),
        MIRBlock(no, (MIRAssign(MIRPlace(result), MIRConstant(0)),), MIRReturn(result), root),
    ), entry, (MIRRecordLayout(P, (PX,), True, True), MIRRecordLayout(H, (HP,), True, True)),
        regions=(MIRRegion(root, None, entry),)))


def _outcomes(fn: MIRFunction) -> tuple[str, ...]:
    def outcome(read) -> str:
        try:
            result = read()
        except MIRPresenceError as error:
            return f"refused: {error}"
        return f"not covered: {result.reason}" if isinstance(result, MIRNotCovered) else "ok"
    try:
        validate_function(fn)
        ready = prepare(fn)
    except MIRPresenceError as error:
        return (f"refused: {error}",)
    return tuple(outcome(lambda read=read: read(ready)) for read in (
        analyze_storage, analyze_retention, analyze_scope_ends, analyze_payload_ends))


SELECTION = "refused: payload access without current presence proof"


@pytest.mark.parametrize(("name", "kind", "inside", "expected"), [
    # A scalar wrapper replaces no storage a borrow points into: only its
    # payload is unknown (payload ends), and a scope root's write (a local).
    ("temporary", MIRSlotKind.TEMPORARY, True,
     ("ok", "ok", "ok", "not covered: missing payload write fact")),
    ("local", MIRSlotKind.LOCAL, True,
     ("ok", "ok", "not covered: missing storage initialization/write fact", "not covered: missing payload write fact")),
    # A whole-record construction through a borrowed holder may replace what
    # any external holder points into: storage and retention refuse it, the
    # scope ends do not track it, and a field proof it may reach dies
    # (fail closed; the base place rule kept it, `P` holding no `H.p`).
    ("holder", None, False,
     ("not covered: missing storage write fact", "not covered: storage: missing storage write fact", "ok", "ok")),
    ("holder_inside", None, True, (SELECTION,)),
])
def test_a_missing_write_fact_is_scoped_by_each_reader(name: str, kind: MIRSlotKind | None, inside: bool,
                                                       expected: tuple[str, ...]) -> None:
    assert _outcomes(_missing_fact(name, kind, inside)) == expected


def test_an_owned_tuple_without_its_fact_never_validates() -> None:
    # The validator demands an owned tuple's initialization fact, so no
    # reader sees one without it (the classifier marks it an unknown replacement).
    fn = _missing_fact("tuple", MIRSlotKind.TEMPORARY, False)
    pair, value = MIRSlotId(fn.id, 3), MIRSlotId(fn.id, 4)
    slots = tuple(MIRSlot(pair, tuple_shape(TupleType((INT32, P)), (leaf_shape(INT32), owned_shape(P))),
                          MIRSlotKind.LOCAL, residence=s.residence, storage_duration=MIRStorageDuration.BODY)
                  if s.id == pair else s for s in fn.slots)
    entry = fn.blocks[0]
    construct = MIRAssign(MIRPlace(pair), MIRConstruct((value, value)))
    fn = bind_parameters(replace(fn, slots=slots, blocks=(replace(entry, statements=(
        MIRAssign(MIRPlace(value), MIRConstant(0)), construct, *entry.statements[1:])), *fn.blocks[1:])))
    with pytest.raises(MIRValidationError, match="owned tuple needs initialization fact"):
        validate_function(fn)

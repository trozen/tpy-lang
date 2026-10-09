"""Copied holders retain storage independently of source reseats and last use."""

from dataclasses import replace
import re

import pytest

from ..thir.nodes import Form
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType, OptionalType, ReadonlyType, TupleType, UnionType, VoidType
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, MIRReferent, analyze_dependencies, dump_dependencies
from .liveness import MIRPoint, analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow,
    MIRBranch, MIRConstant, MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRIsAlternative, MIRIsPresent, MIRMove, MIRNotCovered,
    MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRPlace, MIRRead, MIRRecordLayout, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex,
    MIRTupleLayout, MIRUnionConstruct, MIRUnionCopy, MIRUnionExtract, MIRUnionLayout,
    MIRUnionPayload, MIRValueKind,
)
from .validate import MIRValidationError, successors, validate_function

B = MIRBodyId("dependencies", "test")
P, Q, FLAG, X, Y, Z, OUT, STORE, STORE2 = (MIRSlotId(B, i) for i in range(9))
A, C, D, E = (MIRBlockId(B, i) for i in range(4))
CELL = NominalType("Cell", _module_qname="dependencies.Cell")
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
LAYOUT = MIRRecordLayout(CELL, (FIELD,), True, True)


def reference(sid: MIRSlotId, *, param: bool = False, readonly: bool = False) -> MIRSlot:
    return MIRSlot(sid, CELL, MIRSlotKind.PARAMETER if param else MIRSlotKind.LOCAL,
                   form=Form.BORROW, value_kind=MIRValueKind.BORROWED, readonly=readonly)


def analyze(fn: MIRFunction) -> MIRDependencies:
    result = analyze_dependencies(fn, analyze_liveness(fn))
    assert isinstance(result, MIRDependencies), result
    return result


def external(sid: MIRSlotId, *path) -> frozenset[MIRReferent]:
    return frozenset({MIRReferent(MIRPlace(sid, path), external=True)})


def test_alias_snapshot_survives_source_reseat_and_last_use() -> None:
    slots = (reference(P, param=True), reference(Q, param=True), reference(X), reference(Y),
             MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    fn = MIRFunction(B, INT32, slots, (MIRBlock(A, (
        MIRAssign(MIRPlace(X), MIRAlias(P)),
        MIRAssign(MIRPlace(Y), MIRAlias(X)),
        MIRAssign(MIRPlace(X), MIRAlias(Q)),
        MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(Y, (MIRDeref(), FIELD)))),
    ), MIRReturn(OUT)),), A)
    result = analyze(fn)
    point = MIRPoint(A, 3)
    assert result.referents[point][MIRPlace(X)] == external(Q)
    assert result.active[point] == {MIRPlace(Y): external(P)}
    assert result.holders[point] == {next(iter(external(P))): {MIRPlace(Y)}}
    assert result.active[MIRPoint(A, 4)] == {}
    with pytest.raises(TypeError):
        result.active[point][MIRPlace(Y)] = frozenset()
    assert dump_dependencies(result) == dump_dependencies(analyze(fn))


@pytest.mark.parametrize("shape", ["tuple", "optional", "union"])
def test_aggregate_copy_retains_original_payload(shape: str) -> None:
    member = MIRTupleElement(CELL, MIRValueKind.BORROWED)
    if shape == "tuple":
        wrapper = MIRSlot(X, TupleType((CELL, INT32)), MIRSlotKind.LOCAL,
                          value_kind=MIRValueKind.TUPLE,
                          tuple_layout=MIRTupleLayout((member, MIRTupleElement(INT32))))
        scalar = MIRSlot(Z, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)
        initial, copy, reseat = MIRTupleConstruct((P, Z)), MIRTupleCopy(X), MIRTupleConstruct((Q, Z))
        path = (MIRTupleIndex(0),)
    elif shape == "optional":
        wrapper = MIRSlot(X, OptionalType(CELL), MIRSlotKind.LOCAL,
                          value_kind=MIRValueKind.OPTIONAL,
                          optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED))
        scalar = MIRSlot(Z, BOOL, MIRSlotKind.TEMPORARY)
        initial, copy, reseat = MIROptionalConstruct(MIRPlace(P)), MIROptionalCopy(MIRPlace(X)), MIROptionalConstruct()
        path = (MIROptionalPayload(),)
    else:
        other = NominalType("Other", _module_qname="dependencies.Other")
        wrapper = MIRSlot(X, UnionType((CELL, other)), MIRSlotKind.LOCAL,
                          value_kind=MIRValueKind.UNION, union_layout=MIRUnionLayout((
                              member, MIRTupleElement(other, MIRValueKind.BORROWED))))
        scalar = MIRSlot(Z, BOOL, MIRSlotKind.TEMPORARY)
        initial, copy, reseat = MIRUnionConstruct(0, MIRPlace(P)), MIRUnionCopy(MIRPlace(X)), MIRUnionConstruct(0, MIRPlace(Q))
        path = (MIRUnionPayload(0),)
    statements = (MIRAssign(MIRPlace(X), initial), MIRAssign(MIRPlace(Y), copy), MIRAssign(MIRPlace(X), reseat))
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(Y, (*path, MIRDeref(), FIELD))))
    if shape == "tuple":
        blocks = (MIRBlock(A, (*statements, read), MIRReturn(OUT)),)
    else:
        guard = MIRIsPresent(MIRPlace(Y)) if shape == "optional" else MIRIsAlternative(MIRPlace(Y), (0,))
        blocks = (MIRBlock(A, (*statements, MIRAssign(MIRPlace(Z), guard)), MIRBranch(Z, C, D)),
                  MIRBlock(C, (read,), MIRReturn(OUT)),
                  MIRBlock(D, (MIRAssign(MIRPlace(OUT), MIRConstant(0)),), MIRReturn(OUT)))
    fn = MIRFunction(B, INT32, (reference(P, param=True), reference(Q, param=True), wrapper,
                     replace(wrapper, id=Y), scalar, MIRSlot(OUT, INT32, MIRSlotKind.LOCAL)), blocks, A)
    result = analyze(fn)
    point = MIRPoint(A, 3)
    assert result.active[point] == {MIRPlace(Y, path): external(P)}
    assert result.referents[point].get(MIRPlace(X, path), frozenset()) == (
        frozenset() if shape == "optional" else external(Q))


def test_local_storage_copy_move_and_replacement_keep_distinct_origins() -> None:
    storage = MIRSlot(STORE, CELL, MIRSlotKind.TEMPORARY, form=Form.STORAGE,
                      value_kind=MIRValueKind.OWNED, storage_duration=MIRStorageDuration.BODY)
    third = MIRSlotId(B, 9)
    slots = (MIRSlot(P, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), reference(X), reference(Y), reference(Z),
             storage, replace(storage, id=STORE2), replace(storage, id=third))
    fn = MIRFunction(B, VoidType(), slots, (MIRBlock(A, (
        MIRAssign(MIRPlace(STORE), MIRConstruct((P,))),
        MIRAssign(MIRPlace(X), MIRBorrow(MIRPlace(STORE))),
        MIRAssign(MIRPlace(Y), MIRAlias(X)),
        MIRAssign(MIRPlace(STORE2), MIRCopy(MIRPlace(X, (MIRDeref(),)))),
        MIRAssign(MIRPlace(third), MIRMove(STORE2)),
        MIRAssign(MIRPlace(Z), MIRBorrow(MIRPlace(third))),
        MIRAssign(MIRPlace(X), MIRBorrow(MIRPlace(STORE2))),
        MIRAssign(MIRPlace(Y, (MIRDeref(),)), MIRConstruct((P,))),
        MIRAssign(MIRPlace(Y, (MIRDeref(), FIELD)), MIRRead(MIRPlace(P))),
        MIRAssign(MIRPlace(Z, (MIRDeref(), FIELD)), MIRRead(MIRPlace(P))),
    ), MIRReturn()),), A, records=(LAYOUT,))
    result = analyze(fn)
    state = result.referents[MIRPoint(A, 8)]
    for holder, root in ((X, STORE2), (Y, STORE), (Z, third)):
        assert state[MIRPlace(holder)] == {MIRReferent(MIRPlace(root))}
    assert MIRPlace(Y) in result.active[MIRPoint(A, 8)]
    assert MIRPlace(Y) not in result.active[MIRPoint(A, 9)]


def test_tuple_leaves_and_parameter_payloads_keep_separate_origins() -> None:
    layout = MIRTupleLayout((MIRTupleElement(CELL, MIRValueKind.BORROWED),) * 2)
    pair = MIRSlot(X, TupleType((CELL, CELL)), MIRSlotKind.LOCAL,
                   value_kind=MIRValueKind.TUPLE, tuple_layout=layout)
    slots = (reference(P, param=True), reference(Q, param=True), pair,
             replace(pair, id=Y), replace(pair, id=Z, kind=MIRSlotKind.PARAMETER))
    fn = MIRFunction(B, VoidType(), slots, (MIRBlock(A, (
        MIRAssign(MIRPlace(X), MIRTupleConstruct((P, Q))),
        MIRAssign(MIRPlace(Y), MIRTupleCopy(X)),
        MIRAssign(MIRPlace(X), MIRTupleCopy(Z)),
        MIRAssign(MIRPlace(Y), MIRTupleCopy(Y)),
    ), MIRReturn()),), A)
    state = analyze(fn).referents[MIRPoint(A, 4)]
    for index, param in enumerate((P, Q)):
        path = (MIRTupleIndex(index),)
        assert state[MIRPlace(Y, path)] == external(param)
        assert state[MIRPlace(X, path)] == external(Z, *path)


def test_record_union_extraction_survives_switch_to_other_alternative() -> None:
    other = NominalType("Other", _module_qname="dependencies.Other")
    layout = MIRUnionLayout(tuple(MIRTupleElement(t, MIRValueKind.BORROWED) for t in (CELL, other)))
    wrapper = MIRSlot(X, UnionType((CELL, other)), MIRSlotKind.LOCAL,
                      value_kind=MIRValueKind.UNION, union_layout=layout)
    first = MIRPlace(X, (MIRUnionPayload(0),))
    slots = (reference(P, param=True), replace(reference(Q, param=True), type=other), wrapper,
             reference(Y), MIRSlot(FLAG, BOOL, MIRSlotKind.TEMPORARY), MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    fn = MIRFunction(B, INT32, slots, (
        MIRBlock(A, (MIRAssign(MIRPlace(X), MIRUnionConstruct(0, MIRPlace(P))),
                     MIRAssign(MIRPlace(FLAG), MIRIsAlternative(MIRPlace(X), (0,)))), MIRBranch(FLAG, C, D)),
        MIRBlock(C, (MIRAssign(MIRPlace(Y), MIRUnionExtract(first)),
                     MIRAssign(MIRPlace(X), MIRUnionConstruct(1, MIRPlace(Q))),
                     MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(Y, (MIRDeref(), FIELD))))), MIRReturn(OUT)),
        MIRBlock(D, (MIRAssign(MIRPlace(OUT), MIRConstant(0)),), MIRReturn(OUT)),
    ), A)
    result = analyze(fn)
    state = result.referents[MIRPoint(C, 2)]
    assert first not in state
    assert state[MIRPlace(X, (MIRUnionPayload(1),))] == external(Q)
    assert result.active[MIRPoint(C, 2)] == {MIRPlace(Y): external(P)}


def test_inline_paths_append_to_original_origin_and_preserve_readonly() -> None:
    parent = NominalType("Parent", _module_qname="dependencies.Parent")
    child = MIRField(MIRFieldId(parent, "child"), CELL)
    slots = (replace(reference(P, param=True, readonly=True), type=parent),
             reference(X, readonly=True), reference(Y, readonly=True), MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    fn = MIRFunction(B, INT32, slots, (MIRBlock(A, (
        MIRAssign(MIRPlace(X), MIRBorrow(MIRPlace(P, (MIRDeref(), child)))),
        MIRAssign(MIRPlace(Y), MIRAlias(X)),
        MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(Y, (MIRDeref(), FIELD)))),
    ), MIRReturn(OUT)),), A)
    result = analyze(fn)
    assert result.active[MIRPoint(A, 2)] == {MIRPlace(Y): external(P, child)}
    assert result.function.slots[2].readonly


@pytest.mark.parametrize("readonly,mutual", [(False, False), (True, False), (True, True)])
def test_recursive_field_graph_is_rejected_before_unbounded_path_growth(readonly: bool, mutual: bool) -> None:
    other = NominalType("Other", _module_qname="dependencies.Other") if mutual else CELL
    target_type = ReadonlyType(other) if readonly else other
    child = MIRField(MIRFieldId(CELL, "child"), target_type)
    projections = (MIRDeref(), child)
    if mutual:
        projections += (MIRField(MIRFieldId(other, "parent"), ReadonlyType(CELL)),)
    fn = MIRFunction(B, VoidType(), (reference(P, param=True, readonly=readonly), reference(X, readonly=readonly)), (
        MIRBlock(A, (MIRAssign(MIRPlace(X), MIRAlias(P)),), MIRGoto(C)),
        MIRBlock(C, (MIRAssign(MIRPlace(X), MIRBorrow(MIRPlace(X, projections))),), MIRGoto(C)),
    ), A)
    result = analyze_dependencies(fn, analyze_liveness(fn))
    assert isinstance(result, MIRNotCovered) and result.reason == "recursive inline field paths"


def test_missing_and_malformed_backing_facts_fail_explicitly() -> None:
    storage = MIRSlot(STORE, CELL, MIRSlotKind.TEMPORARY, form=Form.STORAGE,
                      value_kind=MIRValueKind.OWNED)
    fn = MIRFunction(B, VoidType(), (storage, MIRSlot(P, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)), (
        MIRBlock(A, (MIRAssign(MIRPlace(STORE), MIRConstruct((P,))),), MIRReturn()),), A, records=(LAYOUT,))
    result = analyze_dependencies(fn, analyze_liveness(fn))
    assert isinstance(result, MIRNotCovered) and "missing storage duration" in result.reason
    for duration in ("body", MIRStorageDuration.CALLER):
        with pytest.raises(MIRValidationError, match="storage duration"):
            validate_function(replace(fn, slots=(replace(storage, storage_duration=duration), fn.slots[1])))


@pytest.mark.parametrize("local", [False, True])
def test_scalar_union_alias_depends_on_wrapper_but_scalar_copy_does_not(local: bool) -> None:
    typ = UnionType((INT32, BOOL))
    wrapper = MIRSlot(P, typ, MIRSlotKind.PARAMETER, value_kind=MIRValueKind.UNION,
                      union_layout=MIRUnionLayout((MIRTupleElement(INT32), MIRTupleElement(BOOL))),
                      storage_duration=MIRStorageDuration.CALLER)
    source = Q if local else P
    payload = MIRPlace(source, (MIRUnionPayload(0),))
    slots = (wrapper, MIRSlot(FLAG, BOOL, MIRSlotKind.TEMPORARY),
             MIRSlot(X, INT32, MIRSlotKind.LOCAL, form=Form.BORROW, readonly=True,
                     value_kind=MIRValueKind.PAYLOAD_ALIAS, alias_source=payload),
             MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    prefix = ()
    if local:
        slots += (replace(wrapper, id=Q, kind=MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),)
        prefix = (MIRAssign(MIRPlace(Q), MIRUnionCopy(MIRPlace(P))),)
    fn = MIRFunction(B, INT32, slots, (
        MIRBlock(A, (*prefix, MIRAssign(MIRPlace(FLAG), MIRIsAlternative(MIRPlace(source), (0,)))), MIRBranch(FLAG, C, D)),
        MIRBlock(C, (MIRAssign(MIRPlace(X), MIRUnionExtract(payload)),
                     MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(X)))), MIRReturn(OUT)),
        MIRBlock(D, (MIRAssign(MIRPlace(OUT), MIRConstant(0)),), MIRReturn(OUT)),
    ), A)
    result = analyze(fn)
    assert result.active[MIRPoint(C, 1)] == {MIRPlace(X): {MIRReferent(payload)}}
    assert result.active[MIRPoint(C, 2)] == {}
    without_duration = replace(fn, slots=tuple(replace(s, storage_duration=None) if s.id == source else s for s in slots))
    rejected = analyze_dependencies(without_duration, analyze_liveness(without_duration))
    assert isinstance(rejected, MIRNotCovered) and "missing storage duration" in rejected.reason


def loop_function() -> MIRFunction:
    slots = (reference(P, param=True), reference(Q, param=True), reference(X), reference(Y),
             MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    return MIRFunction(B, INT32, slots, (
        MIRBlock(A, (MIRAssign(MIRPlace(X), MIRAlias(P)), MIRAssign(MIRPlace(Y), MIRAlias(Q))), MIRGoto(C)),
        MIRBlock(C, (), MIRBranch(FLAG, D, E)),
        MIRBlock(D, (MIRAssign(MIRPlace(X), MIRAlias(Y)), MIRAssign(MIRPlace(Y), MIRAlias(P))), MIRGoto(C)),
        MIRBlock(E, (MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(X, (MIRDeref(), FIELD)))),), MIRReturn(OUT)),
    ), A)


@pytest.mark.parametrize("same_object", [False, True])
def test_fixed_point_contains_concrete_paths_with_aliased_parameters(same_object: bool) -> None:
    fn = loop_function()
    result = analyze(fn)
    liveness = analyze_liveness(fn)
    assert result.active[MIRPoint(E, 0)] == {MIRPlace(X): external(P) | external(Q)}
    assert dict(result.referents) == dict(analyze(replace(fn, blocks=tuple(reversed(fn.blocks)))).referents)
    # Enumerate paths with concrete object identities, independent of the worklist.
    objects = {P: 100, Q: 100 if same_object else 200}
    blocks = {b.id: b for b in fn.blocks}
    pending = [(fn.entry, objects.copy(), 0)]
    checked = 0
    while pending:
        bid, state, depth = pending.pop()
        block = blocks[bid]
        for index in range(len(block.statements) + 1):
            point = MIRPoint(bid, index)
            for holder in liveness.points[point] & {P, Q, X, Y}:
                refs = result.active[point][MIRPlace(holder)]
                assert state[holder] in {objects[r.place.root] for r in refs}
                checked += 1
            if index < len(block.statements):
                stmt = block.statements[index]
                if isinstance(stmt.value, MIRAlias):
                    state[stmt.target.root] = state[stmt.value.source]
        if depth < 8:
            pending.extend((dest, state.copy(), depth + 1) for dest in successors(block.terminator))
    assert checked > 15


def test_known_empty_unreachable_invalid_and_uncovered_are_distinct() -> None:
    fn = MIRFunction(B, VoidType(), (), (MIRBlock(A, (), MIRReturn()), MIRBlock(C, (), MIRReturn())), A)
    result = analyze(fn)
    assert result.active[MIRPoint(A, 0)] == {}
    assert MIRPoint(C, 0) not in result.active
    with pytest.raises(MIRValidationError, match="different MIR function"):
        analyze_dependencies(replace(fn), analyze_liveness(fn))
    recursive = MIRField(MIRFieldId(CELL, "child"), CELL)
    recursive_fn = MIRFunction(B, VoidType(), (reference(P, param=True), reference(X)), (MIRBlock(A, (
        MIRAssign(MIRPlace(X), MIRBorrow(MIRPlace(P, (MIRDeref(), recursive)))),
    ), MIRReturn()),), A)
    uncovered = analyze_dependencies(recursive_fn, analyze_liveness(recursive_fn))
    assert isinstance(uncovered, MIRNotCovered) and "recursive inline" in uncovered.reason
    assert "not covered" in dump_dependencies(uncovered)
    with pytest.raises(MIRValidationError, match="storage duration"):
        validate_function(replace(recursive_fn, slots=(replace(reference(P, param=True), storage_duration=MIRStorageDuration.BODY), reference(X))))


SOURCE = """\
from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def observe(self, source: Cell) -> int32:
        saved = source
        self.value = 7
        return saved.value
class Other:
    value: int32
def record_union(value: Cell | Other) -> int32:
    if isinstance(value, Cell):
        return value.value
    return value.value
def retained(flag: bool) -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    if flag:
        current = Cell(2)
    current = None
    if saved is not None:
        return saved.value
    return 0
def scalar(value: int32 | bool) -> int32:
    saved = value
    if isinstance(saved, int32):
        return saved
    return 0
"""


def test_real_producer_stamps_storage_duration_and_constructor_entry() -> None:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    (_header, source), ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    bodies = {node.name: lower_function(thir, MIRBodyId("producer", node.name), definitions=definitions)
              for node, thir in ctx.thir_functions.items()}
    for node, ctor in ctx.thir_constructors.items():
        bodies[node.name] = lower_constructor(ctor, MIRBodyId("producer", "ctor"), definitions=definitions)
    for fn in bodies.values():
        assert isinstance(fn, MIRFunction), fn
        result = analyze(fn)
        if fn.receiver_init is not None:
            assert result.entry_active == {MIRPlace(fn.receiver_init.receiver): external(fn.receiver_init.receiver)}
    retained = bodies["retained"]
    roots = [s for s in retained.slots if s.value_kind is MIRValueKind.OWNED]
    assert len(roots) == 2 and all(s.storage_duration is MIRStorageDuration.BODY for s in roots)
    # Branch OWN backing must be hoisted before the conditional, not scoped to it.
    body = source[source.index(" retained("):].split("\n}\n", 1)[0]
    before_branch = body[:body.index("if (")]
    hoisted = re.search(r"std::optional<Cell> (__slot_\d+);", before_branch)
    assert hoisted
    assert re.search(r"Cell __slot_\d+ = Cell\(1\);", before_branch)
    assert f"current = &*({hoisted[1]} = Cell(2));" in body[body.index("if ("):]
    result = analyze(retained)
    saved = next(s for s in retained.slots if s.name == "saved")
    carried = [refs for live in result.active.values() for leaf, refs in live.items() if leaf.root == saved.id]
    assert carried and all(refs == {MIRReferent(MIRPlace(roots[0].id))} for refs in carried)
    scalar = bodies["scalar"]
    wrappers = [s for s in scalar.slots if s.value_kind is MIRValueKind.UNION]
    assert {s.storage_duration for s in wrappers} == {MIRStorageDuration.BODY, MIRStorageDuration.CALLER}
    parameter = next(s for s in bodies["record_union"].slots if s.kind is MIRSlotKind.PARAMETER)
    assert parameter.storage_duration is None
    assert re.search(r"record_union\(::tpy::Union<const Cell\*, const Other\*> value\)", source)
    assert re.search(r"scalar\(const ::tpy::Union<bool, int32_t>& value\)", source)
    bad = replace(bodies["record_union"], slots=tuple(
        replace(s, storage_duration=MIRStorageDuration.CALLER) if s.id == parameter.id else s
        for s in bodies["record_union"].slots))
    with pytest.raises(MIRValidationError, match="caller duration requires borrowed scalar"):
        validate_function(bad)
    assert all(s.storage_duration is None for fn in bodies.values() for s in fn.slots
               if s.value_kind is MIRValueKind.BORROWED)

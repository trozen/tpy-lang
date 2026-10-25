"""Independent CFGs pin last-use, alias-storage and fixed-point boundaries."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, OptionalType, TupleType, UnionType, VoidType
from .liveness import MIRPoint, _liveness, dump_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBranch, MIRConstant, MIRFunction, MIRGoto,
    MIRIsAlternative, MIRMemberInit, MIRMemberInits, MIRPlace, MIRRead, MIRReceiverInit, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRUnionExtract, MIRRvalue, MIRAlias, MIRCopy, MIRMove, MIRConstruct, MIRGlobalId,
    MIRStorageDuration, MIRWrap,
)
from .shapes import MIRDeref, MIRTupleIndex, MIRUnionPayload
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .validate import operands
from .test_iteration import fixture as iteration_fixture
from ..mir_workspace import analyze_call_workspace
from .collect import call_definitions
from .testutil import (
    compiled_definitions, hand_record, bind_parameters, alias_shape, borrowed_shape, leaf_shape, optional_shape,
    owned_shape, tuple_shape, union_shape, prepared,
)

B = MIRBodyId("liveness", "test")
P, X, Y, Z = (MIRSlotId(B, i) for i in range(4))
A, C, D, E = (MIRBlockId(B, i) for i in range(4))
CELL = hand_record("Cell", "liveness.Cell", value=INT32)
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)


def scalar_slots() -> tuple[MIRSlot, ...]:
    return (MIRSlot(P, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
            MIRSlot(X, leaf_shape(INT32), MIRSlotKind.PARAMETER),
            MIRSlot(Y, leaf_shape(INT32), MIRSlotKind.PARAMETER),
            MIRSlot(Z, leaf_shape(INT32), MIRSlotKind.LOCAL))


def function(*blocks: MIRBlock) -> MIRFunction:
    return bind_parameters(MIRFunction(B, INT32, scalar_slots(), blocks, A))


def test_overwrite_self_assignment_and_point_boundaries() -> None:
    fn = function(MIRBlock(A, (
        MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(X))),
        MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(Z))),
        MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(Y))),
    ), MIRReturn(Z)))
    result = prepared(fn).liveness
    assert result.live_in[A] == {X, Y}
    assert result.points[MIRPoint(A, 1)] == {Z, Y}
    assert result.points[MIRPoint(A, 2)] == {Y}
    assert result.points[MIRPoint(A, 3)] == {Z}
    assert result.live_out[A] == set()
    with pytest.raises(TypeError):
        result.live_in[A] = frozenset()
    assert dump_liveness(result) == dump_liveness(prepared(fn).liveness)


def test_diamond_joins_preserve_both_possible_uses() -> None:
    fn = function(
        MIRBlock(A, (), MIRBranch(P, C, D)),
        MIRBlock(C, (MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(X))),), MIRGoto(E)),
        MIRBlock(D, (MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(Y))),), MIRGoto(E)),
        MIRBlock(E, (), MIRReturn(Z)),
    )
    result = prepared(fn).liveness
    assert result.live_in[A] == {P, X, Y}
    assert result.live_out[A] == {X, Y}
    assert result.live_in[C] == {X}
    assert result.live_in[D] == {Y}
    assert result.live_in[E] == {Z}


def test_loop_zero_trip_backedge_and_break_need_fixed_point() -> None:
    # X is used at exit even on zero trips; Y is used only on a later body leg.
    fn = function(
        MIRBlock(A, (), MIRBranch(P, C, E)),
        MIRBlock(E, (), MIRReturn(X)),
        MIRBlock(D, (MIRAssign(MIRPlace(X), MIRRead(MIRPlace(Y))),), MIRGoto(A)),
        MIRBlock(C, (), MIRBranch(P, D, E)),
    )
    result = prepared(fn).liveness
    assert result.live_in[A] == {P, X, Y}
    assert result.live_in[D] == {P, Y}
    assert result.live_out[D] == {P, X, Y}
    assert result.live_in[E] == {X}
    reversed_blocks = replace(fn, blocks=tuple(reversed(fn.blocks)))
    assert dict(result.points) == dict(prepared(reversed_blocks).liveness.points)


def test_nonterminating_loop_and_unreachable_reads() -> None:
    fn = function(
        MIRBlock(A, (), MIRGoto(C)),
        MIRBlock(C, (MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(X))),), MIRGoto(A)),
        MIRBlock(D, (), MIRReturn(Y)),
    )
    result = prepared(fn).liveness
    assert result.live_in[A] == result.live_in[C] == {X}
    assert D not in result.live_in
    assert MIRPoint(D, 0) not in result.points


def test_projected_write_uses_receiver_even_without_a_later_read() -> None:
    slots = (MIRSlot(P, borrowed_shape(CELL), MIRSlotKind.PARAMETER),
             MIRSlot(X, leaf_shape(INT32), MIRSlotKind.PARAMETER))
    fn = bind_parameters(MIRFunction(B, VoidType(), slots, (MIRBlock(A, (
        MIRAssign(MIRPlace(P, (MIRDeref(), FIELD)), MIRRead(MIRPlace(X))),
    ), MIRReturn()),), A))
    result = prepared(fn).liveness
    assert result.live_in[A] == {P, X}
    assert result.points[MIRPoint(A, 1)] == set()


def test_global_write_uses_rhs_without_claiming_to_eliminate_the_store() -> None:
    slots = (MIRSlot(P, leaf_shape(INT32), MIRSlotKind.GLOBAL, global_id=MIRGlobalId("liveness", "count")),
             MIRSlot(X, leaf_shape(INT32), MIRSlotKind.PARAMETER))
    store = MIRAssign(MIRPlace(P), MIRRead(MIRPlace(X)))
    fn = bind_parameters(MIRFunction(B, VoidType(), slots, (MIRBlock(A, (store,), MIRReturn()),), A))
    result = prepared(fn).liveness
    assert result.live_in[A] == {X}
    assert result.points[MIRPoint(A, 1)] == set()
    assert result.function.blocks[0].statements == (store,)


def test_scalar_payload_alias_keeps_union_live_until_value_read() -> None:
    typ = UnionType((INT32, BOOL))
    payload = MIRPlace(P, (MIRUnionPayload(0),))
    slots = (
        MIRSlot(P, union_shape(typ, (leaf_shape(INT32), leaf_shape(BOOL))), MIRSlotKind.PARAMETER),
        MIRSlot(X, leaf_shape(BOOL), MIRSlotKind.TEMPORARY),
        MIRSlot(Y, alias_shape(INT32, readonly=True), MIRSlotKind.LOCAL, alias_source=payload),
        MIRSlot(Z, leaf_shape(INT32), MIRSlotKind.TEMPORARY),
    )
    fn = bind_parameters(MIRFunction(B, INT32, slots, (
        MIRBlock(A, (MIRAssign(MIRPlace(X), MIRIsAlternative(MIRPlace(P), (0,))),), MIRBranch(X, C, D)),
        MIRBlock(C, (MIRAssign(MIRPlace(Y), MIRUnionExtract(payload)),
                     MIRAssign(MIRPlace(Z), MIRRead(MIRPlace(Y)))), MIRReturn(Z)),
        MIRBlock(D, (MIRAssign(MIRPlace(Z), MIRConstant(0)),), MIRReturn(Z)),
    ), A))
    result = _liveness(fn)
    assert result.points[MIRPoint(C, 1)] == {P, Y}
    assert result.points[MIRPoint(C, 2)] == {Z}
    assert result.live_in[D] == set()


def test_receiver_initialization_is_an_entry_use_not_a_loop_use() -> None:
    slots = (MIRSlot(P, borrowed_shape(CELL), MIRSlotKind.PARAMETER),
             MIRSlot(X, leaf_shape(INT32), MIRSlotKind.PARAMETER))
    fn = bind_parameters(MIRFunction(B, VoidType(), slots, (MIRBlock(A, (), MIRReturn()),), A,
                     records=(MIRRecordLayout(CELL, (FIELD,), True, True),),
                     receiver_init=MIRReceiverInit(P, MIRMemberInits((MIRMemberInit(MIRRead(MIRPlace(X))),))),
                     kind=MIRBodyKind.CONSTRUCTOR))
    result = prepared(fn).liveness
    assert result.live_in[A] == set()
    assert result.entry_live == {P, X}


def test_invalid_mir_is_not_an_empty_success() -> None:
    fn = function(MIRBlock(A, (), MIRReturn(Z)))
    with pytest.raises(MIRValidationError, match="return before definite assignment"):
        prepared(fn).liveness


@pytest.mark.parametrize("operation", [
    "construct", "copy", "move", "tuple", "tuple_copy", "tuple_element",
    "optional", "optional_copy", "present", "union", "union_copy",
])
def test_rvalue_operands_survive_dead_destinations(operation: str) -> None:
    ref = MIRSlot(P, borrowed_shape(CELL), MIRSlotKind.PARAMETER)
    scalar = MIRSlot(X, leaf_shape(INT32), MIRSlotKind.PARAMETER)
    other = MIRSlot(Y, leaf_shape(INT32), MIRSlotKind.PARAMETER)
    target = MIRSlot(Z, owned_shape(CELL), MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY)
    records = (MIRRecordLayout(CELL, (FIELD,), True, True),)
    expected = {X}
    value = MIRConstruct((X,))
    prefix = ()
    if operation == "copy":
        value = MIRCopy(MIRPlace(P, (MIRDeref(),)))
        expected = {P}
    elif operation == "move":
        ref = replace(target, id=P)
        prefix = (MIRAssign(MIRPlace(P), MIRConstruct((X,))),)
        value, expected = MIRMove(P), {P}
    elif operation in ("tuple", "tuple_copy", "tuple_element"):
        typ = TupleType((CELL, INT32))
        layout = (borrowed_shape(CELL), leaf_shape(INT32))
        other = MIRSlot(Y, tuple_shape(typ, layout), MIRSlotKind.PARAMETER)
        target = replace(other, id=Z, kind=MIRSlotKind.TEMPORARY)
        if operation == "tuple":
            value, expected = MIRConstruct((P, X)), {P, X}
        elif operation == "tuple_copy":
            value, expected = MIRCopy(MIRPlace(Y)), {Y}
        else:
            target = MIRSlot(Z, leaf_shape(INT32), MIRSlotKind.TEMPORARY)
            value, expected = MIRRead(MIRPlace(Y, (MIRTupleIndex(1),))), {Y}
    elif operation in ("optional", "optional_copy", "present"):
        other = MIRSlot(Y, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.PARAMETER)
        target = replace(other, id=Z, kind=MIRSlotKind.TEMPORARY)
        if operation == "optional":
            value, expected = MIRWrap(1, MIRPlace(P)), {P}
        elif operation == "optional_copy":
            value, expected = MIRCopy(MIRPlace(Y)), {Y}
        else:
            target = MIRSlot(Z, leaf_shape(BOOL), MIRSlotKind.TEMPORARY)
            value, expected = MIRIsAlternative(MIRPlace(Y), (1,)), {Y}
    elif operation in ("union", "union_copy"):
        sibling = hand_record("Other", "liveness.Other")
        other = MIRSlot(Y, union_shape(UnionType((CELL, sibling)), tuple(
                            borrowed_shape(t) for t in (CELL, sibling))), MIRSlotKind.PARAMETER)
        target = replace(other, id=Z, kind=MIRSlotKind.TEMPORARY)
        value, expected = ((MIRWrap(0, MIRPlace(P)), {P}) if operation == "union"
                           else (MIRCopy(MIRPlace(Y)), {Y}))
    fn = bind_parameters(MIRFunction(B, VoidType(), (ref, scalar, other, target),
                     (MIRBlock(A, prefix + (MIRAssign(MIRPlace(Z), value),), MIRReturn()),), A, records=records))
    result = prepared(fn).liveness
    assert result.points[MIRPoint(A, len(prefix))] == expected
    assert result.points[MIRPoint(A, len(prefix) + 1)] == set()


SOURCE = """\
from tpy import int32, copy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def aliases(a: Cell) -> int32:
    saved = a
    return saved.value
def aggregates(a: Cell, b: Other, opt: Cell | None, variant: Cell | Other, flag: bool) -> int32:
    pair = (a, flag)
    saved = pair
    current = opt
    optional_copy = current
    value = variant
    union_copy = value
    if flag:
        current = None
        value = variant
    if optional_copy is not None:
        optional_copy.value = 7
    if isinstance(union_copy, Cell):
        union_copy.value = 9
        return union_copy.value
    return saved[0].value
def scalar_union(value: int32 | bool) -> int32:
    current = value
    current = 7
    if isinstance(current, int32):
        return current
    return 0
def owners() -> int32:
    original = Cell(1)
    held = original
    duplicate = copy(original)
    transfer = Cell(2)
    moved = transfer
    held.value = 7
    return duplicate.value if moved.value > 1 else held.value
def booleans(flag: bool, value: int32) -> bool:
    current = not flag
    return current or value > 0
def arithmetic(value: int32) -> int32:
    return value * 2 + 1
def range_target(stop: int32) -> int32:
    result = 0
    for index in range(stop):
        result = index
    return result
def leaf(value: int32) -> int32:
    return value
def call(value: int32) -> int32:
    return leaf(value)
"""


def test_emitted_operations_and_reference_copy_last_use() -> None:
    compiler, modules = _compile(SOURCE)
    with activate_compiler(compiler):
        entry = _entry(modules)
        ctx = compiler.collect_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        operations: set[type] = set()
        for node, thir in ctx.thir_functions.items():
            fn = lower_function(thir, MIRBodyId("liveness", node.name), definitions=definitions,
                                summaries=workspace.summaries)
            assert isinstance(fn, MIRFunction), fn
            result = prepared(fn).liveness
            for block in fn.blocks:
                operations.update(type(s.value) for s in block.statements)
            if node.name == "aliases":
                block = fn.blocks[0]
                index = next(i for i, stmt in enumerate(block.statements) if isinstance(stmt.value, MIRAlias))
                stmt = block.statements[index]
                assert stmt.value.source in result.points[MIRPoint(block.id, index)]
                assert stmt.value.source not in result.points[MIRPoint(block.id, index + 1)]
                assert stmt.target.root in result.points[MIRPoint(block.id, index + 1)]
        iteration = iteration_fixture()
        live = prepared(iteration).liveness
        for block in iteration.blocks:
            for index, stmt in enumerate(block.statements):
                operations.add(type(stmt.value))
                assert set(operands(stmt.value)) <= live.points[MIRPoint(block.id, index)]
        assert operations == set(MIRRvalue.__args__)

"""Physical wrapper defaults permit writes and scope ends, never source reads."""

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NoneType, OptionalType, UnionType
from .dependencies import analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant, MIREdge,
    MIRFunction, MIRGoto, MIRIsAlternative, MIRIsPresent, MIROptionalConstruct,
    MIROptionalCopy, MIROptionalLayout, MIRPayloadWrite,
    MIRPayloadWriteMode, MIRPlace, MIRPoint, MIRRegion, MIRRegionId, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRStorageInit,
    MIRTupleElement, MIRUnionConstruct, MIRUnionCopy, MIRUnionLayout,
    MIRUnionPayload, MIRValueKind,
)
from .payload_lifetime import analyze_payload_ends
from .presence import _analyze_presence
from .scope_lifetime import analyze_scope_ends
from .storage import analyze_storage
from .testutil import execute
from .validate import MIRDefiniteAssignmentError, MIRValidationError, validate_function


BODY = MIRBodyId("storage_initialization", "example")
CURRENT, VALUE, FLAG, GUARD = (MIRSlotId(BODY, i) for i in range(4))
ENTRY, YES, NO, JOIN = (MIRBlockId(BODY, i) for i in range(4))
ROOT, CHILD = (MIRRegionId(BODY, i) for i in range(2))
ASSIGN = MIRPayloadWrite(MIRPayloadWriteMode.ASSIGN)
SHAPES = ("optional_int", "optional_bool", "union_none", "union_bool", "union_int")


def function(shape: str, *blocks: MIRBlock) -> MIRFunction:
    optional = shape.startswith("optional")
    members = {"union_none": (NoneType(), BOOL, INT32), "union_bool": (BOOL, INT32),
               "union_int": (INT32, BOOL)}.get(shape)
    scalar = BOOL if shape == "optional_bool" else INT32
    slot = MIRSlot(CURRENT, OptionalType(scalar) if optional else UnionType(members), MIRSlotKind.LOCAL,
                   value_kind=MIRValueKind.OPTIONAL if optional else MIRValueKind.UNION,
                   optional_layout=MIROptionalLayout(scalar) if optional else None,
                   union_layout=None if optional else MIRUnionLayout(tuple(
                       None if isinstance(typ, NoneType) else MIRTupleElement(typ) for typ in members)),
                   storage_duration=MIRStorageDuration.BODY, residence=ROOT)
    return MIRFunction(BODY, INT32, (slot, MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
                                    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
                                    MIRSlot(GUARD, BOOL, MIRSlotKind.LOCAL, residence=ROOT)),
                       blocks, ENTRY, regions=(MIRRegion(ROOT, None, ENTRY),))


def initialize(shape: str) -> MIRStorageInit:
    default = False if shape == "union_bool" else 0 if shape == "union_int" else None
    return MIRStorageInit(MIRPlace(CURRENT), 0, MIRConstant(default))


def write(shape: str) -> MIRAssign:
    if shape.startswith("optional"):
        value = MIROptionalConstruct(MIRPlace(FLAG if shape == "optional_bool" else VALUE))
    else:
        value = MIRUnionConstruct(2 if shape == "union_none" else 1,
                                  MIRPlace(FLAG if shape == "union_int" else VALUE))
    return MIRAssign(MIRPlace(CURRENT), value, storage_write=ASSIGN)


@pytest.mark.parametrize("shape", SHAPES)
def test_unassigned_wrapper_has_a_physical_scope_end(shape: str) -> None:
    fn = function(shape, MIRBlock(ENTRY, (initialize(shape),), MIRReturn(VALUE), ROOT))
    validate_function(fn)
    live = analyze_liveness(fn)
    assert live.entry_live == frozenset({VALUE})
    assert all(CURRENT not in slots for slots in live.points.values())
    deps = analyze_dependencies(fn, live)
    assert not any(deps.referents.values())
    assert not analyze_storage(fn).writes
    assert not analyze_payload_ends(fn).ends
    assert dict(_analyze_presence(fn).points[MIRPoint(ENTRY, 1)])[MIRPlace(CURRENT)] == frozenset({0})
    event, = analyze_scope_ends(fn).ends[MIREdge(ENTRY)]
    assert event.storage == MIRPlace(CURRENT)
    assert event.payloads == (frozenset({MIRPlace(CURRENT, (MIRUnionPayload(0),))})
                              if shape in ("union_bool", "union_int") else frozenset())
    assert "initialize-storage %0 alternative=0 value=" in dump_function(fn)
    assert execute(fn, 7, True) == 7


@pytest.mark.parametrize("shape", SHAPES)
def test_first_source_write_assigns_existing_storage(shape: str) -> None:
    fn = function(shape, MIRBlock(ENTRY, (initialize(shape), write(shape), write(shape)), MIRReturn(VALUE), ROOT))
    validate_function(fn)
    ends = analyze_payload_ends(fn).ends
    assert ends == ({MIRPoint(ENTRY, 1): frozenset({MIRPlace(CURRENT, (MIRUnionPayload(0),))})}
                    if shape in ("union_bool", "union_int") else {})
    assert execute(fn, 7, True) == 7


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("operation", ["test", "self_copy"])
def test_default_tag_cannot_authorize_source_reads(optional: bool, operation: str) -> None:
    shape = "optional_int" if optional else "union_bool"
    if operation == "test":
        stmt = MIRAssign(MIRPlace(GUARD), MIRIsPresent(MIRPlace(CURRENT)) if optional else MIRIsAlternative(MIRPlace(CURRENT), (0,)))
    else:
        stmt = MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(MIRPlace(CURRENT)) if optional else MIRUnionCopy(MIRPlace(CURRENT)),
                         storage_write=ASSIGN)
    fn = function(shape, MIRBlock(ENTRY, (initialize(shape), stmt), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRDefiniteAssignmentError, match="read before definite assignment"):
        validate_function(fn)
    with pytest.raises(AssertionError, match="source read before assignment"):
        execute(fn, 7, True)


@pytest.mark.parametrize("shape", ["optional_int", "union_bool"])
def test_conditional_assignment_does_not_assign_the_missing_arm(shape: str) -> None:
    optional = shape.startswith("optional")
    test = MIRAssign(MIRPlace(GUARD), MIRIsPresent(MIRPlace(CURRENT)) if optional else MIRIsAlternative(MIRPlace(CURRENT), (0,)))
    fn = function(shape,
        MIRBlock(ENTRY, (initialize(shape),), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (write(shape),), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (test,), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRDefiniteAssignmentError, match="read before definite assignment"):
        validate_function(fn)


def test_conditional_construction_is_a_may_end_but_not_a_must_write() -> None:
    fn = function("union_bool",
        MIRBlock(ENTRY, (), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (initialize("union_bool"),), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    assert analyze_scope_ends(fn).ends[MIREdge(JOIN)][0].storage == MIRPlace(CURRENT)
    changed = replace(fn, blocks=(*fn.blocks[:3], replace(fn.blocks[3], statements=(write("union_bool"),))))
    with pytest.raises(MIRValidationError, match="assignment before storage initialization"):
        validate_function(changed)


@pytest.mark.parametrize("shape", ["optional_int", "union_bool"])
def test_source_assignment_enables_tests_but_a_zero_trip_loop_does_not(shape: str) -> None:
    guard = MIRAssign(MIRPlace(GUARD), MIRIsPresent(MIRPlace(CURRENT)) if shape == "optional_int"
                      else MIRIsAlternative(MIRPlace(CURRENT), (1,)))
    good = function(shape, MIRBlock(ENTRY, (initialize(shape), write(shape), guard), MIRReturn(GUARD), ROOT))
    good = replace(good, return_type=BOOL)
    validate_function(good)
    assert execute(good, 7, False) is True
    zero_trip = function(shape,
        MIRBlock(ENTRY, (initialize(shape),), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (write(shape),), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (guard,), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRDefiniteAssignmentError, match="read before definite assignment"):
        validate_function(zero_trip)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("damage", ["alternative", "type", "value", "duplicate", "missing_placement", "parameter"])
def test_defaults_require_exact_layout_placement_and_single_site(shape: str, damage: str) -> None:
    init = initialize(shape)
    fn = function(shape, MIRBlock(ENTRY, (init,), MIRReturn(VALUE), ROOT))
    message = "invalid wrapper default"
    if damage == "alternative":
        init = replace(init, alternative=1)
    elif damage == "type":
        init = replace(init, value=MIRConstant(False if type(init.value.value) is int else 0))
    elif damage == "value":
        init = replace(init, value=MIRConstant(1))
    elif damage == "duplicate":
        fn = replace(fn, blocks=(replace(fn.blocks[0], statements=(init, init)),))
        message = "repeated payload initialization"
    elif damage == "missing_placement":
        fn = replace(fn, slots=(replace(fn.slots[0], storage_duration=None), *fn.slots[1:]))
        message = "physical initialization needs owning placement"
    elif damage == "parameter":
        fn = replace(fn, slots=(replace(fn.slots[0], kind=MIRSlotKind.PARAMETER, residence=None,
                                      storage_duration=None), *fn.slots[1:]))
        message = "physical initialization needs local scalar wrapper"
    if damage in ("alternative", "type", "value"):
        fn = replace(fn, blocks=(replace(fn.blocks[0], statements=(init,)),))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


@pytest.mark.parametrize("shape", ["optional_int", "union_bool"])
def test_loop_activation_ends_default_even_when_continue_skips_source_write(shape: str) -> None:
    fn = function(shape,
        MIRBlock(ENTRY, (), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (initialize(shape), MIRAssign(MIRPlace(FLAG), MIRConstant(False))), MIRGoto(ENTRY), CHILD),
        MIRBlock(NO, (), MIRReturn(VALUE), ROOT))
    fn = replace(fn, slots=(replace(fn.slots[0], storage_duration=CHILD, residence=CHILD), *fn.slots[1:]),
                 regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES)))
    ends = analyze_scope_ends(fn).ends
    assert tuple(ends) == (MIREdge(YES),)
    assert bool(ends[MIREdge(YES)][0].payloads) == (shape == "union_bool")
    assert execute(fn, 7, True) == 7
    invalid = replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=MIRGoto(YES)), fn.blocks[2]))
    with pytest.raises(MIRValidationError, match="within region activation"):
        validate_function(invalid)


def test_body_default_before_loop_is_not_reconstructed_on_backedge() -> None:
    fn = function("optional_int",
        MIRBlock(ENTRY, (initialize("optional_int"),), MIRGoto(YES), ROOT),
        MIRBlock(YES, (write("optional_int"),), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(NO, (), MIRReturn(VALUE), ROOT))
    fn = replace(fn, slots=(replace(fn.slots[0], storage_duration=MIRStorageDuration.BODY), *fn.slots[1:]))
    validate_function(fn)
    assert tuple(analyze_scope_ends(fn).ends) == (MIREdge(NO),)
    assert execute(fn, 7, False) == 7
    cyclic = replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=MIRGoto(ENTRY)), fn.blocks[2]))
    with pytest.raises(MIRValidationError, match="payload initialization in cycle"):
        validate_function(cyclic)

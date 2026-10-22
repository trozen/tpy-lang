"""Retagging ends inline payloads; scalar copies and pointer pointees survive."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, NoneType, OptionalType, UnionType
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRFunction, MIRGoto, MIRIsAlternative, MIRNotCovered,
    MIRStorageWrite, MIRStorageWriteMode, MIRPlace, MIRPoint, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRConstant, MIRWrap, MIRCopy,
)
from .shapes import MIRUnionPayload
from .payload_lifetime import MIRPayloadEnds, analyze_payload_ends, dump_payload_ends
from .presence import _analyze_presence
from .validate import validate_function
from .value import MIRValidationError
from .testutil import compiled_definitions, bind_parameters, leaf_shape, optional_shape, union_shape


BODY = MIRBodyId("payload_lifetime", "example")
PARAM, CURRENT, SAVED, VALUE, FLAG, GUARD = (MIRSlotId(BODY, i) for i in range(6))
ENTRY, YES, NO, JOIN = (MIRBlockId(BODY, i) for i in range(4))
INIT = MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)
ASSIGN = MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)


def wrapper_function(optional: bool, *blocks: MIRBlock) -> MIRFunction:
    typ = OptionalType(INT32) if optional else UnionType((NoneType(), BOOL, INT32))
    shape = (optional_shape(typ, leaf_shape(INT32)) if optional
             else union_shape(typ, (None, leaf_shape(BOOL), leaf_shape(INT32))))
    slots = tuple(MIRSlot(sid, shape, kind,
                          storage_duration=None if optional else MIRStorageDuration.CALLER if kind is MIRSlotKind.PARAMETER
                          else MIRStorageDuration.BODY)
                  for sid, kind in ((PARAM, MIRSlotKind.PARAMETER), (CURRENT, MIRSlotKind.LOCAL),
                                    (SAVED, MIRSlotKind.LOCAL)))
    return bind_parameters(MIRFunction(BODY, INT32, (*slots, MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
                                   MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
                                   MIRSlot(GUARD, leaf_shape(BOOL), MIRSlotKind.LOCAL)), blocks, ENTRY))


def write(optional: bool, tag: int | MIRSlotId, *, target: MIRSlotId = CURRENT,
          fact: MIRStorageWrite | None = ASSIGN) -> MIRAssign:
    if isinstance(tag, MIRSlotId):
        value = MIRCopy(MIRPlace(tag))
    elif optional:
        value = MIRWrap(1, MIRPlace(VALUE)) if tag else MIRWrap(0)
    else:
        value = MIRWrap(tag, None if tag == 0 else MIRPlace(FLAG) if tag == 1 else MIRPlace(VALUE))
    return MIRAssign(MIRPlace(target), value, storage_write=fact)


def payload(optional: bool, tag: int = 2, root: MIRSlotId = CURRENT) -> MIRPlace:
    return MIRPlace(root, (MIRUnionPayload(1),) if optional else (MIRUnionPayload(tag),))


def test_an_optional_and_a_union_with_none_are_one_sum() -> None:
    # `int32 | None` as an Optional and as a union laying None out first: the
    # same `MIRWrap` / `MIRIsAlternative` statements give the same selection
    # and payload-end facts.
    def facts(shape) -> tuple:
        slots = (MIRSlot(CURRENT, shape, MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),
                 MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
                 MIRSlot(GUARD, leaf_shape(BOOL), MIRSlotKind.LOCAL))
        fn = bind_parameters(MIRFunction(BODY, INT32, slots, (
            MIRBlock(ENTRY, (MIRAssign(MIRPlace(CURRENT), MIRWrap(1, MIRPlace(VALUE)), storage_write=INIT),
                             MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), (1,)))),
                     MIRBranch(GUARD, YES, NO)),
            MIRBlock(YES, (MIRAssign(MIRPlace(CURRENT), MIRWrap(0), storage_write=ASSIGN),), MIRGoto(NO)),
            MIRBlock(NO, (), MIRReturn(VALUE))), ENTRY))
        presence = _analyze_presence(fn)
        ends = analyze_payload_ends(fn)
        assert isinstance(ends, MIRPayloadEnds)
        return dict(presence.points), dict(presence.edges), presence.domains, dict(ends.ends)

    payload_shape = leaf_shape(INT32)
    optional = facts(optional_shape(OptionalType(INT32), payload_shape))
    union = UnionType((NoneType(), INT32))
    assert facts(union_shape(union, (None, payload_shape))) == optional
    assert optional[3] == {MIRPoint(YES, 0): frozenset({MIRPlace(CURRENT, (MIRUnionPayload(1),))})}


@pytest.mark.parametrize("optional", [False, True])
def test_initialization_same_tag_self_copy_and_clear(optional: bool) -> None:
    tag = 1 if optional else 2
    stmts = (write(optional, tag, fact=INIT), write(optional, tag),
             write(optional, CURRENT), write(optional, 0), write(optional, 0))
    fn = wrapper_function(optional, MIRBlock(ENTRY, stmts, MIRReturn(VALUE)))
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRPayloadEnds)
    assert result.ends == {MIRPoint(ENTRY, 3): frozenset({payload(optional)})}
    assert result.function is fn
    with pytest.raises(TypeError):
        result.ends[MIRPoint(ENTRY, 0)] = frozenset()
    assert "possible inline scalar lifetime ends" in dump_payload_ends(result)
    assert dump_payload_ends(result) == dump_payload_ends(analyze_payload_ends(fn))


@pytest.mark.parametrize("optional", [False, True])
def test_unknown_tag_is_full_domain_but_self_copy_is_correlated(optional: bool) -> None:
    fn = wrapper_function(optional, MIRBlock(ENTRY, (
        write(optional, PARAM, fact=INIT), write(optional, CURRENT), write(optional, PARAM)), MIRReturn(VALUE)))
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRPayloadEnds)
    expected = {payload(True)} if optional else {payload(False, 1), payload(False, 2)}
    assert result.ends == {MIRPoint(ENTRY, 2): frozenset(expected)}
    presence = _analyze_presence(fn)
    assert dict(presence.points[MIRPoint(ENTRY, 2)]).get(MIRPlace(CURRENT)) is None
    assert presence.domains[MIRPlace(CURRENT)] == frozenset({0, 1} if optional else {0, 1, 2})


@pytest.mark.parametrize("optional", [False, True])
def test_copy_has_independent_payload_storage(optional: bool) -> None:
    tag = 1 if optional else 2
    fn = wrapper_function(optional, MIRBlock(ENTRY, (
        write(optional, tag, fact=INIT), write(optional, CURRENT, target=SAVED, fact=INIT),
        write(optional, 0), write(optional, 0, target=SAVED)), MIRReturn(VALUE)))
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRPayloadEnds)
    assert result.ends == {MIRPoint(ENTRY, 2): frozenset({payload(optional)}),
                           MIRPoint(ENTRY, 3): frozenset({payload(optional, root=SAVED)})}


@pytest.mark.parametrize("optional", [False, True])
def test_branch_selection_limits_ends_and_omits_infeasible_points(optional: bool) -> None:
    test = MIRIsAlternative(MIRPlace(CURRENT), (1,)) if optional else MIRIsAlternative(MIRPlace(CURRENT), (2,))
    fn = wrapper_function(optional,
        MIRBlock(ENTRY, (write(optional, PARAM, fact=INIT), MIRAssign(MIRPlace(GUARD), test)),
                 MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (write(optional, 0),), MIRReturn(VALUE)),
        MIRBlock(NO, (write(optional, 0),), MIRReturn(VALUE)))
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRPayloadEnds)
    assert result.ends[MIRPoint(YES, 0)] == frozenset({payload(optional)})
    assert result.ends.get(MIRPoint(NO, 0), frozenset()) == (frozenset() if optional else frozenset({payload(False, 1)}))
    known = replace(fn, blocks=(replace(fn.blocks[0], statements=(write(optional, 0, fact=INIT),
                                                               fn.blocks[0].statements[1])), *fn.blocks[1:]))
    presence = _analyze_presence(known)
    assert MIRPoint(YES, 0) not in presence.points
    assert not analyze_payload_ends(known).ends


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("unreachable", [False, True])
def test_missing_fact_is_uncovered_even_on_unreachable_write(optional: bool, unreachable: bool) -> None:
    initial = write(optional, PARAM, fact=None)
    blocks = (MIRBlock(ENTRY, (initial, write(optional, 0)), MIRReturn(VALUE)),)
    if unreachable:
        blocks = (MIRBlock(ENTRY, (), MIRReturn(VALUE)), MIRBlock(YES, (initial,), MIRReturn(VALUE)))
    fn = wrapper_function(optional, *blocks)
    validate_function(fn)
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRNotCovered) and result.reason == "missing payload write fact"


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("malformation, message", [
    ("assign_first", "assignment before storage initialization"),
    ("double_init", "repeated payload initialization"),
    ("cyclic_init", "payload initialization in cycle"),
    ("bad_mode", "invalid storage write fact"),
])
def test_invalid_initialization_contract(optional: bool, malformation: str, message: str) -> None:
    stmts = (write(optional, PARAM, fact=INIT),)
    term = MIRReturn(VALUE)
    if malformation == "assign_first":
        stmts = (write(optional, 0),)
    elif malformation == "double_init":
        stmts = stmts * 2
    elif malformation == "cyclic_init":
        term = MIRGoto(ENTRY)
    else:
        stmts = (replace(stmts[0], storage_write=MIRStorageWrite("initialize")),)
    fn = wrapper_function(optional, MIRBlock(ENTRY, stmts, term))
    with pytest.raises(MIRValidationError, match=message):
        analyze_payload_ends(fn)


@pytest.mark.parametrize("optional", [False, True])
def test_loop_assignments_use_joined_incoming_tags(optional: bool) -> None:
    tag = 1 if optional else 2
    fn = wrapper_function(optional,
        MIRBlock(ENTRY, (write(optional, tag, fact=INIT),), MIRGoto(YES)),
        MIRBlock(YES, (write(optional, 0), write(optional, tag)), MIRBranch(FLAG, YES, NO)),
        MIRBlock(NO, (), MIRReturn(VALUE)))
    result = analyze_payload_ends(fn)
    assert isinstance(result, MIRPayloadEnds)
    assert result.ends == {MIRPoint(YES, 0): frozenset({payload(optional)})}


def test_payload_fact_cannot_authorize_scalar_or_parameter_writes() -> None:
    # A scalar leaf holds no storage the fact could name; a parameter's payload is the caller's.
    for stmt, message in ((MIRAssign(MIRPlace(GUARD), MIRConstant(True), storage_write=INIT),
                           "record write on non-record operation"),
                          (write(False, 0, target=PARAM, fact=INIT),
                           "payload write needs a local or temporary scalar wrapper")):
        with pytest.raises(MIRValidationError, match=message):
            validate_function(wrapper_function(False, MIRBlock(ENTRY, (stmt,), MIRReturn(VALUE))))


def test_assignment_requires_initialization_on_every_predecessor() -> None:
    fn = wrapper_function(False,
        MIRBlock(ENTRY, (), MIRBranch(FLAG, YES, NO)),
        MIRBlock(YES, (write(False, 2, fact=INIT),), MIRGoto(JOIN)),
        MIRBlock(NO, (), MIRGoto(JOIN)),
        MIRBlock(JOIN, (write(False, 0),), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError, match="assignment before storage initialization"):
        analyze_payload_ends(fn)


SOURCE = """\
from tpy import int32, readonly

def snapshot(value: int32 | bool) -> int32:
    current = value
    saved = 0
    if isinstance(current, int32):
        saved = current
    current = True
    return saved

def clear(value: int32 | None) -> int32:
    current = value
    saved = current
    current = None
    if saved is not None:
        return saved
    return 0

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        current: int32 | bool = value
        current = True
    def replace(self, value: int32 | None) -> int32:
        current = value
        current = None
        return self.value

def pointer_clear(value: Cell | None) -> int32:
    current = value
    current = None
    return 0

class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def pointer_reseat(value: Cell | Other, other: Cell | Other) -> int32:
    current = value
    current = other
    return 0

def readonly_source(value: readonly[int32 | bool]) -> int32:
    current = value
    current = True
    return 0
"""


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def source_bodies(compiled) -> dict[str, MIRFunction]:
    compiler, modules = compiled
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        functions = {fn.name: lower_function(fn, MIRBodyId("payload_source", fn.name), definitions=definitions)
                     for fn in ctx.thir_functions.values()}
        functions.update({c.record_name: lower_constructor(c, MIRBodyId("payload_source", c.record_name),
                                                         definitions=definitions)
                          for c in ctx.thir_constructors.values()})
        for fn in functions.values():
            assert isinstance(fn, MIRFunction), fn
        return functions


@pytest.mark.parametrize("name", ["snapshot", "clear", "replace", "Cell", "readonly_source"])
def test_source_producers_stamp_initialization_and_assignment(source_bodies: dict[str, MIRFunction], name: str) -> None:
    fn = source_bodies[name]
    facts = [s.storage_write.mode for b in fn.blocks for s in b.statements if isinstance(s.storage_write, MIRStorageWrite)]
    assert facts == ([MIRStorageWriteMode.INITIALIZE] * (2 if name == "clear" else 1)
                     + [MIRStorageWriteMode.IN_PLACE])
    events = analyze_payload_ends(fn)
    assert isinstance(events, MIRPayloadEnds) and len(events.ends) == 1
    affected = next(iter(events.ends.values()))
    current = next(s.id for s in fn.slots if s.name == "current")
    assert all(p.root == current for p in affected)


@pytest.mark.parametrize("name", ["snapshot", "clear", "pointer_reseat"])
def test_a_scalar_or_lending_wrapper_copy_cannot_raise(source_bodies: dict[str, MIRFunction], name: str) -> None:
    # A union's or an Optional's copy copies no buffer: its members are
    # inert scalars or loans of a record.
    fn = source_bodies[name]
    copies = [s.value for b in fn.blocks for s in b.statements
              if isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy)]
    assert copies and not any(c.may_raise for c in copies)


@pytest.mark.parametrize("name", ["pointer_clear", "pointer_reseat"])
def test_pointer_wrapper_writes_do_not_end_the_pointee(source_bodies: dict[str, MIRFunction], name: str) -> None:
    fn = source_bodies[name]
    assert not any(isinstance(s.storage_write, MIRStorageWrite) for b in fn.blocks for s in b.statements)
    assert analyze_payload_ends(fn).ends == {}


def test_mixed_wrapper_writes_remain_uncovered_by_lowering() -> None:
    compiler, modules = _compile(SOURCE + """
def mixed(value: Cell | int32) -> int32:
    current = value
    current = 3
    return 0
""")
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for fn in ctx.thir_functions.values() if fn.name == "mixed")
    result = lower_function(fn, MIRBodyId("payload_source", "mixed"))
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported parameter type"

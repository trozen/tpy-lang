"""Nullable holders retain independent record storage across replacement and clear."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from ..parse import RebindStorage
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRConstruct, MIRDeref, MIRFunction, MIRNotCovered,
    MIROptionalConstruct, MIROptionalPayload, MIRPlace, MIRRecordWriteMode,
)
from .storage import MIRStorageEvents, analyze_storage
from .testutil import Heap, Reference, execute
from .validate import MIRPresenceError, MIRValidationError, validate_function


SOURCE = """\
from tpy import int32, Own

class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

def retained() -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    # A copy at saved would miss this mutation; retargeting it would read 2.
    if current is not None:
        current.value = 7
    current = Cell(2)
    if saved is not None:
        return saved.value
    return 0

def cleared() -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    if current is not None:
        current.value = 7
    current = None
    if saved is not None:
        saved.value = 9
        return saved.value
    return 0

def in_place() -> int32:
    current: Cell | None = Cell(3)
    # The RHS reads the old payload before the selected in-place write.
    current = Cell(current.value)
    if current is not None:
        return current.value
    return 0

def from_none() -> int32:
    current: Cell | None = None
    current = Cell(5)
    if current is not None:
        return current.value
    return 0

def branches(flag: bool) -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    if current is not None:
        current.value = 7
    if flag:
        current = Cell(2)
    else:
        current = Cell(3)
    if saved is not None:
        saved.value = 9
    if current is not None:
        return current.value
    return 0

class Host:
    value: int32

    def __init__(self):
        self.value = 0
        current: Cell | None = Cell(1)
        saved: Cell | None = current
        if current is not None:
            current.value = 7
        current = Cell(2)
        if saved is not None:
            self.value = saved.value

    def optional_method(self) -> int32:
        current: Cell | None = Cell(3)
        current = Cell(current.value)
        if current is not None:
            self.value = current.value
        return self.value

def loop(flag: bool) -> int32:
    current: Cell | None = Cell(1)
    while flag:
        current = Cell(2)
        flag = False
    return 0

def annotation_only() -> int32:
    current: Cell | None
    current = Cell(1)
    current = Cell(2)
    return 0

def make() -> Own[Cell | None]:
    return Cell(1)

def owned_call() -> int32:
    current: Cell | None = make()
    return 0

def effectful() -> int32:
    value = 1
    current: Cell | None = Cell(value := 2)
    return value
"""

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    return ({fn.name: fn for fn in ctx.thir_functions.values()}, tuple(ctx.thir_constructors.values()))


def lower(fn: th.THIRFunction, constructors: tuple[th.THIRConstructor, ...]) -> MIRFunction | MIRNotCovered:
    return lower_function(fn, MIRBodyId("optional_owned", fn.name),
                          kind=MIRBodyKind.METHOD if fn.receiver is not None else MIRBodyKind.FREE_FUNCTION,
                          definitions=MIRDefinitions(constructors))


@pytest.mark.parametrize("name,expected,contents", [
    ("retained", 7, [2, 7]), ("cleared", 9, [9]), ("in_place", 3, [3]),
    ("from_none", 5, [5]),
])
def test_optional_storage_identity(artifacts: Artifacts, name: str, expected: int, contents: list[int]) -> None:
    fn = lower(artifacts[0][name], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    heap: Heap = {}
    assert execute(fn, heap=heap) == expected
    member = fn.records[0].fields[0].id
    # Object count and both payloads catch copies and lost aliases even if the return agrees.
    assert sorted(obj[member] for obj in heap.values()) == contents
    assert dump_function(fn) == dump_function(lower(artifacts[0][name], artifacts[1]))
    events = analyze_storage(fn)
    assert isinstance(events, MIRStorageEvents)
    expected_modes = {
        "retained": [MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.OWN_SITE],
        "cleared": [MIRRecordWriteMode.INITIALIZE_ONCE],
        "in_place": [MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.IN_PLACE],
        "from_none": [MIRRecordWriteMode.OWN_SITE],
    }
    assert [s.storage_write.mode for s in events.writes.values()] == expected_modes[name]
    for stmt in events.writes.values():
        if stmt.storage_write.mode is MIRRecordWriteMode.IN_PLACE:
            assert stmt.storage_write.rebind_owner == stmt.target.root


@pytest.mark.parametrize("flag", [False, True])
def test_branch_storage_is_created_only_on_selected_edge(artifacts: Artifacts, flag: bool) -> None:
    fn = lower(artifacts[0]["branches"], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    heap: Heap = {}
    expected = 2 if flag else 3
    assert execute(fn, flag, heap=heap) == expected
    member = fn.records[0].fields[0].id
    assert sorted(obj[member] for obj in heap.values()) == [expected, 9]


def test_method_and_constructor_tail_share_storage_operations(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    ctor = next(c for c in constructors if c.record_layout.type.name == "Host")
    fn = lower_constructor(ctor, MIRBodyId("optional_owned", "Host"),
                           definitions=MIRDefinitions(constructors))
    assert isinstance(fn, MIRFunction), fn
    heap: Heap = {}
    execute(fn, Reference(10), heap=heap)
    member = next(r.fields[0].id for r in fn.records if r.type.name == "Host")
    assert heap[10][member] == 7 and len(heap) == 3
    method = lower(functions["optional_method"], constructors)
    assert isinstance(method, MIRFunction), method
    assert execute(method, Reference(10), heap=heap) == 3
    assert heap[10][member] == 3 and len(heap) == 4


def test_producer_records_payload_ownership_and_effective_replacement(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    owned = functions["retained"].body[0]
    assert owned.kind is th.PtrSlotKind.OPT_RVALUE
    assert owned.owned_storage == owned.optional_layout.payload
    assert functions["in_place"].body[1].rebind_storage is RebindStorage.IN_PLACE
    assert functions["retained"].body[3].rebind_storage is RebindStorage.OWN
    assert functions["from_none"].body[0].owned_storage is None
    assert functions["from_none"].body[1].rebind_storage is RebindStorage.OWN


@pytest.mark.parametrize("change", [
    lambda d: replace(d, kind=th.PtrSlotKind.OPT_NONE),
    lambda d: replace(d, init=None),
    lambda d: replace(d, is_const=True),
    lambda d: replace(d, optional_layout=th.THIROptionalLayout(BOOL)),
    lambda d: replace(d, owned_storage=replace(d.owned_storage, readonly=True)),
])
def test_thir_rejects_inconsistent_owned_payloads(
    artifacts: Artifacts, change: Callable[[th.THIRPtrLocalDecl], th.THIRPtrLocalDecl],
) -> None:
    fn = artifacts[0]["retained"]
    with pytest.raises(THIRValidationError, match="owned|optional layout"):
        validate_thir(replace(fn, body=(change(fn.body[0]), *fn.body[1:])))


def test_missing_fact_does_not_infer_ownership_from_cpp(artifacts: Artifacts) -> None:
    fn = artifacts[0]["retained"]
    fn = replace(fn, body=(replace(fn.body[0], owned_storage=None), *fn.body[1:]))
    result = lower(fn, artifacts[1])
    assert isinstance(result, MIRNotCovered) and result.reason == "optional backing storage"


@pytest.mark.parametrize("name,reason", [
    ("owned_call", "optional backing storage"), ("effectful", "effectful constructor argument"),
])
def test_unsupported_storage_families_remain_uncovered(artifacts: Artifacts, name: str, reason: str) -> None:
    result = lower(artifacts[0][name], artifacts[1])
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_cyclic_optional_in_place_replacement(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["loop"], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    assert execute(fn, False) == execute(fn, True) == 0
    writes = analyze_storage(fn)
    assert any(s.storage_write.mode is MIRRecordWriteMode.IN_PLACE for s in writes.writes.values())


@pytest.mark.parametrize("custom", ["custom_copy", "custom_move", "custom_destructor"])
def test_special_members_still_prevent_constructor_expansion(artifacts: Artifacts, custom: str) -> None:
    constructors = tuple(replace(c, record_layout=replace(c.record_layout, **{custom: True}))
                         if c.record_layout.type.name == "Cell" else c for c in artifacts[1])
    result = lower(artifacts[0]["retained"], constructors)
    assert isinstance(result, MIRNotCovered) and "special" in result.reason


def test_in_place_requires_current_presence(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["in_place"], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    blocks = []
    changed = False
    for block in fn.blocks:
        statements = []
        for stmt in block.statements:
            if isinstance(stmt.value, MIRConstruct) and stmt.target.projections:
                assert stmt.target.projections == (MIROptionalPayload(), MIRDeref())
                statements.append(MIRAssign(MIRPlace(stmt.target.root), MIROptionalConstruct()))
                changed = True
            statements.append(stmt)
        blocks.append(replace(block, statements=tuple(statements)))
    assert changed
    with pytest.raises(MIRPresenceError, match="current presence proof"):
        validate_function(replace(fn, blocks=tuple(blocks)))


def test_in_place_cannot_mutate_readonly_payload(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["in_place"], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    slots = tuple(replace(s, optional_layout=replace(s.optional_layout, readonly=True))
                  if s.optional_layout is not None else s for s in fn.slots)
    with pytest.raises(MIRValidationError, match="readonly"):
        validate_function(replace(fn, slots=slots))


def test_readonly_holder_copy_retains_owned_referent(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["retained"], artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    slots = tuple(replace(s, optional_layout=replace(s.optional_layout, readonly=True))
                  if s.name == "saved" else s for s in fn.slots)
    fn = replace(fn, slots=slots)
    validate_function(fn)
    heap: Heap = {}
    assert execute(fn, heap=heap) == 7 and len(heap) == 2


def test_annotation_only_with_explicit_storage_verdicts(artifacts: Artifacts) -> None:
    source = artifacts[0]["annotation_only"]
    assert source.body[1].rebind_storage is RebindStorage.OWN
    assert source.body[2].rebind_storage is RebindStorage.IN_PLACE
    fn = lower(source, artifacts[1])
    assert isinstance(fn, MIRFunction), fn
    heap: Heap = {}
    assert execute(fn, heap=heap) == 0 and len(heap) == 1


def test_inline_slot_without_storage_verdict_stays_uncovered(artifacts: Artifacts) -> None:
    source = artifacts[0]["annotation_only"]
    first = source.body[1]
    inline = th.THIRPtrLocalRebind(name=first.target.name, kind=th.PtrSlotKind.INLINE_RVALUE,
                                  value=first.value, optional_layout=first.optional_layout)
    fn = replace(source, body=(source.body[0], inline, *source.body[2:]))
    result = lower(fn, artifacts[1])
    assert isinstance(result, MIRNotCovered) and "unsupported optional reseat" in result.reason


@pytest.mark.parametrize("kind", [MIRBodyKind.MODULE, MIRBodyKind.CLOSURE, MIRBodyKind.GENERATOR,
                                 MIRBodyKind.ASYNC, MIRBodyKind.GENERIC])
def test_storage_facts_do_not_admit_unsupported_bodies(artifacts: Artifacts, kind: MIRBodyKind) -> None:
    fn = artifacts[0]["retained"]
    result = lower_function(fn, MIRBodyId("optional_owned", fn.name), kind=kind,
                            definitions=MIRDefinitions(artifacts[1]))
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported body kind"


def test_upcast_constructor_does_not_claim_plain_payload_ownership() -> None:
    compiler, modules = _compile("""\
from tpy import int32
class Base:
    value: int32
    def __init__(self):
        self.value = 1
class Child(Base):
    def __init__(self):
        self.value = 2
def declaration() -> int32:
    current: Base | None = Child()
    if current is not None:
        return current.value
    return 0
def replacement() -> int32:
    current: Base | None = None
    current = Child()
    if current is not None:
        return current.value
    return 0
""")
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    decl = functions["declaration"].body[0]
    assert decl.kind is th.PtrSlotKind.OPT_RVALUE and decl.optional_layout is not None
    assert decl.init.result_type != decl.optional_layout.payload.type
    assert decl.owned_storage is None
    reseat = functions["replacement"].body[1]
    assert reseat.target.result_type != reseat.optional_layout.payload.type
    constructors = tuple(ctx.thir_constructors.values())
    for name, reason in (("declaration", "optional backing storage"),
                         ("replacement", "optional destination type mismatch")):
        result = lower(functions[name], constructors)
        assert isinstance(result, MIRNotCovered) and reason == result.reason

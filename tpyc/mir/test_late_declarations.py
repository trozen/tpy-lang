"""A skipped declaration constructs nothing; a reached one retains its real scope."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch,
    MIREdge, MIRFunction, MIRGoto, MIRIsPresent, MIRReturn,
    MIROptionalConstruct, MIROptionalPayload, MIRPlace, MIRRead, MIRSlot, MIRSlotId, MIRSlotKind,
)
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .test_scope_lifetime import region_fixture
from .testutil import Reference, execute
from .validate import MIRValidationError, validate_function


SOURCE = '''from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

    def method(self, stop: bool) -> int32:
        if stop:
            return 0
        local = Cell(1)
        alias = local
        local.value = 9
        return alias.value

class Observer:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        if flag:
            self.value = 1
        local = Cell(2)
        alias = local
        local.value = 9
        self.value = alias.value

def late(stop: bool) -> int32:
    if stop:
        return 0
    local = Cell(1)
    alias = local
    local.value = 9
    return alias.value

def nested(flag: bool, stop: bool) -> int32:
    if flag:
        if stop:
            return 0
        local = Cell(2)
        alias = local
        local.value = 9
        return alias.value
    return 0

def loop(skip: bool, stop: bool, after: bool) -> int32:
    result = 0
    flag = True
    while flag:
        flag = False
        if stop:
            break
        if skip:
            continue
        local = Cell(3)
        alias = local
        local.value = 9
        result = alias.value
        if after:
            break
        continue
    else:
        return result
    return result

def tuples(stop: bool) -> int32:
    if stop:
        return 0
    local = Cell(1)
    single = (local,)
    mixed = (local, 2)
    local.value = 9
    if single[0].value == 9:
        return mixed[0].value
    return 0

def wrappers(stop: bool, value: int32 | None, choice: int32 | bool) -> int32:
    if stop:
        return 0
    optional = value
    union = choice
    if optional is None:
        return 0
    if isinstance(union, int32):
        return union
    return optional

def optional_record(stop: bool) -> int32:
    if stop:
        return 0
    local: Cell | None = Cell(1)
    if local is not None:
        local.value = 9
        return local.value
    return 0

def borrowed(stop: bool, source: readonly[Cell]) -> int32:
    if stop:
        return 0
    alias = source
    return alias.value

def optional_holder(stop: bool, source: Cell | None) -> int32:
    if stop:
        return 0
    alias = source
    if alias is not None:
        alias.value = 9
        return alias.value
    return 0

def conditional(stop: bool, flag: bool) -> int32:
    if stop:
        return 0
    value = 1 if flag else 2
    selected = flag and (value == 1)
    return value if selected else 3
'''


@pytest.fixture(scope="module")
def late_bodies() -> tuple[dict[str, MIRFunction], str]:
    compiler, modules = _compile(SOURCE)
    (header, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    bodies = {}
    for node, fn in ctx.thir_functions.items():
        result = lower_function(fn, MIRBodyId("late", node.name), definitions=definitions)
        assert isinstance(result, MIRFunction), (node.name, result)
        bodies[node.name] = result
    for ctor in ctx.thir_constructors.values():
        result = lower_constructor(ctor, MIRBodyId("late", ctor.record_name), definitions=definitions)
        assert isinstance(result, MIRFunction), result
        bodies[ctor.record_name] = result
    return bodies, header + cpp


def test_late_producers_keep_scope_and_shared_mutation(late_bodies: tuple[dict[str, MIRFunction], str]) -> None:
    bodies, cpp = late_bodies
    for fn in bodies.values():
        assert inspect_scope_lifetimes(fn).conflicts == ()
    for name in ("late", "tuples", "optional_record"):
        assert execute(bodies[name], False) == 9
        assert execute(bodies[name], True) == 0
    for flag in (False, True):
        for stop in (False, True):
            assert execute(bodies["nested"], flag, stop) == (9 if flag and not stop else 0)
            assert execute(bodies["conditional"], stop, flag) == (0 if stop else 1 if flag else 3)
    assert "return 0;\n    }\n    Cell local = Cell(1);" in cpp
    assert "Cell& alias = local;" in cpp
    # The constructor's later local must not overwrite its receiver identity.
    heap = {}
    execute(bodies["Observer"], Reference(0), False, heap=heap)
    assert tuple(heap[0].values()) == (9,)
    assert execute(bodies["method"], Reference(0), False, heap=heap) == 9


@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("stop", [False, True])
@pytest.mark.parametrize("after", [False, True])
def test_loop_exits_before_and_after_construction(late_bodies: tuple[dict[str, MIRFunction], str],
                                                skip: bool, stop: bool, after: bool) -> None:
    fn = late_bodies[0]["loop"]
    assert execute(fn, skip, stop, after) == (0 if stop or skip else 9)
    ends = analyze_scope_ends(fn)
    assert len(ends.ends) == 2


def conditional_region() -> MIRFunction:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    initialize = MIRBlockId(fn.id, 3)
    skip = MIRSlot(MIRSlotId(fn.id, 3), BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)
    return replace(fn, slots=(*fn.slots, skip), blocks=(entry,
        replace(iteration, statements=(), terminator=MIRBranch(skip.id, after.id, initialize)),
        after, replace(iteration, id=initialize)))


@pytest.mark.parametrize("absent", [False, True])
def test_skipped_construction_has_no_end_but_empty_wrapper_does(absent: bool) -> None:
    fn = conditional_region()
    entry, iteration, after, initialize = fn.blocks
    if absent:
        stmt = replace(initialize.statements[0], value=MIROptionalConstruct())
        fn = replace(fn, blocks=(entry, iteration, after, replace(initialize, statements=(stmt,))))
    validate_function(fn)
    result = analyze_scope_ends(fn)
    assert tuple(result.ends) == (MIREdge(initialize.id),)
    end, = result.ends[MIREdge(initialize.id)]
    assert bool(end.payloads) is not absent


def test_last_activation_does_not_definitely_initialize_the_next() -> None:
    fn = conditional_region()
    entry, iteration, after, initialize = fn.blocks
    read = MIRAssign(MIRPlace(fn.slots[1].id), MIRIsPresent(MIRPlace(fn.slots[2].id)))
    broken = replace(fn, blocks=(entry, replace(iteration, statements=(read,)), after, initialize))
    with pytest.raises(MIRValidationError, match="read before definite assignment"):
        validate_function(broken)


def test_optional_scalar_copy_survives_its_wrapper_end() -> None:
    fn = conditional_region()
    entry, iteration, after, initialize = fn.blocks
    copied = MIRSlot(MIRSlotId(fn.id, 4), INT32, MIRSlotKind.LOCAL, residence=entry.region)
    done = MIRBlockId(fn.id, 4)
    read = MIRAssign(MIRPlace(copied.id), MIRRead(MIRPlace(fn.slots[2].id, (MIROptionalPayload(),))))
    fn = replace(fn, slots=(*fn.slots, copied), blocks=(entry,
        replace(iteration, terminator=MIRBranch(fn.slots[3].id, entry.id, initialize.id)), after,
        replace(initialize, statements=(*initialize.statements, read), terminator=MIRGoto(done)),
        MIRBlock(done, (), MIRReturn(copied.id), entry.region)))
    result = inspect_scope_lifetimes(fn)
    assert MIREdge(iteration.id, 0) not in result.ends.ends
    assert result.conflicts == () and result.freshness == ()
    assert execute(fn, 9, True, False) == 9


def test_bounded_trace_keeps_skipped_activation_empty() -> None:
    fn = conditional_region()
    entry, iteration, after, initialize = fn.blocks
    fn = replace(fn, blocks=(entry, replace(iteration, terminator=MIRBranch(
        fn.slots[3].id, entry.id, initialize.id)), after, initialize))
    ends = analyze_scope_ends(fn)
    blocks = {b.id: b for b in fn.blocks}
    # The execution oracle uses fresh tokens, not the analysis's static root.
    ended_tokens = []
    skipped = (False, True, False)
    activation = -1
    token = None
    block = blocks[fn.entry]
    visited = []
    while not isinstance(block.terminator, MIRReturn):
        visited.append(block.id)
        if block.id == initialize.id:
            assert token is None
            token = ("local", activation)
        match block.terminator:
            case MIRBranch(condition=condition, then=yes, otherwise=no):
                if block.id == entry.id:
                    activation += 1
                    truth = activation < len(skipped)
                else:
                    assert condition == fn.slots[3].id
                    truth = skipped[activation]
                edge, target = MIREdge(block.id, 0 if truth else 1), yes if truth else no
            case MIRGoto(target=target):
                edge = MIREdge(block.id)
            case _:
                raise AssertionError(block.terminator)
        if block.region != blocks[target].region:
            assert bool(ends.ends.get(edge)) == (token is not None)
            if token is not None:
                ended_tokens.append(token)
                token = None
        block = blocks[target]
    assert visited.count(iteration.id) == 3
    assert visited.count(initialize.id) == 2
    assert ended_tokens == [("local", 0), ("local", 2)]

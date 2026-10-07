"""Repeated record writes retain activation and surviving-alias distinctions."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import validate_function as validate_thir
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import (
    MIRBodyId, MIRCopy, MIRDeref, MIRFunction, MIRGoto, MIRMove,
    MIRNotCovered, MIRPlace, MIRRecordWrite, MIRRecordWriteMode, MIRStorageDuration,
    MIRTupleConstruct,
)
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_retention import CURRENT, INITIAL, LOOP, analyze, loop_function
from .test_scope_inspection import scoped_loop
from .testutil import execute
from .validate import MIRValidationError, validate_function


SOURCE = '''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def fresh(run: bool, stop: bool, skip: bool, early: bool) -> int32:
    original = Cell(3)
    result = 0
    second = False
    while run:
        local = Cell(3)
        local.value = 9
        result = original.value
        if early:
            return result
        if stop:
            break
        run = not second
        second = True
        if skip:
            continue
    return result
'''


@pytest.fixture(scope="module")
def source() -> tuple[th.THIRFunction, MIRDefinitions]:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "fresh")
    return fn, MIRDefinitions(tuple(ctx.thir_constructors.values()))


def transferred(source: tuple[th.THIRFunction, MIRDefinitions], move: bool,
                *, inner_source: bool = False, readonly: bool = False) -> th.THIRFunction:
    fn, _ = source
    original, result, second, loop, ret = fn.body
    original = replace(original, is_const=readonly, owned_storage=replace(original.owned_storage, readonly=readonly))
    name = th.THIRName(original.resolved_type, "original", form=th.Form.STORAGE)
    value = (th.THIRMove(name.result_type, name, form=name.form) if move else
             th.THIRCopy(name.result_type, name, "Cell", form=th.Form.STORAGE))
    local = replace(loop.body[0], init=value)
    loop = replace(loop, body=(*((original,) if inner_source else ()), local, *loop.body[1:]))
    fn = replace(fn, body=(*(() if inner_source else (original,)), result, second, loop, ret))
    validate_thir(fn)
    return fn


def lower(fn: th.THIRFunction, definitions: MIRDefinitions) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("cyclic", fn.name),
                            definitions=definitions)
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("move,inner,readonly", [(False, False, False), (False, True, False),
                                                (False, False, True), (True, False, False), (True, True, False)])
def test_each_activation_owns_an_independent_copy(
        source: tuple[th.THIRFunction, MIRDefinitions], move: bool, inner: bool, readonly: bool) -> None:
    fn = lower(transferred(source, move, inner_source=inner, readonly=readonly), source[1])
    writes = analyze_storage(fn).writes.values()
    transfer, = (s for s in writes if isinstance(s.value, MIRMove if move else MIRCopy))
    assert transfer.storage_write.mode is MIRRecordWriteMode.INITIALIZE_REGION
    for run, stop, skip, early, copies in ((False, False, False, False, 0), (True, False, False, False, 2),
                                         (True, True, False, False, 1), (True, False, True, False, 2),
                                         (True, False, False, True, 1)):
        heap = {}
        assert execute(fn, run, stop, skip, early, heap=heap) == (3 if run else 0)
        values = [next(iter(fields.values())) for fields in heap.values()]
        assert values.count(9) == copies
        assert values.count(3) == (copies if inner else 1)
    assert inspect_scope_lifetimes(fn).conflicts == ()


def test_lowering_requires_positive_scoped_copy_fact(source: tuple[th.THIRFunction, MIRDefinitions]) -> None:
    fn = transferred(source, False)
    loop = fn.body[3]
    local = replace(loop.body[0], storage_placement=None)
    fn = replace(fn, body=(*fn.body[:3], replace(loop, body=(local, *loop.body[1:])), fn.body[-1]))
    result = lower_function(fn, MIRBodyId("cyclic", "missing"),
                            definitions=source[1])
    assert isinstance(result, MIRNotCovered) and result.reason == "loop copy or move needs scoped or hoisted storage"


def test_readonly_move_stays_uncovered(source: tuple[th.THIRFunction, MIRDefinitions]) -> None:
    fn = transferred(source, True, readonly=True)
    result = lower_function(fn, MIRBodyId("cyclic", "readonly"),
                            definitions=source[1])
    assert isinstance(result, MIRNotCovered) and result.reason == "move needs fixed movable owned local"


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("reseat_first", [False, True])
def test_previous_activation_alias_survives_copy_reconstruction(move: bool, shape: str, reseat_first: bool) -> None:
    fn, observed = scoped_loop(shape, reseat_first=reseat_first)
    block = fn.blocks[1]
    transfer = MIRMove(INITIAL) if move else MIRCopy(MIRPlace(INITIAL))
    init = replace(block.statements[0], value=transfer)
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(init, *block.statements[1:])), *fn.blocks[2:]))
    result = inspect_scope_lifetimes(fn)
    assert not isinstance(result.conflicts, MIRNotCovered)
    assert {c.holder for c in result.conflicts} == (set() if reseat_first else {observed})


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("fault,reason", [("duplicate", "repeated storage initialization"),
                                         ("activation", "repeated initialization within region activation"),
                                         ("body", "owning operation in cycle")])
def test_transfer_does_not_bypass_activation_rules(move: bool, fault: str, reason: str) -> None:
    fn, _ = scoped_loop("record")
    block = fn.blocks[1]
    init = replace(block.statements[0], value=MIRMove(INITIAL) if move else MIRCopy(MIRPlace(INITIAL)))
    statements = (init, init, *block.statements[1:]) if fault == "duplicate" else (init, *block.statements[1:])
    if fault == "body":
        statements = (replace(init, storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)),
                      *block.statements[1:])
        fn = replace(fn, slots=tuple(replace(s, storage_duration=MIRStorageDuration.BODY,
                                             residence=fn.blocks[0].region)
                                     if s.id == init.target.root else s for s in fn.slots))
    block = replace(block, statements=statements,
                    terminator=MIRGoto(block.id) if fault == "activation" else block.terminator)
    fn = replace(fn, blocks=(fn.blocks[0], block, *fn.blocks[2:]))
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(fn)


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("operation", ["construct", "copy", "move"])
def test_cyclic_replacement_checks_retained_holder_leaves(shape: str, safe: bool, operation: str) -> None:
    fn, point, holder = loop_function(shape, safe=safe)
    block = fn.blocks[1]
    # Replace through current without the OWN-site reseat: prior aliases now overlap the write.
    write = replace(block.statements[point.index], target=MIRPlace(CURRENT, (MIRDeref(),)),
                    storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, CURRENT))
    if operation != "construct":
        write = replace(write, value=MIRCopy(MIRPlace(INITIAL)) if operation == "copy" else MIRMove(INITIAL))
    stmts = (*block.statements[:point.index], write, *block.statements[point.index + 3:])
    capture = stmts[-1]
    value = (replace(capture.value, elements=(CURRENT, *capture.value.elements[1:]))
             if isinstance(capture.value, MIRTupleConstruct) else replace(capture.value, source=CURRENT))
    stmts = (*stmts[:-1], replace(capture, value=value))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=stmts), *fn.blocks[2:]))
    result = analyze(fn)
    # A move also empties INITIAL, which `current` still reaches from the
    # first activation and is captured and read through afterwards.
    moved = {MIRPlace(CURRENT)} if operation == "move" else set()
    assert {c.holder for c in result.conflicts} == (set() if safe else {holder}) | moved
    assert all(c.point.block == LOOP for c in result.conflicts)

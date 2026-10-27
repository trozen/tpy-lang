"""Repeated record writes retain activation and surviving-alias distinctions."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import validate_function as validate_thir
from .definitions import MIRDefinitions
from .testutil import lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRCopy, MIRFunction, MIRGoto, MIRMove, MIRNotCovered, MIRPlace, MIRSlot,
    MIRSlotKind, MIRStorageWrite, MIRStorageWriteMode, MIRStorageDuration, MIRConstruct,
)
from .shapes import MIRDeref
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_retention import BUILT, CELL, CURRENT, INITIAL, LAYOUT, LOOP, N, analyze, loop_function
from .test_scope_inspection import scoped_loop
from .testutil import compiled_definitions, construction, execute, owned_shape, prepared
from .value import MIRValidationError
from .prepare import validate_function


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
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def source(compiled) -> tuple[th.THIRFunction, MIRDefinitions]:
    compiler, modules = compiled
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "fresh")
        return fn, compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))


def transferred(source: tuple[th.THIRFunction, MIRDefinitions], move: bool,
                *, inner_source: bool = False, readonly: bool = False) -> th.THIRFunction:
    fn, _ = source
    original, result, second, loop, ret = fn.body
    # The const verdict is the binding's; the node spells it too.
    original = replace(original, is_const=readonly, binding=replace(original.binding, const=readonly),
                       owned_storage=replace(original.owned_storage, readonly=readonly))
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
    if move and not inner:
        # Moving the one source built before the loop again on each pass reads it after its move.
        result = lower_function(transferred(source, move, readonly=readonly), MIRBodyId("cyclic", "fresh"),
                                definitions=source[1])
        assert isinstance(result, MIRNotCovered) and result.reason == "read after move", result
        return
    fn = lower(transferred(source, move, inner_source=inner, readonly=readonly), source[1])
    writes = analyze_storage(prepared(fn)).writes.values()
    transfer, = (s for s in writes if isinstance(s.value, MIRMove if move else MIRCopy))
    assert transfer.storage_write.mode is MIRStorageWriteMode.INITIALIZE
    for run, stop, skip, early, copies in ((False, False, False, False, 0), (True, False, False, False, 2),
                                         (True, True, False, False, 1), (True, False, True, False, 2),
                                         (True, False, False, True, 1)):
        heap = {}
        assert execute(fn, run, stop, skip, early, heap=heap) == (3 if run else 0)
        values = [next(iter(fields.values())) for fields in heap.values()]
        assert values.count(9) == copies
        assert values.count(3) == (copies if inner else 1)
    assert inspect_scope_lifetimes(prepared(fn)).conflicts == ()


def test_a_loop_copy_is_scoped_whatever_thir_publishes(source: tuple[th.THIRFunction, MIRDefinitions]) -> None:
    # The copy a loop declaration owns is placed in its declaration's scope
    # by MIR's own rule: a dropped THIR placement is not read.
    fn = transferred(source, False)
    loop = fn.body[3]
    local = replace(loop.body[0], storage_placement=None)
    forged = replace(fn, body=(*fn.body[:3], replace(loop, body=(local, *loop.body[1:])), fn.body[-1]))
    result = lower_function(forged, MIRBodyId("cyclic", "missing"), definitions=source[1])
    assert result == lower_function(fn, MIRBodyId("cyclic", "missing"), definitions=source[1])
    assert isinstance(result, MIRFunction), result


def test_readonly_move_stays_uncovered(source: tuple[th.THIRFunction, MIRDefinitions]) -> None:
    fn = transferred(source, True, readonly=True)
    result = lower_function(fn, MIRBodyId("cyclic", "readonly"),
                            definitions=source[1])
    assert isinstance(result, MIRNotCovered) and result.reason == "move needs fixed movable owned local"


def fresh_source(fn: MIRFunction) -> tuple[MIRFunction, MIRAssign]:
    """`fn` with storage of the loop's iteration region a move takes, and
    the statement building it: each activation moves a record built in it
    (moving one built before the loop would read it after its move)."""
    region = next(b.region for b in fn.blocks if b.id == LOOP)
    fresh = MIRSlot(BUILT, owned_shape(CELL), MIRSlotKind.TEMPORARY, storage_duration=region, residence=region)
    build = MIRAssign(MIRPlace(BUILT), construction(LAYOUT, N),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
    return replace(fn, slots=(*(s for s in fn.slots if s.id != BUILT), fresh)), build


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("reseat_first", [False, True])
def test_previous_activation_alias_survives_copy_reconstruction(move: bool, shape: str, reseat_first: bool) -> None:
    fn, observed = scoped_loop(shape, reseat_first=reseat_first)
    fn, build = fresh_source(fn)
    block = fn.blocks[1]
    init = replace(block.statements[0], value=MIRMove(BUILT) if move else MIRCopy(MIRPlace(INITIAL)))
    built = (build, init) if move else (init,)
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(*built, *block.statements[1:])),
                             *fn.blocks[2:]))
    result = inspect_scope_lifetimes(prepared(fn))
    assert not isinstance(result.conflicts, MIRNotCovered)
    assert {c.holder for c in result.conflicts} == (set() if reseat_first else {observed})


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("fault,reason", [("duplicate", "repeated initialization within region activation"),
                                         ("activation", "repeated initialization within region activation"),
                                         ("body", "owning operation in cycle")])
def test_transfer_does_not_bypass_activation_rules(move: bool, fault: str, reason: str) -> None:
    fn, build = fresh_source(scoped_loop("record")[0])
    block = fn.blocks[1]
    # Distinct locations tell a refusal on the transfer's target from one on its source.
    build = replace(build, loc=SourceLocation(1, 4))
    init = replace(block.statements[0], value=MIRMove(BUILT) if move else MIRCopy(MIRPlace(INITIAL)),
                   loc=SourceLocation(2, 4))
    # A move takes a record built just before it, so each transfer's source is initialized.
    transfer = (build, init) if move else (init,)
    statements = (*transfer, *transfer, *block.statements[1:]) if fault == "duplicate" else (
        *transfer, *block.statements[1:])
    if fault == "body":
        statements = (*transfer[:-1], replace(init, storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),
                      *block.statements[1:])
        fn = replace(fn, slots=tuple(replace(s, storage_duration=MIRStorageDuration.BODY,
                                             residence=fn.blocks[0].region)
                                     if s.id == init.target.root else s for s in fn.slots))
    block = replace(block, statements=statements,
                    terminator=MIRGoto(block.id) if fault == "activation" else block.terminator)
    fn = replace(fn, blocks=(fn.blocks[0], block, *fn.blocks[2:]))
    with pytest.raises(MIRValidationError, match=reason) as raised:
        validate_function(fn)
    if fault != "body":
        # The transfer's own target is the place initialized twice, not its source.
        assert raised.value.loc == init.loc


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("operation", ["construct", "copy", "move"])
def test_cyclic_replacement_checks_retained_holder_leaves(shape: str, safe: bool, operation: str) -> None:
    fn, point, holder = loop_function(shape, safe=safe)
    block = fn.blocks[1]
    # Replace through current without the site's fill (the built record, its
    # wrap, the borrow and the reseat): prior aliases now overlap the write.
    build = point.index - 1
    write = replace(block.statements[build], target=MIRPlace(CURRENT, (MIRDeref(),)),
                    storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, CURRENT))
    if operation != "construct":
        write = replace(write, value=MIRCopy(MIRPlace(INITIAL)) if operation == "copy" else MIRMove(BUILT))
    # A move keeps the site's built record as its source.
    built = block.statements[build:build + 1] if operation == "move" else ()
    stmts = (*block.statements[:build], *built, write, *block.statements[point.index + 3:])
    capture = stmts[-1]
    value = (replace(capture.value, fields=(CURRENT, *capture.value.fields[1:]))
             if isinstance(capture.value, MIRConstruct)
             else replace(capture.value, source=CURRENT if isinstance(capture.value, MIRAlias) else MIRPlace(CURRENT)))
    stmts = (*stmts[:-1], replace(capture, value=value))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=stmts), *fn.blocks[2:]))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == (set() if safe else {holder})
    assert all(c.point.block == LOOP for c in result.conflicts)

"""A generator's body lowers from the frame and leaves its emission published."""

from dataclasses import replace

import pytest

from ..mir_diagnostics import CallSites
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, VOID
from .call_contract import MIRGlobalId
from .collect import MIRBodyVerdict, MIRStorageState, MIRVerdictStatus, enumerate_bodies
from .liveness import MIRPoint
from .place_text import MIRSpokenPlace, MIRStorageNames, spoken_place
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBranch, MIRConstant, MIRCopy, MIRFunction, MIRGoto,
    MIRIteratorInit, MIRPlace, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRSuspend,
)
from .prepare import MIRPrepared, validate_function
from .testutil import bind_parameters, execute, leaf_shape, prepared
from .validate import statement_may_raise, statement_read_places
from .value import MIRValidationError


def _verdicts(source: str, sites: list[CallSites] | None = None) -> dict[str, MIRBodyVerdict]:
    compiler, modules = _compile(source)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                                    compiler.thir_reject_by_node, program.workspace)
    if sites is not None:
        sites.append(CallSites(entry.ast, entry.analyzer))
    return {v.name: v for v in verdicts}


def _lowered(source: str, name: str) -> MIRFunction:
    verdict = _verdicts(source)[name]
    assert verdict.function is not None, verdict.describe()
    return verdict.function


def _suspensions(fn: MIRFunction) -> list[tuple[object, MIRSuspend]]:
    return [(block, stmt) for block in fn.blocks for stmt in block.statements if isinstance(stmt, MIRSuspend)]


WHILE = """\
from typing import Iterator
from tpy import int32

def squares(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i * i
        i += 1
"""


def test_frame_blocks_become_a_loop_with_a_suspension_and_a_resume_edge() -> None:
    fn = _lowered(WHILE, "squares")
    assert fn.kind is MIRBodyKind.FREE_FUNCTION and str(fn.return_type) == "None"
    (block, suspend), = _suspensions(fn)
    # The suspension is its block's last statement; the block goes on at the resume block.
    assert block.statements[-1] is suspend and isinstance(block.terminator, MIRGoto)
    # The resume block runs the statement after the `yield`: `i += 1`.
    blocks = {b.id: b for b in fn.blocks}
    resumed = blocks[block.terminator.target]
    assert any(s.loc is not None and s.loc.line == 8 for s in resumed.statements)
    # The loop's branch is the frame's Branch: one of its arms reaches the suspension.
    assert any(isinstance(b.terminator, MIRBranch) for b in fn.blocks)
    # Running the body yields every square: the frame's blocks in their order.
    yields: list[object] = []
    execute(fn, 4, yields=yields)
    assert yields == [0, 1, 4, 9]


def test_a_suspension_reads_its_value_and_is_an_exceptional_exit() -> None:
    fn = _lowered(WHILE, "squares")
    (_block, suspend), = _suspensions(fn)
    slots = {s.id: s for s in fn.slots}
    assert [p.root for p in statement_read_places(suspend)] == [suspend.value]
    assert statement_may_raise(suspend, slots, fn.record_layouts)
    assert fn.exceptional_exits
    # The analyses run over the suspension: the yielded value is live up to it.
    analysis = prepared(fn)
    assert isinstance(analysis, MIRPrepared)
    point = next(MIRPoint(b.id, len(b.statements) - 1) for b in fn.blocks
                 if b.statements and b.statements[-1] is suspend)
    assert suspend.value in analysis.liveness.points[point]


def test_frame_locals_are_declared_at_entry_unassigned() -> None:
    fn = _lowered(WHILE, "squares")
    local, = (s for s in fn.slots if s.kind is MIRSlotKind.LOCAL and s.name == "i")
    entry = next(b for b in fn.blocks if b.id == fn.entry)
    # Declared at the frame's entry with no write there: the leaf `i = 0` writes it first.
    assert all(not (isinstance(s, MIRAssign) and s.target.root == local.id) for s in entry.statements)
    first = [s for b in fn.blocks for s in b.statements
             if isinstance(s, MIRAssign) and s.target.root == local.id]
    assert first and first[0].loc.line == 5


def test_falling_off_and_a_bare_return_end_the_body_without_a_value() -> None:
    fn = _lowered("""\
from typing import Iterator
from tpy import int32

def early(n: int32) -> Iterator[int32]:
    yield n
    if n > 3:
        return
    yield n + 1
""", "early")
    returns = [b.terminator for b in fn.blocks if isinstance(b.terminator, MIRReturn)]
    assert len(returns) == 2 and all(r.value is None for r in returns)
    yields: list[object] = []
    execute(fn, 5, yields=yields)
    assert yields == [5]
    yields.clear()
    execute(fn, 1, yields=yields)
    assert yields == [1, 2]


def test_a_by_value_capture_of_a_view_copies_it_into_the_frame() -> None:
    fn = _lowered("""\
from typing import Iterator
from tpy import int32

def lengths(s: str) -> Iterator[int32]:
    yield len(s)
""", "lengths")
    parameter, = (s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    copy, = (s for s in fn.slots if s.kind is MIRSlotKind.LOCAL and s.name == "s")
    # The parameter keeps its caller's loan; the frame owns its copy for the body.
    assert not parameter.shape.owns_storage
    assert copy.shape.owns_storage and copy.storage_duration is MIRStorageDuration.BODY
    entry = next(b for b in fn.blocks if b.id == fn.entry)
    assert any(isinstance(s, MIRAssign) and s.target.root == copy.id and isinstance(s.value, MIRCopy)
               for s in entry.statements)


def test_unmodeled_frames_are_refused_by_feature() -> None:
    verdicts = _verdicts("""\
from typing import Iterator
from tpy import int32

counter: int32 = 0

def looped(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x

def guarded(n: int32) -> Iterator[int32]:
    try:
        yield n
    finally:
        print("done")

def global_reader() -> Iterator[int32]:
    yield counter

async def ready() -> int32:
    return 1

def total(xs: list[int32]) -> int32:
    return sum(x * 2 for x in xs)

class Res:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Res":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

def managed(n: int32) -> Iterator[int32]:
    with Res():
        yield n

def nested(n: int32) -> Iterator[int32]:
    def inner() -> int32:
        return n + 1
    yield inner()

def lent(xs: list[int32]) -> Iterator[list[int32]]:
    yield xs

def sizes(xs: list[int32]) -> Iterator[int32]:
    yield len(xs)

def matched(n: int32) -> Iterator[int32]:
    match n:
        case 1:
            yield 1
        case _:
            yield 2

def handle() -> Iterator[int32]:
    h = sizes([1, 2])
    yield next(h, 0)

def owned_last_use(t: str) -> Iterator[str]:
    s = t + "a"
    yield s
""")
    reasons = {name: v.reason for name, v in verdicts.items() if v.status is MIRVerdictStatus.UNCOVERED}
    # The cursor over the caller's list is open while the frame is suspended:
    # analyzed, but not proven until the caller's half checks the caller.
    held = "suspension: loan of caller storage held across a suspension"
    assert verdicts["looped"].status is MIRVerdictStatus.INCOMPLETE and verdicts["looped"].reason == held
    assert reasons["guarded"] == "try across a suspension"
    # A frame's leaves carry no global binding fact: the read is a name MIR has not bound.
    assert reasons["global_reader"] == "non-local name"
    assert reasons["ready"] == "async body"
    assert reasons["managed"] == "with across a suspension"
    assert reasons["matched"] == "match across a suspension"
    assert reasons["nested"] == "nested def"
    assert reasons["lent"] == "reference yield"
    # The frame holds the temporary argument the handle's frame borrows, across the `yield`.
    assert reasons["handle"] == "argument held across a suspension"
    # A last-use move reads as a copy only through its operand's inert-leaf
    # admission: an owned local is refused before it.
    assert reasons["owned_last_use"] == "unsupported hoisted value"
    genexpr, = (v for name, v in verdicts.items() if name.startswith("__genexpr"))
    assert genexpr.status is MIRVerdictStatus.INCOMPLETE and genexpr.reason == held


def test_a_generator_verdict_has_no_storage_facts_and_no_summary() -> None:
    verdict = _verdicts(WHILE)["squares"]
    assert verdict.status is MIRVerdictStatus.COVERED and verdict.summary is None
    assert verdict.storage.state is MIRStorageState.NO_FACTS
    assert verdict.analyses is not None and verdict.analyses.gaps == ()


def test_a_suspension_hands_over_only_a_local_inert_or_owned_leaf() -> None:
    body = MIRBodyId("test", "gen")
    local, flag, counter = (MIRSlotId(body, i) for i in range(3))
    resume_from, resume = (MIRBlockId(body, i) for i in range(2))
    slots = (MIRSlot(local, leaf_shape(INT32), MIRSlotKind.LOCAL, "x"),
             MIRSlot(flag, leaf_shape(BOOL), MIRSlotKind.PARAMETER, "flag"),
             MIRSlot(counter, leaf_shape(INT32), MIRSlotKind.GLOBAL, "counter", global_id=MIRGlobalId("test", "counter")))

    def suspending(value: MIRSlotId) -> MIRFunction:
        return bind_parameters(MIRFunction(body, VOID, slots, (
            MIRBlock(resume_from, (MIRAssign(MIRPlace(local), MIRConstant(1)), MIRSuspend(value)), MIRGoto(resume)),
            MIRBlock(resume, (), MIRReturn())), resume_from, exceptional_exits=True))

    validate_function(suspending(local))
    validate_function(suspending(flag))
    # A global's storage is not the frame's to hand over: the caller would receive a loan of it.
    with pytest.raises(MIRValidationError, match="suspension needs a local inert or owned leaf value"):
        validate_function(suspending(counter))
    # Abandonment leaves the body at the suspension: its exit fact is required.
    with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
        validate_function(replace(suspending(local), exceptional_exits=False))


def test_a_generator_expression_frame_spells_its_source_by_the_expression() -> None:
    sites: list[CallSites] = []
    verdicts = _verdicts("""\
from tpy import int32

def byte_sum(data: bytes) -> int:
    return sum(b - 96 for b in data)
""", sites)
    genexpr, = (v for name, v in verdicts.items() if name.startswith("__genexpr"))
    assert genexpr.status is MIRVerdictStatus.COVERED, genexpr.describe()
    fn = genexpr.function
    names = MIRStorageNames(fn)
    source = sites[0].source_text
    # The compiler-made source parameter and the frame's copy of it are the user's `data`.
    parameter, = (s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    copy, = (s for s in fn.slots if s.kind is MIRSlotKind.LOCAL and s.name == parameter.name)
    assert spoken_place(MIRPlace(parameter.id), names, source) == MIRSpokenPlace("data", "named")
    assert spoken_place(MIRPlace(copy.id), names, source) == MIRSpokenPlace("data", "named")
    # The loop's cursor walks it: "the iteration over 'data'", never `__src`.
    cursor, = (stmt.target.root for b in fn.blocks for stmt in b.statements
               if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRIteratorInit))
    assert spoken_place(MIRPlace(cursor), names, source) == MIRSpokenPlace("data", "iteration")


def test_a_loop_across_a_suspension_lowers_with_the_structured_cursor_operations() -> None:
    verdicts = _verdicts("""\
from typing import Iterator
from tpy import int32

def squares_plus(n: int32) -> Iterator[int32]:
    yield -1
    for i in range(n):
        yield i * i

def letters(s: str) -> Iterator[int32]:
    for c in s:
        yield 1
""")
    assert verdicts["squares_plus"].status is MIRVerdictStatus.COVERED
    yields: list[object] = []
    execute(verdicts["squares_plus"].function, 4, yields=yields)
    assert yields == [-1, 0, 1, 4, 9]
    # The cursor walks the frame's own copy of `s`: nothing of the caller's is held at the yield.
    letters = verdicts["letters"]
    assert letters.status is MIRVerdictStatus.COVERED and letters.analyses.suspensions == ()


def test_a_generator_expression_over_a_range_spells_its_bounds_by_their_expressions() -> None:
    sites: list[CallSites] = []
    verdicts = _verdicts("""\
from tpy import int32

def span_sum(a: int32, b: int32) -> int:
    return sum(x * x for x in range(a, b))
""", sites)
    genexpr, = (v for name, v in verdicts.items() if name.startswith("__genexpr"))
    assert genexpr.status is MIRVerdictStatus.COVERED, genexpr.describe()
    fn = genexpr.function
    names = MIRStorageNames(fn)
    source = sites[0].source_text
    # The compiler-made bound parameters are the user's `a` and `b`, in order.
    parameters = [s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert [spoken_place(MIRPlace(p.id), names, source) for p in parameters] == [
        MIRSpokenPlace("a", "named"), MIRSpokenPlace("b", "named")]


def test_a_generator_expression_over_a_range_spells_an_expression_bound_by_its_source_text() -> None:
    sites: list[CallSites] = []
    verdicts = _verdicts("""\
from tpy import int32

def span_sum(a: int32, xs: list[int32]) -> int:
    return sum(x * x for x in range(a + 1, len(xs)))
""", sites)
    genexpr, = (v for name, v in verdicts.items() if name.startswith("__genexpr"))
    assert genexpr.status is MIRVerdictStatus.COVERED, genexpr.describe()
    fn = genexpr.function
    names = MIRStorageNames(fn)
    parameters = [s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert [spoken_place(MIRPlace(p.id), names, sites[0].source_text) for p in parameters] == [
        MIRSpokenPlace("a + 1", "named"), MIRSpokenPlace("len(xs)", "named")]


def test_nested_frame_loops_rebinding_one_name_keep_the_outer_counter() -> None:
    verdicts = _verdicts("""\
from typing import Iterator
from tpy import int32

def nested(n: int32) -> Iterator[int32]:
    for i in range(n):
        for i in range(3):
            yield i
        yield i
""")
    # The frame steps a hidden counter per loop, so the inner rebinding of `i` never moves the
    # outer loop: CPython yields [0, 1, 2, 2, 0, 1, 2, 2] for n=2.
    assert verdicts["nested"].status is MIRVerdictStatus.COVERED, verdicts["nested"].describe()
    yields: list[object] = []
    execute(verdicts["nested"].function, 2, yields=yields)
    assert yields == [0, 1, 2, 2, 0, 1, 2, 2]

"""What a verdict, an annotation or a warning reports is picked by source
location, never by slot or block number: each pick below is fed its
candidates in an order that disagrees with the source, then reversed."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from ..parse import SourceLocation
from ..thir.testutil import _compile, _entry
from ..typesys import INT32, VoidType
from . import collect, storage_evidence
from .testutil import records_of
from .collect import enumerate_bodies, line_facts
from .emit import Fragment, RegionHandle, assemble
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRBorrow, MIRConstant, MIRGlobalId, MIRNotCovered, MIRPlace, MIRReturn,
    MIRSlotId, MIRSlotKind,
)
from ..thir.scalar_leaves import MIRClass
from .value import MIRShape, MIRValidationError
from .liveness import MIRPoint
from .storage_evidence import MIRStorageVerdict, analyze_return_escapes
from ..mir_diagnostics import argument_aliases

BODY = MIRBodyId("main", "f")
LEAF = MIRShape(INT32, MIRClass.LEAF)


def _gap(reason: str, line: int, slot: MIRSlotId | None = None) -> MIRNotCovered:
    return MIRNotCovered(BODY, "storage evidence", reason, SourceLocation(line, 4), slot)


def _first_gap(monkeypatch, gaps: tuple[MIRNotCovered, ...], function: object = None) -> str:
    bound = SimpleNamespace(requires_proof=True, verdict=MIRStorageVerdict.NOT_COVERED, gaps=(),
                            evidence=SimpleNamespace(gaps=gaps), function=function)
    monkeypatch.setattr(collect, "certify_thir_storage", lambda request: bound)
    source = SimpleNamespace(storage_facts=None, temp_plan=None)
    return collect.check_storage(source, BODY, MIRBodyKind.FREE_FUNCTION, None, {}).gap_reason


@pytest.mark.parametrize("flip", [False, True])
def test_the_first_storage_gap_is_the_first_in_the_source(monkeypatch, flip: bool) -> None:
    # The evidence walks slots in number order; the gap on line 3 is reported.
    gaps = (_gap("late", 7), _gap("early", 3))
    assert _first_gap(monkeypatch, gaps[::-1] if flip else gaps) == "early"


def _two_slots(name: str | None) -> tuple[SimpleNamespace, MIRSlotId, MIRSlotId]:
    """A body whose slot %0 first occurs on line 6 and %5 on line 3: the
    numbering and the source disagree."""
    low, high = MIRSlotId(BODY, 0), MIRSlotId(BODY, 5)
    block = SimpleNamespace(statements=(MIRAssign(MIRPlace(high), MIRConstant(1), SourceLocation(3, 4)),
                                        MIRAssign(MIRPlace(low), MIRConstant(2), SourceLocation(6, 4))),
                            terminator=MIRReturn())
    slots = tuple(SimpleNamespace(id=sid, name=name) for sid in (low, high))
    return SimpleNamespace(blocks=(block,), slots=slots), low, high


@pytest.mark.parametrize("name", [None, "x"])
@pytest.mark.parametrize("flip", [False, True])
def test_a_storage_gap_tie_breaks_on_where_its_slot_first_occurs(monkeypatch, flip: bool,
                                                                 name: str | None) -> None:
    # Two gaps on one line over two unnamed (or same-named) slots: the one
    # over the slot first occurring in the source is reported, whatever the
    # candidate order and the slot numbers.
    fn, low, high = _two_slots(name)
    gaps = (_gap("live record %0 has unknown stored loans", 9, low),
            _gap("live record %5 has unknown stored loans", 9, high))
    assert _first_gap(monkeypatch, gaps[::-1] if flip else gaps, fn) == "live record %5 has unknown stored loans"


@pytest.mark.parametrize("flip", [False, True])
def test_the_return_escape_refusal_is_at_the_first_return_in_the_source(monkeypatch, flip: bool) -> None:
    late, early = SourceLocation(9, 4), SourceLocation(5, 8)
    unknown = [late, early]
    monkeypatch.setattr(storage_evidence, "_prepare_function", lambda fn: None)
    monkeypatch.setattr(storage_evidence, "_return_escapes",
                        lambda prepared, dependencies: ((), unknown[::-1] if flip else unknown))
    fn = SimpleNamespace(id=BODY)
    found = analyze_return_escapes(fn, SimpleNamespace(function=fn))
    assert isinstance(found, MIRNotCovered) and found.loc == early


NAMING_SOURCE = """\
from tpy import int32

class P:
    def __init__(self, x: int32) -> None:
        self.x = x

def two_names(c: bool) -> int32:
    r = P(1)
    q = r
    if c:
        q = P(2)
    return q.x + r.x
"""


@pytest.fixture(scope="module")
def naming():
    compiler, modules = _compile(NAMING_SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                                    compiler.thir_reject_by_node, program.workspace)
        verdict = next(v for v in verdicts if v.name == "two_names")
        result = verdict
    return result


@pytest.mark.parametrize("flip", [False, True])
def test_unnamed_storage_takes_the_name_of_its_first_holder_in_the_source(naming, flip: bool) -> None:
    # The body as lowered: `r` borrows its storage on line 8 (bb0), `q` its
    # own on line 11 (bb1). Rebind `q`'s borrow to `r`'s storage at line 5,
    # so the storage has two named holders and the one later in block order
    # comes first in the source; the analyses stay those of the lowered body.
    fn = naming.function
    r_borrow = next(s for b in fn.blocks for s in b.statements
                    if isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow) and s.loc.line == 8)
    storage = r_borrow.value.source

    def rebound(stmt):
        if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRBorrow) and stmt.loc.line == 11:
            return replace(stmt, value=replace(stmt.value, source=storage), loc=SourceLocation(5, 4))
        return stmt

    blocks = tuple(replace(b, statements=tuple(map(rebound, b.statements))) for b in fn.blocks)
    found = line_facts(replace(naming, function=replace(fn, blocks=blocks[::-1] if flip else blocks)))
    # `r`'s referent on the return line is the storage, now spelled `q`.
    assert {ref for state in found.borrows[(12, "r")] for ref in state.referents} == {"q"}


ALIAS_SOURCE = '''\
from tpy import int32


class Two:
    a: str
    b: str

    def __init__(self, a: str, b: str) -> None:
        self.a = a
        self.b = b

    def set_both(self, x: str, y: str) -> int32:
        self.a = "1"
        self.b = "2"
        return len(x) + len(y)


def same(xs: list[str], v: str) -> int32:
    xs.append("x")
    return len(v)


def calls(names: list[str], t: Two) -> int32:
    first = same(names, names[0])
    second = t.set_both(t.a, t.b)
    return first + second


def group(left: Two, right: Two, c: bool) -> int32:
    # One call and parameter pair, two affected storages (`left.a`, `right.a`).
    pick = left
    if c:
        pick = right
    return pick.set_both(pick.a, "z")


def main() -> None:
    print(calls(["a", "b"], Two("p", "q")))


main()
'''


@pytest.fixture(scope="module")
def aliased():
    compiler, modules = _compile(ALIAS_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        result = {v.name: v for v in enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                                   compiler.thir_reject_by_node, mir.workspace)}
    return result


def _reported(verdict, reverse: bool) -> list[tuple[int, int, str | None, int, int]]:
    analyses = verdict.analyses
    classes = analyses.retention_classes
    conflicts = classes.argument_aliases[::-1] if reverse else classes.argument_aliases
    view = SimpleNamespace(retention_classes=replace(classes, argument_aliases=conflicts),
                           dependencies=analyses.dependencies)
    return [(a.loc.line, a.loc.column, a.storage, a.retained, a.written)
            for a in argument_aliases(verdict.function, view)]


@pytest.mark.parametrize("reverse", [False, True])
def test_argument_aliases_follow_the_source_whatever_the_conflict_order(aliased, reverse: bool) -> None:
    calls = aliased["calls"]
    assert len(calls.analyses.retention_classes.argument_aliases) == 3
    assert _reported(calls, reverse) == [(24, 12, "names", 1, 0), (25, 13, "t.a", 1, 0), (25, 13, "t.b", 2, 0)]


@pytest.mark.parametrize("reverse", [False, True])
def test_one_call_reports_one_storage_whatever_the_conflict_order(aliased, reverse: bool) -> None:
    group = aliased["group"]
    assert len(group.analyses.retention_classes.argument_aliases) == 2
    assert _reported(group, reverse) == [(34, 11, "left.a", 1, 0)]


# --- assembly ------------------------------------------------------------------------


def _build(body: Fragment):
    return assemble(body, void=True, return_type=VoidType(), kind=MIRBodyKind.FREE_FUNCTION,
                    callees=(), result=None, layouts=records_of())


def _unused(slots: tuple[tuple[str, SourceLocation | None], ...]):
    body = Fragment(body=BODY)
    for name, loc in slots:
        body.adopt(body.record(LEAF, MIRSlotKind.GLOBAL, name, global_id=MIRGlobalId("main", name), loc=loc))
    body.end(MIRReturn())
    return _build(body)


def test_slots_no_statement_names_number_by_declaration_site() -> None:
    # Created in either order, unused globals number by where they are
    # declared -- before their names -- and one with no declaration last.
    declared = (("b", SourceLocation(2, 4)), ("a", SourceLocation(5, 4)), ("_", None))
    forward, backward = _unused(declared), _unused(declared[::-1])
    assert forward == backward
    assert [(s.name, s.id.index) for s in forward.slots] == [("b", 0), ("a", 1), ("_", 2)]


def test_an_unresolvable_statement_handle_fails_assembly() -> None:
    body = Fragment(body=BODY)
    body.borrow_operation(object())
    slot = body.slot(LEAF)
    body.write(slot, MIRConstant(1), None)
    # The statement the operation names is replaced by an equal, distinct one.
    block = body.current
    block.statements[0] = replace(block.statements[0])
    body.end(MIRReturn())
    with pytest.raises(MIRValidationError, match="borrow operation statement is not in its block"):
        _build(body)


def test_an_unresolvable_region_handle_fails_assembly() -> None:
    body = Fragment(body=BODY)
    slot = body.slot(LEAF)
    # A region the body never opened.
    body.records[slot].residence = RegionHandle(body.region, None)
    body.write(slot, MIRConstant(1), None)
    body.end(MIRReturn())
    with pytest.raises(MIRValidationError, match="region handle names no region of the body"):
        _build(body)


def _leaf_fragment(parent: Fragment, value: int, loc: int) -> tuple[Fragment, MIRSlotId]:
    child = parent.fragment()
    slot = child.slot(LEAF)
    child.write(slot, MIRConstant(value), SourceLocation(loc, 4))
    return child, slot


@pytest.mark.parametrize("prefilled", [False, True])
def test_a_left_nested_chain_splices_its_statements_slots_and_operations(prefilled: bool) -> None:
    # c into b, b into a (non-root), a into the body: the body runs a's own
    # statement (when it has one), then b's, then c's, in that order.
    body = Fragment(body=BODY)
    a = body.fragment()
    expected = []
    if prefilled:
        own = a.slot(LEAF)
        a.borrow_operation("a")
        a.write(own, MIRConstant(0), SourceLocation(1, 4))
        expected.append(0)
    b, _ = _leaf_fragment(a, 1, 2)
    b.borrow_operation("b")
    c, _ = _leaf_fragment(b, 2, 3)
    b.splice(c)
    a.splice(b)
    body.splice(a)
    body.end(MIRReturn())
    fn = _build(body)
    block, = fn.blocks
    assert [s.value.value for s in block.statements] == [*expected, 1, 2]
    assert [s.id.index for s in fn.slots] == list(range(len(expected) + 2))
    # b's operation waited at b's head: it names c's statement, the one after b's.
    assert body.borrow_operations["b"] == (MIRPoint(block.id, len(expected) + 1),)
    if prefilled:
        assert body.borrow_operations["a"] == (MIRPoint(block.id, 0),)


def test_an_operation_pending_at_a_splice_names_the_spliced_head() -> None:
    body = Fragment(body=BODY)
    body.borrow_operation("waiting")
    child, _ = _leaf_fragment(body, 7, 4)
    body.splice(child)
    body.end(MIRReturn())
    fn = _build(body)
    block, = fn.blocks
    assert body.borrow_operations["waiting"] == (MIRPoint(block.id, 0),)
    assert block.statements[0].value == MIRConstant(7)

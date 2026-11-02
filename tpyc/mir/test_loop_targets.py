"""Loops over a leaf's buffer and loop variables that copy their element:
the leaf cursor's shape (`value.py`), its validation (`validate.py`), the
storage it depends on, the replacement that frees what it walks
(`witness.py`), and the remedy each warning offers."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from ..typesys import BIGINT as INT, BOOL, BYTES, BYTESVIEW, CHAR, INT32, STR, STRING, STRVIEW, UINT8
from . import witness as witness_module
from .collect import enumerate_body_sources, lower_and_analyze
from .nodes import MIRAssign, MIRCopy, MIRIteratorInit, MIRIteratorRead, MIRPlace, MIRSlotKind
from .prepare import validate_function
from .value import (
    MIRHolding, MIRRole, MIRValidationError, leaf_element, shape_problem, value_shape,
)
from .witness import MIREffect, decide

SOURCE = '''\
from tpy import BytesView, String, int32


def grow(s: str) -> None:
    t = s + "!"
    for c in t:
        t = t + "x"
    print(t)


def count(s: str) -> int32:
    n = 0
    for c in s:
        n += 1
    return n


def reseat(s: str, r: str) -> int32:
    t = s
    n = 0
    for c in t:
        t = r
        n += 1
    return n


def respell(s: str) -> None:
    for c in s:
        s = "Z"
    print(s)


def pop_keys(d: dict[str, int32]) -> None:
    for k in d:
        d.pop(k)


def keep_key(d: dict[str, int32]) -> str:
    last = ""
    for k in d:
        k = k + "?"
        last = k
    d.pop("a")
    return last


def ints(xs: list[int]) -> int:
    t: int = 0
    for y in xs:
        t = t + y
    return t


def sliced(items: list[int32]) -> None:
    for x in items[0:2]:
        items.append(x)


def spanned(items: list[int32]) -> None:
    sp = items[0:2]
    for x in sp:
        items.append(x)


def string_grow(w: String) -> None:
    for c in w:
        w = w + "x"
    print(w)


def string_count(w: String) -> int32:
    n = 0
    for c in w:
        n += 1
    return n


def view_grow(data: bytes) -> None:
    t = data + b"!"
    v: BytesView = t
    for x in v:
        t = t + b"x"
    print(t)


def view_count(v: BytesView) -> int32:
    n = 0
    for x in v:
        n += 1
    return n
'''


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, ctx


@pytest.fixture(scope="module")
def bodies(compiled):
    compiler, entry, ctx = compiled
    found = {}
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        for source in enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                             compiler.thir_reject_by_node):
            if source.refusal is None:
                fn, analyses = lower_and_analyze(source, mir.definitions, mir.workspace)
                found[source.name] = (fn, analyses)
    return found


@pytest.fixture(scope="module")
def warnings(compiled):
    compiler, entry, ctx = compiled
    before = list(entry.analyzer.diagnostics)
    try:
        return {(d.loc.line, d.message) for _, d in compiler.report_mir_warnings([(entry, ctx)], strict=True)}
    finally:
        entry.analyzer.diagnostics[:] = before


def _warned(bodies, name):
    fn, analyses = bodies[name]
    return [w for w in decide(analyses.prepared, analyses.hazards) if w.warns]


def _statements(fn, kind):
    return [s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and isinstance(s.value, kind)]


# --- value.py: the leaf cursor's shape -----------------------------------------

@pytest.mark.parametrize(("typ", "element"), [(STR, CHAR), (STRVIEW, CHAR), (STRING, CHAR), (BYTES, UINT8), (BYTESVIEW, UINT8)])
def test_a_leaf_cursor_is_memberless_and_yields_its_types_element(typ, element) -> None:
    cursor = value_shape(typ, access=True, role=MIRRole.CURSOR)
    assert cursor.holding is MIRHolding.CURSOR and cursor.leaf_cursor and cursor.members == ()
    assert cursor.element.type == element and cursor.element.is_scalar and cursor.value is None
    assert shape_problem(cursor, typ) is None
    # The leaf itself, held or lent, keeps no members and is no cursor.
    assert value_shape(typ, access=True, role=MIRRole.LOAN).members == ()
    assert leaf_element(typ) == element


def test_a_leaf_that_does_not_iterate_has_no_cursor() -> None:
    assert leaf_element(INT) is None and leaf_element(INT32) is None and leaf_element(BOOL) is None
    assert shape_problem(value_shape(INT, access=True, role=MIRRole.CURSOR), INT) == "invalid holding"


# --- lower.py: what each loop binds --------------------------------------------

def test_a_leaf_cursor_walks_the_borrow_of_the_leaf(bodies) -> None:
    fn, _ = bodies["count"]
    slots = {s.id: s for s in fn.slots}
    (init,) = _statements(fn, MIRIteratorInit)
    assert slots[init.target.root].shape.leaf_cursor
    assert slots[init.value.source].shape.leaf_borrow
    (read,) = _statements(fn, MIRIteratorRead)
    assert slots[read.target.root].shape.is_scalar and slots[read.target.root].type == CHAR


def test_a_copied_target_is_its_own_storage_copied_from_the_element_loan(bodies) -> None:
    fn, _ = bodies["keep_key"]
    slots = {s.id: s for s in fn.slots}
    (read,) = _statements(fn, MIRIteratorRead)
    assert slots[read.target.root].shape.owned_leaf_borrow
    copy = next(s for s in _statements(fn, MIRCopy) if slots[s.target.root].name == "k")
    target = slots[copy.target.root]
    assert target.kind is MIRSlotKind.LOCAL and target.shape.leaf_storage and target.storage_duration is not None
    assert copy.value.source.root == read.target.root and copy.value.may_raise


def test_a_const_typed_reference_target_is_a_loan_of_the_element(bodies) -> None:
    fn, _ = bodies["ints"]
    slots = {s.id: s for s in fn.slots}
    (read,) = _statements(fn, MIRIteratorRead)
    assert slots[read.target.root].name == "y" and slots[read.target.root].shape.owned_leaf_borrow


# --- validate.py ----------------------------------------------------------------

def _damaged(fn, damage):
    slots = list(fn.slots)
    (init,) = _statements(fn, MIRIteratorInit)
    index = next(i for i, s in enumerate(slots) if s.id == init.target.root)
    match damage:
        case "int_cursor":
            slots[index] = replace(slots[index], shape=replace(slots[index].shape, type=INT))
        case "container_source":
            source = next(i for i, s in enumerate(slots) if s.id == init.value.source)
            slots[source] = replace(slots[source], shape=replace(slots[source].shape, holding=MIRHolding.VALUE))
        case "aggregate_read":
            (read,) = _statements(fn, MIRIteratorRead)
            target = next(i for i, s in enumerate(slots) if s.id == read.target.root)
            slots[target] = replace(slots[target], shape=value_shape(STR, access=True, role=MIRRole.LOAN))
        case "mutable":
            slots[index] = replace(slots[index], shape=replace(slots[index].shape, readonly=False))
    return replace(fn, slots=tuple(slots))


def test_the_lowered_leaf_loop_validates(bodies) -> None:
    validate_function(bodies["count"][0])


@pytest.mark.parametrize("damage", ["int_cursor", "container_source", "aggregate_read", "mutable"])
def test_a_malformed_leaf_cursor_fails_closed(bodies, damage) -> None:
    with pytest.raises(MIRValidationError):
        validate_function(_damaged(bodies["count"][0], damage))


# --- dependencies and witness ----------------------------------------------------

def test_a_leaf_cursor_depends_on_the_leafs_whole_storage(bodies) -> None:
    fn, analyses = bodies["grow"]
    (init,) = _statements(fn, MIRIteratorInit)
    deps = analyses.prepared.dependencies
    point = next(p for p, state in deps.referents.items() if MIRPlace(init.target.root) in state)
    (referent,) = deps.referents[point][MIRPlace(init.target.root)]
    assert not referent.place.projections and fn.slots[referent.place.root.index].name == "t"


@pytest.mark.parametrize("name", ["grow", "respell", "string_grow", "view_grow"])
def test_replacing_the_walked_leaf_frees_what_the_cursor_walks(bodies, name) -> None:
    (warned,) = _warned(bodies, name)
    assert warned.effect is MIREffect.FREES
    fn, _ = bodies[name]
    assert witness_module.holder_views(warned.hazard.holder, {s.id: s for s in fn.slots})


@pytest.mark.parametrize("name", ["count", "reseat", "keep_key", "ints", "string_count", "view_count"])
def test_a_clean_neighbour_warns_nothing(bodies, name) -> None:
    assert _warned(bodies, name) == []


# --- the remedies ---------------------------------------------------------------

def test_each_loop_warning_offers_a_remedy_that_compiles(warnings) -> None:
    by_line = dict(warnings)
    assert by_line[7] == ("this assignment frees the old value of 't', but the iteration over 't' still walks it and "
                          "continues on line 6; make the change after the loop, or iterate over a copy under another "
                          "name")
    assert by_line[29].endswith("continues on line 28; make the change after the loop, or iterate over a copy under "
                                "another name")
    # A whole container is copied with `copy(...)`; a slice or a Span with `list(...)`.
    assert by_line[35].endswith("iterate over a copy ('copy(d)'), or make the changes after the loop")
    assert by_line[56] == ("this call may move the elements of 'items', but the iteration over 'items[0:2]' still "
                           "walks them and continues on line 55; iterate over a copy ('list(items[0:2])'), or make the "
                           "changes after the loop")
    assert by_line[62].endswith("the iteration over 'sp' still walks them and continues on line 61; "
                                "iterate over a copy ('list(sp)'), or make the changes after the loop")
    assert by_line[67].startswith("this assignment frees the old value of 'w', but the iteration over 'w'")
    assert by_line[82].startswith("this assignment frees the old value of 't', but the iteration over 'v'")
    assert sorted(by_line) == [7, 29, 35, 56, 62, 67, 82]

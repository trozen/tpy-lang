"""What a write stores in each sum payload it reaches (`reach.statement_stored`):
one rule for a local sum slot, a field and a callee; the callee's published
stored alternatives (`MIRCallSummary.stored`); and the effect the witness
reads off them at a held payload (`witness.replacement_effect`)."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from ..mir_diagnostics import warned_hazards
from ..thir.testutil import _compile, _entry
from .call_contract import MIRStoredAlternatives, summary_problem
from .collect import enumerate_body_sources, lower_and_analyze
from .nodes import MIRAssign, MIRCopy, MIRMove, MIRPlace, MIRPoint, MIRSlotId, MIRWrap, statement_call
from .place_text import MIRStorageNames
from .prepare import _stored_by_referent, anchor_storage
from .reach import (
    MIRReferent, MIRStorageEvent, MIRStorageEventKind, replacement_events, statement_stored, stored_selection,
    sum_payload_ends, union_outcomes,
)
from .shapes import MIRContainerElements, MIRUnionPayload
from .summaries import summarize_function
from .value import MIREffect, MIRStoredOutcome, MIRValidationError, payload_fate
from .witness import MIRHazard, MIRHazardKind, decide, replacement_effect


def _stores(*alternatives: int, vacates: bool = False) -> MIRStoredOutcome:
    return MIRStoredOutcome(frozenset(alternatives), vacates)


SOURCE = '''\
from tpy import int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    o: P | None

    def __init__(self, n: int32) -> None:
        self.o = None
        if n > 0:
            self.o = P(n)


class W:
    h: H

    def __init__(self, n: int32) -> None:
        self.h = H(n)


def clear(h: H) -> None:
    h.o = None


def refill(h: H) -> None:
    h.o = P(5)


def empty_refill(h: H) -> None:
    h.o = None
    h.o = P(1)


def nested(h: H) -> None:
    clear(h)


def replace_above(w: W) -> None:
    w.h = H(0)


def local_writes(src: H) -> None:
    o: P | None = P(1)
    o = None
    o = P(2)
    o = src.o


def field_writes(h: H, src: H) -> None:
    h.o = None
    h.o = P(2)
    h.o = src.o


def w_call(h: H) -> int32:
    if h.o is not None:
        r = h.o
        clear(h)
        return r.v
    return 0


def w_refill(h: H) -> int32:
    if h.o is not None:
        r = h.o
        refill(h)
        return r.v
    return 0


class W2:
    a: H
    b: H

    def __init__(self, n: int32) -> None:
        self.a = H(n)
        self.b = H(n)


def clear_one_refill_other(w: W2) -> None:
    clear(w.a)
    refill(w.b)


def both_nested(w: W2) -> None:
    clear_one_refill_other(w)


def local_refill(n: int32) -> int32:
    o: P | None = None
    i = 0
    total = 0
    while i < n:
        o = P(i)
        if o is not None:
            total += o.v
        i += 1
    return total


def w_above(w: W) -> int32:
    h = w.h
    if h.o is not None:
        r = h.o
        replace_above(w)
        return r.v
    return 0


def fill_then_replace(w: W) -> None:
    w.h.o = P(1)
    w.h = H(0)


def replace_then_set(w: W) -> None:
    w.h = H(0)
    w.h.o = P(1)


def w_replace_then_set(w: W) -> int32:
    r = P(0)
    if w.h.o is not None:
        r = w.h.o
    replace_then_set(w)
    return r.v
'''


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, ctx


@pytest.fixture(scope="module")
def program(compiled):
    compiler, entry, ctx = compiled
    bodies = {}
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        for source in enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                             compiler.thir_reject_by_node):
            if source.refusal is None:
                fn, analyses = lower_and_analyze(source, mir.definitions, mir.workspace)
                if analyses is not None:
                    bodies[source.name] = (fn, analyses)
        summaries = dict(mir.workspace.summaries)
    return bodies, summaries


def _stored_writes(bodies, name, written):
    """Each whole write of the sum rooted at the slot `written` names, in
    order: the alternatives it stores there."""
    fn, analyses = bodies[name]
    prepared = analyses.prepared
    slots = {s.id: s for s in fn.slots}
    found = []
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            point = MIRPoint(block.id, index)
            facts = prepared.presence.points.get(point)
            if (not isinstance(stmt, MIRAssign) or statement_call(stmt) is not None or facts is None
                    or slots[stmt.target.root].name != written):
                continue
            stored = statement_stored(stmt, (), (), slots, fn.record_layouts, dict(facts).get)
            if stmt.target in stored:
                found.append(stored[stmt.target])
    return found


def test_a_local_slot_and_a_field_store_by_one_rule(program) -> None:
    bodies, _ = program
    # `= None`, `= P(2)`, `= src.o` (its selection unproven): one answer per incoming selection, wherever written.
    expected = [_stores(0), _stores(1), None]
    assert _stored_writes(bodies, "field_writes", "h") == expected
    # The local's declaration stores its payload first.
    assert _stored_writes(bodies, "local_writes", "o") == [_stores(1), *expected]


def _stored(summaries, name):
    result = next(r for identity, r in summaries.items() if identity.name == name and identity.owner is None)
    return {(e.parameter, tuple(getattr(step, "name", step) for step in e.path)): e.outcome
            for e in result.summary.stored}


def test_a_summary_publishes_the_union_of_what_its_writes_store(program) -> None:
    _, summaries = program
    assert _stored(summaries, "clear") == {(0, ("o",)): _stores(0)}
    assert _stored(summaries, "refill") == {(0, ("o",)): _stores(1)}
    # Emptied then refilled: either, never a survival.
    assert _stored(summaries, "empty_refill") == {(0, ("o",)): _stores(0, 1)}
    # A nested call's entry is the caller's own, at its path.
    assert _stored(summaries, "nested") == {(0, ("o",)): _stores(0)}
    # A containing record written by a call's result: the sum under it is unknown.
    assert _stored(summaries, "replace_above") == {(0, ("h", "o")): None}
    # The sum refilled, then the record holding it replaced: the covering write
    # states it unknown, so the refill's survival is not published alone.
    assert _stored(summaries, "fill_then_replace") == {(0, ("h", "o")): None}


def test_a_sum_under_an_emptied_outer_payload_is_vacated(program) -> None:
    bodies, _ = program
    fn, _ = bodies["clear"]
    (o,) = {stmt.target.projections[-1] for block in fn.blocks for stmt in block.statements
            if isinstance(stmt, MIRAssign) and stmt.target.projections}
    # `w.h = None` of a nested `H | None` (a shape MIR does not admit as a field yet): the outer
    # sum stores its empty alternative, the inner `w.h.o` under the payload it does not select
    # is left no payload at all, while a refill of the outer payload leaves the inner one unknown.
    empty = MIRWrap(0, None)
    assert stored_selection(empty, (), lambda place: None) == _stores(0)
    assert stored_selection(empty, (MIRUnionPayload(1), o), lambda place: None) == _stores(vacates=True)
    assert stored_selection(MIRWrap(1, MIRPlace(fn.slots[0].id)), (MIRUnionPayload(1), o),
                            lambda place: None) is None


def test_stored_alternatives_stay_outside_write_identity(program) -> None:
    _, summaries = program
    clear = next(r for identity, r in summaries.items() if identity.name == "clear").summary
    refill = next(r for identity, r in summaries.items() if identity.name == "refill").summary
    assert clear.writes == refill.writes and clear.stored != refill.stored
    assert all(isinstance(e, MIRStoredAlternatives) for e in clear.stored)


@pytest.mark.parametrize("name, effect, sum_steps", [
    ("w_call", MIREffect.FREES, ()),
    ("w_refill", MIREffect.OVERWRITES, ()),
    ("w_above", MIREffect.UNKNOWN, ("o",)),
])
def test_a_held_payload_s_fate_decides_the_effect(program, name, effect, sum_steps) -> None:
    bodies, _ = program
    fn, analyses = bodies[name]
    warned = [w for w in decide(analyses.prepared, analyses.hazards) if w.warns]
    assert [w.effect for w in warned] == [effect]
    (witness,) = warned
    # The named sum lies at or under the hazard's anchor, by the steps the payload sits below.
    steps = witness.sum.projections[len(witness.hazard.replacement.anchor.projections):]
    assert tuple(getattr(step, "id", step).name for step in steps) == sum_steps
    assert not any(isinstance(step, MIRUnionPayload) for step in steps)


def test_a_callee_replacing_the_record_then_setting_its_sum(program) -> None:
    bodies, summaries = program
    # `w.h = H(0); w.h.o = P(1)`: the covering record write states the sum unknown, so the later
    # refill's survival is not published alone.
    assert _stored(summaries, "replace_then_set") == {(0, ("h", "o")): None}
    # The caller holding `w.h.o`'s payload across the call: one warning, free or overwrite, at the sum.
    fn, analyses = bodies["w_replace_then_set"]
    (witness,) = warned_hazards(analyses)
    assert witness.effect is MIREffect.UNKNOWN
    assert tuple(step.id.name for step in witness.sum.projections if hasattr(step, "id")) == ("h", "o")


def test_two_same_typed_sums_under_one_record_stay_two_entries(program) -> None:
    _, summaries = program
    # Per path, never per type: through a nested call, `w.a.o` emptied and `w.b.o` refilled.
    expected = {(0, ("a", "o")): _stores(0), (0, ("b", "o")): _stores(1)}
    assert _stored(summaries, "clear_one_refill_other") == expected
    assert _stored(summaries, "both_nested") == expected


def test_a_local_sum_refill_states_its_payload_as_a_field_write_does(program) -> None:
    bodies, _ = program
    fn, analyses = bodies["local_refill"]
    slots = {s.id: s for s in fn.slots}
    kills = analyses.prepared.kills.replacements
    # The in-place fill of the hoisted local's owned sum replaces its payload (`replacement_events`):
    # its sums are stated below the sum written, by the storage it is, as a field's are.
    found = [r for point, replaced in kills.items() for r in replaced if slots[r.place.root].shape.owned_sum]
    assert found and all(r.place.projections == (MIRUnionPayload(1),) and r.anchor == MIRPlace(r.place.root)
                         and r.stored == ((MIRReferent(r.anchor), _stores(1)),) for r in found)


def _field_hazard(bodies):
    fn, analyses = bodies["w_call"]
    (hazard,) = (h for h in analyses.hazards if h.kind is MIRHazardKind.REPLACEMENT)
    return fn, hazard


@pytest.mark.parametrize("stored, effect", [
    (_stores(0), MIREffect.FREES),
    (_stores(1), MIREffect.OVERWRITES),
    (_stores(vacates=True), MIREffect.FREES),
    (_stores(1, vacates=True), MIREffect.UNKNOWN),
    (_stores(0, 1), MIREffect.UNKNOWN),
    (None, MIREffect.UNKNOWN),
])
def test_a_local_slot_and_a_field_take_one_effect_per_outcome(program, stored, effect) -> None:
    bodies, _ = program
    fn, field = _field_hazard(bodies)
    slots = {s.id: s for s in fn.slots}
    (written,) = field.written
    # The field write `h.o` holding its payload, and a local sum's whole write, which replaces
    # the payload place itself below the sum it writes: one answer for each stored outcome.
    sum_place = MIRPlace(field.holder.root)
    payload = MIRPlace(field.holder.root, (MIRUnionPayload(1),))
    local = replace(field, retained=(frozenset({MIRReferent(payload)}),),
                    replacement=replace(field.replacement, place=payload, anchor=sum_place,
                                        storage=frozenset({MIRReferent(payload)}),
                                        stored=((MIRReferent(sum_place), stored),)))
    for hazard in (replace(field, replacement=replace(field.replacement, stored=((written, stored),))), local):
        assert replacement_effect(hazard, slots, fn.record_layouts)[0] is effect


def _under(written: MIRReferent, *steps) -> MIRReferent:
    return MIRReferent(MIRPlace(written.place.root, (*written.place.projections, *steps)), external=written.external)


@pytest.mark.parametrize("inner, effect, crossed", [
    # A deeper end wins over an outer unknown.
    (_stores(0), MIREffect.FREES, "inner"),
    # An outer unknown over an inner survival stays unknown, named at the outer sum.
    (_stores(1), MIREffect.UNKNOWN, "outer"),
])
def test_nested_payload_fates(program, inner, effect, crossed) -> None:
    bodies, _ = program
    fn, field = _field_hazard(bodies)
    slots = {s.id: s for s in fn.slots}
    (written,) = field.written
    member = written.place.projections[-1]
    # A holder under two payloads, the outer sum's fate unknown.
    steps = (MIRUnionPayload(1), member)
    held = _under(written, *steps, MIRUnionPayload(1))
    hazard = replace(field, retained=(frozenset({held}),), replacement=replace(
        field.replacement, stored=((written, None), (_under(written, *steps), inner))))
    assert replacement_effect(hazard, slots, fn.record_layouts) == (effect, steps if crossed == "inner" else ())


def test_the_anchor_is_the_replaced_place_never_its_storage(program) -> None:
    bodies, _ = program
    fn, field = _field_hazard(bodies)
    slots = {s.id: s for s in fn.slots}
    (written,) = field.written
    member = written.place.projections[-1]
    # A record replaced in place whose storage is a payload (`(*x)` holding `h.o`'s payload):
    # only the sums below that storage are the write's, and the one there survives.
    storage = _under(written, MIRUnionPayload(1))
    held = _under(storage, member, MIRUnionPayload(1))
    hazard = replace(field, retained=(frozenset({held}),),
                     replacement=replace(field.replacement, storage=frozenset({storage}),
                                         stored=((_under(storage, member), _stores(1)),)))
    assert replacement_effect(hazard, slots, fn.record_layouts) == (MIREffect.OVERWRITES, (member,))


def test_an_emptying_outcome_survives_the_union() -> None:
    # A move out (or an unselected outer payload) unioned with a refill is either, never a survival.
    stored = union_outcomes(_stores(vacates=True), _stores(1))
    assert payload_fate(stored, 1) is MIREffect.UNKNOWN
    assert payload_fate(_stores(vacates=True), 1) is MIREffect.FREES


def test_a_move_from_an_owned_local_sum_leaves_no_payload(program) -> None:
    bodies, _ = program
    fn, _ = bodies["local_refill"]
    slots = {s.id: s for s in fn.slots}
    (backing,) = (s.id for s in fn.slots if s.shape.owned_sum)
    (target,) = (s.id for s in fn.slots if s.name == "o")
    move = MIRAssign(MIRPlace(target), MIRMove(backing))
    stored = statement_stored(move, (), (), slots, fn.record_layouts, lambda place: None)
    assert stored[MIRPlace(backing)] == _stores(vacates=True)
    # A wrap of the other alternative leaves the sum under the payload it replaces no payload.
    fill = MIRAssign(MIRPlace(backing), MIRWrap(0, None))
    assert statement_stored(fill, (), (), slots, fn.record_layouts, lambda place: None) == {
        MIRPlace(backing): _stores(0)}


def test_a_forged_stored_entry_is_refused(program) -> None:
    bodies, summaries = program
    summary = next(r for identity, r in summaries.items() if identity.name == "clear").summary
    layouts = bodies["clear"][0].record_layouts
    (entry,) = summary.stored
    assert summary_problem(summary, layouts) is None
    # A parameter out of range, a path under no write, an alternative the sum lacks, a non-index,
    # an outcome that stores nothing and vacates nothing, an outcome of another type.
    for forged in (MIRStoredAlternatives(100, (), _stores(0)),
                   MIRStoredAlternatives(0, (), _stores(0)),
                   replace(entry, outcome=_stores(2)),
                   replace(entry, outcome=MIRStoredOutcome(frozenset({"1"}))),
                   replace(entry, outcome=MIRStoredOutcome()),
                   replace(entry, outcome=frozenset({0}))):
        assert summary_problem(replace(summary, stored=frozenset({forged})), layouts) is not None
    # Two entries for one sum.
    assert summary_problem(replace(summary, stored=frozenset({entry, replace(entry, outcome=_stores(1))})),
                           layouts) is not None
    emptied = replace(entry, outcome=_stores(1, vacates=True))
    assert summary_problem(replace(summary, stored=frozenset({emptied})), layouts) is None
    # A path under a write that ends at a record, not a sum.
    above = next(r for identity, r in summaries.items() if identity.name == "replace_above").summary
    (write,) = above.writes
    record = MIRStoredAlternatives(write.parameter, write.path, _stores(0))
    assert summary_problem(above, layouts) is None
    assert summary_problem(replace(above, stored=frozenset({record})), layouts) is not None


def _first_field_write(bodies, name):
    """The body's first write of a sum field, its point and the state there."""
    fn, analyses = bodies[name]
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRAssign) and stmt.target.projections and statement_call(stmt) is None:
                point = MIRPoint(block.id, index)
                return fn, stmt, analyses.prepared.dependencies.referents[point]
    raise AssertionError(f"no field write in {name}")


@pytest.mark.parametrize("sibling", ["unresolved", "empty"])
@pytest.mark.parametrize("known_first", [True, False])
def test_a_sum_place_without_storage_leaves_every_sum_unknown(program, sibling, known_first) -> None:
    bodies, _ = program
    fn, stmt, state = _first_field_write(bodies, "field_writes")
    slots = {s.id: s for s in fn.slots}
    known = stmt.target
    assert _stored_by_referent({known: _stores(0)}, state, slots, fn.record_layouts)
    src = next(s.id for s in fn.slots if s.name == "src")
    if sibling == "unresolved":
        # A step the record does not have: the place resolves to no storage at all.
        other, at = MIRPlace(src, (MIRContainerElements(),)), state
    else:
        # A parameter whose referents the state does not hold: it reaches no storage.
        other = MIRPlace(src, known.projections)
        at = {place: refs for place, refs in state.items() if place.root != src}
    order = [(known, _stores(0)), (other, _stores(1))]
    stored = dict(order if known_first else reversed(order))
    # The resolvable sibling is not stated alone: the statement states nothing.
    assert _stored_by_referent(stored, at, slots, fn.record_layouts) == ()


def test_storage_off_the_replaced_place_has_no_anchor_storage(program) -> None:
    bodies, _ = program
    fn, field = _field_hazard(bodies)
    (written,) = field.written
    payload = MIRPlace(written.place.root, (*written.place.projections, MIRUnionPayload(1)))
    # A payload replaced below its sum: storage that ends in the payload step stands for the
    # sum one step up; storage that does not (the sum itself) has no anchor storage.
    stand_in = _under(written, MIRUnionPayload(1))
    assert dict(anchor_storage(payload, written.place, frozenset({stand_in, written}))) == {
        stand_in: written, written: None}


def test_a_hazard_without_anchor_storage_is_unknown(program) -> None:
    bodies, _ = program
    fn, field = _field_hazard(bodies)
    slots = {s.id: s for s in fn.slots}
    (written,) = field.written
    # A refill the holder's payload survives, but no storage stands at the anchor to key it by.
    survives = replace(field, replacement=replace(field.replacement, stored=((written, _stores(1)),)))
    assert replacement_effect(survives, slots, fn.record_layouts)[0] is MIREffect.OVERWRITES
    # Replaced below the written storage, which does not end in those steps.
    unanchored = replace(survives, replacement=replace(survives.replacement, place=_under(written, MIRUnionPayload(1)).place,
                                                       anchor=written.place))
    assert replacement_effect(unanchored, slots, fn.record_layouts) == (MIREffect.UNKNOWN, ())


def _unanchored(replacement):
    """The replacement stated one payload step below its place: its storage does
    not end in that step, so no storage stands at the anchor for it."""
    place = replacement.place
    return replace(replacement, place=MIRPlace(place.root, (*place.projections, MIRUnionPayload(1))), anchor=place)


def test_a_summary_without_anchor_storage_publishes_unknown(compiled) -> None:
    compiler, entry, ctx = compiled
    declaration = next(fn for node, fn in ctx.thir_functions.items() if node.name == "clear")
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        source = next(s for s in enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                                        compiler.thir_reject_by_node) if s.name == "clear")
        fn, analyses = lower_and_analyze(source, mir.definitions, mir.workspace)
        prepared = analyses.prepared
        unanchored = MappingProxyType({point: tuple(_unanchored(r) for r in found)
                                       for point, found in prepared.kills.replacements.items()})
        doctored = replace(prepared, kills=replace(prepared.kills, replacements=unanchored))

        def stored(at):
            result = summarize_function(declaration, fn, at, mir.definitions)
            return {(e.parameter, tuple(step.name for step in e.path)): e.outcome for e in result.summary.stored}

        assert stored(prepared) == {(0, ("o",)): _stores(0)}
        # No storage stands at the anchor, so the write states no sum below it: 'o' is unknown,
        # never a survival.
        assert stored(doctored) == {(0, ("o",)): None}


def test_a_copy_into_storage_of_its_own_keeps_its_own_name(program) -> None:
    bodies, _ = program
    fn, _ = bodies["local_refill"]
    (backing,) = (s for s in fn.slots if s.shape.owned_sum)
    names = MIRStorageNames(fn)
    assert names.storage[MIRPlace(backing.id)] == "o"
    # A named local owning a sum of its own, copied from the backing before the pointer-form
    # holder binds it: it names its own storage only, never the backing.
    owner = replace(backing, id=MIRSlotId(backing.id.body, len(fn.slots)), name="q")
    entry = fn.blocks[0]
    first = next(stmt.loc for stmt in entry.statements if stmt.loc is not None)
    copied = MIRAssign(MIRPlace(owner.id), MIRCopy(MIRPlace(backing.id)), loc=first)
    fn2 = replace(fn, slots=(*fn.slots, owner),
                  blocks=(replace(entry, statements=(copied, *entry.statements)), *fn.blocks[1:]))
    names = MIRStorageNames(fn2)
    assert names.storage[MIRPlace(backing.id)] == "o"
    assert names.storage[MIRPlace(owner.id)] == "q"


def test_one_unanchored_replacement_leaves_the_summed_entry_unknown(compiled) -> None:
    compiler, entry, ctx = compiled
    declaration = next(fn for node, fn in ctx.thir_functions.items() if node.name == "empty_refill")
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        source = next(s for s in enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                                        compiler.thir_reject_by_node) if s.name == "empty_refill")
        fn, analyses = lower_and_analyze(source, mir.definitions, mir.workspace)
        prepared = analyses.prepared
        points = [point for point, found in prepared.kills.replacements.items() if found]
        assert len(points) == 2
        for dropped in points:
            unanchored = MappingProxyType({
                point: tuple(_unanchored(r) if point == dropped else r for r in found)
                for point, found in prepared.kills.replacements.items()})
            doctored = replace(prepared, kills=replace(prepared.kills, replacements=unanchored))
            result = summarize_function(declaration, fn, doctored, mir.definitions)
            published = {(e.parameter, tuple(step.name for step in e.path)): e.outcome
                         for e in result.summary.stored}
            # Either write unstated makes the union unknown: the other write's known
            # outcome (emptied, or refilled) is never published as the sum's alone.
            assert published == {(0, ("o",)): None}
        (intact,) = summarize_function(declaration, fn, prepared, mir.definitions).summary.stored
        assert intact.outcome == _stores(0, 1)


def test_a_copy_onto_itself_replaces_as_any_copy_does(program) -> None:
    bodies, _ = program
    fn, stmt, _ = _first_field_write(bodies, "field_writes")
    slots = {s.id: s for s in fn.slots}
    (backing,) = (s.id for s in bodies["local_refill"][0].slots if s.shape.owned_sum)
    local_slots = {s.id: s for s in bodies["local_refill"][0].slots}
    # Equal places need not be one object (`xs[i] = xs[j]`), and an explicit copy() is materialized
    # before it is assigned: a field and a local sum written with their own value replace their
    # storage as any other write does (BUGS.md#opt-field-self-copy-false-warnings).
    for written, at in ((stmt.target, slots), (MIRPlace(backing), local_slots)):
        self_copy = replace(stmt, target=written, value=MIRCopy(written))
        found = replacement_events(self_copy, (), lambda place: None, at)
        assert found and found == replacement_events(replace(stmt, target=written), (), lambda place: None, at)


def test_a_scalar_payload_ends_unless_its_fate_is_an_overwrite(program) -> None:
    bodies, _ = program
    # No covered holder reaches a scalar payload of a whole-written local sum yet (`P | int32`
    # and `int32 | None` locals are uncovered), so the event is built: two scalar payloads.
    fn, _ = bodies["local_refill"]
    (backing,) = (s.id for s in fn.slots if s.shape.owned_sum)
    sum_place = MIRPlace(backing)
    event = MIRStorageEvent(MIRStorageEventKind.REPLACE, sum_place, payloads=((0, False), (1, False)))
    payloads = [MIRPlace(backing, (MIRUnionPayload(a),)) for a in (0, 1)]
    call_fn, _ = bodies["w_call"]
    (call,) = (c for block in call_fn.blocks for stmt in block.statements if (c := statement_call(stmt)) is not None)

    def ended(value, old):
        return [place for place, owns in sum_payload_ends(event, value, lambda place: old) if not owns]

    # A call's result or a move states no selection, so its fate is unknown and the payload ends.
    assert ended(call, None) == payloads
    assert ended(MIRMove(backing), frozenset({1})) == payloads[1:]
    # A copy of the sum onto itself selects the alternative it already holds: it ends nothing.
    assert ended(MIRCopy(sum_place), None) == []
    # An element place is any element, so a copy onto an equal one (`xs[i] = xs[j]`) may hold
    # another element's alternative: its scalar payload ends.
    element = MIRPlace(backing, (MIRContainerElements(),))
    element_event = MIRStorageEvent(MIRStorageEventKind.REPLACE, element, payloads=((1, False),))
    assert [place for place, owns in sum_payload_ends(element_event, MIRCopy(element), lambda place: None)
            if not owns] == [MIRPlace(backing, (MIRContainerElements(), MIRUnionPayload(1)))]
    for alternative, other in ((0, 1), (1, 0)):
        # A refill of the same alternative overwrites the payload in place; another one ends it.
        assert ended(MIRWrap(alternative, None), frozenset({alternative})) == []
        assert ended(MIRWrap(other, None), frozenset({alternative})) == [payloads[alternative]]


def test_a_replacement_hazard_carries_its_replacement(program) -> None:
    bodies, _ = program
    _, field = _field_hazard(bodies)
    # Its target and written storage are its replacement's, never stated beside it.
    assert (field.target, field.written) == (field.replacement.place, field.replacement.storage)
    with pytest.raises(MIRValidationError, match="carries its replacement"):
        MIRHazard(MIRHazardKind.REPLACEMENT, field.site, field.target, field.written, field.holder)
    with pytest.raises(MIRValidationError, match="carries its replacement"):
        MIRHazard(MIRHazardKind.SCOPE_END, field.site, None, None, field.holder, replacement=field.replacement)
    with pytest.raises(MIRValidationError, match="target and written storage once"):
        replace(field, ended=field.target)

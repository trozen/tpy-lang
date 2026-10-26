"""The one reach rule (`reach.reaches`), its two distinct-origin oracles,
the one statement storage classifier and the presence field kill over
them, on hand-built MIR."""

from dataclasses import replace

import pytest

from ..typesys import INT32, STR, OptionalType, TupleType
from .dependencies import _leaves, resolve_referents
from .liveness import MIRPoint
from .nodes import (
    MIRAssign, MIRBlockId, MIRBodyId, MIRConstant, MIRMove, MIRPlace, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration, MIRStorageInit, MIRStorageWrite, MIRStorageWriteMode,
    MIRConstruct, MIRWrap,
)
from .presence import _State, _kill_field_facts
from .prepare import _resolver
from .reach import (
    MIRReferent, MIRStorageEvent, MIRStorageEventKind, declared_containment, origin, reaches,
    replacement_events, storage_events, storage_exclusions,
)
from .shapes import MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload
from .testutil import borrowed_shape, hand_record, leaf_shape, optional_shape, owned_shape, records_of, tuple_shape
from .value import MIRField, MIRFieldId, MIRRecordLayout

P = hand_record("P", "reach.P", x=INT32)
PS = hand_record("PS", "reach.PS", name=STR)
H = hand_record("H", "reach.H", p=OptionalType(P), q=P, s=STR, n=INT32, o=OptionalType(PS))
SUB = hand_record("Sub", "reach.Sub", H, extra=OptionalType(P))
X = MIRField(MIRFieldId(P, "x"), INT32)
HP = MIRField(MIRFieldId(H, "p"), OptionalType(P))
HQ = MIRField(MIRFieldId(H, "q"), P)
HS = MIRField(MIRFieldId(H, "s"), STR)
HO = MIRField(MIRFieldId(H, "o"), OptionalType(PS))
NAME = MIRField(MIRFieldId(PS, "name"), STR)
EXTRA = MIRField(MIRFieldId(SUB, "extra"), OptionalType(P))
BODY = MIRBodyId("reach", "example")
A, B, OWN, TUP, GLOBAL, BASE, SUBP, SUM, BUILT = (MIRSlotId(BODY, i) for i in range(9))
SLOTS = {s.id: s for s in (
    MIRSlot(A, borrowed_shape(H), MIRSlotKind.PARAMETER),
    MIRSlot(B, borrowed_shape(H), MIRSlotKind.PARAMETER),
    MIRSlot(OWN, owned_shape(H), MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),
    MIRSlot(SUM, optional_shape(OptionalType(H), owned_shape(H)), MIRSlotKind.LOCAL,
            storage_duration=MIRStorageDuration.BODY),
    MIRSlot(BUILT, owned_shape(H), MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY),
    MIRSlot(TUP, tuple_shape(TupleType((H, H)), (borrowed_shape(H), borrowed_shape(H))), MIRSlotKind.PARAMETER),
    MIRSlot(GLOBAL, owned_shape(H), MIRSlotKind.GLOBAL),
    MIRSlot(BASE, borrowed_shape(H), MIRSlotKind.PARAMETER),
    MIRSlot(SUBP, borrowed_shape(SUB), MIRSlotKind.PARAMETER),
)}


def _records():
    return records_of(MIRRecordLayout(P, (X,), True, True), MIRRecordLayout(PS, (NAME,), True, True),
                      MIRRecordLayout(H, (HP, HQ, HS, HO), True, True),
                      MIRRecordLayout(SUB, (HP, HQ, HS, EXTRA), True, True, ancestors=(H,)))


def _any(written: MIRReferent, target: MIRReferent) -> bool:
    return True


def _none(written: MIRReferent, target: MIRReferent) -> bool:
    return False


def _any_selection(place: MIRPlace) -> None:
    return None


def ref(root: MIRSlotId, *path: object, external: bool = True, **flags: bool) -> MIRReferent:
    return MIRReferent(MIRPlace(root, path), external, **flags)


# --- the reach contract -----------------------------------------------------------------

def test_static_storage_is_never_reached_and_never_reaches() -> None:
    assert not reaches(ref(A), ref(A, HP, static=True), _any)
    assert not reaches(ref(A, HP, static=True), ref(A), _any)


def test_the_bodys_own_storage_is_never_the_callers() -> None:
    assert not reaches(ref(OWN, external=False), ref(A), _any)
    assert not reaches(ref(A), ref(OWN, HP, external=False), _any)


def test_a_held_loan_reaches_and_is_reached_by_any_external_storage() -> None:
    held = ref(A, HS, held=True)
    assert reaches(ref(B, HQ), held, _none) and reaches(held, ref(B, HQ), _none)
    assert not reaches(ref(OWN, external=False), held, _any)


@pytest.mark.parametrize("external", [False, True])
def test_one_origin_reaches_what_lies_under_the_replaced_path(external: bool) -> None:
    root = OWN if not external else A
    whole, field = ref(root, external=external), ref(root, HP, external=external)
    # A whole write reaches everything under its place, never the storage around it.
    assert reaches(whole, field, _none) and not reaches(field, whole, _none)
    # A member field write reaches the field and what lies under it, never a sibling.
    assert reaches(field, ref(root, HP, MIRUnionPayload(1), X, external=external), _none)
    assert not reaches(field, ref(root, HQ, external=external), _none)
    # A write strictly inside the payload never reaches the wrapper around it.
    assert not reaches(ref(root, HP, MIRUnionPayload(1), external=external), field, _none)
    # A container's shape write reaches its elements region, never the container object.
    structure = ref(root, HQ, MIRContainerStructure(), external=external)
    assert reaches(structure, ref(root, HQ, MIRContainerElements(), external=external), _none)
    assert not reaches(structure, ref(root, HQ, external=external), _none)
    assert not reaches(ref(root, HQ, MIRContainerElements(), external=external), structure, _none)


def test_distinct_external_origins_are_the_oracles_answer() -> None:
    assert reaches(ref(A, HP), ref(B, HP), _any)
    assert not reaches(ref(A, HP), ref(B, HP), _none)
    # Two members of one tuple parameter are two origins: they may be one object.
    assert reaches(ref(TUP, MIRTupleIndex(1), HP), ref(TUP, MIRTupleIndex(0), HP), _any)


def test_retentions_oracle_spares_aggregates_under_a_leaf_write() -> None:
    exclusions = storage_exclusions(SLOTS, _records())
    # An owned leaf's buffer holds no record, but may be one an owned leaf holder views.
    assert not reaches(ref(B, HS), ref(A, HQ), exclusions)
    assert reaches(ref(B, HS), ref(A, HS), exclusions)
    # Any other external write may hold whatever a holder retains.
    assert reaches(ref(B, HQ), ref(A, HQ), exclusions)


def test_presences_oracle_reads_the_written_places_declared_type() -> None:
    records = _records()
    fact = MIRPlace(A, (MIRDeref(), HP))

    def answer(written: MIRPlace) -> bool:
        w = resolve_referents(written, _seed(), SLOTS, records)
        t = resolve_referents(fact, _seed(), SLOTS, records)
        distinct = declared_containment(written, fact, SLOTS, records)
        return any(reaches(x, y, distinct) for x in w for y in t)
    # The same member, or a record field whose declared type may hold it.
    assert answer(MIRPlace(B, (MIRDeref(), HP)))
    # A `P` field never holds an `H.p`, and a str field holds no record.
    assert not answer(MIRPlace(B, (MIRDeref(), HQ)))
    assert not answer(MIRPlace(B, (MIRDeref(), HS)))
    # A whole object behind a borrowed `H` may be a subclass object, whose
    # own fields no declared type of `H` shows.
    assert answer(MIRPlace(B, (MIRDeref(),)))


# --- the dependency facts the oracles and the kill read -----------------------------------

def _seed() -> dict[MIRPlace, frozenset[MIRReferent]]:
    """Each parameter leaf names its own external origin, as the dependency pass seeds it."""
    return {leaf: frozenset({MIRReferent(origin(leaf, SLOTS).place, external=True)})
            for slot in SLOTS.values() if slot.kind is MIRSlotKind.PARAMETER for leaf in _leaves(slot, SLOTS)}


def test_a_global_root_resolves_external() -> None:
    records = _records()
    assert resolve_referents(MIRPlace(GLOBAL), {}, SLOTS, records) == {ref(GLOBAL)}
    assert resolve_referents(MIRPlace(GLOBAL, (HP,)), {}, SLOTS, records) == {ref(GLOBAL, HP)}
    assert resolve_referents(MIRPlace(OWN), {}, SLOTS, records) == {ref(OWN, external=False)}


# --- the statement storage classifier -----------------------------------------------------

def _write(target: MIRPlace, mode: MIRStorageWriteMode | None, value: object = MIRConstant(0),
           owner: MIRSlotId | None = None) -> MIRAssign:
    return MIRAssign(target, value, storage_write=None if mode is None else MIRStorageWrite(mode, owner))


def test_the_classifier_keeps_every_kind_and_replacements_select_from_it() -> None:
    field = MIRPlace(A, (MIRDeref(), HP))
    for mode, kind in ((MIRStorageWriteMode.INITIALIZE, MIRStorageEventKind.INITIALIZE),
                       (MIRStorageWriteMode.IN_PLACE, MIRStorageEventKind.REPLACE)):
        stmt = _write(MIRPlace(OWN), mode, owner=A)
        assert storage_events(stmt, (), SLOTS) == (MIRStorageEvent(kind, MIRPlace(OWN), A),)
    # A scalar field is overwritten: no storage a borrow points into.
    assert storage_events(_write(MIRPlace(A, (MIRDeref(), MIRField(MIRFieldId(H, "n"), INT32))), None),
                          (), SLOTS) == ()
    # A call's writes that are storage destinations, then a move out of owned storage.
    moved = MIRAssign(MIRPlace(B), MIRMove(OWN))
    assert storage_events(moved, (field, MIRPlace(A)), SLOTS) == (
        MIRStorageEvent(MIRStorageEventKind.CALL_WRITE, field),
        MIRStorageEvent(MIRStorageEventKind.MOVE, MIRPlace(OWN)))
    # Replacements: no initialization; an owned sum's fill only of a payload
    # its old selection may hold -- the same alternative included, none on
    # a first fill.
    init = _write(MIRPlace(OWN), MIRStorageWriteMode.INITIALIZE)
    assert replacement_events(init, (field,), _any_selection, SLOTS) == ((field, None),)
    fill = _write(MIRPlace(SUM), MIRStorageWriteMode.IN_PLACE, MIRWrap(1, MIRPlace(BUILT)))
    payload = MIRPlace(SUM, (MIRUnionPayload(1),))
    assert storage_events(fill, (), SLOTS) == (MIRStorageEvent(MIRStorageEventKind.REPLACE, MIRPlace(SUM),
                                                               payloads=((1, True),)),)
    assert replacement_events(fill, (), lambda place: frozenset({0}), SLOTS) == ()
    for old in (frozenset({1}), frozenset({0, 1}), None):
        assert replacement_events(fill, (), lambda place: old, SLOTS) == ((payload, None),)
    empty = _write(MIRPlace(SUM), MIRStorageWriteMode.IN_PLACE, MIRWrap(0))
    assert replacement_events(empty, (), lambda place: frozenset({1}), SLOTS) == ((payload, None),)


def test_an_owned_tuple_is_initialized_by_its_construct_and_needs_its_fact() -> None:
    pair, member = MIRSlotId(BODY, 20), MIRSlotId(BODY, 23)
    slots = {**SLOTS, pair: MIRSlot(pair, tuple_shape(TupleType((INT32, H)), (leaf_shape(INT32), owned_shape(H))),
                                    MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),
             member: MIRSlot(member, owned_shape(H), MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY)}
    # The record member is moved in from its own temporary: no event of its own.
    stmt = _write(MIRPlace(pair), MIRStorageWriteMode.INITIALIZE, MIRConstruct((A, member)))
    assert storage_events(stmt, (), slots) == (MIRStorageEvent(MIRStorageEventKind.INITIALIZE, MIRPlace(pair)),)
    assert replacement_events(stmt, (), _any_selection, slots) == ()
    # Without its write fact, what the construction does to storage is unknown.
    missing = replace(stmt, storage_write=None)
    assert storage_events(missing, (), slots) == (MIRStorageEvent(MIRStorageEventKind.REPLACE, MIRPlace(pair),
                                                                  known=False),)
    assert replacement_events(missing, (), _any_selection, slots) is None


# --- the presence field kill --------------------------------------------------------------

def _kill(stmt: MIRAssign, fact: MIRPlace, resolve=None, slots=SLOTS) -> bool:
    records = _records()
    state = _State(frozenset({(fact, frozenset({1}))}))
    resolve = resolve or (lambda point, place: resolve_referents(place, _seed(), slots, records))
    return not _kill_field_facts(state, stmt, MIRPoint(None, 0), slots, records, resolve).present


def test_a_write_through_another_tuple_member_reaches_the_fact() -> None:
    # `t[1].p = None` against a fact on `t[0].p`: one root, but the two
    # members are distinct origins that may be one object (`t = (h, h)`).
    fact = MIRPlace(TUP, (MIRTupleIndex(0), MIRDeref(), HP))
    write = _write(MIRPlace(TUP, (MIRTupleIndex(1), MIRDeref(), HP)), MIRStorageWriteMode.IN_PLACE, MIRWrap(0))
    assert _kill(write, fact)
    other = _write(MIRPlace(TUP, (MIRTupleIndex(1), MIRDeref(), HQ)), MIRStorageWriteMode.IN_PLACE)
    assert not _kill(other, fact)


def test_a_whole_object_write_through_a_base_reaches_a_subclass_field() -> None:
    # `*base = ...` may replace a `Sub` object `sub` names, whose own field
    # `extra` no declared type of `H` shows.
    fact = MIRPlace(SUBP, (MIRDeref(), EXTRA))
    assert _kill(_write(MIRPlace(BASE, (MIRDeref(),)), MIRStorageWriteMode.IN_PLACE, owner=BASE), fact)
    # A field of the base keeps it: a field is inline at its declared type.
    assert not _kill(_write(MIRPlace(BASE, (MIRDeref(), HQ)), MIRStorageWriteMode.IN_PLACE), fact)


def test_the_kill_fails_closed() -> None:
    fact = MIRPlace(A, (MIRDeref(), HP))
    own = MIRPlace(OWN, (HQ,))
    assert not _kill(_write(own, MIRStorageWriteMode.IN_PLACE), fact)
    # A storage write without its fact, a tuple construction without one included.
    assert _kill(_write(own, None), fact)
    pair = MIRSlotId(BODY, 21)
    slots = {**SLOTS, pair: MIRSlot(pair, tuple_shape(TupleType((INT32, H)), (leaf_shape(INT32), owned_shape(H))),
                                    MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY)}
    assert _kill(MIRAssign(MIRPlace(pair), MIRConstruct((A, B))), fact, slots=slots)
    # A scalar wrapper without its fact replaces no storage a borrow points into.
    wrapper = MIRSlotId(BODY, 22)
    slots = {**SLOTS, wrapper: MIRSlot(wrapper, optional_shape(OptionalType(INT32), leaf_shape(INT32)),
                                       MIRSlotKind.TEMPORARY)}
    assert not _kill(MIRAssign(MIRPlace(wrapper), MIRWrap(0)), fact, slots=slots)
    # A point with no dependency state, or a place with no referent.
    assert _kill(_write(own, MIRStorageWriteMode.IN_PLACE), fact, lambda point, place: None)
    assert _kill(_write(own, MIRStorageWriteMode.IN_PLACE), fact, lambda point, place: frozenset())


def test_the_preparation_resolver_knows_no_state_at_an_unreached_point() -> None:
    known = MIRPoint(MIRBlockId(BODY, 0), 0)
    deps = type("Deps", (), {"referents": {known: _seed()}})()
    resolve = _resolver(deps, SLOTS, _records())
    assert resolve(known, MIRPlace(A, (MIRDeref(), HP))) == {ref(A, HP)}
    assert resolve(MIRPoint(MIRBlockId(BODY, 9), 0), MIRPlace(A, (MIRDeref(), HP))) is None


def test_a_storage_write_strictly_below_the_fact_keeps_it() -> None:
    # `h.o.name = ...` replaces the payload's own str buffer: storage inside
    # the wrapper, never the wrapper's selection.
    fact = MIRPlace(A, (MIRDeref(), HO))
    below = MIRPlace(A, (MIRDeref(), HO, MIRUnionPayload(1), NAME))
    assert not _kill(_write(below, MIRStorageWriteMode.IN_PLACE), fact)
    assert _kill(_write(MIRPlace(A, (MIRDeref(), HO)), MIRStorageWriteMode.IN_PLACE, MIRWrap(0)), fact)


def test_an_initialization_or_a_first_fill_keeps_a_field_fact() -> None:
    # Resolved here to the very wrapper the fact names, so only the event
    # keeps the fact: an initialization replaces nothing a holder points
    # into, an owned sum's fill replaces only a payload it may hold.
    fact = MIRPlace(A, (MIRDeref(), HP))
    same = (lambda point, place: frozenset({ref(A, HP)}))
    assert not _kill(_write(MIRPlace(OWN), MIRStorageWriteMode.INITIALIZE), fact, same)
    assert _kill(_write(MIRPlace(OWN), MIRStorageWriteMode.IN_PLACE), fact, same)
    fill = _write(MIRPlace(SUM), MIRStorageWriteMode.IN_PLACE, MIRWrap(1, MIRPlace(BUILT)))
    records = _records()
    for selected, killed in ((frozenset({0}), False), (frozenset({1}), True), (frozenset({0, 1}), True)):
        state = _State(frozenset({(fact, frozenset({1})), (MIRPlace(SUM), selected)}))
        after = _kill_field_facts(state, fill, MIRPoint(None, 0), SLOTS, records, same)
        assert (fact not in dict(after.present)) is killed


def test_a_root_fact_on_a_condition_outcome_survives_a_field_kill() -> None:
    # `o is not None and h.p is not None`: both facts ride the boolean's
    # outcome; emptying h.p ends the field's, never the root wrapper's.
    root = MIRPlace(MIRSlotId(BODY, 30))
    fact = MIRPlace(A, (MIRDeref(), HP))
    flag = MIRSlotId(BODY, 31)
    outcome = frozenset({(root, frozenset({1})), (fact, frozenset({1}))})
    state = _State(conditions={flag: (frozenset(), outcome)})
    records = _records()
    write = _write(MIRPlace(B, (MIRDeref(), HP)), MIRStorageWriteMode.IN_PLACE, MIRWrap(0))
    after = _kill_field_facts(state, write, MIRPoint(None, 0), SLOTS, records,
                              lambda point, place: resolve_referents(place, _seed(), SLOTS, records))
    assert after.conditions[flag][1] == frozenset({(root, frozenset({1}))})


def test_the_classifier_takes_wrapper_constructions_and_payload_writes() -> None:
    wrapper = MIRSlotId(BODY, 40)
    slots = {**SLOTS, wrapper: MIRSlot(wrapper, optional_shape(OptionalType(INT32), leaf_shape(INT32)),
                                       MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY)}
    place = MIRPlace(wrapper)
    # A scalar wrapper is its own storage; an owned sum built empty materializes nothing.
    assert storage_events(MIRStorageInit(place, 0, MIRConstant(None)), (), slots) == (
        MIRStorageEvent(MIRStorageEventKind.CONSTRUCT, place, materialized=place),)
    assert storage_events(MIRStorageInit(MIRPlace(SUM), 0, MIRConstant(None)), (), slots) == (
        MIRStorageEvent(MIRStorageEventKind.CONSTRUCT, MIRPlace(SUM)),)
    # A scalar wrapper's value replaced in place ends its payload, never storage a borrow points into.
    payload = _write(place, MIRStorageWriteMode.IN_PLACE, MIRWrap(0))
    assert storage_events(payload, (), slots) == (MIRStorageEvent(MIRStorageEventKind.PAYLOAD, place,
                                                                  payloads=((1, False),)),)
    assert replacement_events(payload, (), _any_selection, slots) == ()
    # Its missing fact is its own event, but no storage a borrow points into is unknown.
    missing = replace(payload, storage_write=None)
    assert storage_events(missing, (), slots) == (MIRStorageEvent(MIRStorageEventKind.PAYLOAD, place, known=False,
                                                                  payloads=((1, False),)),)
    assert replacement_events(missing, (), _any_selection, slots) == ()
    for construction in (MIRStorageInit(place, 0, MIRConstant(None)), MIRStorageInit(MIRPlace(SUM), 0, MIRConstant(None))):
        assert replacement_events(construction, (), _any_selection, slots) == ()


def test_a_projected_place_inside_a_scalar_wrapper_is_a_payload_write() -> None:
    # Lowering emits none (the validator forbids an Optional payload
    # replacement); the classifier still files it by its destination.
    wrapper = MIRSlotId(BODY, 41)
    slots = {**SLOTS, wrapper: MIRSlot(wrapper, optional_shape(OptionalType(INT32), leaf_shape(INT32)),
                                       MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY)}
    inside = MIRPlace(wrapper, (MIRUnionPayload(1),))
    write = _write(inside, MIRStorageWriteMode.IN_PLACE)
    assert storage_events(write, (), slots) == (MIRStorageEvent(MIRStorageEventKind.PAYLOAD, inside),)
    missing = replace(write, storage_write=None)
    assert storage_events(missing, (), slots) == (MIRStorageEvent(MIRStorageEventKind.PAYLOAD, inside, known=False),)
    assert replacement_events(missing, (), _any_selection, slots) == ()

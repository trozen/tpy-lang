"""Finite payload-selection facts and boolean implications over mutable holders."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType

from ..thir.scalar_leaves import (
    container_view, declared_members, inline_member_type, owned_leaf, record_type, storage_leaf, view_leaf,
)
from ..typesys import BOOL, OptionalType, TpyType, TupleType, UnionType, is_void_like_type, unwrap_readonly
from .coverage import call_write_places
from .nodes import (
    MIRStatement, MIRStorageInit, MIRBlockId, MIRBorrow, MIRBranch, MIRConstant, MIRCopy, MIRFunction, MIRGoto,
    statement_call, statement_target, MIRContainerElements, MIRDeref, MIRField, MIRFieldId, MIRRecordLayout,
    MIRSlot, MIROptionalLayout, MIRTupleIndex, MIRTupleLayout,
    MIRIsPresent, MIRNot, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRPlace, MIRPoint, MIRRead, MIRSlotId, MIRValueKind,
    MIREdge, MIRReturn, MIRRecordStorageInit, MIRRecordStorageKind, MIRAssign, MIRRecordWrite, MIRRecordWriteMode,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionLayout, MIRUnionPayload, MIRUnionExtract,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance, place_layout,
)
from .region_flow import MIRRegionFlow

# Keyed by the wrapper's place (an iterator's by its slot's place).
Facts = frozenset[tuple[MIRPlace, frozenset[int]]]
# None means this boolean outcome is impossible, not that its facts are unknown.
Outcomes = tuple[Facts | None, Facts | None]
EMPTY: Facts = frozenset()


class MIREngagement(Enum):
    EMPTY = auto()
    ENGAGED = auto()


EngagementFacts = frozenset[tuple[MIRSlotId, frozenset[MIREngagement]]]
DISENGAGED = frozenset({MIREngagement.EMPTY})


class MIRPresenceIssueKind(Enum):
    SELECTION = auto()
    FRESHNESS = auto()


@dataclass(frozen=True)
class MIRPresenceIssue:
    point: MIRPoint
    kind: MIRPresenceIssueKind
    message: str


@dataclass(frozen=True)
class MIRPresence:
    function: MIRFunction
    # Absent points are infeasible; absent places at a point have their full domain.
    points: Mapping[MIRPoint, Facts]
    domains: Mapping[MIRPlace, frozenset[int]]
    issues: tuple[MIRPresenceIssue, ...]
    edges: Mapping[MIREdge, Facts]
    engagement: Mapping[MIRPoint, EngagementFacts]
    edge_engagement: Mapping[MIREdge, EngagementFacts]


@dataclass(eq=True)
class _State:
    present: Facts = EMPTY
    conditions: dict[MIRSlotId, Outcomes] = field(default_factory=dict)
    valid_aliases: frozenset[MIRSlotId] = frozenset()
    engagement: EngagementFacts = frozenset()


def _combine(left: Facts, right: Facts | None) -> Facts | None:
    if right is None:
        return None
    result = dict(left)
    for place, alternatives in right:
        result[place] = result.get(place, alternatives) & alternatives
        if not result[place]:
            return None
    return frozenset(result.items())


def _under(place: MIRPlace, prefix: MIRPlace) -> bool:
    return place.root == prefix.root and place.projections[:len(prefix.projections)] == prefix.projections


def _wrapper(payload: MIRPlace) -> MIRPlace:
    """The place of the wrapper a payload projection selects from."""
    return MIRPlace(payload.root, payload.projections[:-1])


def _outcome(state: _State, slot: MIRSlotId, truth: bool) -> Facts | None:
    return _combine(state.present, state.conditions.get(slot, (EMPTY, EMPTY))[int(truth)])


def _remember(conditions: dict[MIRSlotId, Outcomes], slot: MIRSlotId,
              outcomes: Outcomes, present: Facts) -> None:
    # Unconditional facts belong to the state, not to every boolean temporary.
    reduced = tuple(None if facts is None else facts - present for facts in outcomes)
    if reduced == (EMPTY, EMPTY):
        conditions.pop(slot, None)
    else:
        conditions[slot] = (reduced[0], reduced[1])


def _join_facts(facts: list[Facts]) -> Facts:
    maps = [dict(f) for f in facts]
    common = set.intersection(*(set(m) for m in maps))
    return frozenset((place, frozenset.union(*(m[place] for m in maps))) for place in common)


def _join(states: list[_State]) -> _State:
    present = _join_facts([s.present for s in states])
    conditions: dict[MIRSlotId, Outcomes] = {}
    for slot in set().union(*(s.conditions.keys() for s in states)):
        outcomes: list[Facts | None] = []
        for truth in (False, True):
            possible = [facts for s in states if (facts := _outcome(s, slot, truth)) is not None]
            outcomes.append(_join_facts(possible) if possible else None)
        _remember(conditions, slot, (outcomes[0], outcomes[1]), present)
    engagement = [dict(s.engagement) for s in states]
    joined = frozenset((slot, frozenset.union(*(m.get(slot, DISENGAGED) for m in engagement)))
                       for slot in set().union(*(m.keys() for m in engagement)))
    return _State(present, conditions, frozenset.intersection(*(s.valid_aliases for s in states)), joined)


def _place_type(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> TpyType | None:
    """The type of the value at a place, None where a projection names no
    one type MIR models."""
    typ: TpyType | None = slots[place.root].type
    for depth, projection in enumerate(place.projections):
        layout = place_layout(MIRPlace(place.root, place.projections[:depth]), slots)
        match projection:
            case MIRDeref():
                pass
            case MIRField():
                typ = unwrap_readonly(projection.type)
            case MIROptionalPayload() if isinstance(layout, MIROptionalLayout):
                typ = layout.type
            case MIRTupleIndex() if isinstance(layout, MIRTupleLayout):
                typ = layout.elements[projection.index].type
            case MIRUnionPayload() if isinstance(layout, MIRUnionLayout):
                member = layout.elements[projection.alternative]
                typ = member.type if member is not None else None
            case MIRContainerElements() if typ is not None and (members := declared_members(typ)) is not None:
                typ = unwrap_readonly(members[1] if members[1] is not None else members[0])
            case _:
                return None
    return typ


def _may_hold(typ: TpyType | None, member: MIRFieldId, layouts: Mapping[TpyType, MIRRecordLayout]) -> bool:
    """Whether storage of `typ` may hold record member `member`. Only what
    is proven not to answers no: a scalar leaf, an owned leaf or a view
    holds no record; a tuple, an Optional or a union holds the member only
    if an element, the payload or an alternative may; a record whose
    layout is here holds it only if it, or a record stored inline in it
    (`inline_member_type`), declares it. Any other type -- unknown, a
    container, a generic record, a record with no layout here -- may."""
    pending = [typ]
    seen: set[TpyType] = set()
    while pending:
        current = pending.pop()
        if current is None:
            return True
        current = unwrap_readonly(current)
        if storage_leaf(current) or owned_leaf(current) or view_leaf(current) or container_view(current):
            continue
        if current in seen:
            continue
        seen.add(current)
        match current:
            case TupleType():
                pending.extend(current.element_types)
                continue
            case OptionalType():
                pending.append(current.inner)
                continue
            case UnionType():
                pending.extend(m for m in current.members if not is_void_like_type(m))
                continue
        layout = layouts.get(current) if record_type(current) else None
        if layout is None:
            return True
        for f in layout.fields:
            if f.id == member:
                return True
            pending.append(inline_member_type(f.type))
    return False


# Slots whose root write points the slot elsewhere without writing storage
# another place can reach.
_REBINDING_KINDS = frozenset({MIRValueKind.BORROWED, MIRValueKind.BORROWED_CONTAINER,
                              MIRValueKind.PAYLOAD_ALIAS, MIRValueKind.NATIVE_ITERATOR})


def _private(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """A place in the body's own record storage, reached through no holder:
    two such places under different roots are distinct storage."""
    return slots[place.root].value_kind is MIRValueKind.OWNED and MIRDeref() not in place.projections


def _reaches(written: MIRPlace, wrapper: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
             layouts: Mapping[TpyType, MIRRecordLayout]) -> bool:
    """Whether a write of `written` may change which payload the wrapper
    field at `wrapper` selects -- the overlap retention decides by
    (`retention.may_overlap`), on places: under one root, when one path is
    a prefix of the other; under different roots, unless both are the
    body's own storage, when the written storage may be or hold the
    wrapper's member (a holder or parameter may name any object of its
    type). Rebinding a root that borrows (a holder, a container, a payload
    alias, an iterator's cursor) writes no storage; any other root write
    may, as far as its type may hold the member."""
    if written.root == wrapper.root:
        common = min(len(written.projections), len(wrapper.projections))
        return written.projections[:common] == wrapper.projections[:common]
    if _private(written, slots) and _private(wrapper, slots):
        return False
    if not written.projections and slots[written.root].value_kind in _REBINDING_KINDS:
        return False
    member = wrapper.projections[-1]
    if not isinstance(member, MIRField):
        return True
    last = written.projections[-1] if written.projections else None
    if isinstance(last, MIRField) and last.id == member.id:
        return True
    return _may_hold(_place_type(written, slots), member.id, layouts)


def _kill_field_facts(state: _State, stmt: MIRStatement, slots: Mapping[MIRSlotId, MIRSlot],
                      layouts: Mapping[TpyType, MIRRecordLayout]) -> _State:
    """A selection on a wrapper field (a projected place) dies at any write
    that may reach it (`_reaches`), a call's published writes included. A
    root wrapper's selection follows its own writes below."""
    facts = [f for f in state.present if f[0].projections] + [
        f for outcomes in state.conditions.values() for found in outcomes if found is not None
        for f in found if f[0].projections]
    if not facts:
        return state
    target = statement_target(stmt)
    call = statement_call(stmt)
    written = ((target,) if target is not None else ()) + (call_write_places(call, slots) if call is not None else ())

    def dead(fact_place: MIRPlace) -> bool:
        return bool(fact_place.projections) and any(_reaches(w, fact_place, slots, layouts) for w in written)

    if not any(dead(f[0]) for f in facts):
        return state
    present = frozenset(f for f in state.present if not dead(f[0]))
    conditions: dict[MIRSlotId, Outcomes] = {}
    for slot, outcomes in state.conditions.items():
        kept = tuple(None if facts is None else frozenset(f for f in facts if not dead(f[0])) for facts in outcomes)
        _remember(conditions, slot, (kept[0], kept[1]), present)
    return _State(present, conditions, state.valid_aliases, state.engagement)


def _transfer(state: _State, stmt: MIRStatement, booleans: set[MIRSlotId],
              domains: dict[MIRPlace, frozenset[int]] | None = None,
              aliases: dict[MIRPlace, frozenset[MIRSlotId]] | None = None,
              slots: Mapping[MIRSlotId, MIRSlot] | None = None,
              layouts: Mapping[TpyType, MIRRecordLayout] | None = None) -> _State:
    if slots is not None:
        state = _kill_field_facts(state, stmt, slots, layouts or {})
    place = statement_target(stmt)
    if place is None:
        return state
    if place.projections:
        # A wrapper field written whole selects what its value selects.
        if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, (MIROptionalConstruct, MIROptionalCopy))
                and slots is not None and isinstance(place_layout(place, slots), MIROptionalLayout)):
            known = (frozenset({int(stmt.value.source is not None)}) if isinstance(stmt.value, MIROptionalConstruct)
                     else dict(state.present).get(stmt.value.source))
            if known is not None:
                return _State(state.present | {(place, known)}, state.conditions, state.valid_aliases,
                              state.engagement)
        return state
    match stmt:
        case MIRRecordStorageInit():
            engaged = DISENGAGED
        case MIRAssign(storage_write=MIRRecordWrite(mode=MIRRecordWriteMode.OPTIONAL_ASSIGN)):
            engaged = frozenset({MIREngagement.ENGAGED})
        case _:
            engaged = None
    if engaged is not None:
        facts = frozenset(f for f in state.engagement if f[0] != stmt.target.root)
        return _State(state.present, state.conditions, state.valid_aliases, facts | {(stmt.target.root, engaged)})
    target, value = stmt.target.root, stmt.value
    written = stmt.target
    present, conditions = state.present, state.conditions.copy()
    valid = state.valid_aliases
    if isinstance(stmt, MIRStorageInit) or isinstance(
            value, (MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy,
                    MIRIteratorInit, MIRIteratorAdvance)):
        match stmt if isinstance(stmt, MIRStorageInit) else value:
            case MIRStorageInit(alternative=alternative):
                known = frozenset({alternative})
            case MIROptionalConstruct():
                known = frozenset({int(value.source is not None)})
            case MIRUnionConstruct():
                known = frozenset({value.alternative})
            case MIRIteratorInit() | MIRIteratorAdvance():
                known = None
            case _:
                known = dict(present).get(value.source)
        # The write replaces every selection made at or under the written place.
        present = frozenset(f for f in present if not _under(f[0], written))
        valid -= (aliases or {}).get(written, frozenset())
        conditions = {}
        for slot, outcomes in state.conditions.items():
            kept = tuple(None if facts is None else frozenset(f for f in facts if not _under(f[0], written))
                         for facts in outcomes)
            _remember(conditions, slot, (kept[0], kept[1]), present)
        if known is not None:
            present |= {(written, known)}
    elif target in booleans:
        match value:
            case MIRIsPresent() | MIRIsAlternative() | MIRIteratorHasNext():
                tested = MIRPlace(value.source) if isinstance(value, MIRIteratorHasNext) else value.source
                domain = (domains or {}).get(tested, frozenset({0, 1}))
                selected = (frozenset({1}) if isinstance(value, (MIRIsPresent, MIRIteratorHasNext))
                            else frozenset(value.alternatives))
                outcomes = tuple(_combine(present, frozenset({(tested, members)}))
                                 if members else None for members in (domain - selected, selected))
            case MIRRead() if not value.source.projections and value.source.root in booleans:
                outcomes = tuple(_outcome(state, value.source.root, truth) for truth in (False, True))
            case MIRNot():
                outcomes = tuple(_outcome(state, value.operand, not truth) for truth in (False, True))
            case MIRConstant():
                outcomes = tuple(present if value.value is truth else None for truth in (False, True))
            case _:
                outcomes = (present, present)
        _remember(conditions, target, (outcomes[0], outcomes[1]), present)
    if isinstance(value, MIRUnionExtract) and target in (aliases or {}).get(_wrapper(value.source), frozenset()):
        valid |= {target}
    return _State(present, conditions, valid, state.engagement)


def _missing(place: MIRPlace, state: _State, alias_slots: set[MIRSlotId],
             point: MIRPoint) -> list[MIRPresenceIssue]:
    issues = []
    if place.root in alias_slots and place.root not in state.valid_aliases:
        issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.FRESHNESS,
                                       "union payload alias used after holder replacement or storage end"))
    facts = dict(state.present)
    for depth, projection in enumerate(place.projections):
        wrapper = MIRPlace(place.root, place.projections[:depth])
        if isinstance(projection, MIROptionalPayload) and facts.get(wrapper) != frozenset({1}):
            issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                           "optional payload access without current presence proof"))
        if isinstance(projection, MIRUnionPayload) and facts.get(wrapper) != frozenset({projection.alternative}):
            issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                           "union payload access without current alternative proof"))
    return issues


def _analyze_presence(fn: MIRFunction) -> MIRPresence:
    """Solve selection/freshness after structural and definite-assignment checks."""
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    edge_facts: dict[MIREdge, Facts] = {}
    edge_engagement: dict[MIREdge, EngagementFacts] = {}
    optional_records = {s.id for s in fn.slots if s.record_storage is MIRRecordStorageKind.OPTIONAL}
    booleans = {s.id for s in fn.slots if s.type == BOOL and s.value_kind is MIRValueKind.SCALAR}
    slots = {s.id: s for s in fn.slots}
    domains: dict[MIRPlace, frozenset[int]] = {}
    for s in fn.slots:
        if s.value_kind in (MIRValueKind.OPTIONAL, MIRValueKind.UNION, MIRValueKind.NATIVE_ITERATOR):
            place = MIRPlace(s.id)
            layout = place_layout(place, slots)
            domains[place] = (frozenset(range(len(layout.elements))) if isinstance(layout, MIRUnionLayout)
                              else frozenset({0, 1}))
    layouts = {r.type: r for r in fn.records}
    alias_slots = {s.id for s in fn.slots if s.value_kind is MIRValueKind.PAYLOAD_ALIAS}
    aliases_by_wrapper: dict[MIRPlace, set[MIRSlotId]] = {}
    for slot in fn.slots:
        if slot.alias_source is not None:
            aliases_by_wrapper.setdefault(_wrapper(slot.alias_source), set()).add(slot.id)
    aliases = {wrapper: frozenset(ids) for wrapper, ids in aliases_by_wrapper.items()}
    incoming: dict[MIRBlockId, _State] = {}
    edges: dict[tuple[MIRBlockId, MIRBlockId, bool | None], _State] = {}
    predecessors: dict[MIRBlockId, set[tuple[MIRBlockId, MIRBlockId, bool | None]]] = {
        bid: set() for bid in blocks
    }
    work = deque([fn.entry])
    queued = {fn.entry}
    while work:
        bid = work.popleft()
        queued.remove(bid)
        sources = [edges[e] for e in predecessors[bid] if e in edges]
        if bid == fn.entry:
            sources.append(_State())
        if not sources:
            continue
        state = _join(sources)
        incoming[bid] = state
        for stmt in blocks[bid].statements:
            state = _transfer(state, stmt, booleans, domains, aliases, slots, layouts)
        term = blocks[bid].terminator
        outgoing: list[tuple[MIRBlockId, bool | None, _State | None]] = []
        match term:
            case MIRReturn():
                edge_facts[MIREdge(bid)] = state.present
                edge_engagement[MIREdge(bid)] = state.engagement
            case MIRGoto():
                outgoing.append((term.target, None, state))
            case MIRBranch():
                for target, truth in ((term.then, True), (term.otherwise, False)):
                    facts = _outcome(state, term.condition, truth)
                    branch = None
                    if facts is not None:
                        conditions = state.conditions.copy()
                        _remember(conditions, term.condition,
                                  (None, facts) if truth else (facts, None), facts)
                        branch = _State(facts, conditions, state.valid_aliases, state.engagement)
                    outgoing.append((target, truth, branch))
        for target, truth, branch in outgoing:
            edge = MIREdge(bid, int(truth is False))
            if branch is None:
                edge_facts.pop(edge, None)
                edge_engagement.pop(edge, None)
            else:
                edge_facts[edge] = branch.present
                edge_engagement[edge] = branch.engagement
                transition = regions.edges[edge]
                forgotten = transition.reset | transition.ended
                if forgotten:
                    present = frozenset(f for f in branch.present if f[0].root not in forgotten)
                    conditions: dict[MIRSlotId, Outcomes] = {}
                    for slot, outcomes in branch.conditions.items():
                        if slot not in forgotten:
                            kept = tuple(None if facts is None
                                         else frozenset(f for f in facts if f[0].root not in forgotten)
                                         for facts in outcomes)
                            _remember(conditions, slot, (kept[0], kept[1]), present)
                    stale = frozenset().union(*(ids for wrapper, ids in aliases.items() if wrapper.root in forgotten))
                    engagement = frozenset(f for f in branch.engagement if f[0] not in transition.ended)
                    branch = _State(present, conditions, branch.valid_aliases - stale - forgotten, engagement)
            key = (bid, target, truth)
            predecessors[target].add(key)
            if edges.get(key) != branch:
                if branch is None:
                    edges.pop(key, None)
                else:
                    edges[key] = branch
                if target not in queued:
                    queued.add(target)
                    work.append(target)
    points: dict[MIRPoint, Facts] = {}
    engagement: dict[MIRPoint, EngagementFacts] = {}
    issues: list[MIRPresenceIssue] = []
    for bid, state in incoming.items():
        for index, stmt in enumerate(blocks[bid].statements):
            point = MIRPoint(bid, index)
            points[point] = state.present
            engagement[point] = state.engagement
            if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, (MIRIteratorRead, MIRIteratorAdvance))
                    and dict(state.present).get(MIRPlace(stmt.value.source)) != frozenset({1})):
                issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                               "iterator operation without current availability proof"))
            target = statement_target(stmt)
            places = [target] if target is not None and target.projections else []
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, (MIRRead, MIRUnionExtract, MIRBorrow, MIRCopy)):
                places.append(stmt.value.source)
            for place in places:
                issues.extend(_missing(place, state, alias_slots, point))
                if (place.root in optional_records
                        and dict(state.engagement).get(place.root) != frozenset({MIREngagement.ENGAGED})):
                    issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                                   "record access without engagement proof"))
            state = _transfer(state, stmt, booleans, domains, aliases, slots, layouts)
        points[MIRPoint(bid, len(blocks[bid].statements))] = state.present
        engagement[MIRPoint(bid, len(blocks[bid].statements))] = state.engagement
    return MIRPresence(fn, MappingProxyType(points), MappingProxyType(domains), tuple(issues),
                       MappingProxyType(edge_facts), MappingProxyType(engagement), MappingProxyType(edge_engagement))

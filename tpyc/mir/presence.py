"""Finite payload-selection facts and boolean implications over mutable holders."""

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType

from ..thir.scalar_leaves import MIRClass
from ..typesys import BOOL
from .coverage import call_write_places
from .nodes import (
    MIRStatement, MIRStorageInit, MIRBlockId, MIRBorrow, MIRBranch, MIRConstant, MIRCopy, MIRFunction, MIRGoto,
    statement_call, statement_target, MIRSlot, MIRNot, MIRPlace, MIRPoint, MIRRead, MIRSlotId, MIREdge, MIRReturn,
    MIRRecordStorageInit, MIRRecordStorageKind, MIRAssign, MIRStorageWrite, MIRStorageWriteMode, MIRIsAlternative,
    MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance, place_shape, MIRWrap,
    MIRCall, MIRPresentCall, present_effects,
)
from .value import MIRHolding, MIRRecords, SUM_CLASSES
from .shapes import MIRUnionPayload
from .region_flow import MIRRegionFlow
from .reach import MIRReferent, declared_containment, reaches, replacement_events

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


def present_call(call: MIRCall, present: 'Facts', slots: Mapping[MIRSlotId, MIRSlot]) -> MIRPresentCall:
    """`call` under the selections `present` proves before it: a sum
    argument's alternatives outside its possible ones (an absent
    Optional's payload, a narrowed union's other members) lend nothing, so
    its effects are the summary's less what they reach (`present_effects`),
    and its writes are those effects' places, resolved once
    (`call_write_places`). No fact, or an empty one, excludes nothing."""
    facts = dict(present)
    excluded: dict[int, frozenset[int]] = {}
    for i, arg in enumerate(call.arguments):
        possible = facts.get(MIRPlace(arg))
        if slots[arg].shape.cls in SUM_CLASSES and possible:
            excluded[i] = frozenset(range(len(slots[arg].shape.members))) - possible
    effects = present_effects(call.summary, excluded)
    return MIRPresentCall(call, effects, call_write_places(call, effects, slots))


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
    # Every call as presence sees it (`present_call`), at its point.
    calls: Mapping[MIRPoint, MIRPresentCall] = MappingProxyType({})


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


# The referents a place resolves to before the statement at a point; None
# when no referent state is known there.
Resolve = Callable[[MIRPoint, MIRPlace], frozenset[MIRReferent] | None]
Kill = Callable[[_State, MIRStatement], _State]


def _kill_field_facts(state: _State, stmt: MIRStatement, point: MIRPoint, slots: Mapping[MIRSlotId, MIRSlot],
                      layouts: MIRRecords, resolve: Resolve) -> _State:
    """A selection on a wrapper field (a projected place) dies where the
    statement retargets its place -- a write of the place or of a prefix
    of it under its root (`_under`): which object the place names changes,
    not a reach question -- or where storage the statement replaces
    (`replacement_events`, a call's writes as this state sees the call,
    an Optional assignment by this state's engagement) reaches the
    wrapper's storage: `reaches` over the referents of both, across
    distinct external origins by `declared_containment`. Fail closed: a
    statement whose write fact is missing, a written place or the wrapper
    with no known or no resolved referents kills. A root wrapper's
    selection follows its own writes in `_transfer`."""
    facts = [f for f in state.present if f[0].projections] + [
        f for outcomes in state.conditions.values() for found in outcomes if found is not None
        for f in found if f[0].projections]
    if not facts:
        return state
    target = statement_target(stmt)
    call = statement_call(stmt)
    engagement = dict(state.engagement)
    events = replacement_events(
        stmt, present_call(call, state.present, slots).writes if call is not None else (),
        lambda root: MIREngagement.ENGAGED in engagement.get(root, frozenset()), slots)
    written = None if events is None else [(place, resolve(point, place)) for place, _owner in events]

    def dead(fact_place: MIRPlace) -> bool:
        # A root fact (an Optional slot, an iterator) is the slot's own and
        # follows its own assignments, never a field write's reach.
        if not fact_place.projections:
            return False
        if target is not None and _under(fact_place, target):
            return True
        if written is None:
            return True
        if not written:
            return False
        wrapper = resolve(point, fact_place)
        if not wrapper:
            return True
        for place, refs in written:
            if not refs:
                return True
            distinct = declared_containment(place, fact_place, slots, layouts)
            if any(reaches(w, t, distinct) for w in refs for t in wrapper):
                return True
        return False

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
              layouts: MIRRecords | None = None, kill: Kill | None = None, fields: bool = True) -> _State:
    """One statement's effect on the selections. Without `fields`, no
    selection on a projected place is recorded (a whole field write, a
    tested field); `kill` ends the field selections a write may reach."""
    if kill is not None:
        state = kill(state, stmt)
    place = statement_target(stmt)
    if place is None:
        return state
    if place.projections:
        if not fields:
            return state
        # A wrapper field written whole selects what its value selects.
        if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, (MIRWrap, MIRCopy))
                and slots is not None
                and place_shape(place, slots, layouts).cls in SUM_CLASSES):
            known = (frozenset({stmt.value.alternative}) if isinstance(stmt.value, MIRWrap)
                     else dict(state.present).get(stmt.value.source))
            if known is not None:
                return _State(state.present | {(place, known)}, state.conditions, state.valid_aliases,
                              state.engagement)
        return state
    match stmt:
        case MIRRecordStorageInit():
            engaged = DISENGAGED
        case MIRAssign(storage_write=MIRStorageWrite(mode=MIRStorageWriteMode.OPTIONAL_ASSIGN)):
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
    sum_copy = isinstance(value, MIRCopy) and slots is not None and slots[target].shape.cls in SUM_CLASSES
    if isinstance(stmt, MIRStorageInit) or sum_copy or isinstance(
            value, (MIRWrap, MIRIteratorInit, MIRIteratorAdvance)):
        match stmt if isinstance(stmt, MIRStorageInit) else value:
            case MIRStorageInit(alternative=alternative):
                known = frozenset({alternative})
            case MIRWrap():
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
            case MIRIsAlternative() if not fields and value.source.projections:
                outcomes = (present, present)
            case MIRIsAlternative() | MIRIteratorHasNext():
                tested = MIRPlace(value.source) if isinstance(value, MIRIteratorHasNext) else value.source
                domain = (domains or {}).get(tested)
                if domain is None:
                    # A tested field: its sum's alternatives; a cursor has one or none.
                    count = (len(place_shape(tested, slots, layouts).alternatives)
                             if isinstance(value, MIRIsAlternative) and slots is not None else 2)
                    domain = frozenset(range(count))
                selected = (frozenset({1}) if isinstance(value, MIRIteratorHasNext)
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


def _missing(place: MIRPlace, state: _State, alias_slots: set[MIRSlotId], point: MIRPoint,
             fields: bool = True) -> list[MIRPresenceIssue]:
    issues = []
    if place.root in alias_slots and place.root not in state.valid_aliases:
        issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.FRESHNESS,
                                       "union payload alias used after holder replacement or storage end"))
    facts = dict(state.present)
    for depth, projection in enumerate(place.projections):
        wrapper = MIRPlace(place.root, place.projections[:depth])
        if not fields and wrapper.projections:
            continue
        if isinstance(projection, MIRUnionPayload) and facts.get(wrapper) != frozenset({projection.alternative}):
            issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                           "payload access without current presence proof"))
    return issues


def _analyze_presence(fn: MIRFunction, resolve: Resolve | None) -> MIRPresence:
    """Solve selection/freshness after structural and definite-assignment
    checks. Without `resolve` no selection on a projected place is recorded
    or demanded: the facts the first dependency run reads, before referents
    exist (`prepare.prepare`). With it, field selections are recorded and
    demanded, each ending where `_kill_field_facts` says over the referents
    `resolve` gives."""
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    edge_facts: dict[MIREdge, Facts] = {}
    edge_engagement: dict[MIREdge, EngagementFacts] = {}
    optional_records = {s.id for s in fn.slots if s.record_storage is MIRRecordStorageKind.OPTIONAL}
    booleans = {s.id for s in fn.slots if s.type == BOOL and s.shape.is_scalar}
    slots = {s.id: s for s in fn.slots}
    domains: dict[MIRPlace, frozenset[int]] = {}
    for s in fn.slots:
        if s.shape.cls in (MIRClass.OPTIONAL, MIRClass.UNION) or s.shape.holding is MIRHolding.CURSOR:
            domains[MIRPlace(s.id)] = (frozenset(range(len(s.shape.alternatives)))
                                       if s.shape.holding is not MIRHolding.CURSOR else frozenset({0, 1}))
    layouts = fn.record_layouts
    fields = resolve is not None

    def kill_at(point: MIRPoint) -> Kill | None:
        if resolve is None:
            return None
        return lambda state, stmt: _kill_field_facts(state, stmt, point, slots, layouts, resolve)
    alias_slots = {s.id for s in fn.slots if s.shape.holding is MIRHolding.ALIAS}
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
        for index, stmt in enumerate(blocks[bid].statements):
            state = _transfer(state, stmt, booleans, domains, aliases, slots, layouts,
                              kill_at(MIRPoint(bid, index)), fields)
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
    calls: dict[MIRPoint, MIRPresentCall] = {}
    for bid, state in incoming.items():
        for index, stmt in enumerate(blocks[bid].statements):
            point = MIRPoint(bid, index)
            points[point] = state.present
            if (call := statement_call(stmt)) is not None:
                calls[point] = present_call(call, state.present, slots)
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
                issues.extend(_missing(place, state, alias_slots, point, fields))
                if (place.root in optional_records
                        and dict(state.engagement).get(place.root) != frozenset({MIREngagement.ENGAGED})):
                    issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                                   "record access without engagement proof"))
            state = _transfer(state, stmt, booleans, domains, aliases, slots, layouts, kill_at(point), fields)
        points[MIRPoint(bid, len(blocks[bid].statements))] = state.present
        engagement[MIRPoint(bid, len(blocks[bid].statements))] = state.engagement
    for block in fn.blocks:
        if block.id in incoming:
            continue
        # A block the fixpoint does not reach: its calls see what the
        # block's own statements establish from an unknown entry.
        state = _State()
        for index, stmt in enumerate(block.statements):
            if (call := statement_call(stmt)) is not None:
                calls[MIRPoint(block.id, index)] = present_call(call, state.present, slots)
            state = _transfer(state, stmt, booleans, domains, aliases, slots, layouts,
                              kill_at(MIRPoint(block.id, index)), fields)
    return MIRPresence(fn, MappingProxyType(points), MappingProxyType(domains), tuple(issues),
                       MappingProxyType(edge_facts), MappingProxyType(engagement), MappingProxyType(edge_engagement),
                       MappingProxyType(calls))

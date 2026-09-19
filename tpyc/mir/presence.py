"""Finite payload-selection facts and boolean implications over mutable holders."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType

from ..typesys import BOOL
from .nodes import (
    MIRAssign, MIRBlockId, MIRBorrow, MIRBranch, MIRConstant, MIRFunction, MIRGoto,
    MIRIsPresent, MIRNot, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRPlace, MIRPoint, MIRRead, MIRSlotId, MIRValueKind,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionPayload, MIRUnionExtract,
)

Facts = frozenset[tuple[MIRSlotId, frozenset[int]]]
# None means this boolean outcome is impossible, not that its facts are unknown.
Outcomes = tuple[Facts | None, Facts | None]
EMPTY: Facts = frozenset()


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
    # Absent points are infeasible; absent slots at a point have their full domain.
    points: Mapping[MIRPoint, Facts]
    domains: Mapping[MIRSlotId, frozenset[int]]
    issues: tuple[MIRPresenceIssue, ...]


@dataclass(eq=True)
class _State:
    present: Facts = EMPTY
    conditions: dict[MIRSlotId, Outcomes] = field(default_factory=dict)
    valid_aliases: frozenset[MIRSlotId] = frozenset()


def _combine(left: Facts, right: Facts | None) -> Facts | None:
    if right is None:
        return None
    result = dict(left)
    for slot, alternatives in right:
        result[slot] = result.get(slot, alternatives) & alternatives
        if not result[slot]:
            return None
    return frozenset(result.items())


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
    return frozenset((slot, frozenset.union(*(m[slot] for m in maps))) for slot in common)


def _join(states: list[_State]) -> _State:
    present = _join_facts([s.present for s in states])
    conditions: dict[MIRSlotId, Outcomes] = {}
    for slot in set().union(*(s.conditions.keys() for s in states)):
        outcomes: list[Facts | None] = []
        for truth in (False, True):
            possible = [facts for s in states if (facts := _outcome(s, slot, truth)) is not None]
            outcomes.append(_join_facts(possible) if possible else None)
        _remember(conditions, slot, (outcomes[0], outcomes[1]), present)
    return _State(present, conditions, frozenset.intersection(*(s.valid_aliases for s in states)))


def _transfer(state: _State, stmt: MIRAssign, booleans: set[MIRSlotId],
              domains: dict[MIRSlotId, frozenset[int]] | None = None,
              aliases: dict[MIRSlotId, frozenset[MIRSlotId]] | None = None) -> _State:
    if stmt.target.projections:
        return state
    target, value = stmt.target.root, stmt.value
    present, conditions = state.present, state.conditions.copy()
    valid = state.valid_aliases
    if isinstance(value, (MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy)):
        match value:
            case MIROptionalConstruct():
                known = frozenset({int(value.source is not None)})
            case MIRUnionConstruct():
                known = frozenset({value.alternative})
            case _:
                known = dict(present).get(value.source)
        present = frozenset(f for f in present if f[0] != target)
        valid -= (aliases or {}).get(target, frozenset())
        conditions = {}
        for slot, outcomes in state.conditions.items():
            kept = tuple(None if facts is None else frozenset(f for f in facts if f[0] != target)
                         for facts in outcomes)
            _remember(conditions, slot, (kept[0], kept[1]), present)
        if known is not None:
            present |= {(target, known)}
    elif target in booleans:
        match value:
            case MIRIsPresent() | MIRIsAlternative():
                domain = (domains or {}).get(value.source, frozenset({0, 1}))
                selected = (frozenset({1}) if isinstance(value, MIRIsPresent)
                            else frozenset(value.alternatives))
                outcomes = tuple(_combine(present, frozenset({(value.source, members)}))
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
    if isinstance(value, MIRUnionExtract) and target in (aliases or {}).get(value.source.root, frozenset()):
        valid |= {target}
    return _State(present, conditions, valid)


def _missing(place: MIRPlace, state: _State, alias_slots: set[MIRSlotId],
             point: MIRPoint) -> list[MIRPresenceIssue]:
    issues = []
    if place.root in alias_slots and place.root not in state.valid_aliases:
        issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.FRESHNESS,
                                       "union payload alias used after holder replacement"))
    facts = dict(state.present)
    for projection in place.projections:
        if isinstance(projection, MIROptionalPayload) and facts.get(place.root) != frozenset({1}):
            issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                           "optional payload access without current presence proof"))
        if isinstance(projection, MIRUnionPayload) and facts.get(place.root) != frozenset({projection.alternative}):
            issues.append(MIRPresenceIssue(point, MIRPresenceIssueKind.SELECTION,
                                           "union payload access without current alternative proof"))
    return issues


def _analyze_presence(fn: MIRFunction) -> MIRPresence:
    """Solve selection/freshness after structural and definite-assignment checks."""
    blocks = {b.id: b for b in fn.blocks}
    booleans = {s.id for s in fn.slots if s.type == BOOL and s.value_kind is MIRValueKind.SCALAR}
    domains = {s.id: (frozenset({0, 1}) if s.value_kind is MIRValueKind.OPTIONAL
                     else frozenset(range(len(s.union_layout.elements))))
               for s in fn.slots if s.value_kind in (MIRValueKind.OPTIONAL, MIRValueKind.UNION)}
    alias_slots = {s.id for s in fn.slots if s.value_kind is MIRValueKind.PAYLOAD_ALIAS}
    aliases_by_root: dict[MIRSlotId, set[MIRSlotId]] = {}
    for slot in fn.slots:
        if slot.alias_source is not None:
            aliases_by_root.setdefault(slot.alias_source.root, set()).add(slot.id)
    aliases = {root: frozenset(ids) for root, ids in aliases_by_root.items()}
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
            state = _transfer(state, stmt, booleans, domains, aliases)
        term = blocks[bid].terminator
        outgoing: list[tuple[MIRBlockId, bool | None, _State | None]] = []
        match term:
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
                        branch = _State(facts, conditions, state.valid_aliases)
                    outgoing.append((target, truth, branch))
        for target, truth, branch in outgoing:
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
    issues: list[MIRPresenceIssue] = []
    for bid, state in incoming.items():
        for index, stmt in enumerate(blocks[bid].statements):
            point = MIRPoint(bid, index)
            points[point] = state.present
            places = [stmt.target] if stmt.target.projections else []
            if isinstance(stmt.value, (MIRRead, MIRUnionExtract, MIRBorrow)):
                places.append(stmt.value.source)
            for place in places:
                issues.extend(_missing(place, state, alias_slots, point))
            state = _transfer(state, stmt, booleans, domains, aliases)
        points[MIRPoint(bid, len(blocks[bid].statements))] = state.present
    return MIRPresence(fn, MappingProxyType(points), MappingProxyType(domains), tuple(issues))

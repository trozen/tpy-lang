"""Presence facts and boolean implications over mutable Optional holders."""

from collections import deque
from dataclasses import dataclass, field

from ..typesys import BOOL
from .nodes import (
    MIRAssign, MIRBlockId, MIRBranch, MIRConstant, MIRFunction, MIRGoto,
    MIRIsPresent, MIRNot, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRPlace, MIRRead, MIRSlotId, MIRValueKind,
)

Facts = frozenset[tuple[MIRSlotId, bool]]
# None means this boolean outcome is impossible, not that its facts are unknown.
Outcomes = tuple[Facts | None, Facts | None]
EMPTY: Facts = frozenset()


@dataclass(eq=True)
class _State:
    present: Facts = EMPTY
    conditions: dict[MIRSlotId, Outcomes] = field(default_factory=dict)


def _combine(left: Facts, right: Facts | None) -> Facts | None:
    if right is None:
        return None
    result = left | right
    return None if any((slot, not value) in result for slot, value in result) else result


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


def _join(states: list[_State]) -> _State:
    present = frozenset.intersection(*(s.present for s in states))
    conditions: dict[MIRSlotId, Outcomes] = {}
    for slot in set().union(*(s.conditions.keys() for s in states)):
        outcomes: list[Facts | None] = []
        for truth in (False, True):
            possible = [facts for s in states if (facts := _outcome(s, slot, truth)) is not None]
            outcomes.append(frozenset.intersection(*possible) if possible else None)
        _remember(conditions, slot, (outcomes[0], outcomes[1]), present)
    return _State(present, conditions)


def _transfer(state: _State, stmt: MIRAssign, booleans: set[MIRSlotId]) -> _State:
    if stmt.target.projections:
        return state
    target, value = stmt.target.root, stmt.value
    present, conditions = state.present, state.conditions.copy()
    if isinstance(value, (MIROptionalConstruct, MIROptionalCopy)):
        known = (value.source is not None if isinstance(value, MIROptionalConstruct)
                 else dict(present).get(value.source))
        present = frozenset(f for f in present if f[0] != target)
        conditions = {}
        for slot, outcomes in state.conditions.items():
            kept = tuple(None if facts is None else frozenset(f for f in facts if f[0] != target)
                         for facts in outcomes)
            _remember(conditions, slot, (kept[0], kept[1]), present)
        if known is not None:
            present |= {(target, known)}
    elif target in booleans:
        if isinstance(value, MIRIsPresent):
            outcomes = tuple(_combine(present, frozenset({(value.source, truth)})) for truth in (False, True))
        elif isinstance(value, MIRRead) and not value.source.projections and value.source.root in booleans:
            outcomes = tuple(_outcome(state, value.source.root, truth) for truth in (False, True))
        elif isinstance(value, MIRNot):
            outcomes = tuple(_outcome(state, value.operand, not truth) for truth in (False, True))
        elif isinstance(value, MIRConstant):
            outcomes = tuple(present if value.value is truth else None for truth in (False, True))
        else:
            outcomes = (present, present)
        _remember(conditions, target, (outcomes[0], outcomes[1]), present)
    return _State(present, conditions)


def _missing(place: MIRPlace, state: _State) -> bool:
    return (any(isinstance(p, MIROptionalPayload) for p in place.projections)
            and (place.root, True) not in state.present)


def presence_error(fn: MIRFunction) -> str | None:
    """Verify selection only; presence says nothing about a record's lifetime."""
    if not any(s.value_kind is MIRValueKind.OPTIONAL for s in fn.slots):
        return None
    blocks = {b.id: b for b in fn.blocks}
    booleans = {s.id for s in fn.slots if s.type == BOOL}
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
            state = _transfer(state, stmt, booleans)
        term = blocks[bid].terminator
        outgoing: list[tuple[MIRBlockId, bool | None, _State | None]] = []
        if isinstance(term, MIRGoto):
            outgoing.append((term.target, None, state))
        elif isinstance(term, MIRBranch):
            for target, truth in ((term.then, True), (term.otherwise, False)):
                facts = _outcome(state, term.condition, truth)
                branch = None
                if facts is not None:
                    conditions = state.conditions.copy()
                    _remember(conditions, term.condition,
                              (None, facts) if truth else (facts, None), facts)
                    branch = _State(facts, conditions)
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
    for bid, state in incoming.items():
        for stmt in blocks[bid].statements:
            if (_missing(stmt.target, state)
                    or isinstance(stmt.value, MIRRead) and _missing(stmt.value.source, state)):
                return "optional payload access without current presence proof"
            state = _transfer(state, stmt, booleans)
    return None

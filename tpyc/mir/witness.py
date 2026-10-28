"""Which MIR conflicts are definite: the hazard record every conflict-finding
analysis emits beside its conflicts, and the one witness that decides it.

A conflict is evidence: MIR's analyses over-approximate (referents join
over paths, external places may alias, element identity is not tracked,
liveness is per slot). A hazard is definite -- a warning a user sees --
only when MIR proves it on the hazard's own facts:

1. strict reach over the full sets: every place the write may land on
   reaches every referent the holder may retain, with no caller-seeded
   loan, no may-alias step between two external origins and no element
   region on both sides (`reach.reaches_definitely`); a payload's end needs
   its selection proven, a return escape exactly one body origin;
2. the use is reached (replacement, scope end, payload end): every feasible
   path from the kill reads the holder's member (a non-call operand, a
   branch condition or a returned value at the holder or under it,
   directly or through an alias with exactly one referent) before every
   holder carrying it is rebound or the body exits; a cycle with no read
   refutes;
3. the escape is reached (store escape): from a statement that freshly
   stores the body loan, every path reaches a normal return with no
   rewrite of the stored place, the body loan being the entry's only
   non-seeded loan there. A store into an `Own[R]` parameter's object is no
   escape when the body neither returns nor hands on that object.

Anything else stays evidence; a witness that exceeds its node cap does too,
and says so (`capped`)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto

from .call_contract import OWNING_PASSINGS
from .dependencies import MIRCallerStore, MIRReferents, resolve_referents
from .nodes import (
    MIRAlias, MIRAssign, MIRBorrow, MIRBranch, MIREdge, MIRFunction, MIRIsAlternative, MIRIteratorInit, MIRMove,
    MIRPlace, MIRPoint, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRUnionExtract, MIRWrap, statement_call,
    statement_target, transfer_member,
)
from .prepare import MIRPrepared
from .reach import MIRReferent, reaches_definitely, storage_events
from .region_flow import outgoing_edges
from .shapes import MIRDeref, MIRUnionPayload
from .validate import statement_read_places


class MIRHazardKind(Enum):
    REPLACEMENT = auto()
    SCOPE_END = auto()
    PAYLOAD_END = auto()
    RETURN_ESCAPE = auto()
    STORE_ESCAPE = auto()


@dataclass(frozen=True)
class MIRHazard:
    """One conflict-finding event as the analysis that found it saw it:
    the kill (`site`: the replacing or payload-ending statement, the
    region-exit edge, the return), the storage the kill may land on, in
    full (`written`; the escaping origins of a return), the holder, and
    what the holder may retain there, in full (`retained`: a replacement's
    holder in the state before the statement and in the one it leaves).
    `target` is the replaced place, the ended storage or payload, or the
    escaping origin. A store escape lists the caller stores holding the
    origin (`stores`); a replacement the conflicts it groups."""
    kind: MIRHazardKind
    site: MIRPoint | MIREdge
    target: MIRPlace
    written: frozenset[MIRReferent]
    holder: MIRPlace
    retained: tuple[frozenset[MIRReferent], ...] = ()
    stores: tuple[MIRCallerStore, ...] = ()
    conflicts: tuple[object, ...] = ()


@dataclass(frozen=True)
class MIRWitness:
    """A hazard's verdict: definite or evidence only, and its witness --
    the points of the reads every path reaches (rule 2), or the storing
    statements and the returns (rule 3). `capped`: the witness gave up at
    its node cap, so the hazard stays evidence."""
    hazard: MIRHazard
    definite: bool
    reads: tuple[MIRPoint, ...] = ()
    stores: tuple[MIRPoint, ...] = ()
    capped: bool = False


# Graph nodes a witness may explore before it gives up.
NODE_CAP = 20000


# --- places ---------------------------------------------------------------------

def _norm(place: MIRPlace) -> tuple[object, ...]:
    return tuple(p for p in place.projections if not isinstance(p, MIRDeref))


def _member_under(place: MIRPlace, holder: MIRPlace) -> bool:
    """`place` is `holder` or a place under it -- never an enclosing whole record."""
    if place.root != holder.root:
        return False
    a, b = _norm(place), _norm(holder)
    return len(a) >= len(b) and a[:len(b)] == b


def _target_effect(target: MIRPlace, holder: MIRPlace) -> str | None:
    """'through' when a write dereferences `holder` (a use of what it
    holds), 'rebind' when it replaces `holder` or an object containing it."""
    if target.root != holder.root:
        return None
    kn = _norm(holder)
    matched = 0
    for p in target.projections:
        if isinstance(p, MIRDeref):
            if matched >= len(kn):
                return "through"
            continue
        if matched < len(kn):
            if p != kn[matched]:
                return None
        matched += 1
    tn = _norm(target)
    if len(tn) <= len(kn):
        return "rebind" if kn[:len(tn)] == tn else None
    return None


def _rebased(place: MIRPlace, state: MIRReferents, strict: bool) -> list[MIRPlace]:
    """`place`, and `place` re-rooted at the body storage each holder prefix
    of it refers to (an alias or a borrowed record local reaches its
    object's members); `strict` (a write) only through a proper prefix."""
    out = [place]
    for n in range(len(place.projections) + (0 if strict else 1)):
        for ref in state.get(MIRPlace(place.root, place.projections[:n]), ()):
            if ref.held or ref.static:
                continue
            rest = tuple(p for p in place.projections[n:] if not isinstance(p, MIRDeref))
            out.append(MIRPlace(ref.place.root, (*ref.place.projections, *rest)))
    return out


def _definite_bases(place: MIRPlace, state: MIRReferents) -> list[MIRPlace]:
    """`place`, and `place` re-rooted through a holder prefix whose referent
    set is exactly one object: no may-alias enumeration."""
    out = [place]
    for n in range(len(place.projections) + 1):
        refs = state.get(MIRPlace(place.root, place.projections[:n]))
        if not refs or len(refs) != 1:
            continue
        ref = next(iter(refs))
        if ref.held or ref.static:
            continue
        rest = tuple(p for p in place.projections[n:] if not isinstance(p, MIRDeref))
        out.append(MIRPlace(ref.place.root, (*ref.place.projections, *rest)))
    return out


class _Body:
    """One prepared body's facts the witness reads."""

    def __init__(self, prepared: MIRPrepared) -> None:
        fn = prepared.function
        self.fn: MIRFunction = fn
        self.presence = prepared.presence
        self.dependencies = prepared.dependencies
        self.slots: Mapping[MIRSlotId, MIRSlot] = {s.id: s for s in fn.slots}
        self.blocks = {b.id: b for b in fn.blocks}

    def state(self, point: MIRPoint) -> MIRReferents:
        return self.dependencies.referents.get(point, {})

    def successors(self, block) -> list:
        return [t for e, t in outgoing_edges(block.id, block.terminator)
                if t is not None and e in self.presence.edges]

    def rewrites(self, point: MIRPoint, *, transfers: bool = True) -> list[MIRPlace]:
        """Every place the statement at `point` rebinds, re-rooted through
        the holders it writes through: its target, the storage its call
        writes (`storage_events`, over the call as presence sees it) and,
        with `transfers`, the view members its callee may store a loan in."""
        block = self.blocks[point.block]
        stmt = block.statements[point.index]
        called = self.presence.calls.get(point)
        places: list[MIRPlace] = []
        if (target := statement_target(stmt)) is not None:
            places.append(target)
        places += [event.place for event in storage_events(stmt, called.writes if called is not None else (),
                                                           self.slots)]
        if called is not None and transfers:
            for t in called.effects.transfers:
                if isinstance(t.holder, int):
                    record, member = transfer_member(called.call, t, self.slots)
                    places.append(MIRPlace(record.root, (*record.projections, member)))
        pre = self.state(point)
        return [p for place in places for p in _rebased(place, pre, True)]


def _all_paths(start: object, step: Callable[[object], object]) -> tuple[bool | None, list[object]]:
    """Whether every path from `start` reaches a node `step` answers 'T'
    for (least fixpoint: a cycle without one refutes), and the 'T' nodes
    explored; None past the node cap. `step` answers 'T', 'F' or the
    successor nodes (every one of which must hold)."""
    graph: dict[object, object] = {}
    order: list[object] = []
    todo = [start]
    while todo:
        node = todo.pop()
        if node in graph:
            continue
        result = step(node)
        graph[node] = result
        order.append(node)
        if isinstance(result, list):
            todo.extend(result)
            if len(graph) > NODE_CAP:
                return None, []
    value = {n: r == "T" for n, r in graph.items()}
    changed = True
    while changed:
        changed = False
        for n in order:
            r = graph[n]
            if isinstance(r, list) and r and not value[n] and all(value[s] for s in r):
                value[n] = True
                changed = True
    return value[start], [n for n in order if graph[n] == "T"]


_POINTER_ONLY = (MIRIsAlternative, MIRBorrow, MIRAlias, MIRUnionExtract, MIRWrap, MIRIteratorInit)


def _use_reached(body: _Body, start: MIRPoint, holder: MIRPlace) -> tuple[bool | None, tuple[MIRPoint, ...]]:
    """Rule 2 from `start`: every feasible path reaches a non-call read of
    `holder`'s member before every tracked holder is rebound or the body
    exits. A whole block is one step: its statements run in order."""
    def reads_member(place: MIRPlace, tracked: frozenset[MIRPlace], state: MIRReferents) -> bool:
        return any(_member_under(x, k) for x in _definite_bases(place, state) for k in tracked)

    def step(node: tuple) -> object:
        bid, index, tracked = node
        block = body.blocks[bid]
        for i in range(index, len(block.statements)):
            if not tracked:
                return "F"
            point = MIRPoint(bid, i)
            stmt = block.statements[i]
            pre = body.state(point)
            target = stmt.target if isinstance(stmt, MIRAssign) else None
            chained: set[MIRPlace] = set()
            if statement_call(stmt) is None:
                value = getattr(stmt, "value", None)
                if target is not None and any(_target_effect(target, k) == "through" for k in tracked):
                    return ("T", i)
                if (not isinstance(value, _POINTER_ONLY)
                        and any(reads_member(q, tracked, pre) for q in statement_read_places(stmt))):
                    return ("T", i)
                if isinstance(value, (MIRAlias, MIRBorrow)) and target is not None:
                    source = value.source if isinstance(value.source, MIRPlace) else MIRPlace(value.source)
                    if reads_member(source, tracked, pre):
                        post = body.state(MIRPoint(bid, i + 1))
                        leaf = MIRPlace(target.root, target.projections)
                        if len(post.get(leaf, ())) == 1:
                            chained.add(leaf)
            kept = {k for k in tracked if not any(_target_effect(t, k) == "rebind" for t in body.rewrites(point))}
            tracked = frozenset(kept | chained)
        if not tracked:
            return "F"
        term = block.terminator
        state = body.state(MIRPoint(bid, len(block.statements)))
        if isinstance(term, MIRReturn):
            if term.value is not None and reads_member(MIRPlace(term.value), tracked, state):
                return ("T", len(block.statements))
            return "F"
        if isinstance(term, MIRBranch) and reads_member(MIRPlace(term.condition), tracked, state):
            return ("T", len(block.statements))
        successors = body.successors(block)
        return [(t, 0, tracked) for t in successors] if successors else "F"

    reads: dict[tuple, int] = {}

    def recorded(node: tuple) -> object:
        result = step(node)
        if isinstance(result, tuple):
            reads[node] = result[1]
            return "T"
        return result

    verdict, hits = _all_paths((start.block, start.index, frozenset({holder})), recorded)
    return verdict, tuple(sorted({MIRPoint(n[0], reads[n]) for n in hits}, key=lambda p: (p.block.index, p.index)))


def _escape_reached(body: _Body, key: MIRPlace, origin: MIRReferent,
                    returned: bool) -> Callable[[MIRPoint], bool | None]:
    """Rule 3 for one stored place and origin, one backward pass over the
    body: from a point, whether every feasible path reaches a normal return
    with no rewrite of `key` on the way, `origin` being the only
    non-seeded loan of `key` there (least fixpoint). A store into a record
    the body returns (`returned`) escapes only at a return of that record."""
    rewritten: dict[MIRPoint, bool] = {}

    def rewrites(point: MIRPoint) -> bool:
        if point not in rewritten:
            rewritten[point] = any(_target_effect(t, key) == "rebind" for t in body.rewrites(point))
        return rewritten[point]

    def scan(bid, index: int, entry: Mapping) -> bool:
        block = body.blocks[bid]
        for i in range(index, len(block.statements)):
            if rewrites(MIRPoint(bid, i)):
                return False
        term = block.terminator
        if isinstance(term, MIRReturn):
            if returned and term.value != key.root:
                return False
            loans = body.state(MIRPoint(bid, len(block.statements))).get(key, frozenset())
            nonheld = [o for o in loans if not o.held]
            return len(nonheld) == 1 and nonheld[0] == origin
        successors = body.successors(block)
        return bool(successors) and all(entry.get(t, False) for t in successors)

    entry: dict[object, bool] = {bid: False for bid in body.blocks}
    changed = True
    while changed:
        changed = False
        for bid in reversed(list(body.blocks)):
            if not entry[bid] and scan(bid, 0, entry):
                entry[bid] = True
                changed = True
    return lambda point: scan(point.block, point.index, entry)


# --- the rules --------------------------------------------------------------------

def _all_pairs(written: Iterable[MIRReferent], retained: Iterable[MIRReferent]) -> bool:
    written, retained = frozenset(written), frozenset(retained)
    return bool(written) and bool(retained) and all(
        reaches_definitely(w, r) for w in written for r in retained)


def _strict_reach(body: _Body, hazard: MIRHazard) -> bool:
    """Rule 1 on the hazard's full sets."""
    match hazard.kind:
        case MIRHazardKind.REPLACEMENT:
            return any(_all_pairs(hazard.written, retained) for retained in hazard.retained)
        case MIRHazardKind.SCOPE_END:
            ended = hazard.target
            if ended.projections:
                assert isinstance(hazard.site, MIREdge)
                wrapper = MIRPlace(ended.root)
                selected = dict(body.presence.edges[hazard.site]).get(wrapper, body.presence.domains[wrapper])
                if len(selected) != 1:
                    return False
            return all(_all_pairs(hazard.written, retained) for retained in hazard.retained)
        case MIRHazardKind.PAYLOAD_END:
            assert isinstance(hazard.site, MIRPoint)
            payload = hazard.target
            facts = dict(body.presence.points.get(hazard.site, ()))
            alternative = payload.projections[-1]
            assert isinstance(alternative, MIRUnionPayload)
            if facts.get(MIRPlace(payload.root, payload.projections[:-1])) != frozenset({alternative.alternative}):
                return False
            return all(bool(retained) and all(r.place == payload and not r.external for r in retained)
                       for retained in hazard.retained)
        case MIRHazardKind.RETURN_ESCAPE:
            return len(hazard.written) == 1 and not next(iter(hazard.written)).external
    return True


def _own_handed_on(body: _Body, root: MIRSlotId) -> bool:
    """Whether the body hands an `Own[R]` parameter's object on: moves it,
    or passes it (or a holder of it) at an owning or by-value passing."""
    for block in body.fn.blocks:
        for i, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and stmt.value.source == root:
                return True
            call = statement_call(stmt)
            if call is None:
                continue
            state = body.state(MIRPoint(block.id, i))
            params = call.summary.parameters
            for j, argument in enumerate(call.arguments):
                if j >= len(params) or params[j].passing not in OWNING_PASSINGS:
                    continue
                if argument == root or any(r.place.root == root for r in state.get(MIRPlace(argument), ())):
                    return True
    return False


def _fresh_stores(body: _Body, key: MIRPlace, origin: MIRReferent) -> list[MIRPoint]:
    """The statements that store `origin` into `key` afresh: where a
    returned record's store is made."""
    found = []
    for block in body.fn.blocks:
        for i in range(len(block.statements)):
            point = MIRPoint(block.id, i)
            if (origin not in body.state(point).get(key, frozenset())
                    and origin in body.state(MIRPoint(block.id, i + 1)).get(key, frozenset())):
                found.append(point)
    return found


Reached = dict[tuple[MIRPlace, MIRReferent, bool], Callable[[MIRPoint], bool | None]]


def _store_reached(body: _Body, hazard: MIRHazard, reached: Reached) -> MIRWitness:
    """Rule 3 over the caller stores holding the hazard's origin. A store
    whose record the body returns is judged like one into a parameter's
    object: from each statement that stores the origin afresh, every path
    reaches a return with nothing rewriting the member and the origin its
    only non-seeded loan there (`reached`, the body's cache by stored
    place and origin)."""
    key = hazard.holder
    found: list[MIRPoint] = []
    returns: list[MIRPoint] = []

    def escapes(origin: MIRReferent, point: MIRPoint, returned: bool = False) -> bool:
        if (key, origin, returned) not in reached:
            reached[(key, origin, returned)] = _escape_reached(body, key, origin, returned)
        return bool(reached[(key, origin, returned)](point))

    for store in hazard.stores:
        origins = [r for r in store.loans if r.place == hazard.target and not r.external]
        if not origins:
            continue
        origin = origins[0]
        if store.result:
            returns.append(store.point)
            found.extend(p for p in _fresh_stores(body, key, origin)
                         if escapes(origin, MIRPoint(p.block, p.index + 1), returned=True))
            continue
        if store.point.index < 1:
            continue
        prev = MIRPoint(store.point.block, store.point.index - 1)
        if not any(_target_effect(t, key) == "rebind" for t in body.rewrites(prev, transfers=False)):
            continue
        # The statement itself stores the origin: freshly, or its value resolves to it.
        pre = body.state(prev)
        fresh = origin not in pre.get(key, frozenset())
        stmt = body.blocks[prev.block].statements[prev.index]
        source = getattr(getattr(stmt, "value", None), "source", None)
        if not fresh and source is not None:
            place = source if isinstance(source, MIRPlace) else MIRPlace(source)
            fresh = origin in resolve_referents(place, pre, body.slots, body.fn.record_layouts)
        if fresh and escapes(origin, store.point):
            found.append(prev)
    slot = body.slots[key.root]
    if (slot.kind is MIRSlotKind.PARAMETER and slot.shape.owns_storage
            and not any(store.result for store in hazard.stores)):
        if not _own_handed_on(body, key.root):
            return MIRWitness(hazard, False)
    return MIRWitness(hazard, bool(found), tuple(sorted(set(returns), key=lambda p: (p.block.index, p.index))),
                      tuple(sorted(set(found), key=lambda p: (p.block.index, p.index))))


def decide(prepared: MIRPrepared, hazards: Iterable[MIRHazard]) -> tuple[MIRWitness, ...]:
    """Each hazard's verdict (`MIRWitness`), in the order given. The use
    witness runs once per kill point and holder, the escape witness once per
    stored place and origin."""
    body = _Body(prepared)
    uses: dict[tuple[MIRPoint, MIRPlace], tuple[bool | None, tuple[MIRPoint, ...]]] = {}
    reached: Reached = {}
    found: list[MIRWitness] = []
    for hazard in hazards:
        if not _strict_reach(body, hazard):
            found.append(MIRWitness(hazard, False))
            continue
        match hazard.kind:
            case MIRHazardKind.RETURN_ESCAPE:
                assert isinstance(hazard.site, MIREdge)
                block = body.blocks[hazard.site.source]
                found.append(MIRWitness(hazard, True, (MIRPoint(block.id, len(block.statements)),)))
                continue
            case MIRHazardKind.STORE_ESCAPE:
                found.append(_store_reached(body, hazard, reached))
                continue
        start = _use_start(body, hazard)
        key = (start, hazard.holder)
        if key not in uses:
            uses[key] = _use_reached(body, start, hazard.holder)
        verdict, reads = uses[key]
        found.append(MIRWitness(hazard, bool(verdict), reads if verdict else (), capped=verdict is None))
    return tuple(_escape_decides(found))


def _escape_decides(found: list[MIRWitness]) -> list[MIRWitness]:
    """A storage end whose loan a definite store escape carries out of the
    body is that escape's hazard, warned once there; any other scope end is
    decided on its own."""
    escaping = [(w.hazard.holder, w.hazard.target) for w in found
                if w.definite and w.hazard.kind is MIRHazardKind.STORE_ESCAPE]
    return [MIRWitness(w.hazard, False) if w.definite and w.hazard.kind is MIRHazardKind.SCOPE_END and any(
                holder == w.hazard.holder and origin.lies_under(w.hazard.target) for holder, origin in escaping)
            else w for w in found]


def dump_witness_cap(witnesses: Iterable[MIRWitness]) -> str:
    """The `--dump-mir` line for hazards left evidence by the node cap; empty
    when none hit it."""
    capped = sum(w.capped for w in witnesses)
    return f"witness cap reached: {capped} hazards\n" if capped else ""


def _use_start(body: _Body, hazard: MIRHazard) -> MIRPoint:
    if isinstance(hazard.site, MIREdge):
        target = next(t for e, t in outgoing_edges(hazard.site.source, body.blocks[hazard.site.source].terminator)
                      if e == hazard.site)
        return MIRPoint(target, 0)
    return MIRPoint(hazard.site.block, hazard.site.index + 1)

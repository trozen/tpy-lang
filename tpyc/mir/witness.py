"""Which MIR conflicts warn, as definite or "may" hazards: the hazard record
every conflict-finding analysis emits beside its conflicts, and the one
witness that decides it.

A conflict is evidence: MIR's analyses over-approximate (referents join
over paths, external places may alias, element identity is not tracked,
liveness is per slot). A hazard warns -- DEFINITE or MAY -- only when MIR
shows it on the hazard's own facts:

1. reach over the full sets (`_all_pairs`): every place the write may land
   on reaches every referent the holder may retain, with no caller-seeded
   loan and no may-alias step between two external origins
   (`reach.reaches_definitely`) -- DEFINITE; a pair with an element region
   on both sides reaches only maybe (element identity is not tracked) --
   MAY, cause ELEMENT; only some pair reaching (the holder only may keep
   the written storage) -- MAY, cause PARTIAL. A payload's end needs its
   selection proven, and is DEFINITE only for the payload retained by
   name, otherwise at most a PARTIAL MAY; a return escape needs exactly
   one body origin;
2. the use is reached (replacement, scope end, payload end): every feasible
   path from the kill reads the holder's member (a non-call operand, a
   branch condition or a returned value at the holder or under it,
   directly or through an alias with exactly one referent) before every
   holder carrying it is rebound or the body exits. Across a back edge a
   holder is per pass (`_Track`): one rebound onto live storage (the
   body's, or storage outside it) holds it NEW, and holds a freed value
   again only at that storage's own kill (a region end, a statement
   ending its payload, or one replacing it -- storage outside the body
   only when the write lands on one object, or at the hazard's own kill);
   a holder reset safely renews only onto the hazard's own storage, freed
   again at every kill of that storage. Attribution: every path reads a
   freed value and some path the hazard's own storage (a read of another
   storage's freed value does not end a path's walk); a path reading
   only another storage's freed value makes the hazard at most MAY,
   cause PARTIAL, and the reads cited are those of its own storage. A
   cycle with no way to a read refutes; a read
   reached only by leaving a cycle that crosses no kill is at most MAY,
   cause PARTIAL (`_solve`). When that strict walk refutes, the same walk
   in MAY mode (`MIRWalkMode`) reads through a base with several
   referents, ends a holder only at a rebind through a single-referent
   base, and chains any copy of a tracked view: reached there, the hazard
   is MAY, cause PARTIAL, citing that walk's reads;
3. the escape is reached (store escape): from a statement that freshly
   stores the body loan, every path reaches a normal return with no
   rewrite of the stored place, the body loan being the entry's only
   non-seeded loan there; a rewrite storing that loan alone again (the
   next pass's store) is the store renewed, and a return reached only by
   leaving a cycle with no renewed store is at most MAY. A store into an
   `Own[R]` parameter's object is no escape when the body neither returns
   nor hands on that object.

A hazard rule 2 reaches strictly keeps rule 1's verdict. Anything else
stays evidence; a witness that exceeds its node cap does too, and says so
(`capped`)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto

from .call_contract import OWNING_PASSINGS
from .dependencies import MIRCallerStore, MIRReferents, resolve_referents
from .nodes import (
    MIRAlias, MIRAssign, MIRBlockId, MIRBorrow, MIRBranch, MIREdge, MIRFunction, MIRIsAlternative, MIRIteratorInit,
    MIRMove, MIRPlace, MIRPoint, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRUnionExtract, MIRWrap,
    statement_call, statement_target, transfer_member, view_member,
)
from .prepare import MIRPrepared
from ..thir.scalar_leaves import MIRClass
from .reach import (
    MIRReferent, reaches_definitely, referent_walk, replaced_below, storage_events,
)
from .region_flow import outgoing_edges
from .shapes import REGION_STEPS, MIRContainerStructure, MIRDeref, MIRUnionPayload
from .validate import reaches_element, statement_read_places
from .value import MIRRecords, MIRValidationError
from ..scc import strongly_connected_components


class MIRVerdict(Enum):
    """A hazard's verdict; `strength` orders them, weakest first."""
    EVIDENCE = 0
    MAY = 1
    DEFINITE = 2

    @property
    def strength(self) -> int:
        return self.value


class MIRMayCause(Enum):
    """Why a hazard is only MAY: ELEMENT -- element identity is not tracked
    (an index may name another element); PARTIAL -- the holder only may keep
    the written storage (some written x retained pairs reach) or is read
    through a base with several referents."""
    ELEMENT = auto()
    PARTIAL = auto()


class MIREffect(Enum):
    """What a warned replacement does to what its holder keeps: frees it
    (an element's or a whole value's storage), moves it (a container's shape
    write), or overwrites it where it lies (a record field, a record
    payload, a whole container field object), so the holder sees the new
    value."""
    FREES = auto()
    MOVES = auto()
    OVERWRITES = auto()


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
    """A hazard's verdict (`MIRVerdict`), and its witness -- the points of
    the reads every path reaches (rule 2), or the storing statements and the
    returns (rule 3). `capped`: the witness gave up at its node cap, so the
    hazard stays evidence."""
    hazard: MIRHazard
    verdict: MIRVerdict
    reads: tuple[MIRPoint, ...] = ()
    stores: tuple[MIRPoint, ...] = ()
    capped: bool = False
    # A warned replacement's effect (`replacement_effect`); None otherwise.
    effect: MIREffect | None = None
    # Why a MAY verdict is MAY; None for any other verdict.
    cause: MIRMayCause | None = None
    # The kill lies on a feasible cycle of the body, so it recurs each pass.
    looped: bool = False

    @property
    def warns(self) -> bool:
        return self.verdict is not MIRVerdict.EVIDENCE


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


def _bases(place: MIRPlace, state: MIRReferents, strict: bool) -> list[tuple[MIRPlace, MIRVerdict]]:
    """`place`, and `place` re-rooted at the body storage each holder prefix
    of it refers to (an alias or a borrowed record local reaches its
    object's members); `strict` (a write) only through a proper prefix.
    A re-rooting is DEFINITE through a prefix with exactly one referent,
    MAY through one of several."""
    out = [(place, MIRVerdict.DEFINITE)]
    for n in range(len(place.projections) + (0 if strict else 1)):
        refs = state.get(MIRPlace(place.root, place.projections[:n]), ())
        strength = MIRVerdict.DEFINITE if len(refs) == 1 else MIRVerdict.MAY
        for ref in refs:
            if ref.held or ref.static:
                continue
            rest = tuple(p for p in place.projections[n:] if not isinstance(p, MIRDeref))
            out.append((MIRPlace(ref.place.root, (*ref.place.projections, *rest)), strength))
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
        self.regions = prepared.regions
        self.preds: dict[MIRBlockId, list[MIRBlockId]] = {}
        for block in fn.blocks:
            for t in self.successors(block):
                self.preds.setdefault(t, []).append(block.id)
        # The blocks on a feasible cycle: a kill there recurs each pass.
        self.cyclic = frozenset(
            bid for component in strongly_connected_components(self.blocks, lambda b: self.successors(self.blocks[b]))
            for bid in component if len(component) > 1 or bid in self.successors(self.blocks[bid]))
        # Every kill of storage by site: each region-exit edge ends the
        # storage of the regions it leaves, each statement the storage it
        # replaces and the scalar payloads it ends (`MIRStatementKills`).
        self.kills: dict[MIRPoint | MIREdge, frozenset[MIRReferent]] = {
            edge: frozenset(MIRReferent(MIRPlace(slot)) for slot in transition.ended)
            for edge, transition in self.regions.edges.items() if transition.ended}
        statement_kills = prepared.kills
        self.spared: Mapping[MIRPoint, frozenset[MIRReferent]] = statement_kills.spared
        for point in {*statement_kills.replacements, *statement_kills.payloads}:
            killed = {r for replacement in statement_kills.replacements.get(point, ()) for r in replacement.storage}
            killed |= {MIRReferent(place) for place in statement_kills.payloads.get(point, ())}
            if killed:
                self.kills[point] = frozenset(killed)

    def in_scope(self, ref: MIRReferent, block: MIRBlockId) -> bool:
        """Whether body storage `ref` names may be alive in `block`: the
        block lies in its storage's duration region."""
        duration = self.slots[ref.place.root].storage_duration
        return not isinstance(duration, MIRRegionId) or duration in self.regions.chains[self.blocks[block].region]

    def state(self, point: MIRPoint) -> MIRReferents:
        return self.dependencies.referents.get(point, {})

    def successors(self, block) -> list:
        return [t for _, t in self.edges(block)]

    def edges(self, block) -> list[tuple[MIREdge, MIRBlockId]]:
        """The block's feasible outgoing edges, each with its target."""
        return [(e, t) for e, t in outgoing_edges(block.id, block.terminator)
                if t is not None and e in self.presence.edges]

    def rewrites(self, point: MIRPoint, *, transfers: bool = True) -> list[MIRPlace]:
        return [p for p, _ in self.rewrite_bases(point, transfers=transfers)]

    def rewrite_bases(self, point: MIRPoint, *, transfers: bool = True) -> list[tuple[MIRPlace, MIRVerdict]]:
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
        return [b for place in places for b in _bases(place, pre, True)]


def _all_paths(start: object, step: Callable[[object], object],
               kills: Iterable[object] = ()) -> tuple[bool | None, list[object], bool, bool]:
    """Rule 2's answer over the nodes `step` explores from `start`: it
    answers 'T' (a read of the hazard's own freed storage), 'F', the
    successor nodes (every one of which must hold), or `_Other` (a read of
    another storage's freed value, then what the walk past it answers:
    a later read of the hazard's own storage still counts); `kills` holds
    the nodes that cross a kill once exploring is done. Answers whether
    every path reads a freed value and some path the hazard's own (None
    past the node cap), the 'T' nodes explored, whether the answer needs a
    cycle that crosses no kill to end (`_solve`), and whether some path
    reads only another storage's freed value (`mixed`)."""
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
        rest = result.rest if isinstance(result, _Other) else result
        if isinstance(rest, list):
            todo.extend(rest)
            if len(graph) > NODE_CAP:
                return None, [], False, False
    kills = set(kills)
    # A path has read a freed value at its first read of either kind; only the hazard's own counts for `own`.
    full, ended = _solve({n: "O" if isinstance(r, _Other) else r for n, r in graph.items()}, kills,
                         frozenset({"T", "O"}))
    own, own_ended = _solve({n: r.rest if isinstance(r, _Other) else r for n, r in graph.items()}, kills)
    hits = [n for n in order if graph[n] == "T"]
    reached = full[start] and bool(hits)
    every = own[start]
    return reached, hits, reached and not (own_ended if every else ended)[start], reached and not every


@dataclass(frozen=True)
class _Other:
    """A step that read another storage's freed value, then answered `rest`."""
    rest: object


def _solve(graph: Mapping[object, object], kills: set[object],
           goals: frozenset[str] = frozenset({"T"})) -> tuple[dict[object, bool], dict[object, bool]]:
    """Two answers over an explored graph (`goals`, 'F' or successor
    lists), each node's: `full` -- every path reaches a goal node or stays
    in a cycle forever, and some goal node is reachable (a cycle with no
    way to a goal fails); `ended` -- every path reaches a goal node or
    crosses a node of `kills` infinitely often, so only a cycle the
    hazard's kill recurs in is taken to end. Linear in the explored
    edges."""
    def goal(r: object) -> bool:
        return isinstance(r, str) and r in goals

    preds: dict[object, list[object]] = {n: [] for n in graph}
    for n, r in graph.items():
        if isinstance(r, list):
            for t in r:
                preds[t].append(n)
    live = {n for n, r in graph.items() if goal(r)}
    stack = list(live)
    while stack:
        for q in preds[stack.pop()]:
            if q not in live:
                live.add(q)
                stack.append(q)

    def falsify(seeds: set[object]) -> dict[object, bool]:
        bad = set(seeds)
        stack = list(seeds)
        while stack:
            for q in preds[stack.pop()]:
                if q not in bad and not goal(graph[q]):
                    bad.add(q)
                    stack.append(q)
        return {n: n not in bad for n in graph}

    dead = {n for n, r in graph.items() if not goal(r) and (not isinstance(r, list) or not r or n not in live)}
    full = falsify(dead)
    # A node also fails `ended` when it can stay, avoiding a goal, in a cycle with no kill node.
    plain = {n for n, r in graph.items() if isinstance(r, list) and r and n not in kills}
    cycles = set(dead)
    for component in strongly_connected_components(plain, lambda n: [t for t in graph[n] if t in plain]):
        if len(component) > 1 or component[0] in graph[component[0]]:
            cycles.update(component)
    return full, falsify(cycles)


_POINTER_ONLY = (MIRIsAlternative, MIRBorrow, MIRAlias, MIRUnionExtract, MIRWrap, MIRIteratorInit)


class MIRWalkMode(Enum):
    """How rule 2 treats a base with several referents. STRICT reads only
    through a single-referent base, ends a holder at any rebind that may
    land on it (through any base), and chains a copy only when it holds one
    referent. MAY reads through any base, ends a holder only at a rebind
    through a single-referent base, and chains a copy of a tracked view
    whatever its count, at every storage the copy lands in."""
    STRICT = auto()
    MAY = auto()


@dataclass(frozen=True)
class _Freed:
    """Storage a holder was renewed onto, since freed: reading it reads a
    freed value, but not the hazard's own (rule 2's attribution)."""
    storage: MIRReferent


#: A tracked holder on one path: (place, None) holds the hazard's freed
#: value (OLD); (place, storage) holds that live storage (NEW), freed
#: again at that storage's own kill -- the hazard's own storage turns OLD
#: there, any other `_Freed`; (place, _OWN) holds the hazard's own storage
#: after a safe reset, freed again at any kill of that storage.
_Track = tuple[MIRPlace, object]
_OWN = "own"


def _use_reached(body: _Body, start: MIRPoint, holder: MIRPlace, mode: MIRWalkMode = MIRWalkMode.STRICT,
                 own: frozenset[MIRReferent] = frozenset(), site: MIRPoint | MIREdge | None = None
                 ) -> tuple[bool | None, tuple[MIRPoint, ...], bool, bool]:
    """Rule 2 from `start`, the point after the hazard's kill `site` of its
    storage `own`: every feasible path reads `holder`'s member while a
    tracked holder holds a freed value (`_Track`), and some path while it
    holds the hazard's own; `mode` says how a base with several referents
    counts (`MIRWalkMode`). A whole block is one step: its statements run
    in order. Also answers, as `_all_paths` does, the reads of the
    hazard's own storage, whether the answer needs a cycle that crosses no
    kill to end, and whether some path reads only another storage's freed
    value."""
    may = mode is MIRWalkMode.MAY

    def reads_member(place: MIRPlace, tracked: Iterable[MIRPlace], state: MIRReferents) -> bool:
        return any(_member_under(x, k) for x, strength in _bases(place, state, False)
                   if may or strength is MIRVerdict.DEFINITE for k in tracked)

    def rebinds(point: MIRPoint) -> list[MIRPlace]:
        if may:
            return [t for t, strength in body.rewrite_bases(point) if strength is MIRVerdict.DEFINITE]
        return body.rewrites(point)

    def definite_storage(place: MIRPlace, state: MIRReferents) -> list[MIRPlace]:
        return [b for b, strength in _bases(place, state, True) if strength is MIRVerdict.DEFINITE]

    def owned(storage: MIRReferent) -> bool:
        return any(w == storage or reaches_definitely(w, storage) for w in own)

    def frees(where: object, storage: MIRReferent) -> bool:
        """Whether a kill at `where` frees renewed `storage`. The hazard's
        own kill frees its own storage whatever the write's strength (rule
        1 weighed that); any other write frees storage outside the body
        only when it lands on one object (`_Body.spared`)."""
        if where == site and owned(storage):
            return True
        spared = body.spared.get(where, frozenset()) if storage.external else frozenset()
        return any(k == storage or reaches_definitely(k, storage)
                   for k in body.kills.get(where, ()) if k not in spared)

    def kills_own(where: object) -> bool:
        """Whether a kill at `where` frees the hazard's own storage (`frees`)."""
        return where == site or any(frees(where, w) for w in own)

    def cross(where: object, tracked: frozenset[_Track]) -> tuple[frozenset[_Track], bool]:
        """The holders after a kill at `where`: a NEW one whose storage it
        frees holds a freed value again -- the hazard's own (OLD) or another
        (`_Freed`). Crossed: the kill frees what a holder holds."""
        if where != site and where not in body.kills:
            return tracked, False
        out: set[_Track] = set()
        crossed = False
        for k, storage in tracked:
            if storage is None:
                crossed |= kills_own(where)
            elif storage is _OWN:
                # Every exit that ends the hazard's storage frees it, not only the hazard's own site.
                if kills_own(where):
                    storage, crossed = None, True
            elif isinstance(storage, _Freed):
                crossed |= kills_own(where) or frees(where, storage.storage)
            elif frees(where, storage):
                storage, crossed = (None if owned(storage) else _Freed(storage)), True
            out.add((k, storage))
        return frozenset(out), crossed

    def renewed(k: MIRPlace, held: bool, post: MIRReferents, bid: MIRBlockId) -> set[_Track]:
        """What a rebound `k` holds NEW: rebound from a tracked value (`held`)
        straight onto live storage, that storage; back from a safe reset,
        only the hazard's own storage."""
        refs = post.get(k, frozenset())
        # Storage outside the body is live too: its statement-level kills (a replacement) free it again.
        live = [r for r in refs if not (r.static or r.held) and (r.external or body.in_scope(r, bid))]
        if held:
            if may:
                return {(k, r) for r in live}
            return {(k, live[0])} if len(refs) == 1 and live else set()
        if k != holder:
            return set()
        hit = [r for r in refs if any(reaches_definitely(w, r) for w in own)
               and (r.external or body.in_scope(r, bid))]
        return {(k, _OWN)} if refs and (hit if may else len(hit) == len(refs)) else set()

    def read(tracked: frozenset[_Track], uses: Callable[[list[MIRPlace]], bool]) -> str | None:
        """'T' when a use reads a holder of the hazard's freed value, 'O'
        when only one of another freed storage, else None."""
        if uses([k for k, storage in tracked if storage is None]):
            return "T"
        if uses([k for k, storage in tracked if isinstance(storage, _Freed)]):
            return "O"
        return None

    # An empty walk only matters where it can come back to the hazard's own kill.
    returning = _reaching(body, site)
    crossing: set[tuple] = set()

    def step(node: tuple) -> object:
        bid, index, tracked = node
        block = body.blocks[bid]
        # The first read of another storage's freed value: the walk goes on for a read of the hazard's own.
        other: int | None = None

        def answered(result: object) -> object:
            return result if other is None else (_Other(result), other)
        for i in range(index, len(block.statements)):
            if not tracked and bid not in returning:
                return answered("F")
            point = MIRPoint(bid, i)
            stmt = block.statements[i]
            pre = body.state(point)
            post = body.state(MIRPoint(bid, i + 1))
            target = stmt.target if isinstance(stmt, MIRAssign) else None
            chained: set[_Track] = set()
            copies = False
            if statement_call(stmt) is None:
                value = getattr(stmt, "value", None)

                def uses(old: list[MIRPlace]) -> bool:
                    if target is not None and any(_target_effect(target, k) == "through" for k in old):
                        return True
                    return (not isinstance(value, _POINTER_ONLY)
                            and any(reads_member(q, old, pre) for q in statement_read_places(stmt)))
                answer = read(tracked, uses)
                if answer == "T":
                    return (answer, i)
                if answer == "O" and other is None:
                    other = i
                if isinstance(value, (MIRAlias, MIRBorrow)) and target is not None:
                    source = value.source if isinstance(value.source, MIRPlace) else MIRPlace(value.source)
                    leaf = MIRPlace(target.root, target.projections)
                    for k, storage in tracked:
                        if not reads_member(source, (k,), pre):
                            continue
                        # A copy of a tracked holder carries what it holds on this path, never the joined state.
                        copies = True
                        if may:
                            # A copy into a field through an alias (`a.s = v`) is read back through
                            # the object's other names, so chain it at the storage it lands in too.
                            chained |= {(b, storage) for b, _ in _bases(leaf, post, True)}
                        elif len(post.get(leaf, ())) == 1:
                            chained.add((leaf, storage))
            tracked, crossed = cross(point, tracked)
            if crossed:
                crossing.add(node)
            places = rebinds(point)
            # A MAY walk tracks a copy under every name it reached (`(*y).s` and the object's own `a.s`),
            # so the body's own store ends a holder whose name re-roots to the same storage. A callee's
            # published store only may run, so it ends only the holder it is spelled at.
            stored = definite_storage(target, pre) if may and target is not None else []

            def rebound(k: MIRPlace) -> bool:
                return (any(_target_effect(t, k) == "rebind" for t in places)
                        or any(_target_effect(t, b) == "rebind" for t in stored for b in definite_storage(k, pre)))

            held = {k for k, _ in tracked}
            kept = {(k, storage) for k, storage in tracked if not rebound(k)}
            fresh: set[_Track] = set()
            if not copies:
                for k in held | {holder}:
                    if rebound(k):
                        fresh |= renewed(k, k in held, post, bid)
            tracked = frozenset(kept | fresh | chained)
        term = block.terminator
        state = body.state(MIRPoint(bid, len(block.statements)))
        operand = (term.value if isinstance(term, MIRReturn) else term.condition if isinstance(term, MIRBranch)
                   else None)
        if operand is not None:
            answer = read(tracked, lambda old: reads_member(MIRPlace(operand), old, state))
            if answer == "T":
                return (answer, len(block.statements))
            if answer == "O" and other is None:
                other = len(block.statements)
        if isinstance(term, MIRReturn):
            return answered("F")
        out = []
        for edge, t in body.edges(block):
            after, crossed = cross(edge, tracked)
            if crossed:
                crossing.add(node)
            out.append((t, 0, after))
        return answered(out if out else "F")

    reads: dict[tuple, int] = {}

    def recorded(node: tuple) -> object:
        result = step(node)
        if isinstance(result, tuple):
            reads[node] = result[1]
            return result[0]
        return result

    verdict, hits, weak, mixed = _all_paths((start.block, start.index, frozenset({(holder, None)})), recorded,
                                            crossing)
    return (verdict, tuple(sorted({MIRPoint(n[0], reads[n]) for n in hits}, key=lambda p: (p.block.index, p.index))),
            weak, mixed)


def _reaching(body: _Body, site: MIRPoint | MIREdge | None) -> frozenset[MIRBlockId]:
    """The blocks from which a feasible path reaches `site` (a kill point or
    the edge it leaves its block by)."""
    if site is None:
        return frozenset()
    goal = site.block if isinstance(site, MIRPoint) else site.source
    found = {goal}
    stack = [goal]
    while stack:
        for q in body.preds.get(stack.pop(), ()):
            if q not in found:
                found.add(q)
                stack.append(q)
    return frozenset(found)


def _escape_reached(body: _Body, key: MIRPlace, origin: MIRReferent,
                    returned: bool) -> Callable[[MIRPoint], bool | str]:
    """Rule 3 for one stored place and origin, one pass over the body's
    blocks: from a point, whether every feasible path reaches a normal
    return with no rewrite of `key` on the way, `origin` being the only
    non-seeded loan of `key` there. A rewrite that leaves `origin` the
    entry's only loan is the next pass's store, no rewrite; a cycle
    through one, or through an end of the origin's storage, is taken to
    end, and an answer that needs any other cycle to end is 'weak'
    (`_solve`). A store into a record the body returns (`returned`)
    escapes only at a return of that record."""
    rewritten: dict[MIRPoint, bool] = {}
    renewing: set[MIRBlockId] = set()

    def rewrites(point: MIRPoint) -> bool:
        if point not in rewritten:
            hit = any(_target_effect(t, key) == "rebind" for t in body.rewrites(point))
            loans = body.state(MIRPoint(point.block, point.index + 1)).get(key, frozenset())
            if hit and loans == {origin}:
                renewing.add(point.block)
                hit = False
            rewritten[point] = hit
        return rewritten[point]

    def scan(bid: MIRBlockId, index: int) -> object:
        block = body.blocks[bid]
        for i in range(index, len(block.statements)):
            if rewrites(MIRPoint(bid, i)):
                return "F"
        term = block.terminator
        if isinstance(term, MIRReturn):
            if returned and term.value != key.root:
                return "F"
            loans = body.state(MIRPoint(bid, len(block.statements))).get(key, frozenset())
            nonheld = [o for o in loans if not o.held]
            return "T" if len(nonheld) == 1 and nonheld[0] == origin else "F"
        return body.successors(body.blocks[bid]) or "F"

    graph = {bid: scan(bid, 0) for bid in body.blocks}
    # A pass that ends the origin's storage (its region's exit edge) kills it too.
    killing = {site.source for site, killed in body.kills.items()
               if isinstance(site, MIREdge) and any(k == origin or reaches_definitely(k, origin) for k in killed)}
    full, ended = _solve(graph, renewing | killing)

    def answer(point: MIRPoint) -> bool | str:
        found = scan(point.block, point.index)
        if not isinstance(found, list):
            return found == "T"
        if not all(full[t] for t in found):
            return False
        return True if all(ended[t] for t in found) else "weak"
    return answer


# --- the rules --------------------------------------------------------------------

_Reach = tuple[MIRVerdict, MIRMayCause | None]


def _all_pairs(written: Iterable[MIRReferent], retained: Iterable[MIRReferent]) -> _Reach:
    """Every pair reaches: DEFINITE, or MAY (ELEMENT) when some pair has
    both places in an element region; some pair reaches: MAY (PARTIAL)."""
    written, retained = frozenset(written), frozenset(retained)
    if not written or not retained:
        return MIRVerdict.EVIDENCE, None
    if not all(reaches_definitely(w, r) for w in written for r in retained):
        if any(reaches_definitely(w, r) for w in written for r in retained):
            return MIRVerdict.MAY, MIRMayCause.PARTIAL
        return MIRVerdict.EVIDENCE, None
    if any(reaches_element(w.place) and reaches_element(r.place) for w in written for r in retained):
        return MIRVerdict.MAY, MIRMayCause.ELEMENT
    return MIRVerdict.DEFINITE, None


def _rank(reach: _Reach) -> int:
    """One order over reaches: EVIDENCE < MAY (PARTIAL) < MAY (ELEMENT) <
    DEFINITE. Every pair reaching (ELEMENT) is stronger than some pair
    reaching (PARTIAL)."""
    verdict, cause = reach
    return 2 * verdict.strength - (verdict is MIRVerdict.MAY and cause is MIRMayCause.PARTIAL)


def _any_set(reaches: Iterable[_Reach]) -> _Reach:
    """Some retained set's reach (a replacement's states before and after)."""
    return max(reaches, key=_rank, default=(MIRVerdict.EVIDENCE, None))


def _every_set(reaches: Iterable[_Reach]) -> _Reach:
    """Every retained set's reach."""
    return min(reaches, key=_rank, default=(MIRVerdict.DEFINITE, None))


def _strict_reach(body: _Body, hazard: MIRHazard) -> _Reach:
    """Rule 1 on the hazard's full sets, and why a MAY is MAY."""
    match hazard.kind:
        case MIRHazardKind.REPLACEMENT:
            return _any_set(_all_pairs(hazard.written, retained) for retained in hazard.retained)
        case MIRHazardKind.SCOPE_END:
            ended = hazard.target
            if ended.projections:
                assert isinstance(hazard.site, MIREdge)
                wrapper = MIRPlace(ended.root)
                selected = dict(body.presence.edges[hazard.site]).get(wrapper, body.presence.domains[wrapper])
                if len(selected) != 1:
                    return MIRVerdict.EVIDENCE, None
            return _every_set(_all_pairs(hazard.written, retained) for retained in hazard.retained)
        case MIRHazardKind.PAYLOAD_END:
            assert isinstance(hazard.site, MIRPoint)
            payload = hazard.target
            facts = dict(body.presence.points.get(hazard.site, ()))
            alternative = payload.projections[-1]
            assert isinstance(alternative, MIRUnionPayload)
            if facts.get(MIRPlace(payload.root, payload.projections[:-1])) != frozenset({alternative.alternative}):
                return MIRVerdict.EVIDENCE, None
            # The ended payload itself, retained by name: proven whatever region it lies in.
            if all(bool(retained) and all(r.place == payload and not r.external for r in retained)
                   for retained in hazard.retained):
                return MIRVerdict.DEFINITE, None
            # Anything else is not proven to be the payload, so it warns at most as a PARTIAL MAY.
            verdict, _ = _every_set(_all_pairs(hazard.written, retained) for retained in hazard.retained)
            if verdict is MIRVerdict.EVIDENCE:
                return verdict, None
            return MIRVerdict.MAY, MIRMayCause.PARTIAL
        case MIRHazardKind.RETURN_ESCAPE:
            return _proven(len(hazard.written) == 1 and not next(iter(hazard.written)).external), None
    return MIRVerdict.DEFINITE, None


def _proven(proven: bool) -> MIRVerdict:
    return MIRVerdict.DEFINITE if proven else MIRVerdict.EVIDENCE


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
    # The stores whose escape needs a cycle with no renewed store to end.
    weak: set[MIRPoint] = set()

    def escapes(origin: MIRReferent, point: MIRPoint, returned: bool = False) -> bool:
        if (key, origin, returned) not in reached:
            reached[(key, origin, returned)] = _escape_reached(body, key, origin, returned)
        answer = reached[(key, origin, returned)](point)
        if answer == "weak":
            weak.add(point)
        return bool(answer)

    for store in hazard.stores:
        origins = [r for r in store.loans if r.place == hazard.target and not r.external]
        if not origins:
            continue
        origin = origins[0]
        if store.result:
            returns.append(store.point)
            for p in _fresh_stores(body, key, origin):
                if escapes(origin, MIRPoint(p.block, p.index + 1), returned=True):
                    found.append(p)
                    if MIRPoint(p.block, p.index + 1) in weak:
                        weak.add(p)
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
            if store.point in weak:
                weak.add(prev)
    slot = body.slots[key.root]
    if (slot.kind is MIRSlotKind.PARAMETER and slot.shape.owns_storage
            and not any(store.result for store in hazard.stores)):
        if not _own_handed_on(body, key.root):
            return MIRWitness(hazard, MIRVerdict.EVIDENCE)
    verdict = _proven(bool(found))
    if found and all(p in weak for p in found):
        verdict = MIRVerdict.MAY
    return MIRWitness(hazard, verdict, tuple(sorted(set(returns), key=lambda p: (p.block.index, p.index))),
                      tuple(sorted(set(found), key=lambda p: (p.block.index, p.index))),
                      cause=MIRMayCause.PARTIAL if verdict is MIRVerdict.MAY else None)


def decide(prepared: MIRPrepared, hazards: Iterable[MIRHazard]) -> tuple[MIRWitness, ...]:
    """Each hazard's verdict (`MIRWitness`), in the order given. The use
    witness runs once per kill point and holder, the escape witness once per
    stored place and origin."""
    body = _Body(prepared)
    hazards = tuple(hazards)
    uses: dict[tuple, tuple[bool | None, tuple[MIRPoint, ...], bool, bool]] = {}
    reached: Reached = {}
    found: list[MIRWitness] = []
    for hazard in hazards:
        reach, cause = _strict_reach(body, hazard)
        if reach is MIRVerdict.EVIDENCE:
            found.append(MIRWitness(hazard, reach))
            continue
        match hazard.kind:
            case MIRHazardKind.RETURN_ESCAPE:
                assert isinstance(hazard.site, MIREdge)
                block = body.blocks[hazard.site.source]
                found.append(MIRWitness(hazard, reach, (MIRPoint(block.id, len(block.statements)),)))
                continue
            case MIRHazardKind.STORE_ESCAPE:
                found.append(_store_reached(body, hazard, reached))
                continue
        start = _use_start(body, hazard)

        def use(mode: MIRWalkMode) -> tuple[bool | None, tuple[MIRPoint, ...], bool, bool]:
            key = (hazard.site, hazard.holder, mode, hazard.written)
            if key not in uses:
                uses[key] = _use_reached(body, start, hazard.holder, mode, hazard.written, hazard.site)
            return uses[key]

        answered, reads, weak, mixed = use(MIRWalkMode.STRICT)
        verdict = reach if answered else MIRVerdict.EVIDENCE
        if answered and (weak or mixed) and verdict is MIRVerdict.DEFINITE:
            # The read is reached only by leaving a cycle no kill recurs in, which may never end, or some
            # path reads only storage the holder was renewed onto, freed by another kill.
            verdict, cause = MIRVerdict.MAY, MIRMayCause.PARTIAL
        if answered is False:
            # Only a MAY walk reaches the use: the holder only may be read.
            answered, reads, _, _ = use(MIRWalkMode.MAY)
            if answered:
                verdict, cause = MIRVerdict.MAY, MIRMayCause.PARTIAL
        warns = verdict is not MIRVerdict.EVIDENCE
        effect = (replacement_effect(hazard, body.slots, body.fn.record_layouts)
                  if warns and hazard.kind is MIRHazardKind.REPLACEMENT else None)
        site = hazard.site.block if isinstance(hazard.site, MIRPoint) else hazard.site.source
        found.append(MIRWitness(hazard, verdict, reads if warns else (), capped=answered is None, effect=effect,
                                cause=cause if verdict is MIRVerdict.MAY else None, looped=site in body.cyclic))
    return tuple(_escape_decides(found))


def holder_views(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether a holder views what it keeps rather than referring to an
    object: a record's view member, a view slot itself, or a cursor over a
    leaf (it walks the buffer, not the object)."""
    if view_member(place) is not None:
        return True
    shape = slots[place.root].shape
    return not place.projections and (shape.cls in (MIRClass.VIEW, MIRClass.CONTAINER_VIEW) or shape.leaf_cursor)


def _shape_write(place: MIRPlace) -> bool:
    steps = [p for p in place.projections if not isinstance(p, MIRDeref)]
    return bool(steps) and isinstance(steps[-1], MIRContainerStructure)


def replacement_effect(hazard: MIRHazard, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> MIREffect:
    """A replacement overwrites what the holder keeps where it lies only when
    that is shown: a referent the holder keeps lies under the path the write
    replaces (`reach.replaced_below`) with no container region step below
    it, and is no owned buffer (a `str` / `bytes`) a view holder views -- a
    record field, an Optional record payload, a whole container field
    object. Otherwise it frees what the holder keeps, or moves it when
    every write is a container's shape write; a free or move shown for any
    referent wins over an overwrite shown for another."""
    views = holder_views(hazard.holder, slots)
    moves = all(_shape_write(x.place) for x in hazard.written)
    overwrites = False
    for retained in hazard.retained:
        for r in retained:
            for w in hazard.written:
                below = (None if r.held or r.static or r.place.root != w.place.root
                         else replaced_below(w.place, r.place))
                if below is None:
                    continue
                frees = any(isinstance(step, REGION_STEPS) for step in below)
                if not frees and views:
                    try:
                        frees = referent_walk(r.place, slots, records).end.cls is MIRClass.OWNED_LEAF
                    except MIRValidationError:
                        frees = True
                if frees:
                    return MIREffect.MOVES if moves else MIREffect.FREES
                overwrites = True
    if overwrites:
        return MIREffect.OVERWRITES
    return MIREffect.MOVES if moves else MIREffect.FREES


def _escape_decides(found: list[MIRWitness]) -> list[MIRWitness]:
    """A storage end whose loan a definite store escape carries out of the
    body is that escape's hazard, warned once there; any other scope end is
    decided on its own."""
    escaping = [(w.hazard.holder, w.hazard.target) for w in found
                if w.warns and w.hazard.kind is MIRHazardKind.STORE_ESCAPE]
    return [MIRWitness(w.hazard, MIRVerdict.EVIDENCE) if w.warns and w.hazard.kind is MIRHazardKind.SCOPE_END and any(
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

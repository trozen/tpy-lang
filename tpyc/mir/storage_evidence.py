"""Internal lifetime evidence for required body storage origins.

Composes the existing liveness, dependency, scope-end, payload-end, retention,
call-effect and presence facts over one whole validated body. This is not a
production certificate for emitted code: an adapter must still bind the exact
function and roots to the emitted THIR obligations and placement plan.

MIR itself bounds the channels: a container element never holds a borrow
(borrow-holding elements refuse at admission), a record holds one only in a
view member (its own or an inline member record's), keyed at the object's
stored-loan entries (`MIRDependencies.objects`), globals hold no references,
and MIR has no captures, exceptional edges or opaque calls; every call
carries a validated summary whose loan transfers the dependency pass
applies. Record cleanup is not representable in MIR, so every body storage record
(a container's record elements included) needs a verified hook-free definition
from the caller.

A verdict is normal-path evidence: it follows the CFG's edges to normal
returns. A body that may exit by exception says so in
`MIRFunction.exceptional_exits`; no verdict here covers those exits.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType

from ..parse import SourceLocation
from ..thir.scalar_leaves import MIRClass
from .call_effects import analyze_call_effects
from .coverage import MIRUnsupported
from .definitions import MIRDefinitions
from .place_text import _location, _place
from .dependencies import (
    MIRDependencies, MIRReferent, _leaves, feasible_returns, live_holders, resolve_referents,
)
from .nodes import (
    MIRBodyId,
    MIRAssign, MIRBodyKind, MIREdge, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint,
    MIRReturn, MIRSlotId, MIRSlotKind, source_order,
)
from .shapes import MIRUnionPayload
from .value import MIRValidationError
from .payload_lifetime import _payload_conflicts, _payload_ends
from .retention import analyze_retention
from .scope_lifetime import _scope_conflicts, _scope_ends
from .storage import analyze_storage
from .prepare import MIRPrepared
from .reach import BEGINS, MIRStorageEventKind, fields_past_origin, storage_events
from .witness import MIRHazard, MIRHazardKind


class MIRStorageVerdict(Enum):
    CERTIFIED = auto()
    CONFLICT = auto()
    NOT_COVERED = auto()


class MIRStorageConflictKind(Enum):
    SCOPE_END = auto()
    REPLACEMENT = auto()
    PAYLOAD_END = auto()
    RETURN_ESCAPE = auto()
    STORE_ESCAPE = auto()


@dataclass(frozen=True)
class MIRStorageConflict:
    kind: MIRStorageConflictKind
    # The required storage place (object or inline payload) that ends or escapes.
    origin: MIRPlace
    holder: MIRPlace
    site: MIREdge | MIRPoint
    loc: SourceLocation | None = None


@dataclass(frozen=True, eq=False)
class MIRStorageEvidence:
    """Evidence about `required` in exactly `function`; never reusable by equality."""
    function: MIRFunction
    required: frozenset[MIRSlotId]
    definitions: MIRDefinitions
    conflicts: tuple[MIRStorageConflict, ...]
    gaps: tuple[MIRNotCovered, ...]

    @property
    def verdict(self) -> MIRStorageVerdict:
        # Missing facts only ever hide events, so a found conflict stands.
        if self.conflicts:
            return MIRStorageVerdict.CONFLICT
        return MIRStorageVerdict.NOT_COVERED if self.gaps else MIRStorageVerdict.CERTIFIED

    def certifies(self, fn: MIRFunction, roots: frozenset[MIRSlotId]) -> bool:
        return (self.function is fn and roots <= self.required
                and self.verdict is MIRStorageVerdict.CERTIFIED)


@dataclass(frozen=True, eq=False)
class MIRBorrowEvidence:
    """Whole-body evidence for exactly the demanded writes and returns."""
    function: MIRFunction
    operations: frozenset[MIRPoint]
    explicit_roots: frozenset[MIRSlotId]
    required: frozenset[MIRSlotId]
    definitions: MIRDefinitions
    origins: Mapping[MIRPoint, frozenset[MIRReferent]]
    conflicts: tuple[MIRStorageConflict, ...]
    gaps: tuple[MIRNotCovered, ...]

    @property
    def verdict(self) -> MIRStorageVerdict:
        if self.conflicts:
            return MIRStorageVerdict.CONFLICT
        return MIRStorageVerdict.NOT_COVERED if self.gaps else MIRStorageVerdict.CERTIFIED

    def certifies_operations(self, fn: MIRFunction, operations: frozenset[MIRPoint],
                             explicit_roots: frozenset[MIRSlotId]) -> bool:
        return (self.function is fn and bool(operations) and operations == self.operations
                and explicit_roots == self.explicit_roots and self.verdict is MIRStorageVerdict.CERTIFIED)


def _place_key(place: MIRPlace) -> tuple[int, int]:
    return place.root.index, len(place.projections)


def _may_be_selected(key: MIRPlace, selected: dict, domains: Mapping) -> bool:
    """Whether every sum payload a stored-loan key lies under may be selected
    at the point (an unselected payload stores no loan)."""
    for depth, projection in enumerate(key.projections):
        if isinstance(projection, MIRUnionPayload):
            wrapper = MIRPlace(key.root, key.projections[:depth])
            if projection.alternative not in selected.get(wrapper, domains.get(wrapper, frozenset({projection.alternative}))):
                return False
    return True


def certify_storage_origins(prepared: MIRPrepared, required: frozenset[MIRSlotId],
                            definitions: MIRDefinitions) -> MIRStorageEvidence:
    """Malformed MIR or a malformed request raises; it is never Not covered."""
    return _storage_evidence(prepared, required, definitions)


def certify_borrow_operations(prepared: MIRPrepared, operations: frozenset[MIRPoint],
                             explicit_roots: frozenset[MIRSlotId],
                             definitions: MIRDefinitions) -> MIRBorrowEvidence:
    return _borrow_evidence(prepared, operations, explicit_roots, definitions)


def _borrow_evidence(prepared: MIRPrepared, operations: frozenset[MIRPoint], explicit_roots: frozenset[MIRSlotId],
                     definitions: MIRDefinitions) -> MIRBorrowEvidence:
    fn, dependencies = prepared.function, prepared.dependencies
    operations, explicit_roots = frozenset(operations), frozenset(explicit_roots)
    if not operations:
        raise MIRValidationError("borrow evidence needs demanded operations")
    slots = {s.id: s for s in fn.slots}
    records = fn.record_layouts
    blocks = {b.id: b for b in fn.blocks}
    origins: dict[MIRPoint, frozenset[MIRReferent]] = {}
    required = set(explicit_roots)
    gaps: list[MIRNotCovered] = []

    def gap(reason: str, loc: SourceLocation | None = None) -> None:
        gaps.append(MIRNotCovered(fn.id, "borrow evidence", reason, loc))

    for point in sorted(operations, key=lambda p: (p.block.index, p.index)):
        if point.block.body != fn.id:
            raise MIRValidationError("borrow operation belongs to a different MIR body")
        block = blocks.get(point.block)
        if block is None or not 0 <= point.index <= len(block.statements):
            gap("demanded borrow operation is absent from the MIR body")
            continue
        operation = block.statements[point.index] if point.index < len(block.statements) else block.terminator
        match operation:
            case MIRAssign(target=target) if (not target.projections
                    and slots[target.root].shape.borrow_holder):
                holder = target
                state_point = MIRPoint(point.block, point.index + 1)
            case MIRReturn(value=value) if (fn.borrowed_result is not None
                                            and fn.kind is MIRBodyKind.FREE_FUNCTION):
                holder = MIRPlace(value)
                state_point = point
            case _:
                gap("demanded operation is not a supported record borrow", operation.loc)
                continue
        if point not in prepared.presence.points:
            gap("demanded borrow operation has no feasible presence facts", operation.loc)
            continue
        if isinstance(dependencies, MIRNotCovered):
            continue
        state = dependencies.referents.get(state_point)
        if state is None:
            gap("demanded borrow operation has no dependency facts", operation.loc)
            continue
        refs = resolve_referents(holder, state, slots, records)
        if not refs:
            gap("demanded borrow operation has unknown origins", operation.loc)
            continue
        origins[point] = refs
        for ref in refs:
            if not ref.external:
                if ref.place.root not in slots or not slots[ref.place.root].lifetime_root:
                    gap("borrow operation reaches an unrepresented storage origin", operation.loc)
                else:
                    required.add(ref.place.root)
    roots = frozenset(required)
    conflicts, lifetime_gaps = _check_storage(prepared, roots, definitions)
    return MIRBorrowEvidence(fn, operations, explicit_roots, roots, definitions, MappingProxyType(origins),
                             conflicts, (*gaps, *lifetime_gaps))


def _storage_evidence(prepared: MIRPrepared, required: frozenset[MIRSlotId],
                      definitions: MIRDefinitions) -> MIRStorageEvidence:
    required = frozenset(required)
    if not required:
        raise MIRValidationError("storage evidence needs required origins")
    conflicts, gaps = _check_storage(prepared, required, definitions)
    return MIRStorageEvidence(prepared.function, required, definitions, conflicts, gaps)


def _check_storage(prepared: MIRPrepared, required: frozenset[MIRSlotId], definitions: MIRDefinitions
                   ) -> tuple[tuple[MIRStorageConflict, ...], tuple[MIRNotCovered, ...]]:
    fn, presence = prepared.function, prepared.presence
    liveness, dependencies = prepared.liveness, prepared.dependencies
    for result in (presence, liveness, dependencies):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("storage evidence input belongs to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    for root in required:
        if root not in slots or not slots[root].lifetime_root:
            raise MIRValidationError("required origin is not storage of this MIR body")
    conflicts: list[MIRStorageConflict] = []
    gaps: list[MIRNotCovered] = []

    def gap(reason: str, loc: SourceLocation | None = None, slot: MIRSlotId | None = None) -> None:
        gaps.append(MIRNotCovered(fn.id, "storage evidence", reason, loc, slot))

    def attribute(kind: MIRStorageConflictKind, origin: MIRPlace, holder: MIRPlace,
                  site: MIREdge | MIRPoint, loc: SourceLocation | None) -> None:
        if origin.root in required:
            conflicts.append(MIRStorageConflict(kind, origin, holder, site, loc))
        else:
            # A known dangling use of other storage still withholds certification.
            gap("lifetime conflict outside the required origins", loc)

    def point_loc(point: MIRPoint) -> SourceLocation | None:
        block = blocks[point.block]
        return block.statements[point.index].loc if point.index < len(block.statements) else block.terminator.loc

    for slot in fn.slots:
        # An owned tuple's or sum's storage members are checked directly: a
        # whole copy of one has no temporary of its own to be checked through.
        members = ((slot.shape,) if slot.shape.is_storage else
                   tuple(m for m in slot.shape.members if m is not None and m.is_storage)
                   if slot.shape.owned_tuple or slot.shape.owned_sum else ())
        for member in members:
            try:
                definition = definitions.get(slot, member.type)
            except MIRUnsupported as failure:
                gap(f"storage record without verified hook-free definition: {failure.reason}")
                continue
            # Owned container storage answers its container layout (whose
            # record elements its definition verified); records their layout.
            if (definition.members != member.members if member.cls is MIRClass.CONTAINER
                    else definition.layout not in fn.records):
                gap("storage record layout differs from its definition")

    # A root's storage materialized on a feasible path (`BEGINS`).
    # A construction materializes the place its event names: none for an
    # owned sum built empty.
    materialized = {event.place.root for block in fn.blocks for index, stmt in enumerate(block.statements)
                    if MIRPoint(block.id, index) in presence.points
                    for event in storage_events(stmt, (), slots)
                    if event.kind in BEGINS and event.known and not event.place.projections
                    and (event.kind is not MIRStorageEventKind.CONSTRUCT or event.materialized is not None)}
    # A by-value parameter's storage is materialized by the caller, before entry.
    materialized.update(s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.lifetime_root)
    for root in sorted(required - materialized, key=lambda s: s.index):
        gap(f"required origin %{root.index} is never materialized on a feasible path", slot=root)

    for issue in presence.issues:
        gap(f"stale alias: {issue.message}", point_loc(issue.point))

    if isinstance(dependencies, MIRNotCovered):
        gaps.append(dependencies)
        return tuple(conflicts), tuple(gaps)

    # Dependency states omit empty origin sets, so absence is only evidence for
    # a payload that presence proves unselected at that point.
    for block in fn.blocks:
        for index in range(len(block.statements) + 1):
            point = MIRPoint(block.id, index)
            facts = presence.points.get(point)
            if facts is None:
                continue
            state = dependencies.referents.get(point)
            if state is None:
                gap("missing dependency facts at a feasible point", point_loc(point))
                continue
            selected = dict(facts)
            # A record object a live holder reaches -- an inline member
            # object too -- has an entry for each loan it stores
            # (`MIRDependencies.objects`).
            for holder in sorted(live_holders(state, liveness.points[point], dependencies.stored_loans),
                                 key=_place_key):
                for ref in state[holder]:
                    prefix = ref.place.projections
                    # Fields inside the object the referent's origin names (a wrapper member's included).
                    if ref.place.root not in slots or fields_past_origin(ref.place) is None:
                        continue
                    for key in dependencies.objects[ref.place.root]:
                        if (key.projections[:len(prefix)] == prefix and not state.get(key)
                                and _may_be_selected(key, selected, presence.domains)):
                            gap(f"live record %{ref.place.root.index} has unknown stored loans", point_loc(point),
                                ref.place.root)
            for sid in sorted(liveness.points[point], key=lambda s: s.index):
                for leaf in _leaves(slots[sid], slots):
                    wrapper = MIRPlace(leaf.root, leaf.projections[:-1])
                    match leaf.projections:
                        case (*_, MIRUnionPayload(alternative=alternative)):
                            engaged = alternative in selected.get(wrapper, presence.domains[wrapper])
                        case _:
                            engaged = True
                    if engaged and not state.get(leaf):
                        gap(f"live borrowed holder %{sid.index} has unknown origins", point_loc(point), sid)

    ends = _scope_ends(prepared)
    if isinstance(ends, MIRNotCovered):
        gaps.append(ends)
    else:
        scoped = _scope_conflicts(prepared, ends)
        if isinstance(scoped, MIRNotCovered):
            gaps.append(scoped)
        else:
            for conflict in scoped:
                attribute(MIRStorageConflictKind.SCOPE_END, conflict.ended, conflict.holder,
                          conflict.edge, blocks[conflict.edge.source].terminator.loc)

    payload_ends = _payload_ends(prepared)
    if isinstance(payload_ends, MIRNotCovered):
        gaps.append(payload_ends)
    else:
        payloads = _payload_conflicts(prepared, payload_ends)
        if isinstance(payloads, MIRNotCovered):
            gaps.append(payloads)
        else:
            for conflict in payloads:
                attribute(MIRStorageConflictKind.PAYLOAD_END, conflict.payload, conflict.holder,
                          conflict.point, point_loc(conflict.point))

    # The strict replacement and call-effect passes reject stale aliases; the
    # freshness gaps above already withhold certification for such a body.
    if not presence.issues:
        events = analyze_storage(prepared)
        if isinstance(events, MIRNotCovered):
            gaps.append(events)
        retention = analyze_retention(prepared)
        if isinstance(retention, MIRNotCovered):
            gaps.append(retention)
        else:
            for conflict in retention.conflicts:
                if conflict.affected.external:
                    gap("replacement of external storage retained by a live holder", point_loc(conflict.point))
                else:
                    attribute(MIRStorageConflictKind.REPLACEMENT, conflict.affected.place, conflict.holder,
                              conflict.point, point_loc(conflict.point))
        effects = analyze_call_effects(prepared)
        if isinstance(effects, MIRNotCovered):
            gaps.append(effects)

    escapes = analyze_escapes(prepared)
    for loc in escapes.unknown:
        gap("borrowed return has unknown origins", loc)
    for escape in (*escapes.found, *escapes.stored):
        attribute(escape.kind, escape.origin, escape.holder, escape.site, escape.loc)
    return tuple(conflicts), tuple(gaps)


def _return_escapes(prepared: MIRPrepared, dependencies: MIRDependencies
                    ) -> tuple[tuple[MIRStorageConflict, ...], tuple[SourceLocation | None, ...],
                               tuple[MIRHazard, ...]]:
    """Every borrowed return on a feasible path that reaches the body's own
    storage, over all origins, with the locations of returns whose origins
    are unknown, and beside each conflict its hazard (every origin the
    return may hand back). Discovery only: which origins a certificate
    requires is the caller's attribution."""
    fn = prepared.function
    slots = {s.id: s for s in fn.slots}
    records = fn.record_layouts
    escapes: list[MIRStorageConflict] = []
    unknown: list[SourceLocation | None] = []
    hazards: list[MIRHazard] = []
    # Function exit has no successor, so liveness says nothing about a result.
    if fn.borrowed_result is None:
        return (), (), ()
    for point, term, state in feasible_returns(prepared.presence, dependencies):
        origins = resolve_referents(MIRPlace(term.value), state, slots, records)
        if not origins:
            unknown.append(term.loc)
        for origin in sorted(origins, key=lambda r: (r.place.root.index, len(r.place.projections))):
            if not origin.external:
                escapes.append(MIRStorageConflict(MIRStorageConflictKind.RETURN_ESCAPE, origin.place,
                                                  MIRPlace(term.value), MIREdge(point.block), term.loc))
                hazards.append(MIRHazard(MIRHazardKind.RETURN_ESCAPE, MIREdge(point.block), origin.place,
                                         origins, MIRPlace(term.value)))
    return tuple(escapes), tuple(unknown), tuple(hazards)


def _store_escapes(prepared: MIRPrepared, dependencies: MIRDependencies
                   ) -> tuple[tuple[MIRStorageConflict, ...], tuple[MIRHazard, ...]]:
    """Every loan of the body's own storage among the loans it stores in an
    object the caller reaches, which outlives the body
    (`MIRDependencies.caller_stores`), at the first point it is found, and
    beside each conflict its hazard (every caller store of that place
    holding the loan)."""
    blocks = {b.id: b for b in prepared.function.blocks}
    escapes: list[MIRStorageConflict] = []
    seen: set[tuple[MIRPlace, MIRReferent]] = set()
    for store in dependencies.caller_stores:
        block = blocks[store.point.block]
        loc = (block.statements[store.point.index].loc if store.point.index < len(block.statements)
               else block.terminator.loc)
        for ref in sorted(store.escaping, key=lambda r: (r.place.root.index, _place(r.place))):
            if (store.key, ref) not in seen:
                seen.add((store.key, ref))
                escapes.append(MIRStorageConflict(MIRStorageConflictKind.STORE_ESCAPE, ref.place, store.key,
                                                  store.point, loc))
    hazards = tuple(MIRHazard(MIRHazardKind.STORE_ESCAPE, escape.site, escape.origin,
                              frozenset({MIRReferent(escape.origin)}), escape.holder,
                              stores=tuple(store for store in dependencies.caller_stores
                                           if store.key == escape.holder
                                           and any(r.place == escape.origin and not r.external
                                                   for r in store.loans)))
                    for escape in escapes)
    return tuple(escapes), hazards


@dataclass(frozen=True)
class MIREscapes:
    """The escapes of one body, from one discovery pass: the return escape
    conflicts found (`found`) and the returns whose origins are unknown,
    the store escape conflicts (`stored`), and their hazards; `gap` is the
    dependency refusal that leaves all of them unknown."""
    body: MIRBodyId
    found: tuple[MIRStorageConflict, ...] = ()
    unknown: tuple[SourceLocation | None, ...] = ()
    stored: tuple[MIRStorageConflict, ...] = ()
    hazards: tuple[MIRHazard, ...] = ()
    gap: MIRNotCovered | None = None

    @property
    def returns(self) -> tuple[MIRStorageConflict, ...] | MIRNotCovered:
        """The verdict's return escapes: unknown when a return's origins are."""
        if self.gap is not None:
            return self.gap
        if self.unknown:
            return MIRNotCovered(self.body, "return escapes", "borrowed return has unknown origins",
                                 min(self.unknown, key=source_order))
        return self.found

    @property
    def stores(self) -> tuple[MIRStorageConflict, ...] | MIRNotCovered:
        return self.gap if self.gap is not None else self.stored


def analyze_escapes(prepared: MIRPrepared) -> MIREscapes:
    """Unconditional discovery of RETURN_ESCAPE conflicts over every borrowed
    return of a lowered body (a record or view result reaching a local or a
    temporary) and of STORE_ESCAPE conflicts, independent of any
    certificate's required origins. A return with unknown origins leaves the
    verdict's return escapes unknown; the other returns' hazards stand."""
    fn, dependencies = prepared.function, prepared.dependencies
    if isinstance(dependencies, MIRNotCovered):
        return MIREscapes(fn.id, gap=dependencies)
    found, unknown, returned = _return_escapes(prepared, dependencies)
    stored, hazards = _store_escapes(prepared, dependencies)
    return MIREscapes(fn.id, found, unknown, stored, (*returned, *hazards))


def dump_escapes(returns: tuple[MIRStorageConflict, ...] | MIRNotCovered,
                 stores: tuple[MIRStorageConflict, ...] | MIRNotCovered) -> str:
    """The RETURN_ESCAPE and STORE_ESCAPE conflicts the verdict reports."""
    lines = ["escapes (the body's own storage reaching the caller; possible conflicts)"]
    for name, result in (("return escapes", returns), ("store escapes", stores)):
        if isinstance(result, MIRNotCovered):
            lines.append(f"  {name} not covered: {result.reason}")
            continue
        for escape in result:
            site = (f"bb{escape.site.source.index} return" if isinstance(escape.site, MIREdge)
                    else f"bb{escape.site.block.index} before {escape.site.index}")
            lines.append(f"  {site}: {escape.kind.name.lower()} storage:{_place(escape.origin)} "
                         f"held by {_place(escape.holder)}{_location(escape.loc)}")
    if len(lines) == 1:
        lines.append("  no escapes")
    return "\n".join(lines) + "\n"

"""Internal lifetime evidence for required body storage origins.

Composes the existing liveness, dependency, scope-end, payload-end, retention,
call-effect and presence facts over one whole validated body. This is not a
production certificate for emitted code: an adapter must still bind the exact
function and roots to the emitted THIR obligations and placement plan.

MIR itself bounds the channels: it has no field/container/global stores of
references, captures, exceptional edges or opaque calls, and every call carries
a validated summary without retention on any exit. Record cleanup is not
representable in MIR, so every body storage record needs a verified hook-free
definition from the caller.

A verdict is normal-path evidence: it follows the CFG's edges to normal
returns. A body that may exit by exception says so in
`MIRFunction.exceptional_exits`; no verdict here covers those exits.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType

from ..parse import SourceLocation
from .call_effects import analyze_call_effects
from .coverage import MIRUnsupported, owned_tuple, scalar_wrapper
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, MIRReferent, _dependencies, _leaves, resolve_referents
from .liveness import MIRLiveness, _liveness
from .nodes import (
    MIRAssign, MIRBodyKind, MIREdge, MIRFunction, MIRNotCovered, MIROptionalPayload, MIRPlace, MIRPoint,
    MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageInit,
    MIRUnionPayload, MIRValueKind,
)
from .payload_lifetime import _payload_conflicts, _payload_ends
from .presence import MIRPresenceIssueKind
from .retention import analyze_retention
from .scope_lifetime import MIRScopeEndKind, _scope_conflicts, _scope_ends
from .storage import analyze_storage
from .validate import MIRPrepared, MIRPresenceError, MIRValidationError, _prepare_function


class MIRStorageVerdict(Enum):
    CERTIFIED = auto()
    CONFLICT = auto()
    NOT_COVERED = auto()


class MIRStorageConflictKind(Enum):
    SCOPE_END = auto()
    REPLACEMENT = auto()
    PAYLOAD_END = auto()
    RETURN_ESCAPE = auto()


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


def _storage_root(slot: MIRSlot) -> bool:
    return (slot.value_kind is MIRValueKind.OWNED or owned_tuple(slot)
            or slot.kind is MIRSlotKind.LOCAL and scalar_wrapper(slot))


def certify_storage_origins(fn: MIRFunction, required: frozenset[MIRSlotId],
                            definitions: MIRDefinitions) -> MIRStorageEvidence:
    """Malformed MIR or a malformed request raises; it is never Not covered."""
    prepared, liveness, dependencies = _prepare_evidence(fn)
    return _storage_evidence(prepared, liveness, dependencies, required, definitions)


def _prepare_evidence(fn: MIRFunction) -> tuple[MIRPrepared, MIRLiveness, MIRDependencies | MIRNotCovered]:
    prepared = _prepare_function(fn)
    for issue in prepared.presence.issues:
        if issue.kind is MIRPresenceIssueKind.SELECTION:
            raise MIRPresenceError(issue.message)
    liveness = _liveness(prepared)
    return prepared, liveness, _dependencies(prepared, liveness)


def certify_borrow_operations(fn: MIRFunction, operations: frozenset[MIRPoint],
                             explicit_roots: frozenset[MIRSlotId],
                             definitions: MIRDefinitions) -> MIRBorrowEvidence:
    prepared, liveness, dependencies = _prepare_evidence(fn)
    return _borrow_evidence(prepared, liveness, dependencies, operations, explicit_roots, definitions)


def _borrow_evidence(prepared: MIRPrepared, liveness: MIRLiveness,
                     dependencies: MIRDependencies | MIRNotCovered,
                     operations: frozenset[MIRPoint], explicit_roots: frozenset[MIRSlotId],
                     definitions: MIRDefinitions) -> MIRBorrowEvidence:
    fn = prepared.function
    operations, explicit_roots = frozenset(operations), frozenset(explicit_roots)
    if not operations:
        raise MIRValidationError("borrow evidence needs demanded operations")
    slots = {s.id: s for s in fn.slots}
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
                    and slots[target.root].value_kind is MIRValueKind.BORROWED):
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
        refs = resolve_referents(holder, state, slots)
        if not refs:
            gap("demanded borrow operation has unknown origins", operation.loc)
            continue
        origins[point] = refs
        for ref in refs:
            if not ref.external:
                if ref.place.root not in slots or not _storage_root(slots[ref.place.root]):
                    gap("borrow operation reaches an unrepresented storage origin", operation.loc)
                else:
                    required.add(ref.place.root)
    roots = frozenset(required)
    conflicts, lifetime_gaps = _check_storage(prepared, liveness, dependencies, roots, definitions)
    return MIRBorrowEvidence(fn, operations, explicit_roots, roots, definitions, MappingProxyType(origins),
                             conflicts, (*gaps, *lifetime_gaps))


def _storage_evidence(prepared: MIRPrepared, liveness: MIRLiveness,
                      dependencies: MIRDependencies | MIRNotCovered,
                      required: frozenset[MIRSlotId], definitions: MIRDefinitions) -> MIRStorageEvidence:
    required = frozenset(required)
    if not required:
        raise MIRValidationError("storage evidence needs required origins")
    conflicts, gaps = _check_storage(prepared, liveness, dependencies, required, definitions)
    return MIRStorageEvidence(prepared.function, required, definitions, conflicts, gaps)


def _check_storage(prepared: MIRPrepared, liveness: MIRLiveness,
                   dependencies: MIRDependencies | MIRNotCovered,
                   required: frozenset[MIRSlotId], definitions: MIRDefinitions
                   ) -> tuple[tuple[MIRStorageConflict, ...], tuple[MIRNotCovered, ...]]:
    fn, presence = prepared.function, prepared.presence
    for result in (presence, liveness, dependencies):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("storage evidence input belongs to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    for root in required:
        if root not in slots or not _storage_root(slots[root]):
            raise MIRValidationError("required origin is not storage of this MIR body")
    conflicts: list[MIRStorageConflict] = []
    gaps: list[MIRNotCovered] = []

    def gap(reason: str, loc: SourceLocation | None = None) -> None:
        gaps.append(MIRNotCovered(fn.id, "storage evidence", reason, loc))

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
        members = ((slot.type,) if slot.value_kind is MIRValueKind.OWNED else
                   tuple(m.type for m in slot.tuple_layout.elements if m.kind is MIRValueKind.OWNED)
                   if owned_tuple(slot) else ())
        for typ in members:
            try:
                definition = definitions.get(slot, typ)
            except MIRUnsupported as failure:
                gap(f"storage record without verified hook-free definition: {failure.reason}")
                continue
            if definition.layout not in fn.records:
                gap("storage record layout differs from its definition")

    materialized = {stmt.target.root for block in fn.blocks for index, stmt in enumerate(block.statements)
                    if isinstance(stmt, (MIRAssign, MIRStorageInit)) and not stmt.target.projections
                    and MIRPoint(block.id, index) in presence.points}
    # A by-value parameter's storage is materialized by the caller, before entry.
    materialized.update(s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and _storage_root(s))
    for root in sorted(required - materialized, key=lambda s: s.index):
        gap(f"required origin %{root.index} is never materialized on a feasible path")

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
            for sid in sorted(liveness.points[point], key=lambda s: s.index):
                for leaf in _leaves(slots[sid]):
                    match leaf.projections:
                        case (MIROptionalPayload(),):
                            engaged = 1 in selected.get(sid, presence.domains[sid])
                        case (MIRUnionPayload(alternative=alternative),):
                            engaged = alternative in selected.get(sid, presence.domains[sid])
                        case _:
                            engaged = True
                    if engaged and not state.get(leaf):
                        gap(f"live borrowed holder %{sid.index} has unknown origins", point_loc(point))

    ends = _scope_ends(prepared)
    if isinstance(ends, MIRNotCovered):
        gaps.append(ends)
    else:
        for edge, events in ends.ends.items():
            engagement = dict(presence.edge_engagement.get(edge, frozenset()))
            for event in events:
                if event.kind is MIRScopeEndKind.RECORD_WRAPPER and not engagement.get(event.storage.root):
                    gap(f"optional backing %{event.storage.root.index} ends without an engagement fact",
                        blocks[edge.source].terminator.loc)
        scoped = _scope_conflicts(prepared, liveness, dependencies, ends)
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
        payloads = _payload_conflicts(prepared, liveness, dependencies, payload_ends)
        if isinstance(payloads, MIRNotCovered):
            gaps.append(payloads)
        else:
            for conflict in payloads:
                attribute(MIRStorageConflictKind.PAYLOAD_END, conflict.payload, conflict.holder,
                          conflict.point, point_loc(conflict.point))

    # The strict replacement and call-effect passes reject stale aliases; the
    # freshness gaps above already withhold certification for such a body.
    if not presence.issues:
        events = analyze_storage(fn)
        if isinstance(events, MIRNotCovered):
            gaps.append(events)
        else:
            for point, stmt in events.writes.items():
                fact = stmt.storage_write
                if (isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
                        and point in presence.points
                        and not dict(presence.engagement.get(point, frozenset())).get(stmt.target.root)):
                    gap(f"optional backing %{stmt.target.root.index} replaced without an engagement fact",
                        stmt.loc)
        retention = analyze_retention(fn, liveness, dependencies, events)
        if isinstance(retention, MIRNotCovered):
            gaps.append(retention)
        else:
            for conflict in retention.conflicts:
                if conflict.affected.external:
                    gap("replacement of external storage retained by a live holder", point_loc(conflict.point))
                else:
                    attribute(MIRStorageConflictKind.REPLACEMENT, conflict.affected.place, conflict.holder,
                              conflict.point, point_loc(conflict.point))
        effects = analyze_call_effects(fn, dependencies)
        if isinstance(effects, MIRNotCovered):
            gaps.append(effects)

    # Function exit has no successor, so liveness says nothing about a result.
    if fn.borrowed_result is not None:
        for block in fn.blocks:
            term = block.terminator
            point = MIRPoint(block.id, len(block.statements))
            state = dependencies.referents.get(point)
            # A feasible point without facts already recorded its gap above.
            if not isinstance(term, MIRReturn) or point not in presence.points or state is None:
                continue
            origins = resolve_referents(MIRPlace(term.value), state, slots)
            if not origins:
                gap("borrowed return has unknown origins", term.loc)
            for origin in sorted(origins, key=lambda r: (r.place.root.index, len(r.place.projections))):
                if not origin.external:
                    attribute(MIRStorageConflictKind.RETURN_ESCAPE, origin.place, MIRPlace(term.value),
                              MIREdge(block.id), term.loc)
    return tuple(conflicts), tuple(gaps)

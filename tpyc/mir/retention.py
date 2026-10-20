"""Possible retained-object conflicts at logical replacement events."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..thir.scalar_leaves import MIRClass, value_class
from ..typesys import TpyType

from .call_contract import MIRAliasObligation, MIRParameterWrite
from .coverage import call_place, summary_path, whole_wrapper_field
from .dependencies import (
    MIRDependencies, MIRReferent, _referent, analyze_dependencies, origin_shape, resolve_referents,
)
from .dump import _location, _place, _write_step, obligation_text
from .liveness import MIRLiveness, MIRPoint, analyze_liveness
from .nodes import (
    MIRContainerElements, MIRContainerStructure, MIRFunction, MIRNotCovered, MIRPlace, MIRRecordWrite,
    MIRRecordWriteMode, MIRSlot, MIRSlotId, MIRSlotKind, statement_call,
)
from .value import MIRField, MIRRecordLayout, MIRShape, MIRValidationError
from .storage import MIRStorageEvents, analyze_storage, owned_field
from .presence import MIREngagement
from .shapes import walk_to
from .validate import _validated_function


# An initialization creates its storage, so no loan can predate it: the
# record writes outside this set are the replacement events.
INITIALIZING_WRITES = frozenset({MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.INITIALIZE_REGION})


@dataclass(frozen=True)
class MIRRetentionConflict:
    point: MIRPoint
    affected: MIRReferent
    holder: MIRPlace
    retained: MIRReferent
    # The callee's no-alias obligation a call breaks: `holder` is the
    # argument the callee still uses while it writes `affected` through
    # another one. None for a holder of the body's own.
    call: MIRAliasObligation | None = None


@dataclass(frozen=True)
class MIRRetention:
    function: MIRFunction
    conflicts: tuple[MIRRetentionConflict, ...]


def _origin(place: MIRPlace) -> MIRPlace:
    """The object an external referent starts from: its holder leaf, before
    any field or container region inside it."""
    for index, projection in enumerate(place.projections):
        if isinstance(projection, (MIRField, MIRContainerStructure, MIRContainerElements)):
            return MIRPlace(place.root, place.projections[:index])
    return place


def may_overlap(left: MIRReferent, right: MIRReferent) -> bool:
    """Private roots are distinct; external payload origins need no
    disjointness. A stored loan of an external object views storage of no
    known place, so it may be any external storage."""
    if left.external != right.external:
        return False
    if left.external and (left.held or right.held):
        return True
    if left.external and _origin(left.place) != _origin(right.place):
        return True
    if left.place.root != right.place.root:
        return False
    a, b = left.place.projections, right.place.projections
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def _replaced_leaf(place: MIRPlace) -> bool:
    return (bool(place.projections) and isinstance(field := place.projections[-1], MIRField)
            and value_class(field.type) is MIRClass.OWNED_LEAF)


def _reach(written: MIRPlace) -> tuple[tuple[object, ...], bool] | None:
    """For a write that keeps the identity of the object around it, the
    projection path a holder's referent must lie under, and whether the
    path itself is reached; None for a write that replaces whole storage.
    A container's shape write reaches everything inside the container (its
    iterators, elements, views of them) but not the container object, which
    every holder of it keeps; an elements write reaches what lies in the
    elements region. Element identity is never tracked: a write of one
    element reaches a holder of any element."""
    match written.projections:
        case (*base, MIRContainerStructure()):
            return tuple(base), False
        case (*_, MIRContainerElements()):
            return written.projections, True
        case (*_, MIRField() as field) if owned_field(field):
            return written.projections, True
    return None


def _whole_container(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """A referent naming a container object itself (a borrowed container
    parameter's own origin), which no container holds as an element."""
    return not ref.place.projections and slots[ref.place.root].shape.whole_container


def _referent_shape(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot],
                    records: Mapping[TpyType, MIRRecordLayout]) -> MIRShape | None:
    """The object a referent names, when its path is fields only: stepped
    from its origin's object."""
    if not all(isinstance(projection, MIRField) for projection in ref.place.projections):
        return None
    return walk_to(origin_shape(slots[ref.place.root]), ref.place.projections, records)


def static_referent(ref: MIRReferent) -> bool:
    """A literal's storage, as the dependency pass marked it."""
    return ref.static


def affects(written: MIRReferent, retained: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot],
            records: Mapping[TpyType, MIRRecordLayout]) -> bool:
    """Whether replacing `written` reaches what a holder of `retained` holds.
    A literal's static storage is never written, so no replacement reaches
    it. Replacing an owned-leaf, container or record member field replaces
    that storage only: the record around it keeps its identity, so only a
    holder at or under the field is affected; a container's shape or elements write
    reaches what lies inside it (`_reach`). In private storage, or under one
    external origin, that is the path; across external origins that may
    alias, it is any holder that could lie inside the written storage: no
    record or container lives inside an opaque buffer, and no container is
    an element of another."""
    if static_referent(written) or static_referent(retained):
        return False
    if written.external != retained.external:
        return False
    # A stored loan of an external object views owned-leaf storage outside
    # the body that any external replacement may be, a sibling member of
    # the same object included (its place names the member, not what it views).
    if written.held or retained.held:
        return True
    reach = _reach(written.place)
    if reach is None:
        return may_overlap(written, retained)
    if not written.external or _origin(written.place) == _origin(retained.place):
        path, inclusive = reach
        held = retained.place.projections
        return (written.place.root == retained.place.root and held[:len(path)] == path
                and (inclusive or len(held) > len(path)))
    if not _replaced_leaf(written.place):
        # Across external origins that may alias, a container's interior or
        # a record member may hold any record or owned leaf a holder
        # retains; a container object is never an element, so only a shape
        # or elements write spares one.
        region = isinstance(written.place.projections[-1], (MIRContainerStructure, MIRContainerElements))
        return not (region and _whole_container(retained, slots))
    shape = _referent_shape(retained, slots, records)
    return shape is None or not (shape.cls is MIRClass.RECORD or _whole_container(retained, slots))


def analyze_retention(fn: MIRFunction, liveness: MIRLiveness,
                      dependencies: MIRDependencies | MIRNotCovered,
                      events: MIRStorageEvents | MIRNotCovered) -> MIRRetention | MIRNotCovered:
    prepared = _validated_function(fn)
    for result in (liveness, dependencies, events):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("retention input belongs to a different MIR function")
    for result in (dependencies, events):
        if isinstance(result, MIRNotCovered):
            return MIRNotCovered(fn.id, "retention", f"{result.node_kind}: {result.reason}", result.loc)
    slots = {slot.id: slot for slot in fn.slots}
    records = fn.record_layouts
    conflicts: list[MIRRetentionConflict] = []
    # Replacement events, each at its statement's point: assignments to
    # owned storage, the places a call's summary may write, and owned
    # storage a move empties.
    replaced: list[tuple[MIRPoint, MIRPlace, MIRSlotId | None]] = []
    for point, stmt in events.writes.items():
        fact = stmt.storage_write
        assert isinstance(fact, MIRRecordWrite)
        if fact.mode in INITIALIZING_WRITES:
            continue
        if fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN:
            engagement = prepared.presence.engagement.get(point)
            if engagement is None:
                continue
            if MIREngagement.ENGAGED not in dict(engagement).get(stmt.target.root, frozenset()):
                continue
        replaced.append((point, stmt.target, fact.rebind_owner))
    for point, places in events.call_writes.items():
        replaced.extend((point, place, None) for place in places)
    for point, place in events.moves.items():
        replaced.append((point, place, None))
    for point, target, rebind_owner in sorted(replaced, key=lambda e: (e[0].block.index, e[0].index, _place(e[1]))):
        incoming = dependencies.referents[point]
        after = MIRPoint(point.block, point.index + 1)
        outgoing = dependencies.referents[after]
        live_after = liveness.points[after]
        affected = resolve_referents(target, incoming, slots, records)
        # The statement may install a loan into the storage it replaces --
        # a callee's transfer, or the record it fills -- so the holders of
        # the state it leaves count too, and the filled object's own loans
        # before the holder rebinding to it is live.
        holders = dependencies.live(incoming, live_after)
        for leaf, refs in dependencies.live(outgoing, live_after).items():
            holders[leaf] = holders.get(leaf, frozenset()) | refs
        if not target.projections:
            for key in dependencies.objects[target.root]:
                holders[key] = holders.get(key, frozenset()) | outgoing.get(key, frozenset())
        for holder, refs in sorted(holders.items(), key=lambda item: _place(item[0])):
            if holder.root == rebind_owner:
                continue
            for written in sorted(affected, key=_referent_key):
                for retained in sorted(refs, key=_referent_key):
                    if affects(written, retained, slots, records):
                        conflicts.append(MIRRetentionConflict(point, written, holder, retained))
    conflicts.extend(_call_obligation_conflicts(fn, dependencies, slots))
    # Stable: the replacement events keep their order within a statement.
    conflicts.sort(key=lambda c: (c.point.block.index, c.point.index))
    return MIRRetention(fn, tuple(conflicts))


@dataclass(frozen=True)
class MIRRetentionAnalysis:
    """The passes retention reads, run once over one body, with its result."""
    liveness: MIRLiveness
    dependencies: MIRDependencies | MIRNotCovered
    events: MIRStorageEvents | MIRNotCovered
    retention: MIRRetention | MIRNotCovered


def analyze_body_retention(fn: MIRFunction, liveness: MIRLiveness | None = None,
                           dependencies: MIRDependencies | MIRNotCovered | None = None) -> MIRRetentionAnalysis:
    """Liveness, dependencies, storage events and retention over one body,
    reusing the passes a caller already ran."""
    liveness = liveness if liveness is not None else analyze_liveness(fn)
    dependencies = dependencies if dependencies is not None else analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    return MIRRetentionAnalysis(liveness, dependencies, events,
                                analyze_retention(fn, liveness, dependencies, events))


def _call_obligation_conflicts(fn: MIRFunction, dependencies: MIRDependencies,
                               slots: Mapping[MIRSlotId, MIRSlot]) -> list[MIRRetentionConflict]:
    """Each callee obligation a call may break: the argument the callee
    still uses is a holder live across the call -- no liveness after the
    call keeps it -- so the write the callee makes through another argument
    is checked against what that argument lends at the call point."""
    found: list[MIRRetentionConflict] = []
    records = fn.record_layouts
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            call = statement_call(stmt)
            point = MIRPoint(block.id, index)
            state = dependencies.referents.get(point)
            if call is None or not call.present_effects.obligations or state is None:
                continue
            for obligation in sorted(call.present_effects.obligations, key=_obligation_key):
                write = obligation.written
                affected = resolve_referents(call_place(call, write.parameter, write.path, slots), state, slots,
                                             records)
                holder = MIRPlace(call.arguments[obligation.parameter])
                lent = resolve_referents(call_place(call, obligation.parameter, obligation.path, slots), state, slots,
                                         records)
                for written in sorted(affected, key=_referent_key):
                    for retained in sorted(lent, key=_referent_key):
                        if affects(written, retained, slots, records):
                            found.append(MIRRetentionConflict(point, written, holder, retained, obligation))
    return found


def _obligation_key(obligation: MIRAliasObligation) -> tuple[int, str, int, str]:
    return (obligation.parameter, _path_text(obligation.path), obligation.written.parameter,
            _path_text(obligation.written.path))


def _path_text(path: tuple[object, ...]) -> str:
    return "".join(_write_step(step) for step in path)


def parameter_indices(fn: MIRFunction) -> dict[MIRSlotId, int]:
    """Each parameter slot's position, a method's receiver first: the
    index a summary names the parameter by."""
    params = (slot for slot in fn.slots if slot.kind is MIRSlotKind.PARAMETER)
    return {slot.id: index for index, slot in enumerate(params)}


def alias_obligation(conflict: MIRRetentionConflict, parameters: Mapping[MIRSlotId, int]
                     ) -> MIRAliasObligation | None:
    """The one classification of a retention conflict as a no-alias
    obligation: one whose written and retained referents are both the
    caller's storage, reached from two different parameters, exists only
    when the caller passes arguments that overlap, so each call decides it.
    A conflict under one parameter, a stored loan (`held`, whose storage
    has no known place), a global or the body's own storage stays a
    replacement. A published obligation is still a conflict of the body,
    of its own kind (`classify_retention`): only callers MIR analyzes
    check it."""
    written, retained = conflict.affected, conflict.retained
    if not (written.external and retained.external) or written.held or retained.held:
        return None
    if written.static or retained.static:
        return None
    write_origin, retained_origin = _origin(written.place), _origin(retained.place)
    if write_origin.projections or retained_origin.projections:
        return None
    j, i = parameters.get(write_origin.root), parameters.get(retained_origin.root)
    if i is None or j is None or i == j:
        return None
    return MIRAliasObligation(i, summary_path(whole_wrapper_field(retained.place)),
                              MIRParameterWrite(j, summary_path(whole_wrapper_field(written.place))))


def _one_object(conflict: MIRRetentionConflict) -> bool:
    """Whether the write certainly reaches what the holder retains: the
    body's own storage under one root, or the caller's under one origin. A
    stored loan's storage has no known place, and an element write reaches
    a holder of any element only because element identity is not tracked."""
    written, retained = conflict.affected, conflict.retained
    if written.held or retained.held or written.external != retained.external:
        return False
    if written.place.projections[-1:] == (MIRContainerElements(),):
        return False
    if written.external:
        return _origin(written.place) == _origin(retained.place)
    return written.place.root == retained.place.root


@dataclass(frozen=True)
class MIRRetentionClasses:
    """A body's retention conflicts under the obligations its summary
    publishes: those the body answers for (`replacements`), the calls that
    pass one object to two parameters of a callee that writes through one
    while it still uses the other (`argument_aliases`, `_one_object`), and
    the body's own conflicts its summary publishes as no-alias obligations
    (`alias_obligations`). Those stay conflicts of the body: callers MIR
    does not analyze never check them."""
    replacements: tuple[MIRRetentionConflict, ...]
    argument_aliases: tuple[MIRRetentionConflict, ...]
    alias_obligations: tuple[MIRRetentionConflict, ...]


def classify_retention(retention: MIRRetention, published: frozenset[MIRAliasObligation]) -> MIRRetentionClasses:
    parameters = parameter_indices(retention.function)
    replacements: list[MIRRetentionConflict] = []
    aliases: list[MIRRetentionConflict] = []
    obligations: list[MIRRetentionConflict] = []
    for conflict in retention.conflicts:
        if published and alias_obligation(conflict, parameters) in published:
            obligations.append(conflict)
        elif conflict.call is not None and _one_object(conflict):
            aliases.append(conflict)
        else:
            replacements.append(conflict)
    return MIRRetentionClasses(tuple(replacements), tuple(aliases), tuple(obligations))


def _referent_key(ref: MIRReferent) -> tuple[bool, str]:
    return ref.external, _place(ref.place)


def dump_retention(result: MIRRetention | MIRNotCovered,
                   published: frozenset[MIRAliasObligation] = frozenset()) -> str:
    if isinstance(result, MIRNotCovered):
        return f"retention not covered: {result.reason}\n"
    blocks = {block.id: block for block in result.function.blocks}
    published_conflicts = set(classify_retention(result, published).alias_obligations)
    lines = ["retention (possible logical-object conflicts; no lifetime-safety verdict)"]
    if not result.conflicts:
        lines.append("  no conflicts in covered replacement events")
    for conflict in result.conflicts:
        point = conflict.point
        loc = blocks[point.block].statements[point.index].loc
        affected, retained = _referent(conflict.affected), _referent(conflict.retained)
        notes = ""
        if conflict.call is not None:
            notes += f" (call no-alias {obligation_text(conflict.call)})"
        if conflict in published_conflicts:
            notes += " [published obligation]"
        lines.append(f"  bb{point.block.index} before {point.index}: replace {affected}; "
                     f"{_place(conflict.holder)} retains {retained}{notes}{_location(loc)}")
    return "\n".join(lines) + "\n"


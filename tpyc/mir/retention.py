"""Possible retained-object conflicts at logical replacement events."""

from collections.abc import Mapping
from dataclasses import dataclass

from .call_contract import MIRAliasObligation, MIRParameterWrite, summary_path
from .dependencies import MIRDependencies, MIRReferent, _leaves, _referent, resolve_referents, whole_wrapper_field
from .place_text import _location, _place, _write_step, obligation_text
from .liveness import MIRPoint
from .nodes import MIRFunction, MIRNotCovered, MIRPlace, MIRPresentCall, MIRSlot, MIRSlotId, MIRSlotKind, call_place
from .value import MIRRecords, MIRValidationError
from .prepare import MIRPrepared, validated_presence
from .reach import DistinctExternal, _origin, parameter_object, reaches, replacement_events, storage_exclusions
from .shapes import distinct_lent_objects
from .validate import reachable_blocks
from .witness import MIRHazard, MIRHazardKind


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
    # One per replaced place and holder with a conflict, for the witness.
    hazards: tuple[MIRHazard, ...] = ()


def static_referent(ref: MIRReferent) -> bool:
    """A literal's storage, as the dependency pass marked it."""
    return ref.static


def _nothing_selected(place: MIRPlace) -> frozenset[int]:
    return frozenset()


def analyze_retention(prepared: MIRPrepared) -> MIRRetention | MIRNotCovered:
    fn, liveness, dependencies = prepared.function, prepared.liveness, prepared.dependencies
    presence = validated_presence(prepared)
    for result in (liveness, dependencies):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("retention input belongs to a different MIR function")
    if isinstance(dependencies, MIRNotCovered):
        return MIRNotCovered(fn.id, "retention", f"{dependencies.node_kind}: {dependencies.reason}",
                             dependencies.loc)
    slots = {slot.id: slot for slot in fn.slots}
    records = fn.record_layouts
    exclusions = storage_exclusions(slots, records)
    conflicts: list[MIRRetentionConflict] = []
    hazards: list[MIRHazard] = []
    # The storage each reachable statement replaces (`replacement_events`),
    # at its point, the call's writes as the presence sees them.
    reached = reachable_blocks(fn)
    replaced: list[tuple[MIRPoint, MIRPlace, MIRSlotId | None]] = []
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            point = MIRPoint(block.id, index)
            called = presence.calls.get(point)
            # No feasible path reaches a point without selection facts: no
            # sum write there ends a payload.
            facts = presence.points.get(point)
            selected = dict(facts).get if facts is not None else _nothing_selected
            found = replacement_events(stmt, called.writes if called is not None else (), selected, slots)
            if found is None:
                return MIRNotCovered(fn.id, "retention", "storage: missing storage write fact", stmt.loc)
            if block.id in reached:
                replaced.extend((point, place, owner) for place, owner in found)
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
        live_in = dependencies.live(incoming, live_after)
        live_out = dependencies.live(outgoing, live_after)
        holders = dict(live_in)
        for leaf, refs in live_out.items():
            holders[leaf] = holders.get(leaf, frozenset()) | refs
        # The stored loans of the replaced object: a root's, or those under a replaced member place.
        for key in dependencies.objects[target.root]:
            if key.projections[:len(target.projections)] == target.projections:
                holders[key] = holders.get(key, frozenset()) | outgoing.get(key, frozenset())
        # The rebinding holder itself repoints to the new value; any other
        # holder rooted at its slot (a view member of the record it lends)
        # still retains what the write replaces.
        rebinding = frozenset(_leaves(slots[rebind_owner], slots)) if rebind_owner is not None else frozenset()
        for holder, refs in sorted(holders.items(), key=lambda item: _place(item[0])):
            if holder in rebinding:
                continue
            found = [MIRRetentionConflict(point, written, holder, retained)
                     for written in sorted(affected, key=_referent_key)
                     for retained in sorted(refs, key=_referent_key) if reaches(written, retained, exclusions)]
            conflicts.extend(found)
            if found:
                hazards.append(MIRHazard(MIRHazardKind.REPLACEMENT, point, target, affected, holder,
                                         (live_in.get(holder, frozenset()), live_out.get(holder, frozenset())),
                                         conflicts=tuple(found)))
    conflicts.extend(_call_obligation_conflicts(fn, dependencies, slots, presence.calls, exclusions))
    # Stable: the replacement events keep their order within a statement.
    conflicts.sort(key=lambda c: (c.point.block.index, c.point.index))
    return MIRRetention(fn, tuple(conflicts), tuple(hazards))


def _call_obligation_conflicts(fn: MIRFunction, dependencies: MIRDependencies,
                               slots: Mapping[MIRSlotId, MIRSlot],
                               present: Mapping[MIRPoint, MIRPresentCall],
                               exclusions: DistinctExternal) -> list[MIRRetentionConflict]:
    """Each callee obligation a call may break: the argument the callee
    still uses is a holder live across the call -- no liveness after the
    call keeps it -- so the write the callee makes through another argument
    is checked against what that argument lends at the call point."""
    found: list[MIRRetentionConflict] = []
    records = fn.record_layouts
    # Point order, so the conflicts keep the order the blocks give them.
    for point, called in sorted(present.items(), key=lambda item: (item[0].block.index, item[0].index)):
        state = dependencies.referents.get(point)
        if not called.effects.obligations or state is None:
            continue
        call = called.call
        for obligation in sorted(called.effects.obligations, key=_obligation_key):
            write = obligation.written
            affected = resolve_referents(call_place(call, write.parameter, write.path, slots), state, slots, records)
            holder = MIRPlace(call.arguments[obligation.parameter])
            lent = resolve_referents(call_place(call, obligation.parameter, obligation.path, slots), state, slots,
                                     records)
            for written in sorted(affected, key=_referent_key):
                for retained in sorted(lent, key=_referent_key):
                    if reaches(written, retained, exclusions):
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


def alias_obligation(conflict: MIRRetentionConflict, parameters: Mapping[MIRSlotId, int],
                     slots: Mapping[MIRSlotId, MIRSlot],
                     records: MIRRecords) -> MIRAliasObligation | str | None:
    """The one classification of a retention conflict against the caller:
    one whose written and retained referents both lie in objects the
    parameters lend, in two of them (two parameters, or two members of one
    tuple parameter), exists only when the caller passes arguments that
    overlap, so each call decides it -- a no-alias obligation. None for a
    conflict the body has whatever the caller passes: on the body's own
    storage, or inside one lent object. A reason for one the caller's
    arguments can create but no obligation can state -- a stored loan
    (`held`, whose storage the caller chooses) or a global on either side:
    a KNOWN summary that dropped it would let the caller look covered.
    A published obligation is still a conflict of the body, of its own
    kind (`classify_retention`): only callers MIR analyzes check it."""
    written, retained = conflict.affected, conflict.retained
    if written.held or retained.held:
        return "a stored loan the caller chooses"
    # `reaches` never pairs the body's storage with the caller's, nor static
    # storage: a conflict off the caller's storage is the body's own.
    if not (written.external and retained.external):
        return None
    j, i = parameters.get(written.place.root), parameters.get(retained.place.root)
    if i is None or j is None:
        return "a global against the caller's arguments"
    write_object, retained_object = parameter_object(written.place, slots), parameter_object(retained.place, slots)
    if write_object is None or retained_object is None:
        return "a parameter object the body does not describe"
    if i == j and not (write_object and retained_object and distinct_lent_objects(
            slots[written.place.root].shape, write_object, retained_object)):
        return None
    retained_path = whole_wrapper_field(retained.place, slots, records).projections
    written_path = whole_wrapper_field(written.place, slots, records).projections
    return MIRAliasObligation(i, summary_path(retained_path), MIRParameterWrite(j, summary_path(written_path)))


def _one_object(conflict: MIRRetentionConflict) -> bool:
    """Whether the write may reach what the holder retains as one object:
    the body's own storage under one root, or the caller's under one
    origin. A stored loan's storage has no known place. An `[elements]`
    step is a step like any other: element identity is not tracked, so two
    places under one origin through it may be one element."""
    written, retained = conflict.affected, conflict.retained
    if written.held or retained.held or written.external != retained.external:
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
    slots = {slot.id: slot for slot in retention.function.slots}
    replacements: list[MIRRetentionConflict] = []
    aliases: list[MIRRetentionConflict] = []
    obligations: list[MIRRetentionConflict] = []
    for conflict in retention.conflicts:
        if published and alias_obligation(conflict, parameters, slots, retention.function.record_layouts) in published:
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


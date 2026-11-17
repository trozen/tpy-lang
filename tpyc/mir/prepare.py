"""The one preparation of a MIR body: presence without field selections,
liveness, dependencies over that presence, presence with field selections
(each killed where a write reaches it, on those dependencies' referents),
and dependencies again over the final presence. It is built once per
lowered body, which carries it; every later pass takes it as an input."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .dependencies import MIRDependencies, MIRUnseededLoan, _dependencies, resolve_referents, strong_write
from .liveness import MIRLiveness, _liveness
from .nodes import MIRAssign, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRSlot, MIRSlotId, MIRStatement
from .presence import MIRPresence, MIRPresenceIssueKind, Resolve, _analyze_presence
from .reach import MIRReferent, replacement_events, storage_events, sum_payload_ends
from .region_flow import MIRRegionFlow
from .validate import MIRPresenceError, _validate_structure
from .value import MIRRecords, MIRValidationError


@dataclass(frozen=True)
class MIRReplacement:
    """One storage a statement replaces (`reach.replacement_events`): the
    replaced place, the slot whose holder leaves the write rebinds (None
    for none), and the storage the place reaches before the statement."""
    place: MIRPlace
    rebinds: MIRSlotId | None
    storage: frozenset[MIRReferent]

    @property
    def strong(self) -> bool:
        """The write lands on one object, so what it replaces is certainly
        gone (`dependencies.strong_write`); several may each be spared."""
        return strong_write(self.storage)


@dataclass(frozen=True)
class MIRStatementKills:
    """What each statement ends, decided once; later passes read it. Over
    the feasible statements: the storage each replaces, in event order
    (empty when the dependencies refuse, since a replaced place resolves
    over them), the storage outside the body it may leave alone (`spared`:
    a write that may land on several objects, `MIRReplacement.strong`, so
    not one of them is certainly replaced), and the inline scalar payloads
    each ends. `missing`: the first statement with no storage write fact
    where replaced storage may be borrowed -- the replacements are then
    incomplete."""
    replacements: Mapping[MIRPoint, tuple[MIRReplacement, ...]]
    payloads: Mapping[MIRPoint, frozenset[MIRPlace]]
    spared: Mapping[MIRPoint, frozenset[MIRReferent]] = MappingProxyType({})
    missing: MIRStatement | None = None


@dataclass(frozen=True)
class MIRPrepared:
    """A body's complete preparation: the final presence (field selections
    proven), the liveness and the dependencies over that presence, the
    body's region transitions and its statement kills, which every later
    pass reads."""
    function: MIRFunction
    presence: MIRPresence
    liveness: MIRLiveness
    dependencies: MIRDependencies | MIRNotCovered
    regions: MIRRegionFlow
    kills: MIRStatementKills


@dataclass(frozen=True)
class _FirstPhase:
    """A body's structure checked and its presence solved without field
    selections: what liveness and the first dependency run read."""
    function: MIRFunction
    presence: MIRPresence
    regions: MIRRegionFlow


def _require_selections(presence: MIRPresence) -> None:
    for issue in presence.issues:
        if issue.kind is MIRPresenceIssueKind.SELECTION:
            raise MIRPresenceError(issue.message)


def first_phase(fn: MIRFunction) -> _FirstPhase:
    """The structure check and the presence without field selections."""
    regions = _validate_structure(fn)
    return _FirstPhase(fn, _analyze_presence(fn, None, regions), regions)


def _resolver(dependencies: MIRDependencies, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> Resolve:
    """The referents of a place before the statement at a point; None where
    no state is known or a stored loan the place reads has no entry."""
    def resolve(point: MIRPoint, place: MIRPlace) -> frozenset[MIRReferent] | None:
        state = dependencies.referents.get(point)
        if state is None:
            return None
        try:
            return resolve_referents(place, state, slots, records)
        except MIRUnseededLoan:
            return None
    return resolve


def _nothing_selected(place: MIRPlace) -> frozenset[int]:
    return frozenset()


def _statement_kills(fn: MIRFunction, presence: MIRPresence,
                     dependencies: MIRDependencies | MIRNotCovered) -> MIRStatementKills:
    slots = {s.id: s for s in fn.slots}
    replacements: dict[MIRPoint, tuple[MIRReplacement, ...]] = {}
    payloads: dict[MIRPoint, frozenset[MIRPlace]] = {}
    spared: dict[MIRPoint, frozenset[MIRReferent]] = {}
    missing: MIRStatement | None = None
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
                missing = missing or stmt
            elif found and facts is not None and not isinstance(dependencies, MIRNotCovered):
                state = dependencies.referents[point]
                replaced = replacements[point] = tuple(
                    MIRReplacement(place, owner, resolve_referents(place, state, slots, fn.record_layouts))
                    for place, owner in found)
                certain = {r for replacement in replaced if replacement.strong for r in replacement.storage}
                if left := frozenset(r for replacement in replaced if not replacement.strong
                                     for r in replacement.storage if r.external and r not in certain):
                    spared[point] = left
            # A whole sum write's scalar payloads (`storage_events`), weighed
            # by the one rule replacement reads too (`sum_payload_ends`).
            written = [event for event in storage_events(stmt, (), slots) if event.payloads]
            if not written:
                continue
            assert isinstance(stmt, MIRAssign)
            # The validator demands the fact on every whole write of an inline sum.
            if not all(event.known for event in written):
                raise MIRValidationError("inline sum write without its storage write fact")
            if facts is None:
                continue
            ended = {place for event in written for place, owns in sum_payload_ends(event, stmt.value, selected)
                     if not owns}
            if ended:
                payloads[point] = frozenset(ended)
    return MIRStatementKills(MappingProxyType(replacements), MappingProxyType(payloads), MappingProxyType(spared),
                             missing)


def prepare(fn: MIRFunction, first: _FirstPhase | None = None) -> MIRPrepared | MIRNotCovered:
    """The body's preparation, from its first phase (`first_phase`, run
    here unless the caller already ran it), or the first dependency run's
    refusal: the body's verdict is then that refusal, and no field
    selection is proven (the second presence is not run).

    Liveness is computed once, over the first phase: a backward structural
    analysis that reads no presence fact. The field-selection kill reads
    the first dependency run's referents, and is sound for that: it reads
    holder leaves only, field selections can only remove points and edges
    and add absent call arguments, so a non-empty holder set can only
    shrink in the final presence -- and an empty one kills. The
    dependencies every later pass reads run again over the final presence.
    A MIR exception in either dependency run propagates."""
    first = first if first is not None else first_phase(fn)
    if first.function is not fn:
        raise MIRValidationError("first preparation phase belongs to a different MIR function")
    # A selection the first phase cannot prove refuses before any dependency fact.
    _require_selections(first.presence)
    liveness = _liveness(fn)
    regions = first.regions
    found = _dependencies(first.presence, liveness, regions)
    if isinstance(found, MIRNotCovered):
        return found
    slots = {slot.id: slot for slot in fn.slots}
    presence = _analyze_presence(fn, _resolver(found, slots, fn.record_layouts), regions)
    _require_selections(presence)
    dependencies = _dependencies(presence, liveness, regions)
    return MIRPrepared(fn, presence, liveness, dependencies, regions, _statement_kills(fn, presence, dependencies))


def validated(fn: MIRFunction, first: _FirstPhase | None = None) -> MIRPrepared | MIRNotCovered:
    """`prepare`, refusing any presence issue -- a stale payload alias
    included -- of either presence, the first phase's before any
    dependency fact."""
    first = first if first is not None else first_phase(fn)
    if first.presence.issues:
        raise MIRPresenceError(first.presence.issues[0].message)
    outcome = prepare(fn, first)
    if isinstance(outcome, MIRPrepared) and outcome.presence.issues:
        raise MIRPresenceError(outcome.presence.issues[0].message)
    return outcome


def validate_function(fn: MIRFunction) -> None:
    """The full check of a body: structure, presence, and every field
    selection proven -- a dependency refusal leaves them unproven."""
    outcome = validated(fn)
    if isinstance(outcome, MIRNotCovered):
        raise MIRPresenceError(f"field selections not proven: {outcome.node_kind}: {outcome.reason}")


def validated_presence(prepared: MIRPrepared) -> MIRPresence:
    """The final presence, for a pass that reads it only when no presence
    issue (a stale payload alias) stands."""
    if prepared.presence.issues:
        raise MIRPresenceError(prepared.presence.issues[0].message)
    return prepared.presence

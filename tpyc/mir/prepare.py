"""The one preparation of a MIR body: presence without field selections,
liveness, dependencies over that presence, presence with field selections
(each killed where a write reaches it, on those dependencies' referents),
and dependencies again over the final presence. It is built once per
lowered body, which carries it; every later pass takes it as an input."""

from collections.abc import Mapping
from dataclasses import dataclass

from .dependencies import MIRDependencies, MIRUnseededLoan, _dependencies, resolve_referents
from .liveness import MIRLiveness, _liveness
from .nodes import MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRSlot, MIRSlotId
from .presence import MIRPresence, MIRPresenceIssueKind, Resolve, _analyze_presence
from .reach import MIRReferent
from .validate import MIRPresenceError, _validate_structure
from .value import MIRRecords, MIRValidationError


@dataclass(frozen=True)
class MIRPrepared:
    """A body's complete preparation: the final presence (field selections
    proven), the liveness and the dependencies over that presence."""
    function: MIRFunction
    presence: MIRPresence
    liveness: MIRLiveness
    dependencies: MIRDependencies | MIRNotCovered


@dataclass(frozen=True)
class _FirstPhase:
    """A body's structure checked and its presence solved without field
    selections: what liveness and the first dependency run read."""
    function: MIRFunction
    presence: MIRPresence


def _require_selections(presence: MIRPresence) -> None:
    for issue in presence.issues:
        if issue.kind is MIRPresenceIssueKind.SELECTION:
            raise MIRPresenceError(issue.message)


def first_phase(fn: MIRFunction) -> _FirstPhase:
    """The structure check and the presence without field selections."""
    _validate_structure(fn)
    return _FirstPhase(fn, _analyze_presence(fn, None))


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
    found = _dependencies(first.presence, liveness)
    if isinstance(found, MIRNotCovered):
        return found
    slots = {slot.id: slot for slot in fn.slots}
    presence = _analyze_presence(fn, _resolver(found, slots, fn.record_layouts))
    _require_selections(presence)
    return MIRPrepared(fn, presence, liveness, _dependencies(presence, liveness))


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

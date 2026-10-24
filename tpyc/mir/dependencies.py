"""Possible referents and live dependencies, without lifetime-safety verdicts."""

from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from ..parse import SourceLocation
from .call_contract import (
    MIRLoanTransfer, MIRResultHolder, MIRStaticLoan, owned_aggregate_result, path_step, return_endpoint_problem,
)
from ..thir.scalar_leaves import (
    MIRClass, holds_loan, value_class,
)
from ..typesys import NominalType, TpyType, unwrap_readonly
from .place_text import _place
from .liveness import MIRLiveness, MIRPoint
from .nodes import (
    MIRAlias, MIRAssign, MIRCallStmt, MIRStatement, MIRBlockId, MIRBorrow, MIRCall, MIRCompare, MIRConstant,
    MIRConstruct, MIRCopy, MIRFunction, MIRIsAlternative, MIRMove, MIRNot, MIRNotCovered, MIRPlace, MIRRead, MIRSlot,
    MIRSlotId, MIRSlotKind, MIRTupleConstruct, MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead,
    MIRIteratorAdvance, MIRRangeAdvance, MIROp, MIRReturn, statement_target, call_place, place_walk, view_member,
    MIRWrap, place_shape, MIRPresentCall,
)
from .value import (
    MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRecords, MIRShape, MIRValidationError,
    loan_field, member_types, WRAPPER_CLASSES,
)
from .shapes import (
    REGION_STEPS, MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload,
    object_prefix, optional_payload, wrapper_members,
)
from .validate import successors
from .presence import MIRPresence
from .reach import MIRReferent, origin, parameter_objects, referent_walk
from .region_flow import MIRRegionFlow, outgoing_edges


class _DependencyRefusal(Exception):
    """A fact the dependency pass cannot establish: the body is not covered."""

    def __init__(self, reason: str, loc: SourceLocation | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.loc = loc


class MIRUnseededLoan(_DependencyRefusal):
    """A record object's stored loan with no entry in the dependency state
    where one is read, carried over, or must be filled (`key` None for a
    fill with no source object): never read as an empty set of origins."""

    def __init__(self, key: MIRPlace | None,
                 reason: str = "view member read of an object with no stored loan") -> None:
        super().__init__(reason)
        self.key = key


MIRReferents = Mapping[MIRPlace, frozenset[MIRReferent]]


def seeded_loan(key: MIRPlace) -> MIRReferent:
    """The loan a stored-loan entry of an object the caller reaches holds at
    entry: the caller's own, on any storage outside the body."""
    return MIRReferent(key, external=True, held=True)


@dataclass(frozen=True)
class MIRCallerStore:
    """What a stored-loan entry of an object the caller reaches holds at one
    feasible point: an entry of a parameter's object (`caller_keys`) at
    every point -- an exceptional exit may follow any statement -- or, with
    `result`, an entry of the record an owned-record return hands over, at
    that return."""
    key: MIRPlace
    point: MIRPoint
    loans: frozenset[MIRReferent]
    result: bool = False

    @property
    def escaping(self) -> frozenset[MIRReferent]:
        """The loans of the body's own storage: a store escape."""
        return frozenset(ref for ref in self.loans if not ref.external)


def _referent(ref: MIRReferent) -> str:
    return ("held:" if ref.held else "external:" if ref.external else "storage:") + _place(ref.place)


@dataclass(frozen=True)
class MIRDependencies:
    function: MIRFunction
    referents: Mapping[MIRPoint, MIRReferents]
    active: Mapping[MIRPoint, MIRReferents]
    holders: Mapping[MIRPoint, Mapping[MIRReferent, frozenset[MIRPlace]]]
    entry_active: MIRReferents
    # Each slot's record-object stored-loan entries (`object_keys`), and
    # their union: what `live_holders` reaches through an object.
    objects: Mapping[MIRSlotId, tuple[MIRPlace, ...]]
    stored_loans: frozenset[MIRPlace]
    # The stored-loan entries of objects the caller reaches (`caller_keys`):
    # what the body stores there outlives the body.
    pinned: frozenset[MIRPlace] = frozenset()
    # What those entries, and the returned record's, hold (`caller_stores`):
    # the one source of the summary's transfers and the store escapes.
    caller_stores: tuple[MIRCallerStore, ...] = ()

    def live(self, state: MIRReferents, live: frozenset[MIRSlotId]) -> dict[MIRPlace, frozenset[MIRReferent]]:
        """`live_entries` of a state of this body."""
        return live_entries(state, live, self.stored_loans, self.pinned)


def wrapper_leaves(place: MIRPlace, shape: MIRShape) -> tuple[MIRPlace, ...]:
    """The dependency leaves of a wrapper at `place`: its members that are
    holders themselves (a pointer at a record); an inert leaf or inline
    storage holds no loan."""
    if shape.cls not in WRAPPER_CLASSES:
        raise MIRValidationError("unknown wrapper layout")
    return tuple(MIRPlace(place.root, (*place.projections, selector))
                 for selector, m in wrapper_members(shape) if m.is_holder)


def _leaves(slot: MIRSlot, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[MIRPlace, ...]:
    """A slot's dependency leaves: a holder of storage elsewhere is one
    itself, a wrapper's are its holder members; no loan starts, passes
    through or ends at an inert leaf or at owned storage."""
    shape = slot.shape
    if shape.is_holder:
        return (MIRPlace(slot.id),)
    if shape.cls in (MIRClass.TUPLE, MIRClass.OPTIONAL, MIRClass.UNION):
        return wrapper_leaves(MIRPlace(slot.id), shape)
    if shape.is_scalar or shape.is_storage:
        return ()
    raise MIRValidationError("unknown dependency holder kind")


def _rebased(leaf: MIRPlace, wrapper: MIRPlace, source: MIRPlace) -> MIRPlace:
    """The leaf of `source` a copy of it into `wrapper` writes `leaf` from."""
    return MIRPlace(source.root, (*source.projections, *leaf.projections[len(wrapper.projections):]))


def object_keys(slot: MIRSlot, layouts: Mapping[TpyType, MIRRecordLayout]) -> tuple[MIRPlace, ...]:
    """The stored-loan entries of a record object a slot names: owned
    record storage (the body's own object) or a borrowed record parameter
    (the caller's object), one per view member, keyed at the member place --
    through inline record members, whose layouts come with the record's."""
    if slot.shape.is_storage and slot.shape.cls is MIRClass.RECORD:
        return _member_keys(MIRPlace(slot.id), layouts.get(slot.type), layouts)
    # Each object a parameter lends is keyed at its origin (`origin`).
    return tuple(key for prefix, lent in parameter_objects(slot)
                 for key in _member_keys(MIRPlace(slot.id, prefix), layouts.get(lent.type), layouts))


def _member_keys(place: MIRPlace, layout: MIRRecordLayout | None,
                 layouts: Mapping[TpyType, MIRRecordLayout]) -> tuple[MIRPlace, ...]:
    if layout is None:
        return ()
    keys: list[MIRPlace] = []
    for f in layout.fields:
        member = MIRPlace(place.root, (*place.projections, f))
        bare = unwrap_readonly(f.type)
        if loan_field(f.type):
            keys.append(member)
        elif value_class(f.type) is MIRClass.RECORD:
            if bare not in layouts:
                raise MIRValidationError("record field needs its layout")
            keys.extend(_member_keys(member, layouts[bare], layouts))
    return tuple(keys)


def live_holders(state: MIRReferents, live: frozenset[MIRSlotId],
                 stored_loans: frozenset[MIRPlace]) -> frozenset[MIRPlace]:
    """The state entries live at a point: a holder whose slot is live, and
    a record object's stored loan (one of `stored_loans`) whose object a
    live entry reaches (its referent's place is a prefix of the entry),
    transitively."""
    reached = {leaf for leaf in state if leaf.root in live}
    objects: dict[MIRSlotId, list[MIRPlace]] = {}
    for leaf in state:
        if leaf in stored_loans and leaf not in reached:
            objects.setdefault(leaf.root, []).append(leaf)
    if not objects:
        return frozenset(reached)
    pending = list(reached)
    while pending:
        for ref in state[pending.pop()]:
            prefix = ref.place.projections
            for leaf in objects.get(ref.place.root, ()):
                if leaf not in reached and leaf.projections[:len(prefix)] == prefix:
                    reached.add(leaf)
                    pending.append(leaf)
    return frozenset(reached)


def live_entries(state: MIRReferents, live: frozenset[MIRSlotId], stored_loans: frozenset[MIRPlace],
                 pinned: frozenset[MIRPlace] = frozenset()) -> dict[MIRPlace, frozenset[MIRReferent]]:
    """The state entries live at a point and what each holds: those
    `live_holders` reaches, and the loans the body stored in an object the
    caller reaches (one of `pinned`), live to every exit. The loan such an
    entry was seeded with at entry is the caller's own, which the caller
    checks against its own replacements."""
    reached = live_holders(state, live, stored_loans)
    entries = {leaf: state[leaf] for leaf in reached}
    for key in pinned:
        if key not in reached and key in state:
            stored = state[key] - {seeded_loan(key)}
            if stored:
                entries[key] = stored
    return entries


def caller_keys(fn: MIRFunction, objects: Mapping[MIRSlotId, tuple[MIRPlace, ...]]) -> frozenset[MIRPlace]:
    """The stored-loan entries of the objects a caller reaches: a borrowed
    record parameter's (the constructor's receiver included) and an
    `Own[R]` parameter's storage, which binds the caller's temporary."""
    return frozenset(key for slot in fn.slots if slot.kind is MIRSlotKind.PARAMETER for key in objects[slot.id])


def _coverage(fn: MIRFunction) -> tuple[str, MIRSlotId | None] | None:
    """Why the body is not covered, and the slot the reason names."""
    slots = {s.id: s for s in fn.slots}
    for slot in fn.slots:
        root = (slot if slot.shape.is_storage or slot.shape.owned_tuple else
                slots[slot.alias_source.root] if slot.alias_source is not None else None)
        if root is not None and root.storage_duration is None:
            return f"missing storage duration for %{root.id.index}", root.id
    places = [s.alias_source for s in fn.slots if s.alias_source is not None]
    for block in fn.blocks:
        for stmt in block.statements:
            if (target := statement_target(stmt)) is not None:
                places.append(target)
            if not isinstance(stmt, MIRAssign):
                continue
            match stmt.value:
                case MIRBorrow(source=p) | MIRRead(source=p) | MIRCopy(source=p) | MIRUnionExtract(source=p):
                    places.append(p)
    graph: dict[NominalType, set[NominalType]] = {}
    for place in places:
        for projection in place.projections:
            if isinstance(projection, MIRField) and value_class(typ := unwrap_readonly(projection.type)) is (
                    MIRClass.RECORD):
                graph.setdefault(projection.id.owner, set()).add(typ)
                graph.setdefault(typ, set())
    degree = {typ: 0 for typ in graph}
    for children in graph.values():
        for typ in children:
            degree[typ] += 1
    pending = [typ for typ, count in degree.items() if count == 0]
    count = 0
    while pending:
        typ = pending.pop()
        count += 1
        for child in graph[typ]:
            degree[child] -= 1
            if degree[child] == 0:
                pending.append(child)
    return ("recursive inline field paths", None) if count != len(graph) else None


def member_keys(objects: frozenset[MIRReferent], member: MIRField, state: MIRReferents,
                reason: str = "view member read of an object with no stored loan") -> list[MIRPlace]:
    """The stored-loan entry of view member `member` of each record object
    in `objects`, every one present: one with no entry refuses (`reason`)."""
    keys = [MIRPlace(o.place.root, (*o.place.projections, member)) for o in objects]
    for key in keys:
        if key not in state:
            raise MIRUnseededLoan(key, reason)
    return keys


def _stored_loans(refs: frozenset[MIRReferent], member: MIRField, state: MIRReferents) -> frozenset[MIRReferent]:
    """What a view member of each record object in `refs` stores: the
    object's stored-loan entry, set where the object is built (the body's
    own) or seeded at entry (a parameter's)."""
    return frozenset(loan for key in member_keys(refs, member, state) for loan in state[key])


def _project(refs: frozenset[MIRReferent],
             *projections: MIRField | MIRUnionPayload | MIRContainerStructure | MIRContainerElements
             ) -> frozenset[MIRReferent]:
    return frozenset(MIRReferent(MIRPlace(r.place.root, (*r.place.projections, projection)), r.external)
                     for r in refs for projection in projections)


def whole_wrapper_field(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> MIRPlace:
    """An external referent's place under an Optional record field's
    payload, published as the whole field: a summary path selects no
    inline field payload, and replacing the field reaches everything a
    payload write does. A wrapper member's selector after the root is the
    object the parameter lends, kept."""
    if not any(isinstance(projection, MIRUnionPayload) for projection in place.projections):
        return place
    index = 0
    previous: object = None
    for hop in referent_walk(place, slots, records).hops:
        if isinstance(hop.projection, MIRDeref):
            continue
        if isinstance(previous, MIRField) and optional_payload(hop.holder.cls, hop.projection):
            return MIRPlace(place.root, place.projections[:index])
        previous = hop.projection
        index += 1
    return place


def resolve_referents(place: MIRPlace, state: MIRReferents, slots: Mapping[MIRSlotId, MIRSlot],
                      records: Mapping[TpyType, MIRRecordLayout]) -> frozenset[MIRReferent]:
    """Resolve a validated place against the referents at its program point."""
    empty: frozenset[MIRReferent] = frozenset()
    # Payload selectors address a holder leaf; dereference follows its value.
    leaf = MIRPlace(place.root)
    # A global's storage lies outside the body, where a parameter may reach it.
    external = slots[place.root].kind is MIRSlotKind.GLOBAL
    refs = (frozenset({MIRReferent(leaf, external)})
            if slots[place.root].shape.is_storage else state.get(leaf, empty))
    # A container view's referents already are an elements region: a container
    # projection on the view itself names that region, never a region of it.
    through_view = slots[place.root].shape.container_view_holder
    for hop in place_walk(place, slots, records).hops:
        match projection := hop.projection:
            case MIRTupleIndex():
                leaf = MIRPlace(leaf.root, (*leaf.projections, projection))
                refs = (frozenset({MIRReferent(leaf, external)}) if hop.reached.is_storage
                        else state.get(leaf, empty))
            case MIRUnionPayload() if hop.reached.is_storage:
                # An inline payload is storage under the record that holds it.
                leaf = MIRPlace(leaf.root, (*leaf.projections, projection))
                refs = _project(refs, projection)
            case MIRUnionPayload():
                leaf = MIRPlace(leaf.root, (*leaf.projections, projection))
                refs = state.get(leaf, empty)
            case MIRDeref():
                pass
            case MIRField() if loan_field(projection.type):
                # A view member is read whole (`place_info` allows nothing after it).
                refs = _stored_loans(refs, projection, state)
            case MIRContainerStructure() | MIRContainerElements() if through_view:
                pass
            case MIRField() | MIRContainerStructure() | MIRContainerElements():
                through_view = False
                refs = _project(refs, projection)
            case _:
                raise MIRValidationError("unknown dependency projection")
    return refs


def _strong_write(objects: frozenset[MIRReferent], slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether a write through a place reaching `objects` replaces their
    stored loans: it reaches exactly one object, and the body owns it. A
    write into one external object stays a join: conservative, since the
    write lands on that object, but its entry keeps the caller's seeded loan
    (TODO.md, the precision item on whole-member replacement through a
    borrowed parameter)."""
    only = next(iter(objects)) if len(objects) == 1 else None
    return only is not None and not only.external and slots[only.place.root].shape.is_storage


def _write_entries(objects: frozenset[MIRReferent], loans: Mapping[tuple[MIRField, ...], frozenset[MIRReferent]],
                   state: dict[MIRPlace, frozenset[MIRReferent]], slots: Mapping[MIRSlotId, MIRSlot],
                   stored_loans: frozenset[MIRPlace], reason: str) -> None:
    """Each object in `objects` stores `loans` at the entries under it
    (keyed by member path): replaced when the write is strong
    (`_strong_write`), else joined, which needs the entry's current loans.
    An entry no object has refuses (`reason`); one left empty is dropped,
    so a later read of it refuses."""
    strong = _strong_write(objects, slots)
    writes: dict[MIRPlace, frozenset[MIRReferent]] = {}
    for obj in objects:
        for path, stored in loans.items():
            entry = MIRPlace(obj.place.root, (*obj.place.projections, *path))
            if entry not in stored_loans or not strong and entry not in state:
                raise MIRUnseededLoan(entry, reason)
            writes[entry] = stored if strong else state[entry] | stored
    for entry, stored in writes.items():
        if stored:
            state[entry] = stored
        else:
            state.pop(entry, None)


def store_loan(record: MIRPlace, member: MIRField, loans: frozenset[MIRReferent],
               state: dict[MIRPlace, frozenset[MIRReferent]], slots: Mapping[MIRSlotId, MIRSlot],
               records: Mapping[TpyType, MIRRecordLayout], stored_loans: frozenset[MIRPlace]) -> None:
    """A view member of every object `record` may reach rebound to `loans`
    (`_write_entries`): the write may land on any of the objects, and an
    external one may be another's alias."""
    objects = resolve_referents(record, state, slots, records)
    if not loans or not objects:
        raise MIRUnseededLoan(None, "view member write with no origin")
    _write_entries(objects, {(member,): loans}, state, slots, stored_loans,
                   "view member write of an object with no stored loan")


def stored_on_fill(target: MIRSlotId, value: object, state: MIRReferents,
                   slots: Mapping[MIRSlotId, MIRSlot], layouts: Mapping[NominalType, MIRRecordLayout],
                   objects: Mapping[MIRSlotId, tuple[MIRPlace, ...]]) -> dict[MIRPlace, frozenset[MIRReferent]]:
    """The loans a slot's own record storage stores once a whole write
    fills it (`record_fill`), keyed at its stored-loan entries."""
    if not objects[target]:
        return {}
    return record_fill(MIRPlace(target), slots[target].type, value, state, slots, layouts)


def record_fill(place: MIRPlace, typ: NominalType, value: object, state: MIRReferents,
                slots: Mapping[MIRSlotId, MIRSlot], layouts: Mapping[NominalType, MIRRecordLayout]
                ) -> dict[MIRPlace, frozenset[MIRReferent]]:
    """The loans record storage of type `typ` stores once a whole write
    fills it, keyed under `place` at each of its view members (through
    inline record members): a construct's view operand holds the loan its
    member stores; a copy or a move carries the source object's stored
    loans over; a call's owned result stores what the callee's result
    transfers name. Read off the state before the write."""
    keys = {key: key.projections[len(place.projections):]
            for key in _member_keys(place, layouts.get(typ), layouts)}
    if not keys:
        return {}
    empty: frozenset[MIRReferent] = frozenset()
    match value:
        case MIRConstruct(fields=fields):
            # A view member's operand holds its loan; a record member's
            # operand (a holder, or storage handed over) stores the loans
            # under the rest of the member path.
            operands = dict(zip(layouts[typ].fields, fields))
            filled = {}
            for key, path in keys.items():
                operand, rest = operands[path[0]], path[1:]
                through = object_prefix(slots[operand].shape) if rest else ()
                filled[key] = (resolve_referents(MIRPlace(operand, (*through, *rest)), state, slots, layouts) if rest
                               else state.get(MIRPlace(operand), empty))
            return filled
        case MIRCopy(source=source):
            # A source place reaching several objects carries the union of their loans.
            return {key: resolve_referents(MIRPlace(source.root, (*source.projections, *path)), state, slots, layouts)
                    for key, path in keys.items()}
        case MIRMove(source=source):
            moved = {key: MIRPlace(source, path) for key, path in keys.items()}
            for key in moved.values():
                if key not in state:
                    raise MIRUnseededLoan(key, "record move of an object with no stored loan")
            return {key: state[source_key] for key, source_key in moved.items()}
        case MIRPresentCall(call=call, effects=effects) if effects.transfers:
            # An owned record result holds exactly the loans its transfers name.
            filled: dict[MIRPlace, frozenset[MIRReferent]] = {}
            for key, path in keys.items():
                loans = frozenset().union(*(
                    transfer_loans(call, t, key, state, slots, layouts) for t in effects.transfers
                    if isinstance(t.holder, MIRResultHolder) and tuple(map(path_step, t.path)) == path))
                if not loans:
                    raise MIRUnseededLoan(key, "record storage filled with no stored loan")
                filled[key] = loans
            return filled
    # Any other value stores loans no fact names.
    raise MIRUnseededLoan(None, "record storage filled with no stored loan")


def written_record(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
                   records: Mapping[TpyType, MIRRecordLayout]) -> NominalType | None:
    """The loan-holding record a write through `place` replaces whole --
    what a holder points at, an inline member, an optional's payload -- or
    None when no stored loan lies under the place (no type on the way holds
    one; a container's elements by its element types, since a view of them
    holds a loan its elements need not). A loan-holding value under any
    other place (an element, a tuple member, a union payload, a wrapper
    member) refuses: no entry keys it."""
    walked = place_walk(place, slots, records)
    for hop in walked.hops:
        typ = hop.holder.type
        match hop.projection:
            case MIRContainerStructure() | MIRContainerElements():
                # Not `container_members`: an element place MIR does not
                # model (a nested container) still holds no loan.
                members = member_types(typ)
                if members is not None and not any(holds_loan(m) for m in members):
                    return None
                raise _DependencyRefusal("loan-holding write under an unmodeled place")
        # UNKNOWN reads as no loan, as everywhere in MIR (`holds_loan`): a
        # record with a view member beside an undecided one has no layout.
        if not holds_loan(typ):
            return None
        if not (isinstance(hop.projection, (MIRDeref, MIRField))
                or optional_payload(hop.holder.cls, hop.projection)):
            raise _DependencyRefusal("loan-holding write under an unmodeled place")
    end = walked.end
    if not holds_loan(end.type):
        return None
    if end.cls is not MIRClass.RECORD:
        raise _DependencyRefusal("loan-holding write under an unmodeled place")
    return end.type


def fill_through(target: MIRPlace, typ: NominalType, value: object, state: dict[MIRPlace, frozenset[MIRReferent]],
                 slots: Mapping[MIRSlotId, MIRSlot], layouts: Mapping[NominalType, MIRRecordLayout],
                 stored_loans: frozenset[MIRPlace]) -> None:
    """A record of type `typ` written whole through a place (an in-place
    reseat through its holder, a member replaced in place): each object
    the place may reach stores the written record's loans (`record_fill`),
    replacing what it stored when the place reaches exactly one object the
    body owns, else joining it (the write lands on one of them, and an
    external one may be another's alias) -- `store_loan`'s rule, member by
    member (`_write_entries`)."""
    filled = record_fill(MIRPlace(target.root), typ, value, state, slots, layouts)
    if not filled:
        return
    objects = resolve_referents(target, state, slots, layouts)
    if not objects:
        raise MIRUnseededLoan(None, "record write into no object")
    _write_entries(objects, {key.projections: loans for key, loans in filled.items()}, state, slots,
                   stored_loans, "record write into an object with no stored loan")


def transfer_loans(call: MIRCall, transfer: MIRLoanTransfer, key: MIRPlace, state: MIRReferents,
                   slots: Mapping[MIRSlotId, MIRSlot],
                   records: Mapping[TpyType, MIRRecordLayout]) -> frozenset[MIRReferent]:
    """The caller's loans a transfer's source names at a call: a literal's
    static storage, or what the source argument's place (`call_place`)
    holds -- a view's or a lent owned leaf's referents, or the loan a view
    member of its object stores. `transfer` is one of the call's present
    effects (`MIRPresentCall`)."""
    if isinstance(transfer.source, MIRStaticLoan):
        return frozenset({MIRReferent(key, external=True, static=True)})
    loans = resolve_referents(call_place(call, transfer.source, transfer.source_path, slots), state, slots, records)
    if not loans:
        raise MIRUnseededLoan(None, "call transfer with no origin")
    return loans


def apply_transfers(present: MIRPresentCall, state: dict[MIRPlace, frozenset[MIRReferent]],
                    slots: Mapping[MIRSlotId, MIRSlot], records: Mapping[TpyType, MIRRecordLayout]) -> None:
    """Join each loan a callee may store in an argument's object into that
    object's stored-loan entry, to a fixpoint: one transfer's source may be
    another's holder, and the arguments may be one object."""
    call = present.call
    transfers = sorted((t for t in present.effects.transfers if not isinstance(t.holder, MIRResultHolder)),
                       key=lambda t: (t.holder, len(t.path)))
    changed = True
    while changed:
        changed = False
        for transfer in transfers:
            member = path_step(transfer.path[-1])
            objects = resolve_referents(call_place(call, transfer.holder, transfer.path[:-1], slots), state, slots,
                                        records)
            if not objects:
                raise MIRUnseededLoan(None, "call transfer into no object")
            for key in member_keys(objects, member, state, "call transfer into an object with no stored loan"):
                joined = state[key] | transfer_loans(call, transfer, key, state, slots, records)
                if joined != state[key]:
                    state[key] = joined
                    changed = True


def resolve_call_returns(present: MIRPresentCall, state: MIRReferents, slots: Mapping[MIRSlotId, MIRSlot],
                         records: Mapping[TpyType, MIRRecordLayout],
                         result: MIRSlot | None = None) -> frozenset[MIRReferent] | None:
    """The caller's origins of a call's borrowed result: each return
    origin's place (`call_place`), resolved in sequence like a direct
    borrow of it. A whole-parameter origin is the argument; what a
    container lends other than itself (an element, a view of it) lies in
    its elements region, so a `result` holder that is no container takes
    that region of a container argument. A path is never re-projected."""
    call = present.call
    origins: set[MIRReferent] = set()
    for origin in present.effects.returns:
        argument = slots[call.arguments[origin.parameter]]
        refs = resolve_referents(call_place(call, origin.parameter, origin.path, slots), state, slots, records)
        if not refs:
            return None
        if (not origin.path and result is not None and not result.shape.whole_container
                and argument.shape.whole_container):
            refs = _project(refs, MIRContainerElements())
        origins.update(refs)
    return frozenset(origins)


def call_return_problem(present: MIRPresentCall, result: MIRSlot, state: MIRReferents,
                        slots: Mapping[MIRSlotId, MIRSlot], records: Mapping[TpyType, MIRRecordLayout]) -> str | None:
    """Type-check each return origin's place against the holder the result
    fills by the one return endpoint rule (`return_endpoint_problem`, which
    the summary met too), plus what only the caller's arguments answer: a
    container holder of a whole parameter needs a container argument, and
    never one rooted in an elements region (its writes would land under
    that region, where no write of the container reaches)."""
    call = present.call
    for origin in present.effects.returns:
        argument = slots[call.arguments[origin.parameter]]
        place = call_place(call, origin.parameter, origin.path, slots)
        problem = return_endpoint_problem(origin.path, lambda: place_shape(place, slots, records).type,
                                          result.type)
        if problem is not None:
            return problem
        if result.shape.whole_container:
            if not origin.path and not argument.shape.whole_container:
                return "container result of a non-container argument"
            refs = resolve_referents(place, state, slots, records)
            if any(r.place.projections and isinstance(r.place.projections[-1], REGION_STEPS) for r in refs):
                return "container result rooted in an elements region"
    return None


def _dependencies(presence: MIRPresence, liveness: MIRLiveness) -> MIRDependencies | MIRNotCovered:
    """The dependency facts of a body over the presence its preparation
    gives (`prepare.prepare`): every caller reads them from `MIRPrepared`."""
    fn = presence.function
    if liveness.function is not fn:
        raise MIRValidationError("liveness belongs to a different MIR function")
    found = _coverage(fn)
    if found is not None:
        reason, slot = found
        return MIRNotCovered(fn.id, "dependencies", reason, slot=slot)
    slots = {s.id: s for s in fn.slots}
    # A record object's stored loans are entries only where the body
    # reads them: the lowering registers the layout of every record whose
    # view member a read or a lent argument reaches; any other read refuses
    # below (`MIRUnseededLoan`).
    layouts = fn.record_layouts
    objects = {s.id: object_keys(s, layouts) for s in fn.slots}
    stored_loans = frozenset(key for keys in objects.values() for key in keys)
    # A whole write of record storage replaces its holder leaves and its
    # stored loans together.
    leaves = {s.id: _leaves(s, slots) + (objects[s.id] if s.shape.is_storage else ())
              for s in fn.slots}
    empty: frozenset[MIRReferent] = frozenset()
    present = presence.calls

    def transfer(point: MIRPoint, stmt: MIRStatement, state: dict[MIRPlace, frozenset[MIRReferent]],
                 final: bool) -> None:
        if isinstance(stmt, MIRCallStmt):
            apply_transfers(present[point], state, slots, layouts)
        if not isinstance(stmt, MIRAssign):
            return
        target = stmt.target
        # A call's value is the call as presence sees it (`MIRPresentCall`).
        value = present[point] if isinstance(stmt.value, MIRCall) else stmt.value
        result: dict[MIRPlace, frozenset[MIRReferent]] = {}
        match value:
            case MIRPresentCall(call=call):
                # What the callee stores in the arguments' objects is in place
                # before its result is resolved or fills storage.
                apply_transfers(value, state, slots, layouts)
                borrowed = call.summary.borrowed_result is not None
                returns = (resolve_call_returns(value, state, slots, layouts, slots[target.root])
                           if borrowed or final else None)
                # Only the fixpoint's states are checked: an intermediate one may still lack an origin.
                if final and (problem := "missing call return origin" if returns is None else call_return_problem(
                        value, slots[target.root], state, slots, layouts) if borrowed else None) is not None:
                    raise _DependencyRefusal(problem, stmt.loc)
                if borrowed:
                    result[target] = returns or empty
            case MIRIteratorInit(source=source):
                refs = resolve_referents(MIRPlace(source), state, slots, layouts)
                # A cursor over a view depends on the region the view holds;
                # over a container, on its shape and its elements.
                result[target] = (refs if slots[source].shape.container_view_holder
                                  else _project(refs, MIRContainerStructure(), MIRContainerElements()))
            case MIRIteratorAdvance(source=source):
                result[target] = state.get(MIRPlace(source), empty)
            case MIRIteratorRead(source=source):
                if slots[target.root].shape.is_pointer:
                    result[target] = frozenset(r for r in state.get(MIRPlace(source), empty)
                                               if r.place.projections
                                               and isinstance(r.place.projections[-1], MIRContainerElements))
            case MIRAlias(source=source):
                result[target] = state.get(MIRPlace(source), empty)
            case MIRBorrow(source=source):
                result[target] = resolve_referents(source, state, slots, layouts)
            case MIRUnionExtract(source=source):
                result[target] = (frozenset({MIRReferent(source)})
                                  if slots[target.root].shape.holding is MIRHolding.ALIAS
                                  else resolve_referents(source, state, slots, layouts))
            case MIRCopy(source=source) if place_shape(target, slots, layouts).cls in WRAPPER_CLASSES:
                # A sum's or a tuple's members: an inline payload source (an
                # Optional record field) is borrowed where it lies; a holder
                # leaf passes its loan on. A record copy keeps its stored
                # loans (`stored_on_fill`), never rebased.
                result = {leaf: resolve_referents(_rebased(leaf, target, source), state, slots, layouts)
                          for leaf in leaves[target.root]}
            case MIRTupleConstruct(elements=elements):
                result = {leaf: state.get(MIRPlace(elements[leaf.projections[0].index]), empty)
                          for leaf in leaves[target.root]}
            case MIRWrap(alternative=alternative, source=source):
                leaf = MIRPlace(target.root, (*target.projections, MIRUnionPayload(alternative)))
                if source is not None and leaf in leaves[target.root]:
                    result[leaf] = state.get(source, empty)
            case MIRConstant() if slots[target.root].shape.is_pointer:
                # A literal's static storage outlives the body and is never written:
                # the holder's own identity names that immortal external origin.
                result[target] = frozenset({MIRReferent(MIRPlace(target.root), external=True, static=True)})
            case (MIRConstant() | MIRRead() | MIRCompare() | MIRNot() | MIROp()
                  | MIRIsAlternative() | MIRConstruct() | MIRCopy() | MIRMove() | MIRIteratorHasNext()
                  | MIRRangeAdvance()):
                pass
            case _:
                raise MIRValidationError("unknown dependency operation")
        if (member := view_member(target)) is not None:
            store_loan(MIRPlace(target.root, target.projections[:-1]), member, result[target], state, slots, layouts,
                       stored_loans)
            return
        if target.projections and (written := written_record(target, slots, layouts)) is not None:
            # A value that names no stored loan refuses in `record_fill`.
            fill_through(target, written, value, state, slots, layouts, stored_loans)
            return
        if not target.projections:
            result.update(stored_on_fill(target.root, value, state, slots, layouts, objects))
        # A write under a projection (a field, an element of a container) is a
        # weak update: it never kills what the root's leaves hold.
        if not target.projections:
            for leaf in leaves[target.root]:
                state.pop(leaf, None)
            state.update((leaf, refs) for leaf, refs in result.items() if refs)

    # A parameter holder borrows storage outside the body; an owned-leaf
    # global's handle names the global itself (`global:<module>.<name>` by its
    # slot's identity), external and static. External origins may alias.
    seed = {leaf: frozenset({MIRReferent(origin(leaf, slots).place, external=True)})
            for slot in fn.slots if slot.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)
            for leaf in _leaves(slot, slots)}
    # The loans the object of a borrowed record parameter, or of one handed
    # over as `Own[R]`, stores at entry are the caller's: any storage
    # outside the body.
    seed.update((key, frozenset({seeded_loan(key)}))
                for slot in fn.slots if slot.kind is MIRSlotKind.PARAMETER for key in objects[slot.id])
    try:
        flow = _flow(fn, liveness, slots, seed, transfer, MappingProxyType(objects), stored_loans,
                     caller_keys(fn, objects))
    except _DependencyRefusal as refused:
        return MIRNotCovered(fn.id, "dependencies", refused.reason, refused.loc)
    return replace(flow, caller_stores=caller_stores(presence, flow))


def feasible_returns(presence: MIRPresence, dependencies: MIRDependencies
                     ) -> Iterator[tuple[MIRPoint, MIRReturn, MIRReferents]]:
    """Each return on a feasible path with its dependency state; a feasible
    point without facts is the dependency coverage's gap."""
    for block in presence.function.blocks:
        point = MIRPoint(block.id, len(block.statements))
        state = dependencies.referents.get(point)
        if isinstance(block.terminator, MIRReturn) and point in presence.points and state is not None:
            yield point, block.terminator, state


def caller_stores(presence: MIRPresence, dependencies: MIRDependencies) -> tuple[MIRCallerStore, ...]:
    """The loans the body may store in objects the caller reaches, at each
    feasible point: each `pinned` entry, and at each return of an owned
    record the returned slot's entries (`MIRCallerStore`). The summary
    publishes them as transfers, the storage evidence reports a loan of the
    body's own storage among them as a store escape."""
    keys = sorted(dependencies.pinned, key=_place)
    stores: list[MIRCallerStore] = []
    for point, state in dependencies.referents.items():
        if point in presence.points:
            stores.extend(MIRCallerStore(key, point, state[key]) for key in keys if state.get(key))
    if owned_aggregate_result(presence.function.return_type) is not None:
        for point, term, state in feasible_returns(presence, dependencies):
            if term.value is not None:
                stores.extend(MIRCallerStore(key, point, state[key], result=True)
                              for key in dependencies.objects[term.value] if state.get(key))
    return tuple(stores)


def _flow(fn: MIRFunction, liveness: MIRLiveness, slots: Mapping[MIRSlotId, MIRSlot],
          seed: dict[MIRPlace, frozenset[MIRReferent]],
          transfer: 'Callable[[MIRPoint, MIRStatement, dict[MIRPlace, frozenset[MIRReferent]], bool], None]',
          objects: Mapping[MIRSlotId, tuple[MIRPlace, ...]], stored_loans: frozenset[MIRPlace],
          pinned: frozenset[MIRPlace] = frozenset()) -> MIRDependencies:
    """The dependency states to a fixpoint, then each point's facts in a
    final pass that `transfer` checks (`final`)."""
    empty: frozenset[MIRReferent] = frozenset()
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    incoming: dict[MIRBlockId, dict[MIRPlace, frozenset[MIRReferent]]] = {fn.entry: seed.copy()}
    work = deque([fn.entry])
    queued = {fn.entry}
    while work:
        bid = work.popleft()
        queued.remove(bid)
        state = incoming[bid].copy()
        block = blocks[bid]
        for index, stmt in enumerate(block.statements):
            transfer(MIRPoint(bid, index), stmt, state, False)
        for edge, dest in outgoing_edges(bid, block.terminator):
            if dest is None:
                continue
            changed = dest not in incoming
            joined = incoming.setdefault(dest, {})
            for leaf, refs in state.items():
                if leaf.root in regions.edges[edge].reset:
                    continue
                merged = joined.get(leaf, empty) | refs
                if merged != joined.get(leaf, empty):
                    joined[leaf] = merged
                    changed = True
            if changed and dest not in queued:
                work.append(dest)
                queued.add(dest)

    points: dict[MIRPoint, MIRReferents] = {}
    active: dict[MIRPoint, MIRReferents] = {}
    holders: dict[MIRPoint, Mapping[MIRReferent, frozenset[MIRPlace]]] = {}
    for block in fn.blocks:
        if block.id not in incoming:
            continue
        state = incoming[block.id].copy()
        for index in range(len(block.statements) + 1):
            point = MIRPoint(block.id, index)
            points[point] = MappingProxyType(state.copy())
            live = live_entries(state, liveness.points[point], stored_loans, pinned)
            active[point] = MappingProxyType(live)
            inverse: dict[MIRReferent, set[MIRPlace]] = {}
            for leaf, refs in live.items():
                for ref in refs:
                    inverse.setdefault(ref, set()).add(leaf)
            holders[point] = MappingProxyType({ref: frozenset(owners) for ref, owners in inverse.items()})
            if index < len(block.statements):
                transfer(point, block.statements[index], state, True)
    entry = MappingProxyType(live_entries(incoming[fn.entry], liveness.entry_live, stored_loans, pinned))
    return MIRDependencies(fn, MappingProxyType(points), MappingProxyType(active), MappingProxyType(holders), entry,
                           objects, stored_loans, pinned)


def dump_dependencies(result: MIRDependencies | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"dependencies not covered: {result.reason}\n"

    def format_refs(refs: MIRReferents) -> str:
        return "; ".join(f"{_place(leaf)} -> {{" + ", ".join(sorted(_referent(ref) for ref in values)) + "}"
            for leaf, values in sorted(refs.items(), key=lambda item: _place(item[0]))) or "{}"

    lines = ["dependencies (external origins may alias; no safety verdict)",
             "  entry active: " + format_refs(result.entry_active)]
    for point, refs in result.referents.items():
        lines.append(f"  bb{point.block.index} before {point.index}: {format_refs(refs)}")
        lines.append("    active: " + format_refs(result.active[point]))
    return "\n".join(lines) + "\n"

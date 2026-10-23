"""Local effect/exit evidence from validated MIR, without callee scheduling."""

from dataclasses import replace

from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, value_class
from ..type_def_registry import ParamPassing
from ..typesys import TpyType, receiver_neutral_return
from .call_contract import (
    BORROWING_PASSINGS, MIR_RESULT, MIR_STATIC, OWNING_PASSINGS, MIRAliasObligation, MIRCallSummary, MIRGlobalId,
    MIRLoanTransfer, MIRParameterWrite, MIRPathWalks, MIRResultHolder, MIRReturnOrigin, MIRSummaryResult,
    MIRSummaryState, body_result, bound_params, call_result, obligation_problem, parameter_binding,
    return_origin_problem, summary_problem, write_problem, summary_path,
)
from .call_effects import resolve_call_writes
from .shapes import (
    REGION_STEPS, MIRDeref, MIRContainerElements, object_prefix, optional_payload,
)
from .coverage import MIRUnsupported
from .definitions import MIRDefinitions
from .dependencies import (
    MIRDependencies, MIRReferent, analyze_dependencies, resolve_referents, seeded_loan, whole_wrapper_field,
)
from .liveness import MIRLiveness, analyze_liveness
from .retention import alias_obligation, analyze_body_retention, parameter_indices
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRConstruct, MIRCopy,
    MIRFunction, MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMove, MIRNot,
    MIRNotCovered, MIROp, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead, MIRRecordStorageInit, MIRReturn, MIRPlace,
    MIRSlot, MIRSlotKind, MIRSlotId, statement_call, place_walk, under_object, view_member, MIRWrap, MIRIsAlternative,
    place_shape,
)
from .value import AGGREGATE_CLASSES, CONTAINER_CLASSES, MIRField, MIRRecords, MIRShape, lent_record, WRAPPER_CLASSES
from .validate import _validated_function, owned_leaf_place_type, statement_reads

# The passings that lend storage to the callee without a copy; a template
# trait form may copy or bind, so it is no loan.
_LENDING_PASSINGS = BORROWING_PASSINGS | {ParamPassing.MUT_REF, ParamPassing.POINTER}


def _private_records(body: MIRFunction, dependencies: MIRDependencies) -> frozenset[MIRSlotId]:
    """The owned record and container slots whose storage is private to the body.

    HANDED OVER: storage that leaves the body by a transfer -- a return
    (the caller then owns it), a move out, an argument at an owning
    passing, or a temporary a construct moves into the record or element
    it builds -- is private when it is read otherwise only by the borrow
    its holder takes and no borrow of it is live at a transfer. Borrows
    that complete before the transfer are harmless.

    KEPT: storage that never leaves the body is private when every use of
    it is the body's own -- the holder's borrow of the whole storage, a
    read, copy or borrow of one of its fields, a field write into it, a
    whole copy out of it, or a loan at a borrowing passing; a copy into a
    sum or tuple is not one yet, so it makes the storage foreign (a known
    callee keeps nothing but the loans its transfers publish; those and
    its writes reach this storage through the dependency pass). Escapes
    through a holder are the summary's own checks: a returned borrow has
    no parameter origin and a write's origin outside the parameters is
    refused. Its destruction is outside the summary -- by the body at
    scope end, or by the caller for a parameter handed over at OWN -- so
    its definition must still be hook-free."""
    slots = {s.id: s for s in body.slots}
    records = {s.id for s in body.slots
               if s.shape.is_storage and s.shape.cls in AGGREGATE_CLASSES}
    excluded: set[MIRSlotId] = set()
    foreign: set[MIRSlotId] = set()
    transfers: list[tuple[MIRSlotId, MIRPoint]] = []
    for block in body.blocks:
        term = block.terminator
        if isinstance(term, MIRReturn) and term.value in records:
            transfers.append((term.value, MIRPoint(block.id, len(block.statements))))
        for index, stmt in enumerate(block.statements):
            point = MIRPoint(block.id, index)
            reads = statement_reads(stmt)
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRBorrow) and not stmt.value.source.projections:
                # The holder's borrow of the whole storage: tracked by the dependency pass.
                continue
            # A slot can occur in one statement in several roles, so a use outside
            # the kept rule is recorded at the role that makes it one.
            own_uses: set[MIRSlotId] = set()
            if isinstance(stmt, MIRAssign):
                if stmt.target.projections:
                    if isinstance(stmt.target.projections[0], MIRField):
                        own_uses.add(stmt.target.root)
                    elif stmt.target.root in records:
                        foreign.add(stmt.target.root)
                # A sum's or a tuple's copy is no use of its own (the summary
                # admits only an Optional's, under `_optional_place`).
                wrapper_copy = (isinstance(stmt.value, MIRCopy)
                                and place_shape(stmt.target, slots, body.record_layouts).cls in WRAPPER_CLASSES)
                match stmt.value:
                    case MIRCopy() if wrapper_copy:
                        pass
                    case MIRRead(source=source) | MIRCopy(source=source) | MIRBorrow(source=source) if (
                            source.projections[:1] and isinstance(source.projections[0], MIRField)):
                        own_uses.add(source.root)
                    case MIRCopy(source=source) if not source.projections:
                        own_uses.add(source.root)
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and stmt.value.source in records:
                transfers.append((stmt.value.source, point))
                reads = tuple(r for r in reads if r != stmt.value.source)
            if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRWrap)
                    and stmt.value.source is not None and stmt.value.source.root in records):
                # A temporary moved into an Optional field's payload: a transfer.
                transfers.append((stmt.value.source.root, point))
                reads = tuple(r for r in reads if r != stmt.value.source.root)
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRConstruct):
                # Owned temporary record storage a construct names as an
                # operand is moved into the built record or element: a
                # transfer, which leaves the destination's own slot to classify.
                moved = {f for f in stmt.value.fields if f in records and slots[f].kind is MIRSlotKind.TEMPORARY}
                transfers.extend((f, point) for f in moved)
                reads = tuple(r for r in reads if r not in moved)
            if (call := statement_call(stmt)) is not None:
                handed = {arg for arg, binding in zip(call.arguments, call.summary.parameters)
                          if binding.passing in OWNING_PASSINGS}
                lent = {arg for arg, binding in zip(call.arguments, call.summary.parameters)
                        if binding.passing not in OWNING_PASSINGS}
                for arg in handed & records:
                    transfers.append((arg, point))
                excluded.update(lent & records)
                foreign.update(arg for arg, binding in zip(call.arguments, call.summary.parameters)
                               if arg in records and binding.passing not in OWNING_PASSINGS
                               and binding.passing not in _LENDING_PASSINGS)
                own_uses.update(lent)
                reads = tuple(r for r in reads if r not in handed)
            excluded.update(r for r in reads if r in records)
            foreign.update(r for r in reads if r in records and r not in own_uses)
    handed_over = {slot for slot, _ in transfers}
    for slot, point in transfers:
        held = dependencies.holders.get(point, {})
        if any(not ref.external and ref.place.root == slot and owners for ref, owners in held.items()):
            excluded.add(slot)
    return frozenset((handed_over - excluded) | (records - handed_over - foreign))


def _member_chain(source: MIRPlace, slots: dict[MIRSlotId, MIRSlot], private: frozenset[MIRSlotId],
                  records: MIRRecords) -> MIRShape | None:
    """What `source` reaches under storage the summary accounts for -- past
    the object a holder points at (an Optional holder's payload record
    included) or under the body's private storage -- by any walk
    `shapes.step` takes (a payload selection is the presence proof's), or
    None when it lies under neither or ends at a container's structure.
    The end is the caller's to classify."""
    steps = under_object(source, slots, payload=True)
    if steps is None and source.root in private:
        steps = source.projections
    if not steps:
        return None
    return place_walk(source, slots, records).end


def _transfers(body: MIRFunction, dependencies: MIRDependencies,
               parameters: dict[MIRSlotId, int]) -> frozenset[MIRLoanTransfer] | str:
    """The loans the body may store in objects the caller reaches
    (`MIRDependencies.caller_stores`), each mapped to its source: a
    parameter's seeded loan to that parameter's member, a lent view or
    owned leaf to the parameter, a literal to static storage. A loan of the
    body's own storage escapes (a conflict of the body), and one of a
    member or element of the caller's storage has no transfer form: either
    leaves the summary unpublished."""
    slots = {s.id: s for s in body.slots}
    if any(store.escaping for store in dependencies.caller_stores):
        return "summary stores a loan of the body's storage"
    found: set[MIRLoanTransfer] = set()
    for store in dependencies.caller_stores:
        holder: int | MIRResultHolder
        if store.result:
            holder = MIR_RESULT
        elif (lent_record(slots[store.key.root].shape) is not None
              or slots[store.key.root].shape.cls is MIRClass.OPTIONAL):
            # A lent record parameter's object, or the payload record an
            # Optional parameter lends (`object_keys`).
            holder = parameters[store.key.root]
        elif not slots[store.key.root].shape.is_storage:
            return "summary store under an unmodeled holder"
        else:
            # What a body stores in an `Own[R]` parameter's object, the caller
            # never reads again: its argument is a temporary of the call.
            continue
        path = summary_path(store.key.projections)
        for ref in store.loans:
            if not store.result and ref == seeded_loan(store.key):
                # The lent object's own loan, which the caller's weak join keeps.
                continue
            if ref.static:
                found.add(MIRLoanTransfer(holder, path, MIR_STATIC))
                continue
            index = parameters.get(ref.place.root)
            if index is None:
                return "summary transfer source outside the parameters"
            if ref.held:
                found.add(MIRLoanTransfer(holder, path, index, summary_path(ref.place.projections)))
            elif not ref.place.projections:
                found.add(MIRLoanTransfer(holder, path, index))
            else:
                # A loan of a member's or an element's storage would need that
                # storage's own path, which no transfer source publishes.
                return "summary transfer source is a member or element"
    return frozenset(found)


def _aggregate_member(place: MIRPlace, slots: dict[MIRSlotId, MIRSlot], private: frozenset[MIRSlotId],
                   records: MIRRecords) -> bool:
    """Whether `place` is an inline record or container member of storage the summary
    accounts for (`_member_chain`): a whole member a write replaces in
    place or a copy reads, or an Optional record field's payload a holder
    borrows."""
    end = _member_chain(place, slots, private, records)
    return end is not None and end.cls in AGGREGATE_CLASSES


def _optional_place(place: MIRPlace, slots: dict[MIRSlotId, MIRSlot], private: frozenset[MIRSlotId],
                    records: MIRRecords) -> bool:
    """Whether `place` is a whole Optional: a holder slot, or an Optional
    record field of storage the summary accounts for (`_member_chain`):
    replaced, tested or copied."""
    if not place.projections:
        return slots[place.root].shape.cls is MIRClass.OPTIONAL
    end = _member_chain(place, slots, private, records)
    return end is not None and end.cls is MIRClass.OPTIONAL


def _container_access(source: MIRPlace, target: MIRSlot, slots: dict[MIRSlotId, MIRSlot],
                      private: frozenset[MIRSlotId], records: MIRRecords) -> bool:
    """A read the dependency pass tracks: a place under accounted storage
    (`_member_chain`), a region of a container slot, or a whole slot a
    borrowed aggregate holder takes."""
    return (_member_chain(source, slots, private, records) is not None
            or any(isinstance(p, REGION_STEPS) for p in source.projections)
            or target.shape.borrowed_aggregate)


def summarize_function(declaration: th.THIRFunction, body: MIRFunction,
                       definitions: MIRDefinitions) -> MIRSummaryResult:
    """Unsupported evidence is opaque; malformed MIR remains a validation error.

    The summary covers every exit of the body. Possible writes are collected
    at every statement, so a write before a throw is in them, and every
    nested call's writes are promised over its own exits too; nothing the
    body holds survives an exit but its storage, which the body owns. Loops
    are summarized by the dependency and liveness fixpoints.

    An `@auto_readonly` access twin (`THIRFunction.access_twin`) summarizes
    as if its receiver were bound at its definition's passing (`CONST_REF`,
    so readonly: `parameter_binding`), so the two summaries compare equal exactly when the twin's
    effects and origins are its definition's."""
    present = _validated_function(body).presence.calls
    callee = declaration.resolved_callee
    # A method body binds its receiver as parameter 0, and only a method's callee names an owner.
    method = declaration.receiver is not None
    twin = declaration.access_twin
    if (callee is None or body.kind is not (MIRBodyKind.METHOD if method else MIRBodyKind.FREE_FUNCTION)
            or (callee.identity.owner is not None) is not method or body.receiver_init is not None
            or twin and not (method and callee.signature.result_follows_receiver and callee.signature.passings)
            or body.borrowed_result != call_result(callee, method and declaration.receiver.readonly)
            or body.return_type != body_result(declaration)
            # THIR's declared and published types, compared as THIR spells both.
            or callee.signature.return_type not in (
                declaration.return_type, receiver_neutral_return(
                    declaration.return_type, callee.signature.result_follows_receiver))):
        return MIRSummaryResult.opaque("summary definition or result contract mismatch")
    effective = bound_params(declaration)
    # The body bound each parameter as its callers bind it (`bound_params`),
    # checked against its slot by the validator.
    if effective is None or len(effective) != len(body.slot_bindings):
        return MIRSummaryResult.opaque("summary definition signature mismatch")
    slots = {s.id: s for s in body.slots}
    for (sid, binding), param in zip(body.slot_bindings.items(), effective):
        if param.passing is None:
            return MIRSummaryResult.opaque("summary parameter passing unpublished")
        # The body is the declaration's: each binding the one THIR's facts derive.
        if slots[sid].name != param.name or parameter_binding(param) != binding:
            return MIRSummaryResult.opaque("summary parameter binding mismatch")
    bindings = list(body.parameter_bindings)
    if twin:
        twin_receiver = parameter_binding(replace(effective[0], passing=callee.signature.passings[0]))
        if isinstance(twin_receiver, str):
            return MIRSummaryResult.opaque("summary parameter binding mismatch")
        bindings[0] = twin_receiver
    record_layouts = body.record_layouts
    # The dependency pass runs once, first asked for by a record storage slot.
    liveness = analyze_liveness(body)
    facts: list[MIRDependencies | MIRNotCovered] = []
    private: list[frozenset[MIRSlotId]] = []

    def dependency_facts() -> MIRDependencies | MIRNotCovered:
        if not facts:
            facts.append(analyze_dependencies(body, liveness))
        return facts[0]

    def private_records() -> frozenset[MIRSlotId]:
        if not private:
            dependencies = dependency_facts()
            # Without dependency facts the body is opaque below, by their reason.
            private.append(frozenset(s.id for s in body.slots) if isinstance(dependencies, MIRNotCovered)
                           else _private_records(body, dependencies))
        return private[0]

    global_reads: set[MIRGlobalId] = set()
    # The records whose definitions must verify (checked once, below): each
    # a slot holds, then each a published path reads a field of.
    verified: dict[TpyType, None] = {}
    for slot in body.slots:
        if slot.kind is MIRSlotKind.GLOBAL:
            # A handle the body only reads; a write refuses at its statement.
            global_reads.add(slot.global_id)
        records: tuple[TpyType, ...] = ()
        shape = slot.shape
        if slot.shape.is_scalar:
            pass
        elif shape.is_storage and shape.cls in AGGREGATE_CLASSES:
            if slot.id not in private_records():
                return MIRSummaryResult.opaque("summary storage or value shape")
            # Storage private to the body (`_private_records`): whoever
            # destroys it -- the body, a callee or container it is handed
            # to, or the caller it is returned to -- runs no hook, so its
            # definition (a container's: its record elements') must be
            # hook-free.
            records = (slot.type,)
        elif shape.cls in CONTAINER_CLASSES:
            # Whatever else holds a container (a pointer at it, a
            # view, a cursor): a record element is destroyed and copied by
            # the container itself.
            records = tuple(m.type for m in shape.members if m.cls is MIRClass.RECORD)
        elif slot.shape.view_holder:
            # A view holds a loan on owned-leaf storage.
            pass
        elif shape.is_pointer:
            records = (slot.type,)
        elif slot.shape.leaf_storage:
            # An owned leaf's storage is private to the body: nothing of the caller's.
            pass
        elif shape.cls is MIRClass.OPTIONAL and shape.payload.scalar_by_value:
            pass
        elif slot.shape.record_payload:
            # A holder of a record: the record a borrowed holder points at.
            records = (lent_record(shape).type,)
        else:
            return MIRSummaryResult.opaque("summary storage or value shape")
        verified.update(dict.fromkeys(records))
    for consumed in body.call_summaries:
        global_reads.update(consumed.global_reads)
    dependencies = dependency_facts()
    if isinstance(dependencies, MIRNotCovered):
        return MIRSummaryResult.opaque(f"summary dependencies: {dependencies.reason}")
    parameters = parameter_indices(body)
    writes: set[MIRParameterWrite] = set()
    returns: set[MIRReturnOrigin] = set()

    def include_writes(origins: frozenset[MIRReferent] | None) -> str | None:
        if origins is None:
            return "summary missing write origin"
        for origin in origins:
            if not origin.external and slots[origin.place.root].shape.is_storage:
                # A write into the body's own storage: nothing the caller can observe.
                continue
            if not origin.external or origin.place.root not in parameters:
                return "summary unsupported write origin"
            # Published as the full path, under the one grammar and endpoint
            # rule a caller checks it by; each field must be a member of the
            # layout of the storage its hop reads.
            index = parameters[origin.place.root]
            place = whole_wrapper_field(origin.place, slots, record_layouts)
            write = MIRParameterWrite(index, summary_path(place.projections))
            problem = write_problem(write, tuple(bindings), definitions.layouts)
            if problem is not None:
                return f"summary unsupported write origin: {problem}"
            writes.add(write)
        return None

    # The borrowed result the summary publishes, as a caller binds it.
    result = call_result(callee, bool(bindings) and bindings[0].readonly)
    for block in body.blocks:
        if body.borrowed_result is not None and isinstance(block.terminator, MIRReturn):
            state = dependencies.referents.get(MIRPoint(block.id, len(block.statements)))
            if state is not None:
                origins = resolve_referents(MIRPlace(block.terminator.value), state, slots, record_layouts)
                if not origins:
                    return MIRSummaryResult.opaque("summary missing return origin")
                view = value_class(body.borrowed_result.type) is MIRClass.VIEW
                for origin in origins:
                    path = origin.place.projections
                    if not origin.external or origin.place.root not in parameters:
                        # A global, a static literal or the body's own storage has no parameter.
                        return MIRSummaryResult.opaque("view result origin outside the parameters" if view
                                                       else "summary unsupported return origin")
                    index = parameters[origin.place.root]
                    if lent_record(slots[origin.place.root].shape) is None and all(
                            isinstance(p, REGION_STEPS) for p in path):
                        # A container result, a Span, or a record element of a
                        # container parameter returns as the whole parameter (the
                        # caller projects a container argument into its elements).
                        returns.add(MIRReturnOrigin(index))
                        continue
                    # Inside a record parameter: the record itself, or a field
                    # chain through its inline members, under the one grammar
                    # and endpoint rule a caller checks the origin by; each field
                    # must be a member of the layout of the storage its hop reads.
                    returned = MIRReturnOrigin(index, summary_path(origin.place.projections))
                    problem = return_origin_problem(returned, result, tuple(bindings), definitions.layouts)
                    if problem is not None:
                        return MIRSummaryResult.opaque(f"summary unsupported return origin: {problem}")
                    returns.add(returned)
        for index, stmt in enumerate(block.statements):
            state = dependencies.referents.get(MIRPoint(block.id, index), {})
            if (call := present.get(MIRPoint(block.id, index))) is not None:
                problem = include_writes(resolve_call_writes(call, state, slots, record_layouts))
                if problem is not None:
                    return MIRSummaryResult.opaque(problem)
            if isinstance(stmt, MIRCallStmt):
                continue
            if (isinstance(stmt, MIRRecordStorageInit) and not stmt.target.projections
                    and stmt.target.root in private_records()):
                # The empty backing of a lazily built private record: it holds no record yet.
                continue
            if isinstance(stmt, MIRPrint):
                # Output is an effect outside every parameter-rooted summary.
                return MIRSummaryResult.opaque("summary output effect")
            if not isinstance(stmt, MIRAssign):
                return MIRSummaryResult.opaque("summary storage operation")
            target = slots[stmt.target.root]
            if target.kind is MIRSlotKind.GLOBAL:
                return MIRSummaryResult.opaque("summary global access")
            if view_member(stmt.target) is not None:
                # A view member rebound replaces no storage; what it stores in
                # an object the caller reaches is a transfer (`_transfers`).
                continue
            leaf_place = owned_leaf_place_type(stmt.target, slots, record_layouts) is not None
            own_storage = not stmt.target.projections and target.shape.is_storage
            elements = stmt.target.projections[-1:] == (MIRContainerElements(),)
            origins = (resolve_referents(stmt.target, state, slots, record_layouts) if stmt.target.projections
                       else frozenset())
            # A whole record replaced through a holder of the body's private storage (a reseat).
            private_target = (stmt.target.projections == object_prefix(target.shape, payload=False) and bool(origins)
                              and all(not o.external and not o.place.projections
                                      and o.place.root in private_records() for o in origins))
            # An inline record member replaced whole: published as a write of
            # the member under a parameter, nothing under private storage.
            member = _aggregate_member(stmt.target, slots, private_records(), record_layouts)
            # An Optional record field replaced whole: published like a member.
            wrapper = _optional_place(stmt.target, slots, private_records(), record_layouts)
            # A write event's target is admitted where its origin is: the
            # body's own storage, or a caller place the write endpoint rule
            # admits (`include_writes`). A whole caller object replaced
            # through its holder has no summary path.
            through_holder = isinstance(stmt.target.projections[-1] if stmt.target.projections else None, MIRDeref)
            if stmt.storage_write is not None and through_holder and not (private_target or leaf_place):
                return MIRSummaryResult.opaque("summary storage operation")
            if stmt.target.projections:
                problem = include_writes(origins or None)
                if problem is not None:
                    return MIRSummaryResult.opaque(problem)
            match stmt.value:
                case MIRCall():
                    pass
                case MIRConstant() | MIRCompare() | MIRNot() | MIROp() | MIRRangeAdvance():
                    pass
                case MIRBorrow(source=source) if (not source.projections and source.root in private_records()
                                                  and target.shape.is_pointer):
                    # A record holder of the body's private storage.
                    pass
                case MIRBorrow(source=source) if (target.shape.is_pointer
                                                  and slots[source.root].shape.record_payload
                                                  and under_object(source, slots, payload=True) == ()):
                    # A record holder of what an Optional holder's payload points at.
                    pass
                case MIRBorrow(source=source) if (target.shape.is_pointer
                                                  and _aggregate_member(source, slots, private_records(),
                                                                     record_layouts)):
                    # A record holder of an inline member: the dependency pass
                    # tracks it to the member's place, so a write through it is
                    # published (or kept private) by its origin, and a returned
                    # one meets the return rule.
                    pass
                case MIRCopy(source=source) if place_shape(stmt.target, slots, record_layouts).cls in WRAPPER_CLASSES:
                    # Another Optional's payload copied in, or a holder
                    # rebound; a union's or a tuple's copy has no summary form.
                    if not (wrapper and _optional_place(source, slots, private_records(), record_layouts)):
                        return MIRSummaryResult.opaque("summary unsupported operation")
                case MIRCopy() if (own_storage and stmt.target.root in private_records()) or private_target:
                    # A record copied into the body's private storage.
                    pass
                case MIRCopy(source=source) if member and (
                        source.projections in ((), object_prefix(slots[source.root].shape, payload=False))
                        or _aggregate_member(source, slots, private_records(), record_layouts)
                        or _container_access(source, target, slots, private_records(), record_layouts)):
                    # A record copied into a member: a read of the body's own
                    # storage, of what a holder points at, of another member or
                    # of an element.
                    pass
                case MIRCopy() | MIRBorrow() if (leaf_place or elements or target.shape.leaf_storage
                                                 or target.shape.leaf_borrow):
                    # Reads of an owned leaf into the body's own storage, a
                    # field's buffer, or holders (view holders included).
                    pass
                case MIRCopy(source=source) | MIRBorrow(source=source) | MIRRead(source=source) if (
                        _container_access(source, target, slots, private_records(), record_layouts)):
                    # Element reads and borrows, container views, and borrows
                    # of a container field: reads the dependency pass tracks.
                    pass
                case MIRRead(source=source):
                    # Scalar field reads, through inline record members too, are
                    # safe on a live borrowed record and on the body's private
                    # record storage; wrapper extraction and other projections
                    # need more proof.
                    path = source.projections
                    if len(path) == 1 and optional_payload(slots[source.root].shape.cls, path[0]) and (
                            slots[source.root].shape.payload.scalar_by_value):
                        # A scalar Optional's payload, under its presence proof.
                        pass
                    elif path:
                        end = _member_chain(source, slots, private_records(), record_layouts)
                        if end is None or end.cls is not MIRClass.LEAF:
                            return MIRSummaryResult.opaque("summary unsupported read projection")
                case MIRAlias():
                    if not target.shape.borrow_holder:
                        return MIRSummaryResult.opaque("summary unsupported alias")
                case MIRConstruct() | MIRMove() if own_storage or elements or private_target or member:
                    # Building the body's own storage, or handing it to an
                    # element or a member (which takes the moved value; the
                    # moved storage is a transfer of `_private_records`).
                    pass
                case MIRIteratorInit() | MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    pass
                case MIRWrap(source=source) if wrapper and (
                        not stmt.target.projections
                        or source is None or slots[source.root].kind is MIRSlotKind.TEMPORARY):
                    # A holder rebound, or a field emptied or engaged with a
                    # temporary moved into the payload.
                    pass
                case MIRIsAlternative(source=source) if _optional_place(source, slots, private_records(),
                                                                    record_layouts):
                    pass
                case _:
                    return MIRSummaryResult.opaque("summary unsupported operation")
    transfers = _transfers(body, dependencies, parameters)
    if isinstance(transfers, str):
        return MIRSummaryResult.opaque(transfers)
    summary = MIRCallSummary(callee, tuple(bindings),
                             frozenset(range(len(bindings))), frozenset(writes), frozenset(),
                             frozenset(returns), transfers, not body.exceptional_exits,
                             frozenset(global_reads))
    obligations = _obligations(body, definitions, summary, liveness, dependencies)
    if isinstance(obligations, str):
        return MIRSummaryResult.opaque(obligations)
    summary = replace(summary, obligations=obligations)
    walks: MIRPathWalks = {}
    problem = summary_problem(summary, definitions.layouts, walks)
    if problem is not None:
        return MIRSummaryResult.opaque(problem)
    # A layout alone, which a refused definition keeps, publishes no path
    # through the record.
    verified.update(dict.fromkeys(hop.holder.type for walked in walks.values() for hop in walked.hops
                                  if isinstance(hop.projection, MIRField)))
    for typ in verified:
        try:
            definitions.get(declaration, typ)
        except MIRUnsupported as failure:
            return MIRSummaryResult.opaque(f"summary record: {failure.reason}")
    return MIRSummaryResult(MIRSummaryState.KNOWN, summary=summary)


def _obligations(body: MIRFunction, definitions: MIRDefinitions,
                 summary: MIRCallSummary, liveness: MIRLiveness, dependencies: MIRDependencies
                 ) -> frozenset[MIRAliasObligation] | str:
    """The no-alias obligations the body's retention conflicts between two
    parameters are (`alias_obligation`), each published when the summary
    can state it (`obligation_problem`, its fields members of the layouts
    on the way); one it cannot state stays a conflict of the body. A body
    whose conflicts are unknown has no summary: a caller could not tell
    which arguments it must keep apart."""
    retention = analyze_body_retention(body, liveness, dependencies).retention
    if isinstance(retention, MIRNotCovered):
        return f"summary retention: {retention.reason}"
    parameters = parameter_indices(body)
    slots = {slot.id: slot for slot in body.slots}
    found: set[MIRAliasObligation] = set()
    for conflict in retention.conflicts:
        obligation = alias_obligation(conflict, parameters, slots, body.record_layouts)
        if obligation is None or obligation_problem(obligation, summary, definitions.layouts) is not None:
            continue
        found.add(obligation)
    return frozenset(found)


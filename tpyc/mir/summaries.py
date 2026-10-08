"""Local effect/exit evidence from validated MIR, without callee scheduling."""

from ..thir import nodes as th
from ..thir.scalar_leaves import (
    native_container_subject, owned_value_type, record_type, storage_leaf, view_leaf,
)
from ..type_def_registry import ParamPassing
from ..typesys import TpyType, receiver_neutral_return, unwrap_readonly
from .call_contract import (
    BORROWING_PASSINGS, MIR_RESULT, MIR_STATIC, OWNING_PASSINGS, MIRCallSummary, MIRGlobalId, MIRLoanTransfer,
    MIRParameterBinding, MIRParameterWrite, MIRResultHolder, MIRReturnOrigin, MIRSummaryResult,
    MIRSummaryState, bound_result, path_hops, return_origin_problem, summary_problem, transfer_ends, write_problem,
)
from .call_effects import resolve_call_writes
from .coverage import (
    MIRUnsupported, container_view_holder, leaf_borrow, owned_storage, scalar_slot, view_holder, view_member,
)
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, MIRReferent, analyze_dependencies, resolve_referents, seeded_loan
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRConstruct,
    MIRContainerElements, MIRContainerStructure, MIRCopy, MIRDeref, MIRField, MIRFieldId, MIRFunction,
    MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMove, MIRNot, MIRNotCovered, MIROp,
    MIROptionalPayload, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead, MIRRecordStorageInit, MIRReturn, MIRPlace,
    MIRSlot, MIRSlotKind, MIRSlotId, MIRTupleIndex, MIRUnionPayload, MIRValueKind, statement_call,
)
from .validate import owned_leaf_place_type, statement_reads, validate_function

# The passings that lend storage to the callee without a copy; a template
# trait form may copy or bind, so it is no loan.
_LENDING_PASSINGS = BORROWING_PASSINGS | {ParamPassing.MUT_REF, ParamPassing.POINTER}


def _private_records(body: MIRFunction, dependencies: MIRDependencies) -> frozenset[MIRSlotId]:
    """The OWNED record slots whose storage is private to the body.

    HANDED OVER: storage that leaves the body by a transfer -- a return
    (the caller then owns it), a move out, an argument at an owning
    passing, or a temporary a construct moves into the record or element
    it builds -- is private when it is read otherwise only by the borrow
    its holder takes and no borrow of it is live at a transfer. Borrows
    that complete before the transfer are harmless.

    KEPT: storage that never leaves the body is private when every use of
    it is the body's own -- the holder's borrow of the whole storage, a
    read, copy or borrow of one of its fields, a field write into it, a
    whole copy out of it, or a loan at a borrowing passing (a known
    callee keeps nothing but the loans its transfers publish; those and
    its writes reach this storage through the dependency pass). Escapes
    through a holder are the summary's own checks: a returned borrow has
    no parameter origin and a write's origin outside the parameters is
    refused. Its destruction is outside the summary -- by the body at
    scope end, or by the caller for a parameter handed over at OWN -- so
    its definition must still be hook-free."""
    slots = {s.id: s for s in body.slots}
    records = {s.id for s in body.slots
               if s.value_kind is MIRValueKind.OWNED and s.container_layout is None and not owned_storage(s)}
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
                match stmt.value:
                    case MIRRead(source=source) | MIRCopy(source=source) | MIRBorrow(source=source) if (
                            source.projections[:1] and isinstance(source.projections[0], MIRField)):
                        own_uses.add(source.root)
                    case MIRCopy(source=source) if not source.projections:
                        own_uses.add(source.root)
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and stmt.value.source in records:
                transfers.append((stmt.value.source, point))
                reads = tuple(r for r in reads if r != stmt.value.source)
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


def _member_chain(source: MIRPlace, slots: dict[MIRSlotId, MIRSlot], private: frozenset[MIRSlotId]) -> bool:
    """Whether `source` reaches through inline record members of storage the
    summary accounts for: under a live borrowed record (one leading
    dereference of its holder) or under the body's private record storage,
    one or more fields, every one before the last a record member. The last
    field is the caller's to classify."""
    path = source.projections
    if slots[source.root].value_kind is MIRValueKind.BORROWED and path[:1] == (MIRDeref(),):
        fields = path[1:]
    elif source.root in private:
        fields = path
    else:
        return False
    return (bool(fields) and all(isinstance(f, MIRField) for f in fields)
            and all(record_type(unwrap_readonly(f.type)) for f in fields[:-1]))


def _published_path(place: MIRPlace) -> tuple[object, ...]:
    """The projections of a place under a parameter, spelled as a summary
    path: each field by its THIR identity, any other step as it is (the
    path grammar refuses what it does not admit)."""
    return tuple(th.THIRFieldIdentity(p.id.owner, p.id.name, p.type) if isinstance(p, MIRField) else p
                 for p in place.projections)


def _path_in_layouts(declaration: th.THIRFunction, definitions: MIRDefinitions, binding: MIRParameterBinding,
                     path: tuple[object, ...], place: MIRPlace) -> bool:
    """Whether each field of a published path is a member of the layout of
    the storage its hop reads: the parameter's record, then each member's."""
    return all(field in definitions.get(declaration, storage).layout.fields
               for (storage, _), field in zip(path_hops(binding, path), place.projections))


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
        elif slots[store.key.root].value_kind is MIRValueKind.BORROWED:
            holder = parameters[store.key.root]
        else:
            # What a body stores in an `Own[R]` parameter's object, the caller
            # never reads again: its argument is a temporary of the call.
            continue
        path = _published_path(store.key)
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
                found.add(MIRLoanTransfer(holder, path, index, _published_path(ref.place)))
            elif not ref.place.projections:
                found.add(MIRLoanTransfer(holder, path, index))
            else:
                # A loan of a member's or an element's storage would need that
                # storage's own path, which no transfer source publishes.
                return "summary transfer source is a member or element"
    return frozenset(found)


def _transfer_in_layouts(declaration: th.THIRFunction, definitions: MIRDefinitions,
                         bindings: tuple[MIRParameterBinding, ...], transfer: MIRLoanTransfer,
                         result: TpyType) -> bool:
    """Whether each field of a transfer's paths is a member of the layout
    of the storage its hop reads (`_path_in_layouts`)."""
    for end in transfer_ends(transfer, bindings, result):
        hops = (None if end is None else end.owned_hops if end.parameter is None
                else path_hops(bindings[end.parameter], end.path))
        if hops is None or not all(MIRField(MIRFieldId(field.owner, field.name), field.type)
                                   in definitions.get(declaration, storage).layout.fields for storage, field in hops):
            return False
    return True


def _record_member(place: MIRPlace, slots: dict[MIRSlotId, MIRSlot], private: frozenset[MIRSlotId]) -> bool:
    """Whether `place` is an inline record member of storage the summary
    accounts for (`_member_chain`): a whole member a write replaces in
    place or a copy reads."""
    return (_member_chain(place, slots, private)
            and record_type(unwrap_readonly(place.projections[-1].type)))


def _container_access(source: MIRPlace, target: MIRSlot) -> bool:
    """A read through container storage: an element or a region of it, or a
    container place itself borrowed by a container holder. Wrapper payloads
    stay out: selecting one needs a presence proof."""
    if any(isinstance(p, (MIROptionalPayload, MIRUnionPayload, MIRTupleIndex)) for p in source.projections):
        return False
    return (any(isinstance(p, (MIRContainerStructure, MIRContainerElements)) for p in source.projections)
            or target.value_kind is MIRValueKind.BORROWED_CONTAINER)


def summarize_function(declaration: th.THIRFunction, body: MIRFunction,
                       definitions: MIRDefinitions) -> MIRSummaryResult:
    """Unsupported evidence is opaque; malformed MIR remains a validation error.

    The summary covers every exit of the body. Possible writes are collected
    at every statement, so a write before a throw is in them, and every
    nested call's writes are promised over its own exits too; nothing the
    body holds survives an exit but its storage, which the body owns. Loops
    are summarized by the dependency and liveness fixpoints.

    An `@auto_readonly` access twin (`THIRFunction.access_twin`) summarizes
    as if its receiver were bound like its definition's (`CONST_REF`,
    readonly), so the two summaries compare equal exactly when the twin's
    effects and origins are its definition's."""
    validate_function(body)
    callee = declaration.resolved_callee
    # A method body binds its receiver as parameter 0, and only a method's callee names an owner.
    method = declaration.receiver is not None
    twin = declaration.access_twin
    if (callee is None or body.kind is not (MIRBodyKind.METHOD if method else MIRBodyKind.FREE_FUNCTION)
            or (callee.identity.owner is not None) is not method or body.receiver_init is not None
            or twin and not (method and callee.signature.result_follows_receiver and callee.signature.passings)
            or body.borrowed_result != bound_result(callee.signature, method and declaration.receiver.readonly)
            or callee.signature.return_type not in (
                body.return_type, receiver_neutral_return(
                    body.return_type, callee.signature.result_follows_receiver))):
        return MIRSummaryResult.opaque("summary definition or result contract mismatch")
    effective = th.effective_params(declaration)
    params = tuple(s for s in body.slots if s.kind is MIRSlotKind.PARAMETER)
    if (len(params) != len(effective)
            or tuple(th.declared_param_type(t) for t in callee.signature.param_types)
            != tuple(th.declared_param_type(p.type) for p in effective)):
        return MIRSummaryResult.opaque("summary definition signature mismatch")
    bindings: list[MIRParameterBinding] = []
    for slot, param in zip(params, effective):
        if param.passing is None:
            return MIRSummaryResult.opaque("summary parameter passing unpublished")
        owned = owned_value_type(param.type)
        view = view_holder(slot)
        # A container parameter (borrowed, owned, or a Span view of one)
        # carries its container fact in the slot, published from THIR.
        container = slot.container_layout is not None or container_view_holder(slot)
        if (slot.name != param.name or slot.passing is not param.passing
                or slot.type != (owned if owned is not None else native_container_subject(param.type))
                or (slot.value_kind is MIRValueKind.BORROWED and owned is None and not view and not container
                    and (param.borrowed_record is None
                         or slot.readonly != param.borrowed_record.readonly))):
            return MIRSummaryResult.opaque("summary parameter binding mismatch")
        # An owned leaf at a borrowing passing and a view are lent, read-only;
        # a borrowed container or a Span lends with its own access, and an
        # owned container is the callee's own.
        readonly = (param.borrowed_record.readonly if param.borrowed_record is not None
                    else slot.readonly if container and slot.value_kind is not MIRValueKind.OWNED
                    else view or owned is not None and param.passing in BORROWING_PASSINGS)
        bindings.append(MIRParameterBinding(slot.type, param.passing, readonly, param.borrowed_record))
    if twin:
        receiver = bindings[0]
        bindings[0] = MIRParameterBinding(receiver.type, callee.signature.passings[0], True,
                                          th.THIRBorrowedRecord(receiver.borrowed_record.type, True))
    slots = {s.id: s for s in body.slots}
    # The dependency pass runs once, first asked for by a record storage slot.
    facts: list[MIRDependencies | MIRNotCovered] = []
    private: list[frozenset[MIRSlotId]] = []

    def dependency_facts() -> MIRDependencies | MIRNotCovered:
        if not facts:
            facts.append(analyze_dependencies(body, analyze_liveness(body)))
        return facts[0]

    def private_records() -> frozenset[MIRSlotId]:
        if not private:
            dependencies = dependency_facts()
            # Without dependency facts the body is opaque below, by their reason.
            private.append(frozenset(s.id for s in body.slots) if isinstance(dependencies, MIRNotCovered)
                           else _private_records(body, dependencies))
        return private[0]

    global_reads: set[MIRGlobalId] = set()
    for slot in body.slots:
        if slot.kind is MIRSlotKind.GLOBAL:
            # A handle the body only reads; a write refuses at its statement.
            global_reads.add(slot.global_id)
        records: tuple[TpyType, ...] = ()
        match slot.value_kind:
            case MIRValueKind.SCALAR if scalar_slot(slot):
                pass
            case MIRValueKind.BORROWED if view_holder(slot) or container_view_holder(slot):
                # A view holds a loan on owned-leaf or container storage; the
                # container it views is verified where it is held.
                pass
            case MIRValueKind.BORROWED:
                records = (slot.type,)
            case MIRValueKind.OWNED if owned_storage(slot):
                # An owned leaf's storage is private to the body: nothing of the caller's.
                pass
            case MIRValueKind.OWNED if slot.container_layout is None and slot.id in private_records():
                # Record storage private to the body (`_private_records`):
                # whoever destroys it -- the body, a callee or container it is
                # handed to, or the caller it is returned to -- runs no hook,
                # so its definition must be hook-free.
                records = (slot.type,)
            case MIRValueKind.OWNED | MIRValueKind.BORROWED_CONTAINER | MIRValueKind.NATIVE_ITERATOR if (
                    slot.container_layout is not None):
                pass
            case _:
                return MIRSummaryResult.opaque("summary storage or value shape")
        if slot.container_layout is not None:
            # A record element is destroyed and copied by the container.
            members = (slot.container_layout.element, slot.container_layout.value)
            records = tuple(m.type for m in members if m is not None and m.kind is MIRValueKind.BORROWED)
        for typ in records:
            try:
                definitions.get(declaration, typ)
            except MIRUnsupported as failure:
                return MIRSummaryResult.opaque(f"summary record: {failure.reason}")
    for consumed in body.call_summaries:
        global_reads.update(consumed.global_reads)
    dependencies = dependency_facts()
    if isinstance(dependencies, MIRNotCovered):
        return MIRSummaryResult.opaque(f"summary dependencies: {dependencies.reason}")
    parameters = {slot.id: i for i, slot in enumerate(params)}
    writes: set[MIRParameterWrite] = set()
    returns: set[MIRReturnOrigin] = set()

    def include_writes(origins: frozenset[MIRReferent] | None) -> str | None:
        if origins is None:
            return "summary missing write origin"
        for origin in origins:
            if not origin.external and slots[origin.place.root].value_kind is MIRValueKind.OWNED:
                # A write into the body's own storage: nothing the caller can observe.
                continue
            if not origin.external or origin.place.root not in parameters:
                return "summary unsupported write origin"
            # Published as the full path, under the one grammar and endpoint
            # rule a caller checks it by; each field must be a member of the
            # layout of the storage its hop reads.
            index = parameters[origin.place.root]
            write = MIRParameterWrite(index, _published_path(origin.place))
            if write_problem(write, tuple(bindings)) is not None:
                return "summary unsupported write origin"
            if not _path_in_layouts(declaration, definitions, bindings[index], write.path, origin.place):
                return "summary write field differs from definition"
            writes.add(write)
        return None

    # The borrowed result the summary publishes, as a caller binds it.
    result = bound_result(callee.signature, bool(bindings) and bindings[0].readonly)
    for block in body.blocks:
        if body.borrowed_result is not None and isinstance(block.terminator, MIRReturn):
            state = dependencies.referents.get(MIRPoint(block.id, len(block.statements)))
            if state is not None:
                origins = resolve_referents(MIRPlace(block.terminator.value), state, slots)
                if not origins:
                    return MIRSummaryResult.opaque("summary missing return origin")
                view = view_leaf(body.borrowed_result.type)
                for origin in origins:
                    path = origin.place.projections
                    if not origin.external or origin.place.root not in parameters:
                        # A global, a static literal or the body's own storage has no parameter.
                        return MIRSummaryResult.opaque("view result origin outside the parameters" if view
                                                       else "summary unsupported return origin")
                    index = parameters[origin.place.root]
                    if bindings[index].borrowed_record is None:
                        # A container result, a Span, or a record element of a
                        # container parameter returns as the whole parameter (the
                        # caller projects a container argument into its elements).
                        if not all(isinstance(p, (MIRContainerStructure, MIRContainerElements)) for p in path):
                            return MIRSummaryResult.opaque("summary unsupported return origin")
                        returns.add(MIRReturnOrigin(index))
                        continue
                    # Inside a record parameter: the record itself, or a field
                    # chain through its inline members, under the one grammar
                    # and endpoint rule a caller checks the origin by; each field
                    # must be a member of the layout of the storage its hop reads.
                    returned = MIRReturnOrigin(index, _published_path(origin.place))
                    if return_origin_problem(returned, result, tuple(bindings)) is not None:
                        return MIRSummaryResult.opaque("summary unsupported return origin")
                    if not _path_in_layouts(declaration, definitions, bindings[index], returned.path, origin.place):
                        return MIRSummaryResult.opaque("summary return field differs from definition")
                    returns.add(returned)
        for index, stmt in enumerate(block.statements):
            state = dependencies.referents.get(MIRPoint(block.id, index), {})
            if (call := statement_call(stmt)) is not None:
                problem = include_writes(resolve_call_writes(call, state, slots))
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
            # A write event is admitted on the body's own storage, which no
            # caller-visible place reaches, and on an owned-leaf field or a
            # container's elements, whose write origin is published below.
            leaf_place = owned_leaf_place_type(stmt.target, slots) is not None
            own_storage = not stmt.target.projections and target.value_kind is MIRValueKind.OWNED
            elements = stmt.target.projections[-1:] == (MIRContainerElements(),)
            origins = resolve_referents(stmt.target, state, slots) if stmt.target.projections else frozenset()
            # A whole record replaced through a holder of the body's private storage (a reseat).
            private_target = (stmt.target.projections == (MIRDeref(),) and bool(origins)
                              and all(not o.external and not o.place.projections
                                      and o.place.root in private_records() for o in origins))
            # An inline record member replaced whole: published as a write of
            # the member under a parameter, nothing under private storage.
            member = _record_member(stmt.target, slots, private_records())
            if stmt.storage_write is not None and not (
                    leaf_place or own_storage or elements or private_target or member):
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
                                                  and target.value_kind is MIRValueKind.BORROWED):
                    # A record holder of the body's private storage.
                    pass
                case MIRBorrow(source=source) if (target.value_kind is MIRValueKind.BORROWED
                                                  and _record_member(source, slots, private_records())):
                    # A record holder of an inline member: the dependency pass
                    # tracks it to the member's place, so a write through it is
                    # published (or kept private) by its origin, and a returned
                    # one meets the return rule.
                    pass
                case MIRCopy() if (own_storage and stmt.target.root in private_records()) or private_target:
                    # A record copied into the body's private storage.
                    pass
                case MIRCopy(source=source) if member and (
                        source.projections in ((), (MIRDeref(),)) or _record_member(source, slots, private_records())
                        or _container_access(source, target)):
                    # A record copied into a member: a read of the body's own
                    # storage, of what a holder points at, of another member or
                    # of an element.
                    pass
                case MIRCopy() | MIRBorrow() if leaf_place or elements or owned_storage(target) or leaf_borrow(target):
                    # Reads of an owned leaf into the body's own storage, a
                    # field's buffer, or holders (view holders included).
                    pass
                case MIRCopy(source=source) | MIRBorrow(source=source) | MIRRead(source=source) if (
                        _container_access(source, target)):
                    # Element reads and borrows, container views, and borrows
                    # of a container field: reads the dependency pass tracks.
                    pass
                case MIRRead(source=source):
                    # Scalar field reads, through inline record members too, are
                    # safe on a live borrowed record and on the body's private
                    # record storage; wrapper extraction and other projections
                    # need more proof.
                    path = source.projections
                    if path and not (_member_chain(source, slots, private_records())
                                     and storage_leaf(path[-1].type)):
                        return MIRSummaryResult.opaque("summary unsupported read projection")
                case MIRAlias():
                    if target.value_kind not in (MIRValueKind.BORROWED, MIRValueKind.BORROWED_CONTAINER):
                        return MIRSummaryResult.opaque("summary unsupported alias")
                case MIRConstruct() | MIRMove() if own_storage or elements or private_target or member:
                    # Building the body's own storage, or handing it to an
                    # element or a member (which takes the moved value; the
                    # moved storage is a transfer of `_private_records`).
                    pass
                case MIRIteratorInit() | MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    pass
                case _:
                    return MIRSummaryResult.opaque("summary unsupported operation")
    transfers = _transfers(body, dependencies, parameters)
    if isinstance(transfers, str):
        return MIRSummaryResult.opaque(transfers)
    for transfer in transfers:
        if not _transfer_in_layouts(declaration, definitions, tuple(bindings), transfer, callee.signature.return_type):
            return MIRSummaryResult.opaque("summary transfer field differs from definition")
    summary = MIRCallSummary(callee, tuple(bindings),
                             frozenset(range(len(params))), frozenset(writes), frozenset(),
                             frozenset(returns), transfers, not body.exceptional_exits,
                             frozenset(global_reads))
    problem = summary_problem(summary)
    if problem is not None:
        return MIRSummaryResult.opaque(problem)
    return MIRSummaryResult(MIRSummaryState.KNOWN, summary=summary)

"""Local effect/exit evidence from validated MIR, without callee scheduling."""

from ..thir import nodes as th
from ..thir.scalar_leaves import (
    native_container_subject, native_container_type, owned_leaf, owned_value_type, storage_leaf, view_leaf,
)
from ..typesys import TpyType, unwrap_readonly
from .call_contract import (
    BORROWING_PASSINGS, OWNING_PASSINGS, MIRCallSummary, MIRGlobalId, MIRParameterBinding, MIRParameterWrite,
    MIRReturnOrigin, MIRSummaryResult, MIRSummaryState, bound_result, summary_problem,
)
from .call_effects import resolve_call_writes
from .coverage import (
    MIRUnsupported, container_view_holder, leaf_borrow, owned_storage, scalar_slot, view_holder,
)
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, MIRReferent, analyze_dependencies, resolve_referents
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRConstruct,
    MIRContainerElements, MIRContainerStructure, MIRCopy, MIRDeref, MIRField, MIRFunction, MIRIteratorAdvance,
    MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMove, MIRNot, MIRNotCovered, MIROp,
    MIROptionalPayload, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead, MIRReturn, MIRPlace, MIRSlot, MIRSlotKind,
    MIRSlotId, MIRTupleIndex, MIRUnionPayload, MIRValueKind, statement_call,
)
from .validate import owned_leaf_place_type, statement_reads, validate_function


def _private_records(body: MIRFunction, dependencies: MIRDependencies) -> frozenset[MIRSlotId]:
    """The OWNED record slots whose storage is private to the body: read
    only by the borrow its holder takes and by the transfers that hand it
    over -- a return (the caller then owns it), a move out, or an argument
    at an owning passing -- with no borrow of it live at a transfer.
    Borrows that complete before the transfer are harmless. A slot never
    handed over stays out: storage the body keeps to its end is not
    summarized yet."""
    records = {s.id for s in body.slots
               if s.value_kind is MIRValueKind.OWNED and s.container_layout is None and not owned_storage(s)}
    excluded: set[MIRSlotId] = set()
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
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and stmt.value.source in records:
                transfers.append((stmt.value.source, point))
                reads = tuple(r for r in reads if r != stmt.value.source)
            if (call := statement_call(stmt)) is not None:
                handed = {arg for arg, binding in zip(call.arguments, call.summary.parameters)
                          if binding.passing in OWNING_PASSINGS}
                lent = {arg for arg, binding in zip(call.arguments, call.summary.parameters)
                        if binding.passing not in OWNING_PASSINGS}
                for arg in handed & records:
                    transfers.append((arg, point))
                excluded.update(lent & records)
                reads = tuple(r for r in reads if r not in handed)
            excluded.update(r for r in reads if r in records)
    for slot, point in transfers:
        held = dependencies.holders.get(point, {})
        if any(not ref.external and ref.place.root == slot and owners for ref, owners in held.items()):
            excluded.add(slot)
    return frozenset(slot for slot, _ in transfers) - excluded


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
            or callee.signature.return_type != body.return_type):
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
            path = origin.place.projections
            if not origin.external and slots[origin.place.root].value_kind is MIRValueKind.OWNED:
                # A write into the body's own storage: nothing the caller can observe.
                continue
            if not origin.external or origin.place.root not in parameters:
                return "summary unsupported write origin"
            # Published: one leaf or owned-leaf field of a record parameter, or
            # a container's shape or elements region, directly under a
            # container parameter or under one container field of a record.
            region = bool(path) and isinstance(path[-1], (MIRContainerStructure, MIRContainerElements))
            fields = path[:-1] if region else path
            if (len(fields) > 1 or not all(isinstance(f, MIRField) for f in fields)
                    or not region and not (fields and (storage_leaf(fields[0].type) or owned_leaf(fields[0].type)))):
                return "summary unsupported write origin"
            for field in fields:
                if field not in definitions.get(declaration, field.id.owner).layout.fields:
                    return "summary write field differs from definition"
            writes.add(MIRParameterWrite(parameters[origin.place.root], (
                *(th.THIRFieldIdentity(f.id.owner, f.id.name, f.type) for f in fields), *path[len(fields):])))
        return None

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
                    # Inside a record parameter: the record itself, or one of its
                    # fields -- a container a container result is, an owned leaf a
                    # view result views. A record result from inside a record is
                    # deferred with nested records.
                    if len(path) > 1 or path and not (
                            isinstance(path[0], MIRField) and (view or native_container_type(unwrap_readonly(path[0].type)))):
                        return MIRSummaryResult.opaque("summary unsupported return origin")
                    for field in path:
                        if field not in definitions.get(declaration, field.id.owner).layout.fields:
                            return MIRSummaryResult.opaque("summary return field differs from definition")
                    returns.add(MIRReturnOrigin(index, tuple(
                        th.THIRFieldIdentity(f.id.owner, f.id.name, f.type) for f in path)))
        for index, stmt in enumerate(block.statements):
            state = dependencies.referents.get(MIRPoint(block.id, index), {})
            if (call := statement_call(stmt)) is not None:
                problem = include_writes(resolve_call_writes(call, state, slots))
                if problem is not None:
                    return MIRSummaryResult.opaque(problem)
            if isinstance(stmt, MIRCallStmt):
                continue
            if isinstance(stmt, MIRPrint):
                # Output is an effect outside every parameter-rooted summary.
                return MIRSummaryResult.opaque("summary output effect")
            if not isinstance(stmt, MIRAssign):
                return MIRSummaryResult.opaque("summary storage operation")
            target = slots[stmt.target.root]
            if target.kind is MIRSlotKind.GLOBAL:
                return MIRSummaryResult.opaque("summary global access")
            # A write event is admitted on the body's own storage, which no
            # caller-visible place reaches, and on an owned-leaf field or a
            # container's elements, whose write origin is published below.
            leaf_place = owned_leaf_place_type(stmt.target, slots) is not None
            own_storage = not stmt.target.projections and target.value_kind is MIRValueKind.OWNED
            elements = stmt.target.projections[-1:] == (MIRContainerElements(),)
            if stmt.storage_write is not None and not (leaf_place or own_storage or elements):
                return MIRSummaryResult.opaque("summary storage operation")
            if stmt.target.projections:
                origins = resolve_referents(stmt.target, state, slots)
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
                case MIRCopy() if own_storage and stmt.target.root in private_records():
                    # A record copied into the body's private storage.
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
                    # Scalar field reads are safe on a live borrowed record;
                    # wrapper extraction and other projections need more proof.
                    if source.projections and not (
                        slots[source.root].value_kind is MIRValueKind.BORROWED
                        and len(source.projections) == 2
                        and isinstance(source.projections[0], MIRDeref)
                        and isinstance(source.projections[1], MIRField)
                        and storage_leaf(source.projections[1].type)
                    ):
                        return MIRSummaryResult.opaque("summary unsupported read projection")
                case MIRAlias():
                    if target.value_kind not in (MIRValueKind.BORROWED, MIRValueKind.BORROWED_CONTAINER):
                        return MIRSummaryResult.opaque("summary unsupported alias")
                case MIRConstruct() | MIRMove() if own_storage or elements:
                    # Building the body's own storage, or handing it to an
                    # element (the container takes the moved value).
                    pass
                case MIRIteratorInit() | MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    pass
                case _:
                    return MIRSummaryResult.opaque("summary unsupported operation")
    summary = MIRCallSummary(callee, tuple(bindings),
                             frozenset(range(len(params))), frozenset(writes), frozenset(),
                             frozenset(returns), frozenset(), not body.exceptional_exits,
                             frozenset(global_reads))
    problem = summary_problem(summary)
    if problem is not None:
        return MIRSummaryResult.opaque(problem)
    return MIRSummaryResult(MIRSummaryState.KNOWN, summary=summary)

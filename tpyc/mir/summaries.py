"""Local effect/exit evidence from validated MIR, without callee scheduling."""

from ..thir import nodes as th
from ..thir.scalar_leaves import owned_leaf, owned_value_type, storage_leaf, view_leaf
from ..typesys import unwrap_readonly, unwrap_ref_type
from .call_contract import (
    BORROWING_PASSINGS, MIRCallSummary, MIRGlobalId, MIRParameterBinding, MIRParameterWrite, MIRSummaryResult,
    MIRSummaryState, borrowed_result_of, summary_problem,
)
from .call_effects import resolve_call_writes
from .coverage import MIRUnsupported, leaf_borrow, owned_storage, scalar_slot, view_holder
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, analyze_dependencies, resolve_referents
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRCopy, MIRDeref,
    MIRField, MIRFunction, MIRNot, MIRNotCovered, MIROp, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead, MIRReturn,
    MIRPlace, MIRSlotKind, MIRValueKind, statement_call,
)
from .validate import owned_leaf_place_type, validate_function


def summarize_function(declaration: th.THIRFunction, body: MIRFunction,
                       definitions: MIRDefinitions) -> MIRSummaryResult:
    """Unsupported evidence is opaque; malformed MIR remains a validation error.

    The summary covers every exit of the body. Possible writes are collected
    at every statement, so a write before a throw is in them, and every
    nested call's writes are promised over its own exits too; nothing the
    body holds survives an exit but its storage, which the body owns. Loops
    are summarized by the dependency and liveness fixpoints."""
    validate_function(body)
    callee = declaration.resolved_callee
    if (callee is None or body.kind is not MIRBodyKind.FREE_FUNCTION
            or declaration.receiver is not None or body.receiver_init is not None
            or body.borrowed_result != borrowed_result_of(callee.signature)
            or callee.signature.return_type != body.return_type):
        return MIRSummaryResult.opaque("summary definition or result contract mismatch")
    params = tuple(s for s in body.slots if s.kind is MIRSlotKind.PARAMETER)
    if (len(params) != len(declaration.params)
            or callee.signature.param_types != tuple(p.type for p in declaration.params)):
        return MIRSummaryResult.opaque("summary definition signature mismatch")
    bindings: list[MIRParameterBinding] = []
    for slot, param in zip(params, declaration.params):
        if param.passing is None:
            return MIRSummaryResult.opaque("summary parameter passing unpublished")
        owned = owned_value_type(param.type)
        view = view_holder(slot)
        if (slot.name != param.name or slot.passing is not param.passing
                or slot.type != (owned if owned is not None else unwrap_readonly(unwrap_ref_type(param.type)))
                or (slot.value_kind is MIRValueKind.BORROWED and owned is None and not view
                    and (param.borrowed_record is None
                         or slot.readonly != param.borrowed_record.readonly))):
            return MIRSummaryResult.opaque("summary parameter binding mismatch")
        # An owned leaf at a borrowing passing and a view are lent, read-only.
        readonly = (param.borrowed_record.readonly if param.borrowed_record is not None
                    else view or owned is not None and param.passing in BORROWING_PASSINGS)
        bindings.append(MIRParameterBinding(slot.type, param.passing, readonly, param.borrowed_record))
    slots = {s.id: s for s in body.slots}
    global_reads: set[MIRGlobalId] = set()
    for slot in body.slots:
        if slot.kind is MIRSlotKind.GLOBAL:
            # A handle the body only reads; a write refuses at its statement.
            global_reads.add(slot.global_id)
        match slot.value_kind:
            case MIRValueKind.SCALAR if scalar_slot(slot):
                pass
            case MIRValueKind.BORROWED if view_holder(slot):
                # A view holds a loan on owned-leaf storage, which has no definition to verify.
                pass
            case MIRValueKind.BORROWED:
                try:
                    definitions.get(declaration, slot.type)
                except MIRUnsupported as failure:
                    return MIRSummaryResult.opaque(f"summary record: {failure.reason}")
            case MIRValueKind.OWNED if owned_storage(slot):
                # An owned leaf's storage is private to the body: nothing of the caller's.
                pass
            case _:
                return MIRSummaryResult.opaque("summary storage or value shape")
    for consumed in body.call_summaries:
        global_reads.update(consumed.global_reads)
    dependencies = analyze_dependencies(body, analyze_liveness(body))
    if isinstance(dependencies, MIRNotCovered):
        return MIRSummaryResult.opaque(f"summary dependencies: {dependencies.reason}")
    parameters = {slot.id: i for i, slot in enumerate(params)}
    writes: set[MIRParameterWrite] = set()
    returns: set[int] = set()

    def include_writes(origins: frozenset[MIRReferent] | None) -> str | None:
        if origins is None:
            return "summary missing write origin"
        for origin in origins:
            path = origin.place.projections
            if not origin.external and slots[origin.place.root].value_kind is MIRValueKind.OWNED:
                # A write into the body's own storage: nothing the caller can observe.
                continue
            if (not origin.external or origin.place.root not in parameters or len(path) != 1
                    or not isinstance(path[0], MIRField)
                    or not (storage_leaf(path[0].type) or owned_leaf(path[0].type))):
                return "summary unsupported write origin"
            field = path[0]
            if field not in definitions.get(declaration, field.id.owner).layout.fields:
                return "summary write field differs from definition"
            writes.add(MIRParameterWrite(parameters[origin.place.root], (
                th.THIRFieldIdentity(field.id.owner, field.id.name, field.type),)))
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
                    if view:
                        # Return origins name parameters only: a view of a parameter's
                        # field returns as the parameter, which is a safe over-approximation;
                        # a global, a static literal or the body's own storage has no index.
                        if not origin.external or origin.place.root not in parameters:
                            return MIRSummaryResult.opaque("view result origin outside the parameters")
                    elif not origin.external or origin.place.root not in parameters or origin.place.projections:
                        return MIRSummaryResult.opaque("summary unsupported return origin")
                    returns.add(parameters[origin.place.root])
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
            # A write event is admitted on the body's own owned-leaf storage,
            # which no caller-visible place reaches, and on an owned-leaf
            # field, whose write origin is published below.
            leaf_place = owned_leaf_place_type(stmt.target, slots) is not None
            if stmt.storage_write is not None and not leaf_place:
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
                case MIRCopy() | MIRBorrow() if leaf_place or owned_storage(target) or leaf_borrow(target):
                    # Reads of an owned leaf into the body's own storage, a
                    # field's buffer, or holders (view holders included).
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
                    if target.value_kind is not MIRValueKind.BORROWED:
                        return MIRSummaryResult.opaque("summary unsupported alias")
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

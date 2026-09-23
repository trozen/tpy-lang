"""Local effect/exit evidence from validated MIR, without callee scheduling."""

from ..thir import nodes as th
from ..typesys import BOOL, INT32, VoidType, unwrap_readonly, unwrap_ref_type
from .call_contract import MIRCallSummary, MIRParameterWrite, MIRSummaryResult, MIRSummaryState, summary_problem
from .coverage import MIRUnsupported
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies, resolve_referents
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyKind, MIRCall, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFunction, MIRNot, MIRNotCovered, MIRPoint, MIRRead, MIRSlotKind, MIRValueKind,
)
from .validate import _cyclic_blocks, successors, validate_function


def summarize_function(declaration: th.THIRFunction, body: MIRFunction,
                       definitions: MIRDefinitions) -> MIRSummaryResult:
    """Unsupported evidence is opaque; malformed MIR remains a validation error."""
    validate_function(body)
    callee = declaration.resolved_callee
    if (callee is None or body.kind is not MIRBodyKind.FREE_FUNCTION
            or declaration.receiver is not None or body.receiver_init is not None
            or (body.return_type not in (BOOL, INT32) and not isinstance(body.return_type, VoidType))
            or callee.signature.return_type != body.return_type):
        return MIRSummaryResult.opaque("summary needs resolved ordinary scalar-or-void definition")
    params = tuple(s for s in body.slots if s.kind is MIRSlotKind.PARAMETER)
    if (len(params) != len(declaration.params)
            or callee.signature.param_types != tuple(p.type for p in declaration.params)):
        return MIRSummaryResult.opaque("summary definition signature mismatch")
    for slot, param in zip(params, declaration.params):
        if (slot.name != param.name
                or slot.type != unwrap_readonly(unwrap_ref_type(param.type))
                or (slot.value_kind is MIRValueKind.BORROWED_RECORD
                    and (param.borrowed_record is None
                         or slot.readonly != param.borrowed_record.readonly))):
            return MIRSummaryResult.opaque("summary parameter binding mismatch")
    slots = {s.id: s for s in body.slots}
    for slot in body.slots:
        if slot.kind is MIRSlotKind.GLOBAL:
            return MIRSummaryResult.opaque("summary global access")
        match slot.value_kind:
            case MIRValueKind.SCALAR if slot.type in (BOOL, INT32):
                pass
            case MIRValueKind.BORROWED_RECORD:
                try:
                    definitions.get(declaration, slot.type)
                except MIRUnsupported as failure:
                    return MIRSummaryResult.opaque(f"summary record: {failure.reason}")
            case _:
                return MIRSummaryResult.opaque("summary storage or value shape")
    blocks = {b.id: b for b in body.blocks}
    predecessors = {b.id: set() for b in body.blocks}
    for block in body.blocks:
        for target in successors(block.terminator):
            predecessors[target].add(block.id)
    if _cyclic_blocks(blocks, predecessors):
        return MIRSummaryResult.opaque("summary cyclic control flow")
    dependencies = analyze_dependencies(body, analyze_liveness(body))
    if isinstance(dependencies, MIRNotCovered):
        return MIRSummaryResult.opaque(f"summary dependencies: {dependencies.reason}")
    parameters = {slot.id: i for i, slot in enumerate(params)}
    writes: set[MIRParameterWrite] = set()
    for block in body.blocks:
        for index, stmt in enumerate(block.statements):
            if not isinstance(stmt, MIRAssign) or stmt.storage_write is not None:
                return MIRSummaryResult.opaque("summary storage operation")
            target = slots[stmt.target.root]
            if stmt.target.projections:
                state = dependencies.referents.get(MIRPoint(block.id, index), {})
                origins = resolve_referents(stmt.target, state, slots)
                if not origins:
                    return MIRSummaryResult.opaque("summary missing write origin")
                for origin in origins:
                    path = origin.place.projections
                    if (not origin.external or origin.place.root not in parameters or len(path) != 1
                            or not isinstance(path[0], MIRField) or path[0].type not in (BOOL, INT32)):
                        return MIRSummaryResult.opaque("summary unsupported write origin")
                    field = path[0]
                    if field not in definitions.get(declaration, field.id.owner).layout.fields:
                        return MIRSummaryResult.opaque("summary write field differs from definition")
                    writes.add(MIRParameterWrite(parameters[origin.place.root], (
                        th.THIRFieldIdentity(field.id.owner, field.id.name, field.type),)))
            match stmt.value:
                case MIRCall():
                    # The workspace supplies semantic evidence; validation
                    # checks its contract and membership in this body's table.
                    pass
                case MIRConstant() | MIRCompare() | MIRNot():
                    pass
                case MIRRead(source=source):
                    # Scalar field reads are safe on a live borrowed record;
                    # wrapper extraction and other projections need more proof.
                    if source.projections and not (
                        slots[source.root].value_kind is MIRValueKind.BORROWED_RECORD
                        and len(source.projections) == 2
                        and isinstance(source.projections[0], MIRDeref)
                        and isinstance(source.projections[1], MIRField)
                        and source.projections[1].type in (BOOL, INT32)
                    ):
                        return MIRSummaryResult.opaque("summary unsupported read projection")
                case MIRAlias():
                    if target.value_kind is not MIRValueKind.BORROWED_RECORD:
                        return MIRSummaryResult.opaque("summary unsupported alias")
                case _:
                    return MIRSummaryResult.opaque("summary unsupported operation")
    summary = MIRCallSummary(callee, tuple(p.borrowed_record for p in declaration.params),
                             frozenset(range(len(params))), frozenset(writes), frozenset(),
                             frozenset(), frozenset(), True)
    problem = summary_problem(summary)
    if problem is not None:
        return MIRSummaryResult.opaque(problem)
    return MIRSummaryResult(MIRSummaryState.KNOWN, summary=summary)

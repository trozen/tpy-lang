"""Bind internal lifetime evidence to the exact THIR and lowering request.

This API has no source-admission or emission authority. Unsupported storage
producers remain visible even when the temporary planner cannot model them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..identity_map import IdentityMap
from ..thir import nodes as th
from ..thir.storage_facts import THIRBackingKind, THIRStorageFacts, validate_storage_facts
from ..thir.temp_plan import THIRTempPlan, validate_plan
from .call_contract import MIRSummaryResult
from .definitions import MIRDefinitions
from .lower import lower_constructor_storage, lower_function_storage
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRSlotId
from .storage_evidence import (
    MIRBorrowEvidence, MIRStorageEvidence, MIRStorageVerdict,
    certify_borrow_operations, certify_storage_origins,
)
from .validate import MIRValidationError


@dataclass(frozen=True, eq=False)
class MIRStorageRequest:
    source: th.THIRFunction | th.THIRConstructor
    body: MIRBodyId
    kind: MIRBodyKind
    definitions: MIRDefinitions
    summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult]
    facts: THIRStorageFacts | None = field(init=False)
    plan: THIRTempPlan | None = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", self.source.storage_facts)
        object.__setattr__(self, "plan", self.source.temp_plan)
        # A caller mutating its workspace map cannot change an issued request.
        object.__setattr__(self, "summaries", MappingProxyType(dict(self.summaries)))


@dataclass(frozen=True, eq=False)
class MIRBoundStorageEvidence:
    request: MIRStorageRequest
    function: MIRFunction | None
    backings: Mapping[th.THIRExpr, MIRPlace]
    operations: Mapping[th.THIRStmt, tuple[MIRPoint, ...]]
    evidence: MIRStorageEvidence | MIRBorrowEvidence | None
    gaps: tuple[MIRNotCovered, ...]

    @property
    def requires_proof(self) -> bool | None:
        facts = self.request.facts
        return None if facts is None else bool(facts.backings or facts.obligations)

    @property
    def verdict(self) -> MIRStorageVerdict:
        if self.evidence is not None and self.evidence.verdict is MIRStorageVerdict.CONFLICT:
            return MIRStorageVerdict.CONFLICT
        if self.gaps or self.evidence is None:
            return MIRStorageVerdict.NOT_COVERED
        return self.evidence.verdict

    def certifies(self, request: MIRStorageRequest, source: th.THIRFunction | th.THIRConstructor,
                  function: MIRFunction) -> bool:
        if not (request is self.request and source is request.source and function is self.function
                and source.temp_plan is request.plan and source.storage_facts is request.facts
                and self.evidence is not None and self.evidence.function is function
                and self.evidence.definitions is request.definitions
                and self.verdict is MIRStorageVerdict.CERTIFIED):
            return False
        roots = frozenset(self.backings[b.node].root for b in request.facts.backings)
        if isinstance(self.evidence, MIRBorrowEvidence):
            operations = frozenset(p for o in request.facts.obligations for p in self.operations[o.sink])
            return self.evidence.certifies_operations(function, operations, roots)
        return not request.facts.obligations and self.evidence.certifies(function, roots)


def certify_thir_storage(request: MIRStorageRequest) -> MIRBoundStorageEvidence:
    """Lower once and certify all recorded materialized origins in that body.

    A body with no origins and no obligations has `requires_proof == False`;
    it receives no vacuous storage certificate. Missing facts instead report
    `None`, and an obligation with no known backing still requires proof.
    """
    source, facts, plan = request.source, request.facts, request.plan
    gaps: list[MIRNotCovered] = []

    def gap(reason: str, node: object = source) -> None:
        gaps.append(MIRNotCovered(request.body, type(node).__name__, reason, getattr(node, "loc", None)))

    if source.temp_plan is not plan or source.storage_facts is not facts:
        raise MIRValidationError("stale storage request")
    if facts is None:
        gap("storage facts have not been published")
    else:
        initializers = source.mil_inits + source.base_inits if isinstance(source, th.THIRConstructor) else ()
        borrowed_result = (source.resolved_callee.signature.borrowed_result
                           if isinstance(source, th.THIRFunction) and source.resolved_callee is not None else None)
        validate_storage_facts(source.body, plan, facts, initializers, borrowed_result=borrowed_result)
        if plan is not None:
            validate_plan(source.body, plan)
        for backing in facts.backings:
            if backing.uncovered is not None:
                gap(backing.uncovered, backing.node)
            elif backing.kind not in (THIRBackingKind.ARGUMENT, THIRBackingKind.FULL_EXPRESSION,
                                      THIRBackingKind.SELECT_SLOT):
                gap("materialized storage kind is not connected to lifetime evidence", backing.node)
        if not facts.backings and not facts.obligations:
            gap("body has no materialized storage or borrowed-expression obligations")

    if isinstance(source, th.THIRConstructor):
        if request.kind is not MIRBodyKind.CONSTRUCTOR:
            raise MIRValidationError("storage request kind differs from its constructor")
        lowered = lower_constructor_storage(source, request.body, definitions=request.definitions,
                                            summaries=request.summaries)
    else:
        lowered = lower_function_storage(source, request.body, kind=request.kind,
                                         definitions=request.definitions, summaries=request.summaries)
    if isinstance(lowered, MIRNotCovered):
        gaps.append(lowered)
        return MIRBoundStorageEvidence(request, None, MappingProxyType({}), MappingProxyType({}), None, tuple(gaps))
    backings = lowered.backings
    roots: set[MIRSlotId] = set()
    operations: set[MIRPoint] = set()
    slots = {slot.id for slot in lowered.function.slots}
    blocks = {block.id: block for block in lowered.function.blocks}
    if facts is not None:
        for backing in facts.backings:
            place = backings.get(backing.node)
            if place is None:
                gap("materialized storage has no MIR backing correspondence", backing.node)
            elif place.root not in slots:
                gap("materialized storage is absent from the reachable MIR body", backing.node)
            else:
                roots.add(place.root)
        if any(node not in facts.by_node for node in backings):
            raise MIRValidationError("lowered storage is absent from THIR storage facts")
        occurrences: IdentityMap[th.THIRStmt, int] = IdentityMap()
        for obligation in facts.obligations:
            occurrences[obligation.sink] = occurrences.get(obligation.sink, 0) + 1
        for sink, count in occurrences.items():
            points = lowered.operations.get(sink)
            if not points:
                gap("borrowed operation has no MIR correspondence", sink)
                continue
            if len(points) != count or len(set(points)) != count:
                gap("borrowed operation occurrence count differs from MIR correspondence", sink)
            for point in points:
                block = blocks.get(point.block)
                if block is None or point.index > len(block.statements):
                    gap("borrowed operation is absent from the reachable MIR body", sink)
                else:
                    operations.add(point)
    if facts is not None and facts.obligations:
        evidence = (certify_borrow_operations(lowered.function, frozenset(operations),
                                              frozenset(roots), request.definitions) if operations else None)
    else:
        evidence = (certify_storage_origins(lowered.function, frozenset(roots), request.definitions)
                    if roots else None)
    return MIRBoundStorageEvidence(request, lowered.function, backings, lowered.operations, evidence, tuple(gaps))

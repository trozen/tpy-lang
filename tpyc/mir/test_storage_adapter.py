"""Exact THIR requests retain backing identity through the ordinary MIR builder."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from ..thir import nodes as th
from ..identity_map import IdentityMap
from ..thir.storage_facts import collect_storage_facts
from ..thir.temp_plan import prepare_temporaries
from ..typesys import BOOL
from . import lower as lowering
from .dependencies import MIRReferent
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBodyKind, MIRConstruct, MIRGoto,
    MIRPlace, MIRReturn, MIRTupleIndex, MIRUnionPayload, MIRValueKind,
)
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import MIRBorrowEvidence, MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins
from .test_borrowed_argument_storage import SOURCE, Artifacts, _compile_workspace, _thir
from .validate import MIRRepeatedInitializationError, MIRValidationError, validate_function


EXTRA = '''
def forward(cell: readonly[Cell]) -> readonly[Cell]:
    return cell

def printed(value: int32) -> int32:
    saved = observe(Cell(value))
    print(saved.value)
    return saved.value

def select(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    return saved.value

def inline(value: int32) -> int32:
    return Cell(value).value

def stable(owner: Cell) -> int32:
    saved = owner
    return saved.value

def unknown(owner: Cell) -> int32:
    saved = observe(owner)
    return saved.value

def scalar(value: int32) -> int32:
    return value

def chain(owner: Cell) -> readonly[Cell]:
    saved = observe(owner)
    alias = saved
    return alias

def reseat(first: Cell, second: Cell) -> readonly[Cell]:
    saved = observe(first)
    alias = saved
    saved = observe(second)
    return alias

def local(value: int32) -> int32:
    owner = Cell(value)
    alias = owner
    owner.value = 7
    return alias.value

def selected_alias(flag: bool, first: Cell, second: Cell) -> readonly[Cell]:
    saved = observe(first) if flag else observe(second)
    return saved

def tuple_alias(pair: tuple[Cell, int32], owner: Cell) -> int32:
    saved = pair[0]
    owner.value = 9
    return saved.value

class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def union_alias(owner: Cell | Other) -> int32:
    if isinstance(owner, Cell):
        saved = owner
        return saved.value
    return 0

def optional_alias(owner: Cell | None) -> int32:
    if owner is not None:
        saved = owner
        return saved.value
    return 0

def aggregate_alias(owner: Cell | None) -> int32:
    saved = owner
    if saved is not None:
        return saved.value
    return 0

class AliasCaller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        owner = Cell(value)
        saved = owner
        owner.value = 7
        self.value = saved.value
    def alias_method(self, owner: Cell) -> int32:
        saved = owner
        return saved.value

def escape(flag: bool, value: int32, owner: Cell) -> int32:
    outer = observe(owner)
    if flag:
        saved = observe(owner)
        outer = saved
        saved = observe(owner)
    return outer.value

def escape_call(flag: bool, value: int32, owner: Cell) -> int32:
    outer = observe(owner)
    if flag:
        saved = observe(owner)
        outer = forward(saved)
        saved = observe(owner)
    return outer.value

'''


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    return _compile_workspace(SOURCE + EXTRA)


def _request(artifacts: Artifacts, fn: th.THIRFunction | th.THIRConstructor) -> MIRStorageRequest:
    kind = (MIRBodyKind.CONSTRUCTOR if isinstance(fn, th.THIRConstructor) else
            MIRBodyKind.METHOD if fn.receiver is not None else MIRBodyKind.FREE_FUNCTION)
    name = fn.record_name if isinstance(fn, th.THIRConstructor) else fn.name
    return MIRStorageRequest(fn, MIRBodyId("main", name), kind, artifacts[2], artifacts[1].summaries)


@pytest.mark.parametrize("name", ["eager", "method", "Caller", "loop", "ranges"])
def test_source_backings_use_actual_builder_places(artifacts: Artifacts, name: str) -> None:
    fn = (next(c for c in artifacts[0].thir_constructors.values() if c.record_name == name)
          if name == "Caller" else _thir(artifacts, name))
    request = _request(artifacts, fn)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    assert result.certifies(request, fn, result.function)
    assert result.requires_proof
    assert {p.root for p in result.backings.values()} == result.evidence.required
    for backing in fn.storage_facts.backings:
        assert backing.node in result.backings
        assert backing.placement is request.plan.placement(backing.node)


@pytest.mark.parametrize("name,reason", [
    ("printed", "argument storage has no temporary plan"),
    ("select", "select slot placement is not planned"),
    ("inline", "full-expression storage is not connected"),
])
def test_unmodeled_storage_remains_an_obligation(artifacts: Artifacts, name: str, reason: str) -> None:
    result = certify_thir_storage(_request(artifacts, _thir(artifacts, name)))
    assert result.requires_proof is True
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(reason in gap.reason for gap in result.gaps)


def test_no_obligation_and_unpublished_facts_are_distinct(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "scalar")
    result = certify_thir_storage(_request(artifacts, fn))
    assert result.requires_proof is False and result.evidence is None
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    result = certify_thir_storage(_request(artifacts, replace(fn, storage_facts=None)))
    assert result.requires_proof is None
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("have not been published" in g.reason for g in result.gaps)


def test_certificate_never_crosses_request_body_or_mir_identity(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    request = _request(artifacts, fn)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CERTIFIED
    assert not result.certifies(_request(artifacts, fn), fn, result.function)
    assert not result.certifies(request, replace(fn), result.function)
    assert not result.certifies(request, fn, replace(result.function))
    fresh_plan = prepare_temporaries(fn.body)
    assert fresh_plan is not fn.temp_plan
    with pytest.raises(ValueError, match="stale storage facts"):
        certify_thir_storage(_request(artifacts, replace(fn, temp_plan=fresh_plan)))
    # Equal-looking THIR arguments must not hit the identity-keyed builder map.
    backing, = fn.storage_facts.backings
    assert replace(backing.node) not in result.backings


def test_summary_snapshot_and_missing_effects(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    summaries = dict(artifacts[1].summaries)
    request = replace(_request(artifacts, fn), summaries=summaries)
    summaries.clear()
    assert certify_thir_storage(request).verdict is MIRStorageVerdict.CERTIFIED
    missing = certify_thir_storage(replace(request, summaries={}))
    assert missing.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("finalized known summary" in g.reason for g in missing.gaps)


def _with_body(fn: th.THIRFunction, body: tuple[th.THIRStmt, ...]) -> th.THIRFunction:
    plan = prepare_temporaries(body)
    result = fn.resolved_callee.signature.borrowed_result if fn.resolved_callee is not None else None
    return replace(fn, body=body, temp_plan=plan,
                   storage_facts=collect_storage_facts(body, plan, borrowed_result=result))


@pytest.mark.parametrize("name", ["escape", "escape_call", "escape_chain"])
def test_withdrawn_pointer_temporary_routes_conflict_internally(artifacts: Artifacts, name: str) -> None:
    fn = _thir(artifacts, "escape" if name == "escape_chain" else name)
    outer, branch, final = fn.body
    # Source admission withholds this PTR_ADDR initializer until the gate exists.
    # Preserve the real pointer/readonly facts and insert the real eager backing.
    saved, *rest = branch.then_body
    assert isinstance(saved, th.THIRPtrLocalDecl) and saved.is_const
    saved = replace(saved, init=_thir(artifacts, "eager").body[0].init)
    if name == "escape_chain":
        transfer = rest[0]
        alias = replace(saved, name="alias", init=transfer.value, alias_binding=transfer.alias_binding)
        transfer = replace(transfer, value=replace(transfer.value, name="alias"),
                           alias_binding=replace(transfer.alias_binding, source="alias"))
        rest[:1] = [alias, transfer]
    fn = _with_body(fn, (outer, replace(branch, then_body=(saved, *rest)), final))
    result = certify_thir_storage(_request(artifacts, fn))
    assert result.verdict is MIRStorageVerdict.CONFLICT, (result.gaps, result.evidence)
    assert any(c.kind is MIRStorageConflictKind.SCOPE_END for c in result.evidence.conflicts)
    assert all(c.origin.root in result.evidence.required for c in result.evidence.conflicts)


def test_returned_local_borrow_is_checked_without_successor_liveness(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    saved, _ = fn.body
    observe = _thir(artifacts, "observe")
    borrowed = observe.resolved_callee.signature.borrowed_result
    # Sema rejects returning this local; retain the explicit borrowed-return contract.
    result_name = th.THIRName(borrowed.type, "saved", form=th.Form.BORROW)
    final = replace(observe.body[0], value=result_name)
    fn = _with_body(replace(fn, return_type=observe.return_type, resolved_callee=observe.resolved_callee),
                    (saved, final))
    result = certify_thir_storage(_request(artifacts, fn))
    assert result.verdict is MIRStorageVerdict.CONFLICT, (result.gaps, result.evidence)
    assert any(c.kind is MIRStorageConflictKind.RETURN_ESCAPE for c in result.evidence.conflicts)


def test_nested_tuple_branch_inventory_is_not_lost(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    folded = th.THIRFoldedIfChain(((th.THIRLiteral(BOOL, True), fn.body),))
    fn = _with_body(fn, (folded,))
    assert fn.storage_facts.backings and fn.storage_facts.obligations
    result = certify_thir_storage(_request(artifacts, fn))
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert result.requires_proof


def test_missing_backing_facts_are_an_invariant_error(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    stale = replace(fn.storage_facts, backings=())
    with pytest.raises(ValueError, match="stale storage facts"):
        certify_thir_storage(_request(artifacts, replace(fn, storage_facts=stale)))


def test_wrong_constructor_kind_is_an_invariant_error(artifacts: Artifacts) -> None:
    ctor = next(c for c in artifacts[0].thir_constructors.values() if c.record_name == "Caller")
    with pytest.raises(MIRValidationError, match="kind differs"):
        certify_thir_storage(replace(_request(artifacts, ctor), kind=MIRBodyKind.FREE_FUNCTION))


def test_repeated_activation_boundary_does_not_swallow_malformed_mir(artifacts: Artifacts, monkeypatch) -> None:
    request = _request(artifacts, _thir(artifacts, "loop"))
    result = certify_thir_storage(request)
    fn = result.function
    construction = next(b for b in fn.blocks if any(
        isinstance(s, MIRAssign) and isinstance(s.value, MIRConstruct) for s in b.statements))
    malformed = replace(fn, blocks=tuple(
        replace(b, terminator=MIRGoto(b.id)) if b is construction else b for b in fn.blocks))
    with pytest.raises(MIRRepeatedInitializationError) as failure:
        certify_storage_origins(malformed, result.evidence.required, request.definitions)
    assert failure.value.loc is not None

    # Exercise the source-lowering boundary with the exact strict-validator failure.
    def repeated(_):
        validate_function(malformed)

    monkeypatch.setattr(lowering, "validate_function", repeated)
    uncovered = certify_thir_storage(request)
    assert uncovered.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(g.reason == str(failure.value) and g.loc == failure.value.loc for g in uncovered.gaps)

    def other_failure(_):
        raise MIRValidationError("unknown slot")

    monkeypatch.setattr(lowering, "validate_function", other_failure)
    with pytest.raises(MIRValidationError, match="unknown slot"):
        certify_thir_storage(request)


@pytest.mark.parametrize("name", ["forward", "stable", "unknown", "chain", "reseat", "selected_alias",
                                  "tuple_alias", "union_alias", "alias_method"])
def test_parameter_only_operations_need_no_temporary_plan(artifacts: Artifacts, name: str) -> None:
    source = _thir(artifacts, name)
    assert not source.storage_facts.backings
    borrowed_result = (source.resolved_callee.signature.borrowed_result
                       if source.resolved_callee is not None else None)
    source = replace(source, temp_plan=None, storage_facts=collect_storage_facts(
        source.body, None, borrowed_result=borrowed_result))
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    assert result.certifies(request, source, result.function)
    assert isinstance(result.evidence, MIRBorrowEvidence)
    assert not result.evidence.required
    assert result.evidence.operations == frozenset(p for o in source.storage_facts.obligations
                                                 for p in result.operations[o.sink])
    assert all(origins and all(origin.external for origin in origins)
               for origins in result.evidence.origins.values())


def test_correspondence_is_the_final_holder_write_and_return(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "chain")
    result = certify_thir_storage(_request(artifacts, source))
    slots = {slot.id: slot for slot in result.function.slots}
    blocks = {block.id: block for block in result.function.blocks}
    assert len(source.storage_facts.obligations) == 3
    for obligation in source.storage_facts.obligations:
        point, = result.operations[obligation.sink]
        block = blocks[point.block]
        if isinstance(obligation.sink, th.THIRReturn):
            assert point.index == len(block.statements)
            assert isinstance(block.terminator, MIRReturn)
        else:
            write = block.statements[point.index]
            assert isinstance(write, MIRAssign) and isinstance(write.value, MIRAlias)
            assert slots[write.target.root].name == obligation.sink.name
        assert replace(obligation.sink) not in result.operations
        with pytest.raises(TypeError):
            result.operations[obligation.sink] = ()


def test_reseating_holder_preserves_previous_alias_origin(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "reseat")
    result = certify_thir_storage(_request(artifacts, source))
    first, second = (slot.id for slot in result.function.slots if slot.name in ("first", "second"))
    assert len(source.storage_facts.obligations) == 4
    for obligation, expected in zip(source.storage_facts.obligations, (first, first, second, first)):
        point, = result.operations[obligation.sink]
        origins = result.evidence.origins[point]
        assert {origin.place.root for origin in origins} == {expected}


@pytest.mark.parametrize("name", ["selected_alias", "tuple_alias", "union_alias"])
def test_selected_and_payload_aliases_retain_exact_origins(artifacts: Artifacts, name: str) -> None:
    source = _thir(artifacts, name)
    result = certify_thir_storage(_request(artifacts, source))
    slots = {slot.name: slot for slot in result.function.slots if slot.name is not None}
    if name == "selected_alias":
        places = {MIRPlace(slots[name].id) for name in ("first", "second")}
    elif name == "tuple_alias":
        places = {MIRPlace(slots["pair"].id, (MIRTupleIndex(0),))}
    else:
        owner = slots["owner"]
        alternative = next(i for i, member in enumerate(owner.union_layout.elements) if member.type.name == "Cell")
        places = {MIRPlace(owner.id, (MIRUnionPayload(alternative),))}
    expected = frozenset(MIRReferent(place, external=True) for place in places)
    assert result.verdict is MIRStorageVerdict.CERTIFIED
    assert result.evidence.origins
    assert all(origins == expected for origins in result.evidence.origins.values())


@pytest.mark.parametrize("name", ["local", "AliasCaller"])
def test_local_alias_requires_ordinary_storage_root(artifacts: Artifacts, name: str) -> None:
    source = (next(c for c in artifacts[0].thir_constructors.values() if c.record_name == name)
              if name == "AliasCaller" else _thir(artifacts, name))
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    assert result.certifies(request, source, result.function)
    assert not result.backings and not result.evidence.explicit_roots
    root, = result.evidence.required
    assert next(slot for slot in result.function.slots if slot.id == root).value_kind is MIRValueKind.RECORD_STORAGE
    assert all(not origin.external and origin.place.root == root
               for origins in result.evidence.origins.values() for origin in origins)
    obligation, = source.storage_facts.obligations
    point, = result.operations[obligation.sink]
    block = next(block for block in result.function.blocks if block.id == point.block)
    write = block.statements[point.index]
    assert isinstance(write, MIRAssign) and isinstance(write.value, MIRAlias)
    assert next(slot for slot in result.function.slots if slot.id == write.target.root).name == obligation.sink.name
    assert replace(obligation.sink) not in result.operations


def test_dead_destination_still_has_an_operation(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "stable")
    # The scalar return no longer reads saved; its binding must still be proved.
    source = _with_body(source, (source.body[0], replace(source.body[1], value=th.THIRLiteral(source.return_type, 0))))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    assert len(result.evidence.origins) == 1


def test_pruned_operation_remains_uncovered(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "stable")
    # Coverage inspects the unreachable alias, but no MIR write is emitted for it.
    source = _with_body(source, (replace(source.body[-1], value=th.THIRLiteral(source.return_type, 0)),
                                 source.body[0]))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.requires_proof and result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("operation has no MIR correspondence" in g.reason for g in result.gaps)


def test_repeated_statement_keeps_each_point_even_when_one_is_pruned(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "stable")
    declaration, final = source.body
    branch = th.THIRIf(th.THIRLiteral(BOOL, True), (declaration,), (declaration,))
    source = _with_body(source, (branch, replace(final, value=th.THIRLiteral(source.return_type, 0))))
    result = certify_thir_storage(_request(artifacts, source))
    points = result.operations[declaration]
    assert len(points) == 2 and points[0] != points[1]
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("operation is absent from the reachable MIR body" in g.reason for g in result.gaps)


@pytest.mark.parametrize("reachable", [False, True])
@pytest.mark.parametrize("kind", ["reseat", "return"])
def test_repeated_sink_identity_needs_every_occurrence(artifacts: Artifacts, kind: str, reachable: bool) -> None:
    source = _thir(artifacts, "reseat" if kind == "reseat" else "selected_alias")
    if kind == "reseat":
        operation = source.body[2]
        body = (*source.body[:3], operation, source.body[-1]) if reachable else (*source.body, operation)
    else:
        operation = source.body[-1]
        branch = th.THIRIf(th.THIRName(BOOL, "flag"), (operation,), (operation,))
        body = (*source.body[:-1], branch) if reachable else (*source.body, operation)
    source = _with_body(source, body)
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert sum(o.sink is operation for o in source.storage_facts.obligations) == 2
    assert len(result.operations[operation]) == (2 if reachable else 1)
    assert result.certifies(request, source, result.function) is reachable
    assert result.verdict is (MIRStorageVerdict.CERTIFIED if reachable else MIRStorageVerdict.NOT_COVERED)
    if not reachable:
        assert any("occurrence count differs" in gap.reason for gap in result.gaps)


def test_missing_mapping_cannot_reuse_a_partial_proof(artifacts: Artifacts, monkeypatch) -> None:
    source = _thir(artifacts, "chain")
    request = _request(artifacts, source)
    lowered = lowering.lower_function_storage(source, request.body, kind=request.kind,
                                              definitions=request.definitions, summaries=request.summaries)
    omitted = source.storage_facts.obligations[1].sink
    mapping = IdentityMap((stmt, points) for stmt, points in lowered.operations.items() if stmt is not omitted)
    monkeypatch.setattr("tpyc.mir.storage_adapter.lower_function_storage",
                        lambda *args, **kwargs: replace(lowered, operations=MappingProxyType(mapping)))
    result = certify_thir_storage(request)
    assert result.evidence.verdict is MIRStorageVerdict.CERTIFIED
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert not result.certifies(request, source, result.function)


def test_changed_selected_return_contract_invalidates_facts(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "chain")
    signature = source.resolved_callee.signature
    cloned = replace(signature, borrowed_result=replace(signature.borrowed_result))
    source = replace(source, resolved_callee=replace(source.resolved_callee, signature=cloned))
    with pytest.raises(ValueError, match="stale storage facts"):
        certify_thir_storage(_request(artifacts, source))


@pytest.mark.parametrize("name", ["optional_alias", "aggregate_alias"])
def test_unmapped_payload_sink_keeps_its_obligation(artifacts: Artifacts, name: str) -> None:
    source = _thir(artifacts, name)
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert not result.certifies(result.request, source, result.function)

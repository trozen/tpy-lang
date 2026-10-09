"""Full-expression ends expire backing while preserving evaluated scalar results."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType, OptionalType, TupleType, UnionType
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBorrow, MIRBranch, MIRConstruct,
    MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRGoto, MIRNotCovered,
    MIRPlace, MIRRead, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
    MIROptionalConstruct, MIROptionalLayout, MIROptionalPayload,
    MIRTupleConstruct, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRUnionConstruct, MIRUnionLayout, MIRUnionPayload,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage_evidence import MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins
from .testutil import execute
from .validate import MIRValidationError, validate_function


@pytest.fixture(scope="module")
def definitions() -> MIRDefinitions:
    compiler, modules = _compile('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
''')
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return MIRDefinitions(tuple(ctx.thir_constructors.values()))


def constructor(definitions: MIRDefinitions, value: int = 7) -> th.THIRCtorCall:
    typ = next(iter(definitions.records))
    return th.THIRCtorCall(typ, "Cell", (th.THIRLiteral(INT32, value),),
                          form=th.Form.STORAGE, full_expression_storage=th.THIROwnedRecord(typ))


def read(definitions: MIRDefinitions, value: int = 7) -> th.THIRFieldAccess:
    ctor = constructor(definitions, value)
    return th.THIRFieldAccess(INT32, ctor, "value",
                             field_identity=th.THIRFieldIdentity(ctor.result_type, "value", INT32))


def function(expr: th.THIRExpr) -> th.THIRFunction:
    return th.THIRFunction("example", (th.THIRParam("flag", BOOL, passing=ParamPassing.VALUE),), expr.result_type,
                           (th.THIRReturn(expr),), th.THIRFunctionLayout())


def lower(fn: th.THIRFunction, definitions: MIRDefinitions) -> MIRFunction:
    validate_thir(fn)
    result = lower_function(fn, MIRBodyId("temporary", fn.name),
                            definitions=definitions)
    assert isinstance(result, MIRFunction), result
    validate_function(result)
    return result


def storage(fn: MIRFunction) -> tuple[MIRSlot, ...]:
    return tuple(s for s in fn.slots if s.value_kind is MIRValueKind.OWNED)


def test_return_value_survives_the_full_expression(definitions: MIRDefinitions) -> None:
    fn = lower(function(read(definitions)), definitions)
    owned, = storage(fn)
    assert isinstance(owned.storage_duration, MIRRegionId)
    result_block, = (b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    result = next(s for s in fn.slots if s.id == result_block.terminator.value)
    assert result.residence == result_block.region != owned.storage_duration
    ends = analyze_scope_ends(fn)
    edge, = ends.ends
    assert ends.ends[edge][0].storage == MIRPlace(owned.id)
    assert isinstance(next(b for b in fn.blocks if b.id == edge.source).terminator, MIRGoto)
    assert execute(fn, True) == 7
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("op", ["ternary", "and", "or"])
def test_lazy_arms_share_one_end_boundary(definitions: MIRDefinitions, op: str) -> None:
    flag = th.THIRName(BOOL, "flag")
    field = read(definitions)
    if op == "ternary":
        expr = th.THIRIfExpr(INT32, flag, field, read(definitions, 9))
        expected = (9, 7)
    else:
        comparison = th.THIRBinOp(BOOL, field, "==", th.THIRLiteral(INT32, 7), None)
        expr = th.THIRBinOp(BOOL, flag, "&&" if op == "and" else "||", comparison, None)
        expected = (False, True) if op == "and" else (True, True)
    fn = lower(function(expr), definitions)
    assert len({s.storage_duration for s in storage(fn)}) == 1
    # Lazy-arm jumps remain inside the expression; only its final join exits.
    ends = analyze_scope_ends(fn)
    assert len(ends.ends) == 1
    for flag_value, answer in zip((False, True), expected):
        heap = {}
        assert execute(fn, flag_value, heap=heap) == answer
        assert len(heap) == (1 if op == "ternary" or flag_value == (op == "and") else 0)


def test_two_temporaries_end_together_after_comparison(definitions: MIRDefinitions) -> None:
    expr = th.THIRBinOp(BOOL, read(definitions, 1), "<", read(definitions, 2), None)
    fn = lower(function(expr), definitions)
    assert execute(fn, True) is True
    events, = analyze_scope_ends(fn).ends.values()
    assert {e.storage for e in events} == {MIRPlace(s.id) for s in storage(fn)}


def test_condition_activation_ends_before_both_successors(definitions: MIRDefinitions) -> None:
    field = read(definitions)
    condition = th.THIRBinOp(BOOL, th.THIRName(BOOL, "flag"), "&&",
                            th.THIRBinOp(BOOL, field, "==", th.THIRLiteral(INT32, 7), None), None)
    source = th.THIRFunction("repeat", (th.THIRParam("flag", BOOL, passing=ParamPassing.VALUE),), INT32, (
        th.THIRWhile(condition, (th.THIRAssign(th.THIRName(BOOL, "flag"), th.THIRLiteral(BOOL, False)),)),
        th.THIRReturn(th.THIRLiteral(INT32, 5)),
    ), th.THIRFunctionLayout())
    fn = lower(source, definitions)
    assert execute(fn, True) == execute(fn, False) == 5
    owned, = storage(fn)
    ends = analyze_scope_ends(fn)
    edge, = ends.ends
    transitions = MIRRegionFlow(fn)
    after = next(b for b in fn.blocks if b.id == transitions.edges[edge].target)
    assert isinstance(after.terminator, MIRBranch)
    assert after.region != owned.storage_duration
    assert any(owned.id in flow.reset for flow in transitions.edges.values() if flow.entered)


def test_discarded_constructor_has_storage_but_no_result(definitions: MIRDefinitions) -> None:
    source = function(th.THIRLiteral(INT32, 3))
    source = replace(source, body=(th.THIRExprStmt(constructor(definitions)), *source.body))
    fn = lower(source, definitions)
    assert len(storage(fn)) == 1 and len(analyze_scope_ends(fn).ends) == 1
    assert execute(fn, True) == 3


def test_final_false_condition_also_constructs_and_ends(definitions: MIRDefinitions) -> None:
    field = read(definitions)
    field = replace(field, receiver=replace(field.receiver, args=(th.THIRName(INT32, "n"),)))
    source = th.THIRFunction("repeat", (th.THIRParam("n", INT32, passing=ParamPassing.VALUE),), INT32, (
        th.THIRWhile(th.THIRBinOp(BOOL, field, "==", th.THIRLiteral(INT32, 1), None), (
            th.THIRAssign(th.THIRName(INT32, "n"), th.THIRLiteral(INT32, 2)),
        )),
        th.THIRReturn(th.THIRName(INT32, "n")),
    ), th.THIRFunctionLayout())
    fn = lower(source, definitions)
    heap = {}
    assert execute(fn, 1, heap=heap) == 2
    assert len(heap) == 2
    assert len(analyze_scope_ends(fn).ends) == 1
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("shape", ["record", "tuple", "optional", "union"])
def test_reference_kept_after_expression_is_reported(definitions: MIRDefinitions, shape: str) -> None:
    fn = lower(function(read(definitions)), definitions)
    owned, = storage(fn)
    holder = MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1)
    result = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    member = MIRField(MIRFieldId(owned.type, "value"), INT32)
    reference = MIRSlot(holder, owned.type, MIRSlotKind.LOCAL, form=th.Form.BORROW,
                        value_kind=MIRValueKind.BORROWED, residence=result.region)
    borrowed = MIRTupleElement(owned.type, MIRValueKind.BORROWED)
    wrapper_id = MIRSlotId(fn.id, holder.index + 1)
    match shape:
        case "record":
            wrapper, capture, path = None, (), MIRPlace(holder)
        case "tuple":
            wrapper = MIRSlot(wrapper_id, TupleType((owned.type,)), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.TUPLE, tuple_layout=MIRTupleLayout((borrowed,)),
                              residence=result.region)
            capture = (MIRAssign(MIRPlace(wrapper_id), MIRTupleConstruct((holder,))),)
            path = MIRPlace(wrapper_id, (MIRTupleIndex(0),))
        case "optional":
            wrapper = MIRSlot(wrapper_id, OptionalType(owned.type), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.OPTIONAL,
                              optional_layout=MIROptionalLayout(owned.type, MIRValueKind.BORROWED),
                              residence=result.region)
            capture = (MIRAssign(MIRPlace(wrapper_id), MIROptionalConstruct(MIRPlace(holder))),)
            path = MIRPlace(wrapper_id, (MIROptionalPayload(),))
        case "union":
            other = NominalType("Other", _module_qname="temporary.Other")
            wrapper = MIRSlot(wrapper_id, UnionType((owned.type, other)), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.UNION,
                              union_layout=MIRUnionLayout((borrowed, MIRTupleElement(other, MIRValueKind.BORROWED))),
                              residence=result.region)
            capture = (MIRAssign(MIRPlace(wrapper_id), MIRUnionConstruct(0, MIRPlace(holder))),)
            path = MIRPlace(wrapper_id, (MIRUnionPayload(0),))
    blocks = []
    for block in fn.blocks:
        stmts = list(block.statements)
        if block.region == owned.storage_duration:
            stmts.append(MIRAssign(MIRPlace(holder), MIRBorrow(MIRPlace(owned.id))))
            stmts.extend(capture)
        if block.id == result.id:
            stmts.append(MIRAssign(MIRPlace(result.terminator.value),
                                   MIRRead(MIRPlace(path.root, (*path.projections, MIRDeref(), member)))))
        blocks.append(replace(block, statements=tuple(stmts)))
    fn = replace(fn, slots=(*fn.slots, reference, *((wrapper,) if wrapper is not None else ())),
                 blocks=tuple(blocks))
    conflicts = inspect_scope_lifetimes(fn).conflicts
    assert len(conflicts) == 1 and conflicts[0].ended == MIRPlace(owned.id)
    assert conflicts[0].holder == path
    evidence = certify_storage_origins(fn, frozenset((owned.id,)), definitions)
    assert evidence.verdict is MIRStorageVerdict.CONFLICT
    conflict, = evidence.conflicts
    assert conflict.kind is MIRStorageConflictKind.SCOPE_END
    assert conflict.origin == MIRPlace(owned.id) and conflict.holder == path
    assert not evidence.certifies(fn, frozenset((owned.id,)))


@pytest.mark.parametrize("change", ["missing", "readonly", "type", "brace", "form"])
def test_invalid_materialization_fails_closed(definitions: MIRDefinitions, change: str) -> None:
    expr = read(definitions)
    ctor = expr.receiver
    match change:
        case "missing":
            ctor = replace(ctor, full_expression_storage=None)
        case "readonly":
            ctor = replace(ctor, full_expression_storage=replace(ctor.full_expression_storage, readonly=True))
        case "type":
            ctor = replace(ctor, full_expression_storage=th.THIROwnedRecord(NominalType("Other")))
        case "brace":
            ctor = replace(ctor, brace_init=True)
        case "form":
            ctor = replace(ctor, form=th.Form.BORROW)
    fn = function(replace(expr, receiver=ctor))
    with pytest.raises(THIRValidationError):
        validate_thir(fn)
    result = lower_function(fn, MIRBodyId("bad", change), definitions=definitions)
    assert isinstance(result, MIRNotCovered)


def test_temporary_cannot_be_reinitialized_without_leaving_region(definitions: MIRDefinitions) -> None:
    fn = lower(function(read(definitions)), definitions)
    block = next(b for b in fn.blocks if any(isinstance(s.value, MIRConstruct) for s in b.statements))
    fn = replace(fn, blocks=tuple(replace(b, terminator=MIRGoto(b.id)) if b.id == block.id else b
                                 for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="repeated initialization"):
        validate_function(fn)


@pytest.mark.parametrize("hoist,side", [(False, "lhs"), (True, "lhs"), (True, "rhs")])
def test_value_select_hoist_does_not_inherit_inline_lifetime(
        definitions: MIRDefinitions, hoist: bool, side: str) -> None:
    comparison = th.THIRBinOp(BOOL, read(definitions), "==", th.THIRLiteral(INT32, 7), None)
    flag = th.THIRName(BOOL, "flag")
    expr = th.THIRValueSelect(BOOL, comparison if side == "lhs" else flag,
                             flag if side == "lhs" else comparison, "||",
                             lhs_temp_cpp="auto&&" if hoist else None)
    fn = function(expr)
    if hoist and side == "lhs":
        result = lower_function(fn, MIRBodyId("hoist", "select"),
                                definitions=definitions)
        assert isinstance(result, MIRNotCovered) and result.reason == "temporary in hoisted select operand"
    else:
        result = lower(fn, definitions)
        assert execute(result, False) is True
        assert len(analyze_scope_ends(result).ends) == 1

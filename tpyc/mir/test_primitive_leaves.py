"""Every loan-inert primitive is a MIR scalar leaf; every other type stays not covered."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import (
    BOOL, FLOAT, INT32, INT64, STR, UINT8, IntLiteralType, NoneType, Representation, UnionType,
)
from .call_contract import MIRSummaryState
from .coverage import slot_representation
from .dependencies import _leaves
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRCall, MIRCompare, MIRConstant, MIRFunction, MIRNotCovered, MIROp,
    MIRPrint, MIRSlotKind, MIRStorageDuration, MIRTupleElement, MIRUnionLayout, MIRValueKind,
)
from .payload_lifetime import inspect_payload_lifetimes
from .test_payload_inspection import EXTRACT, READ, alias_function
from .test_payload_lifetime import write
from .testutil import execute
from .validate import MIRValidationError, validate_function
from ..thir.validate import THIRValidationError, validate_function as validate_thir

SOURCE = """\
from enum import Enum
from tpy import int32, int64, uint8, float32, char

class Color(Enum):
    RED = 1
    GREEN = 2

class Point:
    x: float
    y: int64
    def __init__(self, x: float, y: int64):
        self.x = x
        self.y = y

LIMIT: float = 1.5

def comparisons(a: int32, b: int32) -> bool:
    # The two dunder comparisons and the four derived ones, over names and literals.
    return a == b and a != 1 and a < b and 2 <= a and a > 3 and b >= a

def leaves(a: int64, b: float, c: char, d: bool, u: uint8, f: float32) -> float:
    y = b
    if d and u > 3 and c == 'q' and f < 2.0 and a != 7:
        return y
    return 0.5

def arithmetic(a: int64, b: int64) -> int64:
    # A checked primitive operation may raise.
    return a * b - 1

def negate(b: float) -> float:
    return -b

def fields(p: Point) -> float:
    p.y = 3
    return p.x

def tuples(t: tuple[float, int64]) -> int64:
    return t[1]

def optional(o: float | None) -> float:
    if o is not None:
        return o
    return 0.0

def union(u: float | bool) -> bool:
    current = u
    current = 2.5
    if isinstance(current, bool):
        return current
    return False

def container(xs: list[float], cs: set[char], d: dict[int64, float]) -> float:
    total = 0.0
    for x in xs:
        total = x
    for c in cs:
        pass
    for k in d:
        pass
    return total

def reads_global() -> float:
    return LIMIT

def prints(a: int64, b: float, c: char, d: bool, u: uint8, f: float32) -> None:
    print(a, b, c, d, u, f, 1, 2.5)

def pure(a: float, b: float) -> bool:
    return a < b

def calls_pure(a: float) -> bool:
    return pure(a, a)

def calls_arithmetic(a: int64) -> int64:
    return arithmetic(a, a)

def mul(a: int64, b: int64) -> int64:
    return a * b

def less(a: int64, b: int64) -> bool:
    return a < b

def points(ps: list[Point]) -> float:
    # A native container of records whose fields are float and int64 leaves.
    total = 0.0
    for p in ps:
        total = p.x
    return total

def float_first(flag: bool) -> bool:
    # The hoisted union's first member is float: its physical default is 0.0.
    if flag:
        current: float | uint8 = 1.5
    else:
        current = 3
    return isinstance(current, uint8)

def enums(c: Color) -> Color:
    # An enum value is an inert leaf as a parameter, a local and a return.
    d = c
    return d
"""

EXCLUDED = """\
from enum import Enum, IntEnum
from tpy import int32, int64, Ptr, Own, StrView, ValueType

class Color(Enum):
    RED = 1

class Level(IntEnum):
    LOW = 1
    HIGH = 2

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class View(ValueType):
    s: StrView
    def __init__(self, s: StrView):
        self.s = s

def text(s: str) -> int32:
    return 1

def big(n: int) -> int32:
    return 1

def big_compare(n: int) -> bool:
    return n < 5

def pointer(p: Ptr[int32]) -> int32:
    return 1

def view(v: View) -> int32:
    return 1

def owned(c: Own[Cell]) -> int32:
    return 1

def enum_print(c: Color) -> None:
    print(c)

def record_print(c: Cell) -> None:
    print(c)

def tuple_print(t: tuple[int32, int32]) -> None:
    print(t)

def optional_print(o: int32 | None) -> None:
    print(o)

def text_print() -> None:
    print("text")

def enum_first(flag: bool) -> bool:
    # An enum has no zero value, so the hoist publishes no physical default.
    if flag:
        current: Color | bool = Color.RED
    else:
        current = True
    return isinstance(current, bool)

def enum_equal(a: Color, b: Color) -> bool:
    # An enum is a leaf without the primitive contract: its operators are refused.
    return a == b

def enum_less(a: Level, b: Level) -> bool:
    # An IntEnum orders through a cast to its int value.
    return a < b

def ambiguous() -> bool:
    # Both int alternatives hold 1: no alternative is named, so no union literal.
    x: int64 | int32 = 1
    return isinstance(x, int32)
"""


def compile_source(source: str):
    return _compile_source(source)[1:]


def _compile_source(source: str):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        bodies = {name: lower_function(fn, MIRBodyId("leaves", name), definitions=mir.definitions,
                                      summaries=mir.workspace.summaries)
                  for name, fn in functions.items()}
        for ctor in ctx.thir_constructors.values():
            bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("leaves", ctor.record_name),
                                                       definitions=mir.definitions)
    summaries = {identity.name: result for identity, result in mir.workspace.summaries.items()}
    return compiler, functions, bodies, summaries


@pytest.fixture(scope="module")
def compiled():
    return _compile_source(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled):
    return compiled[1:]


@pytest.fixture(scope="module")
def excluded():
    return compile_source(EXCLUDED)


def test_every_primitive_leaf_position_is_covered(artifacts) -> None:
    _, bodies, _ = artifacts
    for name, body in bodies.items():
        # A caller of a raising body consumes its summary's exit fact.
        assert isinstance(body, MIRFunction), (name, body)


def test_enum_values_are_inert_leaves(artifacts) -> None:
    fn = artifacts[1]["enums"]
    assert isinstance(fn, MIRFunction)
    assert {s.name: s.value_kind for s in fn.slots if s.name in ("c", "d")} == {
        "c": MIRValueKind.SCALAR, "d": MIRValueKind.SCALAR}


def test_all_six_int32_comparisons_stay_covered(artifacts) -> None:
    fn = artifacts[1]["comparisons"]
    ops = sorted(s.value.op for b in fn.blocks for s in b.statements
                 if isinstance(s, MIRAssign) and isinstance(s.value, MIRCompare))
    assert ops == sorted(["==", "!=", "<", "<=", ">", ">="])
    for a, b in ((5, 6), (5, 5), (4, 4), (1, 1), (9, 2)):
        expected = a == b and a != 1 and a < b and 2 <= a and a > 3 and b >= a
        assert execute(fn, a, b) is expected


def test_leaves_keep_their_own_types(artifacts) -> None:
    fn = artifacts[1]["leaves"]
    types = {s.name: str(s.type) for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    assert types == {"a": "int64", "b": "float", "c": "char", "d": "bool", "u": "uint8", "f": "float32"}
    assert all(s.value_kind is MIRValueKind.SCALAR for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    assert execute(fn, 1, 1.25, "q", True, 9, 1.5) == 1.25
    assert execute(fn, 7, 1.25, "q", True, 9, 1.5) == 0.5


def test_primitive_operations_lower_to_raising_ops(artifacts) -> None:
    fn = artifacts[1]["arithmetic"]
    ops = [s.value for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIROp)]
    assert [op.op for op in ops] == ["*", "-"] and all(op.may_raise for op in ops)
    assert execute(fn, 6, 7) == 41
    with pytest.raises(OverflowError):
        execute(fn, 2**62, 4)
    assert execute(artifacts[1]["negate"], 2.5) == -2.5
    # No fact says an operation cannot raise, so claiming it is refused.
    damaged = replace(fn, blocks=tuple(replace(b, statements=tuple(
        replace(s, value=replace(s.value, may_raise=False))
        if isinstance(s, MIRAssign) and isinstance(s.value, MIROp) else s for s in b.statements))
        for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="primitive operation must be a possible exceptional exit"):
        validate_function(damaged)


def test_aggregate_parameters_keep_their_models(artifacts) -> None:
    bodies = artifacts[1]
    tuple_param = next(s for s in bodies["tuples"].slots if s.name == "t")
    assert tuple_param.value_kind is MIRValueKind.TUPLE
    assert [m.type for m in tuple_param.tuple_layout.elements] == [FLOAT, tuple_param.type.element_types[1]]
    union_param = next(s for s in bodies["union"].slots if s.name == "u")
    assert union_param.value_kind is MIRValueKind.UNION
    assert union_param.storage_duration is MIRStorageDuration.CALLER
    optional_param = next(s for s in bodies["optional"].slots if s.name == "o")
    assert optional_param.value_kind is MIRValueKind.OPTIONAL and optional_param.optional_layout.type == FLOAT
    point = bodies["Point"]
    assert [f.type for r in point.records for f in r.fields] == [FLOAT, bodies["tuples"].return_type]
    container = next(s for s in bodies["container"].slots if s.name == "xs")
    assert container.container_layout.element == MIRTupleElement(FLOAT)
    assert any(s.kind is MIRSlotKind.GLOBAL and s.type == FLOAT for s in bodies["reads_global"].slots)


def test_print_reads_its_leaf_arguments(artifacts) -> None:
    fn = artifacts[1]["prints"]
    prints = [s for b in fn.blocks for s in b.statements if isinstance(s, MIRPrint)]
    assert len(prints) == 1 and len(prints[0].arguments) == 8
    output: list[tuple] = []
    execute(fn, 1, 2.5, "c", True, 3, 0.5, output=output)
    assert output == [(1, 2.5, "c", True, 3, 0.5, 1, 2.5)]


def test_raising_bodies_summarize_their_exit_and_printing_stays_opaque(artifacts) -> None:
    _, bodies, summaries = artifacts
    assert summaries["pure"].state is MIRSummaryState.KNOWN and summaries["pure"].summary.normal_return_only
    # A checked operation may raise: the summary says so rather than going opaque.
    assert summaries["arithmetic"].state is MIRSummaryState.KNOWN
    assert summaries["arithmetic"].summary.normal_return_only is False
    assert bodies["arithmetic"].exceptional_exits and not bodies["pure"].exceptional_exits
    assert summaries["prints"].reason == "summary output effect"
    # Formatting allocates, so a print is a possible exceptional exit.
    assert bodies["prints"].exceptional_exits is True
    with pytest.raises(MIRValidationError, match="exceptional exit fact mismatch"):
        validate_function(replace(bodies["prints"], exceptional_exits=False))
    assert isinstance(bodies["calls_pure"], MIRFunction)
    # The caller's call mirrors the consumed summary and feeds the caller's own exit fact.
    caller = bodies["calls_arithmetic"]
    call, = (s.value for b in caller.blocks for s in b.statements
             if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    assert call.may_raise and caller.exceptional_exits
    assert summaries["calls_arithmetic"].summary.normal_return_only is False
    damaged = replace(caller, blocks=tuple(replace(b, statements=tuple(
        replace(s, value=replace(s.value, may_raise=False))
        if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) else s for s in b.statements))
        for b in caller.blocks))
    with pytest.raises(MIRValidationError, match="call exit fact mismatch"):
        validate_function(damaged)
    functions, _, _ = artifacts
    unscheduled = lower_function(functions["calls_arithmetic"], MIRBodyId("leaves", "caller"), summaries={})
    assert isinstance(unscheduled, MIRNotCovered) and unscheduled.reason == "call needs finalized known summary"


@pytest.mark.parametrize("name,reason", [
    ("pointer", "unsupported parameter type"),
    ("view", "unsupported parameter type"),
    # The enum parameter is a leaf; print refuses it (a user enum may define `__str__`).
    ("enum_print", "print argument needs a scalar leaf"),
    ("record_print", "unsupported expression form"),
    ("tuple_print", "print argument needs a scalar leaf"),
    ("optional_print", "print argument needs a scalar leaf"),
    ("enum_equal", "uncertified binary operation"),
    ("enum_less", "unsupported metadata: left_cast"),
])
def test_non_leaf_types_stay_not_covered(excluded, name: str, reason: str) -> None:
    body = excluded[1][name]
    assert isinstance(body, MIRNotCovered) and reason in body.reason, body


def test_an_owned_record_parameter_is_body_storage(excluded) -> None:
    # A record handed over at OWN is the body's own storage (test_nested_records.py).
    assert isinstance(excluded[1]["owned"], MIRFunction), excluded[1]["owned"]


@pytest.mark.parametrize("name", ["text", "big", "big_compare", "text_print"])
def test_owned_leaf_bodies_are_covered_since_b2(excluded, name: str) -> None:
    # str and BigInt are owned leaves (tpyc/mir/test_owned_leaves.py), no longer outside the vocabulary.
    assert isinstance(excluded[1][name], MIRFunction), excluded[1][name]


def test_conversion_around_a_comparison_is_not_covered(artifacts) -> None:
    fn = artifacts[0]["comparisons"]
    ret = fn.body[-1]
    compare = ret.value

    def first_resolved(node):
        while isinstance(node, th.THIRBinOp):
            if node.resolved is not None:
                return node
            node = node.left
        return None

    target = first_resolved(compare)
    assert target is not None and target.certified_op
    wrapped = replace(target, resolved=replace(target.resolved, promotion=target.resolved.method))
    assert not wrapped.certified_op
    body = replace(fn, body=(*fn.body[:-1], replace(ret, value=wrapped)))
    result = lower_function(body, MIRBodyId("leaves", "wrapped"))
    assert isinstance(result, MIRNotCovered) and result.reason == "uncertified binary operation"


def test_literal_ranges_are_per_type() -> None:
    ret = th.THIRReturn(th.THIRLiteral(UINT8, 300))
    fn = th.THIRFunction("f", (), UINT8, (ret,), th.THIRFunctionLayout())
    result = lower_function(fn, MIRBodyId("leaves", "f"))
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported literal value"
    x = th.THIRName(UINT8, "x")
    compare = th.THIRBinOp(BOOL, x, "<", th.THIRLiteral(IntLiteralType(-1), -1), None)
    fn = th.THIRFunction("g", (th.THIRParam("x", UINT8, passing=ParamPassing.VALUE),), BOOL, (th.THIRReturn(compare),), th.THIRFunctionLayout())
    result = lower_function(fn, MIRBodyId("leaves", "g"))
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported literal value"


def test_validator_rechecks_each_leaf_class(artifacts) -> None:
    fn = artifacts[1]["leaves"]
    slot = next(s for s in fn.slots if s.name == "b")
    broken = replace(fn, slots=tuple(replace(s, type=STR) if s is slot else s for s in fn.slots))
    with pytest.raises(MIRValidationError):
        validate_function(broken)
    with pytest.raises(MIRValidationError, match="not an inert leaf"):
        _leaves(replace(slot, type=STR))
    union = next(s for s in artifacts[1]["union"].slots if s.name == "u")
    member = replace(union, type=UnionType((STR, BOOL)),
                     union_layout=MIRUnionLayout((MIRTupleElement(STR), MIRTupleElement(BOOL))))
    with pytest.raises(MIRValidationError, match="not an inert leaf"):
        _leaves(member)
    constant = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements)
                    if isinstance(s, MIRAssign) and isinstance(s.value, MIRConstant))
    block, index = constant
    stmt = block.statements[index]
    bad = replace(stmt, value=MIRConstant("q"))
    blocks = tuple(replace(b, statements=(*b.statements[:index], bad, *b.statements[index + 1:]))
                   if b is block else b for b in fn.blocks)
    with pytest.raises(MIRValidationError, match="constant type or range"):
        validate_function(replace(fn, blocks=blocks))


def _as_float(fn: MIRFunction) -> MIRFunction:
    def retype(typ):
        if typ == INT32:
            return FLOAT
        if isinstance(typ, UnionType):
            return UnionType(tuple(retype(m) for m in typ.members))
        return typ

    def member(m):
        return None if m is None else replace(m, type=retype(m.type))

    slots = tuple(replace(s, type=retype(s.type), union_layout=None if s.union_layout is None else
                          MIRUnionLayout(tuple(member(m) for m in s.union_layout.elements)))
                  for s in fn.slots)
    return replace(fn, slots=slots, return_type=retype(fn.return_type))


@pytest.mark.parametrize("statements,conflicts", [
    ((EXTRACT, READ, write(False, 0)), 0),
    ((EXTRACT, write(False, 0), READ), 1),
])
def test_float_payload_aliases_survive_reads_and_conflict_on_retag(statements, conflicts: int) -> None:
    fn = _as_float(alias_function(*statements))
    assert isinstance(fn.slots[0].type, UnionType) and FLOAT in fn.slots[0].type.members
    assert NoneType() in fn.slots[0].type.members
    assert len(inspect_payload_lifetimes(fn).conflicts) == conflicts


def _hoists(fn: th.THIRFunction) -> tuple[th.THIRHoistedBinding, ...]:
    return tuple(b for stmt in fn.body if isinstance(stmt, th.THIRIf) for b in stmt.hoisted_bindings)


def test_float_first_union_hoist_defaults_to_float_zero(artifacts) -> None:
    binding, = _hoists(artifacts[0]["float_first"])
    assert binding.union_layout.elements[0] == FLOAT
    assert binding.physical_default == th.THIRWrapperDefault(0, 0.0)
    assert type(binding.physical_default.value) is float
    assert isinstance(artifacts[1]["float_first"], MIRFunction)


def test_enum_first_union_hoist_publishes_no_default(excluded) -> None:
    assert _hoists(excluded[0]["enum_first"]) == ()
    body = excluded[1]["enum_first"]
    assert isinstance(body, MIRNotCovered) and body.reason == "missing or inconsistent hoisted binding facts"


def test_thir_validator_rejects_a_none_default_for_a_leaf_alternative(artifacts) -> None:
    fn = artifacts[0]["float_first"]
    branch = next(stmt for stmt in fn.body if isinstance(stmt, th.THIRIf))
    binding, = branch.hoisted_bindings
    damaged = replace(branch, hoisted_bindings=(replace(binding, physical_default=th.THIRWrapperDefault(0, None)),))
    body = tuple(damaged if stmt is branch else stmt for stmt in fn.body)
    with pytest.raises(THIRValidationError, match="invalid physical wrapper default"):
        validate_thir(replace(fn, body=body))


@pytest.mark.parametrize("default,valid", [(None, True), (th.THIRWrapperDefault(0, None), False)])
def test_thir_validator_rejects_a_none_default_for_an_enum_first_union(compiled, default, valid) -> None:
    # An enum has no zero value, so a None default passes every type check and
    # only the no-zero-value rule rejects it.
    compiler, functions, _, _ = compiled
    fn = functions["float_first"]
    branch = next(stmt for stmt in fn.body if isinstance(stmt, th.THIRIf))
    binding, = branch.hoisted_bindings
    union = UnionType((_enum_type(compiled), UINT8))
    retyped = replace(binding, type=union, union_layout=th.THIRUnionLayout(union, union.members),
                      physical_default=default)
    body = tuple(replace(branch, hoisted_bindings=(retyped,)) if stmt is branch else stmt for stmt in fn.body)
    with activate_compiler(compiler):
        if valid:
            validate_thir(replace(fn, body=body))
        else:
            with pytest.raises(THIRValidationError, match="invalid physical wrapper default"):
                validate_thir(replace(fn, body=body))


def test_ambiguous_union_literal_is_not_covered(excluded) -> None:
    decl = next(stmt for stmt in excluded[0]["ambiguous"].body if isinstance(stmt, th.THIRVarDecl))
    assert decl.union_layout is not None and decl.union_literal is None
    body = excluded[1]["ambiguous"]
    # Without a union literal the initializer is a union-typed coercion, not a leaf value.
    assert isinstance(body, MIRNotCovered)
    assert (body.reason, body.node_kind) == ("unsupported expression type", "THIRCoerce")


def test_native_container_of_float_records(artifacts) -> None:
    fn = artifacts[1]["points"]
    param = next(s for s in fn.slots if s.name == "ps")
    assert param.value_kind is MIRValueKind.BORROWED_CONTAINER
    element = param.container_layout.element
    assert element.kind is MIRValueKind.BORROWED and element.type.name == "Point"
    point, = (r for r in fn.records if r.type == element.type)
    assert [f.type for f in point.fields] == [FLOAT, INT64]


def _retype(fn: MIRFunction, old, new) -> MIRFunction:
    slots = tuple(replace(s, type=new) if s.type == old else s for s in fn.slots)
    return replace(fn, slots=slots, return_type=new if fn.return_type == old else fn.return_type)


def _enum_type(compiled):
    return compiled[1]["enums"].params[0].type


@pytest.mark.parametrize("name,message", [
    ("mul", "primitive operation lacks the primitive contract"),
    ("less", "comparison lacks the primitive contract"),
    ("prints", "print argument lacks the primitive contract"),
])
def test_validator_rechecks_the_primitive_contract(compiled, name: str, message: str) -> None:
    # An enum value is an inert leaf without the contract: every other rule holds.
    compiler, _, bodies, _ = compiled
    with activate_compiler(compiler):
        validate_function(bodies[name])
        with pytest.raises(MIRValidationError, match=message):
            validate_function(_retype(bodies[name], INT64, _enum_type(compiled)))


def _replace_statement(fn: MIRFunction, kind, change) -> MIRFunction:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements)
                        if isinstance(s, kind) or isinstance(s, MIRAssign) and isinstance(s.value, kind))
    stmt = change(block.statements[index])
    return replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], stmt, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))


def test_passing_facts_live_on_parameter_slots(artifacts) -> None:
    fn = artifacts[1]["leaves"]
    local = next(s for s in fn.slots if s.name == "y")
    param = next(s for s in fn.slots if s.name == "a")
    bad = replace(local, passing=ParamPassing.VALUE)
    with pytest.raises(MIRValidationError, match="passing fact on a non-parameter slot"):
        validate_function(replace(fn, slots=tuple(bad if s.id == bad.id else s for s in fn.slots)))
    # An unpublished passing classifies at the unknown representation, not as storage.
    assert slot_representation(param) is Representation.STORAGE
    assert slot_representation(replace(param, passing=None)) is Representation.TRAIT


def test_validator_rejects_malformed_operations_and_prints(artifacts) -> None:
    mul = artifacts[1]["mul"]
    operand = next(s.value.operands[0] for b in mul.blocks for s in b.statements
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIROp))
    three = _replace_statement(mul, MIROp, lambda s: replace(s, value=replace(s.value, operands=(operand,) * 3)))
    with pytest.raises(MIRValidationError, match="primitive operation needs inert leaf operands and result"):
        validate_function(three)
    # A global leaf is printable only once read into a local.
    glob = next(s for s in artifacts[1]["reads_global"].slots if s.kind is MIRSlotKind.GLOBAL)
    prints = artifacts[1]["prints"]
    moved = replace(glob, id=replace(glob.id, body=prints.id, index=len(prints.slots)))
    printed = replace(prints, slots=(*prints.slots, moved))
    printed = _replace_statement(printed, MIRPrint, lambda s: replace(s, arguments=(moved.id,)))
    with pytest.raises(MIRValidationError, match="print needs local inert leaf arguments"):
        validate_function(printed)

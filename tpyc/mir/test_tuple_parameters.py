"""Tuple parameter payloads keep value snapshots and borrowed referent identity."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _assert_rejects_at, _compile, _entry, _strict_reject
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..type_def_registry import ParamPassing
from ..typesys import INT32, TupleType
from .lower import lower_function
from .nodes import MIRBodyId, MIRFieldId, MIRFunction, MIRNotCovered
from .testutil import Reference, TupleValue, execute
from .validate import MIRValidationError, validate_function


SOURCE = """\
from tpy import int32, readonly, Own
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def method(self, pair: tuple[Cell]) -> int32:
        saved = pair[0]
        saved.value = 8
        return pair[0].value
class Observer:
    value: int32
    def __init__(self, pair: tuple[Cell]):
        saved = pair[0]
        saved.value = 9
        self.value = pair[0].value
class Outer:
    inner: Cell
    def __init__(self, value: int32):
        self.inner = Cell(value)
def mutable(pair: tuple[Cell], other: Cell) -> int32:
    saved = pair[0]
    other.value = 17
    saved.value = 18
    return pair[0].value
def mixed(pair: tuple[int32, bool, Cell]) -> int32:
    saved = pair[-1]
    if pair[1]:
        saved.value = 19
    return pair[0]
def duplicate(pair: tuple[Cell, Cell]) -> int32:
    saved = pair[0]
    pair[1].value = 20
    return saved.value
def readonly_capture(pair: readonly[tuple[Cell]], writer: Cell) -> int32:
    saved = pair[0]
    writer.value = 21
    return saved.value
def inferred_readonly(pair: tuple[Cell], writer: Cell) -> int32:
    saved = pair[0]
    writer.value = 22
    return saved.value
def mixed_access(pair: tuple[readonly[Cell], Cell]) -> int32:
    saved = pair[0]
    pair[1].value = 23
    return saved.value
def copy_tuple(pair: tuple[Cell, int32], other: Cell) -> int32:
    saved = pair
    pair[0].value = 24
    other.value = 26
    return saved[0].value
def readonly_copy(pair: tuple[Cell, int32], other: Cell) -> int32:
    saved = pair
    other.value = 24
    return saved[0].value
def scalar_copy(pair: tuple[int32, bool]) -> int32:
    saved = pair
    current = pair
    current = (2, False)
    return saved[0]
def nested(pair: tuple[Outer]) -> int32:
    pair[0].inner.value = 25
    return pair[0].inner.value
def owned(pair: Own[tuple[Cell]]) -> int32:
    return pair[0].value
def owned_element(pair: tuple[Own[Cell]]) -> int32:
    return pair[0].value
"""

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    emitted, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert emitted
    return ({node.name: fn for node, fn in ctx.thir_functions.items()}, tuple(ctx.thir_constructors.values()))


def lower(fn: th.THIRFunction) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("tuple_parameters", fn.name))
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("same", [False, True])
def test_capture_preserves_shared_identity(artifacts: Artifacts, same: bool) -> None:
    functions, _ = artifacts
    fn = lower(functions["mutable"])
    cell = fn.slots[0].tuple_layout.elements[0].type
    value = MIRFieldId(cell, "value")
    heap = {1: {value: 1}, 2: {value: 2}}
    assert execute(fn, TupleValue((Reference(1),)), Reference(1 if same else 2), heap=heap) == 18
    assert heap[2][value] == (2 if same else 17)


@pytest.mark.parametrize("name,written", [("readonly_capture", 21), ("inferred_readonly", 22)])
def test_readonly_capture_observes_another_writer(artifacts: Artifacts, name: str, written: int) -> None:
    functions, _ = artifacts
    fn = lower(functions[name])
    value = MIRFieldId(fn.slots[0].tuple_layout.elements[0].type, "value")
    assert next(slot for slot in fn.slots if slot.name == "saved").readonly
    assert execute(fn, TupleValue((Reference(1),)), Reference(1), heap={1: {value: 1}}) == written


def test_mixed_scalar_snapshots_and_negative_index(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = lower(functions["mixed"])
    value = MIRFieldId(fn.slots[0].tuple_layout.elements[-1].type, "value")
    assert functions["mixed"].body[0].init.tuple_index == 2
    heap = {1: {value: 1}}
    assert execute(fn, TupleValue((5, True, Reference(1))), heap=heap) == 5
    assert heap[1][value] == 19


@pytest.mark.parametrize("name,written", [("duplicate", 20), ("mixed_access", 23)])
@pytest.mark.parametrize("same", [False, True])
def test_duplicate_and_mixed_access_referents(artifacts: Artifacts, name: str, written: int, same: bool) -> None:
    functions, _ = artifacts
    fn = lower(functions[name])
    value = MIRFieldId(fn.slots[0].tuple_layout.elements[0].type, "value")
    heap = {1: {value: 1}, 2: {value: 2}}
    assert execute(fn, TupleValue((Reference(1), Reference(1 if same else 2))), heap=heap) == (
        written if same else 1)


def test_parameter_tuple_copies_and_nested_paths(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = lower(functions["copy_tuple"])
    value = MIRFieldId(fn.slots[0].tuple_layout.elements[0].type, "value")
    assert execute(fn, TupleValue((Reference(1), 1)), Reference(1), heap={1: {value: 1}}) == 26
    assert execute(fn, TupleValue((Reference(1), 1)), Reference(2), heap={1: {value: 1}, 2: {value: 2}}) == 24
    assert execute(lower(functions["scalar_copy"]), TupleValue((24, True))) == 24
    nested = lower(functions["nested"])
    outer = nested.slots[0].tuple_layout.elements[0].type
    assert execute(nested, TupleValue((Reference(1),)), heap={1: {MIRFieldId(outer, "inner"): {value: 1}}}) == 25


def test_parameter_facts_reach_method_and_constructor(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    for body in (functions["method"], next(c for c in constructors if c.record_name == "Observer")):
        assert body.params[0].tuple_layout is not None
        assert any(getattr(stmt, "storage_borrow", None) is not None for stmt in body.body)


@pytest.mark.parametrize("name", ["owned", "owned_element"])
def test_owned_tuple_parameters_remain_uncovered(artifacts: Artifacts, name: str) -> None:
    functions, _ = artifacts
    assert functions[name].params[0].tuple_layout is None
    assert isinstance(lower_function(functions[name], MIRBodyId("tuples", name)), MIRNotCovered)


def test_readonly_auto_copy_fact_cannot_increase_access(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    # Auto preserves const pointers; the existing local fact loses that capability.
    # BUGS.md#readonly-auto-tuple-copy-fact
    fn = functions["readonly_copy"]
    result = lower_function(fn, MIRBodyId("tuples", fn.name))
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "payload copy type or access mismatch"


def test_capture_representation_and_readonly_are_verified(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = functions["readonly_capture"]
    decl = fn.body[0]
    bad = replace(fn, body=(replace(decl, init=replace(decl.init, deref=False)), *fn.body[1:]))
    with pytest.raises(THIRValidationError, match="storage borrow disagrees"):
        validate_thir(bad)
    assert isinstance(lower_function(bad, MIRBodyId("tuples", "bad")), MIRNotCovered)
    mir = lower(fn)
    holder = next(slot for slot in mir.slots if slot.name == "saved")
    with pytest.raises(MIRValidationError, match="borrow increases access"):
        validate_function(replace(mir, slots=tuple(replace(s, readonly=False) if s == holder else s for s in mir.slots)))


def test_parameter_layout_is_required_and_cannot_increase_access(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = functions["readonly_capture"]
    param = fn.params[0]
    missing = replace(fn, params=(replace(param, tuple_layout=None), *fn.params[1:]))
    assert isinstance(lower_function(missing, MIRBodyId("tuples", "missing")), MIRNotCovered)
    layout = replace(param.tuple_layout, elements=(replace(param.tuple_layout.elements[0], readonly=False),))
    bad = replace(fn, params=(replace(param, tuple_layout=layout), *fn.params[1:]))
    with pytest.raises(THIRValidationError, match="invalid tuple member fact"):
        validate_thir(bad)
    assert isinstance(lower_function(bad, MIRBodyId("tuples", "bad")), MIRNotCovered)


def test_empty_tuple_parameter_at_internal_boundary() -> None:
    # Empty tuple source syntax retains its frontend gate; its IR payload has no elements.
    fn = th.THIRFunction("empty", (th.THIRParam("pair", TupleType(()), tuple_layout=th.THIRTupleLayout(()), passing=ParamPassing.CONST_REF),),
                         INT32, (th.THIRReturn(th.THIRLiteral(INT32, 1)),), th.THIRFunctionLayout())
    validate_thir(fn)
    assert execute(lower(fn), TupleValue(())) == 1


@pytest.mark.parametrize("body", [
    "def sample(cell: Cell) -> int32:\n    pair = (cell,)\n    saved = pair[0]\n    return saved.value\n",
    "def sample(pair: tuple[Cell], other: Cell) -> int32:\n    saved = pair[0]\n    saved = other\n    return saved.value\n",
    "def sample(pair: tuple[Cell, int32], other: Cell) -> int32:\n    saved = pair\n    saved = (other, 2)\n    return saved[0].value\n",
])
def test_existing_local_and_reseated_capture_gates_remain(body: str) -> None:
    _, reasons = _strict_reject(SOURCE + body)
    _assert_rejects_at(reasons, "body:stmt.var_decl", shape="decl.slot_type")

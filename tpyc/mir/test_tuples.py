"""Tuple payload copies preserve scalar snapshots and shared record identities."""

from collections.abc import Callable, Iterator
from dataclasses import fields, replace

import pytest

from ..thir import nodes as th
from ..parse import ParseError
from ..thir.testutil import _compile, _entry, _strict_reject, _assert_rejects_at
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import INT32, TupleType
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFieldId, MIRFunction, MIRNotCovered
from .testutil import Heap, Reference, execute

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...], tuple[str, str]]


PRELUDE = """\
from tpy import int32, readonly
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def observe(self, other: Cell) -> int32:
        pair = (other, 1)
        other.value = 7
        return pair[0].value
class Holder:
    value: int32
    def __init__(self, other: Cell):
        pair = (other, 1)
        other.value = 8
        self.value = pair[0].value
"""

SOURCE = PRELUDE + """\
def copied(a: Cell, b: Cell) -> int32:
    pair = (a, 1)
    saved = pair
    pair = (b, 2)
    saved[0].value = 9
    return saved[1]

def snapshot(a: Cell) -> int32:
    pair = (a, a.value)
    a.value = 8
    pair[0].value = 9
    return pair[1]

def singleton(a: Cell) -> int32:
    pair = (a,)
    a.value = 8
    return pair[0].value

def duplicate(a: Cell) -> int32:
    pair = (a, a)
    pair[0].value = 9
    return pair[1].value

def self_binding(a: Cell) -> int32:
    pair = (a,)
    pair = pair
    pair[0].value = 9
    return a.value

def branches(a: Cell, b: Cell, flag: bool) -> int32:
    pair = (a, 1)
    saved = pair
    if flag:
        pair = (b, 2)
    saved[0].value = 9
    return pair[1]

def loop(a: Cell, b: Cell, flag: bool) -> int32:
    pair = (a, 1)
    saved = pair
    while flag:
        pair = (b, 2)
        flag = False
    saved[0].value = 9
    return pair[1]

def owned() -> int32:
    current = Cell(1)
    pair = (current, 1)
    saved = pair
    current.value = 4
    current = Cell(2)
    saved[0].value = 9
    return current.value

def scalars(first: int32, flag: bool) -> int32:
    pair = (first, flag)
    saved = pair
    pair = (2, False)
    first = 3
    return saved[0] if saved[-1] else pair[0]

def conditional_element(a: Cell, flag: bool) -> int32:
    pair = (a, 1 if flag else 2)
    a.value = 7
    return pair[-1]
"""


def compile_artifacts(source: str) -> Artifacts:
    compiler, modules = _compile(source)
    emitted, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {node.name: fn for node, fn in ctx.thir_functions.items()}, tuple(ctx.thir_constructors.values()), emitted


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    return compile_artifacts(SOURCE)


def lower(fn: th.THIRFunction, constructors: tuple[th.THIRConstructor, ...] = ()) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("tuples", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRFunction), result
    return result


def nodes(node: th.THIRNode | th.THIRFunction | th.THIRConstructor) -> Iterator[th.THIRNode]:
    if isinstance(node, th.THIRNode):
        yield node
    if hasattr(node, "__dataclass_fields__"):
        for field in fields(node):
            value = getattr(node, field.name)
            if isinstance(value, th.THIRNode):
                yield from nodes(value)
            elif isinstance(value, tuple):
                for child in value:
                    if isinstance(child, th.THIRNode):
                        yield from nodes(child)


@pytest.mark.parametrize("name,expected,after", [
    ("copied", 1, 9), ("snapshot", 3, 9), ("singleton", 8, 8),
    ("duplicate", 9, 9), ("self_binding", 9, 9),
])
def test_source_payload_identity_and_snapshots(artifacts: Artifacts, name: str,
                                              expected: int, after: int) -> None:
    functions, constructors, _ = artifacts
    fn = lower(functions[name], constructors)
    cell = fn.slots[0].type
    member = MIRFieldId(cell, "value")
    heap = {1: {member: 3}, 2: {member: 4}}
    args = (Reference(1), Reference(2)) if name == "copied" else (Reference(1),)
    assert execute(fn, *args, heap=heap) == expected
    assert heap == {1: {member: after}, 2: {member: 4}}
    assert dump_function(fn) == dump_function(lower(functions[name], constructors))
    assert "payload(" in dump_function(fn)


@pytest.mark.parametrize("name", ["branches", "loop"])
@pytest.mark.parametrize("flag", [False, True])
def test_tuple_reseats_do_not_retarget_saved_payload(artifacts: Artifacts, name: str, flag: bool) -> None:
    functions, constructors, _ = artifacts
    fn = lower(functions[name], constructors)
    member = MIRFieldId(fn.slots[0].type, "value")
    heap = {1: {member: 3}, 2: {member: 4}}
    assert execute(fn, Reference(1), Reference(2), flag, heap=heap) == (2 if flag else 1)
    assert heap == {1: {member: 9}, 2: {member: 4}}


def test_owned_storage_survives_tuple_copies(artifacts: Artifacts) -> None:
    functions, constructors, _ = artifacts
    fn = lower(functions["owned"], constructors)
    heap: Heap = {}
    assert execute(fn, heap=heap) == 2
    assert sorted(next(iter(obj.values())) for obj in heap.values()) == [2, 9]


@pytest.mark.parametrize("flag", [False, True])
def test_scalar_payloads_and_pure_conditional_elements(artifacts: Artifacts, flag: bool) -> None:
    functions, constructors, _ = artifacts
    assert execute(lower(functions["scalars"], constructors), 7, flag) == (7 if flag else 2)
    fn = lower(functions["conditional_element"], constructors)
    member = MIRFieldId(fn.slots[0].type, "value")
    heap = {1: {member: 3}}
    assert execute(fn, Reference(1), flag, heap=heap) == (1 if flag else 2)
    assert heap[1][member] == 7


def test_shared_producers_cover_methods_and_constructors(artifacts: Artifacts) -> None:
    functions, constructors, _ = artifacts
    method = functions["observe"]
    ctor = next(c for c in constructors if c.record_name == "Holder")
    for body in (method, ctor):
        literals = [n for n in nodes(body) if isinstance(n, th.THIRBorrowTupleLiteral)]
        assert literals and all(n.tuple_layout is not None for n in literals)
        projected = [n for n in nodes(body) if isinstance(n, th.THIRFieldAccess)
                     and isinstance(n.receiver, th.THIRSubscript)]
        assert projected and all(n.field_identity is not None for n in projected)
    result = lower_function(method, MIRBodyId("tuples", "observe"), kind=MIRBodyKind.METHOD)
    assert isinstance(result, MIRFunction)
    value = MIRFieldId(method.receiver.type, "value")
    assert execute(result, Reference(1), Reference(1), heap={1: {value: 1}}) == 7


@pytest.mark.parametrize("change", [
    lambda init: replace(init, tuple_layout=None),
    lambda init: replace(init, tuple_layout=th.THIRTupleLayout((INT32, INT32))),
    lambda init: replace(init, tuple_layout=th.THIRTupleLayout(())),
])
def test_missing_or_contradictory_capture_facts_are_uncovered(
        artifacts: Artifacts, change: Callable[[th.THIRExpr], th.THIRExpr]) -> None:
    functions, _, _ = artifacts
    fn = functions["copied"]
    decl = fn.body[0]
    bad = replace(fn, body=(replace(decl, init=change(decl.init)), *fn.body[1:]))
    assert isinstance(lower_function(bad, MIRBodyId("tuples", fn.name),
                                    kind=MIRBodyKind.FREE_FUNCTION), MIRNotCovered)


def test_normalized_index_is_positive_fact(artifacts: Artifacts) -> None:
    functions, _, _ = artifacts
    fn = functions["conditional_element"]
    ret = fn.body[-1]
    assert ret.value.tuple_index == 1 and ret.value.index.value == 1
    for index in (None, -1, 2, True):
        bad = replace(fn, body=(*fn.body[:-1], replace(ret, value=replace(ret.value, tuple_index=index))))
        assert isinstance(lower_function(bad, MIRBodyId("tuples", fn.name),
                                        kind=MIRBodyKind.FREE_FUNCTION), MIRNotCovered)
        if index is not None:
            with pytest.raises(THIRValidationError, match="tuple index"):
                validate_thir(bad)


def test_empty_payload_thir_boundary() -> None:
    # Source () is rejected; construct THIR explicitly to test the IR contract.
    typ = TupleType(())
    layout = th.THIRTupleLayout(())
    literal = th.THIRTupleLiteral(typ, (), tuple_layout=layout)
    fn = th.THIRFunction("empty", (), INT32, (
        th.THIRVarDecl("pair", typ, init=literal, tuple_layout=layout),
        th.THIRVarDecl("saved", typ, init=th.THIRName(typ, "pair"), tuple_layout=layout),
        th.THIRReturn(th.THIRLiteral(INT32, 1)),
    ), th.THIRFunctionLayout())
    validate_thir(fn)
    assert execute(lower(fn)) == 1


def test_readonly_source_thir_boundary(artifacts: Artifacts) -> None:
    functions, _, _ = artifacts
    fn = functions["singleton"]
    decl = fn.body[0]
    typ = fn.params[0].borrowed_record.type
    reference = th.THIRBorrowedRecord(typ, True)
    layout = th.THIRTupleLayout((reference,))
    # Keep a mutable parameter for the write and capture a separate readonly one.
    init = replace(decl.init, elements=(th.THIRName(typ, "ro", form=th.Form.BORROW),),
                   tuple_layout=layout)
    fn = replace(fn, params=(*fn.params, th.THIRParam("ro", typ, reference)),
                 resolved_callee=None,  # The synthetic signature is not the emitted declaration.
                 body=(replace(decl, init=init, tuple_layout=layout), *fn.body[1:]))
    validate_thir(fn)
    member = MIRFieldId(typ, "value")
    for shared in (False, True):
        heap = {1: {member: 3}, 2: {member: 4}}
        assert execute(lower(fn), Reference(1), Reference(1 if shared else 2), heap=heap) == (
            8 if shared else 4)
    mutable = th.THIRTupleLayout((replace(reference, readonly=False),))
    bad = replace(fn, body=(replace(fn.body[0], tuple_layout=mutable), *fn.body[1:]))
    result = lower_function(bad, MIRBodyId("tuples", fn.name), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and "access mismatch" in result.reason


def test_source_admission_is_unchanged() -> None:
    with pytest.raises(ParseError, match="Empty tuple literal is not supported"):
        _compile("def example() -> int:\n    pair = ()\n    return 1\n")
    _, reasons = _strict_reject(PRELUDE + """\
def example(a: readonly[Cell]) -> int32:
    pair = (a, 1)
    return pair[0].value
""")
    _assert_rejects_at(reasons, "body:stmt.var_decl", "decl.tuple_literal_shape")


@pytest.mark.parametrize("body,reason", [
    ("def example(pair: tuple[tuple[int32, int32]]) -> int32:\n    return 1\n",
     "parameter type"),
    ("def example(x: int32) -> tuple[int32, int32]:\n    return (x, 1)\n", "return type"),
    ("def example(x: int32) -> int32:\n    pair = ((x, 1), 2)\n    return x\n", "tuple layout"),
    ("def example(x: int32) -> int32:\n    pair = (x, 'text')\n    return x\n", "tuple layout"),
])
def test_deferred_source_shapes_are_whole_body_uncovered(body: str, reason: str) -> None:
    functions, constructors, _ = compile_artifacts(PRELUDE + body)
    result = lower_function(functions["example"], MIRBodyId("tuples", "example"),
                            kind=MIRBodyKind.FREE_FUNCTION, definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRNotCovered), result
    assert reason in result.reason


@pytest.mark.parametrize("change", [
    lambda decl: replace(decl, tuple_layout=None),
    lambda decl: replace(decl, btuple_slot_cpp="storage"),
    lambda decl: replace(decl, init=replace(decl.init, elem_wraps=("wrap({0})", None))),
])
def test_unknown_tuple_storage_metadata_is_not_ignored(
        artifacts: Artifacts, change: Callable[[th.THIRVarDecl], th.THIRVarDecl]) -> None:
    functions, _, _ = artifacts
    fn = functions["copied"]
    # Disable addr_of solely to allow construction of the wrapped node.
    decl = replace(fn.body[0], init=replace(fn.body[0].init, addr_of=(False, False)))
    fn = replace(fn, body=(change(decl), *fn.body[1:]))
    assert isinstance(lower_function(fn, MIRBodyId("tuples", fn.name),
                                    kind=MIRBodyKind.FREE_FUNCTION), MIRNotCovered)


def test_tuple_elements_cannot_write_existing_locals() -> None:
    typ = TupleType((INT32, INT32))
    layout = th.THIRTupleLayout((INT32, INT32))
    literal = th.THIRTupleLiteral(typ, (
        th.THIRWalrus(INT32, "x", "x", th.THIRLiteral(INT32, 1)),
        th.THIRName(INT32, "x"),
    ), tuple_layout=layout)
    fn = th.THIRFunction("effect", (th.THIRParam("x", INT32),), INT32,
                         (th.THIRVarDecl("pair", typ, init=literal, tuple_layout=layout),
                          th.THIRReturn(th.THIRName(INT32, "x"))), th.THIRFunctionLayout())
    result = lower_function(fn, MIRBodyId("tuples", fn.name), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "effectful or mistyped tuple element"
    assert result.node_kind == "THIRWalrus"


def test_tuple_expression_temporary_projection() -> None:
    typ = TupleType((INT32,))
    literal = th.THIRTupleLiteral(typ, (th.THIRName(INT32, "x"),),
                                 tuple_layout=th.THIRTupleLayout((INT32,)))
    index = th.THIRSubscript(INT32, literal, th.THIRLiteral(INT32, 0), tuple_index=0)
    fn = th.THIRFunction("temporary", (th.THIRParam("x", INT32),), INT32,
                         (th.THIRReturn(index),), th.THIRFunctionLayout())
    validate_thir(fn)
    assert execute(lower(fn), 7) == 7


@pytest.mark.parametrize("body", [
    """\
def example(other: Cell) -> int32:
    current = Cell(1)
    pair = (current, other, 2)
    other.value = 3
    return pair[0].value
""",
    """\
class GenericCell[T]:
    value: T
    def __init__(self, value: T):
        self.value = value
def example(current: GenericCell[int32]) -> int32:
    pair = (current, 2)
    current.value = 3
    return pair[0].value
""",
])
def test_owned_and_generic_captures_do_not_get_borrow_layouts(body: str) -> None:
    functions, constructors, _ = compile_artifacts(PRELUDE + body)
    fn = functions["example"]
    decl = next(n for n in fn.body if isinstance(n, th.THIRVarDecl) and n.name == "pair")
    # The node class alone must not classify every reference-type member as borrowed.
    assert isinstance(decl.init, th.THIRBorrowTupleLiteral)
    assert decl.init.tuple_layout is None and decl.tuple_layout is None
    result = lower_function(fn, MIRBodyId("tuples", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRNotCovered), result

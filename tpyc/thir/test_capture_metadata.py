"""Capture facts distinguish shared bindings from snapshots without admitting MIR closures."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from ..mir.lower import lower_function
from ..mir.nodes import MIRBodyId, MIRBodyKind, MIRNotCovered
from ..parse.nodes import TpyFunction, TpyIntLiteral, TpyLambda, TpyReturn
from ..typesys import BOOL, INT32
from . import nodes as th
from .lower.captures import CaptureSites
from .testutil import _compile, _entry
from .validate import THIRValidationError, _iter_children, validate_function


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for child in _iter_children(node):
        yield from walk(child)


def closures(fn: th.THIRFunction) -> list[th.THIRLambda | th.THIRNestedDef]:
    return [node for stmt in fn.body for node in walk(stmt)
            if isinstance(node, (th.THIRLambda, th.THIRNestedDef))]


@pytest.fixture(scope="module")
def functions() -> dict[str, th.THIRFunction]:
    source = """\
from typing import Callable
from tpy import int32, Fn, readonly, Own
def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)
class Cell:
    value: int32
    def __init__(self, x: int32):
        self.value = x
        def tail() -> int32:
            return self.value
        print(tail())
    def peek(self) -> int32:
        def get() -> int32:
            return self.value
        return get()
    def change(self) -> int32:
        def bump() -> int32:
            self.value += 1
            return self.value
        return bump()
    def snapshot_receiver(self) -> Callable[[], int32]:
        return lambda: self.value
def scalars(x: int32, flag: bool) -> int32:
    y = x
    def bump() -> int32:
        nonlocal y
        y += 1
        return y if flag else x
    a = apply(lambda z: z + y, x)
    saved: Callable[[], int32] = lambda: y
    return bump() + a + saved()
def escaped_scalar(x: int32) -> Callable[[], int32]:
    def get() -> int32:
        return x
    return get
def record_read(p: Cell) -> Callable[[], int32]:
    def get() -> int32:
        return p.value
    return get
def record_write(p: Cell) -> int32:
    def bump() -> int32:
        p.value += 1
        return p.value
    return bump()
def explicit_readonly(p: readonly[Cell]) -> int32:
    return apply(lambda z: p.value + z, 1)
def empty(x: int32) -> int32:
    return apply(lambda z: z + 1, x) + apply(lambda z: z + 2, x)
def shadow(x: int32) -> int32:
    return apply(lambda x: x + 1, x)
def mixed(x: int32, pair: tuple[int32, int32]) -> int32:
    return apply(lambda z: x + pair[0] + z, x)
def local_record(x: int32) -> int32:
    p = Cell(x)
    return apply(lambda z: p.value + z, x)
def copied_record(p: Cell) -> Callable[[], int32]:
    return lambda: p.value
def moved_record(x: int32) -> Callable[[], int32]:
    p = Cell(x)
    def get() -> int32:
        return p.value
    return get
def owned_record(p: Own[Cell]) -> int32:
    return apply(lambda z: p.value + z, 1)
def optional(p: int32 | None) -> int32:
    return apply(lambda z: z if p is None else 0, 1)
def union(p: int32 | bool) -> int32:
    return apply(lambda z: z if isinstance(p, bool) else 0, 1)
def view(p: str) -> int32:
    return apply(lambda z: len(p) + z, 1)
def container(p: list[int32]) -> int32:
    return apply(lambda z: len(p) + z, 1)
def late_local(x: int32) -> int32:
    if x > 0:
        print(x)
    y = x
    return apply(lambda z: z + y, x)
def key_params(x: int32) -> int32:
    y = x
    return max(x, 1, key=lambda z: z + y)
def nested(x: int32) -> int32:
    def outer() -> int32:
        inner: Callable[[], int32] = lambda: x
        return inner() + x
    return outer()
def branch(x: int32, flag: bool) -> int32:
    if flag:
        return apply(lambda z: z + 1, x)
    return apply(lambda z: z + 2, x)
def loop(x: int32) -> int32:
    for i in range(x):
        print(apply(lambda z: z + i, x))
    return x
def global_read(x: int32) -> int32:
    return apply(lambda z: z + global_value, x)
global_value = 3
global_closure: Callable[[], int32] = lambda: 1
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    result = {node.name: fn for node, fn in ctx.thir_functions.items()}
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Cell")
    result["ctor"] = th.THIRFunction(name="ctor", params=ctor.params, return_type=INT32,
                                   body=ctor.body, layout=th.THIRFunctionLayout())
    result["module"] = th.THIRFunction(name="module", params=(), return_type=INT32,
                                     body=ctx.thir_top_level.body, layout=th.THIRFunctionLayout())
    return result


def test_scalar_sources_and_selected_capture_modes(functions: dict[str, th.THIRFunction]) -> None:
    nested, reference, snapshot = closures(functions["scalars"])
    facts = {fact.source_name: fact for fact in nested.captures}
    assert set(facts) == {"x", "y", "flag"}
    assert facts["y"].source_kind is th.THIRCaptureSourceKind.LOCAL
    assert facts["x"].source_kind is th.THIRCaptureSourceKind.PARAMETER
    assert facts["flag"].type == BOOL
    assert all(f.relation is th.THIRCaptureRelation.SCALAR_BINDING and not f.readonly for f in facts.values())
    assert reference.captures[0].relation is th.THIRCaptureRelation.SCALAR_BINDING
    assert snapshot.captures[0].relation is th.THIRCaptureRelation.SCALAR_SNAPSHOT
    assert snapshot.captures[0].readonly
    escaped = closures(functions["escaped_scalar"])[0]
    assert escaped.captures[0].relation is th.THIRCaptureRelation.SCALAR_SNAPSHOT
    assert escaped.captures[0].readonly
    key_capture, = closures(functions["key_params"])[0].captures
    assert key_capture.relation is th.THIRCaptureRelation.SCALAR_BINDING
    assert not key_capture.readonly


@pytest.mark.parametrize("name,readonly", [("record_read", True), ("record_write", False),
                                           ("explicit_readonly", True)])
def test_record_capture_access(functions: dict[str, th.THIRFunction], name: str, readonly: bool) -> None:
    fact, = closures(functions[name])[0].captures
    assert fact.relation is th.THIRCaptureRelation.RECORD_REFERENT
    assert fact.source_kind is th.THIRCaptureSourceKind.PARAMETER
    assert fact.readonly is readonly


@pytest.mark.parametrize("name,readonly", [("peek", True), ("change", False),
                                           ("snapshot_receiver", True), ("ctor", False)])
def test_receiver_is_alias_in_both_capture_modes(functions: dict[str, th.THIRFunction],
                                                name: str, readonly: bool) -> None:
    fact, = closures(functions[name])[0].captures
    assert fact.relation is th.THIRCaptureRelation.RECEIVER_ALIAS
    assert fact.source_kind is th.THIRCaptureSourceKind.RECEIVER
    assert fact.readonly is readonly


def test_occurrences_slots_and_capture_free_are_distinct(functions: dict[str, th.THIRFunction]) -> None:
    first, second = closures(functions["empty"])
    assert first.loc.line == second.loc.line
    assert [first.closure_id.index, second.closure_id.index] == [0, 1]
    assert first.captures == second.captures == ()
    assert closures(functions["shadow"])[0].captures == ()
    assert closures(functions["global_read"])[0].captures == ()
    for node in closures(functions["scalars"]):
        assert [f.slot for f in node.captures] == [th.THIRCaptureSlot(node.closure_id, i)
                                                 for i in range(len(node.captures))]
    unsupported, supported = closures(functions["branch"])
    assert unsupported.captures is None and supported.captures == ()
    assert [unsupported.closure_id.index, supported.closure_id.index] == [0, 1]


@pytest.mark.parametrize("name", ["mixed", "local_record", "copied_record", "moved_record",
                                  "owned_record", "nested", "loop", "module", "optional",
                                  "union", "view", "container", "late_local"])
def test_inventory_is_all_or_unavailable(functions: dict[str, th.THIRFunction], name: str) -> None:
    nodes = closures(functions[name])
    assert nodes
    assert all(node.captures is None for node in nodes)


def test_closure_facts_do_not_admit_mir(functions: dict[str, th.THIRFunction]) -> None:
    result = lower_function(functions["record_write"], MIRBodyId("test", "record_write"),
                            kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and result.node_kind == "THIRNestedDef"


def test_malformed_inventory_is_rejected(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["record_read"]
    node = closures(fn)[0]
    fact, = node.captures
    invalid = [replace(node, closure_id=None),
               replace(node, closure_id=replace(node.closure_id, index=-1)),
               replace(node, captures=(replace(fact, slot=replace(fact.slot, index=2)),)),
               replace(node, captures=(replace(fact, source_kind=th.THIRCaptureSourceKind.LOCAL),)),
               replace(node, captures=(replace(fact, type=INT32),)),
               replace(node, captures=(replace(fact, readonly="yes"),))]
    for bad in invalid:
        with pytest.raises(THIRValidationError, match="capture|closure"):
            validate_function(replace(fn, body=(bad,)))


def test_nested_lambda_occurrences_have_separate_body_scope() -> None:
    inner = TpyLambda([], TpyIntLiteral(1))
    outer = TpyLambda([], inner)
    sibling = TpyLambda([], TpyIntLiteral(2))
    func = TpyFunction("host", [], INT32, [TpyReturn(outer), TpyReturn(sibling)])
    sites = CaptureSites(func, eligible=True).sites
    assert sites[outer] == (th.THIRClosureIdentity(0, th.THIRClosureKind.LAMBDA), False)
    assert sites[inner] == (th.THIRClosureIdentity(0, th.THIRClosureKind.LAMBDA), False)
    assert sites[sibling] == (th.THIRClosureIdentity(1, th.THIRClosureKind.LAMBDA), True)
    unavailable = CaptureSites(func, eligible=False).sites
    assert unavailable[sibling] == (sites[sibling][0], False)


def test_capture_free_closures_in_special_bodies_stay_unavailable() -> None:
    compiler, modules = _compile("""\
from tpy import int32, Fn
from typing import Iterator
def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)
def guarded(x: int32) -> int32:
    try:
        return apply(lambda z: z + 1, x)
    finally:
        print(x)
async def asynchronous(x: int32) -> int32:
    return apply(lambda z: z + 2, x)
def generator(x: int32) -> Iterator[int32]:
    yield apply(lambda z: z + 3, x)
""")
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    guarded = next(fn for node, fn in ctx.thir_functions.items() if node.name == "guarded")
    nodes = {"guarded": closures(guarded)}
    for function, body in ctx.thir_resumables.items():
        found: list[th.THIRLambda] = []
        for table in (body.leaves, body.return_values, body.yield_values):
            for value in table.values():
                for root in value if isinstance(value, tuple) else (value,):
                    found.extend(n for n in walk(root) if isinstance(n, th.THIRLambda))
        nodes[function.name] = found
    assert set(nodes) == {"guarded", "asynchronous", "generator"}
    for name, captures in nodes.items():
        assert captures, name
        assert all(node.captures is None for node in captures), name

"""Qualified external storage stays shared while local bindings stay independent."""

from dataclasses import replace
from collections.abc import Iterator
from pathlib import Path

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, _iter_children, validate_function as validate_thir
from ..typesys import BOOL, INT32
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRBodyId, MIRFunction, MIRGlobalId, MIRNotCovered, MIRSlotKind,
    MIRAssign, MIRCompare, MIRNot, MIRPlace, MIRReturn,
)
from .testutil import Reference, execute
from .validate import MIRValidationError, validate_function


SOURCE = """\
from tpy import int32
count: int32 = 1
enabled = False
def read() -> int32:
    return count
def update(flag: bool) -> int32:
    global count, enabled
    enabled = flag and ((count := 9) > 0)
    return count
def restore() -> int32:
    global count
    previous = count
    count = 4
    return previous
def param(count: int32) -> int32:
    return count
def local() -> int32:
    count = 6
    return count
def ordered() -> bool:
    global count
    return count < (count := 9)
class Writer:
    value: int32
    def __init__(self, value: int32):
        self.value = value
        global count
        count = value
    def write(self) -> int32:
        global count
        count = self.value
        return count
"""

Artifacts = tuple[dict[str, th.THIRFunction], th.THIRConstructor]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return ({node.name: fn for node, fn in ctx.thir_functions.items()},
            next(c for c in ctx.thir_constructors.values() if c.record_name == "Writer"))


def lower(fn: th.THIRFunction, module: str = "globals") -> MIRFunction:
    result = lower_function(fn, MIRBodyId(module, fn.name))
    assert isinstance(result, MIRFunction), result
    return result


def identity(fn: MIRFunction, name: str) -> MIRGlobalId:
    return next(s.global_id for s in fn.slots if s.kind is MIRSlotKind.GLOBAL and s.name == name)


def test_storage_is_shared_between_calls_and_other_bodies(artifacts: Artifacts) -> None:
    functions, ctor = artifacts
    read, update, restore = (lower(functions[name]) for name in ("read", "update", "restore"))
    count, enabled = identity(update, "count"), identity(update, "enabled")
    state = {count: 1, enabled: False}
    assert execute(update, False, global_state=state) == 1
    assert state == {count: 1, enabled: False}
    assert execute(update, True, global_state=state) == 9
    assert state == {count: 9, enabled: True}
    assert execute(read, global_state=state) == 9
    assert execute(restore, global_state=state) == 9
    assert execute(read, global_state=state) == 4
    constructor = lower_constructor(ctor, MIRBodyId("globals", "Writer.__init__"))
    assert isinstance(constructor, MIRFunction), constructor
    heap = {}
    execute(constructor, Reference(1), 12, heap=heap, global_state=state)
    assert execute(read, global_state=state) == 12
    state[count] = 0
    assert execute(lower(functions["write"]), Reference(1), heap=heap, global_state=state) == 12
    assert execute(read, global_state=state) == 12


def test_local_and_parameter_shadows_do_not_name_external_storage(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    for name in ("param", "local"):
        fn = lower(functions[name])
        assert all(s.global_id is None for s in fn.slots)
    assert execute(lower(functions["param"]), 8) == 8
    assert execute(lower(functions["local"])) == 6


def test_external_storage_must_be_supplied_and_eager_writes_stay_uncovered(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    with pytest.raises(AssertionError, match="global storage"):
        execute(lower(functions["read"]))
    result = lower_function(functions["ordered"], MIRBodyId("globals", "ordered"))
    assert isinstance(result, MIRNotCovered) and result.reason == "order-sensitive eager operands"


def test_global_metadata_controls_identity_and_permissions(artifacts: Artifacts) -> None:
    fn = artifacts[0]["read"]
    value = fn.body[0].value
    assert isinstance(value, th.THIRName) and value.global_binding is not None
    changed = replace(fn, body=(replace(fn.body[0], value=replace(value, cpp="unrelated")),))
    assert dump_function(lower(fn)) == dump_function(lower(changed))
    assert f"{value.global_binding.module}::count" in dump_function(lower(fn))
    for fact in (None, replace(value.global_binding, type=BOOL)):
        altered = replace(fn, body=(replace(fn.body[0], value=replace(value, global_binding=fact)),))
        result = lower_function(altered, MIRBodyId("globals", "bad"))
        assert isinstance(result, MIRNotCovered)
    with pytest.raises(THIRValidationError, match="global binding"):
        validate_thir(altered)
    write = th.THIRAssign(target=value, value=th.THIRLiteral(result_type=INT32, value=5))
    result = lower_function(replace(fn, body=(write, *fn.body)), MIRBodyId("globals", "badwrite"))
    assert isinstance(result, MIRNotCovered) and result.reason == "global binding is not writable"


def test_verifier_rejects_duplicate_identity_and_readonly_global_write(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["update"])
    globals = [s for s in fn.slots if s.kind is MIRSlotKind.GLOBAL]
    with pytest.raises(MIRValidationError, match="duplicate global"):
        validate_function(replace(fn, slots=tuple(
            replace(s, global_id=globals[0].global_id) if s.id == globals[1].id else s for s in fn.slots)))
    with pytest.raises(MIRValidationError, match="readonly global"):
        validate_function(replace(fn, slots=tuple(
            replace(s, readonly=True) if s.kind is MIRSlotKind.GLOBAL else s for s in fn.slots)))
    with pytest.raises(MIRValidationError, match="global identity on local"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], global_id=globals[0].global_id), *fn.slots[1:])))


def test_globals_need_explicit_loads_before_value_operations(artifacts: Artifacts) -> None:
    read = lower(artifacts[0]["read"])
    source = next(s.id for s in read.slots if s.kind is MIRSlotKind.GLOBAL)
    with pytest.raises(MIRValidationError, match="explicit read"):
        validate_function(replace(read, blocks=(replace(read.blocks[0], terminator=MIRReturn(source)),)))
    fn = lower(artifacts[0]["update"])
    flag = next(s.id for s in fn.slots if s.name == "enabled")
    temp = next(s.id for s in fn.slots if s.kind is MIRSlotKind.TEMPORARY and s.type == BOOL)
    for value in (MIRNot(flag), MIRCompare("==", flag, flag)):
        bad = MIRAssign(MIRPlace(temp), value)
        with pytest.raises(MIRValidationError, match="explicit read"):
            validate_function(replace(fn, blocks=(replace(fn.blocks[0], statements=(bad,)), *fn.blocks[1:])))
    with pytest.raises(MIRValidationError, match="explicit read"):
        validate_function(replace(fn, blocks=(replace(fn.blocks[0],
            terminator=replace(fn.blocks[0].terminator, condition=flag)), *fn.blocks[1:])))


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for child in _iter_children(node):
        yield from walk(child)


def test_nested_scope_shadows_do_not_inherit_outer_global_facts() -> None:
    source = """\
from tpy import int32
from typing import Callable
count: int32 = 1
def nested() -> int32:
    before = count
    def inner(count: int32) -> int32:
        return (count := 2)
    return inner(before)
def anonymous() -> int32:
    before = count
    fn: Callable[[int32], int32] = lambda count: count
    return fn(before)
def comprehension() -> int32:
    before = count
    values = [count for count in range(2)]
    return values[0]
def set_comprehension() -> int32:
    before = count
    values = {count for count in range(2)}
    return len(values)
def dict_comprehension() -> int32:
    before = count
    values = {count: count for count in range(2)}
    return values[0]
def generator_expression() -> int32:
    before = count
    return sum(count for count in range(2))
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    for fn in functions.values():
        first = fn.body[0]
        assert isinstance(first, th.THIRVarDecl) and first.init.global_binding is not None
        for stmt in fn.body[1:]:
            for node in walk(stmt):
                if isinstance(node, (th.THIRName, th.THIRWalrus)) and node.name == "count":
                    assert node.global_binding is None


def test_native_global_names_and_attributes_remain_uncovered(tmp_path: Path) -> None:
    declaration = 'from tpy import int32\nfrom tpy.extern import native_global\ncount: int32 = native_global("foreign")\n'
    (tmp_path / "native_store.py").write_text(declaration)
    source = declaration + """\
import native_store
def same() -> int32:
    return count
def other() -> int32:
    return native_store.count
"""
    compiler, modules = _compile(source, extra_lib_dirs=[tmp_path])
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    for name in ("same", "other"):
        fn = functions[name]
        assert fn.body[0].value.global_binding is None
        result = lower_function(fn, MIRBodyId("native", name))
        assert isinstance(result, MIRNotCovered)


@pytest.mark.parametrize("module_name", ["left", "alias"])
def test_direct_module_attributes_share_identity_but_imported_bindings_are_excluded(
        tmp_path: Path, module_name: str) -> None:
    store = "from tpy import int32\ncount: int32 = 1\ndef write(value: int32):\n    global count\n    count = value\n"
    for module in ("left", "right"):
        (tmp_path / f"{module}.py").write_text(store)
    (tmp_path / "facade.py").write_text("from left import count\n")
    source = """\
from tpy import int32
import left as MODULE
import right
import facade
from left import count as captured
def first() -> int32:
    return MODULE.count
def same() -> int32:
    return MODULE.count
def other() -> int32:
    return right.count
def imported() -> int32:
    return captured
def reexport() -> int32:
    return facade.count
"""
    source = source.replace("MODULE", module_name)
    compiler, modules = _compile(source, extra_lib_dirs=[tmp_path])
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    first, same, other = (lower(functions[name]) for name in ("first", "same", "other"))
    assert identity(first, "count") == identity(same, "count") == MIRGlobalId("left", "count")
    assert identity(other, "count") == MIRGlobalId("right", "count")
    state = {MIRGlobalId("left", "count"): 1, MIRGlobalId("right", "count"): 2}
    assert execute(first, global_state=state) == 1
    _, left_ctx = compiler.generate_code_and_thir(next(m for m in modules if m.name == "left"))
    writer = next(fn for node, fn in left_ctx.thir_functions.items() if node.name == "write")
    execute(lower(writer, "left"), 9, global_state=state)
    assert execute(same, global_state=state) == 9
    assert execute(other, global_state=state) == 2
    # These bindings currently follow foreign rebinds instead of Python import snapshots.
    # BUGS.md#imported-scalar-binding-tracks-foreign-rebind
    for name in ("imported", "reexport"):
        result = lower_function(functions[name], MIRBodyId("globals", name))
        assert isinstance(result, MIRNotCovered)

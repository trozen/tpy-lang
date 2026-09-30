"""Source constructor reads acquire expression lifetimes without changing C++."""

from dataclasses import replace
from pathlib import Path

import pytest

from ..thir import nodes as th
from ..thir.storage_facts import collect_storage_facts
from ..thir.testutil import _compile, _entry
from ..thir.validate import _iter_children
from ..typesys import INT32
from .collect import dump_codegen_mir
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRValueKind
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import MIRStorageVerdict
from .testutil import Reference, execute


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def local(value: int32) -> int32:
    # Discard, initialization and assignment are three separate expressions.
    Cell(value)
    result = Cell(value).value
    result = Cell(8).value
    return Cell(result).value

def lazy(flag: bool) -> int32:
    n = 0
    # The guard writes n before the selected constructor reads it.
    result = Cell(n).value if flag and (n := 3) == 3 else Cell(7).value
    return result

def lazy_bool(flag: bool) -> bool:
    return flag or Cell(1).value == 1

def repeat(n: int32) -> int32:
    # The final false evaluation must also construct and expire its record.
    while Cell(n).value == 1:
        n = 2
    return n

def branch(n: int32) -> int32:
    if Cell(n).value == 1:
        return Cell(4).value
    else:
        return Cell(5).value

def loop_body(flag: bool) -> int32:
    result = 0
    while flag:
        result = Cell(6).value
        flag = False
        continue
    return result

class Runner:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        # This is a body assignment, after receiver initialization.
        self.value = Cell(value).value

    def method(self, value: int32) -> int32:
        self.value = Cell(value).value
        return Cell(self.value).value
'''

Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("temporary", name), definitions=definitions,
              kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        if ctor.record_name == "Runner":
            bodies["Runner"] = lower_constructor(ctor, MIRBodyId("temporary", "Runner"), definitions=definitions)
    for name, body in bodies.items():
        assert isinstance(body, MIRFunction), (name, body)
        assert analyze_scope_ends(body).ends
        assert inspect_scope_lifetimes(body).conflicts == ()
    return functions, bodies, definitions, cpp


@pytest.mark.parametrize("name,args,expected", [
    ("local", (2,), 8), ("lazy", (True,), 3), ("lazy", (False,), 7),
    ("lazy_bool", (True,), True), ("lazy_bool", (False,), True),
    ("repeat", (1,), 2), ("repeat", (0,), 0),
    ("branch", (1,), 4), ("branch", (0,), 5),
    ("loop_body", (True,), 6), ("loop_body", (False,), 0),
])
def test_source_lowers_and_executes(artifacts: Artifacts, name: str, args: tuple, expected: int | bool) -> None:
    assert execute(artifacts[1][name], *args) == expected


def test_receiver_positions_and_inline_emission(artifacts: Artifacts) -> None:
    _, bodies, _, cpp = artifacts
    heap = {}
    execute(bodies["Runner"], Reference(0), 11, heap=heap)
    assert execute(bodies["method"], Reference(0), 13, heap=heap) == 13
    assert next(iter(heap[0].values())) == 13
    assert "Cell(value).value" in cpp and "Cell(n).value" in cpp
    assert "__tmp_" not in cpp
    assert len(analyze_scope_ends(bodies["local"]).ends) == 4


def test_lazy_source_only_constructs_when_reached(artifacts: Artifacts) -> None:
    fn = artifacts[1]["lazy_bool"]
    for flag, count in ((True, 0), (False, 1)):
        heap = {}
        assert execute(fn, flag, heap=heap) is True
        assert len(heap) == count
    heap = {}
    assert execute(artifacts[1]["repeat"], 1, heap=heap) == 2
    assert len(heap) == 2


@pytest.mark.parametrize("mutation", ["no_field", "no_storage", "effectful_constructor"])
def test_source_facts_and_verified_constructor_are_required(artifacts: Artifacts, mutation: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["lazy_bool"]
    expr = fn.body[0].value
    # The bool select's RHS is the comparison that contains the temporary read.
    rhs = expr.rhs if isinstance(expr, th.THIRValueSelect) else expr.right
    access = rhs.left
    if mutation == "no_field":
        access = replace(access, field_identity=None)
    elif mutation == "no_storage":
        access = replace(access, receiver=replace(access.receiver, full_expression_storage=None))
    else:
        ctor = definitions.records[access.receiver.result_type].constructor
        ctor = replace(ctor, body=(th.THIRExprStmt(th.THIRLiteral(INT32, 0)),))
        definitions = MIRDefinitions((ctor,))
    rhs = replace(rhs, left=access)
    expr = replace(expr, **({"rhs": rhs} if isinstance(expr, th.THIRValueSelect) else {"right": rhs}))
    fn = replace(fn, body=(replace(fn.body[0], value=expr),))
    result = lower_function(fn, MIRBodyId("bad", mutation), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=definitions)
    assert isinstance(result, MIRNotCovered)
    assert result.reason == {"no_field": "missing field identity",
                             "no_storage": "missing or invalid full-expression storage",
                             "effectful_constructor": "constructor body effects"}[mutation]
    fn = replace(fn, storage_facts=collect_storage_facts(fn.body, fn.temp_plan))
    bound = certify_thir_storage(MIRStorageRequest(fn, result.body, MIRBodyKind.FREE_FUNCTION,
                                                  definitions, {}))
    assert bound.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(g.reason == result.reason for g in bound.gaps)


@pytest.mark.parametrize("body,reason", [
    ("return consume(Cell(1))", "call needs finalized known summary"),
    ("return Cell((n := 1)).value", "effectful constructor argument"),
    ("result = Cell(1).value < (n := 2)\n    return n", "order-sensitive eager operands"),
    ("for i in range(Cell(1).value):\n        n = i\n    return n", "temporary needs full-expression boundary"),
    ("pair = (Cell(1),)\n    return pair[0].value", None),
])
def test_adjacent_consumers_stay_bounded(body: str, reason: str | None) -> None:
    source = SOURCE.split("def local")[0] + '''
def consume(x: Cell) -> int32:
    return x.value
def boundary(n: int32) -> int32:
    ''' + body + "\n"
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "boundary")
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    result = lower_function(fn, MIRBodyId("boundary", "test"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=definitions)
    if reason is None:
        assert isinstance(result, MIRFunction)
        # The named tuple's backing retains its existing body lifetime.
        assert all(s.value_kind is not MIRValueKind.RECORD_STORAGE for s in result.slots)
    else:
        assert isinstance(result, MIRNotCovered) and result.reason == reason
        bound = certify_thir_storage(MIRStorageRequest(fn, result.body, MIRBodyKind.FREE_FUNCTION,
                                                      definitions, {}))
        assert bound.verdict is MIRStorageVerdict.NOT_COVERED
        assert any(g.reason == reason for g in bound.gaps)


@pytest.mark.parametrize("qualified", [False, True])
def test_imported_constructor_boundary(tmp_path: Path, qualified: bool) -> None:
    (tmp_path / "cells.py").write_text(SOURCE.split("def local")[0])
    imported, ctor = ("import cells", "cells.Cell") if qualified else ("from cells import Cell", "Cell")
    source = f"from tpy import int32\n{imported}\ndef read() -> int32:\n    return {ctor}(5).value\n"
    compiler, modules = _compile(source, extra_lib_dirs=[tmp_path])
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "read")
    constructors = list(ctx.thir_constructors.values())
    for module in modules:
        if module.name == "cells":
            _, imported_ctx = compiler.generate_code_and_thir(module)
            constructors.extend(imported_ctx.thir_constructors.values())
    definitions = MIRDefinitions(tuple(constructors))
    result = lower_function(fn, MIRBodyId("qualified", "read"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=definitions)
    if qualified:
        # This spelling still lowers as a general THIRCall, without constructor identity.
        assert isinstance(result, MIRNotCovered) and result.reason == "reference needs local name"
    else:
        assert isinstance(result, MIRFunction) and execute(result) == 5


def test_debug_dump_exposes_temporary_region() -> None:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    output = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name,
                             MIRDefinitions(tuple(ctx.thir_constructors.values())),
                             compiler.thir_reject_by_node)
    assert "::local@" in output and "owned-storage temporary duration=r1" in output
    assert "initialize_region" in output and "scope ends" in output


def test_metadata_omitted_for_broader_shapes() -> None:
    source = '''from tpy import int32
class Wide:
    value: int
    def __init__(self, value: int):
        self.value = value
class Finalized:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def __del__(self):
        pass
class Generic[T]:
    value: T
    def __init__(self, value: T):
        self.value = value
def wide() -> int:
    return Wide(3).value
def finalized() -> int32:
    return Finalized(4).value
def generic() -> int32:
    return Generic[int32](5).value
'''
    compiler, modules = _compile(source)
    (hpp, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    assert hpp and cpp
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    for fn in ctx.thir_functions.values():
        pending = list(fn.body)
        while pending:
            node = pending.pop()
            if isinstance(node, th.THIRCtorCall):
                assert node.full_expression_storage is None
            pending.extend(_iter_children(node))
        bound = certify_thir_storage(MIRStorageRequest(fn, MIRBodyId("broader", fn.name),
                                                      MIRBodyKind.FREE_FUNCTION, definitions, {}))
        assert bound.verdict is MIRStorageVerdict.NOT_COVERED


@pytest.mark.parametrize("body", ["value = Cell(1).value\n    return value",
                                  "return (value := Cell(1).value)"])
def test_global_assignment_spellings_remain_uncovered(body: str) -> None:
    source = SOURCE.split("def local")[0] + '''
value = 0
def boundary() -> int32:
    global value
    ''' + body + "\n"
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "boundary")
    result = lower_function(fn, MIRBodyId("global", "boundary"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRNotCovered) and result.reason == "temporary global assignment"

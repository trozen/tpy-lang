"""Constructor tuples expose inline storage facts without changing emission."""

from dataclasses import replace

import pytest

from ..codegen_cpp.context import CodeGenError
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL, INT32, TpyType
from .coverage import owned_tuple
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRBodyId, MIRFunction, MIRNotCovered, MIRRegionId, MIRTupleInitialization,
)
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage
from .testutil import Reference, execute


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def singleton() -> int32:
    pair = (Cell(1),)
    pair[0].value = 9
    return pair[0].value

def multiple() -> int32:
    pair = (Cell(2), 7, Cell(3), True)
    pair[0].value = 5
    pair[2].value = 11
    return pair[2].value if pair[3] else pair[1]

def branch(flag: bool) -> int32:
    if flag:
        pair = (Cell(4), 8)
        pair[0].value = 10
        return pair[0].value
    return 0

def repeat(again: bool) -> int32:
    result = 0
    while True:
        pair = (Cell(2), Cell(3))
        pair[1].value = 7
        result = pair[1].value
        if again:
            again = False
            continue
        break
    return result

def late(flag: bool, value: int32) -> int32:
    if flag:
        value = 5
        pair = (Cell(value),)
        value = 8
        return pair[0].value
    return 0

class Runner:
    result: int32
    def __init__(self, flag: bool):
        self.result = 0
        if flag:
            pair = (Cell(1),)
            pair[0].value = 12
            self.result = pair[0].value

    def method(self, flag: bool) -> int32:
        if flag:
            pair = (Cell(2), 8)
            pair[0].value = 13
            return pair[0].value
        return 0
'''

Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("owned_tuple", name), definitions=definitions)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        if ctor.record_name == "Runner":
            bodies["Runner"] = lower_constructor(ctor, MIRBodyId("owned_tuple", "Runner"), definitions=definitions)
    for name, body in bodies.items():
        assert isinstance(body, MIRFunction), (name, body)
        assert any(owned_tuple(slot) for slot in body.slots)
    return functions, bodies, definitions, cpp


@pytest.mark.parametrize("flag", [False, True])
def test_mutation_and_operands_in_each_admitted_position(artifacts: Artifacts, flag: bool) -> None:
    _, bodies, _, _ = artifacts
    assert execute(bodies["singleton"]) == 9
    assert execute(bodies["multiple"]) == 11
    assert execute(bodies["branch"], flag) == (10 if flag else 0)
    assert execute(bodies["late"], flag, 2) == (5 if flag else 0)
    assert execute(bodies["repeat"], flag) == 7
    heap = {}
    execute(bodies["Runner"], Reference(0), flag, heap=heap)
    assert tuple(heap[0].values()) == (12 if flag else 0,)
    assert execute(bodies["method"], Reference(0), flag, heap=heap) == (13 if flag else 0)


def test_positive_producer_facts_and_inline_placement(artifacts: Artifacts) -> None:
    functions, bodies, _, cpp = artifacts
    decl = functions["multiple"].body[0]
    assert isinstance(decl, th.THIRVarDecl)
    assert decl.tuple_layout.owns_records
    assert decl.init.tuple_layout == decl.tuple_layout
    assert decl.storage_placement is th.THIRStoragePlacement.SCOPE
    assert [isinstance(m, th.THIROwnedRecord) for m in decl.tuple_layout.elements] == [True, False, True, False]
    assert "std::tuple<Cell, int32_t, Cell, bool>{Cell(2), 7, Cell(3), true}" in cpp
    for name, body in bodies.items():
        events = analyze_storage(body)
        assert len(events.member_initializations) == 1, name
        if name in ("repeat", "branch", "late", "method", "Runner"):
            backing, = [s for s in body.slots if owned_tuple(s)]
            assert isinstance(backing.storage_duration, MIRRegionId)
        assert analyze_scope_ends(body).ends
        assert not inspect_scope_lifetimes(body).conflicts
        assert any(isinstance(s.storage_write, MIRTupleInitialization)
                   for b in body.blocks for s in b.statements)


@pytest.mark.parametrize("change", ["decl_layout", "invalid_layout", "literal_layout", "placement", "form", "constructor", "copy"])
def test_damaged_producer_facts_do_not_grant_coverage(artifacts: Artifacts, change: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["singleton"]
    decl = fn.body[0]
    match change:
        case "decl_layout":
            decl = replace(decl, tuple_layout=None)
        case "invalid_layout":
            decl = replace(decl, tuple_layout=object())
        case "literal_layout":
            decl = replace(decl, init=replace(decl.init, tuple_layout=None))
        case "placement":
            decl = replace(decl, storage_placement=None)
        case "form":
            decl = replace(decl, form=th.Form.VALUE)
        case "constructor":
            value = th.THIRName(result_type=decl.init.elements[0].result_type, name="unknown", form=th.Form.BORROW)
            decl = replace(decl, init=replace(decl.init, elements=(value,)))
        case _:
            decl = replace(decl, init=th.THIRName(result_type=decl.resolved_type, name="pair", form=th.Form.STORAGE))
    bad = replace(fn, body=(decl, *fn.body[1:]))
    result = lower_function(bad, MIRBodyId("damaged", change), definitions=definitions)
    assert isinstance(result, MIRNotCovered)
    if change != "decl_layout":
        with pytest.raises(THIRValidationError):
            validate_thir(bad)


def test_renderer_type_strings_do_not_supply_storage_facts(artifacts: Artifacts) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["singleton"]
    decl = fn.body[0]
    fn = replace(fn, body=(replace(decl, cpp_type="unrelated render text"), *fn.body[1:]))
    result = lower_function(fn, MIRBodyId("spelling", "singleton"),
                            definitions=definitions)
    assert isinstance(result, MIRFunction) and execute(result) == 9


@pytest.mark.parametrize("index,typ", [(1, INT32), (3, BOOL)])
def test_scalar_projection_does_not_admit_temporary_owned_backing(artifacts: Artifacts, index: int, typ: TpyType) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["multiple"]
    literal = fn.body[0].init
    read = th.THIRSubscript(result_type=typ, receiver=literal,
                            index=th.THIRLiteral(result_type=INT32, value=index), tuple_index=index)
    callee = replace(fn.resolved_callee, signature=replace(fn.resolved_callee.signature, return_type=typ))
    fn = replace(fn, body=(th.THIRReturn(value=read),), return_type=typ, resolved_callee=callee)
    validate_thir(fn)
    result = lower_function(fn, MIRBodyId("temporary", "projection"),
                            definitions=definitions)
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "owned tuple projection needs existing local"


@pytest.mark.parametrize("source", [
    '''def boundary(flag: bool) -> int32:
    if flag:
        pair = (Cell(1),)
    else:
        pair = (Cell(2),)
    return pair[0].value
''',
    '''def boundary() -> int32:
    pair = (Cell(1),)
    pair = (Cell(2),)
    return pair[0].value
''',
])
def test_owned_tuple_source_hoists_and_rebinds_refuse_before_mir(source: str) -> None:
    # A rebound tuple local refers to its elements, so a fresh element has
    # nothing to refer to: lowering refuses it and no THIR body reaches MIR
    # (BUGS.md#pointer-repr-tuple-local-value-capture-literal).
    compiler, modules = _compile(SOURCE.split("def singleton", 1)[0] + source)
    with pytest.raises(CodeGenError, match="assigned more than once"):
        compiler.generate_code_and_thir(_entry(modules))

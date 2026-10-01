"""Fixed whole-tuple aliases name existing backing without copying its members."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL, INT32
from .coverage import owned_tuple
from .definitions import MIRDefinitions
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRFunction, MIRNotCovered, MIRRead, MIRPoint, MIRTupleCopy
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage
from .testutil import Reference, execute


SOURCE = '''from tpy import int32, nocopy
@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def example(flag: bool) -> int32:
    pair = (Cell(1), 7, Cell(2), True)
    saved = pair
    chain = saved
    if flag:
        chain[0].value = 9
    pair[2].value = 8
    return saved[0].value if flag else chain[2].value

def scalars() -> int32:
    pair = (Cell(1), 7, True)
    saved = pair
    return saved[1] if saved[2] else 0

def singleton() -> int32:
    pair = (Cell(1),)
    saved = pair
    saved[0].value = 9
    return pair[0].value

def repeat(again: bool) -> int32:
    pair = (Cell(1), 7)
    saved = pair
    chain = saved
    while again:
        chain[0].value = 9
        again = False
    return saved[0].value

class Runner:
    result: int32
    def __init__(self):
        self.result = 0
        pair = (Cell(1),)
        saved = pair
        saved[0].value = 12
        self.result = pair[0].value
    def method(self) -> int32:
        pair = (Cell(1), 7)
        saved = pair
        chain = saved
        pair[0].value = 13
        return chain[0].value
'''

Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    bodies = {name: lower(fn, definitions) for name, fn in functions.items()}
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Runner")
    bodies["Runner"] = lower_constructor(ctor, MIRBodyId("tuple_alias", "Runner"), definitions=definitions)
    assert all(isinstance(body, MIRFunction) for body in bodies.values()), bodies
    return functions, bodies, definitions, cpp


def lower(fn: th.THIRFunction, definitions: MIRDefinitions) -> MIRFunction | MIRNotCovered:
    return lower_function(fn, MIRBodyId("tuple_alias", fn.name), definitions=definitions)


def test_alias_chains_preserve_mutation_in_each_position(artifacts: Artifacts) -> None:
    _, bodies, _, _ = artifacts
    assert execute(bodies["example"], True) == 9
    assert execute(bodies["example"], False) == 8
    assert execute(bodies["scalars"]) == 7
    assert execute(bodies["singleton"]) == 9
    assert execute(bodies["repeat"], True) == 9
    assert execute(bodies["repeat"], False) == 1
    heap = {}
    execute(bodies["Runner"], Reference(0), heap=heap)
    assert tuple(heap[0].values()) == (12,)
    assert execute(bodies["method"], Reference(0), heap=heap) == 13


def test_aliases_share_one_backing_initialization_and_end(artifacts: Artifacts) -> None:
    functions, bodies, _, cpp = artifacts
    root, saved, chain = functions["example"].body[:3]
    assert saved.tuple_storage_alias == th.THIRTupleStorageAlias("pair", root.tuple_layout)
    assert chain.tuple_storage_alias == th.THIRTupleStorageAlias("saved", root.tuple_layout)
    assert saved.storage_placement is chain.storage_placement is None
    assert saved.tuple_layout is chain.tuple_layout is None
    assert "auto&& saved = pair;" in cpp and "auto&& chain = saved;" in cpp
    for body in bodies.values():
        backing, = [slot for slot in body.slots if owned_tuple(slot)]
        assert all(slot.name not in ("saved", "chain") for slot in body.slots)
        inventory = analyze_storage(body)
        assert len(inventory.member_initializations) == 1
        assert not inventory.writes
        assert all(not isinstance(getattr(stmt, "value", None), MIRTupleCopy)
                   for block in body.blocks for stmt in block.statements)
        ends = analyze_scope_ends(body)
        assert ends.ends
        assert all(sum(event.storage.root == backing.id for event in events) == 1
                   for events in ends.ends.values())
        assert not inspect_scope_lifetimes(body).conflicts
        live = analyze_liveness(body)
        reads = [(MIRPoint(block.id, i), stmt.value.source) for block in body.blocks
                 for i, stmt in enumerate(block.statements)
                 if isinstance(getattr(stmt, "value", None), MIRRead) and stmt.value.source.root == backing.id]
        assert reads
        assert all(place.root in live.points[point] for point, place in reads)
        assert "saved" not in dump_function(body)


@pytest.mark.parametrize("damage", ["missing", "invalid", "source", "layout", "readonly", "placement", "move", "form"])
def test_alias_facts_fail_closed(artifacts: Artifacts, damage: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["example"]
    root, saved, chain, *rest = fn.body
    match damage:
        case "missing":
            saved = replace(saved, tuple_storage_alias=None)
        case "invalid":
            saved = replace(saved, tuple_storage_alias=object())
        case "source":
            saved = replace(saved, tuple_storage_alias=replace(saved.tuple_storage_alias, source="absent"))
        case "layout":
            saved = replace(saved, tuple_storage_alias=replace(saved.tuple_storage_alias, layout=th.THIRTupleLayout(())))
        case "readonly":
            layout = root.tuple_layout
            layout = replace(layout, elements=(replace(layout.elements[0], readonly=True), *layout.elements[1:]))
            saved = replace(saved, tuple_storage_alias=replace(saved.tuple_storage_alias, layout=layout))
        case "placement":
            saved = replace(saved, storage_placement=th.THIRStoragePlacement.SCOPE)
        case "move":
            saved = replace(saved, init=th.THIRMove(result_type=saved.resolved_type, value=saved.init, form=th.Form.STORAGE))
        case "form":
            saved = replace(saved, form=th.Form.BORROW)
    bad = replace(fn, body=(root, saved, chain, *rest))
    assert isinstance(lower(bad, definitions), MIRNotCovered)
    if damage not in ("missing", "readonly"):
        with pytest.raises(THIRValidationError):
            validate_thir(bad)


@pytest.mark.parametrize("shape", ["forward", "unused_forward", "nested_alias", "branch_root", "out_of_scope"])
def test_erased_alias_still_requires_reached_backing(artifacts: Artifacts, shape: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["example"]
    root, saved, chain, *rest = fn.body
    branch = th.THIRIf(condition=th.THIRName(result_type=BOOL, name="flag"), then_body=(root,), else_body=())
    match shape:
        case "forward":
            body = (saved, root, chain, *rest)
        case "unused_forward":
            body = (saved, root, th.THIRReturn(value=th.THIRLiteral(result_type=INT32, value=7)))
        case "nested_alias":
            body = (root, replace(branch, then_body=(saved,)), *rest)
        case "branch_root":
            body = (branch, saved, chain, *rest)
        case "out_of_scope":
            body = (replace(branch, then_body=(root, saved)), chain, *rest)
    assert isinstance(lower(replace(fn, body=body), definitions), MIRNotCovered)


@pytest.mark.parametrize("name", ["pair", "saved", "chain"])
@pytest.mark.parametrize("declared", [False, True])
def test_root_and_alias_replacement_is_never_normalized(artifacts: Artifacts, name: str, declared: bool) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["example"]
    root, saved, chain, *rest = fn.body
    write = th.THIRAssign(target=th.THIRName(result_type=root.resolved_type, name=name, form=th.Form.STORAGE),
                         value=root.init)
    layout = replace(fn.layout, reassigned_locals=frozenset({name})) if declared else fn.layout
    result = lower(replace(fn, body=(root, saved, chain, write, *rest), layout=layout), definitions)
    assert isinstance(result, MIRNotCovered)
    assert result.reason == ("tuple alias needs fixed bindings" if declared else "owned tuple binding replacement is unsupported")


def test_rendered_spelling_cannot_supply_or_replace_alias_facts(artifacts: Artifacts) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["example"]
    root, saved, chain, *rest = fn.body
    saved = replace(saved, cpp_type="ignored spelling", cpp_local_representation=None)
    body = lower(replace(fn, body=(root, saved, chain, *rest)), definitions)
    assert isinstance(body, MIRFunction) and execute(body, True) == 9


@pytest.mark.parametrize("name", ["pair", "saved", "chain"])
@pytest.mark.parametrize("operation", ["delete", "move", "pointer_reseat", "walrus"])
def test_other_consuming_and_write_forms_stay_uncovered(artifacts: Artifacts, name: str, operation: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["example"]
    root, saved, chain, *rest = fn.body
    source = th.THIRName(result_type=root.resolved_type, name=name, form=th.Form.STORAGE)
    match operation:
        case "delete":
            stmt = th.THIRDelVar(sinks=((name, False),))
        case "move":
            stmt = th.THIRExprStmt(expr=th.THIRMove(result_type=root.resolved_type, value=source, form=th.Form.STORAGE))
        case "pointer_reseat":
            stmt = th.THIRPtrLocalRebind(name=name, value=root.init)
        case "walrus":
            stmt = th.THIRExprStmt(expr=th.THIRWalrus(result_type=root.resolved_type, name=name,
                                                    cpp_name=name, value=root.init))
    assert isinstance(lower(replace(fn, body=(root, saved, chain, stmt, *rest)), definitions), MIRNotCovered)

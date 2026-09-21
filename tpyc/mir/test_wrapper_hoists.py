"""Hoist defaults precede guards; source writes and wrapper copies keep their own timing."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL, INT32
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRPayloadWrite,
    MIRPayloadWriteMode, MIRRegionId, MIRStorageDuration, MIRStorageInit,
)
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .testutil import Reference, execute


SOURCE = '''from tpy import int32

def optional_snapshot(flag: bool) -> int32:
    if flag:
        current: int32 | None = 7
    else:
        current = None
    saved = current
    current = None
    if saved is not None:
        return saved
    return 0

def optional_bool(flag: bool) -> bool:
    if flag:
        current: bool | None = True
    else:
        current = None
    if current is not None:
        return current
    return False

def union_snapshot(flag: bool) -> int32:
    if flag:
        current: bool | int32 = False
    else:
        current = 2
    saved = current
    current = True
    if isinstance(saved, bool):
        if saved:
            return 9
        return 7
    return saved

def union_with_none(flag: bool) -> int32:
    if flag:
        current: None | bool | int32 = None
    else:
        current = 2
    if isinstance(current, int32):
        return current
    return 0

def nested(flag: bool, choose: bool, skip: bool, stop: bool) -> int32:
    result = 0
    while flag:
        seen = False
        # The guard write runs before either source assignment to current.
        if choose and (seen := True):
            current: int32 | None = 4
        else:
            current = None
        flag = False
        if skip:
            continue
        if stop:
            break
        if seen and current is not None:
            result = current
    return result

def early(choose: bool, stop: bool) -> int32:
    if choose:
        # The physical wrapper still exists when this arm exits before writing it.
        if stop:
            return 8
        current: int32 | None = 4
    else:
        current = None
    if current is not None:
        return current
    return 0

class Observer:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        if flag:
            optional: int32 | None = 1
        else:
            optional = None
        if optional is not None:
            self.value = optional
        if flag:
            union: bool | int32 = 2
        else:
            union = False
        if isinstance(union, int32):
            self.value = union

    def optional_loop(self, flag: bool) -> int32:
        while flag:
            current: int32 | None = 3
            break
        else:
            current = None
        if current is not None:
            return current
        return self.value

    def union_loop(self, flag: bool) -> int32:
        while flag:
            current: bool | int32 = 3
            break
        else:
            current = False
        if isinstance(current, bool):
            return self.value
        return current
'''


Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("wrapper_hoists", name), definitions=definitions,
                                  kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("wrapper_hoists", ctor.record_name),
                                                     definitions=definitions)
    for name, fn in bodies.items():
        assert isinstance(fn, MIRFunction), (name, fn)
        assert any(isinstance(stmt, MIRStorageInit) for block in fn.blocks for stmt in block.statements), name
    return functions, bodies, definitions, cpp


@pytest.mark.parametrize("flag", [False, True])
def test_wrapper_snapshots_and_callable_siblings(artifacts: Artifacts, flag: bool) -> None:
    _, bodies, _, _ = artifacts
    assert execute(bodies["optional_snapshot"], flag) == (7 if flag else 0)
    assert execute(bodies["optional_bool"], flag) is flag
    assert execute(bodies["union_snapshot"], flag) == (7 if flag else 2)
    assert execute(bodies["union_with_none"], flag) == (0 if flag else 2)
    heap = {}
    execute(bodies["Observer"], Reference(0), flag, heap=heap)
    assert tuple(heap[0].values()) == (2 if flag else 0,)
    for method in ("optional_loop", "union_loop"):
        assert execute(bodies[method], Reference(0), flag, heap=heap) == (3 if flag else 0)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("choose", [False, True])
@pytest.mark.parametrize("exit_kind", ["normal", "break", "continue"])
def test_nested_scopes_keep_guard_writes_and_end_the_wrapper_on_every_exit(
        artifacts: Artifacts, flag: bool, choose: bool, exit_kind: str) -> None:
    fn = artifacts[1]["nested"]
    assert execute(fn, flag, choose, exit_kind == "continue", exit_kind == "break") == (
        4 if flag and choose and exit_kind == "normal" else 0)
    current = next(s for s in fn.slots if s.name == "current")
    assert isinstance(current.storage_duration, MIRRegionId)
    ends = analyze_scope_ends(fn)
    assert sum(any(e.storage.root == current.id for e in events) for events in ends.ends.values()) == 3


@pytest.mark.parametrize("choose", [False, True])
@pytest.mark.parametrize("stop", [False, True])
def test_return_before_source_assignment_still_ends_the_wrapper(artifacts: Artifacts, choose: bool, stop: bool) -> None:
    fn = artifacts[1]["early"]
    assert execute(fn, choose, stop) == (8 if choose and stop else 4 if choose else 0)
    current = next(s for s in fn.slots if s.name == "current")
    assert current.storage_duration is MIRStorageDuration.BODY
    assert all(any(e.storage.root == current.id for e in events) for events in analyze_scope_ends(fn).ends.values())


def test_producer_defaults_match_render_order_and_source_writes_are_assignments(artifacts: Artifacts) -> None:
    functions, bodies, _, cpp = artifacts
    for name in ("optional_snapshot", "union_snapshot", "union_with_none"):
        fact, = functions[name].body[0].hoisted_bindings
        assert fact.initially_assigned is False
        default = fact.physical_default
        assert isinstance(default, th.THIRWrapperDefault) and default.alternative == 0
        first = fact.union_layout.elements[0] if fact.union_layout is not None else None
        assert type(default.value) is (type(None) if first is None else bool if first == BOOL else int)
        init = bodies[name].blocks[0].statements[0]
        assert isinstance(init, MIRStorageInit) and init.value.value == default.value
        writes = [s for block in bodies[name].blocks for s in block.statements
                  if isinstance(s, MIRAssign) and s.target.root == init.target.root]
        assert writes and all(s.storage_write == MIRPayloadWrite(MIRPayloadWriteMode.ASSIGN) for s in writes)
        body = cpp[cpp.index(f" {name}("):].split("\n}\n", 1)[0]
        declaration = "std::optional<int32_t> current;" if first is None and fact.union_layout is None else (
            functions[name].body[0].hoist_decls[0][1] + " current;")
        assert body.index(declaration) < body.index("if (") < body.index("current =")
    for fn in bodies.values():
        assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("name", ["optional_snapshot", "union_snapshot"])
@pytest.mark.parametrize("damage", ["missing", "tag", "type", "layout", "assigned", "placement", "name"])
def test_bad_producer_facts_remain_uncovered(artifacts: Artifacts, name: str, damage: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions[name]
    stmt = fn.body[0]
    fact, = stmt.hoisted_bindings
    if damage == "missing":
        fact = replace(fact, physical_default=None)
    elif damage == "tag":
        fact = replace(fact, physical_default=replace(fact.physical_default, alternative=1))
    elif damage == "type":
        fact = replace(fact, physical_default=replace(fact.physical_default, value="0"))
    elif damage == "layout":
        fact = replace(fact, union_layout=th.THIRUnionLayout(INT32, (BOOL, INT32)))
    elif damage == "assigned":
        fact = replace(fact, initially_assigned=True)
    elif damage == "placement":
        fact = replace(fact, placement=th.THIRStoragePlacement.BODY)
    else:
        fact = replace(fact, name="wrong")
    changed = replace(fn, body=(replace(stmt, hoisted_bindings=(fact,)), *fn.body[1:]))
    result = lower_function(changed, MIRBodyId("wrapper_hoists", "damaged"), definitions=definitions,
                            kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered), result
    assert any(word in result.reason for word in ("hoisted", "default", "union layout")), result
    if damage in ("tag", "type", "layout"):
        with pytest.raises(THIRValidationError):
            validate_thir(changed)


def test_physical_default_does_not_mask_missing_source_assignment(artifacts: Artifacts) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["optional_snapshot"]
    stmt = replace(fn.body[0], else_body=())
    result = lower_function(replace(fn, body=(stmt, *fn.body[1:])), MIRBodyId("wrapper_hoists", "unassigned"),
                            definitions=definitions, kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and "definite assignment" in result.reason


@pytest.mark.parametrize("damage", ["wrap", "coercion", "form", "type", "range"])
def test_contextual_optional_literal_keeps_strict_coercion_checks(artifacts: Artifacts, damage: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["optional_snapshot"]
    stmt = fn.body[0]
    assignment, = stmt.then_body
    expr = assignment.value
    assert isinstance(expr, th.THIRCoerce)
    if damage == "wrap":
        expr = replace(expr, wrap="uninterpreted({expr})")
    elif damage == "coercion":
        expr = replace(expr, coercion_name="unknown")
    elif damage == "form":
        expr = replace(expr, form=th.Form.STORAGE)
    elif damage == "type":
        expr = replace(expr, expr=replace(expr.expr, result_type=BOOL))
    else:
        expr = replace(expr, expr=replace(expr.expr, value=2**31))
    stmt = replace(stmt, then_body=(replace(assignment, value=expr),))
    result = lower_function(replace(fn, body=(stmt, *fn.body[1:])), MIRBodyId("wrapper_hoists", "literal"),
                            definitions=definitions, kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and result.node_kind in ("THIRLiteral", "THIRCoerce"), result

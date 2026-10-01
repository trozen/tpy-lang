"""Union copies retain values/referents; narrowing reads the selected payload."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry, _strict_reject, _assert_rejects_at
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL, INT32, INT32_MIN, INT32_MAX
from .dump import dump_function
from .lower import lower_function
from .nodes import MIRBodyId, MIRFieldId, MIRFunction, MIRNotCovered, MIRValueKind
from .testutil import Reference, UnionValue, execute


SOURCE = """\
from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def read(self, a: Cell | Other) -> int32:
        if isinstance(a, Cell):
            return a.value
        return 13

class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Third:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Holder:
    value: int32
    def __init__(self, a: Cell | Other):
        if isinstance(a, Cell):
            self.value = a.value
        else:
            self.value = 13

def records(a: Cell | Other, other: Cell) -> int32:
    # Copy the union, then mutate through its extracted record reference.
    saved = a
    if isinstance(saved, Cell):
        saved.value = 9
    return other.value

def readonly_read(a: readonly[Cell | Other], other: Cell) -> int32:
    # A readonly reference must still observe mutation through another alias.
    other.value = 12
    if isinstance(a, Cell):
        return a.value
    return 13

def copied(a: Cell | Other, b: Cell | Other, other: Cell) -> int32:
    current = a
    saved = current
    current = b
    # Replacing current cannot retarget saved's reference.
    other.value = 11
    if isinstance(saved, Cell):
        return saved.value
    return 13

def scalar(a: int32 | bool, b: int32 | bool) -> int32:
    current = a
    saved = current
    current = b
    # The saved scalar value belongs to its own wrapper.
    if isinstance(saved, int32):
        return saved
    return 13

def scalar_construct(a: int32 | bool) -> int32:
    current = a
    current = 7
    if isinstance(current, int32):
        return current
    return 13

def scalar_compound(a: int32 | bool) -> int32:
    # The scalar projection must run only after the successful tag test.
    if isinstance(a, int32) and a > 0:
        return a
    return 13

def boolean(a: bool | int32) -> bool:
    if isinstance(a, bool):
        return a
    return True

def early(a: Cell | Other) -> int32:
    if not isinstance(a, Cell):
        return 13
    return a.value

def compound(a: Cell | Other, flag: bool) -> int32:
    # The field read must occur only after the successful alternative test.
    if isinstance(a, Cell) and a.value > 0 and flag:
        return a.value
    return 13

def loop(a: Cell | Other, flag: bool) -> int32:
    current = a
    while isinstance(current, Cell) and flag:
        current.value = 9
        flag = False
    return 13

def nullable(a: Cell | Other | None) -> int32:
    if a is None:
        return 0
    if isinstance(a, Cell):
        return a.value
    return 13

def multiple(a: Cell | Other | Third) -> int32:
    if isinstance(a, (Cell, Other)):
        if isinstance(a, Cell):
            return a.value
        return 13
    return a.value
"""


Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {node.name: fn for node, fn in ctx.thir_functions.items()}, tuple(ctx.thir_constructors.values())


def lower(fn: th.THIRFunction) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("unions", fn.name))
    assert isinstance(result, MIRFunction), result
    return result


def union_arg(fn: MIRFunction, index: int, typ: str,
              payload: int | bool | Reference | None = None) -> UnionValue:
    layout = fn.slots[index].union_layout
    alternative = next(i for i, m in enumerate(layout.elements)
                       if (m is None and typ == "None") or m is not None and m.type.name == typ)
    return UnionValue(alternative, payload)


def cell_field(fn: MIRFunction) -> MIRFieldId:
    typ = next(m.type for s in fn.slots if s.union_layout is not None
               for m in s.union_layout.elements if m is not None and m.type.name == "Cell")
    return MIRFieldId(typ, "value")


@pytest.mark.parametrize("shared", [False, True])
def test_record_copies_preserve_shared_mutations(artifacts: Artifacts, shared: bool) -> None:
    functions, _ = artifacts
    for name, changed in (("records", 9), ("readonly_read", 12)):
        fn = lower(functions[name])
        field = cell_field(fn)
        heap = {1: {field: 2}, 2: {field: 3}}
        result = execute(fn, union_arg(fn, 0, "Cell", Reference(1)), Reference(1 if shared else 2), heap=heap)
        expected = changed if shared else (3 if name == "records" else 2)
        assert result == expected
    fn = lower(functions["copied"])
    field = cell_field(fn)
    heap = {1: {field: 2}, 2: {field: 3}}
    result = execute(fn, union_arg(fn, 0, "Cell", Reference(1)), union_arg(fn, 1, "Other", Reference(3)),
                     Reference(1 if shared else 2), heap=heap)
    assert result == (11 if shared else 2)


def test_internal_record_member_assembly(artifacts: Artifacts) -> None:
    fn = artifacts[0]["records"]
    decl = fn.body[0]
    other = fn.params[1]
    member = th.THIRName(name="other", result_type=other.borrowed_record.type, form=th.Form.BORROW)
    # Source construction is still gated; pass its semantic operation through
    # THIR coverage and MIR lowering without claiming frontend admission.
    other = replace(other, borrowed_record=replace(other.borrowed_record, readonly=False))
    ir = lower(replace(fn, params=(fn.params[0], other),
                       body=(replace(decl, init=member), *fn.body[1:])))
    field = cell_field(ir)
    heap = {1: {field: 2}}
    assert execute(ir, union_arg(ir, 0, "Other", Reference(2)), Reference(1), heap=heap) == 9


@pytest.mark.parametrize("value", [0, 7, INT32_MIN, INT32_MAX])
def test_scalar_copies_snapshot_values(artifacts: Artifacts, value: int) -> None:
    fn = lower(artifacts[0]["scalar"])
    assert execute(fn, union_arg(fn, 0, "int32", value), union_arg(fn, 1, "bool", False)) == value
    assert any(s.value_kind is MIRValueKind.PAYLOAD_ALIAS for s in fn.slots)
    constructed = lower(artifacts[0]["scalar_construct"])
    assert execute(constructed, union_arg(constructed, 0, "bool", False)) == 7


@pytest.mark.parametrize("value", [False, True])
def test_false_is_a_present_bool_alternative(artifacts: Artifacts, value: bool) -> None:
    fn = lower(artifacts[0]["boolean"])
    assert execute(fn, union_arg(fn, 0, "bool", value)) is value


def test_scalar_inline_extraction_short_circuits(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["scalar_compound"])
    for value in (INT32_MIN, 0, 7, INT32_MAX):
        assert execute(fn, union_arg(fn, 0, "int32", value)) == (value if value > 0 else 13)
    for value in (False, True):
        assert execute(fn, union_arg(fn, 0, "bool", value)) == 13


def test_guarded_early_compound_loop_and_none_paths(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    for name in ("early", "compound", "loop", "nullable", "multiple"):
        fn = lower(functions[name])
        field = cell_field(fn)
        args = (True,) if name in ("compound", "loop") else ()
        heap = {1: {field: 4}}
        assert execute(fn, union_arg(fn, 0, "Cell", Reference(1)), *args, heap=heap) == (13 if name == "loop" else 4)
        assert execute(fn, union_arg(fn, 0, "Other", Reference(2)), *args, heap={}) == 13
        if name == "loop":
            assert heap[1][field] == 9
        if name == "nullable":
            assert execute(fn, union_arg(fn, 0, "None")) == 0
        if name == "multiple":
            third = next(m.type for m in fn.slots[0].union_layout.elements if m.type.name == "Third")
            assert execute(fn, union_arg(fn, 0, "Third", Reference(1)),
                           heap={1: {MIRFieldId(third, "value"): 19}}) == 19
        text = dump_function(fn)
        assert "is-alternative" in text and "extract " in text
        assert text == dump_function(fn)


def test_method_admission_and_shared_constructor_facts(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    method = functions["read"]
    assert method.params[0].union_layout is not None
    result = lower_function(method, MIRBodyId("unions", "method"))
    assert isinstance(result, MIRFunction)
    value = MIRFieldId(method.receiver.type, "value")
    assert execute(result, Reference(1), union_arg(result, 1, "Cell", Reference(1)),
                   heap={1: {value: 7}}) == 7
    holder = next(c for c in constructors if c.record_name == "Holder")
    assert holder.params[0].union_layout is not None


@pytest.mark.parametrize("annotation", [
    "Cell | int32", "Own[Cell | Other]", "str | int32", "tuple[int32] | bool",
    "list[int32] | Cell", "str | bool", "int | bool",
])
def test_deferred_union_families_remain_uncovered(annotation: str) -> None:
    compiler, modules = _compile(SOURCE + "\nfrom tpy import Own\n" +
                                 f"def excluded(a: {annotation}) -> int32:\n    return 1\n")
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    assert isinstance(lower_function(fn, MIRBodyId("unions", "excluded")), MIRNotCovered)


def test_missing_and_contradictory_union_facts(artifacts: Artifacts) -> None:
    fn = artifacts[0]["scalar"]
    param = fn.params[0]
    missing = replace(fn, params=(replace(param, union_layout=None), *fn.params[1:]))
    assert isinstance(lower_function(missing, MIRBodyId("unions", "missing")), MIRNotCovered)
    bad_layout = replace(param.union_layout, elements=(INT32, BOOL))
    bad = replace(fn, params=(replace(param, union_layout=bad_layout), *fn.params[1:]))
    with pytest.raises(THIRValidationError):
        validate_thir(bad)


@pytest.mark.parametrize("component", ["test", "extraction", "literal"])
def test_selection_and_construction_facts_are_required(artifacts: Artifacts, component: str) -> None:
    fn = artifacts[0]["scalar_construct" if component == "literal" else "records"]
    body = list(fn.body)
    if component == "literal":
        body[1] = replace(body[1], union_literal=None)
    elif component == "test":
        body[1] = replace(body[1], condition=replace(body[1].condition, union_test=None))
    else:
        branch = body[1]
        body[1] = replace(branch, then_body=(replace(branch.then_body[0], union_extraction=None), *branch.then_body[1:]))
    bad = replace(fn, body=tuple(body))
    assert isinstance(lower_function(bad, MIRBodyId("unions", "missing")), MIRNotCovered)


@pytest.mark.parametrize("body,reason", [
    ("def excluded(a: readonly[Cell | Other]) -> int32:\n    saved = a\n    return 1\n", "payload copy"),
    ("def excluded(a: bool | int32) -> int32:\n    a = True\n    return 1\n", "union destination"),
])
def test_filed_codegen_defects_cannot_acquire_mir_coverage(body: str, reason: str) -> None:
    # BUGS.md#readonly-record-union-local-copy and
    # BUGS.md#scalar-union-parameter-reassign-const still emit invalid C++.
    compiler, modules = _compile(SOURCE + "\n" + body)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    result = lower_function(fn, MIRBodyId("unions", "excluded"))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_existing_narrowed_reseat_source_gate_is_preserved() -> None:
    source = """from tpy import int32
def sample(a: int32 | bool, b: int32 | bool) -> int32:
    current = a
    if isinstance(current, int32):
        current = b
    return 0
"""
    _, reasons = _strict_reject(source)
    _assert_rejects_at(reasons, "body:stmt.var_decl", "decl.narrowed_rebind")


@pytest.mark.parametrize("statements,reason", [
    ("    current: Cell | Other = a\n", "decl.ptr_union_source"),
    ("    current = base\n    current = a\n", "decl.union_reseat_source"),
])
def test_existing_record_member_construction_gate_is_preserved(statements: str, reason: str) -> None:
    # Direct member construction is internal IR coverage until these gates lift.
    source = SOURCE + "\ndef sample(a: Cell, base: Cell | Other) -> int32:\n" + statements + "    return 0\n"
    _, reasons = _strict_reject(source)
    _assert_rejects_at(reasons, "body:stmt.var_decl", reason)

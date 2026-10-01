"""Resolved declarations survive aliases without claiming call-effect coverage."""

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from ..mir.lower import lower_function
from ..mir.nodes import MIRBodyId, MIRNotCovered
from ..parse.nodes import TpyCall, TpyName, TpyVarDecl
from ..sema.analyzer import SemanticAnalyzer
from ..sema.context import FunctionTrackingState
from ..sema.reach_analysis import _iter_typed_children
from ..type_def_registry import ParamPassing
from ..typesys import (
    BOOL, INT32, MutationCallEdge, NominalType, OwnType, ReadonlyType, Representation, TupleType, unwrap_ref_type,
)
from . import nodes as th
from .lower.callables import resolved_callee
from .testutil import _compile, _entry
from .validate import THIRValidationError, _iter_children, validate_function


Artifacts = tuple[dict[str, th.THIRFunction], th.THIRFunction, SemanticAnalyzer,
                  dict[str, tuple[th.THIRNode, ...]]]


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for child in _iter_children(node):
        yield from walk(child)


def calls(fn: th.THIRFunction) -> list[th.THIRCall]:
    return [node for stmt in fn.body for node in walk(stmt) if isinstance(node, th.THIRCall)]


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Artifacts:
    folder = tmp_path_factory.mktemp("callee_metadata")
    helper = "from tpy import int32\ndef choose(flag: bool, x: int32) -> int32:\n    return x if flag else 0\n"
    (folder / "helper.py").write_text('# tpy: cpp_namespace("custom_helpers")\n' + helper)
    (folder / "other.py").write_text(helper)
    (folder / "facade.py").write_text("from helper import choose as exported\n")
    source = """\
# tpy: cpp_namespace("custom_entry")
from tpy import int32
from helper import choose as selected
from facade import exported
import helper as h
import other
def choose(flag: bool, x: int32) -> int32:
    return x if flag else 0
def run(x: int32) -> int32:
    a = selected(True, x)
    b = exported(True, x)
    c = h.choose(True, x)
    d = other.choose(True, x)
    return choose(True, x)
def shadow(selected: int32) -> int32:
    def choose(x: int32) -> int32:
        return x
    return choose(selected)
class Caller:
    value: int32
    def __init__(self, x: int32):
        self.value = selected(True, x)
    def read(self) -> int32:
        return selected(True, self.value)
initial = selected(True, 1)
"""
    compiler, modules = _compile(source, extra_lib_dirs=[folder])
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    _, helper_ctx = compiler.generate_code_and_thir(next(m for m in modules if m.name == "helper"))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Caller")
    positions = {"method": functions["read"].body,
                 "constructor": tuple(mil.value for mil in ctor.mil_inits),
                 "module": ctx.thir_top_level.body}
    return functions, next(iter(helper_ctx.thir_functions.values())), _entry(modules).analyzer, positions


def test_aliases_reexports_and_namespaces_keep_declaration_identity(artifacts: Artifacts) -> None:
    functions, helper, _, _ = artifacts
    nodes = calls(functions["run"])
    facts = [node.resolved_callee for node in nodes]
    assert len(facts) == 5 and all(fact is not None for fact in facts)
    assert facts[:3] == [helper.resolved_callee] * 3
    assert facts[0].identity == th.THIRFunctionIdentity("helper", "choose")
    assert facts[3].identity == th.THIRFunctionIdentity("other", "choose")
    assert facts[4] == functions["choose"].resolved_callee
    assert facts[4].identity == th.THIRFunctionIdentity("main", "choose")
    assert facts[0].signature == th.THIRCallableSignature(
        (BOOL, INT32), INT32, passings=(ParamPassing.VALUE, ParamPassing.VALUE),
        return_representation=Representation.STORAGE)
    assert "custom_helpers" in nodes[0].callee_cpp
    assert "custom_entry" in nodes[-1].callee_cpp
    assert calls(functions["shadow"])[0].resolved_callee is None


def test_callee_metadata_is_not_call_effect_coverage(artifacts: Artifacts) -> None:
    functions, _, _, _ = artifacts
    fn = functions["run"]
    result = lower_function(fn, MIRBodyId("test", "run"))
    assert isinstance(result, MIRNotCovered) and result.node_kind == "THIRCall"


def test_declaration_evidence_survives_semantic_state_snapshot(artifacts: Artifacts) -> None:
    _, _, analyzer, _ = artifacts
    fi = analyzer.registry.get_function("choose")[0]
    declaration = fi.declaration
    assert declaration is not None
    state = FunctionTrackingState(current_call_edges=[MutationCallEdge(fi, {})])
    copied = deepcopy(state)
    assert copied.current_call_edges is not state.current_call_edges
    assert copied.current_call_edges[0].callee_fi is fi
    assert copied.current_call_edges[0].callee_fi.declaration is declaration


def test_declaration_body_does_not_expand_caller_type_reach(artifacts: Artifacts) -> None:
    _, _, analyzer, _ = artifacts
    fi = analyzer.registry.get_function("choose")[0]
    internal = NominalType("Internal", _module_qname="hidden.Internal")
    declaration = replace(fi.declaration, body=[TpyVarDecl("local", internal, None)])
    call = TpyCall(TpyName("choose"), [], resolved_function_info=replace(fi, declaration=declaration))
    types = _iter_typed_children(call)
    assert INT32 in types and BOOL in types
    assert internal not in types


def test_shared_producer_preserves_identity_across_body_positions(artifacts: Artifacts) -> None:
    _, helper, _, positions = artifacts
    for position, body in positions.items():
        nodes = [node for stmt in body for node in walk(stmt)
                 if isinstance(node, th.THIRCall) and node.callee == "selected"]
        assert len(nodes) == 1, position
        assert nodes[0].resolved_callee == helper.resolved_callee, position


def test_signature_wrappers_are_preserved_without_storage_inference() -> None:
    source = """\
from tpy import int32, Own, readonly
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def borrowed(a: Cell) -> int32:
    return a.value
def owned(a: Own[Cell]) -> int32:
    return a.value
def constant(a: readonly[Cell]) -> int32:
    return a.value
@readonly
def decorated(a: Cell) -> int32:
    return a.value
def wrappers(a: tuple[int32], b: tuple[Cell, int32], c: int32 | None,
             d: int32 | bool, e: str, f: bytes) -> int32:
    return 0
def use(a: Cell) -> int32:
    return borrowed(a)
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    for fn in functions.values():
        assert fn.resolved_callee is not None
        validate_function(fn)
    assert isinstance(unwrap_ref_type(functions["owned"].resolved_callee.signature.param_types[0]), OwnType)
    assert isinstance(unwrap_ref_type(functions["constant"].resolved_callee.signature.param_types[0]), ReadonlyType)
    assert isinstance(unwrap_ref_type(functions["wrappers"].resolved_callee.signature.param_types[0]), TupleType)
    assert calls(functions["use"])[0].resolved_callee == functions["borrowed"].resolved_callee


@pytest.mark.parametrize("flag,value", [
    ("declaration", None), ("is_async", True), ("is_generator", True),
    ("is_method", True), ("is_staticmethod", True), ("is_classmethod", True),
    ("is_constructor", True), ("is_callable_value", True),
    ("is_builtin_function", True), ("special_handling", True),
    ("native_name", "foreign"), ("cpp_template", "foreign({0})"),
    ("error_return_type", "Error"), ("inline_body", object()),
    ("frame_captures", []), ("type_params", ["T"]),
])
def test_nonordinary_callees_have_no_descriptor(
    artifacts: Artifacts, monkeypatch: pytest.MonkeyPatch, flag: str, value: object,
) -> None:
    _, _, analyzer, _ = artifacts
    fi = analyzer.registry.get_function("choose")[0]
    assert resolved_callee(fi, analyzer, arity=2) is not None
    monkeypatch.setattr(fi, flag, value)
    assert resolved_callee(fi, analyzer, arity=2) is None


def test_actual_overload_default_generic_and_callback_boundaries() -> None:
    source = """\
from typing import overload
from tpy import int32, Fn
@overload
def overloaded(x: int32) -> int32: ...
def overloaded(x: int32) -> int32:
    return x
def default(x: int32 = 3) -> int32:
    return x
def generic[T](x: T) -> T:
    return x
def callback(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)
def run(x: int32) -> int32:
    a = overloaded(x)
    b = default()
    c = generic(x)
    return default(x)
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    overloads = [fn for group in ctx.thir_overload_functions.values() for fn in group.values()]
    assert len(overloads) == 1 and overloads[0].resolved_callee is None
    assert functions["generic"].resolved_callee is None
    assert functions["callback"].resolved_callee is None
    nodes = calls(functions["run"])
    assert len(nodes) == 4
    assert [node.resolved_callee for node in nodes[:3]] == [None] * 3
    assert nodes[3].resolved_callee == functions["default"].resolved_callee
    assert calls(functions["callback"])[0].resolved_callee is None


def test_invalid_descriptors_fail_validation(artifacts: Artifacts) -> None:
    functions, _, _, _ = artifacts
    fn = functions["choose"]
    fact = fn.resolved_callee
    for changed in (
        replace(fact, identity=replace(fact.identity, module="")),
        replace(fact, signature=replace(fact.signature, param_types=(BOOL,))),
        replace(fact, signature=replace(fact.signature, return_type=BOOL)),
    ):
        with pytest.raises(THIRValidationError, match="resolved callee"):
            validate_function(replace(fn, resolved_callee=changed))
    call = calls(functions["run"])[0]
    for changed in (replace(call, args=()), replace(call, native_name="helper")):
        with pytest.raises(THIRValidationError, match="resolved callee"):
            validate_function(replace(fn, body=(th.THIRReturn(changed),)))


@pytest.mark.parametrize("second_type", ["int32", "bool"])
def test_repeated_declarations_are_not_unique(second_type: str) -> None:
    source = f"""\
from tpy import int32
def pick(x: int32) -> int32:
    return 1
def pick(x: {second_type}) -> int32:
    return 2
def run() -> int32:
    return pick({"True" if second_type == "bool" else "3"})
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = [fn for node, fn in ctx.thir_functions.items() if node.name == "pick"]
    assert len(definitions) == 2 and all(fn.resolved_callee is None for fn in definitions)
    run = next(fn for node, fn in ctx.thir_functions.items() if node.name == "run")
    assert calls(run)[0].resolved_callee is None


def test_cycle_signature_must_agree_with_final_declaration(tmp_path: Path) -> None:
    folder = tmp_path
    (folder / "a.py").write_text("from b import Color\ndef lookup() -> Color:\n    return Color.RED\n")
    (folder / "b.py").write_text("from a import lookup\nfrom enum import Enum\n"
                               "class Color(Enum):\n    RED = 1\n"
                               "def get() -> Color:\n    return lookup()\n")
    compiler, modules = _compile("from a import lookup\ndef run():\n    print(lookup())\n",
                                 extra_lib_dirs=[folder])
    for module in modules:
        if module.name in ("a", "b", "main"):
            _, ctx = compiler.generate_code_and_thir(module)
            for fn in ctx.thir_functions.values():
                if fn.name == "lookup":
                    assert fn.resolved_callee is None
                for call in calls(fn):
                    if call.callee == "lookup":
                        assert call.resolved_callee is None

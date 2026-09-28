"""Native iteration facts agree with the shared emitted binding decision."""

from dataclasses import replace

import pytest

from ..codegen_cpp.forms import LoopBinding
from ..typesys import INT32, NominalType, ReadonlyType, TypeParamRef, TypeParamKind
from .lower.storage import native_container
from .nodes import THIRBorrowedRecord, THIRForEach, THIRForRange
from .testutil import _compile, _entry
from .validate import THIRValidationError, validate_function


@pytest.fixture(scope="module")
def functions():
    source = '''from tpy import int32, readonly, Array
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def mutable(xs: list[Cell]):
    for x in xs:
        x.value = 7
def const(xs: readonly[Array[Cell, 3]]) -> int32:
    result = 0
    for x in xs:
        result = x.value
    return result
def scalar(xs: set[int32]) -> int32:
    last = 5
    for last in xs:
        pass
    return last
def range_hoist(n: int32) -> int32:
    for i in range(n):
        result = i
    else:
        result = 7
    return result
'''
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {fn.name: fn for fn in ctx.thir_functions.values()}


def test_native_source_and_binding_capabilities(functions) -> None:
    for name, readonly, binding in (("mutable", False, LoopBinding.REFERENCE),
                                    ("const", True, LoopBinding.CONST_REFERENCE),
                                        ("scalar", True, LoopBinding.ASSIGN)):
        fn = functions[name]
        loop = next(st for st in fn.body if isinstance(st, THIRForEach))
        assert loop.iteration.source == fn.params[0].native_container
        assert loop.iteration.source.readonly is readonly
        assert loop.iteration.binding is binding
        element = loop.iteration.source.element
        if name == "scalar":
            assert element == INT32
        else:
            assert isinstance(element, THIRBorrowedRecord) and element.readonly is readonly
    loop = next(st for st in functions["range_hoist"].body if isinstance(st, THIRForRange))
    assert tuple(b.name for b in loop.hoisted_bindings) == tuple(d.name for d in loop.hoist_decls)
    assert "result" in {b.name for b in loop.hoisted_bindings}


@pytest.mark.parametrize("damage", ["binding", "source_type", "element", "consuming", "rvalue", "param_access"])
def test_conflicting_native_facts_rejected(functions, damage: str) -> None:
    fn = functions["const"]
    loop = next(st for st in fn.body if isinstance(st, THIRForEach))
    fact = loop.iteration
    params = fn.params
    match damage:
        case "binding":
            loop = replace(loop, iteration=replace(fact, binding=LoopBinding.VALUE))
        case "source_type":
            loop = replace(loop, iteration=replace(fact, source=replace(fact.source, type=INT32)))
        case "element":
            loop = replace(loop, iteration=replace(fact, source=replace(fact.source, element=INT32)))
        case "consuming":
            loop = replace(loop, consuming=True)
        case "rvalue":
            loop = replace(loop, iterable_lvalue=False)
        case "param_access":
            params = (replace(params[0], native_container=replace(params[0].native_container, readonly=False)),)
    body = tuple(loop if isinstance(st, THIRForEach) else st for st in fn.body)
    with pytest.raises(THIRValidationError):
        validate_function(replace(fn, params=params, body=body))


@pytest.mark.parametrize("args", [(), (INT32, INT32), (INT32, -1), (INT32, True)])
def test_native_argument_shape_rejected(functions, args) -> None:
    fn = functions["const"]
    param = fn.params[0]
    typ = replace(param.native_container.type, type_args=args)
    param = replace(param, type=typ, native_container=replace(param.native_container, type=typ))
    with pytest.raises(THIRValidationError):
        validate_function(replace(fn, params=(param,)))


@pytest.mark.parametrize("args", [(INT32, TypeParamRef("N", kind=TypeParamKind.INT)),
                                  (INT32,), (INT32, -1), (INT32, True)])
def test_unsupported_array_shape_does_not_acquire_native_fact(args) -> None:
    typ = NominalType("Array", args, _module_qname="tpy.Array")
    assert native_container(typ, False, None) is None
    concrete = replace(typ, type_args=(INT32, 3))
    assert native_container(concrete, False, None) is not None


def test_element_qualification_does_not_acquire_plain_element_fact() -> None:
    cell = NominalType("Cell", _module_qname="native.Cell")
    typ = NominalType("list", (ReadonlyType(cell),), _module_qname="builtins.list")
    assert native_container(typ, False, None) is None


def test_explicit_readonly_container_preserves_access_without_inferred_const() -> None:
    typ = NominalType("list", (INT32,), _module_qname="builtins.list")
    fact = native_container(ReadonlyType(typ), False, None)
    assert fact is not None and fact.readonly

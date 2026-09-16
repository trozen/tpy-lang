"""Context supplies lambda signatures before lowering selects supported shapes."""

import pytest

from .parse.nodes import TpyLambda
from .thir.testutil import _compile, _entry
from .typesys import TypeParamRef


@pytest.mark.parametrize("parameter", [
    "int32", "tuple[int32, int32]", "int32 | None", "int32 | str",
    "str", "bytes", "list[int32]", "readonly[list[int32]]",
    "Own[list[int32]]", "Ptr[int32]", "Span[int32]", "Box[int32]", "Rc[int32]",
])
def test_owned_callable_preserves_parameter_context(parameter: str) -> None:
    source = f'''from typing import Callable
from tpy import Own, readonly, Ptr, Span, Box, Rc, int32
def accept(f: Own[Callable[[{parameter}], int32]]):
    pass
def run():
    accept(lambda x: 1)
'''
    _, modules = _compile(source)
    lambdas = [expr for expr in _entry(modules).analyzer.ctx.expr_types
               if isinstance(expr, TpyLambda)]
    assert len(lambdas) == 1
    assert str(lambdas[0].inferred_param_types[0]) == parameter
    assert lambdas[0].captures_by_value


def test_generic_container_supplies_open_lambda_parameter() -> None:
    source = '''from typing import Callable
def install[T](callbacks: list[Callable[[T], T]]):
    callbacks.append(lambda x: x)
'''
    _, modules = _compile(source)
    lambdas = [expr for expr in _entry(modules).analyzer.ctx.expr_types
               if isinstance(expr, TpyLambda)]
    assert len(lambdas) == 1
    assert isinstance(lambdas[0].inferred_param_types[0], TypeParamRef)
    assert lambdas[0].inferred_return_type == lambdas[0].inferred_param_types[0]

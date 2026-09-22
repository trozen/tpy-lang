"""Return-borrow facts retain the distinct receiver and parameter const policies."""

from types import SimpleNamespace

import pytest

from .codegen_cpp.functions import FunctionGenerator
from .parse import TpyFunction
from .sema.mutation_propagation import infer_method_const
from .typesys import (
    BYTESVIEW, INT32, STRVIEW, FunctionInfo, NominalType, OptionalType,
    OwnType, ParamInfo, ReadonlyType, RecordInfo, TpyType, TupleType,
    TypeParamRef, TypeRegistry, UnionType, make_span,
)


RECORD = NominalType("Cell")
MUTATED = frozenset({0, 1})
SELF = frozenset({-1})
PARAM = frozenset({0})


@pytest.mark.parametrize("result,roots,mutated,readonly,remaining", [
    pytest.param(RECORD, None, None, True, None, id="unknown-facts"),
    pytest.param(RECORD, None, MUTATED, True, MUTATED, id="unknown-roots"),
    pytest.param(RECORD, frozenset(), MUTATED, True, MUTATED, id="empty-roots"),
    pytest.param(RECORD, SELF, MUTATED, False, MUTATED, id="self-borrow"),
    pytest.param(RECORD, PARAM, MUTATED, True, MUTATED, id="param-borrow"),
    pytest.param(RECORD, SELF | PARAM, MUTATED, False, frozenset({1}),
                 id="self-and-param-borrow"),
    pytest.param(RECORD, PARAM, None, True, None, id="unknown-mutations"),
    pytest.param(STRVIEW, SELF | PARAM, MUTATED, True, frozenset({1}), id="str-view"),
    pytest.param(BYTESVIEW, SELF, MUTATED, True, MUTATED, id="bytes-view"),
    pytest.param(make_span(INT32), SELF, MUTATED, False, MUTATED, id="mutable-span"),
    pytest.param(make_span(INT32, is_readonly=True), SELF, MUTATED, True, MUTATED,
                 id="readonly-span"),
    pytest.param(ReadonlyType(STRVIEW), SELF, MUTATED, False, MUTATED,
                 id="wrapped-view"),
    pytest.param(OptionalType(STRVIEW), SELF, MUTATED, False, MUTATED,
                 id="optional-view"),
    pytest.param(TupleType((STRVIEW,)), SELF, MUTATED, False, MUTATED,
                 id="tuple-view"),
    pytest.param(TupleType((RECORD, INT32)), SELF, MUTATED, False, MUTATED,
                 id="mixed-tuple"),
    pytest.param(UnionType((RECORD, INT32)), SELF, MUTATED, False, MUTATED,
                 id="union"),
    pytest.param(OwnType(RECORD), SELF, MUTATED, False, MUTATED, id="owned-result"),
    pytest.param(TypeParamRef("T"), SELF, MUTATED, False, MUTATED, id="generic"),
    pytest.param(NominalType("Cancellable", (STRVIEW,)), SELF, MUTATED,
                 False, MUTATED, id="async-wrapped-view"),
])
def test_const_inference_consumers(
        result: TpyType, roots: frozenset[int] | None,
        mutated: frozenset[int] | None, readonly: bool,
        remaining: frozenset[int] | None) -> None:
    fi = FunctionInfo(
        name="get", params=[ParamInfo("borrowed", RECORD), ParamInfo("changed", RECORD)],
        return_type=result, is_method=True, direct_self_mutated=False,
        self_mutated=False, mutated_params=mutated, return_borrows_from=roots,
    )
    infer_method_const([fi])
    assert fi.is_readonly is readonly

    registry = TypeRegistry()
    registry.records["Owner"] = RecordInfo("Owner", [], methods={"get": [fi]})
    ctx = SimpleNamespace(analyzer=SimpleNamespace(registry=registry))
    generator = FunctionGenerator(ctx, None, None)
    method = TpyFunction("get", [], result, [])
    # Return-root subtraction must not drop mutations of unrelated parameters.
    assert generator._get_method_genuine_mutated_params(method, "Owner") == remaining
    assert fi.return_borrows_from is roots
    assert fi.mutated_params is mutated


@pytest.mark.parametrize("flags", [
    {"is_staticmethod": True},
    {"is_consuming": True},
    {"is_auto_readonly_mutable_clone": True},
    {"name": "__init__"},
    {"name": "__iadd__"},
    {"direct_self_mutated": None},
    {"self_mutated": True},
])
def test_const_inference_exclusions(flags: dict[str, object]) -> None:
    fi = FunctionInfo(
        name="get", params=[], return_type=STRVIEW, is_method=True,
        direct_self_mutated=False, self_mutated=False, return_borrows_from=SELF,
    )
    for name, value in flags.items():
        setattr(fi, name, value)
    # Even an inherently const view does not override these method constraints.
    infer_method_const([fi])
    assert not fi.is_readonly

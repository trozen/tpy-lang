"""Pin the distinct call, coroutine-payload and erased-callable conventions."""

from typing import cast

import pytest

from .compilation_context import get_current_compiler
from .typesys import (
    BYTES, BYTESVIEW, INT32, STR, STRVIEW, VOID, CallableType, FunctionInfo,
    FunctionLinkage,
    NominalType, OptionalType, OwnType, PtrType, ReadonlyType,
    RecursiveAliasInstanceType, RefType, SendType, SyncType, TpyType,
    TupleType, TypeParamRef, UnionType, make_span,
)
from .value_category import (
    AsyncReturnForm, ValueCategoryAnalyzer, async_return_form,
    call_returns_cpp_ref,
)


CELL = NominalType("Cell")
OTHER = NominalType("Other")
GENERIC = TypeParamRef("T")
PROTOCOL = NominalType("Readable", is_protocol=True)
RECURSIVE = RecursiveAliasInstanceType("example.Tree", (INT32,))
STORAGE = AsyncReturnForm.STORAGE
BORROW = AsyncReturnForm.BORROW
TRAIT = AsyncReturnForm.TRAIT


@pytest.fixture(autouse=True)
def _recursive_alias_render_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared unit context lacks this compiler-owned name map.
    monkeypatch.setattr(get_current_compiler(), "recursive_alias_cpp_names", {},
                        raising=False)


@pytest.mark.parametrize("result,call_ref,payload,erased", [
    pytest.param(CELL, True, BORROW, "Cell", id="reference"),
    pytest.param(INT32, False, STORAGE, "int32_t", id="value"),
    # Known wrong verdict: BUGS.md#void-method-reads-as-borrow-returning.
    pytest.param(VOID, True, STORAGE, "void", id="void-existing-answer"),
    pytest.param(OwnType(CELL), False, STORAGE, "Cell", id="owned-reference"),
    pytest.param(RefType(CELL), True, BORROW, "Cell&", id="explicit-ref"),
    pytest.param(GENERIC, False, TRAIT, "T", id="generic"),
    pytest.param(OwnType(GENERIC), False, STORAGE, "T", id="owned-generic"),
    pytest.param(RefType(GENERIC), False, TRAIT, "::tpy::val_or_ref_t<T>",
                 id="ref-generic"),
    pytest.param(TypeParamRef("V", bound=NominalType(
        "ValueType", is_protocol=True, _module_qname="tpy.ValueType")),
        False, TRAIT, "V", id="value-bounded-generic"),
    pytest.param(OptionalType(INT32), False, STORAGE, "std::optional<int32_t>",
                 id="optional-value"),
    pytest.param(OptionalType(CELL), False, BORROW, "std::optional<Cell>",
                 id="optional-reference"),
    pytest.param(OptionalType(INT32, force_pointer_repr=True), False, BORROW,
                 "std::optional<int32_t>", id="forced-pointer-optional"),
    pytest.param(OptionalType(GENERIC), False, BORROW, "std::optional<T>",
                 id="optional-generic"),
    pytest.param(UnionType((CELL, OTHER)), False, STORAGE,
                 "::tpy::Union<Cell, Other>", id="reference-union"),
    pytest.param(UnionType((CELL, INT32)), False, STORAGE,
                 "::tpy::Union<Cell, int32_t>", id="mixed-union"),
    pytest.param(RECURSIVE, True, STORAGE, "Tree<int32_t>",
                 id="recursive-wrapper"),
    pytest.param(ReadonlyType(RECURSIVE), True, STORAGE, "Tree<int32_t>",
                 id="readonly-recursive-wrapper"),
    pytest.param(TupleType(()), False, STORAGE, "std::tuple<>", id="empty-tuple"),
    pytest.param(TupleType((CELL,)), False, STORAGE, "std::tuple<Cell>",
                 id="singleton-tuple"),
    pytest.param(TupleType((CELL, INT32)), False, STORAGE,
                 "std::tuple<Cell, int32_t>", id="mixed-tuple"),
    pytest.param(TupleType((RefType(CELL),)), False, STORAGE,
                 "std::tuple<Cell&>", id="tuple-preserves-raw-ref"),
    pytest.param(ReadonlyType(CELL), True, BORROW, "Cell", id="readonly"),
    # Qualifier order is observable today; erasure must not normalize it away.
    pytest.param(ReadonlyType(GENERIC), True, TRAIT, "T", id="readonly-generic"),
    pytest.param(ReadonlyType(OptionalType(CELL)), True, BORROW,
                 "std::optional<Cell>", id="readonly-optional"),
    pytest.param(ReadonlyType(UnionType((CELL, OTHER))), True, STORAGE,
                 "::tpy::Union<Cell, Other>", id="readonly-union"),
    pytest.param(RefType(ReadonlyType(GENERIC)), True, TRAIT, "T&",
                 id="ref-readonly-generic"),
    pytest.param(ReadonlyType(RefType(GENERIC)), True, BORROW,
                 "::tpy::val_or_ref_t<T>", id="readonly-ref-generic"),
    pytest.param(SendType(SyncType(RefType(GENERIC))), True, TRAIT,
                 "::tpy::val_or_ref_t<T>", id="markers-outside-ref"),
    pytest.param(RefType(SendType(GENERIC)), True, BORROW, "T&",
                 id="marker-inside-ref"),
    pytest.param(SyncType(ReadonlyType(GENERIC)), True, TRAIT, "T",
                 id="marker-outside-readonly"),
    pytest.param(ReadonlyType(SyncType(GENERIC)), True, BORROW, "T",
                 id="marker-inside-readonly"),
    pytest.param(STR, False, STORAGE, "std::string", id="string"),
    pytest.param(BYTES, False, STORAGE, "::tpy::Bytes", id="bytes"),
    pytest.param(STRVIEW, False, STORAGE, "std::string_view", id="string-view"),
    pytest.param(BYTESVIEW, False, STORAGE, "::tpy::BytesView", id="bytes-view"),
    pytest.param(PtrType(CELL), False, STORAGE, "Cell*", id="pointer"),
    pytest.param(make_span(INT32), False, STORAGE, "std::span<int32_t>",
                 id="mutable-span"),
    pytest.param(make_span(INT32, is_readonly=True), False, STORAGE,
                 "std::span<const int32_t>", id="readonly-span"),
    pytest.param(PROTOCOL, False, STORAGE, "T", id="structural-protocol"),
    pytest.param(ReadonlyType(PROTOCOL), True, STORAGE, "T",
                 id="readonly-protocol"),
    pytest.param(NominalType("Dynamic", is_protocol=True,
                            is_dynamic_protocol=True),
                 False, STORAGE, "Dynamic", id="dynamic-protocol"),
])
def test_result_consumers(
        result: TpyType, call_ref: bool, payload: AsyncReturnForm,
        erased: str) -> None:
    fi = FunctionInfo(name="get", params=[], return_type=result)
    # Classification needs only callee facts, not an expression or registry.
    assert call_returns_cpp_ref(cast(ValueCategoryAnalyzer, None), fi) is call_ref
    assert async_return_form(result) is payload
    callable_type = CallableType((), result)
    assert callable_type._std_function_sig() == f"std::function<{erased}()>"
    assert callable_type.to_cpp() == f"std::function<{erased}()>"
    assert fi.return_type is result
    assert callable_type.return_type is result


@pytest.mark.parametrize("native,method,constructor,expected", [
    (False, False, False, True),
    (False, True, False, True),
    (True, False, False, False),
    (True, True, False, True),
    (False, False, True, False),
    (True, True, True, False),
])
def test_callee_metadata_controls_call_reference(
        native: bool, method: bool, constructor: bool, expected: bool) -> None:
    linkage = FunctionLinkage.NATIVE if native else FunctionLinkage.DEFAULT
    fi = FunctionInfo(name="get", params=[], return_type=CELL,
                      linkage=linkage, is_method=method,
                      is_constructor=constructor)
    # The free-native exception must not swallow native record methods.
    assert call_returns_cpp_ref(cast(ValueCategoryAnalyzer, None), fi) is expected


def test_missing_return_information() -> None:
    assert not call_returns_cpp_ref(cast(ValueCategoryAnalyzer, None), None)
    fi = FunctionInfo(name="get", params=[], return_type=None)
    assert not call_returns_cpp_ref(cast(ValueCategoryAnalyzer, None), fi)
    assert async_return_form(None) is STORAGE


def test_erased_result_extraction_preserves_parameter_conventions() -> None:
    callback = CallableType((CELL, ReadonlyType(CELL), STR), RefType(GENERIC))
    signature = (
        "std::function<::tpy::val_or_ref_t<T>"
        "(Cell&, const Cell&, std::string_view)>"
    )
    assert callback.to_cpp() == signature
    assert callback.to_cpp_param_type() == f"const {signature}&"

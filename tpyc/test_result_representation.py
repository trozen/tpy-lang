"""Pin the distinct call, coroutine-payload and erased-callable conventions."""

import functools
from typing import Callable, cast

import pytest

from .compilation_context import get_current_compiler
from .parse import TpyCall, TpyExpr, TpyFunction
from .parse.nodes import walk_body_stmts, walk_expr_tree
from .thir.testutil import _compile, _entry, _strict_reject
from .typesys import (
    BYTES, BYTESVIEW, INT32, STR, STRVIEW, VOID, CallableType, FunctionInfo,
    NoneType,
    FunctionLinkage,
    NominalType, OptionalType, OwnType, PtrType, ReadonlyType,
    RecursiveAliasInstanceType, RefType, SendType, SyncType, TpyType,
    TupleType, TypeParamRef, UnionType, make_span,
)
from .value_category import (
    AsyncReturnForm, ValueCategoryAnalyzer, async_return_form,
    call_returns_cpp_ref, is_rvalue_source, origin_new, origin_open,
    place_dies,
)


CELL = NominalType("Cell")
OTHER = NominalType("Other")
GENERIC = TypeParamRef("T")
PROTOCOL = NominalType("Readable", is_protocol=True)
RECURSIVE = RecursiveAliasInstanceType("example.Tree", (INT32,))
STORAGE = AsyncReturnForm.STORAGE
BORROW = AsyncReturnForm.BORROW
TRAIT = AsyncReturnForm.TRAIT
TUPLE = AsyncReturnForm.TUPLE


@pytest.fixture(autouse=True)
def _recursive_alias_render_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared unit context lacks this compiler-owned name map.
    monkeypatch.setattr(get_current_compiler(), "recursive_alias_cpp_names", {},
                        raising=False)


@pytest.mark.parametrize("result,call_ref,payload,erased", [
    pytest.param(CELL, True, BORROW, "Cell", id="reference"),
    pytest.param(INT32, False, STORAGE, "int32_t", id="value"),
    # A void result (a builtin `__init__`) is no reference.
    pytest.param(VOID, False, STORAGE, "void", id="void"),
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
    pytest.param(TupleType((CELL,)), False, TUPLE, "std::tuple<Cell>",
                 id="singleton-tuple"),
    pytest.param(TupleType((CELL, INT32)), False, TUPLE,
                 "std::tuple<Cell, int32_t>", id="mixed-tuple"),
    pytest.param(TupleType((RefType(CELL),)), False, TUPLE,
                 "std::tuple<Cell&>", id="tuple-preserves-raw-ref"),
    pytest.param(TupleType((GENERIC, INT32)), False, TUPLE,
                 "std::tuple<T, int32_t>", id="generic-tuple"),
    # An owned element, or one with a form of its own beside a reference,
    # keeps the storage tuple.
    pytest.param(TupleType((OwnType(CELL), INT32)), False, STORAGE,
                 "std::tuple<Cell, int32_t>", id="owned-element-tuple"),
    pytest.param(TupleType((CELL, OptionalType(INT32))), False, STORAGE,
                 "std::tuple<Cell, std::optional<int32_t>>",
                 id="optional-beside-reference-tuple"),
    pytest.param(TupleType((INT32, INT32)), False, STORAGE,
                 "std::tuple<int32_t, int32_t>", id="value-tuple"),
    pytest.param(ReadonlyType(CELL), True, BORROW, "Cell", id="readonly"),
    # Qualifier order is observable today; erasure must not normalize it away.
    pytest.param(ReadonlyType(GENERIC), False, TRAIT, "T", id="readonly-generic"),
    # Readonly is access, never shape: the readonly Optional / union returns
    # by value like its mutable twin.
    pytest.param(ReadonlyType(OptionalType(CELL)), False, BORROW,
                 "std::optional<Cell>", id="readonly-optional"),
    pytest.param(ReadonlyType(UnionType((CELL, OTHER))), False, STORAGE,
                 "::tpy::Union<Cell, Other>", id="readonly-union"),
    pytest.param(RefType(ReadonlyType(GENERIC)), False, TRAIT, "T&",
                 id="ref-readonly-generic"),
    pytest.param(ReadonlyType(RefType(GENERIC)), False, BORROW,
                 "::tpy::val_or_ref_t<T>", id="readonly-ref-generic"),
    pytest.param(SendType(SyncType(RefType(GENERIC))), False, TRAIT,
                 "::tpy::val_or_ref_t<T>", id="markers-outside-ref"),
    pytest.param(RefType(SendType(GENERIC)), False, BORROW, "T&",
                 id="marker-inside-ref"),
    pytest.param(SyncType(ReadonlyType(GENERIC)), False, TRAIT, "T",
                 id="marker-outside-readonly"),
    pytest.param(ReadonlyType(SyncType(GENERIC)), False, BORROW, "T",
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
                 True, STORAGE, "Dynamic", id="dynamic-protocol"),
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


class _WrapperUnion(UnionType):
    # A recursive alias's union; the registry that answers `wrapper_info`
    # is absent from the unit context.
    def needs_wrapper(self) -> bool:
        return True


DYNAMIC = NominalType("Dynamic", is_protocol=True, is_dynamic_protocol=True)
ITERATOR = NominalType("Iterator", type_args=(INT32,), is_protocol=True,
                       _module_qname="typing.Iterator")


def _own_element_instance() -> FunctionInfo:
    # `list[Own[Cell]].__getitem__`: the declared `Ref[T]` substituted at
    # `T = Own[Cell]`.
    root = FunctionInfo(name="__getitem__", params=[],
                        return_type=RefType(GENERIC), is_method=True)
    return FunctionInfo(name="__getitem__", params=[],
                        return_type=OwnType(CELL), is_method=True,
                        canonical_fi=root)


@pytest.mark.parametrize("fi,expected", [
    # A void result -- a builtin `__init__` -- is no reference.
    pytest.param(FunctionInfo(name="__init__", params=[], return_type=VOID,
                              is_method=True), False, id="void-init"),
    pytest.param(FunctionInfo(name="f", params=[], return_type=NoneType()),
                 False, id="none"),
    # An open `T` in a generic body, under any wrapper.
    pytest.param(FunctionInfo(name="get", params=[],
                              return_type=ReadonlyType(GENERIC)),
                 False, id="readonly-generic"),
    pytest.param(FunctionInfo(name="get", params=[],
                              return_type=RefType(GENERIC)),
                 False, id="ref-generic"),
    pytest.param(FunctionInfo(name="get", params=[], return_type=GENERIC),
                 False, id="generic"),
    # ... except `Ref[T]` at `T = Own[X]`: still the stored `X&`.
    pytest.param(_own_element_instance(), True, id="ref-at-own-element"),
    # A `@dynamic` result is its base by reference, from an accessor;
    # a frame factory hands back its frame by value.
    pytest.param(FunctionInfo(name="get", params=[], return_type=DYNAMIC,
                              is_method=True), True, id="dynamic-accessor"),
    pytest.param(FunctionInfo(name="get", params=[],
                              return_type=ReadonlyType(DYNAMIC)),
                 True, id="readonly-dynamic"),
    pytest.param(FunctionInfo(name="coro", params=[], return_type=DYNAMIC,
                              is_async=True), False, id="async-frame"),
    pytest.param(FunctionInfo(name="gen", params=[], return_type=DYNAMIC,
                              is_generator=True),
                 False, id="generator-frame"),
    pytest.param(FunctionInfo(name="make", params=[],
                              return_type=OwnType(DYNAMIC)),
                 False, id="owned-dynamic"),
    # A recursive wrapper union is `Tree&`; a plain union is a value.
    pytest.param(FunctionInfo(name="get", params=[],
                              return_type=_WrapperUnion((CELL, INT32))),
                 True, id="wrapper-union"),
    pytest.param(FunctionInfo(name="get", params=[],
                              return_type=UnionType((CELL, INT32))),
                 False, id="plain-union"),
    # An iterator is a value.
    pytest.param(FunctionInfo(name="it", params=[], return_type=ITERATOR),
                 False, id="iterator"),
    # A ctor and an undeclared free `@native` hand back storage.
    pytest.param(FunctionInfo(name="Cell", params=[], return_type=CELL,
                              is_constructor=True), False, id="ctor"),
    pytest.param(FunctionInfo(name="get", params=[], return_type=CELL,
                              linkage=FunctionLinkage.NATIVE),
                 False, id="undeclared-free-native"),
])
def test_sync_call_convention_rows(fi: FunctionInfo, expected: bool) -> None:
    assert call_returns_cpp_ref(cast(ValueCategoryAnalyzer, None), fi) is expected


_OPEN_LOCAL_SRC = """\
from tpy import int32, readonly
from tplib import Box


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def peek[T](b: readonly[Box[T]]) -> int32:
    # `val_or_cref_t<T>`: a reference at `T = Item`
    x = b.get()
    return 1


def main() -> None:
    print(peek(Box(Item(4))))
"""


def test_open_t_call_result_local_is_never_a_copy() -> None:
    # `readonly[T]` reads as no reference in the generic body, yet the
    # result is `val_or_cref_t<T>`: a `T x = b.get();` slot would copy the
    # object at a reference instantiation, so the local stays refused.
    err, _reasons = _strict_reject(_OPEN_LOCAL_SRC)
    assert err.message.endswith("(stmt.var_decl:decl.slot_type)"), \
        err.message


_SITE_SRC = """\
from typing import Iterable, Protocol, cast
from tpy import int32, readonly, dynamic, ValueType, ComparableRef
from tplib import Box


@dynamic
class Pet(Protocol):
    def val(self) -> int32: ...


class Coord(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def gen_id[T](x: T) -> T:
    return x


def noop() -> None:
    pass


def casts(p: P, c: Coord, s: str, b: bytes, n: int32, q: P, f: bool) -> None:
    print(cast(P, p).n)
    print(cast(Coord, c).x)
    print(cast(str, s))
    print(cast(bytes, b))
    print(cast(int32, n))
    print((cast(P, p) if f else q).n)
    print(gen_id(cast(P, p)).n)
    print(((p, 1) if f else (P(1), 1))[1])
    print((noop(), 1))


def generic_get[T](b: readonly[Box[T]]) -> int32:
    print(b.get())
    print(gen_id(b.get()))
    return 1


def generic_max[T: ComparableRef](xs: Iterable[T]) -> int32:
    print(max(xs))
    return 1


def dyn_get(b: Box[Pet]) -> int32:
    return b.get().val()
"""


@functools.cache
def _site_exprs() -> dict[tuple[int, str], list]:
    _compiler, modules = _compile(_SITE_SRC)
    entry = _entry(modules)
    found: dict[tuple[int, str], list] = {}

    def visit(e: object) -> bool:
        loc = getattr(e, "loc", None)
        if loc is not None:
            found.setdefault((loc.line, type(e).__name__), []).append(e)
        return True

    for f in entry.ast.functions:
        if isinstance(f, TpyFunction):
            walk_body_stmts(f.body, lambda n: walk_expr_tree(n, visit),
                            lambda _s: None)
    return found | {(0, "analyzer"): [entry.analyzer]}


_Exprs = dict[str, list[TpyExpr]]
_Read = Callable[[ValueCategoryAnalyzer, _Exprs], bool]


def _named(es: _Exprs, name: str) -> TpyCall:
    return next(c for c in es["TpyCall"]
                if isinstance(c, TpyCall) and c.maybe_func_name == name)


def _rvalue(kind: str, index: int = 0) -> _Read:
    return lambda an, es: is_rvalue_source(an, es[kind][index])


def _rvalue_call(name: str) -> _Read:
    return lambda an, es: is_rvalue_source(an, _named(es, name))


# One row per reader site the suite never reaches, with the input as the
# site receives it and its answer. A `cast(T, x)` is its argument (an
# lvalue); a generic body's `readonly[T]` result is no reference; a
# `@dynamic` accessor's is; a void call is no reference.
@pytest.mark.parametrize("line,kind,read,expected", [
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._record_borrow_call_arg-cast-record"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="predicates._record_rvalue_temp_slot-cast-record"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._pre_generic_slot_family-record-cast"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._union_ctor_temp_arg-cast-record"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._tparam_slot_temp_arg-cast-record"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="predicates._optional_ptr_arg_face-ctor-cast-record"),
    pytest.param(32, "TpyCall", _rvalue_call("cast"), False,
                 id="predicates._own_borrow_call_temp_slot-cast-record"),
    pytest.param(33, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._value_record_rvalue_arg-cast-value-record"),
    pytest.param(34, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._str_owned_slot_arg-cast-str"),
    pytest.param(34, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._pre_generic_slot_family-viewfam-cast"),
    pytest.param(35, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._bytes_owned_call_rvalue_arg-cast-bytes"),
    pytest.param(36, "TpyCall", _rvalue_call("cast"), False,
                 id="checks._pre_generic_slot_family-scalar-cast"),
    pytest.param(37, "TpyIfExpr", lambda an, es: is_rvalue_source(
        an, es["TpyIfExpr"][0].then_expr), False,
                 id="select-arm-cast (expressions/frame/predicates)"),
    pytest.param(37, "TpyIfExpr", _rvalue("TpyIfExpr", 0), False,
                 id="select-of-cast-and-name-is-a-place"),
    pytest.param(39, "TpyIfExpr", lambda an, es: origin_new(
        an, es["TpyIfExpr"][0]), False,
                 id="origin-select-arms-members"),
    pytest.param(39, "TpyIfExpr", lambda an, es: origin_new(
        an, es["TpyIfExpr"][0].else_expr), True,
                 id="origin_new-fresh-literal-arm"),
    pytest.param(40, "TpyCall", lambda an, es: is_rvalue_source(
        an, _named(es, "noop")), True,
                 id="statements._lower_print_arg-void-element"),
    pytest.param(44, "TpyMethodCall", _rvalue("TpyMethodCall"), True,
                 id="checks._own_tparam_call_rvalue_arg-readonly-T"),
    pytest.param(44, "TpyMethodCall", _rvalue("TpyMethodCall"), True,
                 id="predicates._own_borrow_call_temp_slot-readonly-T"),
    pytest.param(44, "TpyMethodCall", lambda an, es: origin_open(
        an, es["TpyMethodCall"][0]), True,
                 id="origin_open-readonly-T-result"),
    # The open-T recursion reaches the `readonly[T]` operand's own operands
    # (`b`), so that operand's answer never decides it.
    pytest.param(45, "TpyCall", lambda an, es: place_dies(
        an, _named(es, "gen_id")), False,
                 id="place_dies-open-t-operand-readonly-T"),
    pytest.param(50, "TpyCall", lambda an, es: origin_open(
        an, _named(es, "max")), False,
                 id="origin_open-stamped-copy"),
    pytest.param(55, "TpyMethodCall", lambda an, es: is_rvalue_source(
        an, es["TpyMethodCall"][-1]), False,
                 id="predicates._optional_ptr_arg_face-dynamic-accessor"),
])
def test_unreached_site_input_answers(line: int, kind: str, read: _Read,
                                      expected: bool) -> None:
    found = _site_exprs()
    an = found[(0, "analyzer")][0]
    es = {k: v for (ln, k), v in found.items() if ln == line}
    assert kind in es, (line, sorted(es))
    assert read(an, es) is expected

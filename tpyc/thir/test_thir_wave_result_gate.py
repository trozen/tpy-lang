"""Call-RESULT gate rows + ctor-arg rows from the expr.call track's second
grind: template/native/er record rvalues at their witnessed positions, the
ctor gate's copy/callable/instantiation rows, the open-T element copy, and
the borrow-returning lambda body. Boundaries: unwitnessed positions keep
falling back (byte-identical via AST).
"""

from __future__ import annotations

import io

from ..codegen_cpp.context import CodeGenOptions
from .emit import emit_thir_body
from .testutil import (
    _reject_tally, _lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_POINT = (
    "from tpy import Int32, make_default\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.x = 0\n"
)


class TestTemplateRecordRvalue:
    def test_storage_decl_routes(self):
        src = _POINT + (
            "def f() -> None:\n"
            "    p = make_default[Point]()\n"
            "    print(p.x)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Point p = Point{};" in body
        assert faces["call.template_record_rvalue"] >= 1
        _assert_byte_identical(src)

    def test_value_arg_position_stays_ast(self):
        # The row is STORAGE-only; a template record rvalue at a VALUE arg
        # slot is unwitnessed and must fall back.
        src = _POINT + (
            "def show(p: Point) -> None:\n"
            "    print(p.x)\n"
            "def f() -> None:\n"
            "    show(make_default[Point]())\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.record_f1")


_ER = (
    "from tpy import Int32, error_return, ReturnException, Own\n"
    "class E(Exception, ReturnException):\n"
    "    pass\n"
    "class Data:\n"
    "    value: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.value = v\n"
    "@error_return(E)\n"
    "def make_data(v: Int32) -> Own[Data]:\n"
    "    if v < 0:\n"
    "        raise E()\n"
    "    return Data(v)\n"
)


class TestErRecordRvalue:
    def test_field_receiver_composes_on_the_unwrap(self):
        src = _ER + (
            "@error_return(E)\n"
            "def get_value(v: Int32) -> Int32:\n"
            "    return make_data(v).value\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "get_value")
        assert "auto __er_1 = make_data(v);" in body
        assert "}).value" in body
        assert faces["call.er_record_rvalue"] >= 1
        _assert_byte_identical(src)

    def test_print_arg_stays_ast(self):
        # An er record call at a plain VALUE position (print arg's field
        # would be receiver, so use a bare arg slot): unwitnessed -> AST.
        src = _ER + (
            "def show(d: Data) -> None:\n"
            "    print(d.value)\n"
            "def f() -> None:\n"
            "    try:\n"
            "        show(make_data(2))\n"
            "    except E:\n"
            "        print(\"err\")\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.error_return")


class TestCtorArgRows:
    def test_copy_record_into_own_slot(self):
        src = (
            "from tpy import Int32, Own, copy\n"
            "class Box:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32) -> None:\n"
            "        self.value = value\n"
            "class Holder:\n"
            "    item: Box\n"
            "    def __init__(self, item: Own[Box]) -> None:\n"
            "        self.item = item\n"
            "def f(b: Box) -> Int32:\n"
            "    h = Holder(copy(b))\n"
            "    return h.item.value\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Holder(Box(b))" in body
        assert faces["own.copy_construct"] >= 1
        _assert_byte_identical(src)

    def test_func_ref_into_callable_ctor_slot(self):
        src = (
            "from tpy import Int32\n"
            "from typing import Callable\n"
            "def double(x: Int32) -> Int32:\n"
            "    return x * 2\n"
            "class Handler:\n"
            "    cb: Callable[[Int32], Int32]\n"
            "    def __init__(self, cb: Callable[[Int32], Int32]) -> None:\n"
            "        self.cb = cb\n"
            "def f() -> None:\n"
            "    h = Handler(double)\n"
            "    print(h.cb(10))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Handler(double_)" in body
        assert faces["name.func_ref"] >= 1
        _assert_byte_identical(src)

    def test_open_t_elem_copy_into_own_slot(self):
        src = (
            "from tpy import Int32, Own, copy\n"
            "class Owned[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: Own[T]) -> None:\n"
            "        self.v = v\n"
            "def grab[T](src: list[T]) -> Own[Owned[T]]:\n"
            "    o = Owned(copy(src[0]))\n"
            "    return o\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "grab")
        assert "Owned<T>(T(::tpy::__getitem__(src, 0)))" in body
        assert faces["ctor.copy_open_elem"] >= 1
        _assert_byte_identical(src)

    def test_concrete_elem_copy_subscript_routes(self):
        # The open-T row requires BOTH sides TypeParamRef; a CONCRETE record
        # element copy over the same subscript takes the shared
        # copy-construct row beside it (`Box(__getitem__(xs, 0))`).
        src = (
            "from tpy import Int32, Own, copy\n"
            "class Box:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32) -> None:\n"
            "        self.value = value\n"
            "class Holder:\n"
            "    item: Box\n"
            "    def __init__(self, item: Own[Box]) -> None:\n"
            "        self.item = item\n"
            "def f(xs: list[Box]) -> Int32:\n"
            "    h = Holder(copy(xs[0]))\n"
            "    return h.item.value\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["own.copy_construct"] >= 1
        assert ("Holder h = Holder(Box(::tpy::__getitem__(xs, 0)));"
                in _body(thir, "f"))

    def test_default_factory_instantiation(self):
        src = (
            "import dataclasses\n"
            "from tpy import Int32\n"
            "@dataclasses.dataclass\n"
            "class Foo:\n"
            "    items: list[Int32] = dataclasses.field("
            "default_factory=list)\n"
            "    x: Int32 = dataclasses.field(default=42)\n"
            "def f() -> None:\n"
            "    foo = Foo(x=Int32(1))\n"
            "    print(foo.x)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Foo(std::vector<int32_t>(), 1)" in body
        assert faces["ctor.own_container_instantiation"] >= 1
        _assert_byte_identical(src)


class TestCallableFieldArg:
    def test_callable_field_binds_fn_slot_bare(self):
        src = (
            "from tpy import Int32, Fn\n"
            "from typing import Callable\n"
            "def double(x: Int32) -> Int32:\n"
            "    return x * 2\n"
            "class Handler:\n"
            "    cb: Callable[[Int32], Int32]\n"
            "    def __init__(self, cb: Callable[[Int32], Int32]) -> None:\n"
            "        self.cb = cb\n"
            "def apply(fn: Fn[[Int32], Int32], v: Int32) -> Int32:\n"
            "    return fn(v)\n"
            "def use() -> None:\n"
            "    handler = Handler(double)\n"
            "    print(apply(handler.cb, 10))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "use")
        assert "apply(handler.cb, 10)" in body
        assert faces["call.callable_field_arg"] >= 1
        assert faces["field.callable_value"] >= 1
        _assert_byte_identical(src)


class TestDiscardNativeRecord:
    def test_discarded_open_routes(self):
        src = (
            "def f() -> None:\n"
            "    try:\n"
            "        open(\"definitely_missing_xyz.txt\")\n"
            "    except FileNotFoundError:\n"
            "        print(\"missing\")\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::builtin_open(\"definitely_missing_xyz.txt\");" in body
        assert faces["call.discard_native_record"] >= 1
        _assert_byte_identical(src)

    def test_discarded_plain_record_call_routes_bare(self):
        # The PLAIN sibling was already admitted (the record-rvalue row is
        # position-blind) and renders the same bare call statement -- the
        # new row only added the @native kind. Pinned as routed.
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def make(v: Int32) -> Own[Box]:\n"
            "    return Box(v)\n"
            "def f() -> None:\n"
            "    make(3)\n"
        )
        thir = _lower_ctx(src)
        body = _body(thir, "f")
        assert "make(3);" in body
        _assert_byte_identical(src)


class TestIterRvalueInstArgs:
    def test_copy_iter_combinator_routes(self):
        src = (
            "from tpy import Int32, copy_iter\n"
            "class Named:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "def identity(n: Named) -> Named:\n"
            "    return n\n"
            "def f(src: list[Named]) -> None:\n"
            "    result = list(copy_iter(map(identity, src)))\n"
            "    print(len(result))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::copy_iter<Named>(::tpy::builtin_map<" in body
        assert faces["call.copy_iter_explicit"] >= 1
        _assert_byte_identical(src)

    def test_qualified_generic_generator_routes(self):
        src = (
            "import heapq\n"
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    a: list[Int32] = [1, 3]\n"
            "    b: list[Int32] = [2, 4]\n"
            "    merged: list[Int32] = list(heapq.merge(a, b))\n"
            "    print(len(merged))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpystd::heapq::merge<int32_t>(::tpy::varargs<" in body
        assert faces["call.inst_gen_arg"] >= 1
        _assert_byte_identical(src)

    # No boundary pins for two defensive rejects that valid source cannot
    # reach: the static-generator marker spelling (the parser forbids
    # @staticmethod generators outright) and the copy_iter
    # unresolvable-element fallback (sema rejects non-iterable args, and
    # every sema-accepted iterable resolves an element type).


class TestCopyFaces:
    def test_copy_of_open_t_call_rvalue(self):
        src = (
            "from tpy import Fn, Own, copy\n"
            "def apply[U](fn: Fn[[U], U], init: U) -> Own[U]:\n"
            "    return copy(fn(init))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "apply")
        assert "return U(fn(init));" in body
        assert faces["call.copy_tparam"] >= 1
        _assert_byte_identical(src)

    def test_copy_of_open_t_container_element_routes(self):
        # The heapq/random shift shape: `copy(xs[i])` off an open-T
        # container, the generic tail over a subscript read.
        src = (
            "from tpy import Int32, Comparable, copy\n"
            "def sift[T: Comparable](heap: list[T], pos: Int32) -> None:\n"
            "    newitem: T = copy(heap[pos])\n"
            "    heap[pos] = newitem\n"
            "def main() -> None:\n"
            "    xs = [3, 1, 2]\n"
            "    sift(xs, 0)\n"
            "    print(xs[0])\n"
            "main()\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert ("T newitem = T(::tpy::__getitem__(heap, pos));"
                in _body(thir, "sift"))
        assert faces["call.copy_tparam_elem"] >= 1
        _assert_routes_byte_identical(src)

    def test_copy_of_open_t_element_receiver_shapes(self):
        # The receivers the row admits past a plain list name: a dict, a
        # nested container, and a tuple element (read through the
        # `val_or_ptr_t<T>` traits, never as a bare pointer).
        src = (
            "from tpy import Int32, Comparable, copy\n"
            "def d[T: Comparable](m: dict[Int32, T], k: Int32) -> None:\n"
            "    m[k] = copy(m[k])\n"
            "def n[T: Comparable](rows: list[list[T]], i: Int32) -> None:\n"
            "    rows[i][0] = copy(rows[i][0])\n"
            "def t[T: Comparable](pair: tuple[T, Int32]) -> Int32:\n"
            "    first: T = copy(pair[0])\n"
            "    return pair[1]\n"
            "def main() -> None:\n"
            "    m = {1: 2}\n"
            "    d(m, 1)\n"
            "    rows = [[3], [4]]\n"
            "    n(rows, 0)\n"
            "    pair = (5, 6)\n"
            "    print(t(pair))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_copy_of_str_element_stays_ast(self):
        # Same syntactic source, a CONCRETE payload: the str arm takes
        # NAME sources only, so widening the open-T row must not open the
        # subscript source for every payload family.
        src = (
            "from tpy import copy\n"
            "def f() -> None:\n"
            "    xs = [\"a\", \"b\"]\n"
            "    v = copy(xs[0])\n"
            "    print(v)\n"
            "f()\n"
        )
        _assert_rejects_at(_reject_tally(src), 'body:expr.call', 'call.copy_source.str')

    def test_copy_of_span_name(self):
        src = (
            "from tpy import Int32, Span, copy\n"
            "def f(sp: Span[Int32]) -> None:\n"
            "    sp2: Span[Int32] = copy(sp)\n"
            "    print(len(sp2))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "std::span<int32_t>(sp)" in body
        assert faces["call.copy_span"] >= 1
        _assert_byte_identical(src)

    def test_copy_of_narrowed_span_stays_ast(self):
        # The face excludes narrowed names (their read renames to the
        # extraction alias) -- the guard's boundary.
        src = (
            "from tpy import Int32, Span, copy\n"
            "def f(sp: Span[Int32] | None) -> None:\n"
            "    if sp is not None:\n"
            "        sp2: Span[Int32] = copy(sp)\n"
            "        print(len(sp2))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:name.optspan_narrowed_read")


class TestCallableShadowGate:
    def test_param_shadowing_module_fn_stays_ast(self):
        # A callable param invoked under a name that also names a module
        # function: the AST's registry-first emit renders the MODULE
        # function's call with zip-truncated args (BUGS.md) -- a broken
        # oracle, so THIR must gate the composition, not mirror or fix it.
        src = (
            "from tpy import Int32, Fn\n"
            "def f(x: Int32) -> Int32:\n"
            "    return x * 2\n"
            "def apply(f: Fn[[Int32], Int32], v: Int32) -> Int32:\n"
            "    return f(v)\n"
            "def use() -> None:\n"
            "    print(apply(f, 10))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.callable_shadow")


class TestBorrowLambdaBody:
    def test_borrow_returning_call_body_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def scale(p: Point, factor: Int32) -> Point:\n"
            "    p.x *= factor\n"
            "    return p\n"
            "def f(pts: list[Point]) -> None:\n"
            "    for p in map(lambda p: scale(p, 2), pts):\n"
            "        pass\n"
        )
        thir = _lower_ctx(src)
        body = _body(thir, "f")
        assert "-> Point& { return scale(p, 2); }" in body
        _assert_byte_identical(src)

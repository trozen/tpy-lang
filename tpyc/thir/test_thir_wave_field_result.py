"""Field RESULT-gate rows from the field.result_type grind: the str-family
result row over the ladder's other admitted receivers (record-element
subscript, deref_check Optional pointer), and the callable-field call's
container-FIELD arg precheck. Boundaries: an unadmitted receiver shape and
an unprechecked container arg keep falling back (byte-identical via AST).
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (
    _assert_rejects_at,
    _reject_tally, _lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_NAMED = (
    "class Named:\n"
    "    name: str\n"
    "    def __init__(self, n: str) -> None:\n"
    "        self.name = n\n"
)


class TestStrFieldReceivers:
    def test_subscript_receiver_routes(self):
        src = _NAMED + (
            "def f() -> None:\n"
            "    xs = [Named(\"a\"), Named(\"b\")]\n"
            "    print(xs[0].name)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::__getitem__(xs, 0).name" in body
        assert faces["fstr.str_field"] >= 1
        _assert_byte_identical(src)

    def test_deref_check_receiver_routes(self):
        src = _NAMED + (
            "def f(p: Named | None) -> None:\n"
            "    print(p.name)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::deref_check(p).name" in body
        assert faces["fstr.str_field"] >= 1
        _assert_byte_identical(src)

    def test_ternary_receiver_stays_ast(self):
        # The result row only tags the str form; a receiver shape the
        # ladder below never admits (a ternary) must still fall back.
        src = _NAMED + (
            "def f(a: Named, b: Named, c: bool) -> None:\n"
            "    print((a if c else b).name)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:field.receiver_shape")


_HOLDER = (
    "from typing import Callable\n"
    "from tpy import Int32\n"
    "class Holder:\n"
    "    data: list[Int32]\n"
    "    cb: Callable[[list[Int32]], None]\n"
    "    def __init__(self, cb: Callable[[list[Int32]], None]) -> None:\n"
    "        self.data = [0]\n"
    "        self.cb = cb\n"
)


class TestCallableFieldContainerArg:
    def test_container_field_arg_routes(self):
        src = _HOLDER + (
            "    def poke(self) -> None:\n"
            "        self.cb(self.data)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "poke")
        assert "(*this).cb(this->data)" in body
        assert faces["cfield.container_arg"] >= 1
        assert faces["method.callable_field"] >= 1
        _assert_byte_identical(src)

    def test_chained_elem_write_routes(self):
        # `a.bs[0].as_[0].val = 30` -- each chain level renders the same
        # `__getitem__` / bare member nest, so the write target routes
        # through the recursive receiver admission.
        src = (
            "from __future__ import annotations\n"
            "from tpy import Int32\n"
            "class A:\n"
            "    bs: list[B]\n"
            "    def __init__(self) -> None:\n"
            "        self.bs = []\n"
            "class B:\n"
            "    val: Int32\n"
            "    as_: list[A]\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.val = v\n"
            "        self.as_ = []\n"
            "def f(a: A) -> None:\n"
            "    a.bs[0].as_[0].bs[0].val = 30\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert ("::tpy::__getitem__(::tpy::__getitem__("
                "::tpy::__getitem__(a.bs, 0).as_, 0).bs, 0).val = 30;"
                in body)
        _assert_byte_identical(src)

    def test_varargs_record_elem_write_routes(self):
        # `args[i].value = ...` on a `*args: Counter` param -- the varargs
        # body view is span-backed, so the record-element subscript receiver
        # renders like list's (the checked __getitem__ for an unproven
        # index).
        src = (
            "from tpy import Int32\n"
            "class Counter:\n"
            "    value: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.value = 0\n"
            "def f(*args: Counter) -> None:\n"
            "    args[0].value = 10\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::__getitem__(args, 0).value = 10;" in body
        _assert_byte_identical(src)

    def test_optional_container_field_move_write_routes(self):
        # `h.s = initial` at a `set[T] | None` field -- the value-repr
        # optional absorbs the same moved-name render the plain container
        # field gets; no ptr_to_optional lift.
        src = (
            "from tpy import Int32\n"
            "class H:\n"
            "    s: set[Int32] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.s = None\n"
            "def f() -> None:\n"
            "    xs = {1, 2}\n"
            "    h = H()\n"
            "    h.s = xs\n"
            "    print(h.s is not None)\n"
            "f()\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "h.s = std::move(xs);" in body
        _assert_byte_identical(src)

    def test_varargs_optional_elem_routes_opt_to_ptr(self):
        # A varargs of Optional records: `c = args[0]` lifts the
        # storage-form span element via optional_to_ptr (the
        # container-subscript decl row); the pointer None-test and the
        # write through the lifted pointer follow.
        src = (
            "from tpy import Int32\n"
            "class Counter:\n"
            "    value: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.value = 0\n"
            "def f(*args: Counter | None) -> None:\n"
            "    c = args[0]\n"
            "    if c is not None:\n"
            "        c.value = 10\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert ("Counter* c = ::tpy::optional_to_ptr(::tpy::__getitem__("
                "args, 0));" in cpp[1] or
                "Counter* c = ::tpy::optional_to_ptr(::tpy::__getitem__("
                "args, 0));" in cpp[0])

    def test_optional_elem_chain_decl_routes_opt_to_ptr(self):
        # `b = a.bs[0]` off a field-receiver container with Optional
        # elements: the storage-form subscript lifts via optional_to_ptr
        # into the pointer-local decl; the None-test and the write through
        # the lifted pointer follow.
        src = (
            "from __future__ import annotations\n"
            "from tpy import Int32\n"
            "class B:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.val = v\n"
            "class A:\n"
            "    bs: list[B | None]\n"
            "    def __init__(self) -> None:\n"
            "        self.bs = []\n"
            "def f(a: A) -> None:\n"
            "    b = a.bs[0]\n"
            "    if b is not None:\n"
            "        b.val = 30\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert ("B* b = ::tpy::optional_to_ptr(::tpy::__getitem__(a.bs, 0));"
                in cpp[1])

    def test_optional_container_field_copy_write_routes(self):
        # The non-move sibling: the source name stays live after the
        # assignment, so the value-repr optional absorbs the bare copy.
        src = (
            "from tpy import Int32\n"
            "class H:\n"
            "    s: set[Int32] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.s = None\n"
            "def f() -> None:\n"
            "    xs = {1, 2}\n"
            "    h = H()\n"
            "    h.s = xs\n"
            "    xs.add(3)\n"
            "    print(len(xs))\n"
            "f()\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "h.s = xs;" in body
        _assert_byte_identical(src)

    def test_optional_array_field_param_write_routes(self):
        # An Optional[Array] field write from a param NAME copies bare
        # (`h.a = xs;`) -- the family set of the gate and the tail agree,
        # so the ptr_to_optional lift never fires for value-repr
        # Optional[container/Array] fields.
        src = (
            "from tpy import Int32, Array\n"
            "class H:\n"
            "    a: Array[Int32, 3] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.a = None\n"
            "def f(xs: Array[Int32, 3]) -> None:\n"
            "    h = H()\n"
            "    h.a = xs\n"
            "    print(h.a is not None)\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "h.a = xs;" in body
        _assert_byte_identical(src)

    def test_optional_tuple_field_literal_write_routes(self):
        # `s.auth = ("u", "p")` at a `tuple[str, str] | None` field -- the
        # value-repr optional absorbs the spelled brace-init.
        src = (
            "class S:\n"
            "    auth: tuple[str, str] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.auth = None\n"
            "def f() -> None:\n"
            "    s = S()\n"
            '    s.auth = ("user", "pw")\n'
            "    print(s.auth is not None)\n"
            "f()\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert ('s.auth = std::tuple<std::string, std::string>'
                '{"user", "pw"};' in body)
        _assert_byte_identical(src)

    def test_ptr_local_record_copy_write_routes(self):
        # `self.result = saved` where saved is a pointer-local: the copy
        # reads through the deref, never a move.
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class W:\n"
            "    result: Point\n"
            "    def __init__(self) -> None:\n"
            "        self.result = Point(0)\n"
            "    def run(self) -> None:\n"
            "        saved = Point(0)\n"
            "        for i in range(2):\n"
            "            p = Point(i)\n"
            "            saved = p\n"
            "        self.result = saved\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "run")
        assert "this->result = (*saved);" in body
        assert faces["field_write.ptr_local_copy"] >= 1
        _assert_byte_identical(src)

    def test_record_copy_sink_sources_route(self):
        # The warned record-copy family at COPY sinks: a borrow-returning
        # call at the field write and the checked setitem, a field-read
        # source, and a record-element subscript source -- all render bare
        # (the sink's copy-assign absorbs the reference).
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: Point\n"
            "    def __init__(self, p: Point) -> None:\n"
            "        self.p = p\n"
            "def identity(p: Point) -> Point:\n"
            "    return p\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    pts = [Point(2), Point(3)]\n"
            "    h = Holder(pt)\n"
            "    h2 = Holder(pt)\n"
            "    h.p = identity(pt)\n"
            "    pts[0] = identity(pts[1])\n"
            "    h.p = h2.p\n"
            "    h.p = pts[0]\n"
            "    print(h.p.x)\n"
            "main()\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "main")
        assert "h.p = identity(pt);" in body
        assert "::tpy::__setitem__(pts, 0, identity(" in body
        assert "h.p = h2.p;" in body
        assert "h.p = ::tpy::__getitem__(pts, 0);" in body
        assert faces["call.field_copy_borrow_ret"] >= 1
        assert faces["field_write.borrow_call_copy"] >= 1
        assert faces["setitem.borrow_call_copy"] >= 1
        assert faces["field_write.field_copy"] >= 1
        assert faces["field_write.subscript_copy"] >= 1
        _assert_byte_identical(src)

    def test_optional_ref_tuple_field_literal_stays_ast(self):
        # The Optional unwrap admits VALUE tuples only; a ref-element
        # (record) tuple field behind Optional keeps rejecting.
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class H:\n"
            "    pair: tuple[Point, Point] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.pair = None\n"
            "def f(a: Point, b: Point) -> None:\n"
            "    h = H()\n"
            "    h.pair = (a, b)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_narrowed_source_field_write_stays_ast(self):
        # A NARROWED Optional source is excluded from both the ptr-local
        # and plain-name rows (its read composes the narrowing deref).
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: Point\n"
            "    def __init__(self, p: Point) -> None:\n"
            "        self.p = p\n"
            "def f(h: Holder, o: Point | None) -> None:\n"
            "    if o is not None:\n"
            "        h.p = o\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_setitem_covariant_borrow_call_stays_ast(self):
        # The setitem borrow-call row requires EXACT element-type match
        # (stricter than its rvalue sibling); a covariant subclass result
        # keeps rejecting.
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Derived(Point):\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        super().__init__(x)\n"
            "def pick(d: Derived) -> Derived:\n"
            "    return d\n"
            "def f(d: Derived) -> None:\n"
            "    pts = [Point(1)]\n"
            "    pts[0] = pick(d)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.record_value_shape")

    def test_chained_field_copy_source_stays_ast(self):
        # A field copy source off a FIELD receiver (`h.p = h2.inner.p`)
        # rejects at the copy row's `_field_receiver_ok` (name receivers
        # only) -- the deeper chain stays AST.
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Inner:\n"
            "    p: Point\n"
            "    def __init__(self, p: Point) -> None:\n"
            "        self.p = p\n"
            "class Holder:\n"
            "    p: Point\n"
            "    inner: Inner\n"
            "    def __init__(self, p: Point) -> None:\n"
            "        self.p = p\n"
            "        self.inner = Inner(p)\n"
            "def f(h: Holder, h2: Holder) -> None:\n"
            "    h.p = h2.inner.p\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_optional_container_family_sets_agree(self):
        # The gate (_container_name_field_write_ok's Optional unwrap) and
        # the render tail (val_opt_container) each spell the admitted
        # Optional[container] family set; this pin IS the equality guard --
        # if the sets ever diverge, the diverging family emits the
        # ptr_to_optional lift and the byte-diff fails here.
        for ann, mk in (("list[Int32]", "[1, 2]"),
                        ("dict[Int32, Int32]", "{1: 2}"),
                        ("set[Int32]", "{1, 2}"),
                        ("Array[Int32, 2]", "Array[Int32, 2]()")):
            src = (
                "from tpy import Int32, Array\n"
                "class H:\n"
                f"    c: {ann} | None\n"
                "    def __init__(self) -> None:\n"
                "        self.c = None\n"
                "def f() -> None:\n"
                f"    xs = {mk}\n"
                "    h = H()\n"
                "    h.c = xs\n"
                "    print(h.c is not None)\n"
                "f()\n"
            )
            thir, _ = _lower_ctx_witnessed(src)
            fn = _fn(thir, "f")
            if fn is not None:
                buf = io.StringIO()
                emit_thir_body(buf, fn)
                assert "ptr_to_optional" not in buf.getvalue(), ann

    def test_nested_borrow_call_arg_no_leak(self):
        # The record_copy_sink flag must not leak into the admitted call's
        # OWN arguments (recursive operands lower with the default use) --
        # the nested borrow-returning call as an argument keeps whatever
        # verdict the arg gate gives it, byte-identical either way.
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: Point\n"
            "    def __init__(self, p: Point) -> None:\n"
            "        self.p = p\n"
            "def identity(p: Point) -> Point:\n"
            "    return p\n"
            "def f(h: Holder, pt: Point) -> None:\n"
            "    h.p = identity(identity(pt))\n"
        )
        _assert_byte_identical(src)

    def test_borrow_call_decl_stays_ref_alias(self):
        # The same borrow-returning call at a DECL binds the reference
        # (`Point& q = identity(pt);`) -- the copy-sink admission must not
        # capture it (byte-identity would break loudly if it did).
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def identity(p: Point) -> Point:\n"
            "    return p\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    q = identity(pt)\n"
            "    print(q.x)\n"
            "main()\n"
        )
        _assert_byte_identical(src)

    def test_subscript_receiver_arg_stays_ast(self):
        # The precheck keys on _field_receiver_ok; a container field over a
        # SUBSCRIPT receiver is not prechecked, so the generic VALUE
        # position rejects it and the body falls back.
        src = _HOLDER + (
            "def f(hs: list[Holder]) -> None:\n"
            "    h = hs[0]\n"
            "    h.cb(hs[0].data)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:field.result_type")

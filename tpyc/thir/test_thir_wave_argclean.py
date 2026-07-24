"""Pins for the arg-ladder clean-tier arms: iterator-rvalue auto temps,
deref-coerce args, Optional[Own] moves, native-protocol value args,
readonly empty-container inline binds, void print-body lambdas, and
callable-field calls -- and the boundaries that must keep falling back."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = "from tpy import Int32, UInt64, readonly\n"


class TestIterRvalueAutoTemp:
    def test_gen_factory_arg_routes(self):
        src = _PRELUDE + (
            "from typing import Iterator, Iterable\n"
            "def gen(n: Int32) -> Iterator[Int32]:\n"
            "    i = 0\n"
            "    while i < n:\n"
            "        yield i\n"
            "        i = i + 1\n"
            "def total(it: Iterable[Int32]) -> Int32:\n"
            "    s = 0\n"
            "    for v in it:\n"
            "        s = s + v\n"
            "    return s\n"
            "def main() -> None:\n"
            "    print(total(gen(4)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.iter_proto"] >= 1
        _assert_byte_identical(src)

    def test_iter_call_arg_routes(self):
        src = _PRELUDE + (
            "from typing import Iterator\n"
            "class Pt:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def bump(it: Iterator[Pt]) -> None:\n"
            "    for p in it:\n"
            "        p.x = p.x + 1\n"
            "def main() -> None:\n"
            "    pts = [Pt(1), Pt(2)]\n"
            "    bump(iter(pts))\n"
            "    print(pts[0].x, pts[1].x)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.iter_proto"] >= 1
        _assert_byte_identical(src)

    def test_dict_view_arg_routes(self):
        src = _PRELUDE + (
            "from typing import Iterable\n"
            "def collect(it: Iterable[str]) -> Int32:\n"
            "    n = 0\n"
            "    for k in it:\n"
            "        n = n + 1\n"
            "    return n\n"
            "def main() -> None:\n"
            "    d = {\"a\": 1, \"b\": 2}\n"
            "    print(collect(d.keys()))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.iter_proto"] >= 1
        _assert_byte_identical(src)


class TestDerefCoerceArg:
    _REF = _PRELUDE + (
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Ref:\n"
        "    pt: Point\n"
        "    def __init__(self, pt: Point) -> None:\n"
        "        self.pt = pt\n"
        "    def __deref__(self) -> Point:\n"
        "        return self.pt\n"
        "def show(p: Point) -> None:\n"
        "    print(p.x)\n"
    )

    def test_wrapper_deref_hoists_slot_typed_temp(self):
        src = self._REF + (
            "def main() -> None:\n"
            "    r = Ref(Point(1))\n"
            "    show(r)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.deref_coerce"] >= 1
        _assert_byte_identical(src)

    def test_ptr_deref_renders_inline(self):
        src = self._REF + (
            "from tpy import Ptr\n"
            "def main() -> None:\n"
            "    pt = Point(3)\n"
            "    p: Ptr[Point] = pt\n"
            "    show(p)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.deref_coerce_inline"] >= 1
        _assert_byte_identical(src)


class TestOptOwnMoveArg:
    _BOX = _PRELUDE + (
        "from tplib import Box\n"
        "class Item:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "def sink(b: Box[Item] | None) -> None:\n"
        "    pass\n"
    )

    def test_last_use_moves_bare(self):
        # `Own[Box] | None` slot: the movable last use renders
        # `std::move(conn)` bare -- the optional's converting ctor.
        src = self._BOX.replace(
            "def sink(b: Box[Item] | None)",
            "from tpy import Own\ndef sink(b: Own[Box[Item]] | None)") + (
            "def main() -> None:\n"
            "    conn = Box(Item(1))\n"
            "    sink(conn)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["move.opt_own_last_use"] >= 1
        _assert_byte_identical(src)

    def test_non_last_use_stays_ast(self):
        # A NON-last-use (copyable) name into the Optional[Own] slot -- the
        # copy shape -- is unwitnessed; the body keeps falling back. (The
        # nocopy sibling is sema-unreachable: use-after-move errors.)
        src = _PRELUDE + (
            "from tpy import Own\n"
            "class Item:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def sink(b: Own[Item] | None) -> None:\n"
            "    pass\n"
            "def use(b: Item) -> None:\n"
            "    pass\n"
            "def main() -> None:\n"
            "    it = Item(1)\n"
            "    sink(it)\n"
            "    use(it)\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)


class TestNativeProtocolValueArg:
    def test_hash_literals_route(self):
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    h1: UInt64 = hash(\"hello\")\n"
            "    h2: UInt64 = hash(42)\n"
            "    h3: UInt64 = hash(3.14)\n"
            "    print(h1 == h1, h2 == h2, h3 == h3)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.native_protocol_value"] >= 3
        _assert_byte_identical(src)

    def test_hash_name_args_route(self):
        # Scalar / Char NAME args at the native protocol slot route too
        # (admitted by an earlier ladder row -- the pass-through name rows
        # -- so the value-arg face need not fire); pinned for routing +
        # byte-identity.
        src = _PRELUDE + (
            "from tpy import Char\n"
            "def main() -> None:\n"
            "    n = 7\n"
            "    c: Char = \"a\"\n"
            "    h1: UInt64 = hash(n)\n"
            "    h2: UInt64 = hash(c)\n"
            "    print(h1 == h1, h2 == h2)\n"
        )
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)


_CB = _PRELUDE + (
    "from typing import Callable\n"
    "def run(f: Callable[[Int32], None], v: Int32) -> None:\n"
    "    f(v)\n"
)


class TestVoidPrintLambda:
    def test_print_body_routes_witnessed(self):
        src = _CB + (
            "def main() -> None:\n"
            "    run(lambda x: print(x), 1)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["expr.lambda_void_print"] >= 1
        _assert_byte_identical(src)

    def test_captured_name_print_routes(self):
        src = _CB + (
            "def main() -> None:\n"
            "    k = 10\n"
            "    run(lambda x: print(x + k), 2)\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_non_print_void_body_stays_ast(self):
        # The void admission is print-IFF; a mutating statement body has no
        # mirrored render.
        src = _CB + (
            "def main() -> None:\n"
            "    xs: list[Int32] = []\n"
            "    run(lambda x: xs.append(x), 1)\n"
            "    print(len(xs))\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

    def test_print_kwargs_body_stays_ast(self):
        # sep=/end= inside the closure body stay AST (unwitnessed chain
        # variants).
        src = _CB + (
            "def main() -> None:\n"
            "    run(lambda x: print(x, end=\"\"), 5)\n"
            "    print(\"\")\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)


class TestCallableFieldCall:
    def test_bare_member_call_routes(self):
        src = _PRELUDE + (
            "from typing import Callable\n"
            "class Handler:\n"
            "    cb: Callable[[Int32], None]\n"
            "    def __init__(self, cb: Callable[[Int32], None]) -> None:\n"
            "        self.cb = cb\n"
            "def main() -> None:\n"
            "    h = Handler(lambda n: print(n))\n"
            "    h.cb(3)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["method.callable_field"] >= 1
        assert faces["ctor.lambda_arg"] >= 1
        _assert_byte_identical(src)

    def test_optional_callable_field_stays_ast(self):
        # Optional[Callable] fields take the `.value()` unwrap -- AST.
        src = _PRELUDE + (
            "from typing import Callable\n"
            "class Handler:\n"
            "    cb: Callable[[Int32], None] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.cb = None\n"
            "def use(h: Handler) -> None:\n"
            "    h.cb(3)\n"
        )
        assert _fn(_lower_ctx(src), "use") is None
        _assert_byte_identical(src)


class TestReadonlyEmptyContainer:
    def test_empty_literal_and_ctor_bind_inline(self):
        src = _PRELUDE + (
            "def f(l: readonly[list[Int32]]) -> None:\n"
            "    print(len(l))\n"
            "def main() -> None:\n"
            "    f(list())\n"
            "    f([])\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.readonly_empty_container"] >= 2
        _assert_byte_identical(src)

    def test_nonempty_readonly_literal_stays_ast(self):
        # The AST binds a non-empty readonly-slot literal INLINE
        # (`f({1, 2, 3})`); the temp arms exclude readonly slots, and the
        # inline mirror covers only the empty rvalue -- so this falls back.
        src = _PRELUDE + (
            "def f(l: readonly[list[Int32]]) -> None:\n"
            "    print(len(l))\n"
            "def main() -> None:\n"
            "    f([1, 2, 3])\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

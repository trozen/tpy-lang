"""Unbound-self base-class field access: `BaseN.field` -> `this->BaseN::field`
for reads, plain writes and aug-assigns."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_BASES = (
    "from tpy import Int32\n"
    "class Counter:\n"
    "    value: Int32\n"
    "class Tag:\n"
    "    label: str\n"
)


class TestUnboundSelfScalarField:
    def test_read_and_write_route(self):
        src = (_BASES
               + "class Combined(Counter, Tag):\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        Counter.value = n\n"
               + "    def bump(self) -> Int32:\n"
               + "        Counter.value = Counter.value + 1\n"
               + "        return Counter.value\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "bump") is not None
        assert w.get("field.unbound_self", 0) >= 1
        _assert_byte_identical(src)

    def test_aug_assign_routes(self):
        # The lvalue renders the same string into both slots of the
        # synthetic `target = (target OP value)`.
        src = (_BASES
               + "class Combined(Counter, Tag):\n"
               + "    def __init__(self) -> None:\n"
               + "        Counter.value = 0\n"
               + "    def tick(self) -> None:\n"
               + "        Counter.value += 1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "tick") is not None
        _assert_byte_identical(src)

    def test_str_field_read_and_write_route(self):
        src = (_BASES
               + "class Combined(Counter, Tag):\n"
               + "    def __init__(self, s: str) -> None:\n"
               + "        Tag.label = s\n"
               + "    def show(self) -> str:\n"
               + "        text = Tag.label\n"
               + "        return text\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "show") is not None
        _assert_byte_identical(src)


class TestUnboundSelfNonScalarField:
    def test_container_literal_write_routes(self):
        src = ("from tpy import Int32\n"
               "class Buf:\n"
               "    items: list[Int32]\n"
               "class Combined(Buf):\n"
               "    def __init__(self) -> None:\n"
               "        Buf.items = [1, 2]\n"
               "    def reset(self) -> None:\n"
               "        Buf.items = [3, 4]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "reset") is not None
        _assert_byte_identical(src)

    def test_record_rvalue_write_routes(self):
        src = ("from tpy import Int32\n"
               "class Inner:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Base:\n"
               "    slot: Inner\n"
               "class Child(Base):\n"
               "    def __init__(self) -> None:\n"
               "        Base.slot = Inner(0)\n"
               "    def reset(self) -> None:\n"
               "        Base.slot = Inner(7)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "reset") is not None
        _assert_byte_identical(src)


class TestUnboundSelfBoundaries:
    def test_resumable_receiver_stays_ast(self):
        # A generator/async method coro spells its receiver `__self` (a
        # `Record&`), but `_gen_field_access` hardcodes `this->` for the
        # unbound-self form -- an unwitnessed render, so it must keep
        # falling back rather than mirror an unverified spelling.
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "class Counter:\n"
               "    value: Int32\n"
               "class Combined(Counter):\n"
               "    def __init__(self) -> None:\n"
               "        Counter.value = 3\n"
               "    def walk(self) -> Iterator[Int32]:\n"
               "        i = 0\n"
               "        while i < Counter.value:\n"
               "            yield i\n"
               "            i = i + 1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "walk") is None

    def test_native_renamed_field_stays_ast(self):
        # `_gen_field_access` computes `cpp_field` from the SOURCE name, then
        # overrides it with `native_field_name` -- but the unbound-self early
        # return sits BETWEEN those two steps, so the AST spells the unrenamed
        # member and emits uncompilable C++ (BUGS.md). Routing this would
        # silently "fix" the AST and become a divergence, so it must reject.
        src = ("from tpy.extern import native, native_field\n"
               "from tpy import Int32\n"
               "@native\n"
               "class BaseA:\n"
               "    x: Int32 = native_field(\"m_x\")\n"
               "class Child(BaseA):\n"
               "    def read(self) -> Int32:\n"
               "        return BaseA.x\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "read") is None

    def test_container_field_borrow_decl_stays_ast(self):
        # `nums = Buf.items` binds a `const std::vector<T>&` REF_ALIAS: the
        # const-SPELLING decl sinks pin their source on
        # `_const_exact_field_receiver_ok`, which this arm deliberately does
        # NOT widen -- the read admission must not leak into the borrow-local
        # classifier.
        src = ("from tpy import Int32, readonly\n"
               "class Buf:\n"
               "    items: list[Int32]\n"
               "class Combined(Buf):\n"
               "    def __init__(self) -> None:\n"
               "        Buf.items = [1, 2]\n"
               "    @readonly\n"
               "    def size(self) -> Int32:\n"
               "        nums = Buf.items\n"
               "        return Int32(len(nums))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "size") is None

"""`for x in self.items:` where the field's type is an open type param.

The C++ member is the deduced `T`, so the loop captures it bare and
`::tpy::__iter__` resolves through the bound -- the same universal render a
protocol-typed param NAME already took. THIR had no type-param-bounds
threading at all on the for-head route; the bound now reaches it the way the
AST reads it (`ctx.current_type_param_bounds`).

The bound decides: a NativeIterable / Spannable bound takes the AST's
begin/end peephole instead (the native-bound field leg on the container
route). Resumable LEAVES admit self-field iterables too (wave 16); the
non-self receiver flavor keeps the fence.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (
    _reject_tally, _lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _reject_tags(src: str) -> dict:
    """A routed RESUMABLE never enters `thir.functions`, so its reject has to
    be read off the fallback map."""
    return _reject_tally(src)


_ITER = (
    "from typing import Iterable, Iterator\n"
    "from tpy import Int32, NativeIterable\n"
    "class MyRange:\n"
    "    lo: Int32\n"
    "    hi: Int32\n"
    "    def __init__(self, lo: Int32, hi: Int32) -> None:\n"
    "        self.lo = lo\n        self.hi = hi\n"
    "    def __iter__(self) -> 'MyRange':\n        return self\n"
    "    def __next__(self) -> Int32:\n"
    "        if self.lo >= self.hi:\n            raise StopIteration()\n"
    "        v = self.lo\n        self.lo += 1\n        return v\n"
)

_SUMMER = _ITER + (
    "class Summer[T: Iterable[Int32]]:\n"
    "    items: T\n"
    "    def __init__(self, items: T) -> None:\n        self.items = items\n"
    "    def total(self) -> Int32:\n"
    "        result = 0\n"
    "        for x in self.items:\n"
    "            result += x\n"
    "        return result\n"
    "    def gen_total(self) -> Iterator[Int32]:\n"
    "        for x in self.items:\n"
    "            yield x\n"
)

_NATIVE = _ITER + (
    "class NativeSummer[T: NativeIterable[Int32]]:\n"
    "    items: T\n"
    "    def __init__(self, items: T) -> None:\n        self.items = items\n"
    "    def total(self) -> Int32:\n"
    "        result = 0\n"
    "        for x in self.items:\n"
    "            result += x\n"
    "        return result\n"
)


class TestOpenTIterableField:
    def test_structural_bound_takes_the_universal_loop(self):
        body = _body(_lower_ctx(_SUMMER), "total")
        assert "auto& __src_0 = this->items;" in body
        assert "::tpy::__iter__(__src_0)" in body
        _assert_byte_identical(_SUMMER)

    def test_native_iterable_bound_takes_begin_end(self):
        # CONVERTED (the native-bound field leg): that bound renders the
        # begin/end member peephole, not the universal loop.
        thir, witnesses = _lower_ctx_witnessed(_NATIVE)
        body = _body(thir, "total")
        assert "auto& __obj_0 = this->items;" in body
        assert "__obj_0.begin();" in body
        assert witnesses.get("foreach.native_bound_field", 0) >= 1
        _assert_byte_identical(_NATIVE)

    def test_generator_over_the_same_field_routes(self):
        # The generator sibling rides since the generic-sgen cell (the
        # bounded-T iterable field's for-head admits like the sync row);
        # byte-identical -- formerly fenced on the generic-record reject.
        assert _reject_tags(_SUMMER) == {}
        _assert_byte_identical(_SUMMER)


class TestOwnIterNameLoop:
    """`oi = own_iter(src)` + `for x in oi:` -- the OwnIter decl slot spells
    `auto` (the TypeDef formatter) and the NAME iterable takes the AST's
    is_own_iter arm: plain lvalue capture + consuming `auto&&` elem."""

    _SRC = (
        "from tpy import Int32, own_iter\n"
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "def run() -> None:\n"
        "    src: list[Node] = [Node(1), Node(2)]\n"
        "    oi = own_iter(src)\n"
        "    total = 0\n"
        "    for x in oi:\n"
        "        total += x.val\n"
        "    print(total)\n"
        "run()\n")

    def test_own_iter_name_loop_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("decl.own_copy_iter_slot", 0) >= 1
        assert wit.get("foreach.own_iter_name", 0) >= 1
        assert "auto oi = ::tpy::own_iter(std::move(src));" in cpp
        assert "auto& __obj_0 = oi;" in cpp
        assert "auto&& x = *__beg_0;" in cpp

    def test_copy_iter_name_loop_still_defers(self):
        # The CopyIter sibling binds NON-consuming on the AST path -- the
        # name-iterable arm stays OwnIter-only, so the body keeps the AST.
        from .testutil import _fn, _lower_ctx
        src = (
            "from tpy import Int32, copy_iter\n"
            "def run() -> None:\n"
            "    src: list[Int32] = [1, 2, 3]\n"
            "    ci = copy_iter(src)\n"
            "    total = 0\n"
            "    for x in ci:\n"
            "        total += x\n"
            "    print(total, len(src))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "run") is None

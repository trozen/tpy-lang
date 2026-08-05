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
from .testutil import (_lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _fallback(src: str) -> dict:
    """A routed RESUMABLE never enters `thir.functions`, so its reject has to
    be read off the fallback map."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


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

    def test_generator_over_the_same_field_still_defers(self):
        # The generator sibling never even reaches the for-head gate: a
        # simple generator on a GENERIC record rejects first. The resumable
        # for-head family is closed independently (protocol_param_ok is
        # threaded False there), which the corpus witnesses -- the point
        # here is only that admitting the sync row left the generator out.
        assert _fallback(_SUMMER) == {"body:sgen.generic_record": 1}

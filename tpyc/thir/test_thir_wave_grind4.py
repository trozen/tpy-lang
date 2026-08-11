"""Wave 4 of the grind loop (site expressions.py:6232 + the consuming fi
gate): two rows.

Row 1 -- a VALUE-typed native iterator method result at a STORAGE sink
(`it: SpanIter[Int32] = a.__iter__()` -> the plain spelled copy decl).
Row 2 -- a consuming method on a POINTER-LOCAL receiver moves its deref
(`r1 = w.take()` on a rebind-slot local -> `std::move(*w).take()`; the
arrow folds into the deref, so the member access is `.`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
    _fn,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestNativeIterMethodReturn:
    SRC = (
        "from tpy import Int32, SpanIter\n"
        "from tplib.array_list import ArrayList\n"
        "from typing import Iterable\n"
        "def consume(it: Iterable[Int32]) -> None:\n"
        "    for x in it:\n"
        "        print(x)\n"
        "def main() -> None:\n"
        "    a = ArrayList[Int32, 8]()\n"
        "    a.append(1)\n"
        "    a.append(2)\n"
        "    it: SpanIter[Int32] = a.__iter__()\n"
        "    consume(it)\n"
        "main()\n"
    )

    def test_span_iter_ret_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "::tpy::SpanIter<int32_t> it = a.__iter__();" in cpp

    def test_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert wit.get("method.native_iter_ret", 0) >= 1

    def test_span_iter_at_non_storage_sink_keeps_rejecting(self):
        # The ret row is STORAGE-sink only: a SpanIter result passed
        # directly as a call arg stays on the AST path.
        src = (
            "from tpy import Int32, SpanIter\n"
            "from tplib.array_list import ArrayList\n"
            "from typing import Iterable\n"
            "def consume(it: Iterable[Int32]) -> None:\n"
            "    for x in it:\n"
            "        print(x)\n"
            "def main() -> None:\n"
            "    a = ArrayList[Int32, 8]()\n"
            "    a.append(1)\n"
            "    consume(a.__iter__())\n"
            "main()\n"
        )
        fell = _thir_fallbacks(src)
        # The reject moved INTO the structural-temp init's method-call
        # lowering when the protocol call-rvalue leg landed (the arg gate
        # now admits the hoist; the inner __iter__ call still rejects) --
        # the stays-AST claim is unchanged, dualgen-verified identical.
        assert ("body:expr.call" in fell
                or "body:expr.method_call" in fell), fell


class TestConsumingPointerReceiver:
    SRC = (
        "from typing import Self\n"
        "from tpy import Int32, Own\n"
        "class Wrapper:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "    def take(self: Own[Self]) -> Int32:\n"
        "        return self.v\n"
        "def main() -> None:\n"
        "    w = Wrapper(42)\n"
        "    r1 = w.take()\n"
        "    w = Wrapper(99)\n"
        "    r2 = w.take()\n"
        "    print(r1, r2)\n"
        "main()\n"
    )

    def test_pointer_receiver_moves_deref(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::move(*w).take()" in cpp

    def test_narrowed_optional_receiver_also_moves_deref(self):
        # A narrowed Optional-ptr receiver is the same pointer shape: both
        # paths spell the deref move (`std::move(*w).take()`).
        src = (
            "from typing import Self\n"
            "from tpy import Int32, Own\n"
            "class Wrapper:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    def take(self: Own[Self]) -> Int32:\n"
            "        return self.v\n"
            "def use(w: Wrapper | None) -> Int32:\n"
            "    if w is not None:\n"
            "        return w.take()\n"
            "    return 0\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return std::move(*w).take();" in cpp

"""Wave 11 of the grind loop: the ptr-Optional tuple-field element stack.

Three rows, chained by one case (`tuple/tuple_mixed_elem_field`): the
DECL lift (`first = h.t[0]` -> `Box* first =
::tpy::optional_to_ptr(std::get<0>(h.t));` -- classifies OTHER via the
classifier's tuple carve-out, re-tagged for the subscript-lift row); the
SAME-repr pointer-Optional NAME element at a borrow-tuple literal slot
(`h.set((n, y))` -> `{n, &(y)}` -- already the element's `T*`, passes
bare); and the `is [not] None` subject over the same element read
(`h.t[0] is None` -> the pre-lifted pointer compare).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _thir_fallbacks(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_BOX_H = (
    "from typing import Optional\n"
    "from tpy import Int32\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.val = v\n"
    "class H:\n"
    "    t: tuple[Optional[Box], Box]\n"
    "    def __init__(self, a: Box, b: Box) -> None:\n"
    "        self.t = (a, b)\n"
    "    def set(self, p: tuple[Optional[Box], Box]) -> None:\n"
    "        self.t = p\n"
)


class TestTupleFieldOptElemStack:
    SRC = (
        _BOX_H +
        "def main() -> None:\n"
        "    a = Box(1)\n"
        "    b = Box(2)\n"
        "    h = H(a, b)\n"
        "    x = Box(9)\n"
        "    y = Box(8)\n"
        "    h.set((x, y))\n"
        "    first = h.t[0]\n"
        "    if first is not None:\n"
        "        print(first.val)\n"
        "    n: Optional[Box] = None\n"
        "    h.set((n, y))\n"
        "    print(h.t[0] is None)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert ("Box* first = ::tpy::optional_to_ptr(std::get<0>(h.t));"
                in cpp)
        assert "h.set(std::tuple<Box*, Box*>{n, &(y)});" in cpp
        assert ("(::tpy::optional_to_ptr(std::get<0>(h.t)) == nullptr)"
                in cpp)

    def test_faces(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("decl.opt_tuple_elem_lift", 0) >= 1
        assert w.get("btuple.elem_optptr", 0) >= 1
        assert w.get("isnone.tuple_elem_lift", 0) >= 1


class TestTupleFieldOptElemConst:
    # A readonly receiver makes the element lift const on both paths -- the
    # `_f1_const_rooted_source` subscript-receiver arm.
    SRC = (
        "from typing import Optional\n"
        "from tpy import Int32, readonly\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "class H:\n"
        "    t: tuple[Optional[Box], Box]\n"
        "    def __init__(self, b: Box) -> None:\n"
        "        self.t = (None, b)\n"
        "    @readonly\n"
        "    def peek(self) -> Int32:\n"
        "        first = self.t[0]\n"
        "        if first is not None:\n"
        "            return first.val\n"
        "        return -1\n"
        "def main() -> None:\n"
        "    h = H(Box(2))\n"
        "    print(h.peek())\n"
        "    print(h.t[0] is not None)\n"
        "main()\n"
    )

    def test_const_lift_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=True))
        assert thir == ast
        # peek's body routes (the ctor's tuple member-init is a separate
        # pre-existing fallback axis, so no whole-module routing claim).
        fell = dict(compiler._thir_fallback)
        assert not any(k.startswith("body:") for k in fell), fell
        assert ("const Box* first = "
                "::tpy::optional_to_ptr(std::get<0>(this->t));"
                in thir[0] + thir[1])


class TestLocalTupleReceiverStaysDeferred:
    # The predicate requires a FIELD receiver: the same element read off a
    # LOCAL borrow-tuple binding keeps its own (unwitnessed) axis.
    SRC = (
        _BOX_H +
        "def main() -> None:\n"
        "    a = Box(1)\n"
        "    b = Box(2)\n"
        "    t = (a, b)\n"
        "    first = t[0]\n"
        "    print(first.val)\n"
        "main()\n"
    )

    def test_local_receiver_falls_back(self):
        fell = _thir_fallbacks(self.SRC)
        assert any(k.startswith("body:") for k in fell), fell

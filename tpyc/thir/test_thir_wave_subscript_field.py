"""Pins for the subscript field-receiver row: `.field` off a subscript
(`pp[0][0].fd`, `h.pair[0].v`, `xs[0].v`, `make_pair(a, b)[1].v`).

The boundary is load-bearing rather than tidy: a blanket subscript
admission was measured to DROP `::tpy::deref_check(...)` on a `Ptr`
element -- a missing null check, not merely a byte divergence -- so the
Ptr/Optional element shapes must keep rejecting.
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _fn,
)

_PRELUDE = (
    "from tpy import Int32\n"
    "class Box:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
)


class TestSubscriptFieldReceiver:
    def test_borrow_tuple_element_routes(self):
        src = _PRELUDE + (
            "def read(b: Box, c: Box) -> Int32:\n"
            "    t = (b, c)\n"
            "    return t[0].v + t[1].v\n"
            "def main() -> None:\n"
            "    print(read(Box(1), Box(2)))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_field_rooted_tuple_subscript_routes(self):
        src = _PRELUDE + (
            "class Holder:\n"
            "    pair: tuple[Box, Int32]\n"
            "    def __init__(self, b: Box) -> None:\n"
            "        self.pair = (b, 5)\n"
            "def read(h: Holder) -> Int32:\n"
            "    return h.pair[0].v\n"
            "def main() -> None:\n"
            "    print(read(Holder(Box(3))))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_container_subscript_routes(self):
        src = _PRELUDE + (
            "def from_list(xs: list[Box]) -> Int32:\n"
            "    return xs[0].v\n"
            "def from_dict(d: dict[Int32, Box]) -> Int32:\n"
            "    return d[1].v\n"
            "def main() -> None:\n"
            "    print(from_list([Box(7)]), from_dict({1: Box(9)}))\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src)

    def test_call_rooted_subscript_is_identical_but_blocked_elsewhere(self):
        """The field row admits a CALL-rooted subscript, but the enclosing
        body still falls back at the tuple-returning call (`expr.call`), so
        this cannot claim routing -- identity is all it proves. It stays as
        the witness that the row itself does not diverge on the shape."""
        src = _PRELUDE + (
            "def make_pair(a: Box, b: Box) -> tuple[Box, Box]:\n"
            "    return (a, b)\n"
            "def read(a: Box, b: Box) -> Int32:\n"
            "    return make_pair(a, b)[1].v\n"
            "def main() -> None:\n"
            "    print(read(Box(1), Box(2)))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ret_type.tuple")

    def test_face_witnessed(self):
        src = _PRELUDE + (
            "def read(b: Box, c: Box) -> Int32:\n"
            "    t = (b, c)\n"
            "    return t[0].v\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("field.subscript_recv")
        assert _fn(thir, "read") is not None


class TestPtrElementKeepsItsCheck:
    """REJECT-UNIT: identity IS the claim here. The Ptr element's field read
    renders `::tpy::deref_check(std::get<1>(h.pair)).name`; this row does not
    spell that wrap, so the body must stay on the AST path and re-emit it."""

    def test_ptr_tuple_element_stays_ast(self):
        src = (
            "from tpy import Int32, Ptr, take_ptr\n"
            "class Tag:\n"
            "    name: Int32\n"
            "    def __init__(self, name: Int32) -> None:\n"
            "        self.name = name\n"
            "class Holder:\n"
            "    pair: tuple[Int32, Ptr[Tag]]\n"
            "    def __init__(self, p: Ptr[Tag]) -> None:\n"
            "        self.pair = (1, p)\n"
            "def main() -> None:\n"
            "    t = Tag(5)\n"
            "    h = Holder(take_ptr(t))\n"
            "    print(h.pair[1].name)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:subscript.tuple_shape")


class TestBorrowTupleFieldRootedSubscriptSource:
    """A borrow-tuple decl whose source is a subscript over a FIELD
    (`pair = self.store[k]`), the sibling of the name-rooted source the
    cascade already took -- both render the `tuple_to_pointer` lift."""

    _SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "class Holder:\n"
        "    store: dict[str, tuple[Int32, Box]]\n"
        "    def __init__(self, b: Box) -> None:\n"
        "        self.store = {'a': (1, b)}\n"
        "    def get_weight(self, k: str) -> Int32:\n"
        "        pair = self.store[k]\n"
        "        if pair[0] < 0:\n"
        "            pair = self.store['a']\n"
        "        return pair[0]\n"
        "def main() -> None:\n"
        "    h = Holder(Box(5))\n"
        "    print(h.get_weight('a'))\n"
        "main()\n"
    )

    def test_field_rooted_subscript_source_routes(self):
        _assert_routes_byte_identical(self._SRC)

    def test_readonly_root_stays_ast(self):
        """BOUNDARY: a readonly-typed subscript root keeps rejecting on both
        the name and field roots -- its element pointers would need the const
        spelling this source row does not decide."""
        src = (
            "from tpy import Int32, readonly\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.val = v\n"
            "def peek(store: readonly[dict[str, tuple[Int32, Box]]]) -> Int32:\n"
            "    pair = store['a']\n"
            "    return pair[0]\n"
            "def main() -> None:\n"
            "    print(peek({'a': (1, Box(5))}))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")


class TestReadonlyElementReceiver:
    """The predicate unwraps readonly before the F1-record test, so a
    readonly-typed container's element is admitted -- pinned because that
    unwrap is otherwise only exercised by the corpus byte-diff."""

    def test_readonly_container_element_routes(self):
        src = (
            "from tpy import Int32, readonly\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def peek(xs: readonly[list[Box]]) -> Int32:\n"
            "    return xs[0].v\n"
        )
        _assert_routes_byte_identical(src)


class TestDerefMarkedFieldStaysAst:
    """REJECT-UNIT: a user-`__deref__` receiver behind a subscript. The row
    is marker-clean because the render tail drops the `__deref__()` hops for
    a non-NAME receiver -- without the guard THIR emitted `xs[0].x` where the
    AST emits `xs[0].__deref__().x`, and no corpus case reaches the shape."""

    _SRC = (
        "from tpy import Int32, copy, auto_readonly\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Ref:\n"
        "    _target: Point\n"
        "    def __init__(self, target: Point) -> None:\n"
        "        self._target = copy(target)\n"
        "    @auto_readonly\n"
        "    def __deref__(self) -> Point:\n"
        "        return self._target\n"
    )

    def test_container_element_deref_stays_ast(self):
        src = self._SRC + (
            "def use(xs: list[Ref]) -> Int32:\n"
            "    return xs[0].x\n"
        )
        cpp = "".join(_assert_byte_identical(src))
        assert "__deref__()" in cpp

    def test_tuple_element_deref_stays_ast(self):
        src = self._SRC + (
            "def use(t: tuple[Ref, Int32]) -> Int32:\n"
            "    return t[0].x\n"
        )
        cpp = "".join(_assert_byte_identical(src))
        assert "__deref__()" in cpp

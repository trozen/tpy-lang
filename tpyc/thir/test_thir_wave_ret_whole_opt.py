"""Returning a call result that IS the whole storage optional.

`std::optional<T>`-slotted returns previously admitted only sources that
had to be BUILT into the slot (a record rvalue absorbed by the converting
ctor, an `Own[P | None]` param moved whole, a borrow `T*` lifted). A callee
that already returns the slot's own type hands back a prvalue of exactly
that type, so the return forwards bare with no lift and no spelled move.
Exact type identity is the guard: a ptr-repr `T | None` result renders `T*`
and must never reach a `std::optional<T>` slot this way.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _thir_ctx,
)

_PRE = (
    "from typing import Optional\n"
    "from tpy import Int32, Own\n"
    "class Rec:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def build(n: Int32) -> Optional[Own[Rec]]:\n"
    "    if n < 0:\n"
    "        return None\n"
    "    return Rec(n)\n"
)


class TestFreeCallWholeOptionalRoutes:
    # The routing witness: `build(n)` already returns std::optional<Rec>, so
    # the relay returns it bare.
    SRC = (
        _PRE +
        "def relay(n: Int32) -> Optional[Own[Rec]]:\n"
        "    return build(n)\n"
        "def main() -> None:\n"
        "    r = relay(3)\n"
        "    if r is not None:\n"
        "        print(r.n)\n"
        "main()\n"
    )

    def test_routes_with_face(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("ret.storage_opt_whole_rvalue", 0) >= 1

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return build(n);" in cpp


class TestMethodCallWholeOptionalRoutes:
    # The method-call twin over a constructor receiver -- the stdlib's
    # `re.search` shape (`return Pattern(p, f).search(s);`).
    SRC = (
        _PRE +
        "class Finder:\n"
        "    base: Int32\n"
        "    def __init__(self, base: Int32) -> None:\n"
        "        self.base = base\n"
        "    def find(self, n: Int32) -> Optional[Own[Rec]]:\n"
        "        return build(n + self.base)\n"
        "def relay(n: Int32) -> Optional[Own[Rec]]:\n"
        "    return Finder(1).find(n)\n"
        "def main() -> None:\n"
        "    r = relay(3)\n"
        "    if r is not None:\n"
        "        print(r.n)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return Finder(1).find(n);" in cpp


_WRAPPER = (
    "from typing import Optional\n"
    "from tpy import Int32, Own\n"
    "class Node:\n"
    "    v: Int32\n"
    "    nxt: 'Node | None'\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
    "        self.nxt = None\n"
    "def borrow(n: Node) -> Optional[Node]:\n"
    "    return n.nxt\n"
)


class TestOwnedOptionalSpellingRoutes:
    # The reverse spelling: `Own[Optional[W]]` is the same
    # `std::optional<W>` slot, and a callee declared the same way forwards
    # into it bare.
    SRC = (
        _WRAPPER +
        "def make(v: Int32) -> Own[Optional[Node]]:\n"
        "    if v < 0:\n"
        "        return None\n"
        "    return Node(v)\n"
        "def relay(v: Int32) -> Own[Optional[Node]]:\n"
        "    return make(v)\n"
        "def main() -> None:\n"
        "    r = relay(1)\n"
        "    if r is not None:\n"
        "        print(r.v)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return make(v);" in cpp


class TestPointerReprCalleeAtOwnedSlotDefers:
    # BOUNDARY: sema strips `Own` off a call's RESULT type, so a callee
    # declared `-> Optional[Node]` (pointer-repr, `Node*`) reads as the
    # same type as the `Own[Optional[Node]]` slot. Only the callee's
    # DECLARED return tells them apart, and forwarding this one bare would
    # put a `Node*` in a `std::optional<Node>`.
    SRC = (
        _WRAPPER +
        "def relay(n: Node) -> Own[Optional[Node]]:\n"
        "    return borrow(n)\n"
        "def main() -> None:\n"
        "    r = relay(Node(1))\n"
        "    if r is not None:\n"
        "        print(r.v)\n"
        "main()\n"
    )

    def test_defers_at_named_shape(self):
        _ctx, fb = _thir_ctx(self.SRC)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.storage_opt_source")


class TestTernaryOfCallsAtStorageSlotDefers:
    # BOUNDARY: same slot, same callee, but the source is a TERNARY -- the
    # arm admits a call node only, so the whole-optional forward is
    # unwitnessed for it and the body keeps the named reject.
    SRC = (
        _PRE +
        "def relay(n: Int32) -> Optional[Own[Rec]]:\n"
        "    return build(n) if n > 0 else None\n"
        "def main() -> None:\n"
        "    r = relay(3)\n"
        "    if r is not None:\n"
        "        print(r.n)\n"
        "main()\n"
    )

    def test_defers_at_named_shape(self):
        _ctx, fb = _thir_ctx(self.SRC)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.storage_opt_source")

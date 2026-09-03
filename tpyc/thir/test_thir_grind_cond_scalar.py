"""Everyday truthiness conditions: the operand's TYPE decides, not its shape.

C++'s contextual conversion makes the ordinary value render a valid boolean
test for every native scalar, so a subscript read, a call result, a field
read, an arithmetic operand and a bare name of the same type all render the
same condition. These pins hold that one predicate in place -- a shape that
starts asking the question for itself will disagree with one of them.
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
    _reject_tally,
)

_SRC = (
    "from tpy import Int32, Char\n"
    "class Holder:\n"
    "    n: Int32\n"
    "    ratio: float\n"
    "    tag: str\n"
    "    def __init__(self) -> None:\n"
    "        self.n = 2\n"
    "        self.ratio = 0.5\n"
    "        self.tag = \"t\"\n"
    "    def size(self) -> Int32:\n"
    "        return self.n\n"
    "def width() -> Int32:\n"
    "    return 3\n"
    "def probe(h: Holder, xs: list[Int32], names: list[str],\n"
    "          rows: list[list[Int32]], c: Char) -> Int32:\n"
    "    n = 0\n"
    "    if xs[0]:\n        n += 1\n"
    "    if names[0]:\n        n += 2\n"
    "    if rows[0]:\n        n += 4\n"
    "    if h.n:\n        n += 8\n"
    "    if h.ratio:\n        n += 16\n"
    "    if h.size():\n        n += 32\n"
    "    if width():\n        n += 64\n"
    "    if n + 1:\n        n += 128\n"
    "    if 1:\n        n += 256\n"
    "    if c:\n        n += 512\n"
    "    while len(xs):\n        xs.pop()\n"
    "    return n\n"
    "def main() -> None:\n"
    "    print(probe(Holder(), [1], [\"a\"], [[1]], Char(\"x\")))\n"
    "main()\n"
)


class TestScalarConditionShapes:
    def test_every_shape_routes(self):
        thir = _lower_ctx(_SRC)
        assert _fn(thir, "probe") is not None
        assert _reject_tally(_SRC) == {}

    def test_renders(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC)
        out = hpp + cpp
        # Scalar element, field, method-call, free-call, arithmetic, literal
        # and Char operands all render the bare value as the test.
        assert "if (::tpy::__getitem__(xs, 0)) {" in out
        assert "if (h.n) {" in out
        assert "if (h.ratio) {" in out
        assert "if (h.size()) {" in out
        assert "if (width()) {" in out
        assert "if ((::tpy::add_check<int32_t>(n, 1))) {" in out
        assert "if (1) {" in out
        assert "if (c) {" in out
        assert "while (::tpy::__len__(xs)) {" in out
        # The moded shapes wrap the SAME element render they always did.
        assert "if ((!::tpy::__getitem__(names, 0).empty())) {" in out
        assert "if ((::tpy::__len__(::tpy::__getitem__(rows, 0)) != 0)) {" in out

    def test_bare_render_faces_stay_witnessed(self):
        _thir, wit = _lower_ctx_witnessed(_SRC)
        assert wit.get("cond.bool_field", 0) >= 1
        assert wit.get("cond.bool_method", 0) >= 1
        assert wit.get("cond.bool_call", 0) >= 1


class TestConditionBoundaries:
    def test_always_true_element_keeps_rejecting(self):
        # A record ELEMENT with no dunder folds to `true` on the oracle and
        # DISCARDS the read -- but that read carries the bounds check, so the
        # discard render is not verified here and the shape must not admit
        # on the scalar arm's coattails.
        src = (
            "from tpy import Int32\n"
            "class Rec:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n        self.n = 1\n"
            "def probe(rs: list[Rec]) -> Int32:\n"
            "    if rs[0]:\n        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe([Rec()]))\n"
            "main()\n"
        )
        # The observable tag is the statement's shape label: the composed
        # `cond.<kind>` detail is set first and wins over the inner reason.
        _assert_rejects_at(_reject_tally(src), "body:stmt.if",
                           "cond.subscript")

    def test_str_literal_condition_keeps_rejecting(self):
        # `if "":` has no verified render in this position -- the oracle
        # spells `(!"".empty())` over a `const char*`, which is not C++.
        src = (
            "def probe() -> int:\n"
            "    if \"\":\n        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe())\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:stmt.if",
                           "cond.str_literal")

    def test_none_literal_condition_keeps_rejecting(self):
        src = (
            "def probe() -> int:\n"
            "    if None:\n        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe())\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:stmt.if",
                           "cond.none_literal")


class TestScalarConditionOperandPositions:
    _SRC = (
        "from tpy import Int32\n"
        "def width() -> Int32:\n"
        "    return 3\n"
        "def probe(xs: list[Int32], n: Int32) -> Int32:\n"
        "    total = 0\n"
        "    if n and xs[0]:\n        total += 1\n"
        "    if width() or xs[0]:\n        total += 2\n"
        "    if not xs[0]:\n        total += 4\n"
        "    total += 8 if xs[0] else 0\n"
        "    return total\n"
        "def main() -> None:\n"
        "    print(probe([1], 1))\n"
        "main()\n"
    )

    def test_logical_and_negated_operands_route(self):
        # A scalar condition composes as an operand too -- `and`/`or` recurse
        # into the same predicate and `not` shares it with them.
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "probe") is not None
        assert _reject_tally(self._SRC) == {}

    def test_operand_renders(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "if ((n && ::tpy::__getitem__(xs, 0))) {" in out
        assert "if ((width() || ::tpy::__getitem__(xs, 0))) {" in out
        assert "if ((!(::tpy::__getitem__(xs, 0)))) {" in out


class TestMatchGuardIsAConditionSite:
    _SRC = (
        "from tpy import Int32\n"
        "class H:\n"
        "    n: Int32\n"
        "    tag: str\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "        self.tag = \"t\"\n"
        "def size(h: H) -> Int32:\n"
        "    return h.n\n"
        "def probe(h: H, xs: list[Int32], names: list[str]) -> Int32:\n"
        "    match h.n:\n"
        "        case 1 if xs[0]:\n            return 1\n"
        "        case 2 if size(h):\n            return 2\n"
        "        case 3 if h.n + 1:\n            return 3\n"
        "        case 4 if names[0]:\n            return 4\n"
        "        case 5 if h.tag:\n            return 5\n"
        "        case _:\n            return 0\n"
        "def main() -> None:\n"
        "    print(probe(H(1), [1], [\"a\"]))\n"
        "main()\n"
    )

    def test_scalar_guards_route(self):
        # A guard is a boolean context like any other: it asks the shared
        # type question instead of demanding the lowered node BE a bool.
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "probe") is not None
        assert _reject_tally(self._SRC) == {}

    def test_guard_renders(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "::tpy::__getitem__(xs, 0)" in out
        assert "size(h)" in out
        assert "!::tpy::__getitem__(names, 0).empty()" in out
        assert "!h.tag.empty()" in out

"""`while` heads that narrow a ptr-variant union subject: the
exhaustiveness-FOLDED head, an isinstance leaf under `||`, and a rebind of
the narrowed subject inside the loop body (which kills the narrowing and
takes the ordinary union reseat)."""

from __future__ import annotations

import pytest

from .reject import ThirUnsupported
from .lower import _LowerCtx
from .lower.statements import _lower_stmt
from ..parse.nodes import TpyAssign, TpyName
from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_byte_identical, _assert_routes_byte_identical,
                       _compile, _entry, _fn, _lower_ctx)

_UNION = (
    "from tpy import Int32\n"
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "class B:\n"
    "    y: Int32\n"
    "    def __init__(self, y: Int32) -> None:\n"
    "        self.y = y\n"
)


class TestWhileNarrowRoutes:
    def test_folded_head_routes(self):
        # Entry narrowing folds the test to the exhaustiveness constant, so
        # the head renders `while (true)` -- and the body still extracts.
        src = (_UNION
               + "def invariant() -> Int32:\n"
               + "    t: A | B = A(3)\n"
               + "    n = 0\n"
               + "    while isinstance(t, A):\n"
               + "        n += t.x\n"
               + "        break\n"
               + "    return n\n"
               + "def main() -> None:\n"
               + "    print(invariant())\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "while (true) {" in cpp
        assert "auto& __t = *std::get<A*>(t);" in cpp

    def test_or_chain_head_routes(self):
        # `or` installs no fact: the head is the bare membership test and the
        # body walks UN-narrowed (no extraction alias), so the body's rebind
        # rides the ordinary un-narrowed reseat.
        src = (_UNION
               + "def or_rebind() -> Int32:\n"
               + "    t: A | B = A(1)\n"
               + "    keep = True\n"
               + "    n = 0\n"
               + "    while isinstance(t, A) or keep:\n"
               + "        n += 1\n"
               + "        t = B(2)\n"
               + "        keep = False\n"
               + "    return n\n"
               + "def main() -> None:\n"
               + "    print(or_rebind())\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "while ((std::holds_alternative<A*>(t) || keep)) {" in cpp
        assert "auto& __t = " not in cpp

    def test_narrowed_rebind_routes(self):
        # The rebind kills the narrowing and takes the rebind-slot reseat
        # over the ORIGINAL union local.
        src = (_UNION
               + "def drain() -> Int32:\n"
               + "    t: A | B = A(2)\n"
               + "    total = 0\n"
               + "    while isinstance(t, A):\n"
               + "        total += t.x\n"
               + "        t = B(9)\n"
               + "    return total\n"
               + "def main() -> None:\n"
               + "    print(drain())\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "__slot_2.emplace(B(9));" in cpp
        assert "t = ::tpy::to_ptr_variant(*__slot_2);" in cpp

    def test_renarrow_after_rebind_routes(self):
        # A re-narrow of the same subject AFTER the kill: the killed scope
        # leaves no stale alias behind, so the inner `if` installs its own.
        src = (_UNION
               + "def f() -> Int32:\n"
               + "    t: A | B = A(1)\n"
               + "    n = 0\n"
               + "    while isinstance(t, A):\n"
               + "        n += t.x\n"
               + "        t = B(7)\n"
               + "        if isinstance(t, B):\n"
               + "            n += t.y\n"
               + "    return n\n"
               + "def main() -> None:\n"
               + "    print(f())\n"
               + "main()\n")
        _assert_routes_byte_identical(src, comments=False)


class TestWhileNarrowBoundaries:
    def test_name_source_rebind_stays_ast(self):
        # BOUNDARY: the kill is scoped to the concrete-member RVALUE reseat
        # (`UNION_RVALUE`). A concrete-member NAME source is the address
        # reseat -- a different render, unwitnessed under a kill.
        src = (_UNION
               + "def f() -> Int32:\n"
               + "    t: A | B = A(1)\n"
               + "    other = B(4)\n"
               + "    n = 0\n"
               + "    while isinstance(t, A):\n"
               + "        n += t.x\n"
               + "        t = other\n"
               + "    return n\n"
               + "def main() -> None:\n"
               + "    print(f())\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.narrowed_rebind")

    def test_raw_assign_rebind_of_narrowed_still_rejects(self):
        # BOUNDARY: the kill lives in the var-decl arm. A frontend-IR raw
        # TpyAssign of a narrowed subject keeps rejecting -- its own arm
        # never classifies the union reseat.
        src = (_UNION
               + "def f(v: A | B, w: A | B) -> Int32:\n"
               + "    x = w\n"
               + "    return 0\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        fn = next(f for f in entry.ast.functions if f.name == "f")
        declared = {n: t for n, t in fn.params}
        lc = _LowerCtx(fn, entry.analyzer, None)
        lc.narrow.narrowed["v"] = "__v"
        with pytest.raises(ThirUnsupported, match="stmt.assign"):
            _lower_stmt(TpyAssign(target=TpyName("v"),
                                  value=fn.body[0].init), lc, declared)

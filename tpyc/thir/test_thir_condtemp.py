"""Conditional-operand arg temps: the eager and deferred slices. A
NON-deferring temp (non-movable type or unspellable slot) hoists at the
enclosing statement inside a logical RHS, a ternary scalar arm, or a chained
comparator i>=2; an AUDITED deferring temp (movable fact mirrored off the
AST creator) defers through the emit's shared `conditional_region`
machinery; an UNAUDITED row that might defer keeps rejecting via the
`_lower_expr` exit check (argtemp.cond_defer). The logical LHS always
evaluates and keeps the plain statement hoist."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_byte_identical, _fn, _lower_ctx,
                       _lower_ctx_witnessed, _assert_routes_byte_identical)

# The @nomove + __del__ pair deletes the C++ move ctor, so is_movable() is
# False and the AST keeps the temp eager inside conditional regions.
_PINNED = (
    "from tpy import Int32, nomove\n"
    "@nomove\n"
    "class Pinned:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "    def __del__(self) -> None:\n        self.n = 0\n"
    "def take(p: Pinned | None) -> Int32:\n"
    "    return p.n if p is not None else 0\n"
)


class TestCondEagerTemps:
    SRC = (
        _PINNED
        + "def and_rhs(flag: bool) -> bool:\n"
        + "    return flag and take(Pinned(7)) > 0\n"
        + "def ternary_arm(flag: bool) -> Int32:\n"
        + "    return take(Pinned(9)) if flag else -1\n"
        + "def chained(a: Int32, b: Int32) -> bool:\n"
        + "    return a < b < take(Pinned(10))\n"
        + "def main() -> None:\n"
        + "    print(and_rhs(True), ternary_arm(False), chained(0, 5))\n"
        + "main()\n"
    )

    def test_routed_and_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        for name in ("and_rhs", "ternary_arm", "chained"):
            assert _fn(thir, name) is not None, name
        assert witnessed.get("argtemp.cond_eager", 0) >= 3

    def test_byte_identical_eager_hoist(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The temp hoists at the statement (eager), never an optional slot.
        assert "Pinned __tmp_1 = Pinned(7);" in cpp
        assert "std::optional<Pinned>" not in cpp

    def test_movable_temp_defers(self):
        # An audited MOVABLE record temp in a conditional operand DEFERS
        # through the emit's region (the optional-slot render), witnessed
        # as argtemp.cond_defer_audited.
        src = (
            "from tpy import Int32\n"
            "class Pt:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def take(p: Pt | None) -> Int32:\n"
            "    return p.n if p is not None else 0\n"
            "def f(flag: bool) -> bool:\n"
            "    return flag and take(Pt(7)) > 0\n"
            "def main() -> None:\n    print(f(True))\nmain()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert witnessed.get("argtemp.cond_defer_audited", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::optional<Pt> __tmp_1;" in cpp
        assert "__tmp_1.emplace(Pt(7))" in cpp

    def test_logical_lhs_temp_hoists(self):
        # The LHS always evaluates: its temp is the plain statement hoist,
        # never an optional slot.
        src = (
            _PINNED
            + "def f(flag: bool) -> bool:\n"
            + "    return take(Pinned(7)) > 0 and flag\n"
            + "def main() -> None:\n    print(f(True))\nmain()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "Pinned __tmp_1 = Pinned(7);" in cpp
        assert "std::optional<Pinned>" not in cpp

    def test_unaudited_movable_temp_keeps_rejecting(self):
        # BOUNDARY: an UNAUDITED temp row (no movable fact) that might
        # defer keeps rejecting -- a value-union member temp in a
        # conditional operand.
        src = (
            "from tpy import Int32\n"
            "def take(u: Int32 | str) -> bool:\n"
            "    return isinstance(u, Int32)\n"
            "def f(flag: bool, x: Int32) -> bool:\n"
            "    return flag and take(x)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:argtemp.cond_defer")


class TestCondTempPeripheryArms:
    """The fork-4 periphery arms (the two remaining cases' set cover): the
    list-repeat ref-param temp, the ctor container-literal temp, the
    value-select RHS grant, the for-head iterator ternary, the
    comprehension-as-ternary-arm rung, and per-iteration comp element
    temps. Byte-identity for the whole family also rides the two flipped
    corpus cases (short_circuit_brace_arg, short_circuit_comp_arg_temp)."""

    def test_list_repeat_arg_defers(self):
        src = ("from tpy import Int64, Int32\n"
               "def take_i64(o: list[Int64]) -> Int64:\n"
               "    return len(o)\n"
               "def f(flag: bool, n: Int32) -> Int64:\n"
               "    return take_i64([0] * n) if flag else 0\n"
               "def main() -> None:\n    print(f(True, 2))\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("argtemp.list_repeat", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::optional<std::vector<int64_t>> __tmp_1;" in cpp

    def test_list_repeat_plain_position_hoists(self):
        # The same row outside a conditional operand: the eager statement
        # hoist.
        src = ("from tpy import Int64, Int32\n"
               "def take_i64(o: list[Int64]) -> Int64:\n"
               "    return len(o)\n"
               "def f(n: Int32) -> Int64:\n"
               "    return take_i64([0] * n)\n"
               "def main() -> None:\n    print(f(2))\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::optional" not in cpp

    def test_ctor_container_literal_arg_defers(self):
        # The MUTATED ctor slot is what hoists (a mutable ref cannot bind a
        # prvalue); a const slot keeps the inline brace-init -- see the
        # boundary below.
        src = ("from tpy import Int64\n"
               "class Holder:\n"
               "    n: Int64\n"
               "    def __init__(self, o: list[Int64]):\n"
               "        o.append(4)\n"
               "        self.n = o[0]\n"
               "def f(flag: bool) -> Int64:\n"
               "    return Holder([1, 2, 3]).n if flag else 0\n"
               "def main() -> None:\n    print(f(True))\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::optional<std::vector<int64_t>> __tmp_1;" in cpp
        assert "Holder((*__tmp_1)).n" in cpp

    def test_value_select_rhs_defers(self):
        # `0 or Holder([..]).n` -- the VALUE-position or's RHS grant (the
        # mutated slot again, so the temp exists to defer).
        src = ("from tpy import Int64\n"
               "class Holder:\n"
               "    n: Int64\n"
               "    def __init__(self, o: list[Int64]):\n"
               "        o.append(4)\n"
               "        self.n = o[0]\n"
               "def f() -> Int64:\n"
               "    return 0 or Holder([1, 2, 3]).n\n"
               "def main() -> None:\n    print(f())\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "__tmp_2.emplace(std::vector<int64_t>{1, 2, 3})" in cpp

    def test_iterator_ternary_source_routes(self):
        src = ("from tpy import Int64\n"
               "from typing import Iterator\n"
               "def gen(o: list[Int64]) -> Iterator[Int64]:\n"
               "    for x in o:\n        yield x\n"
               "def f(flag: bool) -> Int64:\n"
               "    total = Int64(0)\n"
               "    for v in (gen([1, 2, 3]) if flag else gen([9])):\n"
               "        total += v\n"
               "    return total\n"
               "def main() -> None:\n    print(f(True))\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("foreach.ifexpr_iterable", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("auto __src_0 = ((flag) ? "
                "(__tmp_1.emplace(std::vector<int64_t>{1, 2, 3}), "
                "gen((*__tmp_1))) : "
                "(__tmp_2.emplace(std::vector<int64_t>{9}), "
                "gen((*__tmp_2))));" in cpp)

    def test_iterator_ternary_lvalue_arm_stays_ast(self):
        # BOUNDARY: an LVALUE arm (a user-iterator NAME) needs the alias
        # capture the rvalue ternary cannot carry -- keeps rejecting.
        src = ("from tpy import Int32\n"
               "class It:\n"
               "    i: Int32\n"
               "    def __init__(self):\n        self.i = 0\n"
               "    def __iter__(self) -> 'It':\n        return self\n"
               "    def __next__(self) -> Int32 | None:\n"
               "        if self.i >= 2:\n            return None\n"
               "        self.i += 1\n        return self.i\n"
               "def f(flag: bool) -> None:\n"
               "    a = It()\n"
               "    b = It()\n"
               "    for v in (a if flag else b):\n"
               "        print(v)\n"
               "f(True)\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:iter.user_iterator.if_expr")

    def test_comp_ternary_arm_routes_with_degrade(self):
        # The comp's per-iteration element flush RELOCATES the deferred
        # temp into the loop body (the eager-optional degrade form).
        src = ("from tpy import Int32\n"
               "class Probe:\n"
               "    tag: Int32\n"
               "    def __init__(self, t: Int32):\n        self.tag = t\n"
               "def take(p: Probe | None) -> Int32:\n"
               "    return p.tag if p is not None else 0\n"
               "def f(cond: bool, xs: list[Int32]) -> Int32:\n"
               "    ys = [take(Probe(i)) for i in xs] if cond else [0]\n"
               "    return len(ys)\n"
               "def main() -> None:\n    print(f(True, [1, 2]))\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("ifexpr.container_comp_arm", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::optional<Probe> __tmp_1 = Probe(i);" in cpp
        assert "std::vector<int32_t>{0}" in cpp

    def test_comp_ternary_name_arm_stays_out(self):
        # BOUNDARY: a NAME arm next to a comp arm is outside the all-rvalue
        # rung (and outside the all-NAME alias rung) -- keeps rejecting.
        src = ("from tpy import Int32\n"
               "def f(cond: bool, xs: list[Int32]) -> Int32:\n"
               "    ys = [i + 1 for i in xs] if cond else xs\n"
               "    return len(ys)\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.list_comprehension")

    def test_const_ctor_slot_keeps_inline_brace(self):
        # BOUNDARY: a NON-mutated ctor slot binds the prvalue inline
        # (`Holder({1, 2, 3}).n`) -- the hoist row must not claim it.
        src = ("from tpy import Int64\n"
               "class Holder:\n"
               "    n: Int64\n"
               "    def __init__(self, o: list[Int64]):\n"
               "        self.n = len(o)\n"
               "def f(flag: bool) -> Int64:\n"
               "    return Holder([1, 2, 3]).n if flag else 0\n"
               "def main() -> None:\n    print(f(True))\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "Holder({1, 2, 3}).n" in cpp
        assert "std::optional" not in cpp

    def test_sized_comp_arg_inlines(self):
        # `len([comp])`: the Sized slot rides the same inline stmt-expr
        # render as Iterable/Sequence.
        src = ("from tpy import Int32\n"
               "def f(cond: bool, xs: list[Int32]) -> bool:\n"
               "    return cond and len([i + 1 for i in xs]) > 0\n"
               "def main() -> None:\n    print(f(True, [1]))\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("arg.native_comprehension", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_filtered_comp_element_temp_flushes_inside_if(self):
        # The FILTERED flavor: element temps flush INSIDE the `if` at the
        # innermost indent, after the filter passes (the AST's
        # _emit_iter_temps placement) -- the one flavor no corpus case
        # exercises.
        src = ("from tpy import Int32\n"
               "class Probe:\n"
               "    tag: Int32\n"
               "    def __init__(self, t: Int32):\n        self.tag = t\n"
               "def take(p: Probe | None) -> Int32:\n"
               "    return p.tag if p is not None else 0\n"
               "def f(xs: list[Int32]) -> Int32:\n"
               "    ys = [take(Probe(i)) for i in xs if i > 0]\n"
               "    return len(ys)\n"
               "def main() -> None:\n    print(f([1, -2, 3]))\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("            if ((i > 0)) {\n"
                "                Probe __tmp_1 = Probe(i);\n"
                "                __result.push_back(take(&(__tmp_1)));"
                in cpp)

    def test_value_select_lhs_temp_keeps_rejecting(self):
        # BOUNDARY: the value-select LHS always evaluates and gets NO
        # grant -- a temp-needing LHS falls back rather than deferring.
        src = ("from tpy import Int64\n"
               "class Holder:\n"
               "    n: Int64\n"
               "    def __init__(self, o: list[Int64]):\n"
               "        o.append(4)\n"
               "        self.n = o[0]\n"
               "def f() -> Int64:\n"
               "    return Holder([1, 2, 3]).n or 0\n"
               "def main() -> None:\n    print(f())\nmain()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.container")

    def test_mixed_iterator_ternary_arm_stays_ast(self):
        # BOUNDARY: a ternary mixing an iterator CALL arm with a
        # gen-valued NAME arm is outside the all-rvalue-call rung (sema
        # rejects a call-vs-container mix outright, so the name arm is the
        # closest compilable neighbor).
        src = ("from tpy import Int64\n"
               "from typing import Iterator\n"
               "def gen(o: list[Int64]) -> Iterator[Int64]:\n"
               "    for x in o:\n        yield x\n"
               "def f(flag: bool) -> Int64:\n"
               "    it = gen([9])\n"
               "    total = Int64(0)\n"
               "    for v in (gen([1]) if flag else it):\n"
               "        total += v\n"
               "    return total\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:iter.user_iterator.if_expr")

    def test_set_comp_ternary_arm_stays_out(self):
        # BOUNDARY: the comp-arm ternary rung is LIST-only until a set/dict
        # witness exists.
        src = ("from tpy import Int32\n"
               "def f(cond: bool, xs: list[Int32]) -> Int32:\n"
               "    ys = {i + 1 for i in xs} if cond else {0}\n"
               "    return len(ys)\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.set_comprehension")

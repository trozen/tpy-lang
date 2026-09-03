"""Pins for the comprehension ELEMENT arm of the container-literal gate
(the asdict recursion's `{"vertices": [<expansion> for p in ..]}`): a
list/dict comp at a matching container element slot lowers through the
pre-existing render arm (the `({...})` stmt-expr, target-typed).
Boundary: a set comp keeps rejecting (unwitnessed kind)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = "from tpy import Int32\n"


class TestCompElement:
    def test_list_comp_value_routes(self):
        src = _HDR + (
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2, 3]\n"
            "    d = {'squares': [x * x for x in xs]}\n"
            "    print(d)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["comp.nested"] >= 1
        _assert_byte_identical(src)

    def test_dict_comp_value_routes(self):
        src = _HDR + (
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2]\n"
            "    d = {'m': {x: x * 2 for x in xs}}\n"
            "    print(d)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["comp.nested"] >= 1
        _assert_byte_identical(src)

    def test_set_comp_value_still_defers(self):
        # The unwitnessed kind: a set comp at a set element slot keeps
        # rejecting; identity via fallback.
        src = _HDR + (
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2]\n"
            "    d = {'s': {x * 2 for x in xs}}\n"
            "    print(d)\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.container_literal")


class TestLiteralIterableComp:
    def test_list_literal_iterable_routes(self):
        # A LIST-LITERAL comp source captures the braced init-list itself
        # (`auto __obj_N = {"a", "bb"};` -- no container spelling) and
        # iterates begin/end with the sized reserve.
        src = _HDR + (
            "def main() -> None:\n"
            "    r = [len(s) for s in ['a', 'bb', 'ccc']]\n"
            "    print(r)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_loop_var_shadowing_global_slot_still_defers(self):
        # The loop var shadowing a pointer-slot GLOBAL keeps rejecting:
        # the body's reads of that name would need the render-state
        # save/restore the comp slice does not carry (the shadow fence).
        src = _HDR + (
            "x = [10, 20]\n"
            "def main() -> None:\n"
            "    r = [len(x) for x in ['a', 'bb']]\n"
            "    print(r, len(x))\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.list_comp")


class TestOwnedElemTempFlush:
    """Owned move-temps in the array_from_index lambda and the genexpr
    make_generator lambda: the element (and filter condition) are flush
    positions -- the emit drains the banked temps into the lambda body
    per iteration, before the return/yield."""

    _BOX = (
        "from tpy import Int32\n"
        "from tplib.box import Box\n"
        "def score(b: Box[Int32]) -> Int32:\n"
        "    return 1 if b < Box(3) else 0\n")

    def test_array_range_elem_temp_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = self._BOX + (
            "def main() -> None:\n"
            "    xs = [Box(i) for i in range(4)]\n"
            "    print(len(xs), xs[0].get())\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("comp.array_range")
        _assert_routes_byte_identical(src)

    def test_genexpr_elem_and_cond_temps_route(self):
        from .testutil import _assert_routes_byte_identical
        src = self._BOX + (
            "def is_small(b: Box[Int32]) -> bool:\n"
            "    return b < Box(3)\n"
            "def main(n: Int32) -> None:\n"
            "    print(sum(score(Box(i)) for i in range(n)"
            " if is_small(Box(i))))\n"
            "main(6)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("genexpr.range")
        _assert_routes_byte_identical(src)

    def test_genexpr_name_source_elem_temp_routes(self):
        # The non-range flavor: same yield_lines flush, container NAME
        # source.
        from .testutil import _assert_routes_byte_identical
        src = self._BOX + (
            "def go(nums: list[Int32]) -> None:\n"
            "    print(sum(score(Box(v)) for v in nums))\n"
            "def main() -> None:\n"
            "    ns: list[Int32] = [1, 2, 5]\n"
            "    go(ns)\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "go") is not None
        assert faces.get("genexpr.native_iterable")
        _assert_routes_byte_identical(src)

    def test_array_source_elem_temp_still_defers(self):
        # BOUNDARY: the array_SOURCE arm's element has no flush window in
        # its emit -- a temp-needing element keeps rejecting.
        src = (
            "from tpy import Int32, Array\n"
            "from tplib.box import Box\n"
            "def main() -> None:\n"
            "    src: Array[Int32, 3] = [1, 2, 3]\n"
            "    xs: Array[Box[Int32], 3] = [Box(v) for v in src]\n"
            "    print(len(xs), xs[0].get())\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_scalar")

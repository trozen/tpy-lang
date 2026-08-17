"""Pins for the comprehension ELEMENT arm of the container-literal gate
(the asdict recursion's `{"vertices": [<expansion> for p in ..]}`): a
list/dict comp at a matching container element slot lowers through the
pre-existing render arm (the `({...})` stmt-expr, target-typed).
Boundary: a set comp keeps rejecting (unwitnessed kind)."""

from __future__ import annotations

from .testutil import (
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
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        _assert_byte_identical(src)


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
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        _assert_byte_identical(src)

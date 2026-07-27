"""A whole value-repr `Optional[T]` name passed into a matching value-repr
`Optional[T]` param slot: `std::optional<T>` is a value passed BY VALUE, so
the name renders bare on both paths."""

from __future__ import annotations

from .testutil import _lower_ctx, _fn, _assert_byte_identical

_EQ = (
    "from tpy import Char\n"
    "def eq_left(o: Char | None, c: Char) -> bool:\n"
    "    return o == c\n"
)


class TestValueOptPassThroughArg:
    def test_matching_optional_name_passes_bare(self):
        src = (_EQ
               + "def use(some: Char | None, a: Char) -> bool:\n"
               + "    return eq_left(some, a)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_top_level_global_source_passes_bare(self):
        src = (_EQ
               + "a: Char = \"a\"\n"
               + "some_a: Char | None = a\n"
               + "print(eq_left(some_a, a))\n")
        # `_lower_ctx` calls `lower_module` without the generator's global
        # map, so `top_level` is always None here -- the ROUTING proof for
        # this shape is the flipped corpus case
        # `none_safety/optional_char_eq_safe` (unmarked => the ratchet fails
        # if its module init falls back). Byte identity is what this pin adds.
        _assert_byte_identical(src)


class TestValueOptArgBoundaries:
    def test_narrowed_name_still_passes_the_whole_optional(self):
        # A NARROWED source is still passed WHOLE at an Optional slot -- the
        # AST's gen_call_arg derefs only for a non-optional slot -- so the
        # name arm's deref-on-narrow is stripped. Pinned by byte identity
        # because the deref would be silent otherwise.
        src = (_EQ
               + "def use(some: Char | None, a: Char) -> bool:\n"
               + "    if some is not None:\n"
               + "        return eq_left(some, a)\n"
               + "    return False\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_pointer_repr_optional_stays_on_its_own_row(self):
        # A record inner is POINTER-repr: its arg face is the `T*` lift, not
        # this by-value pass-through, so the exact-repr pin must hold.
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def takes(o: P | None) -> bool:\n"
               "    return o is None\n"
               "def use(p: P | None) -> bool:\n"
               "    return takes(p)\n")
        thir = _lower_ctx(src)
        # Routed or not, it must NOT be this row that admits it -- the
        # pointer-repr lift owns that slot.
        fn = _fn(thir, "use")
        if fn is not None:
            _assert_byte_identical(src)

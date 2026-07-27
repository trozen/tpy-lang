"""Ctor MIL cells at a value-repr `Optional[scalar]` field: the same-typed
optional copy and the bare INNER scalar the converting ctor absorbs."""

from __future__ import annotations

from .testutil import _lower_ctor, _assert_byte_identical


class TestValueOptScalarMil:
    def test_inner_scalar_param_absorbs(self):
        # `self.value = value` at an `Int32 | None` field from an `Int32`
        # param: `std::optional<int32_t>`'s converting ctor absorbs it, so
        # the MIL cell is the bare `value(value)` -- no wrap on either path.
        src = ("from tpy import Int32\n"
               "class Box:\n"
               "    value: Int32 | None\n"
               "    def __init__(self, value: Int32) -> None:\n"
               "        self.value = value\n")
        assert _lower_ctor(src, "Box") is not None
        _assert_byte_identical(src)

    def test_same_typed_optional_param_still_copies(self):
        # The pre-existing row: an `Int32 | None` param into the same slot.
        src = ("from tpy import Int32\n"
               "class Box:\n"
               "    value: Int32 | None\n"
               "    def __init__(self, value: Int32 | None) -> None:\n"
               "        self.value = value\n")
        assert _lower_ctor(src, "Box") is not None
        _assert_byte_identical(src)

    def test_widening_inner_mismatch_stays_ast(self):
        # A DIFFERENT scalar (Int64 param into an `Int32 | None` field)
        # carries an int conversion the bare render does not spell, so the
        # exact-inner pin must keep it out.
        src = ("from tpy import Int32, Int64\n"
               "class Box:\n"
               "    value: Int32 | None\n"
               "    def __init__(self, value: Int64) -> None:\n"
               "        self.value = Int32(value)\n")
        assert _lower_ctor(src, "Box") is None

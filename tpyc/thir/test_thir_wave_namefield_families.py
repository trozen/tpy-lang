"""Bare-name field writes at the Array and Callable families, and the
`Own[...]` param unwrap that the container row was missing."""

from __future__ import annotations

from .testutil import _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical


class TestNameFieldWriteFamilies:
    def test_own_container_param_moves(self):
        # An `Own[list[T]]` param binds the same storage the field wants; the
        # wrapper only decides whether the tail MOVES at last use
        # (`this->_items = std::move(v);`). Without the unwrap the row saw an
        # OwnType and rejected a shape the AST renders bare-or-moved.
        src = ("from tpy import Int32, Own\n"
               "class C:\n"
               "    _items: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._items = []\n"
               "    def set(self, v: Own[list[Int32]]) -> None:\n"
               "        self._items = v\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("field_write.container_name", 0) >= 1
        _assert_byte_identical(src)

    def test_array_param_copies_bare(self):
        src = ("from tpy import Int32, Array\n"
               "class C:\n"
               "    data: Array[Int32, 3]\n"
               "    def __init__(self, data: Array[Int32, 3]) -> None:\n"
               "        self.data = data\n"
               "    def set(self, arr: Array[Int32, 3]) -> None:\n"
               "        self.data = arr\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "set") is not None
        _assert_byte_identical(src)



class TestNameFieldWriteBoundaries:
    def test_callable_field_stays_ast(self):
        # A `Callable` field is NOT in the row: `std::function` has no
        # borrow->storage convert arm, so admitting it would CRASH the
        # emitter (`unhandled THIRFormConvert: CallableType`) instead of
        # falling back. Verified during this cell -- do not add it without
        # the convert arm.
        src = ("from typing import Callable\n"
               "class C:\n"
               "    cb: Callable[[], None]\n"
               "    def __init__(self, cb: Callable[[], None]) -> None:\n"
               "        self.cb = cb\n"
               "    def set(self, f: Callable[[], None]) -> None:\n"
               "        self.cb = f\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "set") is None

    def test_optional_callable_field_stays_ast(self):
        # `Callable | None` is the OPTIONAL family, not the callable one --
        # its store goes through the optional arms, so this row must not
        # capture it.
        src = ("from typing import Callable, Optional\n"
               "class C:\n"
               "    cb: Optional[Callable[[], None]]\n"
               "    def __init__(self) -> None:\n"
               "        self.cb = None\n"
               "    def set(self, f: Callable[[], None]) -> None:\n"
               "        self.cb = f\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "set") is None

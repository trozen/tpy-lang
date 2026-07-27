"""Base-init args that render as the bare param name: a builtin container,
an open type param, and an owned `String`."""

from __future__ import annotations

from .testutil import _lower_ctor, _assert_byte_identical


class TestBareNameBaseInitArgs:
    def test_container_param_passes_bare(self):
        src = ("class Parent:\n"
               "    store: dict[str, str]\n"
               "    def __init__(self, store: dict[str, str]) -> None:\n"
               "        self.store = store\n"
               "class Child(Parent):\n"
               "    def __init__(self, store: dict[str, str]) -> None:\n"
               "        super().__init__(store)\n")
        assert _lower_ctor(src, "Child") is not None
        _assert_byte_identical(src)

    def test_open_type_param_passes_bare(self):
        src = ("class Base[T]:\n"
               "    val: T\n"
               "    def __init__(self, val: T):\n"
               "        self.val = val\n"
               "class Child[T](Base[T]):\n"
               "    def __init__(self, val: T):\n"
               "        super().__init__(val)\n")
        assert _lower_ctor(src, "Child") is not None
        _assert_byte_identical(src)

    def test_owned_string_param_passes_bare(self):
        # `String` is deliberately outside `_resolved_str_value` (the VIEW
        # family predicate), so it needed its own row.
        src = ("from tpy import String\n"
               "class Parent:\n"
               "    msg: String\n"
               "    def __init__(self, msg: String) -> None:\n"
               "        self.msg = msg\n"
               "class Child(Parent):\n"
               "    def __init__(self, msg: String) -> None:\n"
               "        super().__init__(msg)\n")
        assert _lower_ctor(src, "Child") is not None
        _assert_byte_identical(src)


class TestBaseInitArgBoundaries:
    def test_field_read_arg_stays_ast(self):
        # Only declared PARAM names render bare with no temp; a deeper
        # lvalue is outside the row (the cell has no flush point).
        src = ("class Src:\n"
               "    store: dict[str, str]\n"
               "    def __init__(self, store: dict[str, str]) -> None:\n"
               "        self.store = store\n"
               "class Parent:\n"
               "    store: dict[str, str]\n"
               "    def __init__(self, store: dict[str, str]) -> None:\n"
               "        self.store = store\n"
               "class Child(Parent):\n"
               "    def __init__(self, s: Src) -> None:\n"
               "        super().__init__(s.store)\n")
        assert _lower_ctor(src, "Child") is None

    def test_container_expression_arg_stays_ast(self):
        # A non-NAME container arg could register a temp; the cell has no
        # flush point, so only bare names are admitted.
        src = ("class Parent:\n"
               "    store: dict[str, str]\n"
               "    def __init__(self, store: dict[str, str]) -> None:\n"
               "        self.store = store\n"
               "class Child(Parent):\n"
               "    def __init__(self) -> None:\n"
               "        super().__init__({})\n")
        assert _lower_ctor(src, "Child") is None

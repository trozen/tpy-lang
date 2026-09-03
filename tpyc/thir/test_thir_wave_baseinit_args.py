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


def _hpp(src: str) -> str:
    from ..codegen_cpp.context import CodeGenOptions
    from .testutil import _compile, _entry
    compiler, modules = _compile(src)
    hpp, _cpp = compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False))
    return hpp


class TestNoneSlotSpelling:
    """A `None` base-init arg spelled from its SLOT via the shared
    `default_to_cpp` renderer: `{}` for a variant slot, `std::nullopt` for
    a value optional (both dualgen-probed when the row landed; the variant
    slot's corpus witness is defaults/baseinit_union_none)."""

    def test_variant_slot_spells_braces(self):
        src = ("from tpy import Int64\n"
               "class Cat:\n"
               "    def __init__(self) -> None:\n        pass\n"
               "class Dog:\n"
               "    def __init__(self) -> None:\n        pass\n"
               "class Base:\n"
               "    has_pet: bool\n"
               "    def __init__(self, tag: Int64,"
               " pet: Cat | Dog | None = None) -> None:\n"
               "        self.has_pet = pet is not None\n"
               "class Sub(Base):\n"
               "    def __init__(self, tag: Int64) -> None:\n"
               "        super().__init__(tag, None)\n")
        assert _lower_ctor(src, "Sub") is not None
        hpp = _hpp(src)
        assert "Base(tag, {})" in hpp
        _assert_byte_identical(src)

    def test_pointer_repr_optional_slot_spells_nullptr(self):
        src = ("from tpy import Int32\n"
               "class Pet:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Base:\n"
               "    has_pet: bool\n"
               "    def __init__(self, tag: Int32,"
               " pet: Pet | None = None) -> None:\n"
               "        self.has_pet = pet is not None\n"
               "class Sub(Base):\n"
               "    def __init__(self, tag: Int32) -> None:\n"
               "        super().__init__(tag, None)\n")
        assert _lower_ctor(src, "Sub") is not None
        hpp = _hpp(src)
        assert "Base(tag, nullptr)" in hpp
        _assert_byte_identical(src)

    def test_value_optional_slot_spells_nullopt(self):
        src = ("from tpy import Int32\n"
               "class Base:\n"
               "    tag: Int32\n"
               "    n: Int32 | None\n"
               "    def __init__(self, tag: Int32,"
               " n: Int32 | None = None) -> None:\n"
               "        self.tag = tag\n"
               "        self.n = n\n"
               "class Sub(Base):\n"
               "    def __init__(self, tag: Int32) -> None:\n"
               "        super().__init__(tag, None)\n")
        assert _lower_ctor(src, "Sub") is not None
        hpp = _hpp(src)
        assert "Base(tag, std::nullopt)" in hpp
        _assert_byte_identical(src)

"""The empty-container ctor call in a member-init cell:
`self.items = list()` -> `items(std::vector<T>())`."""

from __future__ import annotations

from .testutil import _lower_ctor, _ctor_tail, _assert_byte_identical


class TestMilContainerDefault:
    def test_list_dict_set_default_construct(self):
        src = ("from tpy import Int32\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    data: dict[str, Int32]\n"
               "    seen: set[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = list()\n"
               "        self.data = dict()\n"
               "        self.seen = set()\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        # The spelling comes from the FIELD type, not from the call.
        assert "std::vector<int32_t>()" in _ctor_tail(ctor)
        _assert_byte_identical(src)

    def test_array_default_constructs_too(self):
        src = ("from tpy import Int32, Array\n"
               "class C:\n"
               "    data: Array[Int32, 3]\n"
               "    def __init__(self) -> None:\n"
               "        self.data = Array()\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        _assert_byte_identical(src)


class TestMilContainerDefaultBoundaries:
    def test_argumentful_call_stays_ast(self):
        # Any argument makes it a range/iterable construction with its own
        # render -- the zero-arg pin keeps those out.
        src = ("from tpy import Int32\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, src: list[Int32]) -> None:\n"
               "        self.items = list(src)\n")
        assert _lower_ctor(src, "C") is None

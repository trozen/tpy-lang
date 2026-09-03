"""User-record `__setitem__` writes whose VALUE slot is a record: the
`Own[T]` slot's move source and ctor rvalue, the plain `T` slot's bare copy,
and the boundary the `Own[T]` slot keeps rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
    _reject_tally,
)

_ARRAYLIST_SRC = (
    "from tpy import Int32\n"
    "from tplib import ArrayList\n"
    "class Data:\n"
    "    value: Int32\n"
    "    def __init__(self, v: Int32 = 0):\n"
    "        self.value = v\n"
    "def a_move(l: ArrayList[Data, 8]) -> None:\n"
    "    z = Data()\n"
    "    z.value = 5\n"
    "    l[0] = z\n"
    "def a_rvalue(l: ArrayList[Data, 8]) -> None:\n"
    "    l[0] = Data(7)\n"
    "def main() -> None:\n"
    "    lst = ArrayList[Data, 8]()\n"
    "    lst.append(Data(1))\n"
    "    a_move(lst)\n"
    "    a_rvalue(lst)\n"
    "    print(lst[0].value)\n"
    "main()\n"
)

# The plain (non-Own) `T` value slot: `__setitem__` binds `const Data&`, so a
# copy-shaped name is a legal source there.
_PLAIN_SLOT_SRC = (
    "from tpy import Int32\n"
    "class Data:\n"
    "    value: Int32\n"
    "    def __init__(self, v: Int32 = 0):\n"
    "        self.value = v\n"
    "class Slots:\n"
    "    a: Data\n"
    "    b: Data\n"
    "    def __init__(self) -> None:\n"
    "        self.a = Data(0)\n"
    "        self.b = Data(0)\n"
    "    def __setitem__(self, index: Int32, value: Data) -> None:\n"
    "        if index == 0:\n"
    "            self.a = value\n"
    "        else:\n"
    "            self.b = value\n"
    "    def __getitem__(self, index: Int32) -> Data:\n"
    "        if index == 0:\n"
    "            return self.a\n"
    "        return self.b\n"
    "def fill(s: Slots) -> None:\n"
    "    z = Data(5)\n"
    "    s[0] = z\n"
    "    print(z.value)\n"
    "def main() -> None:\n"
    "    s = Slots()\n"
    "    fill(s)\n"
    "    print(s.a.value)\n"
    "main()\n"
)


class TestRecordSetItemValue:
    def test_own_slot_move_and_rvalue_route(self):
        thir, faces = _lower_ctx_witnessed(_ARRAYLIST_SRC)
        assert _fn(thir, "a_move") is not None
        assert _fn(thir, "a_rvalue") is not None
        assert faces["setitem.record_move"] >= 1
        assert faces["setitem.user_record"] >= 1

    def test_own_slot_renders_move_and_prvalue(self):
        cpp = _assert_routes_byte_identical(_ARRAYLIST_SRC)[1]
        assert "::tpy::__setitem__(l, 0, std::move(z));" in cpp
        assert "::tpy::__setitem__(l, 0, Data(7));" in cpp

    def test_plain_slot_copies_name_bare(self):
        thir, faces = _lower_ctx_witnessed(_PLAIN_SLOT_SRC)
        assert _fn(thir, "fill") is not None
        assert faces["setitem.user_record"] >= 1
        cpp = _assert_routes_byte_identical(_PLAIN_SLOT_SRC)[1]
        assert "::tpy::__setitem__(s, 0, z);" in cpp

    def test_own_slot_copy_shaped_name_rejects(self):
        # The adjacent shape: the name is read again after the write, so it is
        # not a move source. An `Own[T]` value slot is a `V&&` sink with no
        # binding for an lvalue, so the bare forward would not compile.
        src = _ARRAYLIST_SRC.replace(
            "def a_rvalue(l: ArrayList[Data, 8]) -> None:\n"
            "    l[0] = Data(7)\n",
            "def a_copy(l: ArrayList[Data, 8]) -> None:\n"
            "    z = Data()\n"
            "    l[0] = z\n"
            "    print(z.value)\n").replace("    a_rvalue(lst)\n",
                                            "    a_copy(lst)\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.assign",
                           shape="setitem.record_own_copy")

    def test_narrowed_optional_record_value_rejects(self):
        # The adjacent SOURCE shape: a None-narrowed name reads through a
        # deref, which the bare forward into the value slot does not spell.
        src = ("from tpy import Int32\n"
               "from tplib import ArrayList\n"
               "class Data:\n    value: Int32\n"
               "    def __init__(self, v: Int32 = 0):\n        self.value = v\n"
               "def store(l: ArrayList[Data, 8], z: Data | None) -> None:\n"
               "    if z is not None:\n"
               "        l[0] = z\n"
               "def main() -> None:\n"
               "    lst = ArrayList[Data, 8]()\n    lst.append(Data(1))\n"
               "    store(lst, Data(5))\n    print(lst[0].value)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.assign",
                           shape="setitem.family")

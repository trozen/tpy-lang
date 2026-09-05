"""A method call filling a by-value container return slot.

The storage return already forwarded a free call bare
(`return make_list(n);`). Its method-call twin (`return p.split('/');`)
is the same bare render, and rides the same reference-slot ladder arm the
record half does (`ret.record_methodcall`).
"""

from .testutil import _assert_routes_byte_identical, _lower_ctx_witnessed

_HOLDER = (
    "from tpy import Int32, Own\n"
    "class Holder:\n"
    "    items: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.items = []\n"
    "    def borrow(self) -> list[Int32]:\n"
    "        return self.items\n"
    "    def make(self) -> Own[list[Int32]]:\n"
    "        out: list[Int32] = []\n"
    "        out.append(1)\n"
    "        return out\n"
)


class TestStrSplitAtStorageSlotRoutes:
    # The stdlib witness shape (`urllib.parse.url_split_path_only`): a
    # builtin-receiver method whose result IS the slot's vector.
    SRC = (
        "from tpy import Own\n"
        "def seg(p: str) -> Own[list[str]]:\n"
        "    return p.split('/')\n"
        "def main() -> None:\n"
        "    print(len(seg('a/b')))\n"
        "main()\n"
    )

    def test_routes_with_face(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("ret.record_methodcall", 0) >= 1

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return ::tpy::str_split(p, \"/\");" in cpp


class TestUserMethodAndDictSetSlotsRoute:
    # The user-record receiver over all three container flavors, plus a
    # method call on a constructor temporary (the `re.findall` shape).
    SRC = (
        _HOLDER +
        "class Maps:\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def d(self) -> Own[dict[str, Int32]]:\n"
        "        out: dict[str, Int32] = {}\n"
        "        return out\n"
        "    def s(self) -> Own[set[Int32]]:\n"
        "        out: set[Int32] = set()\n"
        "        return out\n"
        "def take_list() -> Own[list[Int32]]:\n"
        "    return Holder().make()\n"
        "def take_dict(m: Maps) -> Own[dict[str, Int32]]:\n"
        "    return m.d()\n"
        "def take_set(m: Maps) -> Own[set[Int32]]:\n"
        "    return m.s()\n"
        "def main() -> None:\n"
        "    m = Maps()\n"
        "    print(len(take_list()), len(take_dict(m)), len(take_set(m)))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return Holder().make();" in cpp
        assert "return m.d();" in cpp

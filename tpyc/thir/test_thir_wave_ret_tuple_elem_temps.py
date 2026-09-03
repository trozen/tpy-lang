"""Arg-temp hoists inside a RETURNED value-tuple literal's elements.

`return (True, f(x))` is a statement flush point, so an element call whose
arg row hoists a `__tmp_N` lands that decl ahead of the return -- the same
grant the generic return tail already carries, and the one the borrow-tuple
sibling spells with its own element-temps parameter. The grant is per
CALLER: `_lower_tuple_literal`'s other positions have no flush point, so it
defaults off, and a NESTED tuple element keeps rejecting. Corpus witness:
`tplib.requests._parse_http_date` (`return (True,
dt.replace(tzinfo=UTC).timestamp())`)."""

from .testutil import (
    _reject_tally, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


_SRC = ("from tpy import Int32, Own, StrView\n"
        "class Pet:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "class Dog(Pet):\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        super().__init__(n)\n"
        "def take_union(u: Int32 | StrView | None) -> Int32:\n"
        "    return 0\n"
        "def take_list(xs: list[Int32]) -> Int32:\n"
        "    return len(xs)\n"
        "def take_own(p: Own[Pet]) -> Int32:\n"
        "    return p.n\n"
        "def take_pet(p: Pet) -> Int32:\n"
        "    return p.n\n")


def _reject_tags(src: str):
    return _reject_tally(src)


class TestReturnTupleElemTemps:
    def test_union_temp_element_routes(self):
        src = (_SRC
               + "def ret(k: Int32) -> tuple[bool, Int32]:\n"
               + "    return (True, take_union(k))\n"
               + "def main() -> None:\n"
               + "    print(ret(3)[1])\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert ("std::variant<std::monostate, int32_t, std::string_view> "
                "__tmp_1 = k;") in out
        assert "return std::tuple<bool, int32_t>{true, take_union(__tmp_1)};" in out

    def test_container_literal_element_routes(self):
        src = (_SRC
               + "def ret() -> tuple[bool, Int32]:\n"
               + "    return (True, take_list([1, 2, 3]))\n"
               + "def main() -> None:\n"
               + "    print(ret()[1])\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "std::vector<int32_t> __tmp_1 = {1, 2, 3};" in out

    def test_own_copy_temp_element_routes(self):
        # A non-last-use lvalue at an Own slot copies into a temp and moves
        # -- the row whose omission would silently drop the copy.
        src = (_SRC
               + "def ret(p: Pet) -> tuple[Int32, Int32]:\n"
               + "    return (take_own(p), p.n)\n"
               + "def main() -> None:\n"
               + "    p = Pet(1)\n"
               + "    a, b = ret(p)\n"
               + "    print(a + b)\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "auto __tmp_1 = p;" in out
        assert "take_own(std::move(__tmp_1))" in out

    def test_covariant_temp_element_routes(self):
        src = (_SRC
               + "def ret() -> tuple[bool, Int32]:\n"
               + "    return (True, take_pet(Dog(6)))\n"
               + "def main() -> None:\n"
               + "    print(ret()[1])\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "Dog __tmp_1 = Dog(6);" in out

    def test_two_temps_in_one_tuple_keep_their_numbering(self):
        # Two hoisting elements in one returned literal: the numbering is
        # what a per-element grant most easily gets wrong.
        src = (_SRC
               + "def ret(k: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (take_union(k), take_list([9]))\n"
               + "def main() -> None:\n"
               + "    print(ret(5)[0])\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "__tmp_1 = k;" in out
        assert "std::vector<int32_t> __tmp_2 = {9};" in out
        assert "{take_union(__tmp_1), take_list(__tmp_2)}" in out

    def test_temp_inside_a_receiver_subexpression_routes(self):
        # The corpus shape: the hoisting call is the RECEIVER of the
        # element's outer call, not the element itself.
        src = ("from tpy import Float64\n" + _SRC
               + "class Chain:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n"
               + "    def with_u(self, u: Int32 | StrView | None) -> Chain:\n"
               + "        return self\n"
               + "    def out(self) -> Float64:\n"
               + "        return Float64(self.v)\n"
               + "def ret(c: Chain, k: Int32) -> tuple[bool, Float64]:\n"
               + "    return (True, c.with_u(k).out())\n"
               + "def main() -> None:\n"
               + "    c = Chain(2)\n"
               + "    ok, v = ret(c, 8)\n"
               + "    print(v)\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "c.with_u(__tmp_1).out()" in out


class TestReturnTupleElemTempsBoundary:
    def test_nested_tuple_element_keeps_rejecting(self):
        # BOUNDARY: the grant is per CALLER and does not recurse -- a nested
        # tuple element's own hoisting call has no flush point of its own.
        src = (_SRC
               + "def ret(k: Int32) -> tuple[bool, tuple[Int32, Int32]]:\n"
               + "    return (True, (take_union(k), 1))\n"
               + "def main() -> None:\n"
               + "    print(ret(1)[1][0])\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.tuple_source:expr.call:call.arg_shape.union")

    def test_tuple_literal_decl_init_keeps_rejecting(self):
        # BOUNDARY: the same literal at a DECL init is a different caller of
        # the tuple lowerer and was not granted temps.
        src = (_SRC
               + "def use(k: Int32) -> Int32:\n"
               + "    t = (True, take_union(k))\n"
               + "    return t[1]\n"
               + "def main() -> None:\n"
               + "    print(use(3))\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.tuple_literal_shape:expr.call:call.arg_shape.union")

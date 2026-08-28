"""Pins for a value-repr `Optional[value tuple]` DECL slot.

`std::optional<std::tuple<...>>` is a by-value binding like the scalar and
enum inners already admitted, so the decl is the plain spelled copy and the
init reads its source as a whole optional.

The reads keep their own gates: the whole-optional ones (has_value test,
same-optional pass) and the narrowed `std::get<N>((*t))` route; a REASSIGNED
target does not, because the reassign sink asks whether the target is a
registered value-optional binding and this family has no registration."""

from __future__ import annotations

from .testutil import (_assert_routes_byte_identical,
                       _lower_ctx_witnessed)


class TestValueOptTupleDeclSlot:
    def test_value_opt_tuple_slot_routes(self):
        src = (
            "from tpy import Int32\n"
            "def use(a: tuple[Int32, Int32] | None) -> Int32:\n"
            "    if a is None:\n"
            "        return 0\n"
            "    return 1\n"
            "def outer(auth: tuple[Int32, Int32] | None) -> Int32:\n"
            "    local = auth\n"
            "    return use(local)\n"
            "def main() -> None:\n"
            "    print(outer((1, 2)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("std::optional<std::tuple<int32_t, int32_t>> local = auth;"
                in hpp + cpp)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["decl.value_opt_tuple_slot"] >= 1

    def test_owned_element_tuple_slot_routes(self):
        # The owned-element flavor binds `std::tuple<std::string, ...>` by
        # value the same way; nothing about the copy depends on the elements.
        src = (
            "from tpy import Int32\n"
            "def outer(auth: tuple[str, str] | None) -> Int32:\n"
            "    local = auth\n"
            "    if local is None:\n"
            "        return 0\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    print(outer(('a', 'b')))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("std::optional<std::tuple<std::string, std::string>> local"
                " = auth;" in hpp + cpp)

    def test_narrowed_element_read_routes(self):
        src = (
            "from tpy import Int32\n"
            "def outer(auth: tuple[Int32, Int32] | None) -> Int32:\n"
            "    local = auth\n"
            "    if local is not None:\n"
            "        return local[0]\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(outer((1, 2)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return std::get<0>((*local));" in hpp + cpp

    def test_reassigned_value_opt_tuple_slot_routes(self):
        # The reassign sink takes the whole-optional copy for this family
        # too: the target is asked off its DECLARED type rather than the
        # binding registry, which this family deliberately stays out of (the
        # tuple kind admits only the whole-optional read).
        src = (
            "from tpy import Int32\n"
            "def use(a: tuple[Int32, Int32] | None) -> Int32:\n"
            "    if a is None:\n"
            "        return 0\n"
            "    return 1\n"
            "def outer(auth: tuple[Int32, Int32] | None,\n"
            "          fallback: tuple[Int32, Int32] | None) -> Int32:\n"
            "    local = auth\n"
            "    if local is None:\n"
            "        local = fallback\n"
            "    return use(local)\n"
            "def main() -> None:\n"
            "    print(outer((1, 2), (3, 4)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "local = fallback;" in hpp + cpp

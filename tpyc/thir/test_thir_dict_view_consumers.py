"""A dict VIEW result outside the for-head: `sorted(d.keys())` binds the
view rvalue bare into a native builtin's `Iterable[T]` slot, and `keys()` is
admitted value-family-blind (the view yields the KEY, so what the dict maps
to cannot change the render). `values()` / `items()` keep the value-family
rows, since their element IS the value."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical, _lower_ctx_witnessed,
    _thir_ctx,
)


class TestDictViewAtNativeIterableSlot:
    SRC = ("from tpy import Int32\n"
           "def f(d: dict[str, Int32], sort_keys: bool) -> Int32:\n"
           "    keys = sorted(d.keys()) if sort_keys else list(d.keys())\n"
           "    n = 0\n"
           "    for k in keys:\n"
           "        n += d[k]\n"
           "    return n\n"
           "def g(d: dict[str, Int32]) -> Int32:\n"
           "    return sorted(d.values())[0]\n"
           "def main() -> None:\n"
           '    d = {"b": 2, "a": 1}\n'
           "    print(f(d, True), f(d, False), g(d))\n"
           "main()\n")

    def test_sorted_over_keys_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "::tpy::builtin_sorted<std::string>(::tpy::dict_keys(d))" in cpp
        assert ("::tpy::construct<std::vector<std::string>>("
                "::tpy::dict_keys(d))") in cpp

    def test_len_over_a_view_still_routes(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32]) -> Int32:\n"
               "    return Int32(len(d.keys()))\n"
               "def main() -> None:\n"
               '    print(f({"a": 1}))\n'
               "main()\n")
        _, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__len__(::tpy::dict_keys(d))" in cpp

    def test_non_view_result_at_the_same_slot_is_untouched(self):
        # BOUNDARY: the slot's admission is what widened, not the result set
        # -- a plain container call rvalue at the same Iterable slot keeps
        # its own row and its own bare bind.
        src = ("from tpy import Int32, Own\n"
               "def mk() -> Own[list[Int32]]:\n"
               "    return [3, 1]\n"
               "def f() -> Int32:\n"
               "    return sorted(mk())[0]\n"
               "def main() -> None:\n"
               "    print(f())\n"
               "main()\n")
        _, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::builtin_sorted<int32_t>(mk())" in cpp


class TestDictKeysValueFamilyBlind:
    SRC = ("from tpy import Int32\n"
           "type JV = None | int | str | list[JV] | dict[str, JV]\n"
           "def f(v: JV) -> Int32:\n"
           "    match v:\n"
           "        case dict() as d:\n"
           "            return Int32(len(sorted(d.keys())))\n"
           "    return 0\n"
           "def main() -> None:\n"
           "    print(f(1))\n"
           "main()\n")

    def test_wrapper_union_valued_dict_keys_routes(self):
        # The dict maps to a recursive-alias wrapper union -- a family none
        # of the value-family rows name -- yet `keys()` renders and binds
        # identically, because the key is all the view yields.
        _, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["iter.dict_keys_value_blind"] >= 1
        _, cpp = _assert_routes_byte_identical(self.SRC)
        assert "::tpy::dict_keys(d)" in cpp

    def test_values_over_the_same_dict_keeps_rejecting(self):
        # BOUNDARY: `values()` yields the VALUE, so the value family is
        # exactly what decides -- the blind admission must not reach it.
        src = ("from tpy import Int32\n"
               "type JV = None | int | str | list[JV] | dict[str, JV]\n"
               "def f(v: JV) -> Int32:\n"
               "    n = 0\n"
               "    match v:\n"
               "        case dict() as d:\n"
               "            for w in d.values():\n"
               "                n += 1\n"
               "    return Int32(n)\n"
               "def main() -> None:\n"
               "    print(f(1))\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.for_each")

    def test_own_dict_binding_keeps_rejecting(self):
        # BOUNDARY: the shared container dispatch still refuses an
        # `Own[dict]` binding (its move-in ABI is a different shape), which
        # the key-only leg reuses rather than sidesteps.
        src = ("from tpy import Int32, Own\n"
               "def f(d: Own[dict[str, Int32]]) -> Int32:\n"
               "    return Int32(len(sorted(d.keys())))\n"
               "def main() -> None:\n"
               '    print(f({"a": 1}))\n'
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           shape="method.ret_type")

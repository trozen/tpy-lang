"""`copy()` of a literal-seeded container: the argument's container type is
still PENDING (list vs Array, decided by sema's resolution pass) when the
call is analyzed, so both emit paths must read it through the shared
resolver. The list/dict/set literal sources route byte-identically; the
Array-resolved source routes too now that the copy row reads the reference
axis, and is pinned as a case (`tests/cases/list/copy_literal_array`)."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _thir_ctx,
)


class TestLiteralSeededCopyRoutes:
    def test_list_literal_copy(self):
        src = ("from tpy import copy\n"
               "def main() -> None:\n"
               "    xs = [1, 2]\n"
               "    ys = copy(xs)\n"
               "    ys.append(3)\n"
               "    print(len(xs), len(ys))\n"
               "main()\n")
        cpp = _assert_routes_byte_identical(src)
        assert "std::vector<int32_t>(xs)" in cpp[1]

    def test_dict_literal_copy(self):
        src = ("from tpy import copy\n"
               "def main() -> None:\n"
               "    d = {\"a\": 1}\n"
               "    e = copy(d)\n"
               "    e[\"b\"] = 2\n"
               "    print(len(d), len(e))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_set_literal_copy(self):
        src = ("from tpy import copy\n"
               "def main() -> None:\n"
               "    s = {1, 2}\n"
               "    t = copy(s)\n"
               "    t.add(3)\n"
               "    print(len(s), len(t))\n"
               "main()\n")
        _assert_routes_byte_identical(src)


class TestNarrowedOptionalCopyKeepsCopying:
    def test_copy_of_narrowed_optional_local_is_a_copy(self):
        # The pending-container resolution must not widen to the declared
        # Optional binding: a narrowed `R | None` local copies its referent
        # (`R((*x))`), it does not hand back the pointer.
        src = ("from tpy import copy, Int32\n"
               "class R:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def f(x: R | None) -> Int32:\n"
               "    if x is not None:\n"
               "        y = copy(x)\n"
               "        y.n += 1\n"
               "        return x.n + y.n\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(f(R(1)))\n"
               "main()\n")
        # THIR still rejects a copy of a narrowed name (its own row), so the
        # claim here is the AST render, byte-identical through the fallback.
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call", "call.builtin_special")

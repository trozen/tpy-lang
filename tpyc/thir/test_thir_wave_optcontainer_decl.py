"""The container-payload half of the pointer-repr Optional borrow locals: a
`list`/`dict`/`set` inner takes the same `T* x = ::tpy::optional_to_ptr(fld)`
lift the record inner does (the AST decl arm has no record check -- it keys
only on `uses_pointer_repr()`), and the narrowed ptr-opt for-each carve-out
covers list/set beside dict (the AST loop is family-blind)."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)


def _field_decl_src(payload: str, init: str) -> str:
    return ("from tpy import Int32\n"
            "class H:\n"
            f"    items: {payload} | None\n"
            "    def __init__(self) -> None:\n"
            f"        self.items = {init}\n"
            "def f(h: H) -> Int32:\n"
            "    xs = h.items\n"
            "    if xs is None:\n"
            "        return Int32(0)\n"
            "    return Int32(len(xs))\n"
            "def main() -> None:\n"
            "    print(f(H()))\n"
            "main()\n")


class TestOptionalPtrContainerDecl:
    def test_list_payload_routes_and_witnesses(self):
        src = _field_decl_src("list[Int32]", "[Int32(1)]")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["decl.opt_ptr_container"] >= 1
        hpp, cpp = _assert_byte_identical(src)
        assert ("std::vector<int32_t>* xs = ::tpy::optional_to_ptr(h.items);"
                in hpp + cpp)

    def test_dict_payload_routes(self):
        src = _field_decl_src("dict[str, Int32]", "{\"a\": Int32(1)}")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["decl.opt_ptr_container"] >= 1
        _assert_byte_identical(src)

    def test_set_payload_routes(self):
        src = _field_decl_src("set[Int32]", "{Int32(1)}")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["decl.opt_ptr_container"] >= 1
        _assert_byte_identical(src)

    def test_array_payload_stays_ast(self):
        # `std::array` has no oracle witness at this slot and is excluded
        # deliberately -- the row lists list/dict/set, never a blanket
        # container test.
        src = ("from tpy import Int32, Array\n"
               "class H:\n"
               "    items: Array[Int32, 2] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.items = [Int32(1), Int32(2)]\n"
               "def f(h: H) -> Int32:\n"
               "    xs = h.items\n"
               "    if xs is None:\n"
               "        return Int32(0)\n"
               "    return Int32(len(xs))\n"
               "def main() -> None:\n"
               "    print(f(H()))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is None
        assert not faces.get("decl.opt_ptr_container")
        _assert_byte_identical(src)

    def test_own_container_optional_stays_off_the_row(self):
        # `Own[list[T]] | None` is VALUE-repr (`std::optional<std::vector<>>`),
        # so it never reaches the OPTIONAL_TO_PTR binding -- which is why the
        # row must not test through an Own-peeling container predicate.
        src = ("from tpy import Int32, Own\n"
               "def f(src: Own[list[Int32]] | None) -> Int32:\n"
               "    xs = src\n"
               "    if xs is None:\n"
               "        return Int32(0)\n"
               "    return Int32(len(xs))\n"
               "def main() -> None:\n"
               "    print(f([Int32(1)]))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert not faces.get("decl.opt_ptr_container")
        _assert_byte_identical(src)


class TestNarrowedOptContainerForEach:
    def test_list_iterable_routes_and_witnesses(self):
        src = ("from tpy import Int32\n"
               "def f(xs: list[Int32] | None) -> Int32:\n"
               "    n = 0\n"
               "    if xs is not None:\n"
               "        for v in xs:\n"
               "            n += v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f([Int32(1)]), f(None))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces["foreach.narrowed_opt_listset"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __src_0 = (*xs);" in hpp + cpp

    def test_set_iterable_routes_and_witnesses(self):
        src = ("from tpy import Int32\n"
               "def f(xs: set[Int32] | None) -> Int32:\n"
               "    n = 0\n"
               "    if xs is not None:\n"
               "        for v in xs:\n"
               "            n += v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({Int32(1)}), f(None))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces["foreach.narrowed_opt_listset"] >= 1
        _assert_routes_byte_identical(src)

    def test_dict_iterable_keeps_its_own_face(self):
        # Separate faces per family: a merged one would read as witnessed off
        # the dict traffic alone if the list/set leg regressed.
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> int:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        for k in d:\n"
               "            n += len(k)\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}), f(None))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces["foreach.narrowed_opt_dict"] >= 1
        assert not faces.get("foreach.narrowed_opt_listset")
        _assert_routes_byte_identical(src)

    def test_user_record_iterable_stays_ast(self):
        # A narrowed ptr-opt USER-ITERATOR record enters the same carve-out
        # and must keep rejecting: the family test is the fence.
        src = ("from tpy import Int32\n"
               "class R:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n"
               "    def __iter__(self) -> 'R':\n"
               "        return self\n"
               "    def __next__(self) -> Int32 | None:\n"
               "        self.n += 1\n"
               "        if self.n > 3:\n"
               "            return None\n"
               "        return self.n\n"
               "def f(r: R | None) -> Int32:\n"
               "    n = 0\n"
               "    if r is not None:\n"
               "        for v in r:\n"
               "            n = n + v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f(R()), f(None))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

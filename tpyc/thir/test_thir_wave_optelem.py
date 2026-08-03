"""Long-tail container-method arg rows: a pending int LITERAL at a
value-repr `Optional[fixed-int]` element slot renders the bare digits
(std::optional's converting ctor wraps), and a ptr VALUE at an
`Own[Ptr[T]]` element slot renders bare for name and call sources alike
(Own on a value type is a no-op spelling; the native stub's
inline-template arg takes no copy temp)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)


class TestOptScalarElemLiteral:
    def test_pending_literal_append_routes(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    vals: list[Int32 | None] = []\n"
               "    vals.append(3)\n"
               "    vals.append(None)\n"
               "    print(len(vals))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "vals.push_back(3);" in cpp[1]
        assert "vals.push_back(std::nullopt);" in cpp[1]

    def test_float_inner_literal_routes_via_resolved_path(self):
        # A float literal at a value-opt FLOAT element is outside the
        # fixed-int literal row's domain; it resolves through the ordinary
        # scalar path and stays byte-identical.
        src = ("from tpy import Float32\n"
               "def main() -> None:\n"
               "    vals: list[Float32 | None] = []\n"
               "    vals.append(1.5)\n"
               "    print(len(vals))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_bigint_inner_literal_still_routes(self):
        # The BigInt inner resolves through the ordinary resolved-scalar
        # path, not the fixed-int literal row -- both stay byte-identical.
        src = ("def main() -> None:\n"
               "    vals: list[int | None] = []\n"
               "    vals.append(3)\n"
               "    print(len(vals))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)


class TestOwnPtrElemArg:
    _NODE = (
        "from tpy import Int32, Ptr\n"
        "from tpy.unsafe import unsafe_cast, unsafe_ptr\n"
        "class Node:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
    )

    def test_ptr_name_and_call_args_route(self):
        src = self._NODE + (
            "def main() -> None:\n"
            "    backing: list[Node] = [Node(5)]\n"
            "    p: Ptr[Node] = unsafe_cast(unsafe_ptr(backing))\n"
            "    ps: list[Ptr[Node]] = []\n"
            "    ps.append(p)\n"
            "    ps.append(unsafe_cast(unsafe_ptr(backing)))\n"
            "    print(len(ps))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.own_ptr_value"] >= 2
        cpp = _assert_byte_identical(src)
        assert "ps.push_back(p);" in cpp[1]

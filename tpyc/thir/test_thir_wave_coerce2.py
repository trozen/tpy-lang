"""Long-tail coerce dispositions: the scalar cast into an
`Own[value-scalar]` slot (the Own[Ptr] precedent), the view->owned
materialize of an RVALUE source at an `Own[str]` slot (binds `T&&`
bare, no temp), and the bytes->BytesView identity (a bytes NAME/slice
is already the span; a LITERAL flips to the static `bytes_literal`
span)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestCoerceLongTail:
    def test_own_scalar_cast_arg_routes(self):
        # `log.append(v)` at BigInt v -> the to_fixed_check cast binds the
        # Own[Int32] slot natively.
        src = ("from tpy import Int32\n"
               "def add(xs: list[Int32], v: int) -> None:\n"
               "    xs.append(v)\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = []\n"
               "    add(xs, 7)\n"
               "    print(xs[0])\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "add") is not None
        cpp = _assert_byte_identical(src)
        assert "xs.push_back((v).to_fixed_check<int32_t>());" in cpp[1]

    def test_str_slice_rvalue_append_routes(self):
        # The view->owned materialize of the slice rvalue renders inline.
        src = ("def main() -> None:\n"
               "    items: list[str] = []\n"
               "    subject = \"hello world\"\n"
               "    items.append(subject[:5])\n"
               "    print(items[0])\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("items.push_back(std::string(::tpy::str_slice(subject, "
                "::tpy::BasicSlice{std::nullopt, 5})));" in cpp[1])

    def test_strview_name_at_own_str_slot_routes(self):
        # A declared-StrView NAME at the Own[str] element slot arrives
        # under the sema strview_to_str coerce; the owned-str arg row
        # peels it and wraps the bare view read (`std::string(s)` -- the
        # stub method's inline_template render skips the copy temp), so
        # this former fence routes byte-identically.
        src = ("from tpy import StrView\n"
               "def add(items: list[str], s: StrView) -> None:\n"
               "    items.append(s)\n"
               "def main() -> None:\n"
               "    items: list[str] = []\n"
               "    add(items, \"hi\")\n"
               "    print(items[0])\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "add") is not None
        _assert_byte_identical(src)

    def test_bytes_view_identity_routes(self):
        src = ("from tpy import BytesView\n"
               "def from_param(b: bytes) -> BytesView:\n"
               "    return b\n"
               "def from_literal() -> BytesView:\n"
               "    bv: BytesView = b\"hello\"\n"
               "    return bv\n"
               "def main() -> None:\n"
               "    data = b\"abc\"\n"
               "    print(from_param(data).decode())\n"
               "    print(from_literal().decode())\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "from_param") is not None
        assert _fn(thir, "from_literal") is not None
        cpp = _assert_byte_identical(src)
        assert "return b;" in cpp[1]
        assert ("std::span<const uint8_t> bv = "
                "::tpy::bytes_literal(\"hello\", 5);" in cpp[1])

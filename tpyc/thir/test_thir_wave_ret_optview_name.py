"""A str/bytes NAME at a value-repr `Optional[view-family]` return.

The slot's inner decides which form lands bare: an owned inner
(`std::optional<std::string>`) takes a STORAGE name -- an owned local, an
`Own[str]` param -- through the optional's converting ctor, and a view
inner (`std::optional<std::string_view>`) takes a BORROW one. The crossed
pair stays out in both directions.

The view-into-owned half is not merely unwitnessed: the AST emits an
un-convertible bare `return <string_view>;` there, so the shape must keep
rejecting rather than be mirrored.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _thir_ctx,
)


class TestOwnedNamesAtOwnedInnerRoute:
    # An owned `str` local, a concat result, an owned `bytes` local and an
    # `Own[str]` param -- every STORAGE-form name at an owned inner.
    SRC = (
        "from tpy import Own, String\n"
        "def from_local(k: str) -> str | None:\n"
        "    if len(k) > 0:\n"
        "        v = String(k)\n"
        "        return v\n"
        "    return None\n"
        "def from_concat(k: str) -> str | None:\n"
        "    if len(k) > 0:\n"
        "        v = k + 'x'\n"
        "        return v\n"
        "    return None\n"
        "def from_bytes(k: bytes) -> bytes | None:\n"
        "    if len(k) > 0:\n"
        "        v = bytes(k)\n"
        "        return v\n"
        "    return None\n"
        "def from_own_param(k: Own[str]) -> str | None:\n"
        "    if len(k) > 0:\n"
        "        return k\n"
        "    return None\n"
        "def main() -> None:\n"
        "    print(from_local('a') is not None,\n"
        "          from_concat('a') is not None,\n"
        "          from_bytes(b'a') is not None,\n"
        "          from_own_param(String('a')) is not None)\n"
        "main()\n"
    )

    def test_routes_with_face(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("ret.value_opt_view_name", 0) == 4

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<std::string> from_local" in cpp
        assert cpp.count("        return v;\n") == 3


class TestViewNameAtViewInnerRoutes:
    # The mirrored half: a `StrView` local at a `StrView | None` slot is
    # BORROW into a view inner, which is the matching pair.
    SRC = (
        "from tpy import StrView\n"
        "def f(k: str) -> StrView | None:\n"
        "    if len(k) > 0:\n"
        "        v: StrView = k\n"
        "        return v\n"
        "    return None\n"
        "def main() -> None:\n"
        "    print(f('a') is not None)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<std::string_view>" in cpp


class TestViewNameAtOwnedInnerDefers:
    # BOUNDARY: a `str` PARAM is `std::string_view` in the signature, and
    # the owned inner has no implicit conversion from it -- the AST's own
    # `return k;` here does not compile, so mirroring it would propagate a
    # defect. The shape keeps its named reject.
    SRC = (
        "def f(k: str) -> str | None:\n"
        "    if len(k) > 0:\n"
        "        return k\n"
        "    return None\n"
        "def main() -> None:\n"
        "    print(f('a') is not None)\n"
        "main()\n"
    )

    def test_defers_at_named_shape(self):
        _ctx, fb = _thir_ctx(self.SRC)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.opt_view_source")


class TestSliceSourceAtOwnedInnerDefers:
    # BOUNDARY: a slice at an owned inner is wrapped by sema in the
    # view->owned coercion, whose MATERIALIZING render the bare name arm
    # does not carry -- the identity-coerce see-through must not reach it.
    SRC = (
        "def f(k: str) -> str | None:\n"
        "    at = k.find(':')\n"
        "    if at >= 0:\n"
        "        return k[:at]\n"
        "    return None\n"
        "def main() -> None:\n"
        "    print(f('a:b') is not None)\n"
        "main()\n"
    )

    def test_defers_at_named_shape(self):
        _ctx, fb = _thir_ctx(self.SRC)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.opt_view_source")

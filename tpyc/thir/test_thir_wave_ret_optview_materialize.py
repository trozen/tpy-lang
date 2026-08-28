"""A view->owned MATERIALIZING coercion returned into a value-repr Optional
slot (`-> str | None` / `-> bytes | None`): the coercion owns the render
(`std::string(x)` / `::tpy::bytes_copy(x)`) and the optional's converting ctor
absorbs it. A coercion that does NOT materialize keeps the reject."""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _codegen_facts(source: str):
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True,
                               comment_line_numbers=False, thir_codegen=True))
    return compiler._thir_face_witnesses, compiler._thir_fallback


# Both flavors in one body: the coercion sits over a SLICE and over a NAME.
STR_OWNED_INNER = """
def user_of(netloc: str) -> str | None:
    at = netloc.rfind("@")
    if at < 0:
        return None
    userinfo = netloc[:at]
    colon = userinfo.find(":")
    if colon >= 0:
        return userinfo[:colon]
    return userinfo


def main() -> None:
    print(user_of("bob:pw@host"))
    print(user_of("bob@host"))


main()
"""


def test_str_materialize_at_owned_inner_routes():
    faces, fallback = _codegen_facts(STR_OWNED_INNER)
    assert fallback == {}
    assert faces.get("ret.value_opt_view_materialize", 0) >= 2


def test_str_materialize_at_owned_inner_byte_identical():
    _hpp, cpp = _assert_routes_byte_identical(STR_OWNED_INNER)
    assert ("return std::string(::tpy::str_slice(userinfo, "
            "::tpy::BasicSlice{std::nullopt, colon}));") in cpp
    assert "return std::string(userinfo);" in cpp


# The bytes twin of the same row -- a different materialize helper, same gate.
BYTES_OWNED_INNER = """
from tpy import Int32, BytesView


def head(bv: BytesView, n: Int32) -> bytes | None:
    if n < 0:
        return None
    return bv[:n]


def whole(bv: BytesView) -> bytes | None:
    return bv


def main() -> None:
    b = b"abcdef"
    print(head(b, 3))
    print(whole(b))


main()
"""


def test_bytes_materialize_at_owned_inner_routes():
    faces, fallback = _codegen_facts(BYTES_OWNED_INNER)
    assert fallback == {}
    assert faces.get("ret.value_opt_view_materialize", 0) >= 2


def test_bytes_materialize_at_owned_inner_byte_identical():
    _hpp, cpp = _assert_routes_byte_identical(BYTES_OWNED_INNER)
    assert ("return ::tpy::bytes_copy(::tpy::bytes_slice(bv, "
            "::tpy::BasicSlice{std::nullopt, n}));") in cpp
    assert "return ::tpy::bytes_copy(bv);" in cpp


# The VIEW-inner sibling keeps its own arm: no coercion is inserted there, so
# the slice render lands bare in `std::optional<std::string_view>`.
VIEW_INNER = """
from tpy import Int32, StrView


def head(s: StrView, n: Int32) -> StrView | None:
    if n < 0:
        return None
    return s[:n]


def main() -> None:
    print(head("abcdef", 3))


main()
"""


def test_view_inner_slice_keeps_its_own_arm():
    faces, fallback = _codegen_facts(VIEW_INNER)
    assert fallback == {}
    assert faces.get("ret.value_opt_view_slice", 0) >= 1
    assert faces.get("ret.value_opt_view_materialize", 0) == 0


# Boundary: `optional_strview_to_str` rebuilds the whole optional through the
# AST's `__ov` statement expression -- it does not materialize, so the gate
# must not admit it just because a coercion is present.
NON_MATERIALIZING = """
from tpy import StrView


def relay(o: StrView | None) -> str | None:
    return o


def main() -> None:
    print(relay("hi"))


main()
"""


def test_non_materializing_optional_coerce_stays_ast():
    _faces, fallback = _codegen_facts(NON_MATERIALIZING)
    _assert_rejects_at(fallback, "body:stmt.return",
                       shape="return.opt_view_source")

"""Pins for call / method-call / subscript RESULTS at a value-repr
`Optional[str|bytes|view]` return slot.

Two ways such a result lands bare: the callee already hands back the same
optional, or it hands back an inner value whose owned-vs-view form matches the
slot's inner, so the optional's converting constructor absorbs it.

A form MISMATCH is not this rung's business. A VIEW result at an OWNED inner
arrives as a sema coercion that owns its own `std::string(...)` render, so the
coercion arm takes it; an OWNED result at a VIEW inner renders bare over a
temporary and keeps falling back."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)


class TestValueOptViewReturnResult:
    def test_whole_optional_call_result_routes(self):
        src = (
            "def inner(s: str) -> str | None:\n"
            "    if len(s) == 0:\n"
            "        return None\n"
            "    return s.lower()\n"
            "def outer(s: str) -> str | None:\n"
            "    return inner(s)\n"
            "def main() -> None:\n"
            "    v = outer('AB')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return inner(s);" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.value_opt_view_result"] >= 1

    def test_owned_method_result_routes(self):
        # The inner-value half: an owned `str` result converts into the
        # `std::optional<std::string>` slot with no materialization.
        src = (
            "def f(s: str) -> str | None:\n"
            "    if len(s) == 0:\n"
            "        return None\n"
            "    return s.lower()\n"
            "def main() -> None:\n"
            "    v = f('AB')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ::tpy::str_lower(s);" in hpp + cpp

    def test_subscript_result_routes(self):
        src = (
            "def f(d: dict[str, str], k: str) -> str | None:\n"
            "    if k in d:\n"
            "        return d[k]\n"
            "    return None\n"
            "def main() -> None:\n"
            "    d = {'a': 'b'}\n"
            "    v = f(d, 'a')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ::tpy::__getitem__(d, k);" in hpp + cpp

    def test_bytes_call_result_routes(self):
        src = (
            "def f(b: bytes) -> bytes | None:\n"
            "    if len(b) == 0:\n"
            "        return None\n"
            "    return bytes(b)\n"
            "def main() -> None:\n"
            "    v = f(b'ab')\n"
            "    if v is not None:\n"
            "        print(len(v))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ::tpy::bytes_copy(b);" in hpp + cpp

    def test_view_result_at_owned_inner_routes_through_the_coerce(self):
        # Sema wraps the view in a view->owned coercion, so the copy -- not
        # this rung -- owns the render; the coercion arm below admits it.
        src = (
            "from tpy import StrView\n"
            "def pick(s: str) -> StrView:\n"
            "    return s\n"
            "def f(s: str) -> str | None:\n"
            "    if len(s) == 0:\n"
            "        return None\n"
            "    return pick(s)\n"
            "def main() -> None:\n"
            "    v = f('AB')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return std::string(pick(s));" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.value_opt_view_materialize"] >= 1

    def test_owned_result_at_view_inner_stays_ast(self):
        # BOUNDARY the other way: an owned result binds the view slot to a
        # temporary, so the forms do not match and the rung must not admit it.
        src = (
            "from tpy import StrView\n"
            "def make(s: StrView) -> str:\n"
            "    return s.lower()\n"
            "def f(s: StrView) -> StrView | None:\n"
            "    if len(s) == 0:\n"
            "        return None\n"
            "    return make(s)\n"
            "def main() -> None:\n"
            "    v = f('AB')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.return",
                           "return.opt_view_source")
        _assert_byte_identical(src)

    def test_coerced_slice_at_owned_inner_routes_through_the_coerce(self):
        # The slice arm above admits only a BARE subscript; at an owned inner
        # the node is the coercion instead, and the copy is its render.
        src = (
            "def f(s: str) -> str | None:\n"
            "    if len(s) == 0:\n"
            "        return None\n"
            "    return s[1:]\n"
            "def main() -> None:\n"
            "    v = f('abc')\n"
            "    if v is not None:\n"
            "        print(v)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return std::string(::tpy::str_slice(" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.value_opt_view_materialize"] >= 1

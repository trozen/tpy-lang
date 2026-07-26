"""Pins for admitting `tpy.String` into the resolved str slice: a String
binding is OWNED in every position (its param slot is `const std::string&`,
spelled by the skeleton emitter, so no body arm renders a view), which makes
the name form STORAGE, keeps the view->owned return convert off a String
slot, and keeps `+=` in place. Boundary pins hold the rows O1 leaves out."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


class TestStringSlice:
    def test_string_param_returns_bare(self):
        src = (
            "from tpy import String\n"
            "def take(s: String) -> String:\n"
            "    return s\n"
            "def main() -> None:\n"
            "    print(take(String(\"hi\")))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert "std::string take(const std::string& s) {" in out
        # STORAGE form: no view->owned copy at the return.
        assert "    return s;\n" in out
        assert "body:stmt.return" not in "".join(fallback)
        _assert_byte_identical(src)

    def test_str_param_into_string_slot_materializes(self):
        src = (
            "from tpy import String\n"
            "def take(s: String) -> String:\n"
            "    return s\n"
            "def forward(s: str) -> String:\n"
            "    return take(s)\n"
            "def main() -> None:\n"
            "    print(forward(\"hello\"))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "return take(std::string(s));" in out
        _assert_byte_identical(src)

    def test_string_inplace_append_stays_in_place(self):
        src = (
            "from tpy import String\n"
            "def main() -> None:\n"
            "    s = String(\"a\")\n"
            "    s += \"b\"\n"
            "    print(s)\n"
            "main()\n"
        )
        out, _faces, _fallback = _gen_thir(src)
        assert "s += \"b\";" in out
        _assert_byte_identical(src)


class TestStringBoundaries:
    def test_bytes_family_unaffected(self):
        src = (
            "def take(b: bytes) -> bytes:\n"
            "    return b\n"
            "def main() -> None:\n"
            "    print(len(take(b\"xy\")))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::vector<uint8_t> take(std::span<const uint8_t> b)" in out
        _assert_byte_identical(src)

    def test_str_param_return_still_copies(self):
        # A `str` PARAM is a view: the owned return slot keeps its explicit
        # `std::string(...)` construction -- String must not eat that arm.
        src = (
            "def echo(s: str) -> str:\n"
            "    return s\n"
            "def main() -> None:\n"
            "    print(echo(\"hi\"))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "return std::string(s);" in out
        _assert_byte_identical(src)


class TestStringCtorArgs:
    def test_scalar_into_string_ctor_uses_the_numeric_template(self):
        src = (
            "from tpy import Int32, String\n"
            "def main() -> None:\n"
            "    s: String = String(Int32(42))\n"
            "    b: String = String(True)\n"
            "    print(s, b)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert not fallback
        assert "std::string s = ::tpy::fixed_to_str<int32_t>(42);" in out
        assert "std::string b = std::string(::tpy::bool_to_str(true));" in out
        _assert_byte_identical(src)

    def test_char_into_string_ctor_stays_ast(self):
        # Char is deliberately outside `_resolved_scalar`: its ctor renders
        # through a different template (`::tpy::char_to_str`), so the scalar
        # row must not swallow it.
        src = (
            "from tpy import String, Char\n"
            "def main() -> None:\n"
            "    c: Char = 'x'\n"
            "    s: String = String(c)\n"
            "    print(s)\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert fallback
        assert "std::string s = std::string(::tpy::char_to_str(c));" in out
        _assert_byte_identical(src)


class TestOptionalStringFence:
    def test_optional_string_narrowed_return_stays_ast(self):
        # The AST MOVES the narrowed deref at an owned String sink
        # (`return std::move((*x));`) instead of the view->owned copy the
        # value-optional view family emits, so `Optional[String]` is not
        # part of that family.
        src = (
            "from typing import Optional\n"
            "from tpy import String\n"
            "def unwrap(x: Optional[String]) -> String:\n"
            "    if x is not None:\n"
            "        return x\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(unwrap(String(\"a\")))\n"
            "main()\n"
        )
        out, _faces, fallback = _gen_thir(src)
        assert fallback
        assert "return std::move((*x));" in out
        _assert_byte_identical(src)

    def test_optional_str_param_return_still_routes(self):
        # The str twin keeps its own family arm -- the String exclusion must
        # not take `Optional[str]` with it.
        src = (
            "from typing import Optional\n"
            "def unwrap(x: Optional[str]) -> str:\n"
            "    if x is not None:\n"
            "        return x\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(unwrap(\"a\"))\n"
            "main()\n"
        )
        _out, _faces, fallback = _gen_thir(src)
        assert not fallback
        _assert_byte_identical(src)

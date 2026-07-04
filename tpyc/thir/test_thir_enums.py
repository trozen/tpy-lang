"""THIR enum value bindings: member access, locals/params/returns, compares
(incl. the int-enum underlying casts), E(x) value lookup, fields/MIL,
print/f-string args."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from .lower import lower_module
from .nodes import THIRCall, THIREnumMember
from .testutil import (
    _compile, _entry, _lower_ctx, _fn,
)

_ENUM_PRELUDE = (
    "from enum import Enum, IntEnum\n"
    "from tpy import Int32\n"
    "class Color(Enum):\n    RED = 1\n    GREEN = 2\n"
    "class Prio(IntEnum):\n    LOW = 1\n    HIGH = 5\n"
)


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


class TestEnumValues:
    SRC = (
        _ENUM_PRELUDE
        + "class H:\n"
        + "    c: Color\n"
        + "    def __init__(self):\n        self.c = Color.RED\n"
        + "def pick(c: Color) -> Color:\n"
        + "    x = Color.GREEN\n"
        + "    if c == Color.RED:\n"
        + "        return x\n"
        + "    return c\n"
        + "def prios(p: Prio, n: Int32) -> bool:\n"
        + "    ok = p >= Prio.HIGH\n"
        + "    m = Prio(n)\n"
        + "    if p == m:\n"
        + "        return True\n"
        + "    return ok\n"
        # Enum field write + field-read arg, through an H param (a record
        # ctor LOCAL `h = H()` is the pre-existing deferred rvalue-local
        # cell -- unrelated to enums, main() below stays AST because of it).
        + "def flip(h: H) -> Color:\n"
        + "    h.c = Color.GREEN\n"
        + "    r = pick(h.c)\n"
        + "    return r\n"
        + "def main():\n"
        + "    h = H()\n"
        + "    print(flip(h), prios(Prio.LOW, 5))\n"
        + '    print(f"p={Prio.HIGH}")\n'
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower_ctx(self.SRC)
        for name in ("pick", "prios", "flip"):
            fn = _fn(thir, name)
            assert fn is not None, name

    def test_member_access_node(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "pick")
        decl = fn.body[0]
        assert isinstance(decl.init, THIREnumMember)
        assert decl.init.cpp == "Color::GREEN"

    def test_from_value_template(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "prios")
        m_decl = fn.body[1]
        assert isinstance(m_decl.init, THIRCall)
        assert m_decl.init.cpp_template == \
            "::tpy::EnumUtil<Prio>::from_value({0})"

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "Color x = Color::GREEN;" in cpp            # decl spelling
        assert "if ((c == Color::RED))" in cpp             # plain-enum compare
        # int-enum ordering casts both sides to the underlying type
        assert ("bool ok = (static_cast<int32_t>(p) >= "
                "static_cast<int32_t>(Prio::HIGH));") in cpp
        assert "Prio m = ::tpy::EnumUtil<Prio>::from_value(n);" in cpp
        assert "if ((p == m))" in cpp                      # same-enum ==, no casts
        assert "h.c = Color::GREEN;" in cpp                # enum field write
        assert "static_cast<int>(Prio::HIGH)" in cpp       # f-string arm

    def test_int_enum_vs_int_compare_casts_enum_side_only(self):
        # Mixed IntEnum-vs-int ordering: only the enum operand casts to the
        # underlying type; the int side renders bare.
        src = (
            _ENUM_PRELUDE
            + "def hot(p: Prio, n: Int32) -> bool:\n"
            + "    return p >= n\n"
            + "def main():\n    print(hot(Prio.HIGH, 3))\nmain()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "hot") is not None
        cpp = _cpp(src, thir=True)
        assert "(static_cast<int32_t>(p) >= n)" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_cross_module_enum_ineligible(self, tmp_path):
        # A cross-module enum spells qualified (::tpyapp::m::E); the slice
        # admits same-module enums only.
        (tmp_path / "helper.py").write_text(
            "from enum import Enum\n"
            "class Color(Enum):\n    RED = 1\n    GREEN = 2\n")
        src = (
            "from helper import Color\n"
            "def f(c: Color) -> bool:\n"
            "    return c == Color.RED\n"
            "def main():\n    print(f(Color.RED))\nmain()\n"
        )
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "f") is None

"""THIR enum value bindings: member access, locals/params/returns, compares
(incl. the int-enum underlying casts), E(x) value lookup, fields/MIL,
print/f-string args; the remainder rows -- cross-module / @native / nested
spellings, truthiness, IntEnum unary minus and arithmetic, .value, .name
(a BORROW-tagged static-storage view), nested Outer.Kind(v)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from .lower import lower_module
from .nodes import THIRCall, THIREnumMember, THIREnumWrap
from .testutil import (
    _compile, _entry, _lower_ctx, _lower_ctx_witnessed, _fn,
)

_ENUM_PRELUDE = (
    "from enum import Enum, IntEnum\n"
    "from tpy import Int32\n"
    "class Color(Enum):\n    RED = 1\n    GREEN = 2\n"
    "class Prio(IntEnum):\n    LOW = 1\n    HIGH = 5\n"
)


def _cpp(src: str, thir: bool, extra_lib_dirs=None):
    compiler, modules = _compile(src, extra_lib_dirs)
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

    def test_cross_module_enum_routes_qualified(self, tmp_path):
        # A cross-module enum spells qualified (::tpyapp::m::E) in every
        # position: the member access, the param decl, and the local decl
        # (the render_type-threaded cpp_type).
        (tmp_path / "helper.py").write_text(
            "from enum import Enum\n"
            "class Color(Enum):\n    RED = 1\n    GREEN = 2\n")
        src = (
            "from helper import Color\n"
            "def f(c: Color) -> bool:\n"
            "    x = Color.GREEN\n"
            "    return c == x\n"
            "def main():\n    print(f(Color.RED))\nmain()\n"
        )
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIREnumMember)
        assert decl.init.cpp == "::tpyapp::helper::Color::GREEN"
        cpp = _cpp(src, thir=True, extra_lib_dirs=[tmp_path])
        assert cpp == _cpp(src, thir=False, extra_lib_dirs=[tmp_path])
        assert ("::tpyapp::helper::Color x = "
                "::tpyapp::helper::Color::GREEN;") in cpp


class TestEnumTruthiness:
    SRC = (
        _ENUM_PRELUDE
        + "def t(c: Color, p: Prio) -> bool:\n"
        + "    if c:\n        print(1)\n"
        + "    while p:\n        break\n"
        + "    b = not p\n"
        + "    assert c\n"
        + "    return not c or b\n"
        + "def main():\n    print(t(Color.RED, Prio.LOW))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "t") is not None
        assert w.get("enum.truthy_plain", 0) >= 2   # `if c:` + `not c`
        assert w.get("enum.truthy_int", 0) >= 2     # `while p:` + `not p`

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "if (true) {" in cpp                          # plain enum cond
        assert "while ((static_cast<int32_t>(p) != 0))" in cpp
        assert "bool b = (!((static_cast<int32_t>(p) != 0)));" in cpp
        assert "if (!(true)) ::tpy::raise_assertion_error();" in cpp  # assert c
        assert "return ((!(true)) || b);" in cpp             # `not c` value

    def test_call_operand_stays_ast(self):
        # `if make():` drops the call render on the AST path (BUGS.md) --
        # gate-rejected, so the body stays on the AST path.
        src = (
            _ENUM_PRELUDE
            + "def make() -> Color:\n    return Color.RED\n"
            + "def f() -> None:\n"
            + "    if make():\n        print(1)\n"
            + "def main():\n    f()\nmain()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "make") is not None
        assert _fn(thir, "f") is None


class TestIntEnumScalarOps:
    SRC = (
        _ENUM_PRELUDE
        + "def a(p: Prio, n: Int32) -> Int32:\n"
        + "    x = p + 10\n"
        + "    y = p * n\n"
        + "    m = -p\n"
        + "    return x + y + m\n"
        + "def main():\n    print(a(Prio.HIGH, 3))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "a") is not None
        assert w.get("enum.neg", 0) == 1

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        # Arithmetic casts the enum side to the underlying type (the
        # resolved-binop operand casts); the int side renders bare.
        assert ("int32_t x = (::tpy::add_check<int32_t>("
                "static_cast<int32_t>(p), 10));") in cpp
        assert ("int32_t y = (::tpy::mul_check<int32_t>("
                "static_cast<int32_t>(p), n));") in cpp
        assert "int32_t m = (-static_cast<int32_t>(p));" in cpp


class TestEnumProps:
    SRC = (
        _ENUM_PRELUDE
        + "def props(c: Color) -> Int32:\n"
        + "    v = c.value\n"
        + '    print(f"v={c.value}")\n'
        + "    return v\n"
        + "def main():\n    print(props(Color.RED))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "props")
        assert fn is not None
        assert w.get("enum.value", 0) >= 2
        decl = fn.body[0]
        assert isinstance(decl.init, THIREnumWrap)
        assert decl.init.wrap == "static_cast<int32_t>({0})"

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "int32_t v = static_cast<int32_t>(c);" in cpp
        assert 'std::format("v={}", static_cast<int32_t>(c))' in cpp

    # `.name` sources: sema types the read StrView (the value IS a
    # static-storage string_view), so lowering tags the wrap BORROW and the
    # S1 owned-sink machinery fires -- print/view sinks render the wrap bare,
    # an owned-str return arrives strview_to_str-coerce-wrapped
    # (`std::string(...)`), a mutated annotated local resolves owned and
    # copies at the decl init. A READ-ONLY annotated local resolves VIEW,
    # so its stale coerce peels and the wrap renders bare (the
    # _peel_stale_view_owned_coerce mirror).
    NAME_SRC = (
        _ENUM_PRELUDE
        + "def f(c: Color) -> None:\n"
        + "    print(c.name)\n"
        + "def g(c: Color) -> str:\n"
        + "    return c.name\n"
        + "def h(c: Color) -> None:\n"
        + "    v = c.name\n"
        + "    label: str = c.name\n"
        + '    label += "!"\n'
        + "    print(v, label, v == label)\n"
        + "def r(c: Color) -> None:\n"
        + "    label: str = c.name\n"
        + "    print(label)\n"
        + "def main():\n    f(Color.RED)\n    print(g(Color.RED))\n"
        + "    h(Color.GREEN)\n    r(Color.RED)\nmain()\n"
    )

    def test_name_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.NAME_SRC)
        for name in ("f", "g", "h", "r"):
            assert _fn(thir, name) is not None, name
        assert w.get("enum.name", 0) >= 5
        wrap = _fn(thir, "f").body[0].args[0].expr
        assert isinstance(wrap, THIREnumWrap)
        assert wrap.wrap == "::tpy::EnumUtil<Color>::name({0})"

    def test_name_emit_arms(self):
        cpp = _cpp(self.NAME_SRC, thir=True)
        assert cpp == _cpp(self.NAME_SRC, thir=False)
        # Owned-str sinks copy the view explicitly; view sinks stay bare.
        assert "return std::string(::tpy::EnumUtil<Color>::name(c));" in cpp
        assert ("std::string label = "
                "std::string(::tpy::EnumUtil<Color>::name(c));") in cpp
        assert "std::string_view v = ::tpy::EnumUtil<Color>::name(c);" in cpp
        # The read-only annotated local resolved VIEW: the stale coerce
        # peels on both paths -- bare static view, no owned temporary.
        assert ("std::string_view label = "
                "::tpy::EnumUtil<Color>::name(c);") in cpp


class TestNativeEnum:
    SRC = (
        '# tpy: include("native_types.hpp")\n'
        "from enum import Enum, auto\n"
        "from tpy.extern import native, native_member\n"
        '@native("ns::E")\n'
        "class E(Enum):\n"
        '    A = native_member("CppA")\n'
        "    B = auto()\n"
        "def pick(e: E) -> E:\n"
        "    x = E.A\n"
        "    if e == x:\n"
        "        print(e)\n"
        "        print(e.value)\n"
        "    return x\n"
        "def main():\n    print(pick(E.B))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "pick")
        assert fn is not None
        # The member rename map applies at lowering: A -> ns::E::CppA.
        decl = fn.body[0]
        assert isinstance(decl.init, THIREnumMember)
        assert decl.init.cpp == "::ns::E::CppA"
        assert w.get("enum.repr_print", 0) >= 1

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "::ns::E x = ::ns::E::CppA;" in cpp          # decl + rename
        assert "std::cout << ::tpy::__repr__(e)" in cpp     # native print arm
        assert "static_cast<int32_t>(e)" in cpp             # .value


class TestNestedEnum:
    SRC = (
        "from enum import Enum\n"
        "from tpy import Int32\n"
        "class Message:\n"
        "    class Kind(Enum):\n"
        "        TEXT = 1\n"
        "        IMAGE = 2\n"
        "def probe(k: Message.Kind, n: Int32) -> Message.Kind:\n"
        "    x = Message.Kind.IMAGE\n"
        "    if k == Message.Kind.TEXT:\n"
        "        return x\n"
        "    y = Message.Kind(n)\n"
        "    print(y.value)\n"
        "    return y\n"
        "def main():\n    print(probe(Message.Kind.TEXT, 2))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "probe")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIREnumMember)
        assert decl.init.cpp == "Message::Kind::IMAGE"       # chained stamp
        assert w.get("enum.nested_from_value", 0) == 1

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "Message::Kind x = Message::Kind::IMAGE;" in cpp
        assert ("Message::Kind y = "
                "::tpy::EnumUtil<Message::Kind>::from_value(n);") in cpp
        assert "static_cast<int32_t>(y)" in cpp

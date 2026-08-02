"""The F1 type-arg fence re-scope wave: enum / tuple / union-alias args of a
generic user record are spelled byte-identically by both paths at lowering
time (the divergence the fence's docstring claimed was a map-population
timing artifact), so `_f1_record_type_arg_ok` admits them family-by-family.

The three measured-real holes stay fenced, each with an adversarial pin:
PendingView args (`to_cpp()` raises), module-LOCAL plain union aliases
(registered mid-emission AFTER lowering), and un-imported @dynamic-protocol
args (the BUGS.md bare-vs-qualified spelling entry -- AST-first, THIR must
not mirror the uncompilable output by routing).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    """Emit through THIR and return the fallback tally (reject pins).

    Byte-identity for these fixtures is already guaranteed by fallback
    (the AST re-emits the body), so the tally IS the claim."""
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_PAIR = (
    "class Pair[T]:\n"
    "    first: T\n"
    "    def __init__(self, first: T):\n"
    "        self.first = first\n"
)

_COLOR = (
    "from enum import Enum\n"
    "class Color(Enum):\n"
    "    RED = 1\n"
    "    BLUE = 2\n"
)


class TestEnumTypeArg:
    SRC = (
        "from tpy import Int32\n"
        + _COLOR + _PAIR +
        "def probe(p: Pair[Color]) -> bool:\n"
        "    return p.first == Color.BLUE\n"
        "def main():\n"
        "    p = Pair(Color.RED)\n"
        "    print(probe(p))\n"
        "main()\n"
    )

    def test_local_enum_arg_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Pair<Color>" in hpp + cpp

    def test_cross_module_enum_arg_routes_byte_identical(self, tmp_path):
        (tmp_path / "shades.py").write_text(
            "from enum import Enum\n"
            "class Shade(Enum):\n"
            "    DARK = 1\n"
            "    LIGHT = 2\n"
        )
        src = (
            "from tpy import Int32\n"
            "from shades import Shade\n"
            + _PAIR +
            "def main():\n"
            "    p = Pair(Shade.DARK)\n"
            "    print(p.first == Shade.LIGHT)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(
            src, extra_lib_dirs=[tmp_path])
        assert "Pair<::tpyapp::shades::Shade>" in hpp + cpp

    def test_own_slot_enum_member_arg_keeps_rejecting(self):
        # The SPELLING now admits Pair<Color>, but the enum-member-access
        # arg into an `Own[T]` ctor slot has no arg-row yet -- the body
        # must keep falling back at the ctor-arg gate, not route half-way.
        src = (
            "from tpy import Int32, Own\n"
            + _COLOR +
            "class Keep[T]:\n"
            "    first: T\n"
            "    def __init__(self, first: Own[T]):\n"
            "        self.first = first\n"
            "def main():\n"
            "    p = Keep(Color.RED)\n"
            "    print(p.first == Color.BLUE)\n"
            "main()\n"
        )
        assert "body:expr.call" in _thir_fallbacks(src)


class TestTupleTypeArg:
    SRC = (
        "from tpy import Int32\n"
        + _PAIR +
        "def probe(p: Pair[tuple[Int32, Int32]]) -> Int32:\n"
        "    return p.first[0] + p.first[1]\n"
        "def main():\n"
        "    p = Pair((1, 2))\n"
        "    print(probe(p))\n"
        "main()\n"
    )

    def test_scalar_tuple_arg_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Pair<std::tuple<int32_t, int32_t>>" in hpp + cpp

    def test_str_elem_tuple_arg_routes_byte_identical(self):
        # A str-family element recurses through the same slice (the owned
        # storage spelling inside a type-arg list, on both paths).
        src = (
            "from tpy import Int32, StrView\n"
            + _PAIR +
            "def main():\n"
            "    p = Pair((1, \"hi\"))\n"
            "    print(p.first[0])\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Pair<std::tuple<int32_t, std::string>>" in hpp + cpp

    def test_local_alias_union_elem_keeps_rejecting(self):
        # The element recursion is the fence: a tuple whose element is a
        # module-LOCAL plain-alias union rejects element-wise (the alias
        # registers only mid-emission, after lowering).
        src = (
            "from tpy import Int32, StrView\n"
            "type Num = Int32 | StrView\n"
            + _PAIR +
            "def make(n: Num) -> None:\n"
            "    p = Pair((1, n))\n"
            "    print(1)\n"
        )
        fell = _thir_fallbacks(src)
        assert "body:stmt.var_decl:decl.slot_type" in fell, fell


class TestUnionAliasTypeArg:
    def test_cross_module_alias_arg_routes_byte_identical(self, tmp_path):
        # The alias registers during the entry generator's import walk --
        # BEFORE lowering -- so both paths spell the alias name.
        (tmp_path / "nums.py").write_text(
            "from tpy import Int32, StrView\n"
            "type Num = Int32 | StrView\n"
        )
        src = (
            "from tpy import Int32, StrView\n"
            "from nums import Num\n"
            + _PAIR +
            "def make(n: Num) -> None:\n"
            "    p = Pair(n)\n"
            "    print(1)\n"
            "def main():\n"
            "    make(5)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(
            src, extra_lib_dirs=[tmp_path])
        assert "Pair<Num>" in hpp + cpp

    def test_local_recursive_alias_arg_routes_byte_identical(self):
        # A LOCAL recursive alias registers in the generator's setup block
        # (pre-lowering), unlike the plain local alias below -- the
        # "pre-registered" line falls exactly between the two. Param-only
        # shape: the wrapper-union CTOR-ARG rows are a separate, still-open
        # gap (see the reject below), so the pin isolates the spelling.
        src = (
            "from tpy import Int32\n"
            + _PAIR +
            "type Tree = Int32 | list[Tree]\n"
            "def probe(p: Pair[Tree]) -> Int32:\n"
            "    return 1\n"
            "def main():\n"
            "    print(1)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Pair<Tree>" in hpp + cpp

    def test_wrapper_union_ctor_arg_keeps_rejecting(self):
        # Adjacent-shape pin: the SPELLING admits Pair<Tree>, but a
        # wrapper-union value into the ctor's T slot has no arg-row yet --
        # the body must keep falling back at the call gate.
        src = (
            "from tpy import Int32\n"
            + _PAIR +
            "type Tree = Int32 | list[Tree]\n"
            "def make(t: Tree) -> None:\n"
            "    p = Pair(t)\n"
            "    print(1)\n"
        )
        assert "body:expr.call" in _thir_fallbacks(src)

    def test_local_plain_alias_arg_keeps_rejecting(self):
        # HOLE 2 pin: the module-LOCAL plain alias registers mid-emission
        # AFTER lowering (generator.py header pass) -- admitting it would
        # pre-spell `std::variant<...>` where the AST walk spells `Num`.
        src = (
            "from tpy import Int32, StrView\n"
            "type Num = Int32 | StrView\n"
            + _PAIR +
            "def make(n: Num) -> None:\n"
            "    p = Pair(n)\n"
            "    print(1)\n"
        )
        fell = _thir_fallbacks(src)
        assert "body:stmt.var_decl:decl.slot_type" in fell, fell


class TestFenceHoles:
    def test_pending_view_arg_resolves_and_routes(self):
        # HOLE 1, RESOLVED in wave 3c: the fence resolves a Pending view
        # arg through the same per-analyzer view_vars the resolver reads
        # (`Box[PendingStrType]` -> `Box<std::string_view>`), and every
        # spell site goes through the resolver -- the raw `to_cpp()` that
        # RAISES is never called. Routing without an exception escaping is
        # the whole claim; the render pins live in
        # test_thir_wave_pending_view_own.py.
        src = (
            "from tplib import Box\n"
            "def main():\n"
            "    s = \"hi\"\n"
            "    b = Box(s)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Box<std::string_view>" in cpp

    def test_unimported_dyn_protocol_arg_keeps_rejecting(self):
        # HOLE 3 pin: `Box(f())` with async `f` in a module that never
        # imports Cancellable -- the AST emits uncompilable C++ (the
        # bare-vs-qualified spelling BUGS.md entry); the fence stays closed
        # so THIR does not mirror it. AST-first when the bug is fixed.
        src = (
            "from tplib import Box\n"
            "async def f() -> int:\n"
            "    return 1\n"
            "def main():\n"
            "    b = Box(f())\n"
        )
        fell = _thir_fallbacks(src)
        assert "body:stmt.var_decl:decl.slot_type" in fell, fell

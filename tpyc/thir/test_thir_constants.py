"""The two NON-BODY constant positions: a class constant's default and a
`Final` global's initializer.

Neither is a body -- no statements, no scope, no flush point -- but both
render through the same expression dispatch a body uses, so they route
through THIR rather than growing a second constant renderer beside the
overflow-checked arithmetic one.

Every routing pin here goes through `_constant_positions`, which spies on
the `gen_expr` call each position falls back to: byte-identity and an empty
fallback tally are both satisfied by a fallback, so neither can carry a
routing claim on its own.
"""

from __future__ import annotations

from .testutil import _assert_byte_identical, _constant_positions

PRELUDE = "from typing import Final\nfrom tpy import Char, Int32\n"


class TestFinalGlobalScalars:
    """Coerced literals -- the bulk of the domain. The declared slot drives
    the spelling, so `Final[int]` wraps in `::tpy::BigInt` where `Final[Int32]`
    stays bare."""

    SRC = PRELUDE + (
        "MAX_SIZE: Final[Int32] = 100\n"
        "NEG_VAL: Final[Int32] = -42\n"
        "PI: Final[float] = 3.14159\n"
        "DEBUG: Final[bool] = True\n"
        "NAME: Final[str] = \"hello\"\n"
        "LETTER: Final[Char] = \"A\"\n"
        "BIG: Final[int] = 100\n"
        "\n"
        "def main() -> None:\n"
        "    print(MAX_SIZE)\n"
        "    print(BIG)\n"
        "    print(LETTER)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert routed == {"__name__", "MAX_SIZE", "NEG_VAL", "PI", "DEBUG",
                          "NAME", "LETTER", "BIG"}
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        # The two slot-driven renders the position exists for: a Char slot
        # turns a str literal into a character literal, a BigInt slot wraps.
        assert "inline constexpr char LETTER = 'A';" in thir[0]
        assert "const ::tpy::BigInt BIG = ::tpy::BigInt(100);" in thir[1]


class TestFinalGlobalArithmetic:
    """Binops over sibling `Final` names -- the reason this position routes
    instead of getting a dedicated renderer. The names read bare, exactly as
    a read-only value global does inside a function body."""

    SRC = (
        "from typing import Final\n"
        "A: Final[int] = 100\n"
        "B: Final[int] = 7\n"
        "SUM: Final[int] = A + B\n"
        "NESTED: Final[int] = (A + B) * 10 - 1\n"
        "\n"
        "def main() -> None:\n"
        "    print(SUM)\n"
        "    print(NESTED)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert {"A", "B", "SUM", "NESTED"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert "const ::tpy::BigInt SUM = ((A) + (B));" in thir[1]


class TestFinalGlobalOverflowChecked:
    """The fixed-width sibling: the same names, but the slot re-resolves the
    operator so the render is the overflow-checked template."""

    SRC = PRELUDE + (
        "BASE: Final[Int32] = 10\n"
        "DOUBLE: Final[Int32] = BASE * 2\n"
        "\n"
        "def main() -> None:\n"
        "    print(DOUBLE)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert {"BASE", "DOUBLE"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert ("inline constexpr int32_t DOUBLE = "
                "(::tpy::mul_check<int32_t>(BASE, 2));") in thir[0]


class TestClassConstants:
    """A class body's constants see each other in declaration order, on top
    of the module's `Final` globals."""

    SRC = PRELUDE + (
        "LIMIT: Final[Int32] = 3\n"
        "\n"
        "\n"
        "class Limits:\n"
        "    BASE: Final[Int32] = 10\n"
        "    DOUBLE: Final[Int32] = BASE * 2\n"
        "    TRIPLE: Final[Int32] = BASE + DOUBLE\n"
        "    SCALED: Final[Int32] = BASE * LIMIT\n"
        "    LABEL: Final[str] = \"limits\"\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    print(Limits.TRIPLE)\n"
        "    print(Limits.SCALED)\n"
        "    print(Limits.LABEL)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert {"BASE", "DOUBLE", "TRIPLE", "SCALED", "LABEL"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert ("static constexpr int32_t TRIPLE = "
                "(::tpy::add_check<int32_t>(BASE, DOUBLE));") in thir[0]
        # The module-level Final is in scope inside the class body too.
        assert ("static constexpr int32_t SCALED = "
                "(::tpy::mul_check<int32_t>(BASE, LIMIT));") in thir[0]


class TestTupleConstant:
    """A tuple constant is the one shape whose element FORM the declared slot
    decides: `Final[tuple[str, ...]]` elements are static `std::string_view`,
    where the literal's own resolved type would give owned `std::string`."""

    SRC = PRELUDE + (
        "class Version:\n"
        "    SEMVER: Final[tuple[Int32, Int32, Int32]] = (1, 2, 3)\n"
        "    LABEL: Final[tuple[str, bool]] = (\"alpha\", True)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    major, minor, patch = Version.SEMVER\n"
        "    print(major + minor + patch)\n"
        "    name, stable = Version.LABEL\n"
        "    print(name)\n"
        "    print(stable)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert {"SEMVER", "LABEL"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert ("static constexpr std::tuple<std::string_view, bool> LABEL = "
                "std::tuple<std::string_view, bool>{\"alpha\", true};"
                ) in thir[0]


MACRO_MOD = '''# tpy: macro_module
"""Emits constant literals a Final initializer splices in place."""
from tpyc.macro_api import CallMacroContext, Expr, call_macro, ast


@call_macro
def build_pair(ctx: CallMacroContext) -> Expr:
    return ast.tuple_lit([ast.int_lit(1), ast.str_lit("dev")])


@call_macro
def build_count(ctx: CallMacroContext) -> Expr:
    return ast.int_lit(7)
'''


class TestMacroExpandedConstant:
    """A `@call_macro` initializer: the AST renders the expansion in place
    and carries the declared slot INTO it, so the position must too. The
    general THIR macro arm lowers its expansion target-less, which a tuple
    expansion would render with the literal's own element types
    (`std::string` where the slot says `std::string_view`)."""

    SRC = PRELUDE + (
        "from constmacro import build_pair, build_count\n"
        "PAIR: Final[tuple[Int32, str]] = build_pair()\n"
        "COUNT: Final[Int32] = build_count()\n"
        "\n"
        "def main() -> None:\n"
        "    print(COUNT)\n"
        "\n"
        "main()\n"
    )

    @staticmethod
    def _libs(tmp_path):
        (tmp_path / "constmacro.py").write_text(MACRO_MOD)
        return [tmp_path]

    def test_routes(self, tmp_path):
        routed, ast_rendered, fallback = _constant_positions(
            self.SRC, extra_lib_dirs=self._libs(tmp_path))
        assert ast_rendered == set(), ast_rendered
        assert {"PAIR", "COUNT"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self, tmp_path):
        thir = _assert_byte_identical(
            self.SRC, extra_lib_dirs=self._libs(tmp_path))
        assert ("const std::tuple<int32_t, std::string_view> PAIR = "
                "std::tuple<int32_t, std::string_view>{1, \"dev\"};"
                ) in thir[1]


class TestPrimitiveConstructorCall:
    """One-arg primitive constructors -- the third-largest slice of the
    domain -- route through the ordinary call arms."""

    SRC = PRELUDE + (
        "from tpy import Float32, Int64, UInt8\n"
        "SMALL: Final[Int32] = Int32(42)\n"
        "BIG: Final[Int64] = Int64(SMALL)\n"
        "BYTE: Final[UInt8] = UInt8(255)\n"
        "HALF: Final[Float32] = Float32(0.5)\n"
        "FLAG: Final[bool] = bool(1)\n"
        "\n"
        "def main() -> None:\n"
        "    print(BIG)\n"
        "    print(HALF)\n"
        "    print(FLAG)\n"
        "    print(BYTE)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert ast_rendered == set(), ast_rendered
        assert {"SMALL", "BIG", "BYTE", "HALF", "FLAG"} <= routed
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert ("inline constexpr int64_t BIG = "
                "::tpy::int_cast_check<int64_t>(SMALL);") in thir[0]


class TestCharConstructorRoutes:
    """`Char(65)` is a value-scalar type constructor like the fixed-int ones;
    the constant position renders its `static_cast<char>(65)` on both paths.
    Was the last shape keeping a valid program's constant on the AST path."""

    SRC = PRELUDE + (
        "CH: Final[Char] = Char(65)\n"
        "\n"
        "def main() -> None:\n"
        "    print(CH)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert "CH" in routed
        assert "CH" not in ast_rendered
        assert fallback == {}, fallback

    def test_byte_identical(self):
        thir = _assert_byte_identical(self.SRC)
        assert "inline constexpr char CH = static_cast<char>(65);" in thir[0]


class TestClassConstantPositionRoutes:
    """The class-constant position takes the same render as the Final global,
    and keys its own component in the tally when either does reject."""

    SRC = PRELUDE + (
        "class Codes:\n"
        "    CH: Final[Char] = Char(65)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    print(Codes.CH)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert "CH" in routed
        assert "CH" not in ast_rendered
        assert fallback == {}, fallback

    def test_byte_identical(self):
        hpp, _cpp = _assert_byte_identical(self.SRC)
        assert "static constexpr char CH = static_cast<char>(65);" in hpp


class TestNonfiniteFloatRoutes:
    """A float literal that overflows to infinity (`math.inf` is spelled this
    way) has no C++ literal form; both paths fold it to the constexpr
    `numeric_limits` spelling, so the constant position routes."""

    SRC = (
        "from typing import Final\n"
        "MY_INF: Final[float] = 1e309\n"
        "MY_NEG_INF: Final[float] = -1e309\n"
        "MY_NAN: Final[float] = float(\"nan\")\n"
        "\n"
        "def main() -> None:\n"
        "    print(MY_INF > 1.0)\n"
        "    print(MY_NEG_INF < 1.0)\n"
        "    print(MY_NAN != MY_NAN)\n"
        "\n"
        "main()\n"
    )

    def test_routes(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert "MY_INF" in routed and "MY_NEG_INF" in routed
        assert "MY_INF" not in ast_rendered
        assert fallback == {}, fallback

    def test_byte_identical(self):
        hpp, cpp = _assert_byte_identical(self.SRC)
        both = hpp + cpp
        assert ("inline constexpr double MY_INF = "
                "std::numeric_limits<double>::infinity();") in both
        assert ("inline constexpr double MY_NAN = "
                "std::numeric_limits<double>::quiet_NaN();") in both


class TestTupleSlotOutsideConstantFamilyFallsBack:
    """Boundary: a tuple element the constant family does not cover (a `Char`
    element -- neither scalar nor str) rejects at the slot rather than
    lowering against the literal's own type, which is where a diverging
    element form would come from."""

    SRC = PRELUDE + (
        "PAIR: Final[tuple[Char, Int32]] = (Char(65), 1)\n"
        "\n"
        "def main() -> None:\n"
        "    print(PAIR[1])\n"
        "\n"
        "main()\n"
    )

    def test_falls_back(self):
        routed, ast_rendered, fallback = _constant_positions(self.SRC)
        assert "PAIR" in ast_rendered
        assert fallback.get("final_global:const.tuple_slot") == 1, fallback

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestConstantFallbackIsRatcheted:
    """The constant positions are in the ratchet like every body: they render
    through the same `gen_expr` the cutover deletes, so exempting them
    understated the residue. The exclusion set stays as the mechanism -- an
    empty one, so a new component defaults INTO the ratchet."""

    def test_ratchet_total_counts_every_component(self):
        from .fallback import NON_RATCHET_COMPONENTS, ratchet_total
        tally = {"body:stmt.assign": 2, "ctor:ctor.mil": 1,
                 "class_const:expr.call": 3, "final_global:expr.call": 4}
        assert not NON_RATCHET_COMPONENTS
        assert ratchet_total(tally) == 10
        assert sum(tally.values()) == 10

    def test_exclusion_set_is_still_honoured(self):
        # The mechanism must keep working, or re-populating the set later
        # would be a silent no-op.
        from . import fallback as fb
        tally = {"body:stmt.assign": 2, "class_const:expr.call": 3}
        saved = fb.NON_RATCHET_COMPONENTS
        try:
            fb.NON_RATCHET_COMPONENTS = frozenset({"class_const"})
            assert fb.ratchet_total(tally) == 2
        finally:
            fb.NON_RATCHET_COMPONENTS = saved

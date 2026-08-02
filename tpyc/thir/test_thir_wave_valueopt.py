"""The value-opt read-arm rekeying wave (on the kind-map consolidation).

Whole-optional consumers key on the C++ BINDING, not sema's narrow: a
literal-init owned-view Optional local is proven non-None (every later
read is a narrowed occurrence), yet its None-test renders
`src.has_value()`, its truthiness `::tpy::is_truthy(src)`, and its pass
into an `Own[Optional[StrView]]` element slot goes whole (bare for the
narrowed identity coerce, the `__ov` once-evaluated shim otherwise).
The value-repr Optional ternary wraps both arms in the spelled optional.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile,
    _entry,
)


def _gen(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    outs = {}
    for flag in (False, True):
        outs[flag] = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=flag))
    return compiler, outs[False], outs[True]


class TestOwnedViewLiteralDeclBindingKeyedReads:
    # The formerly fenced literal-init decl: reads after it are narrowed
    # occurrences, but the None-test and truthiness key on the BINDING.
    SRC = (
        "from typing import Optional\n"
        "def main() -> None:\n"
        "    s: Optional[str] = \"one\"\n"
        "    if s is not None:\n"
        "        print(len(s))\n"
        "    print(s if s else \"empty\")\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "std::optional<std::string> s = \"one\";" in thir[1]
        assert "s.has_value()" in thir[1]
        assert "(*s).has_value()" not in thir[1]
        assert "::tpy::is_truthy(s)" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("truthy.value_opt_whole", 0) >= 1


class TestOptViewOwnElemArgRows:
    # The two coercion faces at the Own[Optional[StrView]] element slot:
    # bare for the narrowed identity coerce, the `__ov` shim otherwise.
    SRC = (
        "from tpy import StrView\n"
        "from typing import Optional\n"
        "def maybe() -> Optional[str]:\n"
        "    return \"hello\"\n"
        "def main() -> None:\n"
        "    src1: Optional[str] = \"one\"\n"
        "    src2: Optional[str] = None\n"
        "    src3 = maybe()\n"
        "    items: list[Optional[StrView]] = []\n"
        "    items.append(src1)\n"
        "    items.append(src2)\n"
        "    items.append(src3)\n"
        "    print(items)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "items.push_back(src1);" in thir[1]
        assert ("({ auto __ov = (src2); __ov ? "
                "std::make_optional(std::string_view(*__ov)) : "
                "std::nullopt; })") in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("arg.opt_view_own_bare", 0) >= 1
        assert w.get("arg.opt_view_own_shim", 0) >= 2


class TestValueOptTernaryReturns:
    # Both arms wrap in the spelled optional for C++ ternary deduction.
    SRC = (
        "from typing import Optional\n"
        "def value_or_none(flag: bool) -> Optional[str]:\n"
        "    return None if flag else \"hello\"\n"
        "def main() -> None:\n"
        "    print(value_or_none(False))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert ("return ((flag) ? (std::optional<std::string>(std::nullopt))"
                " : (std::optional<std::string>(\"hello\")));") in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("ifexpr.value_opt", 0) >= 1
        assert w.get("ret.value_opt_view_ternary", 0) >= 1


class TestOwnedViewBytesLiteralDecl:
    # The bytes twin of the literal-init decl + binding-keyed None-test:
    # the family predicates are str/bytes-neutral, so the owned vector decl
    # and the whole-optional has_value render route identically. (The bytes
    # ternary and whole-optional print keep their own pre-existing rejects
    # -- ifexpr.bytes_mixed / print.optstr -- and stay out of this pin.)
    SRC = (
        "from typing import Optional\n"
        "def main() -> None:\n"
        "    b: Optional[bytes] = b\"one\"\n"
        "    if b is not None:\n"
        "        print(len(b))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "b.has_value()" in thir[1]
        assert "(*b).has_value()" not in thir[1]


class TestScalarValueOptTernaryDefers:
    # BOUNDARY: the value-opt ternary gate admits the SCALAR family, but
    # the arm helper lowers only None/str/bytes literal arms -- a scalar
    # Optional[Int32] ternary admits at the gate and defers at the arms.
    SRC = (
        "from tpy import Int32\n"
        "def pick(flag: bool) -> Int32 | None:\n"
        "    return None if flag else Int32(1)\n"
        "def main() -> None:\n"
        "    print(pick(False))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("body:expr.ifexpr") == 1, fb


class TestValueOptTernaryNameArmDefers:
    # BOUNDARY: only the witnessed literal arms are in the ternary slice --
    # a NAME arm (whatever its render would be) keeps the named reject.
    SRC = (
        "from typing import Optional\n"
        "def pick(flag: bool, s: str) -> Optional[str]:\n"
        "    return None if flag else s\n"
        "def main() -> None:\n"
        "    print(pick(False, \"x\"))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert "body:expr.ifexpr" in fb, fb


class TestOptViewParamArgStaysOut:
    # BOUNDARY: a value-opt view PARAM at the Own element slot is the
    # borrow `optional<string_view>` binding -- a different shim family,
    # not witnessed at this slot; the row admits LOCALS only.
    SRC = (
        "from tpy import StrView\n"
        "from typing import Optional\n"
        "def add(items: list[Optional[StrView]], s: Optional[str]) -> None:\n"
        "    items.append(s)\n"
        "def main() -> None:\n"
        "    items: list[Optional[StrView]] = []\n"
        "    add(items, \"x\")\n"
        "    print(items)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert "body:expr.method_call" in fb, fb

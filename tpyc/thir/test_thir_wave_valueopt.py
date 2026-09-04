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
    _reject_tally,
    _assert_rejects_at,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _gen(source):
    """`(compiler, (hpp, cpp))` from one emit -- the compiler is kept for the
    face-witness reads."""
    compiler, modules = _compile(source)
    return compiler, compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False))


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
        compiler, thir = _gen(self.SRC)
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
        compiler, thir = _gen(self.SRC)
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
        compiler, thir = _gen(self.SRC)
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
        compiler, thir = _gen(self.SRC)
        assert "b.has_value()" in thir[1]
        assert "(*b).has_value()" not in thir[1]


class TestScalarValueOptTernaryCtorArm:
    # A scalar type-ctor arm is a scalar-VALUED expression: it renders bare
    # under the optional wrap, like a literal or a scalar name.
    SRC = (
        "from tpy import Int32\n"
        "def pick(flag: bool) -> Int32 | None:\n"
        "    return None if flag else Int32(1)\n"
        "def main() -> None:\n"
        "    print(pick(False))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "std::optional<int32_t>(1)" in thir[1]


class TestScalarNameValueOptTernaryArm:
    # A scalar name renders bare and carries no form facts, so the AST's
    # per-arm `std::optional<T>(...)` wrap composes over it unchanged.
    SRC = (
        "from tpy import Int32\n"
        "def lookup(n: Int32) -> Int32 | None:\n"
        "    return n if n > 0 else None\n"
        "def main() -> None:\n"
        "    print(lookup(1))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, thir = _gen(self.SRC)
        assert "std::optional<int32_t>(n)" in thir[1]

    def test_face_witnessed(self):
        _, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("ifexpr.value_opt_scalar_name", 0) == 1


class TestCharNameValueOptTernaryArmDefers:
    # BOUNDARY: `Char` sits outside `_eligible_scalar` (it shares the str
    # family's view/owned axis), so a Char name arm keeps the named reject.
    SRC = (
        "from tpy import Char\n"
        "def pick(flag: bool, c: Char) -> Char | None:\n"
        "    return c if flag else None\n"
        "def main() -> None:\n"
        "    print(pick(True, Char('a')))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        fb = _reject_tally(self.SRC)
        assert fb.get("body:expr.ifexpr") == 1, fb


class TestValueOptTernaryNonScalarNameArmDefers:
    # BOUNDARY: the ternary slice carries the witnessed literal arms plus a
    # SCALAR name; a name of any other family (here `str`, whose owned/view
    # split is a separate render axis) keeps the named reject.
    SRC = (
        "from typing import Optional\n"
        "def pick(flag: bool, s: str) -> Optional[str]:\n"
        "    return None if flag else s\n"
        "def main() -> None:\n"
        "    print(pick(False, \"x\"))\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        fb = _reject_tally(self.SRC)
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
        _assert_rejects_at(_reject_tally(self.SRC), 'body:expr.method_call', 'method.arg_shape')

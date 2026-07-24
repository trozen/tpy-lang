"""Pins for the constexpr concept-if arm: `if isinstance(x, Protocol):`
lowers to `if constexpr (<concept>)` via the render_concept driver hook.
Spelling pins cover the AST's exact render pairs (concept vs the
Optional[Protocol] nullptr_t special, each under negation); boundary pins
hold the un-mirrored neighbours on fallback (@dynamic checks, the
nullable-protocol `is not None` guard family, branch hoists,
renderer-less resumables)."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    """Generate through the driver path (render_concept seeded) and return
    (emitted hpp+cpp, face witnesses, fallback reasons)."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, compiler._thir_face_witnesses, compiler._thir_fallback


class TestConceptSpelling:
    def test_positive_concept_routes(self):
        src = (
            "from typing import Sized\n"
            "def describe(items: Sized) -> None:\n"
            "    if isinstance(items, Sized):\n"
            "        print(\"sized:\", len(items))\n"
            "    else:\n"
            "        print(\"not sized\")\n"
            "def main() -> None:\n"
            "    describe([10, 20, 30])\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        assert not fallback
        assert "if constexpr (::tpystd::typing::Sized<T_items>)" in out
        _assert_byte_identical(src)

    def test_negated_concept_wraps_parens(self):
        src = (
            "from typing import Sized\n"
            "def check_not(items: Sized) -> None:\n"
            "    if not isinstance(items, Sized):\n"
            "        print(\"not sized\")\n"
            "    else:\n"
            "        print(\"sized:\", len(items))\n"
            "def main() -> None:\n"
            "    check_not([1, 2])\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        assert not fallback
        assert "if constexpr ((!(::tpystd::typing::Sized<T_items>)))" in out
        _assert_byte_identical(src)

    def test_optional_protocol_same_as_positive(self):
        # Nullable single-protocol param: the concept is swapped for the
        # nullptr_t same_as special (branch bodies read nothing from the
        # subject -- its deref-read form is un-mirrored and rejects).
        src = (
            "from typing import Sized\n"
            "def probe(x: Sized | None) -> None:\n"
            "    if isinstance(x, Sized):\n"
            "        print(\"some\")\n"
            "    else:\n"
            "        print(\"none\")\n"
            "def main() -> None:\n"
            "    probe([1])\n"
            "    probe(None)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        assert "if constexpr (!std::same_as<T_x, std::nullptr_t>)" in out
        _assert_byte_identical(src)

    def test_optional_protocol_same_as_negated(self):
        # `not isinstance` flips the same_as polarity DIRECTLY -- no
        # `(!(...))` wrap (gen_truthy_expr's unary-! special).
        src = (
            "from typing import Sized\n"
            "def probe(x: Sized | None) -> None:\n"
            "    if not isinstance(x, Sized):\n"
            "        print(\"none\")\n"
            "    else:\n"
            "        print(\"some\")\n"
            "def main() -> None:\n"
            "    probe([1])\n"
            "    probe(None)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        assert "if constexpr (std::same_as<T_x, std::nullptr_t>)" in out
        _assert_byte_identical(src)


class TestConstexprBoundaries:
    def test_dynamic_protocol_stays_off_arm(self):
        # sema sets isinstance_is_protocol only for the static concept
        # family; a @dynamic-subject class narrowing (runtime cast) must
        # never witness the constexpr face.
        src = (
            "from typing import Protocol\n"
            "from tpy import dynamic\n"
            "from tplib.box import Box\n"
            "@dynamic\n"
            "class Pet(Protocol):\n"
            "    def name(self) -> str: ...\n"
            "class Dog(Pet):\n"
            "    def name(self) -> str:\n"
            "        return \"dog\"\n"
            "    def bark(self) -> str:\n"
            "        return \"woof\"\n"
            "class Cat(Pet):\n"
            "    def name(self) -> str:\n"
            "        return \"cat\"\n"
            "def describe(b: Box[Pet]) -> str:\n"
            "    if isinstance(b, Dog):\n"
            "        return \"dog:\" + b.bark()\n"
            "    return \"other:\" + b.name()\n"
            "def main() -> None:\n"
            "    print(describe(Box(Dog())))\n"
            "    print(describe(Box(Cat())))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not faces.get("if.constexpr_concept")
        _assert_byte_identical(src)

    def test_nullproto_union_guard_rejects(self):
        # `items is not None` on a nullable protocol-union param is the
        # AST's constexpr nullptr_t guard (+ deref reads) -- un-mirrored,
        # so the if rejects instead of routing the union monostate test.
        src = (
            "from typing import Sized, Sequence\n"
            "def process(items: Sized | Sequence[int] | None = None) -> int:\n"
            "    if items is not None:\n"
            "        if isinstance(items, Sized):\n"
            "            return len(items)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(process([1, 2]))\n"
            "    print(process())\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert any("constexpr_nullproto_guard" in r for r in fallback)
        _assert_byte_identical(src)

    def test_optional_protocol_none_guard_rejects(self):
        src = (
            "from typing import Sized\n"
            "def probe(x: Sized | None) -> int:\n"
            "    if x is not None:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe([1]))\n"
            "    print(probe(None))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert any("constexpr_nullproto_guard" in r for r in fallback)
        _assert_byte_identical(src)

    def test_plain_union_monostate_unaffected(self):
        # The guard reject is protocol-member-keyed: an ordinary union
        # None-test keeps routing through the monostate arm.
        src = (
            "from tpy import Int32\n"
            "class Dog:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "def probe(v: Int32 | Dog | None) -> int:\n"
            "    if v is not None:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe(3))\n"
            "    print(probe(None))\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("isnone.union_monostate", 0) >= 1
        assert "std::holds_alternative<std::monostate>" in out
        _assert_byte_identical(src)

    def test_branch_hoist_rejects(self):
        # A var first declared in both branches and read after hoists
        # (if_branch_decls) -- the constexpr arm rejects the hoist shape.
        src = (
            "from typing import Sized\n"
            "def describe(items: Sized) -> None:\n"
            "    if isinstance(items, Sized):\n"
            "        n = 1\n"
            "    else:\n"
            "        n = 2\n"
            "    print(n)\n"
            "def main() -> None:\n"
            "    describe([1, 2])\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert any("if.constexpr" in r for r in fallback)
        _assert_byte_identical(src)

    def test_mixed_constexpr_runtime_elif_chain(self):
        # is_constexpr is PER-NODE: a chain can mix a concept-if member
        # with an ordinary runtime elif (`if constexpr` then `} else if`).
        src = (
            "from typing import Sized\n"
            "from tpy import Int32\n"
            "def probe(items: Sized, n: Int32) -> None:\n"
            "    if isinstance(items, Sized):\n"
            "        print(\"sized\")\n"
            "    elif n > 0:\n"
            "        print(\"pos\")\n"
            "    else:\n"
            "        print(\"neg\")\n"
            "def main() -> None:\n"
            "    probe([1], 2)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        assert "if constexpr (::tpystd::typing::Sized<T_items>)" in out
        _assert_byte_identical(src)

    def test_compound_condition_shape_rejects(self):
        # `isinstance(x, P) and y` carries facts past the pure-isinstance
        # shape -- the constexpr arm must not witness (compound conditions
        # never set isinstance_var on the whole condition).
        src = (
            "from typing import Sized\n"
            "def probe(items: Sized, flag: bool) -> None:\n"
            "    if isinstance(items, Sized) and flag:\n"
            "        print(\"both\")\n"
            "    else:\n"
            "        print(\"no\")\n"
            "def main() -> None:\n"
            "    probe([1], True)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not faces.get("if.constexpr_concept")
        _assert_byte_identical(src)

    def test_ternary_protocol_union_none_test_rejects(self):
        # The EXPRESSION-position None-test on a protocol union hits the
        # binop-level monostate arm, whose protocol-member reject fences
        # the AST's ptr-compare render (statement guards never run here).
        src = (
            "from typing import Sized, Sequence\n"
            "def probe(items: Sized | Sequence[int] | None = None) -> int:\n"
            "    r = 1 if items is not None else 0\n"
            "    return r\n"
            "def main() -> None:\n"
            "    print(probe([1]))\n"
            "    print(probe())\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert any("union_none_protocol_subject" in r for r in fallback)
        _assert_byte_identical(src)

    def test_resumable_keeps_rejecting(self):
        # A CFG-lowered frame branch can't be constexpr (state writes
        # differ per branch), so the AST emits the concept as a RUNTIME
        # `if (...)` there; resumable lowering builds its ctx without
        # render_concept, so the frame keeps falling back to that render.
        src = (
            "from typing import Sized, Iterator\n"
            "from tpy import Int32\n"
            "def gen(items: Sized) -> Iterator[Int32]:\n"
            "    if isinstance(items, Sized):\n"
            "        yield 1\n"
            "    yield 2\n"
            "def main() -> None:\n"
            "    for v in gen([1]):\n"
            "        print(v)\n"
            "main()\n"
        )
        out, faces, fallback = _gen_thir(src)
        assert not faces.get("if.constexpr_concept")
        assert "if (::tpystd::typing::Sized<T_items>)" in out
        _assert_byte_identical(src)

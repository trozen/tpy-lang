"""Pins for the constexpr concept-if arm: `if isinstance(x, Protocol):`
lowers to `if constexpr (<concept>)` via the render_concept driver hook.
Spelling pins cover the AST's exact render pairs (concept vs the
Optional[Protocol] nullptr_t special, each under negation); boundary pins
hold the un-mirrored neighbours on fallback (@dynamic checks, the
nullable-protocol `is not None` guard family, branch hoists,
renderer-less resumables)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at, _assert_byte_identical, _compile, _entry,
                      _reject_tally)


def _gen_thir(source: str):
    """Generate through the driver path (render_concept seeded) and return
    (emitted hpp+cpp, face witnesses, fallback reasons)."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, compiler._thir_face_witnesses


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
        out, faces = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
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
        out, faces = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
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
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.optional")

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
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.optional")


class TestCtorConstexpr:
    _METER = (
        "from typing import Sized\n"
        "from tpy import Int32\n"
        "class Meter:\n"
        "    n: Int32\n"
        "    def __init__(self, items: Sized) -> None:\n"
        "        if isinstance(items, Sized):\n"
        "            self.n = len(items)\n"
        "        else:\n"
        "            self.n = -1\n"
    )

    def test_ctor_body_constexpr_routes(self):
        # lower_constructor threads render_concept, so a ctor-body
        # concept-if routes like a function body's.
        src = self._METER + (
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2, 3]\n"
            "    m = Meter(xs)\n"
            "    print(m.n)\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("if.constexpr_concept", 0) >= 1
        _assert_byte_identical(src)

    def test_ctor_structural_rvalue_protocol_arg_rejects(self):
        # A structural (non-@dynamic) RVALUE at a ctor protocol slot is
        # ctor-INLINE on the AST path (no temp arm in the ctor loop);
        # the shared protocol temp row must not fire -- the body falls
        # back and keeps the inline render.
        src = self._METER + (
            "def main() -> None:\n"
            "    m = Meter([1, 2, 3])\n"
            "    print(m.n)\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert any("expr.call" in r for r in fallback)

    def test_ctor_dynamic_rvalue_protocol_arg_routes(self):
        # A @dynamic-slot ctor arg RVALUE keeps the adapter temp row (the
        # AST ctor loop runs _gen_dynamic_protocol_arg) -- witnessed here,
        # since no corpus case passes a direct rvalue conformer.
        src = (
            "from typing import Protocol\n"
            "from tpy import dynamic, Int32\n"
            "@dynamic\n"
            "class Greeter(Protocol):\n"
            "    def hello(self) -> Int32: ...\n"
            "class En(Greeter):\n"
            "    def hello(self) -> Int32:\n"
            "        return 1\n"
            "class Holder:\n"
            "    n: Int32\n"
            "    def __init__(self, g: Greeter) -> None:\n"
            "        self.n = g.hello()\n"
            "def main() -> None:\n"
            "    h = Holder(En())\n"
            "    print(h.n)\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
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
        out, faces = _gen_thir(src)
        assert not faces.get("if.constexpr_concept")
        _assert_byte_identical(src)

    def test_nullproto_union_guard_routes(self):
        # `items is not None` on a nullable protocol-union param takes the
        # constexpr nullptr_t swap (`if constexpr (!std::same_as<T_items,
        # std::nullptr_t>)`), with the nested protocol-isinstance fold
        # riding _lower_constexpr_if unchanged and branch reads deref'd.
        src = (
            "from typing import Sized, Sequence\n"
            "def process(items: Sized | Sequence[int] | None = None) -> int:\n"
            "    if items is not None:\n"
            "        if isinstance(items, Sized):\n"
            "            return len(items)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    xs: list[int] = [1, 2]\n"
            "    print(process(xs))\n"
            "    print(process())\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("if.nullproto_guard", 0) >= 1
        assert "if constexpr (!std::same_as<T_items, std::nullptr_t>)" in out
        assert "::tpy::__len__((*items))" in out
        _assert_byte_identical(src)

    def test_optional_protocol_none_guard_routes(self):
        src = (
            "from typing import Sized\n"
            "def probe(x: Sized | None) -> int:\n"
            "    if x is not None:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    xs: list[int] = [1]\n"
            "    print(probe(xs))\n"
            "    print(probe(None))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("if.nullproto_guard", 0) >= 1
        assert faces.get("arg.nullproto_none", 0) >= 1
        assert "if constexpr (!std::same_as<T_x, std::nullptr_t>)" in out
        # The None call arg spells the typed null.
        assert "probe(static_cast<std::nullptr_t*>(nullptr))" in out
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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.arg_shape.union")

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
        fallback = _reject_tally(src)
        assert any("if.constexpr" in r for r in fallback)

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
        out, faces = _gen_thir(src)
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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.cond_binop.&&.scalar_scalar:truthy.call_nonbool")

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
        fallback = _reject_tally(src)
        assert any("union_none_protocol_subject" in r for r in fallback)

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
        _assert_rejects_at(_reject_tally(src), "resumable:res.narrowed_resume")


class TestNullprotoGuardFamily:
    """Queue item 5 P-B additions: the guard-swap chain shapes, the
    method-position lift, and the boundaries the dualgen probes fixed."""

    def test_guard_elif_chain_routes(self):
        # An elif after the guard renders the flat `} else if (...)` off
        # the constexpr arm -- the nested else-if lowering emits the same
        # chain the AST's per-node guard logic writes.
        src = (
            "from typing import Optional, Sized\n"
            "from tpy import Int32\n"
            "def pick(a: Optional[Sized] = None, flag: bool = False)"
            " -> Int32:\n"
            "    if a is not None:\n"
            "        return len(a)\n"
            "    elif flag:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2]\n"
            "    print(pick(xs))\n"
            "    print(pick(None, True))\n"
            "    print(pick())\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("if.nullproto_guard", 0) >= 1
        assert "if constexpr (!std::same_as<T_a, std::nullptr_t>)" in out
        _assert_byte_identical(src)

    def test_method_nullable_proto_arg_lifts(self):
        # The METHOD position takes the same addr/typed-null lifts as the
        # free call (`c.update(nums, &(more))`); the required-union method
        # slot keeps its bare template bind (TestStructProtoUnionArg).
        src = (
            "from typing import Optional, Sized\n"
            "from tpy import Int32\n"
            "class Counter:\n"
            "    count: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.count = 0\n"
            "    def update(self, items: Sized,"
            " extra: Optional[Sized] = None) -> None:\n"
            "        self.count = len(items)\n"
            "        if extra is not None:\n"
            "            self.count = self.count + len(extra)\n"
            "def main() -> None:\n"
            "    c = Counter()\n"
            "    nums: list[Int32] = [1, 2]\n"
            "    more: list[Int32] = [3]\n"
            "    c.update(nums, more)\n"
            "    print(c.count)\n"
            "    c.update(nums, None)\n"
            "    print(c.count)\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("arg.nullable_proto_addr", 0) >= 2
        assert "c.update(nums, &(more));" in out
        assert ("c.update(nums, static_cast<std::nullptr_t*>(nullptr));"
                in out)
        _assert_byte_identical(src)

    def test_container_literal_at_nullable_slot_still_defers(self):
        # BOUNDARY: a container LITERAL at the nullable-protocol slot is
        # the AST's temp-materializing tail (`&(__tmp_N)` over an inferred
        # temp) -- unadmitted, byte-identical fallback.
        src = (
            "from typing import Optional, Sized\n"
            "def probe(x: Optional[Sized] = None) -> int:\n"
            "    if x is not None:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe([1]))\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert any("call.arg_shape" in r for r in fallback)

    def test_dyn_member_is_not_none_still_defers(self):
        # BOUNDARY: the @dynamic-member flavor keeps the runtime compare
        # on the AST path -- the swap is static-protocols-only.
        src = (
            "from typing import Protocol, Optional\n"
            "from tpy import dynamic\n"
            "@dynamic\n"
            "class Pet(Protocol):\n"
            "    def name(self) -> str: ...\n"
            "class Dog(Pet):\n"
            "    def name(self) -> str:\n"
            "        return \"dog\"\n"
            "def greet(p: Optional[Pet]) -> str:\n"
            "    if p is not None:\n"
            "        return p.name()\n"
            "    return \"<none>\"\n"
            "def main() -> None:\n"
            "    print(greet(Dog()))\n"
            "    print(greet(None))\n"
            "main()\n"
        )
        fallback = _reject_tally(src)
        assert any("constexpr_nullproto_guard" in r for r in fallback)


class TestRequiredUnionPassOnward:
    def test_narrowed_param_pass_onward_routes(self):
        # The widened _required_protocol_union_arg rows: a guard-narrowed
        # nullable-protocol param forwarded into the REQUIRED protocol-
        # union slot derefs (`total((*items))`).
        src = (
            "from typing import Iterable\n"
            "from tpy import Int32, Spannable, span\n"
            "def total(items: Spannable[Int32] | Iterable[Int32]) -> Int32:\n"
            "    if isinstance(items, Spannable):\n"
            "        s = span(items)\n"
            "        result: Int32 = 0\n"
            "        for x in s:\n"
            "            result += x\n"
            "        return result\n"
            "    else:\n"
            "        result2: Int32 = 0\n"
            "        for x2 in items:\n"
            "            result2 += x2\n"
            "        return result2\n"
            "def maybe_total(items: Spannable[Int32] | Iterable[Int32]"
            " | None) -> Int32:\n"
            "    if items is not None:\n"
            "        return total(items)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    nums: list[Int32] = [10, 20, 30]\n"
            "    print(maybe_total(nums))\n"
            "    print(maybe_total(None))\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert faces.get("arg.required_protocol_union", 0) >= 1
        assert "return total((*items));" in out

    def test_nullproto_hoist_still_defers(self):
        # BOUNDARY: a guard-branch decl used AFTER the branch needs the
        # AST's hoist machinery -- the guard arm rejects (nullproto_hoist)
        # and the body stays byte-identical via fallback.
        src = (
            "from typing import Optional, Sized\n"
            "from tpy import Int32\n"
            "def probe(x: Optional[Sized] = None) -> Int32:\n"
            "    if x is not None:\n"
            "        n = len(x)\n"
            "    else:\n"
            "        n = 0\n"
            "    return n\n"
            "def main() -> None:\n"
            "    xs: list[Int32] = [1, 2]\n"
            "    print(probe(xs))\n"
            "    print(probe())\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.nullproto_hoist")

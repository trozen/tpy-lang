"""Pins for the generic-type INSTANTIATION gate: the shapes the `call.inst_shape`
reject used to swallow, plus the neighbours that must keep rejecting there."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)


def _fallback_reasons(src: str) -> set:
    """The body-component fallback reasons, sans the `body:` prefix and any
    statement-position qualifier -- the only honest claim for a widening that
    moves a reject down a layer rather than routing the body."""
    from ..codegen_cpp.context import CodeGenOptions
    from .testutil import _compile, _entry
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
    return {k.split(":")[-1] for k in compiler._thir_fallback
            if k.startswith("body:")}


def _cpp(src: str) -> str:
    from ..codegen_cpp.context import CodeGenOptions
    from .testutil import _compile, _entry
    compiler, modules = _compile(src)
    _hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
    return cpp


_PRELUDE = "from tpy import Int32\n"


class TestArrayLiteralInstantiation:
    SRC = _PRELUDE + (
        "from tpy import Array\n"
        "def main() -> None:\n"
        "    arr = Array[Int32, 3]([10, 20, 30])\n"
        "    print(arr[0])\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.array_literal_instantiation"] >= 1

    def test_renders_the_spelled_array_over_braces(self):
        assert "std::array<int32_t, 3>({10, 20, 30})" in _cpp(self.SRC)

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_element_target_reaches_the_literal(self):
        # The arm hands the literal `call_type` as its brace target, which is
        # what puts the Float32 `f` suffix on the elements -- a target-less
        # lowering would render bare doubles.
        src = _PRELUDE + (
            "from tpy import Array, Float32\n"
            "def main() -> None:\n"
            "    arr = Array[Float32, 2]([1.5, 2.5])\n"
            "    print(arr[0])\n"
        )
        assert "std::array<float, 2>({1.5f, 2.5f})" in _cpp(src)
        _assert_byte_identical(src)

    def test_list_over_an_array_literal_is_not_this_arm(self):
        # The boundary: `list([...])` reaches the same AST tail but is a
        # STORAGE container spelled from its own ctor slot -- it must not be
        # claimed by the Array-shaped arm. Asserting the SIBLING face fires is
        # what keeps this non-vacuous: a regression to full fallback would
        # satisfy the zero-count and the byte-diff on its own.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    out = list([1, 2, 3])\n"
            "    print(len(out))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.array_literal_instantiation", 0) == 0
        assert faces["call.container_literal_instantiation"] >= 1
        _assert_byte_identical(src)

    def test_user_ctor_with_a_container_param_is_not_this_arm(self):
        # The boundary the arm's `not fi.params` guard exists for: a user ctor
        # taking a real container slot reaches the same AST tail, where the
        # literal renders against that slot rather than against `call_type`.
        src = _PRELUDE + (
            "class Bag:\n"
            "    xs: list[Int32]\n"
            "    def __init__(self, xs: list[Int32]) -> None:\n"
            "        self.xs = xs\n"
            "def main() -> None:\n"
            "    b = Bag([1, 2, 3])\n"
            "    print(len(b.xs))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.array_literal_instantiation", 0) == 0
        assert faces["ctor.container_literal_arg"] >= 1
        _assert_byte_identical(src)


class TestSpanTemplateInstantiation:
    SRC = _PRELUDE + (
        "from tpy import Array, Span, take_ptr\n"
        "def main() -> None:\n"
        "    arr = Array[Int32, 3]([10, 20, 30])\n"
        "    p = take_ptr(arr[0])\n"
        "    s = Span(p, 3)\n"
        "    print(s[0])\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.span_instantiation"] >= 1

    def test_renders_the_ctor_template(self):
        assert ("std::span<int32_t>(p, static_cast<size_t>(3))"
                in _cpp(self.SRC))

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_bare_source_span_keeps_the_direct_init_arm(self):
        # The boundary: `Span[readonly[Int32]](lst)` resolves NO template ctor,
        # so it must keep taking the spelled direct-init render -- placing the
        # template arm first must not steal it.
        src = _PRELUDE + (
            "from tpy import Span, readonly\n"
            "def main() -> None:\n"
            "    lst: list[Int32] = [10, 20]\n"
            "    ros: Span[readonly[Int32]] = Span[readonly[Int32]](lst)\n"
            "    print(len(ros))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.span_instantiation", 0) == 0
        assert faces["call.view_instantiation"] >= 1
        _assert_byte_identical(src)


class TestRecordElementInstantiation:
    """The instantiation result gate is element-BLIND: the ctor template already
    spells the whole container, so a record element renders what a scalar one
    does."""

    SRC = _PRELUDE + (
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "def main() -> None:\n"
        "    src: list[Node] = [Node(1), Node(2)]\n"
        "    out = list(src)\n"
        "    print(len(out))\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.instantiation_template"] >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_exotic_element_families_route(self):
        # The element families the old `_container_scalar_read` verdict
        # rejected and the element-blind gate newly admits. Byte-identity is
        # the claim under test: the ctor template spells the whole container,
        # so none of these element shapes changes the render.
        for elem, init in (("Int32 | None", "[1, None]"),
                           ("list[Int32]", "[[1, 2], [3]]"),
                           ("str", "[\"a\", \"b\"]")):
            src = _PRELUDE + (
                "def main() -> None:\n"
                f"    src: list[{elem}] = {init}\n"
                "    out = list(src)\n"
                "    print(len(out))\n"
            )
            _thir, faces = _lower_ctx_witnessed(src)
            assert faces["call.instantiation_template"] >= 1, elem
            _assert_byte_identical(src)

    def test_tuple_element_view_instantiation(self):
        # `list(d.items())` -- a tuple element, which the element-keyed decl
        # verdict rejects and this gate must not.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    d: dict[str, Int32] = {\"a\": 1}\n"
            "    items = list(d.items())\n"
            "    print(len(items))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["call.instantiation_template"] >= 1
        _assert_byte_identical(src)


class TestSpelledTypeArgsInstantiation:
    SRC = _PRELUDE + (
        "def main() -> None:\n"
        "    items = list[Int32](range(5))\n"
        "    print(len(items))\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.instantiation_template"] >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestContainerLiteralInstantiation:
    SRC = _PRELUDE + (
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "    def __hash__(self) -> Int32:\n"
        "        return self.val\n"
        "    def __eq__(self, other: 'Node') -> bool:\n"
        "        return self.val == other.val\n"
        "def main() -> None:\n"
        "    s = set([Node(2)])\n"
        "    print(len(s))\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.container_literal_instantiation"] >= 1

    def test_renders_the_spelled_set_over_braces(self):
        assert "::tpy::ordered_set<Node>({Node(2)})" in _cpp(self.SRC)

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_bigint_element_keeps_the_bare_literal(self):
        # The literal lowers against a LIST of the RESULT's element slot, not
        # against its own sema type -- a read-only literal demotes to
        # `Array[T, N]`, whose element retype would wrap the small literal as
        # `::tpy::BigInt(2)` where the AST renders it bare.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    a = list([1000000000000000000000, 2])\n"
            "    print(a[0])\n"
        )
        cpp = _cpp(src)
        assert ("std::vector<::tpy::BigInt>({"
                "::tpy::BigInt::from_str(\"1000000000000000000000\"), 2})"
                in cpp)
        _assert_byte_identical(src)

    def test_dict_over_a_literal_keeps_rejecting(self):
        # The boundary: a DICT result retargets its elements to `tuple[K, V]`,
        # a derivation this arm's list-of-slot target does not reproduce.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    d = dict([(\"a\", Int32(1))])\n"
            "    print(len(d))\n"
        )
        assert _fallback_reasons(src) == {"expr.call"}

    def test_str_element_keeps_rejecting(self):
        # The boundary: a str element slot IS one the AST derives an element
        # target for, so it stays out of the target-substituting arm.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    a = list([\"x\", \"y\"])\n"
            "    print(a[0])\n"
        )
        assert _fallback_reasons(src) == {"expr.call"}


_RVALUE_PRELUDE = _PRELUDE + (
    "from tpy import Own, copy\n"
    "class Node:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32) -> None:\n"
    "        self.val = val\n"
    "    def __hash__(self) -> Int32:\n"
    "        return self.val\n"
    "    def __eq__(self, other: 'Node') -> bool:\n"
    "        return self.val == other.val\n"
    "def make_nodes() -> Own[list[Node]]:\n"
    "    return [Node(1)]\n"
)


class TestInstantiationCallRvalueArg:
    SRC = _RVALUE_PRELUDE + (
        "def main() -> None:\n"
        "    a = set(make_nodes())\n"
        "    print(len(a))\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.inst_call_rvalue_arg"] >= 1

    def test_renders_the_bare_call_in_the_template(self):
        assert "::tpy::set_construct<Node>(make_nodes())" in _cpp(self.SRC)

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_copy_rvalue_arg(self):
        # `copy(b)` is an rvalue whose own lowering already exists; the arg
        # ladder just had no arm reaching it.
        src = _RVALUE_PRELUDE + (
            "def main() -> None:\n"
            "    b: list[Node] = [Node(1)]\n"
            "    a = set(copy(b))\n"
            "    print(len(b), len(a))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["call.inst_call_rvalue_arg"] >= 1
        _assert_byte_identical(src)

    def test_borrowed_return_keeps_rejecting(self):
        # The boundary: a call returning a BORROW is an lvalue, so the
        # own_iter / last-use reasoning the bare-name branch applies is still
        # live for it -- the rvalue arm must not claim it.
        src = _RVALUE_PRELUDE + (
            "def borrow(xs: list[Node]) -> list[Node]:\n"
            "    return xs\n"
            "def main() -> None:\n"
            "    b: list[Node] = [Node(1)]\n"
            "    a = list(borrow(b))\n"
            "    print(len(a), len(b))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.inst_call_rvalue_arg", 0) == 0
        assert _fallback_reasons(src) == {"expr.call"}

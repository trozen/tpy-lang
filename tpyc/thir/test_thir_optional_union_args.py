"""Free-call args at Optional / union slots: the value-repr-Optional
member-arg admission (bare render via the generic tail), the pointer-repr
Optional container-name faces (`&(name)` / `nullptr`), and the
pointer-variant union ctor-rvalue arg temp (`pv{&__tmp_N}`) -- routed emits
plus the position/shape rejects that must keep falling back."""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .nodes import (
    THIRArgTemp, THIRCall, THIRContainerLiteral, THIRExprStmt, THIRLiteral,
    THIROptionalPtrArg, THIRPrint, THIRUnionArgLift,
)
from .testutil import (_assert_byte_identical, _assert_routes_byte_identical,
                       _fn, _lower, _lower_ctx, _lower_ctx_witnessed)

_PRELUDE = "from tpy import Int32\nfrom typing import Optional\n"

_UNION_RECORDS = (
    "from tpy import Int32\n"
    "class Dog:\n"
    "    legs: Int32\n"
    "    def __init__(self, legs: Int32) -> None:\n        self.legs = legs\n"
    "class Cat:\n"
    "    tag: Int32\n"
    "    def __init__(self, tag: Int32) -> None:\n        self.tag = tag\n"
    "def check(a: Dog | Cat) -> bool:\n"
    "    return True\n"
)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


class TestValueOptMemberArgs:
    def test_int_literal_and_none_route_bare(self):
        thir, faces = _lower_ctx_witnessed(_PRELUDE + (
            "def take(x: Optional[Int32]) -> None:\n    pass\n"
            "def f() -> None:\n"
            "    take(5)\n"
            "    take(None)\n"))
        assert _body(thir, "f") == (
            "    take(5);\n"
            "    take(std::nullopt);\n")
        assert faces.get("call.optval_member", 0) >= 1

    def test_str_literal_routes_bare(self):
        thir = _lower_ctx(_PRELUDE + (
            "def take(s: Optional[str]) -> None:\n    pass\n"
            "def f() -> None:\n    take(\"hello\")\n"))
        assert _body(thir, "f") == '    take("hello");\n'

    def test_member_name_routes_bare(self):
        thir = _lower_ctx(_PRELUDE + (
            "def take(x: Optional[Int32]) -> None:\n    pass\n"
            "def f(n: Int32) -> None:\n    take(n)\n"))
        assert _body(thir, "f") == "    take(n);\n"

    def test_enum_member_routes_fixed_spelling(self):
        thir = _lower_ctx(_PRELUDE + (
            "from enum import Enum, auto\n"
            "class Color(Enum):\n    Red = auto()\n    Green = auto()\n"
            "def take(c: Optional[Color]) -> None:\n    pass\n"
            "def f() -> None:\n    take(Color.Red)\n"))
        assert _body(thir, "f") == "    take(Color::Red);\n"

    def test_scalar_ctor_arg_folds_into_bare_literal(self):
        thir = _lower_ctx(_PRELUDE + (
            "def take(x: Optional[Int32]) -> None:\n    pass\n"
            "def f() -> None:\n    take(Int32(0))\n"))
        assert _body(thir, "f") == "    take(0);\n"

    def test_bytes_rvalue_routes_bare(self):
        # The value-repr Optional[bytes] slot absorbs the owned concat rvalue
        # (for_narrowed_optional_view's main shape).
        thir = _lower_ctx(
            "def take(b: bytes | None) -> None:\n    pass\n"
            "def f() -> None:\n    take(b\"ab\" + b\"c\")\n")
        assert _body(thir, "f") == (
            "    take((::tpy::bytes_concat(::tpy::bytes_literal_owned"
            '("ab", 2), ::tpy::bytes_literal_owned("c", 1))));\n')

    def test_narrowed_optional_local_arg_passes_whole_optional(self):
        # A narrowed value-opt LOCAL's C++ binding is still the optional, so
        # passing it into an Optional param passes the WHOLE optional bare
        # (`take(x)`), NOT the narrowed `(*x)` deref -- _lower_call_arg strips
        # the deref-on-narrow at the optional-slot arg boundary.
        thir = _lower_ctx(_PRELUDE + (
            "def take(x: Optional[Int32]) -> None:\n    pass\n"
            "def give() -> Optional[Int32]:\n    return 5\n"
            "def f() -> None:\n"
            "    x = give()\n"
            "    if x is not None:\n"
            "        take(x)\n"))
        assert _body(thir, "f") == (
            "    std::optional<int32_t> x = give();\n"
            "    if ((x.has_value())) {\n"
            "        take(x);\n"
            "    }\n")


class TestOptionalPtrContainerArgs:
    def test_container_name_and_none_route(self):
        thir, faces = _lower_ctx_witnessed(_PRELUDE + (
            "def take(xs: list[Int32] | None) -> None:\n    pass\n"
            "def f() -> None:\n"
            "    data: list[Int32] = [1, 2]\n"
            "    take(data)\n"
            "    take(None)\n"))
        assert _body(thir, "f") == (
            "    std::vector<int32_t> data = {1, 2};\n"
            "    take(&(data));\n"
            "    take(nullptr);\n")
        assert faces.get("optptr.name", 0) >= 1
        assert faces.get("optptr.none", 0) >= 1

    def test_optional_declared_name_routes(self):
        # Forwarding a `list | None` BINDING is the pass face (already `T*`)
        # -- not lowered for containers; keep the precise reject.
        thir = _lower_ctx(_PRELUDE + (
            "def take(xs: list[Int32] | None) -> None:\n    pass\n"
            "def f(xs: list[Int32] | None) -> None:\n    take(xs)\n"))
        assert _fn(thir, "f") is not None  # the wide-pointee pass face


class TestUnionCtorTempArg:
    def test_ctor_rvalue_hoists_temp_in_flush_position(self):
        thir, faces = _lower_ctx_witnessed(
            _UNION_RECORDS + "def f() -> None:\n    check(Dog(3))\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRExprStmt) and isinstance(stmt.expr, THIRCall)
        arg = stmt.expr.args[0]
        assert isinstance(arg, THIRUnionArgLift) and arg.temp_cpp == "Dog"
        assert _body(thir, "f") == (
            "    Dog __tmp_1 = Dog(3);\n"
            "    check(std::variant<Cat*, Dog*>{&__tmp_1});\n")
        assert faces.get("unionlift.ctor_temp", 0) >= 1

    def test_bigint_ctor_arg_renders_inside_temp_init(self):
        # An `int` field ctor arg takes the BigInt wrap inside the hoisted
        # temp's init, exactly like the AST's target-threaded ctor render.
        thir = _lower_ctx(
            "class Dog:\n"
            "    legs: int\n"
            "    def __init__(self, legs: int) -> None:\n        self.legs = legs\n"
            "class Cat:\n"
            "    tag: int\n"
            "    def __init__(self, tag: int) -> None:\n        self.tag = tag\n"
            "def check(a: Dog | Cat) -> bool:\n    return True\n"
            "def f() -> None:\n    check(Dog(3))\n")
        assert _body(thir, "f") == (
            "    Dog __tmp_1 = Dog(::tpy::BigInt(3));\n"
            "    check(std::variant<Cat*, Dog*>{&__tmp_1});\n")

    def test_ctor_rvalue_in_condition_routes_per_iteration(self):
        # A while condition re-evaluates per iteration: both paths place the
        # union-lift ctor temp in the RESTRUCTURED loop head (`while (true)
        # { Dog __tmp_1 = Dog(3); if (!(check(...))) break; }` -- the old
        # hoist-once-before-the-loop miscompile is gone since the AST
        # restructure, and the mirror reproduces it byte-identically).
        src = (_UNION_RECORDS
               + "def f() -> None:\n"
               + "    while check(Dog(3)):\n"
               + "        pass\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_member_name_lift_still_routes(self):
        # Regression guard: the temp arm must not shadow the temp-free
        # member-name lift.
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f(d: Dog) -> None:\n    check(d)\n")
        stmt = _fn(thir, "f").body[0]
        arg = stmt.expr.args[0]
        assert isinstance(arg, THIRUnionArgLift) and arg.temp_cpp is None
        assert _body(thir, "f") == (
            "    check(std::variant<Cat*, Dog*>{&(d)});\n")


_MIXED_UNION = (
    "from tpy import Int32\n"
    "class Dog:\n"
    "    name: str\n"
    "    def __init__(self, name: str) -> None:\n        self.name = name\n"
    "def check(v: Int32 | Dog | None) -> bool:\n"
    "    return v is None\n"
)


class TestUnionNoneTest:
    """The union-binding `is [not] None` monostate arm and the mixed
    scalar+record ptr-union family it unblocks."""

    def test_union_param_none_test_renders_monostate_holds(self):
        thir, faces = _lower_ctx_witnessed(_MIXED_UNION + (
            "def f(v: Int32 | Dog | None) -> bool:\n"
            "    return v is not None\n"))
        assert _body(thir, "f") == (
            "    return (!std::holds_alternative<std::monostate>(v));\n")
        assert faces.get("isnone.union_monostate", 0) >= 1

    def test_commuted_none_is_v_routes(self):
        thir = _lower_ctx(_MIXED_UNION + (
            "def f(v: Int32 | Dog | None) -> bool:\n"
            "    return None is v\n"))
        assert _body(thir, "f") == (
            "    return (std::holds_alternative<std::monostate>(v));\n")

    def test_mixed_union_isinstance_routes_byte_identical(self):
        src = _MIXED_UNION + (
            "def f(v: Int32 | Dog | None) -> str:\n"
            "    if v is None:\n"
            "        return \"none\"\n"
            "    if isinstance(v, Int32):\n"
            "        return \"int\"\n"
            "    return \"other\"\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_scalar_ctor_into_mixed_union_hoists_member_temp(self):
        thir, faces = _lower_ctx_witnessed(_MIXED_UNION + (
            "def f() -> None:\n    check(Int32(1))\n"))
        assert _body(thir, "f") == (
            "    int32_t __tmp_1 = 1;\n"
            "    check(std::variant<std::monostate, Dog*, int32_t*>"
            "{&__tmp_1});\n")
        assert faces.get("unionlift.ctor_temp", 0) >= 1

    def test_scalar_ctor_decl_slot_lifts(self):
        src = _MIXED_UNION + (
            "def f() -> None:\n"
            "    a: Int32 | Dog | None = Int32(42)\n"
            "    print(check(a))\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_wrapper_union_none_test_routes(self):
        # A recursive-alias wrapper binding reads the variant through
        # `.value` -- the same monostate holds test, respelled
        # (`std::holds_alternative<std::monostate>(t.value)`).
        src = (
            "type Tree = int | None | list[Tree]\n"
            "def f(t: Tree) -> bool:\n"
            "    return t is None\n"
            "def main() -> None:\n"
            "    a: Tree = None\n"
            "    print(f(a))\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("isnone.union_wrapper_monostate", 0) >= 1
        assert faces.get("decl.wrapper_none", 0) >= 1

    def test_scalar_name_decl_source_still_defers(self):
        # A scalar-member NAME init at a mixed-union decl slot stays out
        # (only type-ctor RVALUES route the UNION_RVALUE slot).
        src = _MIXED_UNION + (
            "def f(n: Int32) -> None:\n"
            "    a: Int32 | Dog | None = n\n"
            "    print(check(a))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_str_member_union_isinstance_routes(self):
        # The isinstance CONDITION rides the WIDE member class
        # (`_eligible_ptr_union_wide` in `_isinstance_narrow_info`), and the
        # non-member implicit-else fact (`str | None`) is tolerated without
        # an else body -- so this routes; the extraction is member-keyed on
        # the CHECKED member only, blind to the str sibling. Arg/decl
        # positions still reject str members via `_eligible_ptr_union`
        # (their own gates, untouched).
        src = (
            "from tpy import Int32\n"
            "class Dog:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "def f(v: str | Dog | None) -> bool:\n"
            "    if isinstance(v, Dog):\n"
            "        return True\n"
            "    return False\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_str_member_union_none_test_member_blind(self):
        # The None-test render is MEMBER-BLIND (holds_alternative over the
        # monostate slot), so even a str-member ptr-union subject routes it
        # -- pinned byte-identical. Member-KEYED positions (isinstance,
        # args, decls) still reject str members via _eligible_ptr_union.
        src = (
            "from tpy import Int32\n"
            "class Dog:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "def f(v: str | Dog | None) -> bool:\n"
            "    return v is None\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


class TestRecursiveUnionLiteralArg:
    """The qualcall recursive-union wrapper ArgTemp row (`json.dumps([...])`
    -- `JsonValue __tmp_N = std::vector<JsonValue>{...};` + the bare temp at
    the arg position) and its boundaries."""

    def test_list_literal_hoists_wrapper_temp(self):
        # Node shape + witness; the wrapper/vector SPELLINGS come from the
        # per-compilation alias maps codegen populates, so the exact render
        # is pinned by _assert_byte_identical below, not by _body here.
        src = ("import json\n"
               "def f() -> None:\n    print(json.dumps([1, None, \"x\"]))\n")
        thir, faces = _lower_ctx_witnessed(src)
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRPrint)
        arg = stmt.args[0].expr.args[0]
        assert isinstance(arg, THIRArgTemp)
        assert isinstance(arg.init, THIRContainerLiteral)
        none_elem = arg.init.elements[1]
        assert isinstance(none_elem, THIRLiteral) and none_elem.value is None
        assert faces.get("argtemp.recursive_union_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_nested_mixed_literals_byte_identical(self):
        src = (
            "import json\n"
            "def f() -> None:\n"
            "    print(json.dumps([1, [None, {\"a\": [], \"b\":"
            " {\"c\": [True, 1.5, \"x\"]}}], \"y\"]))\n"
            "    print(json.dumps({\"a\": {}, \"b\": []}))\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_nonliteral_element_still_defers(self):
        src = ("import json\n"
               "def f(n: int) -> None:\n    print(json.dumps([1, n]))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_beyond_int32_literal_still_defers(self):
        # A wider literal takes the width-pinned ctor spelling -- unmirrored.
        src = ("import json\n"
               "def f() -> None:\n    print(json.dumps([1099511627776]))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_nonliteral_dict_key_still_defers(self):
        src = ("import json\n"
               "def f(k: str) -> None:\n    print(json.dumps({k: 1}))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_negative_literal_element_routes(self):
        # A negative literal parses as a unary op; the unary-minus literal
        # fold renders the bare negated token, same bounds as the raw
        # literal (`{-1}` in the hoisted JsonValue temp).
        src = ("import json\n"
               "def f() -> None:\n    print(json.dumps([-1]))\n"
               "def main() -> None:\n    f()\nmain()\n")
        _assert_routes_byte_identical(src)

    def test_out_of_range_negative_literal_element_still_defers(self):
        # The fold keeps the raw literal's int32 bounds; a wider negated
        # value takes the width-pinned ctor spelling -- unmirrored.
        src = ("import json\n"
               "def f() -> None:\n    print(json.dumps([-4294967296]))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_record_ctor_elements_route(self):
        # `[Leaf(1), [Leaf(2)]]`: member-record CTOR rvalues render bare
        # inside the spelled container (the converting ctor absorbs the
        # prvalue) -- decl slot, wrapper-arg temp, global init, and the
        # container-element subscript at a same-wrapper slot.
        src = (
            "from dataclasses import dataclass\n"
            "from tpy import Int32\n"
            "@dataclass\n"
            "class Leaf:\n"
            "    value: Int32\n"
            "type Tree = Leaf | list[Tree]\n"
            "def depth(t: Tree) -> Int32:\n"
            "    if isinstance(t, Leaf):\n"
            "        return 0\n"
            "    return 1\n"
            "g: Tree = [Leaf(7)]\n"
            "def main() -> None:\n"
            "    x: Tree = [Leaf(1), [Leaf(2), Leaf(3)]]\n"
            "    zs: list[Tree] = [Leaf(1), [Leaf(2)]]\n"
            "    print(depth(x))\n"
            "    print(depth(zs[1]))\n"
            "    print(depth([Leaf(10), [Leaf(20)]]))\n"
            "    print(depth(g))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_generic_instance_ctor_element_routes(self):
        # The generic-instance flavor shares the element rules; a ctor
        # element renders the same bare prvalue in the spelled container.
        src = (
            "from dataclasses import dataclass\n"
            "from tpy import Int32\n"
            "@dataclass\n"
            "class Leaf:\n"
            "    value: Int32\n"
            "type Tree[T] = Leaf | T | list[Tree[T]]\n"
            "def depth(t: Tree[Int32]) -> Int32:\n"
            "    if isinstance(t, Leaf):\n"
            "        return 0\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    x: Tree[Int32] = [Leaf(1), 5]\n"
            "    print(depth(x))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_nonctor_call_element_still_defers(self):
        # A non-ctor CALL element (`make_leaf()`) stays out of the ru
        # element family.
        src = (
            "from dataclasses import dataclass\n"
            "from tpy import Int32, Own\n"
            "@dataclass\n"
            "class Leaf:\n"
            "    value: Int32\n"
            "type Tree = Leaf | list[Tree]\n"
            "def make_leaf() -> Own[Leaf]:\n"
            "    return Leaf(9)\n"
            "def f() -> None:\n"
            "    x: Tree = [Leaf(1), make_leaf()]\n"
            "    print(isinstance(x, Leaf))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_generic_alias_instance_literal_still_defers(self):
        # A generic recursive-alias instance slot is a
        # RecursiveAliasInstanceType, never the UnionType the wrapper row
        # keys on -- the literal arg keeps falling back.
        src = (
            "type Tree[T] = T | None | list[Tree[T]]\n"
            "def sink(t: Tree[int]) -> None:\n    pass\n"
            "def f() -> None:\n    sink([1, None])\n")
        assert _fn(_lower_ctx(src), "f") is None


_WRAP_V = "type V = None | bool | int | str | list[V] | dict[str, V]\n"


class TestWrapperNoneDeclAndElems:
    """The wrapper-union None family: `a: V = None` decls (monostate),
    container-OF-wrapper literal elements (None -> monostate, nested
    literals spelled), and the Own[wrapper] ctor literal row. Boundaries:
    reassigned wrapper locals (the AST's rebind-slot pointer binding) and
    None at the Own[wrapper] ctor slot keep deferring."""

    def test_container_of_wrapper_literal_routes(self):
        src = _WRAP_V + (
            "def main() -> None:\n"
            "    items: list[V] = [None, True, 42, \"hi\", [1, None],"
            " {\"k\": 1, \"n\": None}]\n"
            "    d: dict[str, V] = {\"k\": 1, \"n\": None}\n"
            "    print(len(items), len(d))\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("containerlit.wrapper_elem", 0) >= 1

    def test_fstring_wrapper_element_still_defers(self):
        # A non-literal element (f-string) at a wrapper element slot stays
        # out of the ru element family.
        src = _WRAP_V + (
            "def f() -> None:\n"
            "    xs: list[V] = [f\"a{1}\"]\n"
            "    print(len(xs))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_reassigned_wrapper_local_stays_ast(self):
        # A REASSIGNED wrapper local is the AST's F2 rebind-slot pointer
        # binding (`V* a = &__slot_N;` + emplace reseats) -- the decl, the
        # `a = None` reseat, and the deref'd None-test all stay AST.
        src = _WRAP_V + (
            "def main() -> None:\n"
            "    a: V = 5\n"
            "    print(\"x\")\n"
            "    a = None\n"
            "    b: V = None\n"
            "    print(a is None, b is None)\n"
            "main()\n")
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "main") is None

    def test_own_wrapper_ctor_literal_routes(self):
        src = _WRAP_V + (
            "from tpy import Own\n"
            "class Holder:\n"
            "    value: V\n"
            "    def __init__(self, value: Own[V]) -> None:\n"
            "        self.value = value\n"
            "def main() -> None:\n"
            "    h = Holder(7)\n"
            "    g = Holder(\"s\")\n"
            "    b = Holder(True)\n"
            "    print(\"done\")\n"
            "main()\n")
        _assert_routes_byte_identical(src)
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("ctor.ru_wrapper_own_literal", 0) >= 1

    def test_none_at_own_wrapper_ctor_slot_still_defers(self):
        # `Holder(None)` -- the monostate spelling at the Own slot is its
        # own unwitnessed row; keep deferring.
        src = _WRAP_V + (
            "from tpy import Own\n"
            "class Holder:\n"
            "    value: V\n"
            "    def __init__(self, value: Own[V]) -> None:\n"
            "        self.value = value\n"
            "def f() -> None:\n"
            "    h = Holder(None)\n"
            "    print(\"done\")\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestOptionalSpanSlot:
    """The value-repr `Optional[Span]` pair: the spanlike coerce at an
    `Span[...] | None` slot (single-level Optional peel, readonly helper
    choice preserved) and the has_value None-test over the bare param.
    Narrowed reads keep deferring."""

    def test_optional_span_coerce_and_none_test_route(self):
        src = (
            "from tpy import Int32, Span, readonly\n"
            "def has_values(values: Span[readonly[Int32]] | None) -> bool:\n"
            "    return values is not None\n"
            "def main() -> None:\n"
            "    arr: list[Int32] = [10, 20, 30]\n"
            "    print(has_values(arr))\n"
            "    print(has_values(None))\n"
            "    print(has_values([42, 99]))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_mutable_optional_span_coerce_routes(self):
        # The MUTABLE inner picks as_mut_span through the Optional peel
        # (unpeeled, the readonly test on the whole Optional would flip
        # the helper).
        src = (
            "from tpy import Int32, Span\n"
            "def bump(values: Span[Int32] | None) -> bool:\n"
            "    return values is not None\n"
            "def main() -> None:\n"
            "    arr: list[Int32] = [1, 2]\n"
            "    print(bump(arr))\n"
            "    print(bump(None))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_narrowed_optional_span_read_stays_ast(self):
        # A narrowed read of the binding derefs `(*values)` on the AST
        # path -- unmirrored; only the None test routes.
        src = (
            "from tpy import Int32, Span, readonly\n"
            "def first(values: Span[readonly[Int32]] | None) -> Int32:\n"
            "    if values is not None:\n"
            "        return values[0]\n"
            "    return -1\n")
        assert _fn(_lower_ctx(src), "first") is None


class TestValueOptReturnPosition:
    def test_member_arg_in_return_call_routes(self):
        # Family is position-blind: the same bare render inside a return.
        thir = _lower_ctx(_PRELUDE + (
            "def classify(x: Optional[Int32]) -> Int32:\n    return 1\n"
            "def f() -> Int32:\n    return classify(42)\n"))
        assert _body(thir, "f") == "    return classify(42);\n"


_OPTVIEW = (
    "from tpy import StrView\n"
    "def takes_str_opt(s: str | None) -> None:\n"
    "    if s is None:\n        print(\"(none)\")\n"
    "    else:\n        print(s)\n"
    "def returns_view_opt() -> StrView | None:\n"
    "    return StrView(\"v\")\n"
)


class TestOptViewIdentityCoerceArg:
    """The Optional view<->str identity coerce at a plain ARG slot: both
    sides spell `std::optional<std::string_view>`, and the coercion lambda
    passes the expression through bare exactly there (every other position
    rebuilds via the `__ov` statement expression -- deferred)."""

    def test_call_rvalue_at_coerced_opt_slot_passes_bare(self):
        src = _OPTVIEW + (
            "def f() -> None:\n"
            "    takes_str_opt(returns_view_opt())\n")
        thir, faces = _lower_ctx_witnessed(src)
        body = io.StringIO()
        emit_thir_body(body, _fn(thir, "f"))
        assert "takes_str_opt(returns_view_opt());" in body.getvalue()
        assert faces["arg.optview_identity_coerce"] >= 1
        _assert_byte_identical(src)

    def test_decl_init_rebuild_still_defers(self):
        # Boundary: the INIT position takes the `__ov` statement-expression
        # rebuild -- unmirrored, the body falls back whole.
        src = _OPTVIEW + (
            "def f() -> None:\n"
            "    x: str | None = returns_view_opt()\n"
            "    takes_str_opt(x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_name_source_still_defers(self):
        # Boundary: a NAME inner has no witness -- the coerce row admits
        # CALL rvalues only.
        src = _OPTVIEW + (
            "def f(v: StrView | None) -> None:\n"
            "    takes_str_opt(v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestRuWrapperFreeCallArgs:
    """The plain free-call ladder's wrapper-slot literal rows: the
    container literal hoists the typed temp (the marker ladder's row,
    coerce-peeled), and a coerced INT literal passes bare (the wrapper's
    converting ctor absorbs it -- no temp)."""

    PRE = ("from tpy import Int32\n"
           "type Expr = Int32 | str | list[Expr]\n"
           "def eval_len(e: Expr) -> Int32:\n"
           "    if isinstance(e, Int32):\n"
           "        return 1\n"
           "    if isinstance(e, str):\n"
           "        return len(e)\n"
           "    total: Int32 = 0\n"
           "    for c in e:\n"
           "        total += eval_len(c)\n"
           "    return total\n")

    def test_container_literal_arg_hoists(self):
        src = self.PRE + (
            "def main() -> None:\n"
            "    print(eval_len([1, \"two\", [3]]))\n"
            "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("argtemp.recursive_union_literal", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_coerced_int_literal_passes_bare(self):
        src = self.PRE + (
            "def main() -> None:\n"
            "    print(eval_len(42))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "eval_len(42)" in cpp

    def test_qualified_wrapper_literal_args_route(self, tmp_path):
        # The MARKER ladder's wrapper rows: a module-qualified call reaches
        # the same predicates through _marker_call_arg_ok, so both flavors
        # (coerced int bare, container literal hoisted) must route there too.
        (tmp_path / "exprmod.py").write_text(self.PRE)
        src = ("import exprmod\n"
               "def main() -> None:\n"
               "    print(exprmod.eval_len(42))\n"
               "    print(exprmod.eval_len([1, \"two\", [3]]))\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(
            src, extra_lib_dirs=[tmp_path])
        assert "eval_len(42)" in cpp

    def test_float_literal_at_wrapper_slot_defers(self):
        # BOUNDARY: a non-int literal types at the MEMBER, never arriving
        # coerced-to-union -- the arg ladder has no row for it, so the body
        # falls back (byte-identically) rather than mis-rendering.
        src = ("from tpy import Int32, Float64\n"
               "type W = Float64 | str | list[W]\n"
               "def leaves(w: W) -> Int32:\n"
               "    if isinstance(w, list):\n"
               "        return len(w)\n"
               "    return 1\n"
               "def main() -> None:\n"
               "    print(leaves(2.5))\n"
               "main()\n")
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "main") is None


class TestValueOptTupleArgs:
    """The value-TUPLE inner of the whole-value-opt arg family: a
    `tuple[str, str] | None` slot is `std::optional<std::tuple<..>>` by
    value, so a same-optional NAME and a tuple LITERAL both bind it bare --
    the scalar/callable inners' twin, in the free and record-method
    ladders alike. The name arm fences this kind to whole-optional
    positions, so the arg row threads that use."""

    _SRC = (
        "from tpy import Int32\n"
        "class Svc:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def send(self, auth: tuple[str, str] | None) -> Int32:\n"
        "        return 1\n"
        "def free_send(auth: tuple[str, str] | None) -> Int32:\n"
        "    return 2\n"
        "def relay(s: Svc, auth: tuple[str, str] | None) -> None:\n"
        "    print(s.send(auth))\n"
        "    print(free_send(auth))\n"
        "def main() -> None:\n"
        "    s = Svc()\n"
        "    relay(s, (\"u\", \"p\"))\n"
        "    print(free_send((\"a\", \"b\")))\n"
        "    print(free_send(None))\n"
        "main()\n"
    )

    def test_whole_name_and_literal_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        # Twice at the pass rows (gate + render key on one predicate), once
        # per position.
        assert w.get("arg.value_opt_tuple", 0) >= 2
        assert w.get("arg.tuple_literal_value_opt", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "s.send(auth)" in out
        assert "free_send(auth)" in out
        assert 'free_send(std::tuple<std::string, std::string>{"a", "b"})' in out

    def test_pointer_repr_tuple_optional_stays_ast(self):
        # BOUNDARY: a RECORD-element tuple optional uses the pointer repr,
        # whose whole pass renders a conversion -- the value-tuple row must
        # not capture it.
        src = ("from tpy import Int32\n"
               "class Box:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n"
               "def take_ptr(p: tuple[Box, Box] | None) -> Int32:\n"
               "    return 1\n"
               "def ptr_pass(p: tuple[Box, Box] | None) -> None:\n"
               "    print(take_ptr(p))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "ptr_pass") is None
        _assert_byte_identical(src)

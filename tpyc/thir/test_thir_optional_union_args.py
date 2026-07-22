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
from .testutil import (_assert_byte_identical, _fn, _lower, _lower_ctx,
                       _lower_ctx_witnessed)

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

    def test_optional_declared_name_rejects(self):
        # Forwarding a `list | None` BINDING is the pass face (already `T*`)
        # -- not lowered for containers; keep the precise reject.
        thir = _lower_ctx(_PRELUDE + (
            "def take(xs: list[Int32] | None) -> None:\n    pass\n"
            "def f(xs: list[Int32] | None) -> None:\n    take(xs)\n"))
        assert _fn(thir, "f") is None


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

    def test_wrapper_union_none_test_still_defers(self):
        # A recursive-alias wrapper binding reads through `.value` -- a
        # different render, out of slice.
        src = (
            "type Tree = int | None | list[Tree]\n"
            "def f(t: Tree) -> bool:\n"
            "    return t is None\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_scalar_name_decl_source_still_defers(self):
        # A scalar-member NAME init at a mixed-union decl slot stays out
        # (only type-ctor RVALUES route the UNION_RVALUE slot).
        src = _MIXED_UNION + (
            "def f(n: Int32) -> None:\n"
            "    a: Int32 | Dog | None = n\n"
            "    print(check(a))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_str_member_union_member_keyed_still_defers(self):
        # A str member fails _eligible_ptr_union, so member-KEYED positions
        # (isinstance narrowing here) keep rejecting even though the
        # member-blind None-test routes.
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
        assert _fn(_lower_ctx(src), "f") is None

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

    def test_negative_literal_element_still_defers(self):
        # A negative literal parses as a unary op, not a literal node --
        # outside the element family.
        src = ("import json\n"
               "def f() -> None:\n    print(json.dumps([-1]))\n")
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


class TestValueOptReturnPosition:
    def test_member_arg_in_return_call_routes(self):
        # Family is position-blind: the same bare render inside a return.
        thir = _lower_ctx(_PRELUDE + (
            "def classify(x: Optional[Int32]) -> Int32:\n    return 1\n"
            "def f() -> Int32:\n    return classify(42)\n"))
        assert _body(thir, "f") == "    return classify(42);\n"

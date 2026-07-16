"""Free-call args at Optional / union slots: the value-repr-Optional
member-arg admission (bare render via the generic tail), the pointer-repr
Optional container-name faces (`&(name)` / `nullptr`), and the
pointer-variant union ctor-rvalue arg temp (`pv{&__tmp_N}`) -- routed emits
plus the position/shape rejects that must keep falling back."""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .nodes import (
    THIRCall, THIRExprStmt, THIRLiteral, THIROptionalPtrArg, THIRPrint,
    THIRUnionArgLift,
)
from .testutil import _fn, _lower, _lower_ctx, _lower_ctx_witnessed

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

    def test_ctor_rvalue_in_condition_rejects(self):
        # A while condition is re-evaluated per iteration; the AST hoists the
        # temp ONCE before the loop (a pre-existing miscompile shape) -- the
        # mirror must reject, not reproduce it.
        thir = _lower_ctx(
            _UNION_RECORDS
            + "def f() -> None:\n"
            + "    while check(Dog(3)):\n"
            + "        pass\n")
        assert _fn(thir, "f") is None

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


class TestValueOptReturnPosition:
    def test_member_arg_in_return_call_routes(self):
        # Family is position-blind: the same bare render inside a return.
        thir = _lower_ctx(_PRELUDE + (
            "def classify(x: Optional[Int32]) -> Int32:\n    return 1\n"
            "def f() -> Int32:\n    return classify(42)\n"))
        assert _body(thir, "f") == "    return classify(42);\n"

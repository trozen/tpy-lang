"""THIR BigInt value bindings: literal ctor wraps, bare ops/compares, mixed
BigInt/float compare casts, the scalar-cast coercions, f-string/print args,
and the deferred-narrow pins (subscript indices, range counters)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn,
)

_BI_PRELUDE = "from tpy import Int32, Float64\n"


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


def _hpp_cpp(src: str, thir: bool):
    """Header + source together -- inline (record-method) bodies emit in the
    hpp, so a byte-identity check over cpp alone is blind to them."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestBigIntValues:
    SRC = (
        _BI_PRELUDE
        + "def grow(b: int, c: int) -> int:\n"
        + "    total = b + c\n"
        + "    total = total * 2\n"
        + "    if total > b:\n"
        + "        return total\n"
        + "    return b\n"
        + "def mixed(b: int, x: Float64, n: Int32) -> bool:\n"
        + "    ok = b < x\n"
        + "    w = b + n\n"
        + "    return ok and w == b\n"
        + "def seed(n: Int32) -> int:\n"
        + "    b = 7\n"
        + "    z: int = 0\n"
        + "    k = int(n)\n"
        + "    return b + z + k\n"
        + "def main():\n"
        + "    r = grow(10, 32)\n"
        + "    print(r, mixed(r, 1.5, 3), seed(4))\n"
        + '    print(f"r={r}")\n'
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("grow", "mixed", "seed", "main"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        # literal wraps at call-arg slots (bare literals into BigInt params)
        assert "grow(::tpy::BigInt(10), ::tpy::BigInt(32))" in cpp
        assert "::tpy::BigInt z = ::tpy::BigInt(0);" in cpp  # annotated decl
        # the mixed BigInt/float compare casts the BigInt side
        assert "(static_cast<double>(b) < x)" in cpp
        # fixed_int_to_bigint coercion at the binop arg
        assert "((b) + (::tpy::BigInt(n)))" in cpp
        assert "(r).to_string()" in cpp                     # f-string row
        assert "::tpy::BigInt(static_cast<int64_t>(n))" in cpp  # int(n) ctor

    def test_bigint_index_stays_ast(self):
        # A runtime-BigInt subscript index takes the .to_fixed_check narrow
        # (deferred row) -> the body stays on the AST path.
        src = (
            _BI_PRELUDE
            + "def f(xs: list[Int32], k: int) -> Int32:\n"
            + "    return xs[k]\n"
            + "def main():\n    print(f([1, 2], 1))\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_fixed_target_aug_bigint_value_stays_ast(self):
        # FixedInt += BigInt wraps the value in .to_fixed_check<T>() -> AST.
        src = (
            _BI_PRELUDE
            + "def f(n: Int32, b: int) -> Int32:\n"
            + "    m = n\n"
            + "    m += b\n"
            + "    return m\n"
            + "def main():\n    print(f(3, 4))\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is None


class TestTargetTypedLiteralBoundaries:
    """Positions where the AST does NOT thread a scalar literal target:
    method args and list/Array literal elements render bare, unlike
    free-call args and dict/set elements."""

    METHOD_SRC = (
        "from tpy import Float32\n"
        "class Acc:\n"
        "    n: Float32\n"
        "    def __init__(self):\n        self.n = 0.0\n"
        "    def take(self, x: int) -> int:\n        return x\n"
        "    def add(self, y: Float32) -> Float32:\n"
        "        self.n = self.n + y\n        return self.n\n"
        # the caller takes Acc as a param -- a ctor LOCAL (`a = Acc()`)
        # is the still-deferred rvalue-local cell and would block routing
        "def use(a: Acc) -> Float32:\n"
        "    k = a.take(3)\n"
        "    return a.add(1.5)\n"
        "def main():\n"
        "    a = Acc()\n"
        "    print(use(a))\n"
        "main()\n"
    )

    def test_method_literal_args_render_bare(self):
        # gen_call_from_fi renders literal method args target-less: no
        # ::tpy::BigInt(3) wrap, no 1.5f suffix (unlike free-call args).
        thir = _lower_ctx(self.METHOD_SRC)
        assert _fn(thir, "use") is not None
        cpp = _cpp(self.METHOD_SRC, thir=True)
        assert "a.take(3)" in cpp
        assert "a.add(1.5)" in cpp

    def test_method_literal_args_byte_identical(self):
        assert (_hpp_cpp(self.METHOD_SRC, thir=True)
                == _hpp_cpp(self.METHOD_SRC, thir=False))

    LIST_SRC = (
        "from tpy import Float32\n"
        "def f() -> Float32:\n"
        "    xs: list[Float32] = [1.0, 2.5]\n"
        "    return xs[0]\n"
        "def main():\n    print(f())\nmain()\n"
    )

    def test_list_literal_elements_render_bare(self):
        # _gen_array_literal threads no scalar element target -- list/Array
        # Float32/BigInt literal elements stay bare (dict/set elements DO
        # thread and wrap).
        thir = _lower(self.LIST_SRC)
        assert _fn(thir, "f") is not None
        cpp = _cpp(self.LIST_SRC, thir=True)
        assert "{1.0, 2.5}" in cpp

    def test_list_literal_elements_byte_identical(self):
        assert (_cpp(self.LIST_SRC, thir=True)
                == _cpp(self.LIST_SRC, thir=False))

    def test_int32_min_literal_takes_int64_arm(self):
        # -2147483648 (INT32_MIN) folds via the negation arm and is the one
        # gate-admitted value outside arm 1 -- it must take the
        # static_cast<int64_t> ctor wrap, not the plain BigInt(v) ctor.
        src = (
            "def f(b: int) -> int:\n    return b\n"
            "def main():\n    print(f(-2147483648))\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "main") is not None
        cpp = _cpp(src, thir=True)
        assert "::tpy::BigInt(static_cast<int64_t>(-2147483648LL))" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestDeepConstNarrowSubject:
    """The deep-const narrowing-subject fix: a ptr-variant param under the
    deep_const_borrow_params verdict (discriminant-only use) spells const
    pointees in the isinstance / std::get template args."""

    SRC = (
        "class Dog:\n    def __init__(self) -> None:\n        pass\n"
        "class Cat:\n    def __init__(self) -> None:\n        pass\n"
        "class Classifier:\n"
        "    def __init__(self) -> None:\n        pass\n"
        "    def which(self, a: Dog | Cat) -> int:\n"
        "        if isinstance(a, Dog):\n"
        "            return 1\n"
        "        return 2\n"
        "def main() -> None:\n"
        "    c = Classifier()\n"
        "    d: Dog | Cat = Dog()\n"
        "    print(c.which(d))\n"
        "main()\n"
    )

    def test_const_pointee_spelling(self):
        # `which` is emitted inline in the header (a record method), so the
        # spelling assertion reads hpp + cpp together.
        both = _hpp_cpp(self.SRC, thir=True)
        assert "std::holds_alternative<const Dog*>(a)" in both
        assert "*std::get<const Dog*>(a)" in both

    def test_byte_identical(self):
        assert _hpp_cpp(self.SRC, thir=True) == _hpp_cpp(self.SRC, thir=False)

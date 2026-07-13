"""THIR BigInt value bindings: literal ctor wraps, bare ops/compares, mixed
BigInt/float compare casts, the scalar-cast coercions, f-string/print args,
and the deferred-narrow pins (subscript indices, range counters)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn,
)

_BI_PRELUDE = "from tpy import Int32, Float64\n"


def _cpp(src: str, thir: bool, default_int: str = "Int32"):
    compiler, modules = _compile(src, default_int=default_int)
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

    def test_huge_literal_index_stays_ast(self):
        # An out-of-int32-range literal index: the AST wraps the BigInt-ctor
        # literal render inside the narrow -- a shape the bare-literal emit
        # does not reproduce, so the body stays on the AST path.
        src = (
            _BI_PRELUDE
            + "def f(xs: list[Int32]) -> Int32:\n"
            + "    return xs[9999999999]\n"
            + "def main():\n    print(f([1, 2]))\nmain()\n"
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
        # A LIST literal threads no scalar element target -- vector
        # Float32/BigInt literal elements stay bare (a demoted/annotated
        # ARRAY's and dict/set elements DO thread and wrap).
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

    def test_huge_literals_take_from_str_arm(self):
        src = (
            "def f(b: int) -> int:\n    return b\n"
            "def main():\n"
            "    print(f(18446744073709551616))\n"
            "    print(f(-9223372036854775809))\n"
            "main()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "main") is not None
        cpp = _cpp(src, thir=True)
        assert '::tpy::BigInt::from_str("18446744073709551616")' in cpp
        assert '::tpy::BigInt::from_str("-9223372036854775809")' in cpp
        assert cpp == _cpp(src, thir=False)


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


class TestBigIntNarrowIndex:
    """Runtime-BigInt subscript indices / slice bounds / del keys take the
    AST's `.to_fixed_check<int32_t>()` narrow (no outer parens: composite
    renders carry their own)."""

    SRC = (
        _BI_PRELUDE
        + "def idx(xs: list[Int32], k: int) -> Int32:\n"
        + "    return xs[k]\n"
        + "def idx_expr(xs: list[Int32], k: int) -> Int32:\n"
        + "    return xs[k + 1]\n"
        + "def sidx(s: str, k: int) -> None:\n"
        + "    print(s[k])\n"
        + "def bidx(b: bytes, k: int) -> None:\n"
        + "    print(b[k])\n"
        + "def dkey(d: dict[Int32, str], k: int) -> None:\n"
        + "    print(d[k])\n"
        + "def dbig(d: dict[int, str], k: int) -> None:\n"
        + "    print(d[k])\n"
        + "def ddel(d: dict[Int32, str], k: int) -> None:\n"
        + "    del d[k]\n"
        + "def sbound(s: str, k: int) -> None:\n"
        + "    print(s[k:k + 2])\n"
        + "    print(s[1:k])\n"
        + "    print(s[k:])\n"
        + "def main():\n"
        + "    xs = [1, 2, 3]\n"
        + "    print(idx(xs, 1), idx_expr(xs, 0))\n"
        + "    sidx(\"hello\", 1)\n"
        + "    bidx(b\"abc\", 1)\n"
        + "    d = {1: \"a\", 2: \"b\"}\n"
        + "    dkey(d, 2)\n"
        + "    db: dict[int, str] = {}\n"
        + "    db[7] = \"x\"\n"
        + "    dbig(db, 7)\n"
        + "    ddel(d, 1)\n"
        + "    sbound(\"hello\", 1)\n"
        + "main()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        for name in ("idx", "idx_expr", "sidx", "bidx", "dkey", "dbig",
                     "ddel", "sbound"):
            assert _fn(thir, name) is not None, name
        assert w.get("narrow.subscript_index", 0) >= 6
        assert w.get("narrow.slice_bound", 0) >= 4

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "::tpy::__getitem__(xs, k.to_fixed_check<int32_t>())" in cpp
        assert (".to_fixed_check<int32_t>())" in cpp
                and "((k) + (::tpy::BigInt(1))).to_fixed_check<int32_t>()" in cpp)
        assert "::tpy::__getitem__(s, k.to_fixed_check<int32_t>())" in cpp
        assert "::tpy::bytes_getitem(b, k.to_fixed_check<int32_t>())" in cpp
        assert "::tpy::__getitem__(d, k.to_fixed_check<int32_t>())" in cpp
        assert "::tpy::__delitem__(d, k.to_fixed_check<int32_t>())" in cpp
        # slice bounds: var/composite bounds narrow, literal + absent stay bare
        assert ("::tpy::BasicSlice{k.to_fixed_check<int32_t>(), "
                "((k) + (::tpy::BigInt(2))).to_fixed_check<int32_t>()}") in cpp
        assert "::tpy::BasicSlice{1, k.to_fixed_check<int32_t>()}" in cpp
        assert ("::tpy::BasicSlice{k.to_fixed_check<int32_t>(), "
                "std::nullopt}") in cpp

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_bounds_safe_index_narrows_inside_cast(self):
        # Bounds-proven BigInt index: the narrow lands INSIDE the
        # static_cast<std::size_t> operator[] form.
        src = (
            _BI_PRELUDE
            + "def f(xs: list[Int32], k: int) -> None:\n"
            + "    if 0 <= k and k < len(xs):\n"
            + "        print(xs[k])\n"
            + "def main():\n    f([1, 2], 1)\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "xs[static_cast<std::size_t>(k.to_fixed_check<int32_t>())]" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_literal_bigint_slice_bound_stays_ast(self):
        # A literal slice bound under a BigInt default: the AST wraps the bare
        # digit token (`1.to_fixed_check<...>`) -- ill-formed C++ (BUGS.md),
        # so the shape stays out of the slice rather than mirroring it.
        src = (
            "def f(s: str) -> None:\n"
            + "    print(s[1:3])\n"
            + "def main():\n    f(\"hello\")\nmain()\n"
        )
        thir = _lower(src, default_int="BigInt")
        assert _fn(thir, "f") is None
        # ... while the same body routes under a fixed-int default.
        assert _fn(_lower(src), "f") is not None


class TestBigIntAugAssignNarrow:
    """FixedInt-target aug-assigns fed BigInt values: the value wraps in
    `({0}).to_fixed_check<T>()` before the resolved binop (target width keys
    the template)."""

    SRC = (
        _BI_PRELUDE
        + "from tpy import Int64\n"
        + "class Acc:\n"
        + "    n: Int32\n"
        + "    def __init__(self) -> None:\n        self.n = 0\n"
        + "def f(n: Int32, b: int) -> Int32:\n"
        + "    m = n\n"
        + "    m += b\n"
        + "    return m\n"
        + "def g(w: Int64, b: int) -> Int64:\n"
        + "    w -= b\n"
        + "    return w\n"
        + "def h(a: Acc, b: int) -> Int32:\n"
        + "    a.n += b\n"
        + "    return a.n\n"
        + "def main():\n"
        + "    acc = Acc()\n"
        + "    print(f(3, 4), g(9, 2), h(acc, 5))\n"
        + "main()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        for name in ("f", "g", "h"):
            assert _fn(thir, name) is not None, name
        assert w.get("narrow.aug_value", 0) >= 3

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("m = ::tpy::add_check<int32_t>(m, "
                "(b).to_fixed_check<int32_t>());") in cpp
        assert ("w = ::tpy::sub_check<int64_t>(w, "
                "(b).to_fixed_check<int64_t>());") in cpp
        assert ("a.n = ::tpy::add_check<int32_t>(a.n, "
                "(b).to_fixed_check<int32_t>());") in cpp

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestBigIntEnumFromValueNarrow:
    """`E(x)` with a runtime-BigInt arg: `({0}).to_fixed_check<U>()` over the
    enum's underlying type inside the EnumUtil template."""

    SRC = (
        "from enum import Enum\n"
        "class Color(Enum):\n"
        "    RED = 0\n"
        "    GREEN = 1\n"
        "def f(v: int) -> None:\n"
        "    c = Color(v)\n"
        "    print(c)\n"
        "def g(v: int) -> None:\n"
        "    c = Color(v + 1)\n"
        "    print(c)\n"
        "def main():\n    f(1)\n    g(0)\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None
        assert w.get("narrow.enum_arg", 0) >= 2

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("::tpy::EnumUtil<Color>::from_value("
                "(v).to_fixed_check<int32_t>())") in cpp
        assert ("::tpy::EnumUtil<Color>::from_value("
                "(((v) + (::tpy::BigInt(1)))).to_fixed_check<int32_t>())") in cpp

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_literal_bigint_arg_stays_ast(self):
        # A literal arg resolving BigInt (BigInt default): the AST wraps the
        # literal's ::tpy::BigInt(...) render -- out of the bare-literal slice.
        src = (
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 0\n"
            "def f() -> None:\n"
            "    c = Color(0)\n"
            "    print(c)\n"
            "def main():\n    f()\nmain()\n"
        )
        thir, _w = _lower_ctx_witnessed(src, default_int="BigInt")
        assert _fn(thir, "f") is None
        # ... while the same body routes under a fixed-int default (enums
        # resolve through the registry, so the compiler context is required).
        routed, _w = _lower_ctx_witnessed(src)
        assert _fn(routed, "f") is not None


class TestBigIntRangeCounter:
    """BigInt-counter range loops share the step-1 emit: `::tpy::BigInt`
    cpp_elem, literal bounds retype to the elem slot, non-literal bounds
    hoist to `__start/__stop` temps."""

    SRC = (
        "def f(n: int) -> None:\n"
        "    for i in range(n):\n"
        "        print(i)\n"
        "def g(a: int, b: int) -> None:\n"
        "    for j in range(a, b):\n"
        "        print(j)\n"
        "def main():\n    f(2)\n    g(1, 3)\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None
        assert w.get("range.bigint_counter", 0) >= 2

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "::tpy::BigInt __stop_0 = n;" in cpp
        assert "for (::tpy::BigInt i = 0; i < __stop_0; ++i) {" in cpp
        assert "::tpy::BigInt __start_0 = a;" in cpp
        assert "for (::tpy::BigInt j = __start_0; j < __stop_0; ++j) {" in cpp

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_literal_bounds_bigint_default(self):
        # Literal bounds under a BigInt default inline with the elem-slot
        # retype (`::tpy::BigInt(3)`), no temp hoist; the 1-arg start stays
        # the bare `0`.
        src = (
            "def f() -> None:\n"
            "    for i in range(3):\n"
            "        print(i)\n"
            "    for j in range(2, 5):\n"
            "        print(j)\n"
            "def main():\n    f()\nmain()\n"
        )
        thir = _lower(src, default_int="BigInt")
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True, default_int="BigInt")
        assert "for (::tpy::BigInt i = 0; i < ::tpy::BigInt(3); ++i) {" in cpp
        assert ("for (::tpy::BigInt j = ::tpy::BigInt(2); "
                "j < ::tpy::BigInt(5); ++j) {") in cpp
        assert (_cpp(src, thir=True, default_int="BigInt")
                == _cpp(src, thir=False, default_int="BigInt"))


class TestBigIntDefaultArrayElements:
    """A demoted `std::array` literal threads its element target (the
    `::tpy::BigInt(N)` ctor wraps); a vector's elements stay bare -- the
    retype keys on the RESOLVED container kind."""

    def test_array_elements_wrap_list_elements_bare(self):
        src = (
            "def arr() -> None:\n"
            "    xs = [10, 20, 30]\n"
            "    print(xs[0])\n"
            "def lst() -> None:\n"
            "    ys = [10, 20, 30]\n"
            "    ys.append(40)\n"
            "    print(ys[0])\n"
            "def main():\n    arr()\n    lst()\nmain()\n"
        )
        thir = _lower(src, default_int="BigInt")
        assert _fn(thir, "arr") is not None
        assert _fn(thir, "lst") is not None
        cpp = _cpp(src, thir=True, default_int="BigInt")
        assert ("{::tpy::BigInt(10), ::tpy::BigInt(20), "
                "::tpy::BigInt(30)}") in cpp
        assert "{10, 20, 30}" in cpp
        assert (_cpp(src, thir=True, default_int="BigInt")
                == _cpp(src, thir=False, default_int="BigInt"))

    def test_float32_array_elements_take_suffix(self):
        src = (
            "from tpy import Array, Float32\n"
            "def f() -> None:\n"
            "    xs: Array[Float32, 2] = [1.5, 2.5]\n"
            "    print(xs[0])\n"
            "def main():\n    f()\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "{1.5f, 2.5f}" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

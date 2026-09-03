"""The checked-narrow family keyed on a local's DECLARED type.

Sema caches a per-occurrence type on every name node; codegen declares the C++
slot from the variable's final, retro-widened type. A literal-seeded local that
a later `p = <int>` widens to BigInt therefore still types Int32 at every
earlier read -- and the AST's `is_runtime_bigint` reads the DECLARED type, so it
emits `.to_fixed_check<T>()` where the per-occurrence type says nothing is
needed. Reading the per-occurrence type drops the narrow and emits ill-formed
C++ (no `__getitem__(std::string_view, BigInt)` overload exists).

Each row pins routing + byte identity; the bracket classes pin the two
neighbours that must keep their existing render (never widened -> no narrow;
widened at BOTH declared and per-node -> narrow on both paths).
"""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical, _assert_routes_byte_identical, _compile, _entry,
    _fn, _lower_ctx_witnessed,
)

# A call the seeded local is later reassigned from: `int` return retro-widens
# the declared slot to BigInt while leaving every earlier read typed Int32.
_GI = "from tpy import Int32\ndef gi() -> int:\n    return 1\n"


def _cpp(src: str):
    compiler, modules = _compile(src)
    _, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    return cpp


class TestWidenedLocalSubscriptIndex:
    """Read, setitem, element aug-assign and delitem indices."""

    SRC = (
        _GI
        + "def read_index(data: str, xs: list[Int32]) -> None:\n"
        + "    p = 0\n"
        + "    print(data[p])\n"
        + "    print(xs[p])\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def write_index(xs: list[Int32]) -> None:\n"
        + "    p = 0\n"
        + "    xs[p] = 5\n"
        + "    xs[p] += 1\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def del_index(d: dict[Int32, Int32]) -> None:\n"
        + "    p = 0\n"
        + "    del d[p]\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def main() -> None:\n"
        + '    read_index("abc", [1, 2, 3])\n'
        + "    write_index([1, 2, 3])\n"
        + "    del_index({0: 1})\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC, comments=False)

    def test_witnessed(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("narrow.subscript_index", 0) >= 5

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC)
        assert ("::tpy::__getitem__(data, p.to_fixed_check<int32_t>())"
                in cpp)
        assert "::tpy::__setitem__(xs, p.to_fixed_check<int32_t>(), 5)" in cpp
        assert "::tpy::__delitem__(d, p.to_fixed_check<int32_t>())" in cpp


class TestWidenedLocalOtherNarrowSinks:
    """Slice bound, aug-assign value (name + element), enum from_value arg and
    f-string arg -- the rest of the checked-narrow family."""

    SRC = (
        "from enum import Enum\n"
        + _GI
        + "class Color(Enum):\n"
        + "    RED = 0\n"
        + "    GREEN = 1\n"
        + "def slice_bound(data: str) -> None:\n"
        + "    p = 0\n"
        + "    print(data[p:])\n"
        + "    print(data[:p])\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def aug_name() -> None:\n"
        + "    p = 0\n"
        + "    q: Int32 = 7\n"
        + "    q += p\n"
        + "    print(q)\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def aug_elem(xs: list[Int32]) -> None:\n"
        + "    p = 0\n"
        + "    xs[1] += p\n"
        + "    print(xs[1])\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def enum_arg() -> None:\n"
        + "    p = 0\n"
        + "    print(Color(p))\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def fstring() -> None:\n"
        + "    p = 0\n"
        + '    print(f"{p}")\n'
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def main() -> None:\n"
        + '    slice_bound("abcd")\n'
        + "    aug_name()\n"
        + "    aug_elem([1, 2, 3])\n"
        + "    enum_arg()\n"
        + "    fstring()\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC, comments=False)

    def test_witnessed(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("narrow.slice_bound", 0) >= 2
        assert w.get("narrow.aug_value", 0) >= 2
        assert w.get("narrow.enum_arg", 0) >= 1
        assert w.get("narrow.fstring_arg", 0) >= 1

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC)
        assert ("::tpy::BasicSlice{p.to_fixed_check<int32_t>(), std::nullopt}"
                in cpp)
        assert ("::tpy::BasicSlice{std::nullopt, p.to_fixed_check<int32_t>()}"
                in cpp)
        assert ("::tpy::add_check<int32_t>(q, (p).to_fixed_check<int32_t>())"
                in cpp)
        assert ("::tpy::EnumUtil<Color>::from_value("
                "(p).to_fixed_check<int32_t>())") in cpp
        assert 'std::format("{}", (p).to_string())' in cpp


class TestWidenedLocalBinopParamSlot:
    """`_convert_to_fixed_int_arg` at a resolved binop's fixed-int PARAM slot:
    the user-dunder forward/reverse operand and the `__contains__` needle."""

    SRC = (
        _GI
        + "class Bag:\n"
        + "    xs: list[Int32]\n"
        + "    def __init__(self) -> None:\n"
        + "        self.xs = [1, 2, 3]\n"
        + "    def __contains__(self, k: Int32) -> bool:\n"
        + "        return k == 1\n"
        + "    def __add__(self, k: Int32) -> Int32:\n"
        + "        return k + 1\n"
        + "    def __radd__(self, k: Int32) -> Int32:\n"
        + "        return k + 2\n"
        + "def needle(b: Bag) -> None:\n"
        + "    p = 0\n"
        + "    print(p in b)\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def fwd(b: Bag) -> None:\n"
        + "    p = 0\n"
        + "    print(b + p)\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def rev(b: Bag) -> None:\n"
        + "    p = 0\n"
        + "    print(p + b)\n"
        + "    p = gi()\n"
        + "    print(p)\n"
        + "def main() -> None:\n"
        + "    needle(Bag())\n"
        + "    fwd(Bag())\n"
        + "    rev(Bag())\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC, comments=False)

    def test_witnessed(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("narrow.binop_param", 0) >= 2

    def test_emit_spellings(self):
        cpp = _cpp(self.SRC)
        assert "((b) + ((p).to_fixed_check<int32_t>()))" in cpp
        assert "(((p).to_fixed_check<int32_t>()) + (b))" in cpp
        # the __contains__ needle takes the same call-arg narrow
        assert "(p).to_fixed_check<int32_t>()" in cpp

    def test_int_literal_operand_stays_bare(self):
        # `_convert_to_fixed_int_arg` exempts an int LITERAL argument on both
        # paths -- the param-slot narrow must not capture it.
        src = (
            "from tpy import Int32\n"
            "class Bag:\n"
            "    def __add__(self, k: Int32) -> Int32:\n"
            "        return k + 1\n"
            "def f(b: Bag) -> None:\n"
            "    print(b + 3)\n"
            "def main() -> None:\n"
            "    f(Bag())\n"
            "main()\n"
        )
        _assert_routes_byte_identical(src, comments=False)
        assert "to_fixed_check" not in _cpp(src)


class TestWidenedLocalNarrowBrackets:
    """The two neighbours the widened-local key must NOT disturb."""

    NEVER_WIDENED = (
        _GI
        + "def probe(data: str) -> None:\n"
        + "    p = 0\n"
        + "    print(data[p])\n"
        + "def main() -> None:\n"
        + '    probe("abc")\n'
        + "main()\n"
    )

    ALWAYS_BIGINT = (
        _GI
        + "def probe(data: str) -> None:\n"
        + "    p = gi()\n"
        + "    print(data[p])\n"
        + "def main() -> None:\n"
        + '    probe("abc")\n'
        + "main()\n"
    )

    def test_never_widened_stays_bare(self):
        _assert_routes_byte_identical(self.NEVER_WIDENED, comments=False)
        cpp = _cpp(self.NEVER_WIDENED)
        assert "::tpy::__getitem__(data, p)" in cpp
        assert "to_fixed_check" not in cpp

    def test_bigint_seeded_narrows(self):
        _assert_routes_byte_identical(self.ALWAYS_BIGINT, comments=False)
        cpp = _cpp(self.ALWAYS_BIGINT)
        assert ("::tpy::__getitem__(data, p.to_fixed_check<int32_t>())"
                in cpp)

    def test_bigint_keyed_receiver_stays_bare(self):
        # A BigInt-KEYED receiver takes no narrow on either path -- the
        # declared-type key must not turn `bigint_index_narrow_type`'s None
        # verdict into a wrap.
        src = (
            _GI
            + "def probe(d: dict[int, Int32]) -> None:\n"
            + "    p = 0\n"
            + "    print(d[p])\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe({0: 1})\n"
            + "main()\n"
        )
        _assert_routes_byte_identical(src, comments=False)
        assert "to_fixed_check" not in _cpp(src)

    def test_int_literal_index_stays_bare(self):
        # An in-int32-range literal index is exempt on both paths
        # (`_is_int_constant`), widened neighbour or not.
        src = (
            _GI
            + "def probe(data: str) -> None:\n"
            + "    p = 0\n"
            + "    print(data[3])\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + '    probe("abc")\n'
            + "main()\n"
        )
        _assert_routes_byte_identical(src, comments=False)
        assert "to_fixed_check" not in _cpp(src)


class TestWidenedLocalIndexRejects:
    """A shape whose AST render the slice does not reproduce keeps rejecting."""

    def test_out_of_range_literal_index_stays_ast(self):
        # An out-of-int32-range literal headed for a narrow: the AST renders
        # the BigInt ctor INSIDE the narrow, which the literal emit does not
        # reproduce -- the 'reject' disposition, unchanged by the key swap.
        src = (
            "def probe(data: str) -> None:\n"
            "    print(data[4294967296])\n"
            "def main() -> None:\n"
            '    probe("abc")\n'
            "main()\n"
        )
        assert _fn(_lower_ctx_witnessed(src)[0], "probe") is None

    def test_composite_index_over_widened_local_stays_ast(self):
        # `xs[p + 1]` on a widened `p`: codegen recomputes the binop result
        # from the DECLARED operand types and narrows, over a render that is
        # already ill-formed (`add_check<int32_t>(<BigInt p>, 1)`). The slice
        # does not mirror that accident -- it rejects.
        src = (
            _GI
            + "def probe(xs: list[Int32]) -> None:\n"
            + "    p = 0\n"
            + "    print(xs[p + 1])\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe([1, 2, 3])\n"
            + "main()\n"
        )
        assert _fn(_lower_ctx_witnessed(src)[0], "probe") is None
        # ... while the same composite over a NON-widened local routes.
        ok = (
            "from tpy import Int32\n"
            "def probe(xs: list[Int32]) -> None:\n"
            "    p = 0\n"
            "    print(xs[p + 1])\n"
            "def main() -> None:\n"
            "    probe([1, 2, 3])\n"
            "main()\n"
        )
        _assert_routes_byte_identical(ok, comments=False)

    def test_composite_slice_bound_over_widened_local_stays_ast(self):
        # Same unmirrored composite one sink over: `data[p:p + 1]`.
        src = (
            _GI
            + "def probe(data: str) -> None:\n"
            + "    p = 0\n"
            + "    print(data[p:p + 1])\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + '    probe("abc")\n'
            + "main()\n"
        )
        assert _fn(_lower_ctx_witnessed(src)[0], "probe") is None

    def test_composite_aug_value_over_widened_local_stays_ast(self):
        src = (
            _GI
            + "def probe() -> None:\n"
            + "    p = 0\n"
            + "    q: Int32 = 7\n"
            + "    q += p + 1\n"
            + "    print(q)\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe()\n"
            + "main()\n"
        )
        assert _fn(_lower_ctx_witnessed(src)[0], "probe") is None

    def test_composite_enum_arg_over_widened_local_stays_ast(self):
        src = (
            "from enum import Enum\n"
            + _GI
            + "class Color(Enum):\n"
            + "    RED = 0\n"
            + "    GREEN = 1\n"
            + "def probe() -> None:\n"
            + "    p = 0\n"
            + "    print(Color(p + 1))\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe()\n"
            + "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.enum_from_value.arg")

    def test_composite_fstring_arg_over_widened_local_stays_ast(self):
        src = (
            _GI
            + "def probe() -> None:\n"
            + "    p = 0\n"
            + '    print(f"{p + 1}")\n'
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe()\n"
            + "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:fstring.arg_wrap")


class TestCompositeOverWidenedLocalUserRecordRejects:
    """The user-record dunder sinks: the binop/`__contains__` param slot and
    the `__getitem__` / `__setitem__` key. Their gates run the shared
    disposition UNCONDITIONALLY -- a per-occurrence pre-test would short out
    for a composite over a retro-widened local and admit the shape whose
    narrow the slice does not mirror."""

    _BAG = (
        "class Bag:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.xs = [1, 2, 3, 4]\n"
        "    def __contains__(self, k: Int32) -> bool:\n"
        "        return k == 1\n"
        "    def __add__(self, k: Int32) -> Int32:\n"
        "        return k + 1\n"
        "    def __getitem__(self, i: Int32) -> Int32:\n"
        "        return self.xs[i]\n"
        "    def __setitem__(self, i: Int32, v: Int32) -> None:\n"
        "        self.xs[i] = v\n"
    )

    def _src(self, body: str) -> str:
        return (
            _GI
            + self._BAG
            + "def probe(b: Bag) -> None:\n"
            + "    p = 0\n"
            + f"    {body}\n"
            + "    p = gi()\n"
            + "    print(p)\n"
            + "def main() -> None:\n"
            + "    probe(Bag())\n"
            + "main()\n"
        )

    def test_composite_binop_param_stays_ast(self):
        src = self._src("print(b + (p + 1))")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:binop.shape.+.record")

    def test_composite_contains_needle_stays_ast(self):
        src = self._src("print((p + 1) in b)")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:binop.shape.in.record")

    def test_composite_record_getitem_key_stays_ast(self):
        src = self._src("print(b[p + 1])")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:subscript.record_getitem")

    def test_composite_record_setitem_key_stays_ast(self):
        src = self._src("b[p + 1] = 5")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.family")

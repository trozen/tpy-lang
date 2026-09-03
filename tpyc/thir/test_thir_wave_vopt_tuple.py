"""The value-repr `Optional[value tuple]` LOCAL binding
(`std::optional<std::tuple<...>>`).

Three positions land together, because the `@model` decode accumulator that
witnesses them uses all three in one body: the `has_value` None test, the
whole-binding NAME write (the macro-generated raw-assign shape), and the
NARROWED `(*coord)` deref -- including at a value-tuple arg slot, where the
pass-through row must read the narrowed EXPR type rather than the still-
optional binding. The spelled tuple-literal render resolves its pending
elements, which `to_cpp` does not do.
"""

from .testutil import (
    _reject_tally,
    _assert_byte_identical,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)
from ..codegen_cpp.context import CodeGenOptions


def _reject_tags(source):
    return _reject_tally(source)


MODEL_SRC = (
    "from tpy import Int32\n"
    "from tplib.json.model import model\n"
    "@model\n"
    "class P:\n"
    "    coord: tuple[Int32, Int32, str]\n"
    "    pair: tuple[Int32, Int32]\n"
    "def main() -> None:\n"
    "    p = P.from_json('{\"coord\": [1, 2, \"n\"], \"pair\": [3, 4]}')\n"
    "    print(p.coord)\n"
    "    print(p.to_json())\n"
    "main()\n"
)


class TestValueOptTupleLocal:
    def test_model_decode_routes(self):
        _hpp, cpp = _assert_routes_byte_identical(MODEL_SRC)
        # the binding
        assert ("std::optional<std::tuple<int32_t, int32_t, std::string>> "
                "coord = std::nullopt;") in cpp
        # the whole-binding write, with the pending str element resolved
        assert ("coord = std::tuple<int32_t, int32_t, std::string>{"
                "__t0_1, __t1_3, __t2_5};") in cpp
        # the has_value None test
        assert "if ((!coord.has_value())) {" in cpp
        # the narrowed deref, at a value-tuple ctor arg slot
        assert "return P((*coord), (*pair));" in cpp

    def test_model_decode_witnesses_the_vopt_tuple_faces(self):
        _thir, faces = _lower_ctx_witnessed(MODEL_SRC)
        assert faces.get("name.opt_vtuple_whole", 0) >= 1, faces
        assert faces.get("name.opt_vtuple_deref", 0) >= 1, faces
        assert faces.get("assign.value_opt_target", 0) >= 1, faces

    def test_narrowed_local_deref_routes(self):
        # The same narrowed deref reached from ordinary source: the None
        # test guards, the read renders `(*t)` under std::get.
        src = (
            "from tpy import Int32\n"
            "def f() -> Int32:\n"
            "    t: tuple[Int32, Int32] | None = None\n"
            "    if t is None:\n"
            "        return -1\n"
            "    return t[0]\n"
            "def main() -> None:\n"
            "    print(f())\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "if ((!t.has_value())) {" in cpp
        assert "std::get<0>((*t))" in cpp

    def test_value_opt_span_sibling_keeps_rejecting(self):
        # The row is keyed on the value-TUPLE kind, not on "value-opt": the
        # span sibling at the same narrowed arg slot still defers.
        src = (
            "from tpy import Int32, Span, readonly\n"
            "def sum_span(s: Span[readonly[Int32]]) -> Int32:\n"
            "    total = 0\n"
            "    for v in s:\n"
            "        total += v\n"
            "    return total\n"
            "def f() -> Int32:\n"
            "    s: Span[readonly[Int32]] | None = None\n"
            "    if s is None:\n"
            "        return -1\n"
            "    return sum_span(s)\n"
            "def main() -> None:\n"
            "    print(f())\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.span")

"""Scaffold units for the C1+C2 comprehension slice (THIRComprehension):
gate routes/rejects, byte-identical emit per arm, face pins. Deferred rows
(Array demotion, 3-arg range, owned-move elements, non-decl positions,
genexpr) stay on the AST path."""

from __future__ import annotations

from .dump import dump_thir
from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctx_witnessed, _PRELUDE,
    _F1_RECORDS,
)


def _cpp(src: str, thir: bool, comments: bool = False):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=comments,
                                      thir_codegen=thir))
    return cpp


SRC = (
    _PRELUDE
    + "def squares(n: Int32) -> Int32:\n"
    + "    xs = [i * i for i in range(n)]\n    return len(xs)\n"
    + "def shifted(a: Int32, n: Int32) -> Int32:\n"
    + "    xs = [i + 1 for i in range(a, n)]\n    return len(xs)\n"
    + "def evens(xs: list[Int32]) -> Int32:\n"
    + "    ys = [x * 2 for x in xs if x % 2 == 0]\n    return len(ys)\n"
    + "def uniq(xs: list[Int32]) -> Int32:\n"
    + "    s = {x for x in xs}\n    return len(s)\n"
    + "def index(n: Int32) -> Int32:\n"
    + "    d = {i: i * i for i in range(n)}\n    return len(d)\n"
    + "def vals(d: dict[Int32, Int32]) -> Int32:\n"
    + "    vs = [v + 1 for v in d.values()]\n    return len(vs)\n"
    + "def pairs(ps: list[tuple[Int32, Int32]]) -> Int32:\n"
    + "    sums = [a + b for a, b in ps]\n    return len(sums)\n"
    + "def main():\n"
    + "    print(squares(4))\n    print(shifted(1, 3))\n"
    + "    print(evens([1, 2, 3, 4]))\n    print(uniq([1, 2, 2]))\n"
    + "    print(index(4))\n    print(vals({1: 2}))\n"
    + "    print(pairs([(1, 2), (3, 4)]))\n"
    + "main()\n"
)

ROUTED = {"squares", "shifted", "evens", "uniq", "index", "vals", "pairs"}


class TestComprehensionRoutes:
    def test_c1_c2_shapes_route(self):
        thir = _lower(SRC)
        assert ROUTED <= {f.name for f in thir.functions}

    def test_faces_witnessed(self):
        _, w = _lower_ctx_witnessed(SRC)
        for face in ("comp.list", "comp.set", "comp.dict", "comp.range",
                     "comp.begin_end", "comp.reserve", "comp.filter",
                     "comp.unpack"):
            assert w.get(face, 0) >= 1, face

    def test_byte_identical(self):
        assert _cpp(SRC, thir=True) == _cpp(SRC, thir=False)

    def test_byte_identical_with_comments(self):
        assert (_cpp(SRC, thir=True, comments=True)
                == _cpp(SRC, thir=False, comments=True))

    def test_range_bound_hoist_and_reserve(self):
        cpp = _cpp(SRC, thir=True)
        # Per-bound counter draws (the comprehension scheme): start then stop.
        assert "const int32_t __start_0 = a;" in cpp
        assert "const int32_t __stop_1 = n;" in cpp
        assert ("if (__stop_1 > __start_0) __result.reserve("
                "static_cast<size_t>(__stop_1 - __start_0));") in cpp
        # Sized begin/end reserve (list over a name container).
        assert ("__result.reserve(static_cast<std::size_t>"
                "(__obj_0.size()));") in cpp

    def test_filter_and_inserts(self):
        cpp = _cpp(SRC, thir=True)
        assert "if (((::tpy::mod_floor<int32_t>(x, 2)) == 0)) {" in cpp
        assert "__result.push_back((::tpy::mul_check<int32_t>(x, 2)));" in cpp
        assert "__result.insert(x);" in cpp
        assert ("__result.insert_or_assign(i, "
                "(::tpy::mul_check<int32_t>(i, i)));") in cpp

    def test_unpack_binding(self):
        cpp = _cpp(SRC, thir=True)
        assert "auto& __tup_1 = *__beg_0;" in cpp
        assert "int32_t a = std::get<0>(__tup_1);" in cpp

    def test_str_elements_route(self):
        src = (
            _PRELUDE
            + "def owned(names: list[str]) -> Int32:\n"
            + "    xs = [s for s in names]\n    return len(xs)\n"
            + "def main():\n    print(owned([\"a\"]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "owned") is not None

    def test_record_iterable_routes(self):
        src = (
            _F1_RECORDS
            + "def vals(items: list[Inner]) -> Int32:\n"
            + "    xs = [p.value for p in items]\n    return len(xs)\n"
            + "def main():\n    print(vals([Inner(1)]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "vals") is not None

    def test_dump(self):
        thir = _lower(SRC)
        assert dump_thir(thir)  # the node renders without crashing


class TestComprehensionRejects:
    def _rejects(self, body: str, name: str = "f") -> None:
        src = _PRELUDE + body
        thir = _lower(src)
        assert _fn(thir, name) is None
        # A rejected shape must still be byte-identical (it stays AST).
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_array_demotion_rejects(self):
        # Literal range bounds resolve to a stack Array -> array_from_index
        # lambda emit (the C3 row).
        self._rejects("def f() -> Int32:\n"
                      "    xs = [i for i in range(3)]\n    return len(xs)\n")

    def test_reassigned_local_rejects(self):
        # A reassigned container local is a pointer-local on the AST path.
        self._rejects("def f(n: Int32) -> Int32:\n"
                      "    xs = [i for i in range(n)]\n"
                      "    xs = [i for i in range(n + 1)]\n"
                      "    return len(xs)\n")

    def test_three_arg_range_rejects(self):
        # 3-arg range takes the begin/end-over-Range emit -- deferred.
        self._rejects("def f(n: Int32) -> Int32:\n"
                      "    xs = [i for i in range(0, n, 2)]\n"
                      "    return len(xs)\n")

    def test_return_position_rejects(self):
        # Only the fresh decl-init position routes (C3 widens positions).
        self._rejects("def f(n: Int32) -> list[Int32]:\n"
                      "    return [i for i in range(n)]\n")

    def test_genexpr_rejects(self):
        # A genexpr local is the make_generator lambda emit (C4).
        self._rejects("def f(n: Int32) -> Int32:\n"
                      "    g = (i for i in range(n))\n"
                      "    c = 0\n"
                      "    for x in g:\n        c = c + x\n"
                      "    return c\n")

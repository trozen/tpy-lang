"""Scaffold units for the C1+C2 comprehension slice (THIRComprehension):
gate routes/rejects, byte-identical emit per arm, face pins. Deferred rows
(Array demotion, owned-move elements, unsupported expression positions,
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

    def test_owned_str_unpack_binding(self):
        src = (
            _PRELUDE
            + "def f(pairs: list[tuple[str, Int32]]) -> Int32:\n"
            + "    names = {name for name, _ in pairs}\n"
            + "    return len(names)\n"
            + "def main():\n    print(f([(\"a\", 1)]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "std::string name = std::get<0>(__tup_1);" in cpp

    def test_str_elements_route(self):
        src = (
            _PRELUDE
            + "def owned(names: list[str]) -> Int32:\n"
            + "    xs = [s for s in names]\n    return len(xs)\n"
            + "def main():\n    print(owned([\"a\"]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "owned") is not None

    def test_three_arg_range_routes(self):
        # C3 range3: begin/end over the Range object, rvalue capture, no
        # reserve; bounds render against the counter slot.
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    xs = [i for i in range(0, n, 2)]\n"
               + "    return len(xs)\n"
               + "def main():\n    print(f(9))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _, w = _lower_ctx_witnessed(src)
        assert w.get("comp.range3", 0) >= 1
        cpp = _cpp(src, thir=True)
        assert "auto __obj_0 = ::tpy::Range<int32_t>(0, n, 2);" in cpp

    def test_array_range_comp_routes(self):
        # C3 Array demotion, range arm: literal-bound range comps construct
        # via the array_from_index per-index lambda (1/2/3-arg inits).
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    xs = [i * 2 for i in range(3)]\n    return len(xs)\n"
               + "def g() -> Int32:\n"
               + "    ys = [i for i in range(1, 4)]\n    return len(ys)\n"
               + "def h():\n"
               + "    print([i for i in range(0, 6, 2)])\n"
               + "def main():\n    print(f())\n    print(g())\n    h()\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        for name in ("f", "g", "h"):
            assert _fn(thir, name) is not None, name
        _, w = _lower_ctx_witnessed(src)
        assert w.get("comp.array_range", 0) >= 3
        cpp = _cpp(src, thir=True)
        assert "::tpy::array_from_index<int32_t, 3>(" in cpp
        assert "int32_t i = 1 + int32_t(" in cpp          # 2-arg start offset
        assert " * (2);" in cpp                           # 3-arg step arm
        assert "::tpy::ListPrinter(::tpy::array_from_index" in cpp

    def test_print_arg_comp_routes(self):
        # C3 print-arg position: the stmt-expr render inside the container
        # printer wrap (gen_print's ListPrinter/SetPrinter/DictPrinter arms).
        src = (_PRELUDE
               + "def f(xs: list[Int32], n: Int32):\n"
               + "    print([x * 2 for x in xs])\n"
               + "    print({x for x in xs}, {i: i for i in range(n)})\n"
               + "def main():\n    f([1, 2], 2)\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _, w = _lower_ctx_witnessed(src)
        assert w.get("comp.print_arg", 0) >= 3
        cpp = _cpp(src, thir=True)
        assert "::tpy::ListPrinter(({" in cpp
        assert "::tpy::SetPrinter(({" in cpp
        assert "::tpy::DictPrinter(({" in cpp

    def test_storage_container_return_comp_routes(self):
        src = (
            _PRELUDE
            + "from tpy import Own\n"
            + "def ret_list(n: Int32) -> Own[list[Int32]]:\n"
            + "    return [i for i in range(n)]\n"
            + "def ret_set(n: Int32) -> Own[set[Int32]]:\n"
            + "    return {i for i in range(n)}\n"
            + "def ret_dict(n: Int32) -> Own[dict[Int32, Int32]]:\n"
            + "    return {i: i + 1 for i in range(n)}\n"
            + "def main():\n"
            + "    print(len(ret_list(2)))\n"
            + "    print(len(ret_set(2)))\n"
            + "    print(len(ret_dict(2)))\n"
            + "main()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        for name in ("ret_list", "ret_set", "ret_dict"):
            assert _fn(thir, name) is not None, name
        _, witnessed = _lower_ctx_witnessed(src)
        assert witnessed.get("ret.container_comp", 0) >= 3

    def test_char_elements_route(self):
        # C3 Char element slots: `[c for c in s]` -> std::vector<char>,
        # Char loop var + Char element through the targeted render.
        src = (_PRELUDE
               + "def f(s: str) -> Int32:\n"
               + "    cs = [c for c in s]\n    return len(cs)\n"
               + "def main():\n    print(f(\"ab\"))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "std::vector<char> __result;" in cpp
        assert "__result.push_back(c);" in cpp

    def test_bigint_range3_routes(self):
        # BigInt counter: the Range<::tpy::BigInt> overload; literal bounds
        # take the runtime-BigInt wrap on both paths.
        src = (_PRELUDE
               + "def f(n: int) -> Int32:\n"
               + "    xs = [i for i in range(0, n, 2)]\n"
               + "    return len(xs)\n"
               + "def main():\n    print(f(9))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        cpp = _cpp(src, thir=True)
        assert ("auto __obj_0 = ::tpy::Range<::tpy::BigInt>"
                "(::tpy::BigInt(0), n, ::tpy::BigInt(2));") in cpp

    def test_field_iterable_routes(self):
        # C3 field iterable: `self.items` / `h.items` off an F1-record
        # receiver -- lvalue capture (`auto& __obj_N = ...`), sized reserve.
        src = (
            "from tpy import Int32\n"
            "class Holder:\n"
            "    items: list[Int32]\n"
            "    def __init__(self):\n        self.items = [1, 2, 3]\n"
            "    def doubled(self) -> Int32:\n"
            "        xs = [v * 2 for v in self.items]\n        return len(xs)\n"
            "def outer(h: Holder) -> Int32:\n"
            "    ys = [v + 1 for v in h.items]\n    return len(ys)\n"
            "def main():\n"
            "    h = Holder()\n    print(h.doubled())\n    print(outer(h))\n"
            "main()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "outer") is not None
        assert _fn(thir, "doubled") is not None
        _, w = _lower_ctx_witnessed(src)
        assert w.get("comp.field_iter", 0) >= 2
        cpp = _cpp(src, thir=True)
        assert "auto& __obj_0 = h.items;" in cpp
        # The method body emits in the header (inline method) -- byte-compare
        # the hpp too, and pin the `this->` receiver render.
        def hpp(thir: bool) -> str:
            compiler, modules = _compile(src)
            out, _ = compiler.generate_code_to_strings(
                _entry(modules), options=CodeGenOptions(thir_codegen=thir))
            return out
        h_thir = hpp(True)
        assert h_thir == hpp(False)
        assert "auto& __obj_0 = this->items;" in h_thir

    def test_narrowed_optional_field_iterable_rejects(self):
        # A narrowed Optional field iterable takes the AST's `(*...)` unwrap
        # -- the route types on the DECLARED field type and must reject.
        src = (_PRELUDE
               + "class H:\n"
               + "    items: list[Int32] | None\n"
               + "    def __init__(self):\n        self.items = None\n"
               + "def f(h: H) -> Int32:\n"
               + "    if h.items is not None:\n"
               + "        xs = [v for v in h.items]\n"
               + "        return len(xs)\n"
               + "    return 0\n"
               + "def main():\n    print(f(H()))\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_record_iterable_routes(self):
        src = (
            _F1_RECORDS
            + "def vals(items: list[Inner]) -> Int32:\n"
            + "    xs = [p.value for p in items]\n    return len(xs)\n"
            + "def main():\n    print(vals([Inner(1)]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "vals") is not None

    def test_enum_loop_var_routes(self):
        # Compositional loop-var gate: an enum element (a value type outside the
        # old scalar/char/str/F1-record whitelist) binds through the shared
        # loop_var_binding; the filter's enum comparison routes recursively.
        src = (
            "from tpy import Int32\nfrom enum import Enum\n"
            "class Color(Enum):\n    RED = 0\n    GREEN = 1\n"
            "def f() -> Int32:\n    xs = [Color.RED, Color.GREEN]\n"
            "    ys = [1 for c in xs if c == Color.RED]\n    return len(ys)\n"
            "def main():\n    print(f())\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_nested_container_loop_var_routes(self):
        # A `list[list[...]]` local iterated binds the inner list `auto&&` (the
        # shared non-value loop_var_binding arm); the element's read of the loop
        # var (`len(row)`) is gated recursively -- no enumerated element family.
        src = (_PRELUDE
               + "def f() -> Int32:\n    xs = [[1, 2], [3, 4]]\n"
               + "    ys = [len(row) for row in xs]\n    return len(ys)\n"
               + "def main():\n    print(f())\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "f") is not None

    def test_enum_element_result_slot(self):
        # Compositional element-slot gate: a `list[Color]` result -- an enum
        # element slot (a value type, bare render) outside the old
        # scalar/char/str whitelist.
        src = (
            "from tpy import Int32\nfrom enum import Enum\n"
            "class Color(Enum):\n    RED = 0\n    GREEN = 1\n"
            "def f() -> Int32:\n    xs = [Color.RED, Color.GREEN]\n"
            "    ys = [c for c in xs]\n    return len(ys)\n"
            "def main():\n    print(f())\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_record_ctor_element_slot_routes(self):
        # The record slot admits the constructor element, whose admission now
        # runs when the comprehension actually lowers the element expression.
        src = (
            _F1_RECORDS
            + "def f(xs: list[Int32]) -> Int32:\n"
            + "    ps = [Inner(x) for x in xs]\n    return len(ps)\n"
            + "def main():\n    print(f([1, 2]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_record_name_element_result_slot(self):
        # A `list[Inner]` result whose element is the bare record loop var
        # (a borrow alias): push_back copies, matching the AST.
        src = (
            _F1_RECORDS
            + "def f(items: list[Inner]) -> Int32:\n"
            + "    ps = [p for p in items]\n    return len(ps)\n"
            + "def main():\n    print(f([Inner(1)]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_bytes_literal_element_result_slot(self):
        # A `set[bytes]` result whose element is a bytes literal (owned bare).
        src = (_PRELUDE
               + "def f(xs: list[Int32]) -> Int32:\n"
               + "    bs = {b'ab' for x in xs}\n    return len(bs)\n"
               + "def main():\n    print(f([1, 2]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

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

    def test_array_source_indexing_rejects(self):
        # The array_from_index ARRAY-SOURCE arm (`__obj_N[__i_N]` random
        # access) is a deferred row; only the range arm routes.
        self._rejects("from tpy import Array\n"
                      "def f(src: Array[Int32, 3]) -> Int32:\n"
                      "    xs = [v + 1 for v in src]\n    return len(xs)\n")

    def test_reassigned_local_rejects(self):
        # A reassigned container local is a pointer-local on the AST path.
        self._rejects("def f(n: Int32) -> Int32:\n"
                      "    xs = [i for i in range(n)]\n"
                      "    xs = [i for i in range(n + 1)]\n"
                      "    return len(xs)\n")


    def test_array_return_position_rejects(self):
        # A literal-bound comp at return position resolves to an Array ->
        # the array_from_index emit (deferred row).
        self._rejects("def f() -> list[Int32]:\n"
                      "    return [i for i in range(3)]\n")

    def test_genexpr_rejects(self):
        # A genexpr local is the make_generator lambda emit (C4).
        self._rejects("def f(n: Int32) -> Int32:\n"
                      "    g = (i for i in range(n))\n"
                      "    c = 0\n"
                      "    for x in g:\n        c = c + x\n"
                      "    return c\n")


class TestCompReturnPosition:
    # A comprehension at a storage-container return slot (`-> Own[set[T]]`)
    # renders the same position-independent stmt-expr the decl-init arm emits.
    def test_return_comp_routes(self):
        src = (_PRELUDE + "from tpy import Own\n"
               "def f(n: Int32) -> Own[set[Int32]]:\n"
               "    return {i for i in range(n)}\n"
               "print(len(f(3)))\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert witnesses.get("ret.container_comp", 0) >= 1
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "return ({" in out

    def test_return_comp_out_of_slice_falls_back(self):
        # A call iterable is outside the comp route -> the raise inside the
        # return arm falls the body back (byte-identical via AST).
        src = (_PRELUDE + "from tpy import Own\n"
               "def make() -> Own[list[Int32]]:\n    return [1, 2]\n"
               "def f() -> Own[set[Int32]]:\n"
               "    return {x for x in make()}\n"
               "print(len(f()))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestCompStrUnpackTarget:
    # A str tuple element unpack target COPIES the stored element
    # (`std::string k = std::get<0>(tup);` -- the AST's is_value_type branch),
    # and the owned declared type keeps its reads STORAGE-form (bare insert).
    SRC = (
        _PRELUDE
        + "def f(pairs: list[tuple[str, Int32]]) -> Int32:\n"
        + "    names = {k for k, _ in pairs}\n    return len(names)\n"
        + "print(f([(\"a\", 1), (\"b\", 2)]))\n"
    )

    def test_str_unpack_target_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        out = _cpp(self.SRC, thir=True)
        assert out == _cpp(self.SRC, thir=False)
        assert "std::string k = std::get<0>(" in out
        assert "__result.insert(k)" in out

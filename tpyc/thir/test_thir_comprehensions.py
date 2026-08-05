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

    def test_array_source_comp_routes(self):
        # C3 Array demotion, source arm: `[expr for x in <sized Array>]`
        # borrows the source once (`__obj_N`) and indexes it per slot via the
        # array_from_index lambda.
        src = (_PRELUDE
               + "from tpy import Array\n"
               + "def f(src: Array[Int32, 3]) -> Int32:\n"
               + "    xs = [v + 1 for v in src]\n    return len(xs)\n"
               + "def main():\n    print(f([1, 2, 3]))\nmain()\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _, w = _lower_ctx_witnessed(src)
        assert w.get("comp.array_source", 0) >= 1
        cpp = _cpp(src, thir=True)
        assert "auto& __obj_0 = src;" in cpp
        assert "int32_t v = __obj_0[__i_0];" in cpp
        assert "::tpy::array_from_index<int32_t, 3>(" in cpp

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

    def test_return_comp_call_iterable_routes(self):
        # A container-returning CALL iterable now rides the comp route
        # (the free-call arm), so the return-position comp routes whole.
        src = (_PRELUDE + "from tpy import Own\n"
               "def make() -> Own[list[Int32]]:\n    return [1, 2]\n"
               "def f() -> Own[set[Int32]]:\n"
               "    return {x for x in make()}\n"
               "print(len(f()))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
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


class TestDictCompContainerValue:
    """The dict-comp list/Array VALUE slot widening: a container-literal
    value renders self-describing (`std::array<int32_t, 2>{...}` -- the
    typed_brace_init mirror; insert_or_assign is a template that cannot
    deduce a bare brace-init) and a nested comprehension value recurses into
    the stmt-expr render (the inner `__result` shadow / `__stop_N` numbering
    continue the enclosing body's streams)."""

    SRC = (
        _PRELUDE
        + "def fixed() -> None:\n"
        + "    d = {i: [i, i + 1] for i in range(3)}\n"
        + "    print(d)\n"
        + "def jagged() -> None:\n"
        + "    d = {i: [k for k in range(i)] for i in range(4)}\n"
        + "    print(d)\n"
        + "def main():\n    fixed()\n    jagged()\nmain()\n"
    )

    def test_routes(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "fixed") is not None
        assert _fn(thir, "jagged") is not None

    def test_faces_witnessed(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("comp.container_value", 0) >= 2
        assert w.get("comp.nested", 0) >= 1

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
        assert (_cpp(self.SRC, thir=True, comments=True)
                == _cpp(self.SRC, thir=False, comments=True))

    def test_array_value_renders_self_typed(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("__result.insert_or_assign(i, std::array<int32_t, 2>"
                "{i, (::tpy::add_check<int32_t>(i, 1))});") in cpp

    def test_nested_comp_value_renders_stmt_expr(self):
        cpp = _cpp(self.SRC, thir=True)
        # The inner comp opens inside the insert, shadows __result, and draws
        # the next __stop index after the outer comp's.
        assert "__result.insert_or_assign(i, ({" in cpp
        assert "const int32_t __stop_1 = i;" in cpp
        assert "__result.push_back(k);" in cpp

    def test_name_value_rejects(self):
        # A container NAME value (per-entry copy semantics) is unvetted --
        # only the literal / nested-comp sources route.
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    xs = [1, 2]\n"
               + "    d = {i: xs for i in range(n)}\n"
               + "    return len(d)\n"
               + "print(f(3))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_list_comp_container_elem_routes(self):
        # A list/set element and an Array-lambda return also take the container
        # slot now (allow_container), but keep the BARE brace (push_back /
        # declared lambda return deduce the type) -- only the dict VALUE spells
        # it typed. A nested-comp element recurses; a container LITERAL renders
        # bare-brace self.
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    xs = [[k for k in range(i)] for i in range(n)]\n"
               + "    return len(xs)\n"
               + "def lits(n: Int32) -> Int32:\n"
               + "    ys = [[i, i + 1] for i in range(n)]\n"
               + "    return len(ys)\n"
               + "print(f(3))\nprint(lits(3))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "lits") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestBranchPositionCompDecl:
    # The branch-first comp decl admission (the fn_top gate removed): the
    # render is a position-independent stmt-expr, so a branch-local comp
    # decl lowers in place; hoisted (read-after-branch) and
    # shadow-of-classified shapes keep rejecting.

    def test_if_branch_local_routes(self):
        src = (_PRELUDE
               + "def f(k: Int32) -> Int32:\n"
               + "    if k > 0:\n"
               + "        xs = [i * 2 for i in range(3)]\n"
               + "        return len(xs)\n"
               + "    return -1\n"
               + "print(f(1))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_loop_body_redecl_routes(self):
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    for j in range(n):\n"
               + "        zs = [i * j for i in range(3)]\n"
               + "        total = total + len(zs)\n"
               + "    return total\n"
               + "print(f(3))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_read_after_if_hoisted_stays_ast(self):
        # A branch comp decl READ AFTER the if is sema-hoisted; the AST
        # pre-declares and assigns in-branch -- unmirrored, falls back.
        src = (_PRELUDE
               + "def f(k: Int32) -> Int32:\n"
               + "    if k > 0:\n"
               + "        ys = [i + 1 for i in range(4)]\n"
               + "    else:\n"
               + "        ys = [i + 2 for i in range(4)]\n"
               + "    return len(ys)\n"
               + "print(f(1))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_comp_var_shadowing_narrowed_stays_ast(self):
        # A comp loop var shadowing the live NARROWED union subject: the
        # outer post-comp read renames to the extraction alias, which the
        # comp scope would clobber -- the route's shadow check rejects.
        src = (_PRELUDE
               + "def f(v: Int32 | str) -> Int32:\n"
               + "    if isinstance(v, Int32):\n"
               + "        ws = [v for v in range(3)]\n"
               + "        return len(ws) + v\n"
               + "    return -1\n"
               + "print(f(7))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestGenexprC4Cells:
    """The C4 make_generator cells: range counter lambdas, filter
    conditions, the structural-slot auto temp vs the native inline split.
    Corpus witnesses: iterators/genexpr_basic + genexpr_builtins,
    builtins/enumerate_rvalue, inference/literal_binding_resolves."""

    def test_range_counter_lambda_routes_byte_identical(self):
        src = (_PRELUDE
               + "def total(n: Int32) -> Int32:\n"
               + "    return sum(x * x for x in range(n))\n"
               + "def stepped() -> Int32:\n"
               + "    return sum(x for x in range(10, 0, -2))\n"
               + "print(total(5))\nprint(stepped())\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "total") is not None
        assert _fn(thir, "stepped") is not None
        assert witnesses.get("genexpr.range", 0) >= 2
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "__i = int32_t(0), __stop = static_cast<int32_t>(n)" in out
        assert "::tpy::range_check_step_nonzero(__step);" in out

    def test_filter_wraps_the_yield_byte_identical(self):
        src = (_PRELUDE
               + "def evens(xs: list[Int32]) -> Int32:\n"
               + "    return sum(x for x in xs if x % 2 == 0)\n"
               + "print(evens([1, 2, 3, 4]))\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "evens") is not None
        assert witnesses.get("genexpr.filter", 0) >= 1
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "if (((::tpy::mod_floor<int32_t>(x, 2)) == 0)) {" in out

    def test_structural_slot_hoists_auto_temp(self):
        # A USER fn's Iterable slot hoists `auto __tmp_N = <make_generator>`
        # (the argtemp.genexpr_proto row) where a native consumer renders
        # inline -- the AST's temp-vs-inline split.
        src = (_PRELUDE
               + "from typing import Iterable\n"
               + "def total(items: Iterable[Int32]) -> Int32:\n"
               + "    t: Int32 = 0\n"
               + "    for x in items:\n        t += x\n"
               + "    return t\n"
               + "def main() -> None:\n"
               + "    print(total(x for x in range(4)))\n"
               + "main()\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert witnesses.get("argtemp.genexpr_proto", 0) >= 1
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "auto __tmp_1 = ::tpy::make_generator<int32_t>(" in out


_OPT_COMP_PRE = (
    "from typing import Optional\n"
    "from tpy import Int32\n\n"
    "class Foo:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "    def __repr__(self) -> str:\n"
    "        return f\"Foo({self.x})\"\n\n"
)


class TestStorageOptCompLoopVar:
    # A ptr-repr Optional[F1-record] comp ELEMENT registers the loop var in
    # the storage-opt set (the for-STATEMENT container leg's comp twin): a
    # NARROWED member access derefs at the ACCESS site (`(*item).x`), a
    # protocol-slot arg passes the WHOLE optional (`repr_of(item)`), and the
    # None-test spells has_value over the bare storage binding.

    def test_narrowed_ternary_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (_OPT_COMP_PRE
               + "def main() -> None:\n"
               + "    items: list[Optional[Foo]] = [Foo(1), None, Foo(3)]\n"
               + "    xs = [item.x if item is not None else -1 for item in items]\n"
               + "    print(xs)\n"
               + "    reprs = [repr(item) if item is not None else \"none\" for item in items]\n"
               + "    print(reprs)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*item).x" in cpp
        assert "::tpy::repr_of(item)" in cpp
        assert "item.has_value()" in cpp

    def test_filter_unproven_access_routes(self):
        # A FILTERED comp's element access stays sema-unproven: the AST
        # wraps the whole storage optional -- deref_optional_check, NOT the
        # optional_to_ptr lift the pointer-repr families take.
        from .testutil import _assert_routes_byte_identical
        src = (_OPT_COMP_PRE
               + "def main() -> None:\n"
               + "    items: list[Optional[Foo]] = [Foo(1), None]\n"
               + "    xs = [item.x for item in items if item is not None]\n"
               + "    print(xs)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::deref_optional_check(item).x" in cpp
        assert "optional_to_ptr" not in cpp

    def test_const_source_defers(self):
        # A const-bound iteration source's consumers spell `const P*` -- the
        # unmirrored const twin keeps the fence.
        src = (_OPT_COMP_PRE
               + "from tpy import readonly\n"
               + "def pick(items: readonly[list[Optional[Foo]]]) -> Int32:\n"
               + "    xs = [item.x if item is not None else -1 for item in items]\n"
               + "    return xs[0]\n"
               + "def main() -> None:\n"
               + "    items: list[Optional[Foo]] = [Foo(1), None]\n"
               + "    print(pick(items))\n"
               + "main()\n")
        thir = _lower(src)
        assert _fn(thir, "pick") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_record_slot_arg_defers(self):
        # A NARROWED storage-opt read at a plain RECORD param slot is not the
        # whole-optional protocol pass -- the name fence keeps it AST-side.
        src = (_OPT_COMP_PRE
               + "def take(f: Foo) -> Int32:\n"
               + "    return f.x\n"
               + "def main() -> None:\n"
               + "    items: list[Optional[Foo]] = [Foo(1), None]\n"
               + "    xs = [take(item) if item is not None else -1 for item in items]\n"
               + "    print(xs)\n"
               + "main()\n")
        thir = _lower(src)
        assert _fn(thir, "main") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


_SPAN_BUF_PRE = (
    "from tpy import Int32, Array, Span, SpanIter, auto_readonly\n\n"
    "class Buf:\n"
    "    _data: Array[Int32, 4]\n"
    "    _n: Int32\n\n"
    "    def __init__(self) -> None:\n"
    "        self._data = [10, 20, 30, 0]\n"
    "        self._n = 3\n\n"
    "    @auto_readonly\n"
    "    def __span__(self) -> Span[auto_readonly[Int32]]:\n"
    "        return self._data\n\n"
    "    @auto_readonly\n"
    "    def __iter__(self) -> SpanIter[auto_readonly[Int32]]:\n"
    "        return SpanIter(self.__span__())\n\n"
)


class TestSynthBeginEndCompIterable:
    # A user record with SYNTHESIZED begin()/end() (a Spannable conformer:
    # `__span__` + SpanIter-returning `__iter__`, no explicit begin/end) is a
    # comp iterable: the AST's unconditional begin/end comp loop serves it,
    # unsized (no reserve) and with no storage-form registration.

    def test_comp_over_spannable_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (_SPAN_BUF_PRE
               + "def main() -> None:\n"
               + "    b = Buf()\n"
               + "    xs = [x * 2 for x in b]\n"
               + "    print(xs)\n"
               + "    s = {x // 10 for x in b}\n"
               + "    print(len(s))\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "__obj_0.begin()" in cpp
        assert ".reserve(" not in cpp

    def test_user_iterator_comp_defers(self):
        # A record whose `__iter__` does NOT return SpanIter is outside the
        # begin/end synthesis -- the comp keeps rejecting.
        src = ("from tpy import Int32\n\n"
               "class Counter:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n"
               "    def __iter__(self) -> \"Counter\":\n"
               "        return self\n"
               "    def __next__(self) -> Int32:\n"
               "        if self.n >= 3:\n"
               "            raise StopIteration\n"
               "        self.n += 1\n"
               "        return self.n\n\n"
               "def main() -> None:\n"
               "    c = Counter()\n"
               "    xs = [v * 2 for v in c]\n"
               "    print(xs)\n"
               "main()\n")
        thir = _lower(src)
        assert _fn(thir, "main") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_genexpr_over_spannable_defers(self):
        # The genexpr route keeps its own native-iterable gate -- a synth
        # source genexpr stays AST-side.
        src = (_SPAN_BUF_PRE
               + "def main() -> None:\n"
               + "    b = Buf()\n"
               + "    print(sum(x * x for x in b))\n"
               + "main()\n")
        thir = _lower(src)
        assert _fn(thir, "main") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestCompStorageOptFences:
    # Boundary pins for the storage-opt/synth widenings' sibling routes:
    # the Array-source indexed comp and a synth (Spannable) source with an
    # Optional element both keep rejecting.

    def test_array_source_storage_opt_elem_defers(self):
        src = (_OPT_COMP_PRE
               + "from tpy import Array\n"
               + "def main() -> None:\n"
               + "    ps: Array[Optional[Foo], 2] = [Foo(1), None]\n"
               + "    xs = [p for p in ps]\n"
               + "    print(len(xs))\n"
               + "main()\n")
        thir = _lower(src)
        assert _fn(thir, "main") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_synth_source_optional_elem_defers(self):
        src = (_OPT_COMP_PRE
               + "from tpy import Array, Span, SpanIter, auto_readonly\n"
               + "class OptBuf:\n"
               + "    _data: Array[Optional[Foo], 2]\n"
               + "    def __init__(self) -> None:\n"
               + "        self._data = [Foo(1), None]\n"
               + "    @auto_readonly\n"
               + "    def __span__(self) -> Span[auto_readonly[Optional[Foo]]]:\n"
               + "        return self._data\n"
               + "    @auto_readonly\n"
               + "    def __iter__(self) -> SpanIter[auto_readonly[Optional[Foo]]]:\n"
               + "        return SpanIter(self.__span__())\n"
               + "def main() -> None:\n"
               + "    b = OptBuf()\n"
               + "    xs = [1 if p is not None else 0 for p in b]\n"
               + "    print(xs)\n"
               + "main()\n")
        thir = _lower(src)
        assert _fn(thir, "main") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

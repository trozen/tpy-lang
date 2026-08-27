"""Generic-call arg rows, wave 12: lambdas at still-open Fn slots (with
tuple params), open-slot subscript args, nested value-tuple names at T
slots, the narrowed Optional[wrapper] pass, and the value-yielding
generator comp source -- plus the boundaries that stay AST."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _lower_ctx_witnessed, _fn, _compile, _entry, _assert_rejects_at,
    _assert_routes_byte_identical,
)


def _gen(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=True,
                                      thir_codegen=thir))
    return compiler, hpp, cpp


def _assert_identical(src: str) -> 'tuple[dict, dict]':
    _, hpp_ast, cpp_ast = _gen(src, thir=False)
    c, hpp_thir, cpp_thir = _gen(src, thir=True)
    assert hpp_ast == hpp_thir
    assert cpp_ast == cpp_thir
    return c._thir_face_witnesses, c._thir_fallback


class TestOpenSlotLambdaAndElem:
    """A generic caller passing a lambda at a still-open Fn slot and a
    container-element subscript at the open T slot (`map_keys(pairs,
    lambda p: p[1])`, `f(xs[i])`, `out.append(xs[i])`)."""
    _SRC = (
        "from tpy import Fn, Int32, Comparable, Own\n"
        "def map_keys[T, K: Comparable](xs: list[T], f: Fn[[T], K])"
        " -> Own[list[K]]:\n"
        "    out: list[K] = []\n"
        "    i = 0\n"
        "    while i < len(xs):\n"
        "        out.append(f(xs[i]))\n"
        "        i += 1\n"
        "    return out\n"
        "def names[T](pairs: list[tuple[T, str]]) -> Own[list[str]]:\n"
        "    return map_keys(pairs, lambda p: p[1])\n"
        "def main() -> None:\n"
        "    ps: list[tuple[Int32, str]] = [(1, \"c\"), (2, \"a\")]\n"
        "    for s in names(ps):\n        print(s)\n"
        "main()\n")

    def test_lambda_and_subscript_args_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "names") is not None
        assert _fn(thir, "map_keys") is not None
        assert w.get("call.generic_open_slot_lambda", 0) >= 1
        assert w.get("call.generic_open_slot_elem", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        both = _hpp + cpp
        assert "::tpy::val_or_ptr_t<T>" in both
        assert "std::get<1>(p)" in both

    def test_ptr_elem_tuple_lambda_param_stays_ast(self):
        # A lambda whose tuple param carries a pointer-repr element
        # (record) keeps the AST path -- that spelling is unverified.
        src = (
            "from tpy import Fn, Int32, Comparable, Own\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
            "def pick[T, K: Comparable](xs: list[T], f: Fn[[T], K])"
            " -> Own[list[K]]:\n"
            "    out: list[K] = []\n"
            "    i = 0\n"
            "    while i < len(xs):\n"
            "        out.append(f(xs[i]))\n"
            "        i += 1\n"
            "    return out\n"
            "def keys(pairs: list[tuple[Box, Int32]]) -> Own[list[Int32]]:\n"
            "    return pick(pairs, lambda p: p[1])\n"
            "def main() -> None:\n"
            "    ps: list[tuple[Box, Int32]] = [(Box(1), 5)]\n"
            "    for n in keys(ps):\n        print(n)\n"
            "main()\n")
        _, _hpp_a, cpp_a = _gen(src, thir=False)
        c, _hpp_t, cpp_t = _gen(src, thir=True)
        assert cpp_a == cpp_t
        # Exactly ONE body falls back -- keys, rejected at the call whose
        # lambda arg the gate refuses; pick's own body stays clean.
        _assert_rejects_at(dict(c._thir_fallback), "body:expr.call",
                           "call.generic_arg_shape", count=1)


class TestOpenSlotElemBoundary:
    """The open-slot bare bind admits NAME and SUBSCRIPT sources only: a
    FIELD-access arg at the still-open slot (`f(c.v)` in a generic body)
    keeps deferring."""

    def test_field_access_at_open_slot_stays_ast(self):
        src = (
            "from tpy import Fn, Int32, Comparable, Own\n"
            "class Cell[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: T) -> None:\n        self.v = v\n"
            "def app[T, K: Comparable](c: Cell[T], f: Fn[[T], K]) -> K:\n"
            "    return f(c.v)\n"
            "def use(c: Cell[Int32]) -> Int32:\n"
            "    return app(c, lambda x: x + 1)\n"
            "def main() -> None:\n"
            "    print(use(Cell(4)))\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.arg_shape.generic")


class TestNestedTupleNameAtTSlot:
    """`less(a, b)` on `((1, 2), "x")` at a bounded T slot: the nested
    value-tuple NAME binds bare. A nested tuple LITERAL stays AST."""
    _SRC = (
        "from tpy import Comparable\n"
        "def less[T: Comparable](a: T, b: T) -> bool:\n"
        "    return a < b\n"
        "def main() -> None:\n"
        "    a = ((1, 2), \"x\")\n"
        "    b = ((1, 3), \"x\")\n"
        "    print(less(a, b))\n"
        "main()\n")

    def test_nested_tuple_names_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert w.get("call.generic_nested_tuple_name", 0) >= 1
        _assert_routes_byte_identical(self._SRC)

    def test_nested_tuple_literal_routes_inline(self):
        # Converted fence: the generic value-tuple-literal row admits the
        # nested flavor too -- the inline spelled brace prvalue
        # (`less<std::tuple<...>>(std::tuple<...>{...}, ...)`).
        src = (
            "from tpy import Comparable\n"
            "def less[T: Comparable](a: T, b: T) -> bool:\n"
            "    return a < b\n"
            "def main() -> None:\n"
            "    print(less(((1, 2), \"x\"), ((1, 3), \"x\")))\n"
            "main()\n")
        _, _hpp_a, cpp_a = _gen(src, thir=False)
        c, _hpp_t, cpp_t = _gen(src, thir=True)
        assert cpp_a == cpp_t
        assert not c._thir_fallback


class TestNarrowedOptWrapperArg:
    """`leaf_count(t)` on a `Tree[int] | None` param after the None test:
    the pointer-local deref binds the same-wrapper slot bare."""
    _TREELIB = (
        "from tpy import Int32\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def leaf_count[T](t: Tree[T]) -> Int32:\n"
        "    match t:\n"
        "        case list() as branches:\n"
        "            total = 0\n"
        "            for child in branches:\n"
        "                total += leaf_count(child)\n"
        "            return total\n"
        "        case _:\n"
        "            return 1\n")

    def test_narrowed_opt_wrapper_routes(self):
        src = (self._TREELIB
               + "def maybe_count(t: Tree[int] | None) -> int:\n"
               + "    if t is None:\n        return -1\n"
               + "    return leaf_count(t)\n"
               + "def main() -> None:\n"
               + "    t: Tree[int] = [1, [2, 3], 4]\n"
               + "    print(maybe_count(t))\n"
               + "    print(maybe_count(None))\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "maybe_count") is not None
        assert w.get("arg.ru_wrapper_opt_narrowed", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "leaf_count<::tpy::BigInt>((*t))" in cpp


class TestGenFactoryCompSource:
    """`[v for v in wrap(3)]` -- a VALUE-yielding generator-factory comp
    source takes the owning `auto __obj_N` capture with begin/end, and the
    source call's own arg temps flush before the statement."""
    _SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "def repeat_n[T](obj: T, times: Int32) -> Iterator[T]:\n"
        "    for _ in range(times):\n"
        "        yield obj\n"
        "def main() -> None:\n"
        "    print([v for v in repeat_n(8, 2)])\n"
        "main()\n")

    def test_value_gen_source_routes(self):
        # The GENERIC generator's own body routes too since the
        # generic-sgen cells (the T-typed param-name yield in a range
        # loop); the comp in main routes as before.
        w, fallback = _assert_identical(self._SRC)
        assert w.get("comp.genfac_source", 0) >= 1
        assert not fallback, fallback
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert "int32_t __tmp_1 = 8;" in cpp
        assert "repeat_n<int32_t>(__tmp_1, 2)" in cpp

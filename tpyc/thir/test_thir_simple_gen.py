"""Simple-generator (lambda peephole) leaf routing -- the gen_generators seam.

Pins the foundation slice: routed bodies are byte-identical to the AST
path with every leaf render witnessed, and each sliced-out shape rejects
with its named `sgen.*` reason (falling back to the AST leaves) instead of
routing wrong."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry

_ITER = "from tpy import Int32, Int64\nfrom typing import Iterator\n\n"


def _gen(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=True,
                                      thir_codegen=thir))
    return compiler, hpp, cpp


def _assert_identical(src: str) -> 'tuple[dict, dict]':
    """Byte-compare THIR vs AST output; return (witnesses, fallback)."""
    _, hpp_ast, cpp_ast = _gen(src, thir=False)
    c, hpp_thir, cpp_thir = _gen(src, thir=True)
    assert hpp_ast == hpp_thir
    assert cpp_ast == cpp_thir
    return c._thir_face_witnesses, c._thir_fallback


def _sgen_fallback(src: str) -> dict:
    """The body-component fallback reasons, sans the `body:` prefix."""
    c, _hpp, _cpp = _gen(src, thir=True)
    return {k.split(":", 1)[1]: n for k, n in c._thir_fallback.items()
            if k.startswith("body:")}


class TestRoutedFoundation:
    def test_while_counter_routes(self):
        src = (_ITER
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield i\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(3):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert witnesses.get("sgen.while_cond") == 1
        assert witnesses.get("sgen.yield_value") == 1
        assert not any("sgen." in k for k in fallback)

    def test_init_block_and_pre_yield_route(self):
        # Init stmts before the loop (lambda captures) + a pre-yield
        # statement inside it.
        src = (_ITER
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    total = 0\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        total = total + i\n"
               + "        yield total\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(4):\n        print(x)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1

    def test_for_range_routes(self):
        # Both range forms: the 1-arg stop and the 2-arg start/stop bounds
        # render position-blind inside the skeleton's static_cast scaffolding.
        src = (_ITER
               + "def squares(n: Int32) -> Iterator[Int32]:\n"
               + "    for i in range(n):\n"
               + "        yield i * i\n\n"
               + "def offsets(a: Int32, b: Int32) -> Iterator[Int32]:\n"
               + "    for i in range(a, b):\n"
               + "        yield i + 10\n\n"
               + "def main() -> None:\n"
               + "    for x in squares(4):\n        print(x)\n"
               + "    for y in offsets(1, 4):\n        print(y)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 2
        assert witnesses.get("sgen.range_arg") == 2

    def test_for_container_param_routes(self):
        # The universal __iter__/__next__ pull branch over a list param: the
        # iterable renders once, reused across the skeleton's decltype /
        # emplace scaffolding.
        src = (_ITER
               + "def doubles(xs: list[Int32]) -> Iterator[Int32]:\n"
               + "    for x in xs:\n"
               + "        yield x * 2\n\n"
               + "def main() -> None:\n"
               + "    items = [1, 2, 3]\n"
               + "    for v in doubles(items):\n        print(v)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert witnesses.get("sgen.iterable") == 1

    def test_method_generator_routes(self):
        # A method peephole reads `self` as `(*this)` (generator_self_ref).
        src = (_ITER
               + "class C:\n"
               + "    base: Int32\n"
               + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
               + "    def counts(self, n: Int32) -> Iterator[Int32]:\n"
               + "        i = 0\n"
               + "        while i < n:\n"
               + "            yield i + self.base\n"
               + "            i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    c = C(7)\n"
               + "    for x in c.counts(3):\n        print(x)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        _, hpp, _cpp = _gen(src, thir=True)
        assert "(*this).base" in hpp


class TestValueFamilies:
    def test_enum_yield_routes(self):
        src = ("from tpy import Int32\nfrom typing import Iterator\n"
               + "from enum import Enum\n\n"
               + "class Color(Enum):\n"
               + "    RED = 0\n"
               + "    GREEN = 1\n\n"
               + "def colors(n: Int32) -> Iterator[Color]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield Color.RED\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for c in colors(2):\n        print(c)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1

    def test_char_yield_routes(self):
        # Char yield + Char loop var, via the native-iterable str branch.
        src = ("from tpy import Int32, Char\nfrom typing import Iterator\n\n"
               + "def chars(s: str) -> Iterator[Char]:\n"
               + "    for c in s:\n"
               + "        yield c\n\n"
               + "def main() -> None:\n"
               + "    for c in chars(\"ab\"):\n        print(c)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert witnesses.get("sgen.iterable") == 1

    def test_optional_none_test_cond_routes(self):
        # `while x is not None:` on a pointer-repr Optional param lowers as a
        # truthy None-test (not the U4 narrow-cond shape), so it routes.
        src = (_ITER
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def ticks(x: Node | None) -> Iterator[Int32]:\n"
               + "    while x is not None:\n"
               + "        yield x.v\n\n"
               + "def main() -> None:\n"
               + "    for v in ticks(None):\n        print(v)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)


class TestSlicedOutShapes:
    # sgen.static_method is defensive-only: sema rejects a @staticmethod
    # generator at parse time, so the reject can never fire end-to-end. The
    # same holds for _marker_call_kind's is_static_call generator exclusion
    # on the caller side. sgen.method / sgen.bare_yield / sgen.hoist_promoted
    # and the body.hoisted_vars tail-check likewise mirror sync-arm defensive
    # checks with no reachable public-entry shape found by probing.
    def test_record_yield_and_loop_var_route(self):
        # A record yield uses the val_or_ref borrow slot (bare-name render)
        # and the record loop element binds `auto&&` -- both skeleton-side,
        # so the F1-record family routes.
        src = (_ITER
               + "class P:\n"
               + "    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n        self.x = x\n\n"
               + "def gen(ps: list[P]) -> Iterator[P]:\n"
               + "    for p in ps:\n"
               + "        yield p\n\n"
               + "def main() -> None:\n"
               + "    ps = [P(1), P(2)]\n"
               + "    for p in gen(ps):\n        print(p.x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)

    def test_own_record_yield_routes(self):
        # An Own[record] yield keeps the bare value slot; the skeleton's
        # `std::move(__val)` moves it out -- the ctor-call value render is
        # position-blind.
        src = (_ITER
               + "from tpy import Own\n"
               + "class P:\n"
               + "    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n        self.x = x\n\n"
               + "def gen(n: Int32) -> Iterator[Own[P]]:\n"
               + "    for i in range(n):\n"
               + "        yield P(i)\n\n"
               + "def main() -> None:\n"
               + "    for p in gen(2):\n        print(p.x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)

    def test_str_yield_routes(self):
        # A str yield whose source is the OWNED param capture: the skeleton's
        # optional<std::string> slot absorbs the bare render.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def echo(s: str, n: Int32) -> Iterator[str]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield s\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for v in echo(\"hi\", 2):\n        print(v)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)

    def test_static_view_yield_materializes(self):
        # Same slot, still-a-VIEW source: a literal-sourced local keeps its
        # zero-copy view (static storage), so string_view -> string is explicit
        # and the peephole needs the same copy the frame path takes. A `while`
        # body is deliberate -- a for-loop head falls back (sgen.loop_var_type),
        # which would make the render assertion read the AST output instead.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def parts(n: Int32) -> Iterator[str]:\n"
               + "    lit = \"static\"\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield lit\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for v in parts(2):\n        print(v)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert "std::string(lit)" in hpp

    def test_static_bytes_view_yield_materializes(self):
        # bytes sibling: span -> vector has no implicit conversion at all, so
        # the missing copy here was a hard build failure, not a silent one.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def chunks(n: Int32) -> Iterator[bytes]:\n"
               + "    view = b\"xy\"\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield view\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for c in chunks(2):\n        print(len(c))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not any("sgen." in k for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert "::tpy::bytes_copy(view)" in hpp

    def test_tuple_yield_literal_routes(self):
        # A tuple-literal yield now routes through the borrow builder (the
        # resumable tuple arm's mirror); the non-name element shapes
        # (Int32(i) fold, subscript element) validate inside the builder.
        src = (_ITER
               + "class P:\n"
               + "    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n        self.x = x\n\n"
               + "def gen(ps: list[P]) -> Iterator[tuple[Int32, P]]:\n"
               + "    for i in range(len(ps)):\n"
               + "        yield (Int32(i), ps[i])\n\n"
               + "def main() -> None:\n"
               + "    ps = [P(1), P(2)]\n"
               + "    for i, p in gen(ps):\n        print(i, p.x)\nmain()\n")
        witnesses, _fallback = _assert_identical(src)
        assert not _sgen_fallback(src).get("sgen.yield_type")

    def test_str_loop_var_defers(self):
        # A str loop element stays out of the loop-var slice (the view/owned
        # usage-resolution duality is not mirrored for the sgen binding);
        # the yield gate passes (scalar).
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def lens(ws: list[str]) -> Iterator[Int32]:\n"
               + "    for w in ws:\n"
               + "        yield Int32(len(w))\n\n"
               + "def main() -> None:\n"
               + "    ws = [\"a\", \"bc\"]\n"
               + "    for n in lens(ws):\n        print(n)\nmain()\n")
        assert _sgen_fallback(src).get("sgen.loop_var_type") == 1

    def test_generic_defers(self):
        # The generic DEF still rejects at the sgen gate (a template frame is
        # not the sliced lambda peephole). The CALLER's foreach over that
        # factory now ROUTES: the iterable position spells the generic call
        # exactly like any other generic free call (`rep<int32_t>(...)`), so
        # only the DEF stays on the AST path -- byte-identical either way.
        src = (_ITER
               + "def rep[T](v: T, n: Int32) -> Iterator[T]:\n"
               + "    for _i in range(n):\n"
               + "        yield v\n\n"
               + "def main() -> None:\n"
               + "    for x in rep(5, 2):\n        print(x)\nmain()\n")
        _assert_identical(src)
        fb = _sgen_fallback(src)
        assert fb.get("sgen.generic") == 1
        assert fb.get("expr.call") is None

    def test_generic_record_method_defers(self):
        # The generator METHOD on a generic record rejects (template frame);
        # the CALLER's member-call foreach still routes (monomorphized
        # spelling).
        src = (_ITER
               + "class Box[T]:\n"
               + "    v: T\n"
               + "    def __init__(self, v: T) -> None:\n"
               + "        self.v = v\n"
               + "    def rep(self, n: Int32) -> Iterator[Int32]:\n"
               + "        i = 0\n"
               + "        while i < n:\n"
               + "            yield i\n"
               + "            i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    b = Box(7)\n"
               + "    for x in b.rep(2):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert fallback.get("body:sgen.generic_record") == 1
        assert witnesses.get("foreach.iter_proto") == 1

    def test_property_generator_defers(self):
        src = (_ITER
               + "class Bag:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    @property\n"
               + "    def items(self) -> Iterator[Int32]:\n"
               + "        i = 0\n"
               + "        while i < self.n:\n"
               + "            yield i\n"
               + "            i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    b = Bag(2)\n"
               + "    for v in b.items:\n        print(v)\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("body:sgen.property", 0) >= 1

    def test_nonbool_call_cond_defers(self):
        # An Int32-returning call in the while-truthy position rejects at
        # _lower_truthy -> sgen.cond.
        src = (_ITER
               + "def countdown(n: Int32) -> Int32:\n"
               + "    return n\n\n"
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    while countdown(n):\n"
               + "        yield n\n"
               + "        n = n - 1\n\n"
               + "def main() -> None:\n"
               + "    for v in gen(2):\n        print(v)\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("body:sgen.cond") == 1

    def test_while_isinstance_cond_defers(self):
        # A U4 while-isinstance head would need the loop-entry extraction
        # woven into the skeleton -> sgen.narrow_cond.
        src = (_ITER
               + "class A:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "class B:\n"
               + "    w: Int32\n"
               + "    def __init__(self, w: Int32) -> None:\n"
               + "        self.w = w\n\n"
               + "def ticks(x: A | B) -> Iterator[Int32]:\n"
               + "    while isinstance(x, A):\n"
               + "        yield x.v\n\n"
               + "def main() -> None:\n"
               + "    b = B(1)\n"
               + "    for v in ticks(b):\n        print(v)\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("body:sgen.narrow_cond") == 1

    def test_forwarded_proto_local_defers(self):
        src = (_ITER
               + "def relay(it: Iterator[Int32]) -> Iterator[Int32]:\n"
               + "    xs = it\n"
               + "    for x in xs:\n"
               + "        yield x\n\n"
               + "def src(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield i\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for v in relay(src(3)):\n        print(v)\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("body:sgen.forwarded_local") == 1

    def test_range3_iterable_routes(self):
        # A 3-arg range in a generator's for-loop (not the counter-loop shape)
        # routes through the sgen peephole byte-identically: the pull-branch
        # iterable is the range() call, which the range-object arm now lowers.
        src = (_ITER
               + "def evens(n: Int32) -> Iterator[Int32]:\n"
               + "    for i in range(0, n, 2):\n"
               + "        yield i\n\n"
               + "def main() -> None:\n"
               + "    for v in evens(7):\n        print(v)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body")
        assert not fallback


class TestForeachCallers:
    """The caller half of the generator track: foreach over a generator call
    / user-iterator name routes via THIRForIterProto (the universal
    `::tpy::__iter__` loop, `_gen_direct_next_loop_with_iter`)."""

    GEN = (_ITER
           + "def gen(n: Int32) -> Iterator[Int32]:\n"
           + "    i = 0\n"
           + "    while i < n:\n"
           + "        yield i\n"
           + "        i = i + 1\n\n")

    def test_foreach_gen_call_routes(self):
        # An rvalue source: the owning `auto __src_N` capture inside the
        # CPython-refcount brace scope; body statements keep the AST's
        # original-level indent inside that scope.
        src = (self.GEN
               + "def main() -> None:\n"
               + "    for x in gen(3):\n"
               + "        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("foreach.iter_proto") == 1
        assert not fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto __src_0 = gen(3);" in cpp
        assert "auto&& __itr_0 = ::tpy::__iter__(__src_0);" in cpp

    def test_foreach_gen_call_with_else_routes(self):
        src = (self.GEN
               + "def main() -> None:\n"
               + "    for x in gen(3):\n"
               + "        if x > 100:\n"
               + "            break\n"
               + "        print(x)\n"
               + "    else:\n"
               + "        print(99)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("foreach.iter_proto") == 1

    def test_foreach_user_iterator_name_routes(self):
        # An lvalue source: `auto& __src_N = c;`.
        src = (_ITER
               + "class Counter:\n"
               + "    n: Int32\n"
               + "    limit: Int32\n"
               + "    def __init__(self, limit: Int32) -> None:\n"
               + "        self.n = 0\n"
               + "        self.limit = limit\n"
               + "    def __iter__(self) -> Iterator[Int32]:\n"
               + "        while self.n < self.limit:\n"
               + "            yield self.n\n"
               + "            self.n = self.n + 1\n\n"
               + "def main() -> None:\n"
               + "    c = Counter(3)\n"
               + "    for x in c:\n"
               + "        print(x)\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("foreach.iter_proto") == 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto& __src_0 = c;" in cpp

    def test_foreach_member_gen_call_routes(self):
        # A bare-name-receiver member generator call routes (the
        # _member_gen_call_iterable_ok override bypasses the fi generator
        # reject and the result-family gates; receiver/args gate as usual).
        src = (_ITER
               + "class Stack:\n"
               + "    items: list[Int32]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.items = []\n"
               + "    def push(self, v: Int32) -> None:\n"
               + "        self.items.append(v)\n"
               + "    def each_doubled(self) -> Iterator[Int32]:\n"
               + "        for x in self.items:\n"
               + "            yield x * 2\n\n"
               + "def main() -> None:\n"
               + "    s = Stack()\n"
               + "    s.push(3)\n"
               + "    for v in s.each_doubled():\n"
               + "        print(v)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("foreach.iter_proto") == 1
        # The generator BODY routed too (the sgen field-iterable arm), not
        # just the caller: a silent leaf fallback would keep the caller
        # witness and the byte-diff green while un-routing each_doubled.
        assert witnesses.get("sgen.iterable") == 1
        assert not fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto __src_0 = s.each_doubled();" in cpp

    def test_foreach_self_member_gen_call_routes(self):
        # `for x in self.counts():` -- the receiver is the bare `self` name;
        # the factory call renders `this->counts()` like any member call.
        src = (_ITER
               + "class Runner:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def counts(self) -> Iterator[Int32]:\n"
               + "        i = 0\n"
               + "        while i < self.n:\n"
               + "            yield i\n"
               + "            i = i + 1\n"
               + "    def total(self) -> Int32:\n"
               + "        t = 0\n"
               + "        for x in self.counts():\n"
               + "            t = t + x\n"
               + "        return t\n\n"
               + "def main() -> None:\n"
               + "    r = Runner(4)\n"
               + "    print(r.total())\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("foreach.iter_proto") == 1

    def test_foreach_self_iterable_defers(self):
        # `for x in self:` renders the receiver dereferenced (`(*this)`) --
        # the self-iterable rung stays on the AST path (byte-identical via
        # fallback).
        src = (_ITER
               + "class Bag:\n"
               + "    total: Int32\n"
               + "    def __init__(self) -> None:\n"
               + "        self.total = 0\n"
               + "    def __iter__(self) -> Iterator[Int32]:\n"
               + "        yield self.total\n"
               + "    def first(self) -> Int32:\n"
               + "        for x in self:\n"
               + "            return x\n"
               + "        return 0\n\n"
               + "def main() -> None:\n"
               + "    b = Bag()\n"
               + "    print(b.first())\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not witnesses.get("foreach.iter_proto")
        assert any("iter.name" in k or "user_iterator" in k
                   for k in fallback), fallback


class TestTupleYield:
    """Tuple yield slots: the resumable Yield tuple arm's mirror (borrow
    builder for pointer-repr slots, value builder otherwise); non-literal
    sources keep falling back."""

    _BOX = (_ITER
            + "class Box:\n"
            + "    val: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n"
            + "        self.val = v\n\n")

    def test_borrow_tuple_literal_yield_routes(self):
        src = (self._BOX
               + "def pairs(items: list[Box]) -> Iterator[tuple[Int32, Box]]:\n"
               + "    i = 0\n"
               + "    for item in items:\n"
               + "        yield (i, item)\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n"
               + "    xs = [Box(1), Box(2)]\n"
               + "    for i, b in pairs(xs):\n"
               + "        b.val = i + 10\n"
               + "    for b in xs:\n"
               + "        print(b.val)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.tuple_yield", 0) >= 1
        assert not fallback

    def test_value_tuple_literal_yield_routes(self):
        src = (_ITER
               + "def pairs(n: Int32) -> Iterator[tuple[Int32, Int32]]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield (i, i * 2)\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n"
               + "    for a, b in pairs(2):\n"
               + "        print(a, b)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.tuple_yield", 0) >= 1
        assert not fallback

    def test_name_tuple_yield_stays_ast(self):
        # A yielded tuple NAME (a loop var) is not the literal shape --
        # falls back (byte-identical via fallback).
        src = (self._BOX
               + "def relay(items: list[tuple[Int32, Box]])"
               + " -> Iterator[tuple[Int32, Box]]:\n"
               + "    for it in items:\n"
               + "        yield it\n\n"
               + "def main() -> None:\n"
               + "    b = Box(3)\n"
               + "    items: list[tuple[Int32, Box]] = [(1, b)]\n"
               + "    for i, x in relay(items):\n"
               + "        x.val = 9\n"
               + "    print(b.val)\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert fallback, "expected the name-source tuple yield to fall back"


class TestYieldCopyRecord:
    _PT = ("from tpy import Int32, Own, copy\n"
           "from typing import Iterator\n\n"
           "class Point:\n    x: Int32\n"
           "    def __init__(self, x: Int32) -> None:\n        self.x = x\n\n")

    def test_yield_copy_routes(self):
        # `yield copy(p)` binds the skeleton's `__val` slot through the shared
        # copy-construct row (`Point(p)`), like every other copy() sink.
        src = (self._PT
               + "def points() -> Iterator[Own[Point]]:\n"
               + "    src: list[Point] = [Point(3), Point(1)]\n"
               + "    for p in src:\n"
               + "        yield copy(p)\n\n"
               + "def main() -> None:\n"
               + "    for p in points():\n        print(p.x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.yield_copy_record", 0) >= 1
        assert not fallback

    def test_yield_copy_of_ctor_rvalue_stays_ast(self):
        # `copy(Point(1))` is the PRVALUE arm of _gen_copy_expr (the ctor
        # render handed back unchanged), not the copy-construct row -- it must
        # not ride this arm.
        src = (self._PT
               + "def points() -> Iterator[Own[Point]]:\n"
               + "    i = 0\n"
               + "    while i < 2:\n"
               + "        yield copy(Point(i))\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n"
               + "    for p in points():\n        print(p.x)\nmain()\n")
        witnesses, _fallback = _assert_identical(src)
        assert witnesses.get("sgen.yield_copy_record", 0) == 0


class TestContainerLoopVar:
    """A CONTAINER element loop var (`for v in d.values():`) binds the same
    skeleton `auto&& v = *__beg++;` as an F1 record, and the leaf reads it
    through the container-name arms (`v.append(9)` -> `v.push_back(9)`).
    A TUPLE element (`d.items()`) keeps its own rung (the storage-form
    tuple-local machinery), rejected at sgen.loop_var_type."""

    def test_container_loop_var_routes(self):
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def bump(d: dict[str, list[Int32]]) -> Iterator[Int32]:\n"
               + "    for v in d.values():\n"
               + "        v.append(9)\n"
               + "        yield len(v)\n\n"
               + "def main() -> None:\n"
               + "    d: dict[str, list[Int32]] = {}\n"
               + "    d[\"a\"] = [1]\n"
               + "    for n in bump(d):\n        print(n)\n"
               + "    print(d[\"a\"])\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("sgen.body") == 1
        assert not fallback

    def test_whole_tuple_loop_var_still_defers(self):
        # The `d.items()` UNPACK form routes (the items tuple-unpack arm);
        # the fence is the WHOLE-tuple loop var (`for kv in d.items()`),
        # whose element is a tuple type -- the storage-form tuple-local
        # rung, rejected at sgen.loop_var_type.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def pairs(d: dict[str, Int32]) -> Iterator[Int32]:\n"
               + "    for kv in d.items():\n"
               + "        yield kv[1]\n\n"
               + "def main() -> None:\n"
               + "    for n in pairs({\"a\": 1}):\n        print(n)\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert any("sgen.loop_var_type" in k for k in fallback), fallback

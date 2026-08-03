"""Pins for the free-call RESULT-gate wave: the `copy()` sinks that had no
interception, the generic callee's open-slot / container-literal arg rows, the
generic GENERATOR factory in iterable position, and the slice-assign generator
RHS -- plus the neighbours that must keep rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _compile,
    _entry,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)


def _cpp(src: str) -> str:
    """The THIR-path emit (header + source; templates live in the header, so a
    generic render is only visible in the pair). Byte-identity is asserted
    separately."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
    return hpp + cpp

_PT = ("from tpy import Int32, copy\n"
       "class Point:\n    x: Int32\n    y: Int32\n"
       "    def __init__(self, x: Int32, y: Int32) -> None:\n"
       "        self.x = x\n        self.y = y\n")


class TestCopyAtContainerElement:
    SRC = _PT + (
        "class Holder:\n    data: tuple[Point, Int32]\n"
        "    def __init__(self, p: Point, n: Int32) -> None:\n"
        "        self.data = (copy(p), n)\n"
        "def update(h: Holder, p: Point) -> None:\n"
        "    h.data = (copy(p), Int32(99))\n"
        "def main() -> None:\n"
        "    p = Point(1, 2)\n"
        "    h = Holder(p, 42)\n"
        "    update(h, p)\n"
        "    print(h.data[0].x)\n"
        "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "update") is not None
        assert faces.get("containerlit.copy_record", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        # The copy renders the SOURCE record's own copy-construct spelling.
        assert "Point(p)" in cpp

    def test_ctor_rvalue_copy_element_stays_ast(self):
        # `copy(Point(..))` is _gen_copy_expr's PRVALUE arm (the ctor render
        # handed back unchanged), not the copy-construct row -- the element
        # arm keys on a plain NAME source and must not swallow it.
        src = _PT + (
            "class Holder:\n    data: tuple[Point, Int32]\n"
            "    def __init__(self, p: Point, n: Int32) -> None:\n"
            "        self.data = (copy(p), n)\n"
            "def build(h: Holder) -> None:\n"
            "    h.data = (copy(Point(7, 8)), Int32(1))\n"
            "def main() -> None:\n"
            "    h = Holder(Point(1, 2), 42)\n"
            "    build(h)\n    print(h.data[0].x)\nmain()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("containerlit.copy_record", 0) == 0


class TestCopyIntoAny:
    SRC = ("from typing import Any\nfrom tpy import copy\n"
           "class Counter:\n"
           "    def __init__(self, n: int) -> None:\n        self.n = n\n"
           "def main() -> None:\n"
           "    c = Counter(1)\n"
           "    a: Any = copy(c)\n"
           "    c.n = 99\n"
           "    print(c.n)\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("coerce.into_any_copy_peel", 0) >= 1

    def test_byte_identical_drops_the_redundant_copy(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        # make_any copy-constructs into the cell, so no `Counter(c)` wrap.
        assert "::tpy::make_any(c)" in cpp
        assert "make_any(Counter(c))" not in cpp


class TestGenericOpenSlotName:
    SRC = ("from typing import Protocol\n"
           "class Mutable(Protocol):\n"
           "    def mutate(self) -> None: ...\n"
           "class Point:\n    x: int\n"
           "    def __init__(self, x: int) -> None:\n        self.x = x\n"
           "    def mutate(self) -> None:\n        self.x += 10\n"
           "def first[T](items: list[T]) -> T:\n"
           "    return items[0]\n"
           "def process[T: Mutable](items: list[T]) -> None:\n"
           "    item = first(items)\n"
           "    item.mutate()\n"
           "def main() -> None:\n"
           "    pts = [Point(1), Point(2)]\n"
           "    process(pts)\n"
           "    print(pts[0].x)\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "process") is not None
        assert faces.get("call.generic_open_slot_name", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "first<T>(items)" in cpp

    def test_own_slot_name_keeps_the_move_row(self):
        # An `Own[T]` slot MOVES rather than binding; the open-slot row is
        # ordered after `_own_move_arg` and must never claim it.
        src = ("from tpy import Int32, Own\n"
               "class Box:\n    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               "def sink[T](x: Own[T]) -> None:\n    print(1)\n"
               "def relay[T](x: Own[T]) -> None:\n    sink(x)\n"
               "def main() -> None:\n"
               "    relay(Box(3))\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "std::move(x)" in cpp


class TestGenericContainerLiteralArg:
    SRC = ("from typing import Iterator\n"
           "def doubled[T](xs: list[T]) -> Iterator[T]:\n"
           "    for x in xs:\n        yield x\n"
           "def main() -> None:\n"
           "    for v in doubled([1, 2, 3]):\n        print(v)\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("argtemp.generic_container_literal", 0) >= 1

    def test_byte_identical_hoists_the_named_temp(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        # The `std::vector<T>&` param cannot bind a brace prvalue.
        assert "std::vector<int32_t> __tmp_1 = {1, 2, 3};" in cpp
        assert "doubled<int32_t>(__tmp_1)" in cpp


class TestGenericGeneratorFactory:
    def test_iterable_position_routes(self):
        src = ("from tpy import Int32\nfrom typing import Iterator\n"
               "def rep[T](v: T, n: Int32) -> Iterator[T]:\n"
               "    for _i in range(n):\n        yield v\n"
               "def main() -> None:\n"
               "    for x in rep(5, 2):\n        print(x)\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "rep<int32_t>(" in cpp

    def test_local_decl_source_routes(self):
        # The for-each SOURCE decl is an iterable position too, so binding the
        # factory to a local routes on the same row.
        src = ("from tpy import Int32\nfrom typing import Iterator\n"
               "def rep[T](v: T, n: Int32) -> Iterator[T]:\n"
               "    for _i in range(n):\n        yield v\n"
               "def main() -> None:\n"
               "    g = rep(5, 2)\n"
               "    for x in g:\n        print(x)\n"
               "main()\n")
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None

    def test_arg_position_still_defers(self):
        # The widening is scoped to callers that pass `generator_ok` (the
        # iterable positions). A generic generator factory handed to an
        # ordinary CALL ARG has no such admission and must keep rejecting --
        # its arg slot render is unprobed.
        src = ("from tpy import Int32\nfrom typing import Iterator\n"
               "def rep[T](v: T, n: Int32) -> Iterator[T]:\n"
               "    for _i in range(n):\n        yield v\n"
               "def take(it: Iterator[Int32]) -> None:\n"
               "    for x in it:\n        print(x)\n"
               "def main() -> None:\n"
               "    take(rep(5, 2))\n"
               "main()\n")
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None


class TestSliceAssignGeneratorRhs:
    SRC = ("from tpy import Int32\nfrom typing import Iterator\n"
           "def gen_values() -> Iterator[Int32]:\n"
           "    yield 10\n    yield 20\n"
           "def main() -> None:\n"
           "    d: list[Int32] = [1, 2, 3, 4, 5]\n"
           "    d[1:3] = gen_values()\n"
           "    for x in d:\n        print(x)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::list_set_slice(d, ::tpy::BasicSlice{1, 3}, gen_values())" in cpp


class TestBorrowTupleSubscriptArg:
    SRC = ("from tpy import Int32\n"
           "class T:\n    v: Int32\n"
           "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
           "def consume(p: tuple[T | None, T | None]) -> None:\n"
           "    print(1)\n"
           "def main() -> None:\n"
           "    t1 = T(1)\n"
           "    t2 = T(2)\n"
           "    items: list[tuple[T | None, T | None]] = [(t1, t2)]\n"
           "    consume(items[0])\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("arg.borrow_tuple_subscript", 0) >= 1
        assert faces.get("subscript.borrow_tuple_elem", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::tuple_to_pointer<" in cpp
        assert "::tpy::__getitem__(items, 0)" in cpp

    def test_slice_subscript_stays_ast(self):
        # A SLICE read is a different container operation entirely; the row
        # keys on the single-index element read.
        src = ("from tpy import Int32, Span\n"
               "class T:\n    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               "def consume(ps: Span[tuple[T | None, T | None]]) -> None:\n"
               "    print(len(ps))\n"
               "def main() -> None:\n"
               "    t1 = T(1)\n"
               "    items: list[tuple[T | None, T | None]] = [(t1, None)]\n"
               "    consume(items[0:1])\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.borrow_tuple_subscript", 0) == 0


class TestRecursiveUnionWrapperLiteralArg:
    SRC = ("from tplib import Box\n"
           "type Value = int | str | Neg\n"
           "class Neg:\n    inner: Box[Value]\n"
           "def kind(v: Value) -> int:\n"
           "    if isinstance(v, Neg):\n        return 0\n"
           "    elif isinstance(v, int):\n        return 1\n"
           "    else:\n        return 2\n"
           "def main() -> None:\n"
           "    print(kind(42))\n"
           "    print(kind(\"hello\"))\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("argtemp.ru_wrapper_literal", 0) >= 2

    def test_byte_identical_hoists_the_typed_temp(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "Value __tmp_1 = 42;" in cpp
        assert "kind(__tmp_1)" in cpp

    def test_union_typed_name_stays_bare(self):
        # A same-union source is `already_union` on the AST path: it passes
        # BARE with no temp, so the literal row must not claim it.
        src = ("from tplib import Box\n"
               "type Value = int | str | Neg\n"
               "class Neg:\n    inner: Box[Value]\n"
               "def kind(v: Value) -> int:\n"
               "    if isinstance(v, int):\n        return 1\n"
               "    return 2\n"
               "def relay(v: Value) -> int:\n"
               "    return kind(v)\n"
               "def main() -> None:\n"
               "    print(relay(7))\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "kind(v)" in cpp


class TestMemberShadowsLocalType:
    # A record whose MEMBER shadows a same-named type: the AST emits that
    # record's decl and method defs inside `qualify_shadowed_nominals()`, so
    # every local nominal leaf renders fully-qualified. THIR pre-renders its
    # spellings at LOWERING time, so the same context must be live there --
    # and the ctor CALLEE (a raw name, not a rendered type) must follow it.
    SRC = ("from tpy import Own\n"
           "class day:\n    n: int\n"
           "    def __init__(self, n: int) -> None:\n        self.n = n\n"
           "class clock:\n    tick: int\n"
           "    def __init__(self, tick: int) -> None:\n        self.tick = tick\n"
           "    def day(self) -> Own[day]:\n        return day(self.tick)\n"
           "def main() -> None:\n"
           "    c = clock(3)\n"
           "    print(c.day().n)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "clock.day") is not None or any(
            f.name.endswith("day") for f in thir.functions)

    def test_byte_identical_qualifies_the_ctor_callee(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "return ::tpyapp::main::day(this->tick);" in cpp

    def test_non_shadowing_record_keeps_the_bare_callee(self):
        # The qualification is keyed on the ENCLOSING record shadowing; an
        # ordinary record must keep spelling the raw ctor name.
        src = ("from tpy import Own\n"
               "class day:\n    n: int\n"
               "    def __init__(self, n: int) -> None:\n        self.n = n\n"
               "class clock:\n    tick: int\n"
               "    def __init__(self, tick: int) -> None:\n        self.tick = tick\n"
               "    def make(self) -> Own[day]:\n        return day(self.tick)\n"
               "def main() -> None:\n"
               "    c = clock(3)\n"
               "    print(c.make().n)\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "return day(this->tick);" in cpp

    def test_generator_method_on_shadowing_record_routes(self):
        # Adversarial: a generator method lowers at FRAME-emission time, not
        # in the up-front pass the shadow context wraps. Both paths render it
        # outside that context, so the spellings still agree -- pinned because
        # no corpus case pairs a shadowing record with a generator.
        src = ("from typing import Iterator\n"
               "from tpy import Int32, Own\n"
               "class day:\n    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
               "class clock:\n    day: Int32\n"
               "    def __init__(self, day: Int32) -> None:\n        self.day = day\n"
               "    def make(self) -> Own[day]:\n        return day(self.day)\n"
               "    def days(self) -> Iterator[Own[day]]:\n"
               "        i = 0\n"
               "        while i < 2:\n"
               "            yield day(i)\n"
               "            i = i + 1\n"
               "def main() -> None:\n"
               "    c = clock(3)\n"
               "    print(c.make().n)\n"
               "    for d in c.days():\n        print(d.n)\n"
               "main()\n")
        _assert_byte_identical(src)


class TestTupleLiteralMethodArg:
    SRC = ("from typing import Optional\nfrom tpy import Int32\n"
           "class Box:\n    val: Int32\n"
           "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
           "class H:\n    t: tuple[Optional[Box], Box]\n"
           "    def __init__(self, a: Box, b: Box) -> None:\n"
           "        self.t = (a, b)\n"
           "    def set(self, p: tuple[Optional[Box], Box]) -> None:\n"
           "        self.t = p\n"
           "def main() -> None:\n"
           "    a = Box(1)\n    b = Box(2)\n"
           "    h = H(a, b)\n"
           "    x = Box(9)\n    y = Box(8)\n"
           "    h.set((x, y))\n"
           "main()\n")

    def test_byte_identical(self):
        # The user-record METHOD arg loop shares `_lower_call_arg`'s
        # tuple-literal arm with the free-call path; only the gate admission
        # was missing, so the borrow builder renders it unchanged.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "h.set(std::tuple<Box*, Box*>{&(x), &(y)});" in cpp


class TestNativeIterableFieldArg:
    SRC = ("class Writer:\n    _parts: list[str]\n"
           "    def __init__(self) -> None:\n        self._parts = []\n"
           "    def write(self, s: str) -> None:\n"
           "        self._parts.append(s)\n"
           "    def result(self) -> str:\n"
           "        return \",\".join(self._parts)\n"
           "def main() -> None:\n"
           "    w = Writer()\n    w.write(\"a\")\n    w.write(\"b\")\n"
           "    print(w.result())\n"
           "main()\n")

    def test_byte_identical(self):
        # A container FIELD read at a native builtin's structural Iterable
        # slot renders bare, exactly like the bare-NAME row beside it.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::str_join(\",\", this->_parts)" in cpp

    def test_plain_iterable_slot_still_defers(self):
        # The row is scoped to NATIVE builtins, whose runtime overload binds
        # the container by template. A plain-TPy `Iterable` param needs the
        # adapter conversion, which this slice does not mirror.
        src = ("from typing import Iterable\n"
               "def count(xs: Iterable[str]) -> int:\n"
               "    n = 0\n"
               "    for _x in xs:\n        n += 1\n"
               "    return n\n"
               "class W:\n    parts: list[str]\n"
               "    def __init__(self) -> None:\n        self.parts = []\n"
               "    def n(self) -> int:\n        return count(self.parts)\n"
               "def main() -> None:\n"
               "    print(W().n())\n"
               "main()\n")
        _assert_byte_identical(src)


class TestNarrowedForeachSource:
    SRC = ("from tpy import Int32\n"
           "type Tree = Int32 | list[Tree]\n"
           "def depth(t: Tree) -> Int32:\n"
           "    if isinstance(t, Int32):\n        return 0\n"
           "    m: Int32 = 0\n"
           "    for child in t:\n"
           "        d = depth(child)\n"
           "        if d > m:\n            m = d\n"
           "    return m + 1\n"
           "def main() -> None:\n"
           "    print(depth(3))\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("foreach.narrowed_proto_src", 0) >= 1

    def test_byte_identical_uses_the_protocol_loop(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        # The narrowed alias binds as an lvalue source, then the universal
        # __iter__/__next__ loop -- never the begin/end peephole.
        assert "::tpy::__iter__(__src_0)" in cpp

    def test_tuple_unpack_over_narrowed_src_still_defers(self):
        # The richer for-shapes re-dispatch on the iterable in ways the
        # narrowed bypass would skip, so they keep rejecting.
        src = ("from tpy import Int32\n"
               "type Pairs = Int32 | list[tuple[Int32, Int32]]\n"
               "def total(p: Pairs) -> Int32:\n"
               "    if isinstance(p, Int32):\n        return 0\n"
               "    s: Int32 = 0\n"
               "    for a, b in p:\n"
               "        s = s + a + b\n"
               "    return s\n"
               "def main() -> None:\n"
               "    print(total(1))\n"
               "main()\n")
        _assert_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("foreach.narrowed_proto_src", 0) == 0


class TestRecordGetitemBigIntNarrow:
    SRC = ("from tpy import Int64\n"
           "class WidePages:\n    _n: Int64\n"
           "    def __init__(self) -> None:\n        self._n = 0\n"
           "    def __getitem__(self, k: Int64) -> Int64:\n"
           "        return self._n + k\n"
           "    def __setitem__(self, k: Int64, v: Int64) -> None:\n"
           "        self._n = k + v\n"
           "def main() -> None:\n"
           "    p = WidePages()\n"
           "    k: int = 17592186044416\n"
           "    p[k] = 9\n"
           "    print(p[k])\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("narrow.subscript_index", 0) >= 1

    def test_byte_identical_narrows_the_key(self):
        # A runtime-BigInt key against a FIXED-int key param narrows on BOTH
        # the read and the write; the write target lowers through the read
        # arm, so one wrap serves both.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "k.to_fixed_check<int64_t>()" in cpp

    def test_bigint_key_param_takes_no_narrow(self):
        # A BigInt-KEYED receiver passes the index through unnarrowed -- the
        # 'bare' disposition, which must not pick up the wrap.
        src = ("class Pages:\n    _n: int\n"
               "    def __init__(self) -> None:\n        self._n = 0\n"
               "    def __getitem__(self, k: int) -> int:\n"
               "        return self._n + k\n"
               "def main() -> None:\n"
               "    p = Pages()\n"
               "    k: int = 17592186044416\n"
               "    print(p[k])\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "to_fixed_check" not in cpp


class TestMethodUnionCtorTemp:
    SRC = ("class Cat:\n    name: str\n"
           "    def __init__(self, n: str) -> None:\n        self.name = n\n"
           "class Dog:\n    name: str\n"
           "    def __init__(self, n: str) -> None:\n        self.name = n\n"
           "class Pen:\n    pet: Cat | Dog\n"
           "    def __init__(self, p: Cat | Dog) -> None:\n        self.pet = p\n"
           "    def set_pet(self, p: Cat | Dog) -> None:\n        self.pet = p\n"
           "def main() -> None:\n"
           "    p = Pen(Cat(\"a\"))\n"
           "    p.set_pet(Cat(\"Mittens\"))\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical_hoists_and_lifts(self):
        # The member ctor rvalue hoists its own temp, then the variant lifts
        # its address -- the free-call ladder's row, now admitted at a method
        # slot with the matching flush.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "p.set_pet(std::variant<Cat*, Dog*>{&__tmp_" in cpp


class TestMarkerValueOptMemberArg:
    SRC = ("import io\n"
           "def main() -> None:\n"
           "    b = io.BytesIO(b\"xyz123\")\n"
           "    print(len(b.getvalue()))\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical_renders_bare(self):
        # gen_call_arg has no value-optional arm, so the member render lands
        # bare and the optional's converting ctor absorbs it.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "BytesIO(" in cpp

    def test_optional_typed_source_still_defers(self):
        # The row is for a MEMBER-typed arg; a source already typed as the
        # whole optional is the pass-through family, not this one.
        src = ("import io\n"
               "def f(v: bytes | None) -> None:\n"
               "    b = io.BytesIO(v)\n"
               "    print(len(b.getvalue()))\n"
               "def main() -> None:\n    f(b\"z\")\nmain()\n")
        _assert_byte_identical(src)


class TestMethodValueOptPassThrough:
    SRC = ("from tpy import Int32\n"
           "class Holder:\n    n: Int32\n"
           "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
           "    def take(self, v: Int32 | None) -> Int32:\n"
           "        if v is None:\n            return self.n\n"
           "        return v\n"
           "def main() -> None:\n"
           "    h = Holder(5)\n"
           "    opt: Int32 | None = 7\n"
           "    print(h.take(opt))\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical_passes_bare(self):
        # `std::optional<T>` is a value type passed BY VALUE, so the whole
        # optional name renders bare -- no lift, no shim, no temp.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "h.take(opt)" in cpp

    def test_bytes_inner_still_defers(self):
        # Scoped to the SCALAR inner, matching the lowering row that renders
        # it; str/bytes inners have their own shim rows and would only be
        # admitted here to reject again inside lowering.
        src = ("class Sink:\n"
               "    def put(self, b: bytes | None) -> int:\n"
               "        if b is None:\n            return 0\n"
               "        return len(b)\n"
               "def relay(s: Sink, b: bytes | None) -> int:\n"
               "    return s.put(b)\n"
               "def main() -> None:\n"
               "    print(relay(Sink(), b\"xy\"))\nmain()\n")
        _assert_byte_identical(src)


class TestMethodValueRecordRvalueArg:
    def test_byte_identical(self):
        # A ValueType record param is no ref slot, so a record rvalue binds
        # bare on both paths -- exactly as at a ctor slot.
        src = ("from datetime import datetime, timezone, timedelta\n"
               "def main() -> None:\n"
               "    tz = timezone(timedelta(hours=1))\n"
               "    d = datetime(2023, 10, 29, 0, 30, tzinfo=tz)\n"
               "    print(d.year)\n"
               "main()\n")
        _assert_byte_identical(src)


class TestUnitTypeContainers:
    SRC = ("def main() -> None:\n"
           "    xs: list[None] = []\n"
           "    xs.append(None)\n"
           "    print(\"list len:\", len(xs))\n"
           "    d: dict[str, None] = {}\n"
           "    d[\"a\"] = None\n"
           "    print(\"dict size:\", len(d))\n"
           "    t: tuple[None, int] = (None, 42)\n"
           "    print(\"tuple snd:\", t[1])\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("setitem.unit_none", 0) >= 1

    def test_byte_identical(self):
        # `std::monostate` is a value type: stored bare at a dict value slot
        # and read bare as a tuple element -- no storage/borrow split.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::__setitem__(d, \"a\", std::monostate{})" in cpp
        assert "std::tuple<std::monostate, ::tpy::BigInt>" in cpp

    def test_non_none_source_at_unit_slot_defers(self):
        # `None` is the only source sema admits at a unit slot; the arm
        # rejects anything else rather than guessing a render.
        src = ("def main() -> None:\n"
               "    d: dict[str, None] = {}\n"
               "    d[\"a\"] = None\n"
               "    for k in d:\n        print(k)\n"
               "main()\n")
        _assert_byte_identical(src)


class TestVarargsPrintArg:
    SRC = ("def show(*xs: int) -> None:\n"
           "    print(xs)\n"
           "def main() -> None:\n"
           "    show(1, 2, 3)\n"
           "    show(7)\n"
           "    show()\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "show") is not None

    def test_byte_identical_uses_varargs_printer(self):
        # A whole `*args` body view is a tuple in Python, so it takes
        # VarargsPrinter -- NOT a container printer.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::VarargsPrinter(xs)" in cpp
        assert "ListPrinter(xs)" not in cpp

    def test_list_arg_keeps_list_printer(self):
        # The boundary: an ordinary container name must keep its own printer.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    print(xs)\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "::tpy::ListPrinter(xs)" in cpp


class TestVarargsUnprovenSubscript:
    SRC = ("from tpy import Int32\n"
           "def first[T](*args: T) -> T:\n"
           "    return args[0]\n"
           "def main() -> None:\n"
           "    print(first(1, 2, 3))\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "first") is not None

    def test_byte_identical_uses_the_checked_read(self):
        # An UNPROVEN varargs index takes the checked helper; the emit picks
        # between that and the bounds-safe form off `bounds_safe`, exactly as
        # for a container receiver.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::__getitem__(args, 0)" in cpp

    def test_proven_index_keeps_the_bounds_safe_read(self):
        # The other side of the same emit branch must not regress.
        src = ("from tpy import Int32\n"
               "def pick(*args: Int32) -> Int32:\n"
               "    n = len(args)\n"
               "    if n > 0:\n"
               "        i = 0\n"
               "        while i < n:\n"
               "            if args[i] > 0:\n                return args[i]\n"
               "            i = i + 1\n"
               "    return 0\n"
               "def main() -> None:\n    print(pick(1, 2))\nmain()\n")
        _assert_byte_identical(src)


class TestValueRecordOptionalFieldWrite:
    SRC = ("from tpy import Int32, ValueType\n"
           "class V(ValueType):\n    x: Int32\n"
           "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
           "class W:\n    o: V | None\n"
           "    def __init__(self) -> None:\n        self.o = None\n"
           "    def put(self, v: V) -> None:\n        self.o = v\n"
           "def main() -> None:\n"
           "    w = W()\n    w.put(V(3))\n"
           "    if w.o is not None:\n        print(w.o.x)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "W.put") is not None or any(
            f.name.endswith("put") for f in thir.functions)

    def test_byte_identical_writes_bare(self):
        # Borrow and storage coincide for a value record, so no convert --
        # `optional::operator=` absorbs the bare lvalue.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "this->o = v;" in cpp

    def test_pointer_repr_inner_keeps_its_own_arms(self):
        # The pointer-repr Optional[record] field write has real convert arms
        # (the `ptr_to_optional` lift); the value-record predicate must stay
        # separate from it rather than widening it.
        src = ("from tpy import Int32\n"
               "class R:\n    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
               "class H:\n    o: R | None\n"
               "    def __init__(self) -> None:\n        self.o = None\n"
               "    def put(self, r: R) -> None:\n        self.o = r\n"
               "def main() -> None:\n"
               "    h = H()\n    h.put(R(1))\n"
               "    if h.o is not None:\n        print(h.o.x)\n"
               "main()\n")
        _assert_byte_identical(src)


class TestOptionalFieldChainWrite:
    SRC = ("from tpy import Int32, copy\n"
           "class Point:\n    x: Int32\n    y: Int32\n"
           "    def __init__(self, x: Int32, y: Int32) -> None:\n"
           "        self.x = x\n        self.y = y\n"
           "class Holder:\n    opt: Point | None\n"
           "    def __init__(self, p: Point) -> None:\n        self.opt = copy(p)\n"
           "def use(h: Holder) -> None:\n"
           "    h.opt.x = 5\n"
           "def main() -> None:\n"
           "    h = Holder(Point(1, 2))\n"
           "    use(h)\n"
           "    if h.opt is not None:\n        print(h.opt.x)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "use") is not None

    def test_byte_identical_wraps_the_optional_lvalue(self):
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::deref_optional_check(h.opt).x = 5;" in cpp

    def test_proven_write_keeps_the_plain_member_chain(self):
        # A PROVEN access carries no runtime-check marker and must keep the
        # plain chain rather than picking up the check wrap.
        src = ("from tpy import Int32, copy\n"
               "class Point:\n    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
               "class Holder:\n    p: Point\n"
               "    def __init__(self, p: Point) -> None:\n        self.p = copy(p)\n"
               "def use(h: Holder) -> None:\n    h.p.x = 5\n"
               "def main() -> None:\n"
               "    h = Holder(Point(1))\n    use(h)\n    print(h.p.x)\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "deref_optional_check" not in cpp


class TestProtocolMethodDiscard:
    _P = ("from typing import Protocol\n"
          "from tpy import Int32, Own, dynamic\n")

    def test_record_result_discard_byte_identical(self):
        # Nothing consumes a discarded result, so the call renders bare
        # whatever its type; the result-family set exists for VALUE positions.
        src = (self._P
               + "class Node:\n    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
               + "@dynamic\nclass Source(Protocol):\n"
               + "    def head(self) -> Own[Node]: ...\n"
               + "class Impl(Source):\n"
               + "    def head(self) -> Own[Node]:\n        return Node(1)\n"
               + "def run(s: Source) -> None:\n    s.head()\n"
               + "def main() -> None:\n    run(Impl())\nmain()\n")
        _assert_byte_identical(src)
        thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("method.protocol_discard", 0) >= 1

    def test_container_result_discard_byte_identical(self):
        src = (self._P
               + "@dynamic\nclass Source(Protocol):\n"
               + "    def items(self) -> Own[list[Int32]]: ...\n"
               + "class Impl(Source):\n"
               + "    def items(self) -> Own[list[Int32]]:\n        return [1, 2]\n"
               + "def run(s: Source) -> None:\n    s.items()\n"
               + "def main() -> None:\n    run(Impl())\nmain()\n")
        _assert_byte_identical(src)

    def test_value_position_record_result_still_defers(self):
        # The discard row is statement-position ONLY; a value position must
        # keep rejecting, since its render is what the family set gates.
        src = (self._P
               + "class Node:\n    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
               + "@dynamic\nclass Source(Protocol):\n"
               + "    def head(self) -> Own[Node]: ...\n"
               + "class Impl(Source):\n"
               + "    def head(self) -> Own[Node]:\n        return Node(1)\n"
               + "def run(s: Source) -> Int32:\n    h = s.head()\n    return h.n\n"
               + "def main() -> None:\n    print(run(Impl()))\nmain()\n")
        _assert_byte_identical(src)


class TestContainerCopyFieldWrite:
    SRC = ("from tpy import Int32, copy\n"
           "class Bag:\n    items: list[Int32]\n"
           "    def __init__(self) -> None:\n        self.items = []\n"
           "    def set_items(self, data: list[Int32]) -> None:\n"
           "        self.items = copy(data)\n"
           "def main() -> None:\n"
           "    b = Bag()\n"
           "    src: list[Int32] = [1, 2]\n"
           "    b.set_items(src)\n"
           "    src.append(3)\n"
           "    print(len(b.items), len(src))\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("field_write.container_copy", 0) >= 1

    def test_byte_identical_copy_constructs(self):
        # `_gen_copy_expr`'s tail spells `{type}({arg})` for a container just
        # as it does `T(x)` for a record -- and the copy is observable: the
        # source is mutated afterwards and the field must not see it.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "this->items = std::vector<int32_t>(data);" in cpp

    def test_bare_name_source_keeps_its_own_row(self):
        # `self.items = data` (no copy()) is the aliasing/copy-warning row,
        # not this one -- it must keep rendering the bare assign.
        src = ("from tpy import Int32\n"
               "class Bag:\n    items: list[Int32]\n"
               "    def __init__(self) -> None:\n        self.items = []\n"
               "    def set_items(self, data: list[Int32]) -> None:\n"
               "        self.items = data\n"
               "def main() -> None:\n"
               "    b = Bag()\n"
               "    src: list[Int32] = [1, 2]\n"
               "    b.set_items(src)\n"
               "    print(len(b.items))\n"
               "main()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "this->items = data;" in cpp


class TestStaticExplicitClassTargs:
    SRC = ("from tpy import Int32, Own\n"
           "from tpy.coro import Poll\n"
           "class Probe:\n    tag: Int32\n"
           "    def __init__(self, tag: Int32) -> None:\n        self.tag = tag\n"
           "def make_ready(tag: Int32) -> Own[Poll[Probe]]:\n"
           "    return Poll[Probe].ready(Probe(tag))\n"
           "def main() -> None:\n"
           "    p = make_ready(3)\n    print(1)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "make_ready") is not None

    def test_byte_identical(self):
        # The AST's static arm renders from `inferred_type_args` alone and
        # never reads `type_args`, so an explicit spelling that sema folded
        # into an IDENTICAL inferred list is redundant at this arm.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpystd::tpy::Poll<Probe>::ready(" in cpp

    def test_nonstatic_explicit_targs_still_defer(self):
        # The carve-out is scoped to STATIC calls: a generic METHOD call with
        # explicit type args renders a different spelling (the method-level
        # `<T>` suffix) and must keep rejecting.
        src = ("from tpy import Int32\n"
               "class Box:\n    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               "    def transform[T](self, x: T) -> T:\n        return x\n"
               "def main() -> None:\n"
               "    b = Box(1)\n"
               "    print(b.transform[Int32](42))\n"
               "main()\n")
        _assert_byte_identical(src)


class TestGenericValuePrintArg:
    SRC = ("def show[T](x: T) -> None:\n"
           "    print(x)\n"
           "def main() -> None:\n"
           "    show(True)\n"
           "    show(False)\n"
           "    show(42)\n"
           "main()\n")

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "show") is not None

    def test_byte_identical_uses_value_printer(self):
        # An open type-param dispatches formatting at runtime: a `bool` T
        # must print True/False, not the 1/0 a raw `<<` would give.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert "::tpy::ValuePrinter(x)" in cpp

    def test_concrete_bool_keeps_print_bool(self):
        # The boundary: a CONCRETE bool takes `::tpy::print_bool`, not the
        # runtime-dispatching ValuePrinter.
        src = ("def show(x: bool) -> None:\n"
               "    print(x)\n"
               "def main() -> None:\n    show(True)\nmain()\n")
        _assert_byte_identical(src)
        cpp = _cpp(src)
        assert "::tpy::print_bool(x)" in cpp
        assert "ValuePrinter" not in cpp


class TestMarkerCallMacroExpansion:
    SRC = ("import dataclasses\n"
           "from tpy import Int32\n"
           "@dataclasses.dataclass\n"
           "class Point:\n    x: Int32\n    y: Int32\n"
           "def main() -> None:\n"
           "    p = Point(Int32(1), Int32(2))\n"
           "    d = dataclasses.asdict(p)\n"
           "    print(d)\n"
           "main()\n")

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("call.macro_expansion", 0) >= 1

    def test_byte_identical_renders_the_expansion(self):
        # A `@call_macro`'s sema-synthesized replacement renders IN PLACE on
        # the AST path (gen_expr's macro arm is receiver-blind), so the
        # module-qualified call lowers to its expansion exactly as the
        # free-call ladder already does.
        _assert_byte_identical(self.SRC)
        cpp = _cpp(self.SRC)
        assert '::tpy::ordered_map<std::string, int32_t>({{"x", p.x}, {"y", p.y}})' in cpp

    def test_astuple_expansion_routes(self):
        # `astuple`'s expansion is a bare TUPLE LITERAL reaching `_lower_expr`
        # with no slot threaded from the position, so it spells its own sema
        # type (`std::tuple<int32_t, int32_t>{p.x, p.y}`) -- the AST's render.
        src = ("import dataclasses\n"
               "from tpy import Int32\n"
               "@dataclasses.dataclass\n"
               "class Point:\n    x: Int32\n    y: Int32\n"
               "def main() -> None:\n"
               "    p = Point(Int32(1), Int32(2))\n"
               "    t = dataclasses.astuple(p)\n"
               "    print(t)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["expr.value_tuple_self_typed"] >= 1
        assert ("std::tuple<int32_t, int32_t>{p.x, p.y}") in _cpp(src)
        _assert_byte_identical(src)

    def test_nonvalue_element_expansion_still_defers(self):
        # The boundary: only a VALUE tuple can be spelled from its own type.
        # A reference-typed field makes the expansion a pointer-repr tuple,
        # whose borrow-vs-storage form the position decides -- so the
        # self-typed arm must not claim it.
        src = ("import dataclasses\n"
               "from tpy import Int32\n"
               "class Rec:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "@dataclasses.dataclass\n"
               "class Holder:\n    r: Rec\n    k: Int32\n"
               "def main() -> None:\n"
               "    h = Holder(Rec(5), 6)\n"
               "    u = dataclasses.astuple(h)\n"
               "    print(u[1])\n"
               "main()\n")
        # Assert the FALLBACK, not just byte-identity: a fallback body
        # emits byte-identical AST by construction, so identity alone would
        # pass even if this shape started routing through some other arm.
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert faces.get("expr.value_tuple_self_typed", 0) == 0
        _assert_byte_identical(src)


class TestTupleReturnCallSources:
    """Call sources at a value-tuple return slot pass through bare -- the
    module-qualified stub (`return math.frexp(2.0)`) and the user-method
    flavor alike; the generic tail's call gates own the result rows."""

    def test_module_stub_tuple_return_routes(self):
        src = ("import math\n"
               "def wrap() -> tuple[float, int]:\n"
               "    return math.frexp(2.0)\n"
               "def main() -> None:\n"
               "    m, e = wrap()\n"
               "    print(e)\n"
               "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "wrap") is not None
        assert w.get("ret.tuple_call", 0) >= 1
        _assert_byte_identical(src)

    def test_user_method_tuple_return_routes(self):
        src = ("from tpy import Int32\n"
               "class M:\n"
               "    def pair(self) -> tuple[Int32, Int32]:\n"
               "        return (1, 2)\n"
               "def f(m: M) -> tuple[Int32, Int32]:\n"
               "    return m.pair()\n"
               "def main() -> None:\n"
               "    m = M()\n"
               "    a, b = f(m)\n"
               "    print(a + b)\n"
               "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("ret.tuple_call", 0) >= 1
        _assert_byte_identical(src)

    def test_ternary_tuple_source_defers(self):
        # BOUNDARY: the source gate admits literal/name/call shapes only --
        # a TERNARY tuple source stays out and the body falls back
        # byte-identically.
        src = ("from tpy import Int32\n"
               "def f(c: bool) -> tuple[Int32, Int32]:\n"
               "    a = (1, 2)\n"
               "    b = (3, 4)\n"
               "    return a if c else b\n"
               "def main() -> None:\n"
               "    x, y = f(True)\n"
               "    print(x + y)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestUnpackCtorArgTempCrash:
    def test_bugs_repro_validator_escapes_fallback(self):
        # BUGS.md repro (the tuple-unpack ctor-arg crash): the gate admits
        # a temp-needing ctor arg at the non-flushable unpack source, and
        # the validator raises OUT of the per-body fallback boundary. This
        # pin documents the CURRENT broken behavior -- the fix flips it to
        # a clean fallback (delete the raises-assert then).
        import pytest
        from .validate import THIRValidationError
        src = ("from tpy import Int32\n"
               "class M:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "    def pair(self) -> tuple[Int32, Int32]:\n"
               "        return (1, 2)\n"
               "def f(m: M) -> tuple[Int32, Int32]:\n"
               "    return m.pair()\n"
               "def main() -> None:\n"
               "    a, b = f(M())\n"
               "    print(a + b)\n"
               "main()\n")
        with pytest.raises(THIRValidationError):
            _lower_ctx(src)

"""The asdict/astuple expansion track: container-ctor calls at VALUE
sinks, `dict({...})` instantiation, field reads at union element slots,
str-field elements in tuple/dict literals, the nested-dict/comp union
members, the nested storage-decl families, and the tuple-keyed dict
family -- each row pinned on a routable fixture with its rejecting
boundary."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _lower_ctx_witnessed, _fn, _assert_routes_byte_identical,
    _compile, _entry,
)


def _assert_identical(src: str) -> 'tuple[dict, dict]':
    """Byte-compare THIR vs AST output; return (witnesses, fallback)."""
    compiler, modules = _compile(src)
    a_h, a_c = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=True,
                                                thir_codegen=False))
    c2, modules2 = _compile(src)
    t_h, t_c = c2.generate_code_to_strings(
        _entry(modules2), options=CodeGenOptions(emit_source_comments=True,
                                                 thir_codegen=True))
    assert (a_h, a_c) == (t_h, t_c)
    return c2._thir_face_witnesses, c2._thir_fallback


class TestContainerCtorValueSink:
    """`print(asdict(p))` on a MIXED-field dataclass -- the expansion's
    `dict({...})` ctor at the print VALUE sink renders the bare prvalue
    under the printer wrap (the shape is macro-synthesized; user-written
    `dict({...})` is a sema error, so the macro IS the fixture; the flat
    all-scalar flavor expands to a bare literal and never mints the
    ctor)."""
    _SRC = (
        "from dataclasses import dataclass, asdict\n"
        "from tpy import Int32\n"
        "@dataclass\n"
        "class P:\n"
        "    name: str\n"
        "    age: Int32\n"
        "def main() -> None:\n"
        "    p = P(\"x\", 3)\n"
        "    print(asdict(p))\n"
        "main()\n")

    def test_asdict_at_print_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert w.get("call.container_ctor_value", 0) >= 1
        assert w.get("call.dict_literal_instantiation", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "std::variant<std::string, int32_t>" in cpp


class TestUnionFieldElem:
    """A scalar/owned-str FIELD read at a union dict-value slot renders
    bare (`{"n": p.name, "a": p.age}` -- the converting ctor picks the
    member)."""
    _SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    name: str\n"
        "    age: Int32\n"
        "    def __init__(self, name: str, age: Int32) -> None:\n"
        "        self.name = name\n        self.age = age\n"
        "def pack(p: P) -> None:\n"
        "    d: dict[str, str | Int32] = {\"n\": p.name, \"a\": p.age}\n"
        "    print(len(d))\n"
        "def main() -> None:\n"
        "    pack(P(\"x\", 3))\n"
        "main()\n")

    def test_union_field_values_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "pack") is not None
        assert w.get("containerlit.union_field_elem", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "p.name" in cpp and "p.age" in cpp


class TestTupleLiteralStrField:
    """A str FIELD element in a value-tuple literal renders the bare
    member (`(p.name, p.age)` -- the astuple expansion's shape)."""
    _SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    name: str\n"
        "    age: Int32\n"
        "    def __init__(self, name: str, age: Int32) -> None:\n"
        "        self.name = name\n        self.age = age\n"
        "def pack(p: P) -> tuple[str, Int32]:\n"
        "    return (p.name, p.age)\n"
        "def main() -> None:\n"
        "    t = pack(P(\"x\", 3))\n"
        "    print(t[0], t[1])\n"
        "main()\n")

    def test_tuple_str_field_elem_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "pack") is not None
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "{p.name, p.age}" in cpp


class TestStorageDeclNestedFamilies:
    """`d = asdict(line)` / `t = astuple(line)` on a nested dataclass:
    the storage decl admits a dict-of-scalar-read-dict result and a
    nested value-tuple result (element-blind bare rvalue copies), and
    the self-typed tuple literal spells the nested tuple type."""
    _SRC = (
        "from dataclasses import dataclass, asdict, astuple\n"
        "from tpy import Int32\n"
        "@dataclass\n"
        "class Pt:\n"
        "    x: Int32\n"
        "    y: Int32\n"
        "@dataclass\n"
        "class Line:\n"
        "    start: Pt\n"
        "    end: Pt\n"
        "def main() -> None:\n"
        "    line = Line(Pt(1, 2), Pt(3, 4))\n"
        "    d = asdict(line)\n"
        "    print(d)\n"
        "    t = astuple(line)\n"
        "    print(t)\n"
        "main()\n")

    def test_nested_decl_results_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert w.get("decl.storage_call", 0) >= 1
        assert w.get("expr.value_tuple_self_typed", 0) >= 1
        _assert_routes_byte_identical(self._SRC)

    # No record-valued boundary pin: probed fixtures (Own-returning call,
    # name init) all route byte-identically through SIBLING arms (the
    # owned-container and borrow-cascade decls), so the widening's
    # non-scalar-read exclusion has no reachable rejecting shape at this
    # sink -- the sibling arms own those shapes.


class TestCompDictElement:
    """A dict-literal element inside a comp at a PLAIN dict element slot
    (`[{"x": m.x} for m in ms]` -- the asdict list recursion): the
    self-describing ordered_map render pushed bare. The union-slot comp
    flavor is TestCompAtUnionSlot's."""
    _SRC = (
        "from tpy import Int32\n"
        "class M:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def pack(ms: list[M]) -> None:\n"
        "    rows: list[dict[str, Int32]] = [{\"x\": m.x} for m in ms]\n"
        "    print(len(rows))\n"
        "def main() -> None:\n"
        "    pack([M(1), M(2)])\n"
        "main()\n")

    def test_dict_elem_comp_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "pack") is not None
        assert w.get("comp.container_value", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert 'push_back(::tpy::ordered_map<std::string, int32_t>({{"x", '\
               'm.x}}));' in cpp

    def test_dict_name_elem_stays_ast(self):
        # Only literal/comp elements route at a dict element slot: a bare
        # dict-typed NAME element keeps raising comp.container_value.
        src = (
            "from tpy import Int32\n"
            "def pack(ms: list[Int32], d0: dict[str, Int32]) -> None:\n"
            "    rows: list[dict[str, Int32]] = [d0 for m in ms]\n"
            "    print(len(rows))\n"
            "def main() -> None:\n"
            "    pack([1], {\"k\": 2})\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback


class TestOptTupleYield:
    """Pointer-repr Optional-element tuple literals at yield slots:
    a container-element lvalue lifts (`{&(items[...]), nullptr}`), a
    pointee-typed pointer local passes bare (`{prev, it}`), and the
    frame pointer reseats render slot-free (`prev = nullptr;` /
    `prev = it;`)."""
    _SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def pairs(items: list[P]) -> Iterator[tuple[P | None, P | None]]:\n"
        "    prev: P | None = None\n"
        "    for it in items:\n"
        "        yield (prev, it)\n"
        "        if it.x == 0:\n"
        "            prev = None\n"
        "        else:\n"
        "            prev = it\n"
        "    yield (prev, None)\n"
        "def main() -> None:\n"
        "    for a, b in pairs([P(1), P(0), P(2)]):\n"
        "        print(a is None, b is None)\n"
        "main()\n")

    def test_opt_tuple_yield_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert w.get("btuple.elem_optptr", 0) >= 1

    def test_subscript_elem_yield_routes(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
            "def firsts(items: list[P]) -> "
            "Iterator[tuple[P | None, P | None]]:\n"
            "    i = 0\n"
            "    while i < len(items):\n"
            "        yield (items[i], None)\n"
            "        i += 1\n"
            "def main() -> None:\n"
            "    for a, b in firsts([P(1)]):\n"
            "        print(a is None, b is None)\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback
        assert w.get("btuple.elem_optptr", 0) >= 2


class TestFieldWriteReceiverRows:
    """Field-write receiver rows: a scalar write through a
    borrow-returning PROPERTY getter (`h.val.x = 8` -> `h.val().x = 8;`),
    a Callable field written from a hoisted-lambda NAME
    (`self.callback = add_offset;`), and a str field write over a
    record-getitem element with a literal index (`ps[0].name = "x";`).
    The UNPROVEN Optional-ptr alias boundary has no fixture: sema types
    an un-narrowed Optional source at the whole-Optional binding, so the
    alias-to-inner flavor exists only under a proof (the sema-implied
    -proof rule) -- the deref-check marker guard in
    _bare_nonvalue_name_alias_ok is defensive."""

    def test_property_recv_scalar_write_routes(self):
        src = (
            "from tpy import Int32\n"
            "class V:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
            "class H:\n"
            "    _v: V\n"
            "    def __init__(self, v: V) -> None:\n        self._v = v\n"
            "    @property\n"
            "    def val(self) -> V:\n        return self._v\n"
            "def main() -> None:\n"
            "    h = H(V(1))\n"
            "    h.val.x = 8\n"
            "    print(h._v.x)\n"
            "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "h.val().x = 8;" in cpp

    def test_callable_field_lambda_name_routes(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "class Adder:\n"
            "    callback: Callable[[Int32], Int32]\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        def add_offset(x: Int32) -> Int32:\n"
            "            return x + n\n"
            "        self.callback = add_offset\n"
            "def main() -> None:\n"
            "    a = Adder(10)\n"
            "    print(a.callback(5))\n"
            "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "this->callback = add_offset;" in (_hpp + cpp)

    def test_getitem_elem_str_write_routes(self):
        src = (
            "from tpy import Int32, Own\n"
            "from tplib import ArrayList\n"
            "class W:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "def main() -> None:\n"
            "    ws = ArrayList[W, 4]()\n"
            "    ws.append(W(\"alpha\"))\n"
            "    ws[0].name = \"shifted\"\n"
            "    print(ws[0].name)\n"
            "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert 'ws[0].name = "shifted";' in cpp


class TestCoroHandleMove:
    """A NAME-source write into a concrete-coro handle slot (`d = c`)
    renders the two-line pair `d.emplace(std::move(*c)); c.reset();`
    (THIRCoroHandleMove -- optional's move-assign is deleted when the
    frame holds reference members)."""
    _SRC = (
        "import asyncio\n"
        "from tpy import Int32\n"
        "class Counter:\n"
        "    base: Int32\n"
        "    def __init__(self, base: Int32) -> None:\n"
        "        self.base = base\n"
        "    async def bump(self, n: Int32) -> Int32:\n"
        "        return self.base + n\n"
        "async def go() -> None:\n"
        "    w = Counter(40)\n"
        "    c = w.bump(5)\n"
        "    d = c\n"
        "    print(await d)\n"
        "def main() -> None:\n"
        "    asyncio.run(go())\n"
        "main()\n")

    # No self-write pin: `c = c` on a handle slot is a SEMA ERROR
    # ("cannot copy non-copyable") -- the THIRNoOpStmt divert mirrors the
    # AST's defensive "" arm for a shape sema never admits. No
    # ternary-source boundary pin either: `a if flag else b` over handles
    # is a sema error too ("cannot bind as borrowed coroutine ref"), so
    # the res.coro_handle_source reject guards only sema-unreachable
    # residue -- both probed 2026-08-06.

    def test_handle_move_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("res.coro_handle_move", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "d.emplace(std::move(*c));" in (_h + cpp)
        assert "c.reset();" in (_h + cpp)


class TestPtrUnionDeclSources:
    """New ptr-variant union decl sources: a SELF-rooted field chain
    (`p = self.inner.pet`, const rides the method's readonly-ness) and a
    union container-element read off a bare-NAME receiver
    (`pet = pets["rex"]`, const rides the receiver binding). The
    Own[container] free-arg literal leg is pinned separately in
    TestOwnContainerLiteralFreeArg."""
    _SRC = (
        "from tpy import Int32, readonly\n"
        "class Dog:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "class Cat:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "class Inner:\n"
        "    pet: Dog | Cat\n"
        "    def __init__(self, pet: Dog | Cat) -> None:\n"
        "        self.pet = pet\n"
        "class Holder:\n"
        "    inner: Inner\n"
        "    def __init__(self, inner: Inner) -> None:\n"
        "        self.inner = inner\n"
        "    @readonly\n"
        "    def get_pet_name(self) -> str:\n"
        "        p = self.inner.pet\n"
        "        if isinstance(p, Dog):\n"
        "            return p.name\n"
        "        return \"cat\"\n"
        "    def poke_pet(self) -> str:\n"
        "        p = self.inner.pet\n"
        "        if isinstance(p, Cat):\n"
        "            return p.name\n"
        "        return \"dog\"\n"
        "def pick(pets: dict[str, Dog | Cat]) -> None:\n"
        "    pet = pets[\"rex\"]\n"
        "    if isinstance(pet, Dog):\n"
        "        print(pet.name)\n"
        "def main() -> None:\n"
        "    h = Holder(Inner(Dog(\"Rex\")))\n"
        "    print(h.get_pet_name())\n"
        "    print(h.poke_pet())\n"
        "    pets: dict[str, Dog | Cat] = {\"rex\": Dog(\"Rex\")}\n"
        "    pick(pets)\n"
        "main()\n")

    def test_union_decl_sources_route(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("subscript.value_union_elem", 0) >= 1
        assert not fallback, fallback
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        both = _h + cpp
        assert "to_const_ptr_variant(this->inner.pet)" in both
        # The non-readonly sibling method takes the mutable lift.
        assert "to_ptr_variant(this->inner.pet)" in both
        # The unmutated dict param is `const T&`, so its element lift is
        # the const flavor too.
        assert "to_const_ptr_variant(::tpy::__getitem__(pets" in both


class TestOwnContainerLiteralFreeArg:
    """A container LITERAL at an `Own[container]` FREE-call slot renders
    inline spelled (`consume({"a": 1})` ->
    `consume(::tpy::ordered_map<...>({{...}}))`); the same literal at a
    plain borrow param routes through the ordinary literal-arg arms."""

    def test_own_dict_literal_free_arg_routes(self):
        src = ("from tpy import Int32, Own\n"
               "def consume(d: Own[dict[str, Int32]]) -> None:\n"
               "    print(\"got\")\n"
               "def main() -> None:\n"
               "    consume({\"a\": 1})\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert ("consume(::tpy::ordered_map<std::string, int32_t>"
                "({{\"a\", 1}}));" in cpp)

    def test_borrow_dict_literal_free_arg_routes(self):
        # The non-Own boundary: a literal at a plain borrow param does
        # not need the Own leg -- it routes via the ordinary literal-arg
        # arms (probed, not assumed; this documents that the leg's gate
        # is not what admits it).
        src = ("from tpy import Int32\n"
               "def read(d: dict[str, Int32]) -> None:\n"
               "    print(len(d))\n"
               "def main() -> None:\n"
               "    read({\"a\": 1})\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback


class TestSliceObjectFieldRead:
    """A slice-object NAME receiver's member read (`index.start` in the
    split slice-overload body) renders bare; the value-opt result rides
    the whole-optional decl threading."""
    _SRC = (
        "from typing import overload\n"
        "from tpy import Int32, Span, readonly\n"
        "class Buf:\n"
        "    _data: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._data = [1, 2, 3, 4]\n"
        "    @overload\n"
        "    def __getitem__(self, index: Int32) -> Int32: ...\n"
        "    @overload\n"
        "    def __getitem__(self, index: slice) -> "
        "Span[readonly[Int32]]: ...\n"
        "    def __getitem__(self, index: Int32 | slice) -> "
        "Int32 | Span[readonly[Int32]]:\n"
        "        if isinstance(index, slice):\n"
        "            s_start = index.start\n"
        "            start: Int32 = s_start if s_start is not None else 0\n"
        "            s_stop = index.stop\n"
        "            stop: Int32 = (s_stop if s_stop is not None\n"
        "                           else Int32(len(self._data)))\n"
        "            return self._data[start:stop]\n"
        "        else:\n"
        "            return self._data[index]\n"
        "def main() -> None:\n"
        "    b = Buf()\n"
        "    print(b[Int32(1)])\n"
        "    for x in b[Int32(1):Int32(3)]:\n"
        "        print(x)\n"
        "main()\n")

    def test_slice_field_read_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("field.slice_recv", 0) >= 1
        assert not fallback, fallback


class TestLiteralTupleOrdering:
    """Literal value-tuple ordering compares (`(1, 0) < (1, 1)`): the
    operand types carry IntLiteral elements pre-resolution; the tuple
    compare pair resolves them like the subscript/needle rows and the
    bare `(l < r)` render fires."""
    _SRC = (
        "def main() -> None:\n"
        "    a = (1, 0)\n"
        "    b = (1, 1)\n"
        "    if a < b:\n"
        "        print(\"lt\")\n"
        "    print((1, 0) < (1, 1))\n"
        "main()\n")

    def test_literal_tuple_ordering_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert not fallback, fallback


class TestErBindHoistedOptional:
    """An @error_return bind whose target is a TRY-HOISTED optional local
    (`std::optional<Point> p;` predecled; reads deref) takes the bind
    arm's default render verbatim (`p = ::tpy::unwrap_ref_move(
    *__try_tmp_N);` -- no decl)."""
    _SRC = (
        "from typing import Self\n"
        "from tpy import Int32, Own, ReturnException, error_return\n"
        "class Invalid(Exception, ReturnException):\n"
        "    pass\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "    @error_return(Invalid)\n"
        "    @classmethod\n"
        "    def parse(cls, x: Int32) -> Own[Self]:\n"
        "        if x < 0:\n"
        "            raise Invalid\n"
        "        return cls(x)\n"
        "def main() -> None:\n"
        "    try:\n"
        "        p = Point.parse(3)\n"
        "        print(p.x)\n"
        "    except Invalid:\n"
        "        print(\"bad\")\n"
        "main()\n")

    def test_branch_hoisted_bind_stays_ast(self):
        # An if-cascade-hoisted bind target (the OTHER optional_locals
        # flavor) falls back safely at its OWN gate (try.hoist) before
        # the er-bind arm -- probed byte-identical, no mis-route.
        src = self._SRC.replace(
            "    try:\n"
            "        p = Point.parse(3)\n",
            "    try:\n"
            "        if True:\n"
            "            p = Point.parse(3)\n"
            "        else:\n"
            "            p = Point.parse(5)\n")
        w, fallback = _assert_identical(src)
        assert fallback, fallback

    def test_hoisted_optional_bind_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert not fallback, fallback
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "std::optional<Point> p;" in cpp
        assert "p = ::tpy::unwrap_ref_move(*__try_tmp_" in cpp


class TestPointeePtrReseat:
    """A pointee-typed pointer LOCAL as an Optional-ptr reseat source
    (`saved = p;` -- the escape-hoisted `Point* p` is already the raw
    T*). The same-Optional GLOBAL flavor (`q = g;`) stays blocked on the
    pointer-slot-global consumer guard (TODO.md)."""
    _SRC = (
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def f() -> None:\n"
        "    saved: Point | None = None\n"
        "    for i in range(3):\n"
        "        p: Point = Point(i)\n"
        "        saved = p\n"
        "    if saved is not None:\n"
        "        print(saved.x)\n"
        "def main() -> None:\n"
        "    f()\n"
        "main()\n")

    def test_pointee_ptr_reseat_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert w.get("reseat.opt_ptr_copy", 0) >= 1
        assert not fallback


class TestOwnValueParamRead:
    """An `Own[Int32]` PARAM's reads render the bare name (Own on a
    value type is the no-op spelling; the param is by value). LOCALS
    with Own-typed bindings stay out: the AST registers an Own-typed
    loop var movable, whose harmless `std::move(x)` last-use render
    this slice does not mirror (heapq_merge is the corpus witness)."""
    _SRC = (
        "from tpy import Int32, Own\n"
        "def use_int(x: Own[Int32]) -> Int32:\n"
        "    return x\n"
        "def main() -> None:\n"
        "    print(use_int(7))\n"
        "main()\n")

    def test_own_value_param_read_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use_int") is not None
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "int32_t use_int(int32_t x)" in (_hpp + cpp)

    def test_own_value_loop_var_routes_with_move(self):
        # Converted fence: the Own[scalar] loop-var movable seed now mirrors
        # the AST's harmless `std::move(x)` last-use render (the for-each
        # seed's Own-elem-type half + the movable_local read leg), so the
        # heapq.merge loop-var flavor routes byte-identically.
        src = (
            "import heapq\n"
            "from tpy import Int32\n"
            "def f(a: list[Int32], b: list[Int32]) -> None:\n"
            "    for x in heapq.merge(a, b):\n"
            "        r: list[Int32] = []\n"
            "        r.append(x)\n"
            "        print(len(r))\n"
            "def main() -> None:\n"
            "    f([1, 3], [2, 4])\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("move.own_last_use", 0) >= 1


class TestFrameSlotDel:
    """`del t` on a resumable frame-slot local renders the same
    position-blind bare member move-sink the sync arm emits
    (`{ auto __del_sink = std::move(t); }`)."""
    _SRC = (
        "import asyncio\n"
        "from tpy import Int32\n"
        "async def work(n: Int32) -> Int32:\n"
        "    await asyncio.sleep(0.001)\n"
        "    return n\n"
        "async def go() -> None:\n"
        "    t = asyncio.create_task(work(1))\n"
        "    del t\n"
        "    await asyncio.sleep(0.01)\n"
        "def main() -> None:\n    asyncio.run(go())\nmain()\n")

    def test_frame_slot_del_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_narrowed_param_del_routes_via_skip(self):
        # A narrowed PARAM del is INTERIOR to the skip ladder: a param is
        # never the sole owner, so BOTH paths emit nothing and the shape
        # routes -- probed; the narrowed exclusion guards owned-local
        # flavors the ladder does not skip.
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
            "def f(b: Box | None) -> None:\n"
            "    if b is not None:\n"
            "        del b\n"
            "def main() -> None:\n"
            "    f(Box(1))\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback


class TestPtrDeclAdjacentShapes:
    """The shapes ADJACENT to the wave's pointer rows route via SIBLING
    families (probed, not assumed): a VALUE-returning call at a
    reassigned record local was never binding POINTER -- it rides the
    REBIND_SLOT machinery; an inline-lambda Callable write renders
    itself through the lambda arms. Both byte-identical -- these pins
    document that the adjacent shapes are interior, not rejects."""

    def test_value_returning_call_reassigned_routes(self):
        src = (
            "from tpy import Int32, Own\n"
            "class Node:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32) -> None:\n"
            "        self.value = value\n"
            "def mint(v: Int32) -> Own[Node]:\n"
            "    return Node(v)\n"
            "def use() -> Int32:\n"
            "    cur = mint(1)\n"
            "    cur = mint(2)\n"
            "    return cur.value\n"
            "def main() -> None:\n"
            "    print(use())\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback

    def test_callable_field_lambda_literal_routes(self):
        src = (
            "from typing import Callable\n"
            "from tpy import Int32\n"
            "class C:\n"
            "    cb: Callable[[Int32], Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.cb = lambda x: x + 1\n"
            "def main() -> None:\n"
            "    c = C()\n"
            "    print(c.cb(4))\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback


class TestOptPtrAliasSource:
    """A REF_ALIAS whose source is a null-tested Optional-ptr local
    (`v = d` inside `if d is not None:` where d came from an
    optional_to_ptr lift): the bare deref render (`Seconds& v = (*d);`).
    The witness fixture mirrors the JSON model macro's shape."""
    _SRC = (
        "from tpy import Int32\n"
        "class Seconds:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Sched:\n"
        "    deadline: Seconds | None\n"
        "    def __init__(self, d: Seconds | None) -> None:\n"
        "        self.deadline = d\n"
        "def show(sc: Sched) -> None:\n"
        "    d = sc.deadline\n"
        "    if d is not None:\n"
        "        v = d\n"
        "        print(v.n)\n"
        "def main() -> None:\n"
        "    show(Sched(Seconds(5)))\n"
        "    show(Sched(None))\n"
        "main()\n")

    def test_proven_opt_ptr_alias_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "show") is not None
        assert w.get("decl.alias_opt_ptr_deref_src", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "Seconds& v = (*d);" in cpp


class TestPtrCallAddrDecl:
    """A borrow-returning record call bound to a REASSIGNED local:
    `Point* first = &(get_first(data));` with the rvalue reseat riding
    the rebind-slot machinery (`first = &*(__slot_1 = Point(9, 9));`,
    hoisted `std::optional<Point> __slot_1;`)."""
    _SRC = (
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def get_first(items: list[Point]) -> Point:\n"
        "    return items[0]\n"
        "def go() -> None:\n"
        "    data = [Point(1), Point(2)]\n"
        "    first = get_first(data)\n"
        "    first = Point(9)\n"
        "    print(first.x)\n"
        "def main() -> None:\n"
        "    go()\n"
        "main()\n")

    def test_reassigned_borrow_call_decl_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "go") is not None
        assert w.get("decl.ptr_call_addr", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "Point* first = &(get_first(data));" in cpp
        assert "first = &*(__slot_1 = Point(9));" in cpp
        assert "std::optional<Point> __slot_1;" in cpp


class TestTupleKeyedDict:
    """Tuple-keyed dict/set: the value-tuple key family (spelled
    `std::tuple<...>{...}` literal keys), the tuple needle in
    membership (`(1, 2) in d` / `k1 in d` -> `contains(...)`), and the
    container-element tuple chain read (`pairs[0][0]`)."""
    _SRC = (
        "from tpy import Int32\n"
        "def main() -> None:\n"
        "    d: dict[tuple[Int32, Int32], str] = {(1, 2): \"a\"}\n"
        "    k1: tuple[Int32, Int32] = (1, 2)\n"
        "    print(k1 in d)\n"
        "    print((1, 2) in d, (9, 9) in d)\n"
        "    pairs = [(1, 2), (Int32(3), 4)]\n"
        "    print(pairs[0][0], pairs[1][0])\n"
        "main()\n")

    def test_tuple_key_shapes_route(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert w.get("binop.contains_tuple_needle", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "std::tuple<int32_t, int32_t>{1, 2}" in cpp

    def test_nested_tuple_key_stays_ast(self):
        # The key family is the FLAT value-tuple only: a nested-tuple key
        # (and its membership needle) keep rejecting.
        src = (
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    d: dict[tuple[tuple[Int32, Int32], Int32], str] = "
            "{((1, 2), 3): \"a\"}\n"
            "    print(((1, 2), 3) in d)\n"
            "def main() -> None:\n"
            "    f()\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback


class TestCompAtUnionSlot:
    """A list comp at a union value slot with a unique list member
    (`{"rows": [{"x": m.x} for m in ms]}` on `dict[str, str |
    list[dict[str, Int32]]]` -- the asdict Group recursion): the comp
    lowers against the member; two list members is ambiguous and
    keeps rejecting."""
    _SRC = (
        "from tpy import Int32\n"
        "class M:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def pack(ms: list[M]) -> None:\n"
        "    d: dict[str, str | list[dict[str, Int32]]] = {\n"
        "        \"name\": \"g\",\n"
        "        \"rows\": [{\"x\": m.x} for m in ms],\n"
        "    }\n"
        "    print(len(d))\n"
        "def main() -> None:\n"
        "    pack([M(1)])\n"
        "main()\n")

    def test_comp_at_union_slot_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "pack") is not None
        assert w.get("comp.union_member_source", 0) >= 1
        _assert_routes_byte_identical(self._SRC)

    def test_two_list_members_stays_ast(self):
        src = (
            "from tpy import Int32, Int64\n"
            "def build(xs: list[Int32]) -> None:\n"
            "    d: dict[str, list[Int32] | list[Int64]] = {\n"
            "        \"a\": [v for v in xs],\n"
            "    }\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    build([1])\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback


class TestUnionDictMemberLiteral:
    """A nested dict literal at a union value slot with a unique dict
    member lowers against the member (`{"pos": {"x": 1}}` on
    `dict[str, str | dict[str, Int32]]`)."""
    _SRC = (
        "from tpy import Int32\n"
        "def build() -> None:\n"
        "    d: dict[str, str | dict[str, Int32]] = {\n"
        "        \"name\": \"origin\",\n"
        "        \"pos\": {\"x\": 0, \"y\": 0},\n"
        "    }\n"
        "    print(len(d))\n"
        "def main() -> None:\n"
        "    build()\n"
        "main()\n")

    def test_nested_dict_member_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "build") is not None
        assert w.get("containerlit.union_member_prefix", 0) >= 1
        _assert_routes_byte_identical(self._SRC)

    def test_two_dict_members_stays_ast(self):
        # An AMBIGUOUS union (two dict members) has no unique member to
        # lower against -- keeps rejecting.
        src = (
            "from tpy import Int32, Int64\n"
            "def build() -> None:\n"
            "    d: dict[str, dict[str, Int32] | dict[str, Int64]] = {\n"
            "        \"a\": {\"x\": 0},\n"
            "    }\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    build()\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback

class TestOwnScalarCoerceCast:
    """A REAL scalar-cast coerce over a local name at a plain `Own[scalar]`
    slot binds the cast rvalue bare (`take((big).to_fixed_check<..>())` --
    the AST's needs_copy=False flip), and `copy(big)` of a scalar name
    renders the type-blind general tail (`::tpy::BigInt(big)`)."""

    def test_coerce_cast_and_copy_scalar_route(self):
        src = ("from tpy import Int32, Own, copy\n"
               "def ret_owned() -> Own[Int32]:\n"
               "    big: int = 42\n"
               "    return copy(big)\n"
               "def take(x: Own[Int32]) -> Int32:\n"
               "    return x\n"
               "def main() -> None:\n"
               "    print(ret_owned())\n"
               "    big: int = 100\n"
               "    take(big)\n"
               "    print(take(big))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("call.own_coerce_cast", 0) >= 1
        assert w.get("call.copy_scalar", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "take((big).to_fixed_check<int32_t>())" in cpp
        assert "(::tpy::BigInt(big)).to_fixed_check<int32_t>()" in cpp

    def test_field_and_global_source_boundaries(self):
        # A coerce over a FIELD access is not a plain-name cast and copy()
        # of a field source is outside the NAME-only copy arm -- both fall
        # back byte-identically. A scalar GLOBAL routes: value-scalar
        # globals are not pointer slots, so the bare read renders `(g)`
        # under the wrap on both paths (probed).
        src = ("from tpy import Int32, Own, copy\n"
               "class H:\n"
               "    big: int\n"
               "    def __init__(self) -> None:\n"
               "        self.big = 7\n"
               "g: int = 55\n"
               "def take(x: Own[Int32]) -> Int32:\n"
               "    return x\n"
               "def field_source(h: H) -> None:\n"
               "    print(take(h.big))\n"
               "def global_source() -> None:\n"
               "    print(take(g))\n"
               "def copy_of_field(h: H) -> None:\n"
               "    b = copy(h.big)\n"
               "    print(b)\n"
               "def main() -> None:\n"
               "    field_source(H())\n"
               "    global_source()\n"
               "    copy_of_field(H())\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        # Exactly the two field-source bodies fall back; global_source
        # (and main) route.
        assert sum(fallback.values()) == 2, fallback

    def test_narrowed_optional_source_stays_ast(self):
        # BOUNDARY (probed divergent before the declared-scalar guard):
        # a coerce over a NARROWED `int | None` name -- the AST drops the
        # narrowing deref there (BUGS.md), so THIR must keep rejecting.
        src = ("from tpy import Int32, Own\n"
               "def take(x: Own[Int32]) -> Int32:\n"
               "    return x\n"
               "def f(v: int | None) -> None:\n"
               "    if v is not None:\n"
               "        print(take(v))\n"
               "def main() -> None:\n"
               "    f(100)\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the narrowed source to fall back"


class TestLambdaBorrowTupleReturn:
    """A pointer-repr tuple lambda return (`Fn[[Point], tuple[str,
    Ref[Point]]]`) spells the borrow form (`-> std::tuple<std::string,
    Point*>`, to_cpp_return) and admits ONLY a same-typed generic-call
    body -- the monomorphized val_or_ptr_t tuple IS the closure's return.
    The record dict-element print arg rides the same fixture
    (`<< ::tpy::__getitem__(d, k)`)."""
    _SRC = (
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "    def __str__(self) -> str:\n"
        "        return f\"P{self.x}\"\n"
        "def label[T](tag: str, val: T) -> tuple[str, T]:\n"
        "    return (tag, val)\n"
        "def main() -> None:\n"
        "    pts: list[Point] = [Point(1), Point(2)]\n"
        "    d = dict(map(lambda p: label(str(p.x), p), pts))"
        "  # tpyc: warning(/copies tuple\\[str, Point\\] elements/)\n"
        "    for k in d:\n"
        "        print(k, d[k])\n"
        "main()\n")

    def test_btuple_lambda_routes(self):
        w, fallback = _assert_identical(self._SRC)
        assert not fallback, fallback
        assert w.get("lambda.btuple_ret", 0) >= 1
        assert w.get("call.lambda_btuple_ret", 0) >= 1
        assert w.get("print.record_subscript", 0) >= 1
        # The dict-receiver widening of record_elem_ok carries the read.
        assert w.get("subscript.record_elem_borrow", 0) >= 1
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "-> std::tuple<std::string, Point*> { return label<Point>(" \
            in cpp
        assert "<< ::tpy::__getitem__(d, k)" in cpp

    def test_tuple_literal_body_stays_ast(self):
        # A tuple-LITERAL body at the same borrow-tuple slot needs the
        # per-element lifts -- unadmitted, falls back byte-identically.
        src = (
            "from tpy import Int32, copy_iter\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def __str__(self) -> str:\n"
            "        return f\"P{self.x}\"\n"
            "def main() -> None:\n"
            "    pts: list[Point] = [Point(1), Point(2)]\n"
            "    d = dict(copy_iter(map(lambda p: (str(p.x), p), pts)))\n"
            "    for k in d:\n"
            "        print(k, d[k])\n"
            "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the tuple-literal lambda body to fall back"


class TestGenrecAliasInstanceArgs:
    """A generic recursive-alias INSTANCE type-arg (`Box[Tree[int]]`) is in
    the byte-identical slice (both paths spell through the same
    recursive_alias_cpp_names map), so the nested ctor rvalue admits at the
    Own[genrec] ctor slot and a borrow-returning wrapper-like method call
    (`h.data.get()` -> `Tree<T>&`) binds a same-wrapper generic-call slot
    bare."""
    _SRC = (
        "from tpy import Int32, Own\n"
        "from tplib.box import Box\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "class Holder[T]:\n"
        "    data: Box[Tree[T]]\n"
        "    def __init__(self, data: Own[Box[Tree[T]]]) -> None:\n"
        "        self.data = data\n"
        "def leaf_count[T](t: Tree[T]) -> Int32:\n"
        "    match t:\n"
        "        case list() as branches:\n"
        "            total = 0\n"
        "            for child in branches:\n"
        "                total += leaf_count(child)\n"
        "            return total\n"
        "        case _:\n"
        "            return 1\n"
        "def main() -> None:\n"
        "    tree: Tree[int] = [1, [2, 3], 4]\n"
        "    h = Holder(Box(tree))\n"
        "    print(leaf_count(h.data.get()))\n"
        "main()\n")

    def test_alias_instance_args_route(self):
        w, fallback = _assert_identical(self._SRC)
        assert not fallback, fallback
        assert w.get("arg.ru_wrapper_borrow_call", 0) >= 1
        assert w.get("own.record_rvalue", 0) >= 1
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "(::tpystd::tplib::box::Box<Tree<::tpy::BigInt>>"\
            "(std::move(tree)))" in cpp
        assert "leaf_count<::tpy::BigInt>(h.data.get())" in cpp

    def test_local_union_alias_arg_stays_ast(self):
        # BOUNDARY for the alias-instance type-arg slice: a nested arg
        # that is a module-LOCAL plain union alias (registered
        # mid-emission, after lowering) keeps the outer generic on the
        # AST path -- the UnionType sibling's reject, through the
        # recursive-alias arm.
        src = ("from tpy import Int32, StrView, Own\n"
               "from tplib.box import Box\n"
               "type Num = Int32 | StrView\n"
               "type Tree[T] = T | list[Tree[T]]\n"
               "class Holder:\n"
               "    data: Box[Tree[Num]]\n"
               "    def __init__(self, data: Own[Box[Tree[Num]]]) -> None:\n"
               "        self.data = data\n"
               "def main() -> None:\n"
               "    seed: Tree[Num] = [Int32(1)]\n"
               "    h = Holder(Box(seed))\n"
               "    print(\"ok\")\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the local-alias nested arg to fall back"

    def test_call_receiver_subscript_print_stays_ast(self):
        # BOUNDARY for print.record_subscript's bare-NAME receiver guard:
        # a record element off an Own-call rvalue receiver keeps
        # rejecting (the rvalue-container borrow shape).
        src = ("from tpy import Int32, Own\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "    def __str__(self) -> str:\n"
               "        return f\"P{self.x}\"\n"
               "def get_ps() -> Own[list[P]]:\n"
               "    return [P(1)]\n"
               "def main() -> None:\n"
               "    print(get_ps()[0])\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the call-receiver flavor to fall back"


class TestUnionValueLiftArg:
    """A ptr-variant-BOUND union name at a value-variant `Own[union]` slot
    copies the active member out (`to_value_variant<...>(p)` -- the AST
    Own-cascade's union lift, keyed on the BINDING set). Before this arm
    the free-call flavor ROUTED DIVERGENTLY (an ill-typed copy temp), so
    the routing pin here is also the regression guard."""
    _BASE = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self, y: Int32) -> None:\n        self.y = y\n"
        "def consume(v: Own[A | B]) -> None:\n"
        "    pass\n")

    def test_ptr_variant_bound_name_lifts(self):
        src = (self._BASE
               + "def free_flavor(p: A | B) -> None:\n"
               + "    consume(p)\n"
               + "def method_flavor(xs: list[A | B], p: A | B) -> None:\n"
               + "    xs.append(p)\n"
               + "def main() -> None:\n"
               + "    xs: list[A | B] = []\n"
               + "    method_flavor(xs, A(1))\n"
               + "    free_flavor(A(2))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.union_value_lift", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "consume(::tpy::to_value_variant<std::variant<A, B>>(p))" \
            in cpp
        assert "xs.push_back(::tpy::to_value_variant<std::variant<A, B>>" \
            "(p))" in cpp

    def test_value_variant_element_keeps_plain_copy(self):
        # A for-each ELEMENT binds the value variant: no lift, the plain
        # copy machinery renders -- byte-identical without the arm firing.
        src = (self._BASE
               + "def elem_bound(xs: list[A | B]) -> None:\n"
               + "    for v in xs:\n"
               + "        consume(v)\n"
               + "def main() -> None:\n"
               + "    elem_bound([A(1)])\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.union_value_lift", 0) == 0

    def test_narrowed_name_stays_ast(self):
        # INSIDE a narrow the AST renders the concrete alternative, not
        # the variant lift -- the gate's narrowed exclusion keeps it out.
        src = (self._BASE
               + "def narrowed_inside(p: A | B) -> None:\n"
               + "    if isinstance(p, A):\n"
               + "        consume(p)\n"
               + "def main() -> None:\n"
               + "    narrowed_inside(A(2))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the narrowed flavor to fall back"


class TestContainerPropertyReceiver:
    """A CONTAINER-valued property receiver composes the container method
    over the borrow-returning getter call (`c.items.append(4)` ->
    `c.items().push_back(4)`); a RECORD-valued property receiver keeps
    rejecting (its member-compose face is unwitnessed)."""
    _BASE = (
        "from tpy import Int32\n"
        "class C:\n"
        "    _items: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._items = [1]\n"
        "    @property\n"
        "    def items(self) -> list[Int32]:\n"
        "        return self._items\n")

    def test_container_property_recv_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    c = C()\n"
               + "    c.items.append(4)\n"
               + "    print(c.items)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("method.recv.container_property", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "c.items().push_back(4);" in cpp

    def test_record_property_recv_stays_ast(self):
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "    def bump(self) -> None:\n"
               "        self.x += 1\n"
               "class C:\n"
               "    _p: P\n"
               "    def __init__(self) -> None:\n"
               "        self._p = P(1)\n"
               "    @property\n"
               "    def p(self) -> P:\n"
               "        return self._p\n"
               "def main() -> None:\n"
               "    c = C()\n"
               "    c.p.bump()\n"
               "    print(c.p.x)\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the record-property receiver to fall back"

    def test_set_property_recv_routes(self):
        # The set flavor of the same row (the shared _container_method_recv
        # predicate gates set elements to the scalar/str/record slice).
        src = ("from tpy import Int32\n"
               "class C:\n"
               "    _tags: set[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._tags = {1}\n"
               "    @property\n"
               "    def tags(self) -> set[Int32]:\n"
               "        return self._tags\n"
               "def main() -> None:\n"
               "    c = C()\n"
               "    c.tags.add(4)\n"
               "    print(len(c.tags))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("method.recv.container_property", 0) >= 1


class TestUnionValueLiftContainerArgBoundary:
    """A value-variant-BOUND union name (a for-each element) at a
    container-method arg slot keeps the plain copy machinery -- the lift
    must not fire through the container-arg admission either."""

    def test_value_variant_elem_container_arg(self):
        src = ("from tpy import Int32, Own\n"
               "class A:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
               "class B:\n"
               "    y: Int32\n"
               "    def __init__(self, y: Int32) -> None:\n        self.y = y\n"
               "def copy_all(xs: list[A | B]) -> None:\n"
               "    ys: list[A | B] = []\n"
               "    for v in xs:\n"
               "        ys.append(v)\n"
               "    print(len(ys))\n"
               "def main() -> None:\n"
               "    copy_all([A(1)])\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert w.get("arg.union_value_lift", 0) == 0


class TestProtocolConceptAssert:
    """The PROTOCOL-isinstance assert renders the concept spelling under
    THIRAssert's negated-if wrap (`if (!(<concept>)) raise_assertion_
    error();`) -- the F5 constexpr-if arm's assert flavor. No extraction
    and no retype follow: sema resolves member dispatch off the fact."""

    def test_protocol_assert_routes(self):
        src = ("from typing import Iterable\n"
               "from tpy import Int32, Spannable, span\n"
               "def total_of(items: Spannable[Int32] | Iterable[Int32])"
               " -> Int32:\n"
               "    assert isinstance(items, Spannable)\n"
               "    return 7\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2, 3]\n"
               "    print(total_of(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("assert.protocol_concept", 0) >= 1
        c2, modules2 = _compile(src)
        hpp, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "Spannable<T_items, int32_t>)) ::tpy::raise_assertion_error();" \
            in (hpp + cpp)


class TestOptvalElemCopySetitem:
    """A same-element-type whole-optional ELEMENT read at a value-opt setitem
    slot passes bare (`items[i] = items[0]` -> `__setitem__(items, i,
    __getitem__(items, 0))` -- the optional element copies whole); a
    SCALAR value source keeps rejecting (its wrap is unwitnessed)."""

    def test_optval_elem_copy_routes(self):
        src = ("from tpy import Int32\n"
               "def f(items: list[Int32 | None], i: Int32) -> None:\n"
               "    items[i] = items[0]\n"
               "def main() -> None:\n"
               "    xs: list[Int32 | None] = [1, None]\n"
               "    f(xs, 1)\n"
               "    print(len(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("setitem.optval_elem_copy", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "::tpy::__setitem__(items, i, ::tpy::__getitem__(items, 0))" \
            in cpp

    def test_scalar_value_source_stays_ast(self):
        src = ("from tpy import Int32\n"
               "def f(items: list[Int32 | None], n: Int32) -> None:\n"
               "    items[0] = n\n"
               "def main() -> None:\n"
               "    xs: list[Int32 | None] = [1]\n"
               "    f(xs, 5)\n"
               "    print(len(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the scalar source to fall back"

    def test_downstream_peephole_rides_the_retype(self):
        # The persisted fact: after the assert the subject's declared
        # entry retypes, so the NativeIterable for-loop peephole routes
        # (without the retype the for-head gate rejects the stale
        # protocol type -- probed).
        src = ("from typing import Iterable\n"
               "from tpy import Int32, NativeIterable\n"
               "def total(xs: Iterable[Int32] | NativeIterable[Int32])"
               " -> Int32:\n"
               "    assert isinstance(xs, NativeIterable)\n"
               "    t: Int32 = 0\n"
               "    for x in xs:\n"
               "        t += x\n"
               "    return t\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2, 3]\n"
               "    print(total(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("assert.protocol_concept", 0) >= 1

    # No resumable boundary pin: a generator taking the protocol-union
    # param shape rejects at async-coro codegen outright ("multi-protocol
    # ... not yet supported"), so the render_concept-None guard has no
    # constructible witness through this shape -- sema/codegen-implied.

class TestOptvalElemCopyCrossType:
    """The element-copy row is TYPE-keyed, not container-keyed: a
    same-element-type read off a DIFFERENT container routes; a
    cross-TYPE read keeps the named reject."""

    def test_cross_container_same_type_routes(self):
        src = ("from tpy import Int32\n"
               "def f(items: list[Int32 | None], other: list[Int32 | None])"
               " -> None:\n"
               "    items[0] = other[0]\n"
               "def main() -> None:\n"
               "    xs: list[Int32 | None] = [1]\n"
               "    ys: list[Int32 | None] = [2]\n"
               "    f(xs, ys)\n"
               "    print(len(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("setitem.optval_elem_copy", 0) >= 1

    # No cross-TYPE reject pin: sema rejects the cross-type element
    # assign outright ("Type mismatch in assignment"), so the row's
    # type-equality guard is defensive, not a reachable boundary.


class TestNestedContainerElementRows:
    """Three sibling rows over nested-container elements: a MOVE-source
    same-type container name at the element store (`g["a"] = a` ->
    `std::move(a)`), a container-element subscript at a matching ref
    param (`push(g["b"], 7)` -- the checked lvalue binds inline), and a
    container-element subscript ITERABLE (`for v in g["a"]:` -- the
    `auto& __obj_N` begin/end capture)."""
    _SRC = (
        "from tpy import Int32\n"
        "def push(xs: list[Int32], v: Int32) -> None:\n"
        "    xs.append(v)\n"
        "def main() -> None:\n"
        "    a: list[Int32] = [1, 2]\n"
        "    g: dict[str, list[Int32]] = {}\n"
        "    g[\"a\"] = a\n"
        "    push(g[\"a\"], 7)\n"
        "    total = 0\n"
        "    for v in g[\"a\"]:\n"
        "        total += v\n"
        "    print(total)\n"
        "main()\n")

    def test_three_rows_route(self):
        w, fallback = _assert_identical(self._SRC)
        assert not fallback, fallback
        assert w.get("setitem.container_move", 0) >= 1
        c2, modules2 = _compile(self._SRC)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert '::tpy::__setitem__(g, "a", std::move(a));' in cpp
        assert 'push(::tpy::__getitem__(g, "a"), 7);' in cpp
        assert 'auto& __obj_0 = ::tpy::__getitem__(g, "a");' in cpp

    def test_live_name_store_stays_ast(self):
        # A still-LIVE container name at the element store copies with a
        # warning on the AST path -- the move row must not capture it.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    a: list[Int32] = [1]\n"
               "    g: dict[str, list[Int32]] = {}\n"
               "    g[\"a\"] = a\n"
               "    a.append(2)\n"
               "    print(len(g[\"a\"]))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert w.get("setitem.container_move", 0) == 0


class TestSteppedSliceOverload:
    """A STEPPED slice at a user-record @overload getitem dispatches on
    the 3-part `::tpy::Slice{lo, hi, step}` initializer (the emit picks
    the spelling off the stepped flag); the 2-part BasicSlice row is the
    wave-20 arm."""

    def test_stepped_slice_routes(self):
        src = ("from typing import overload\n"
               "from tpy import Int32, Span, readonly\n"
               "class Window:\n"
               "    _data: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = [10, 20, 30]\n"
               "    @overload\n"
               "    def __getitem__(self, index: basic_slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    @overload\n"
               "    def __getitem__(self, index: slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    def __getitem__(self, index: basic_slice | slice)"
               " -> Span[readonly[Int32]]:\n"
               "        return self._data[0:len(self._data)]\n"
               "def main() -> None:\n"
               "    w = Window()\n"
               "    sp = w[0:5:2]\n"
               "    print(len(sp))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        # The simplified impl body keeps its own overload fence; the pin
        # claims the CALLER (the stepped-slice dispatch site) routes.
        assert list(fallback) == ["body:sig.overload_set.narrow_param"], \
            fallback
        c2, modules2 = _compile(src)
        hpp, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "w.__getitem__(::tpy::Slice{0, 5, 2})" in (cpp + hpp)

    def test_negative_step_renders(self):
        src = ("from typing import overload\n"
               "from tpy import Int32, Span, readonly\n"
               "class Window:\n"
               "    _data: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = [10, 20, 30]\n"
               "    @overload\n"
               "    def __getitem__(self, index: basic_slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    @overload\n"
               "    def __getitem__(self, index: slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    def __getitem__(self, index: basic_slice | slice)"
               " -> Span[readonly[Int32]]:\n"
               "        return self._data[0:len(self._data)]\n"
               "def main() -> None:\n"
               "    w = Window()\n"
               "    sp = w[0:5:-1]\n"
               "    print(len(sp))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert list(fallback) == ["body:sig.overload_set.narrow_param"], \
            fallback
        c2, modules2 = _compile(src)
        hpp, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "w.__getitem__(::tpy::Slice{0, 5, -1})" in (cpp + hpp)

    def test_variable_step_bound_routes(self):
        # Probed, not assumed: an int-VARIABLE step is a supported bound
        # (_slice_bound_supported admits names), so the caller routes --
        # a documenting pin; no reachable rejecting step shape was found
        # (BigInt-literal bounds are the str-slice family's fence).
        src = ("from typing import overload\n"
               "from tpy import Int32, Span, readonly\n"
               "class Window:\n"
               "    _data: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = [10, 20, 30]\n"
               "    @overload\n"
               "    def __getitem__(self, index: basic_slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    @overload\n"
               "    def __getitem__(self, index: slice)"
               " -> Span[readonly[Int32]]: ...\n"
               "    def __getitem__(self, index: basic_slice | slice)"
               " -> Span[readonly[Int32]]:\n"
               "        return self._data[0:len(self._data)]\n"
               "def main() -> None:\n"
               "    w = Window()\n"
               "    st: int = 2\n"
               "    sp = w[0:5:st]\n"
               "    print(len(sp))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert list(fallback) == ["body:sig.overload_set.narrow_param"], \
            fallback


class TestOwnTupleParamSubscript:
    """Element reads off a per-element-Own tuple PARAM (`p: tuple[Own[A],
    Int32]` -> `std::tuple<A, int32_t>&&`): borrow and storage coincide,
    so `p[0].n` / `p[1]` spell the bare `std::get<N>(p)` reads (the
    Own record element reads `.`)."""

    def test_own_tuple_elem_reads_route(self):
        src = ("from tpy import Int32, Own\n"
               "class A:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def consume(p: tuple[Own[A], Int32]) -> Int32:\n"
               "    return p[0].n + p[1]\n"
               "def main() -> None:\n"
               "    a = A(5)\n"
               "    t = (a, 7)\n"
               "    print(consume(t))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        # The caller's tuple-NAME arg routes too since the Own-tuple arg
        # rows landed (wave 28); the whole program is clean.
        assert not fallback, fallback
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "std::get<0>(p).n" in cpp
        assert "std::get<1>(p)" in cpp


class TestNativeOptptrArg:
    """A ptr-repr Optional[F1-record] NAME passed WHOLE at a native
    callee's same-optional slot (`repr(opt_none)` -> the bare `T*`; the
    runtime overload prints None); the narrowed read derefs via the
    name-read model (`repr_of((*opt_some))`)."""

    def test_optional_repr_flavors_route(self):
        src = ("from tpy import Int32\n"
               "class R:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def __repr__(self) -> str:\n"
               "        return f\"R({self.n})\"\n"
               "def main() -> None:\n"
               "    opt_some: R | None = R(7)\n"
               "    opt_none: R | None = None\n"
               "    print(repr(opt_some))\n"
               "    print(repr(opt_none))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.native_protocol_optptr", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "::tpy::repr_of((*opt_some))" in cpp
        assert "::tpy::repr_of(opt_none)" in cpp

    def test_str_element_flavor_routes(self):
        # Probed, not assumed: a str-element mixed Own tuple's body reads
        # also route byte-identically (the owned-str element read rides
        # the value-read arm) -- a documenting pin; only the caller's
        # tuple-NAME arg keeps its own gate.
        src = ("from tpy import Int32, Own\n"
               "class A:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def consume(p: tuple[Own[A], str]) -> Int32:\n"
               "    print(p[1])\n"
               "    return p[0].n\n"
               "def main() -> None:\n"
               "    a = A(5)\n"
               "    t = (a, \"x\")\n"
               "    print(consume(t))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        # The caller's tuple-NAME arg routes too since the Own-tuple arg
        # rows landed (wave 28); the whole program is clean.
        assert not fallback, fallback


class TestNestedCtorValueOptArgs:
    """None and str-literal args at a value-opt slot of a NESTED ctor
    (`describe(Dog(None))` / `describe(Dog("rex"))`): both render
    temp-free (`std::nullopt` / the bare literal into the optional's
    converting ctor), so the restricted NESTED tail admits them like
    the direct loop."""

    def test_nested_value_opt_args_route(self):
        src = ("from tpy import Int32\n"
               "class Dog:\n"
               "    name: str | None\n"
               "    def __init__(self, name: str | None) -> None:\n"
               "        self.name = name\n"
               "def describe(d: Dog) -> Int32:\n"
               "    return 1 if d.name is not None else 0\n"
               "def main() -> None:\n"
               "    print(describe(Dog(None)))\n"
               "    print(describe(Dog(\"rex\")))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        # This flavor rides the DIRECT loop (the statement flushes); the
        # restricted-tail witness is the flipped corpus case
        # match/poly_field_none, held by the ratchet.
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "Dog(std::nullopt)" in cpp
        assert 'Dog("rex")' in cpp


class TestBorrowCallTernaryReseat:
    """A ternary of borrow-returning calls at a pointer reseat renders
    the PTR_ADDR address-of over the whole conditional (`b = &(((flag) ?
    (g.itself()) : (h.itself())));`); a MIXED name/call ternary keeps the
    named reject (only both-borrow-call arms make the rung)."""
    _BASE = (
        "from tpy import Int32\n"
        "class Bag:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 1\n"
        "    def itself(self) -> Bag:\n"
        "        return self\n")

    def test_borrow_call_ternary_routes(self):
        src = (self._BASE
               + "def f(flag: bool) -> None:\n"
               + "    g = Bag()\n"
               + "    h = Bag()\n"
               + "    b = Bag()\n"
               + "    b = g.itself() if flag else h.itself()\n"
               + "    print(b.n)\n"
               + "def main() -> None:\n"
               + "    f(True)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("reseat.borrow_call_ternary", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "b = &(((flag) ? (g.itself()) : (h.itself())));" in cpp

    def test_mixed_arm_ternary_stays_ast(self):
        src = (self._BASE
               + "def f(flag: bool) -> None:\n"
               + "    g = Bag()\n"
               + "    b = Bag()\n"
               + "    b = g.itself() if flag else b\n"
               + "    print(b.n)\n"
               + "def main() -> None:\n"
               + "    f(True)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the mixed-arm ternary to fall back"

    def test_value_ctor_arm_ternary_stays_ast(self):
        # BOUNDARY for the record-ifexpr arm widening: a VALUE-returning
        # ctor arm makes the C++ ternary a prvalue -- outside the
        # name-or-borrow-call slice, the body falls back.
        src = (self._BASE
               + "def f(flag: bool) -> None:\n"
               + "    b = Bag() if flag else Bag()\n"
               + "    print(b.n)\n"
               + "def main() -> None:\n"
               + "    f(True)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the ctor-arm ternary to fall back"

    def test_container_borrow_call_ternary_unreachable(self):
        # The reseat rung is type-shape-agnostic, but a container-typed
        # flavor is unreachable today: the container reassignment falls
        # back at its own earlier gate. Documenting pin (byte-identical
        # fallback) so a future loosening of that gate surfaces here.
        src = ("from tpy import Int32\n"
               "class Pool:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.xs = [1]\n"
               "    def get(self) -> list[Int32]:\n"
               "        return self.xs\n"
               "def f(flag: bool) -> None:\n"
               "    g = Pool()\n"
               "    h = Pool()\n"
               "    xs = g.get()\n"
               "    xs = g.get() if flag else h.get()\n"
               "    print(len(xs))\n"
               "def main() -> None:\n"
               "    f(True)\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the container flavor to fall back"

    def test_name_arg_nested_value_opt_stays_ast(self):
        # BOUNDARY for the nested value-opt rows: a NAME source at the
        # same nested slot keeps rejecting (only literal sources are
        # position-independent).
        src = ("from tpy import Int32\n"
               "class Dog:\n"
               "    name: str | None\n"
               "    def __init__(self, name: str | None) -> None:\n"
               "        self.name = name\n"
               "def describe(d: Dog) -> Int32:\n"
               "    return 1 if d.name is not None else 0\n"
               "def use(flag: bool, nm: str | None) -> None:\n"
               "    if flag:\n"
               "        print(describe(Dog(nm)))\n"
               "def main() -> None:\n"
               "    use(True, \"rex\")\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert w.get("ctor.nested_none_value_opt", 0) == 0


class TestOwnTupleArgForms:
    """An Own-element tuple NAME at the `std::tuple<...>&&` slot: a
    STORAGE-form binding moves in bare at its last use (the modulo-Own
    element compare in _own_tuple_move_arg), a BORROW-form binding lifts
    through the F3 storage conversion (the warned copy). Still-live
    STORAGE bindings (the AST's `auto(p)` decay-copy, incl. Own-tuple
    params) stay unmirrored -- pinned as fallback."""
    _BASE = (
        "from tpy import Int32, Own, copy\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "def sink(p: tuple[Own[Box], Int32]) -> Int32:\n"
        "    return p[1]\n")

    def test_borrow_form_lifts(self):
        # The post-use read of `ob` keeps the pair a BORROW-form binding.
        src = (self._BASE
               + "def f(ob: Own[Box]) -> Int32:\n"
               + "    pair = (ob, 0)\n"
               + "    r = sink(pair)\n"
               + "    return r + ob.val\n"
               + "def main() -> None:\n"
               + "    print(f(Box(1)))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.own_tuple_borrow_lift", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "sink(::tpy::tuple_to_storage<" in cpp

    def test_storage_form_moves(self):
        src = (self._BASE
               + "def f(b: Box) -> Int32:\n"
               + "    pair = (copy(b), 0)\n"
               + "    return sink(pair)\n"
               + "def main() -> None:\n"
               + "    print(f(Box(1)))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("move.own_tuple", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "sink(std::move(pair))" in cpp

    def test_still_live_storage_stays_ast(self):
        # BOUNDARY: a still-live STORAGE binding needs the auto(p)
        # decay-copy -- unmirrored, the body falls back byte-identically.
        src = (self._BASE
               + "def f(b: Box) -> Int32:\n"
               + "    pair = (copy(b), 0)\n"
               + "    r = sink(pair)\n"
               + "    return r + pair[1]\n"
               + "def main() -> None:\n"
               + "    print(f(Box(1)))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert fallback, "expected the still-live storage form to fall back"


class TestCopyIterForHead:
    """A copy_iter(...) call at the for-head: the AST's CopyIter peephole
    (the iterator object exposes begin/end) is the container route's
    owning rvalue capture. own_iter (consuming binding) and an IntLiteral
    elem (the peephole skips the native path's literal resolution) stay
    deferred."""

    def test_copy_iter_call_routes(self):
        src = ("from tpy import copy_iter\n"
               "def use(xs: list[int]) -> None:\n"
               "    for x in copy_iter(xs):\n"
               "        print(x)\n"
               "def main() -> None:\n"
               "    use([1, 2, 3])\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("foreach.copy_iter_call", 0) >= 1

    def test_own_iter_stays_ast(self):
        # BOUNDARY: own_iter's consuming elem binding + movable-local
        # registration are unmirrored -- the body falls back.
        src = ("from tpy import own_iter\n"
               "def use() -> None:\n"
               "    ys: list[int] = [4, 5]\n"
               "    for x in own_iter(ys):\n"
               "        print(x)\n"
               "def main() -> None:\n"
               "    use()\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected own_iter for-head to fall back"

    def test_literal_elem_stays_ast(self):
        # BOUNDARY: a literal-list arg leaves the elem IntLiteral; the AST
        # peephole binds it unresolved, so the arm defers.
        src = ("from tpy import copy_iter\n"
               "def main() -> None:\n"
               "    for x in copy_iter([1, 2, 3]):\n"
               "        print(x)\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected literal-elem copy_iter to fall back"


class TestQualCtorProtocolTemp:
    """A module-qualified ctor rvalue at a STRUCTURAL protocol slot
    (`via_protocol(io.StringIO("..."))`): the hoisted temp is a storage
    sink, so the init lowers under STORAGE and the marker gate admits the
    record result. A @dynamic slot keeps its adapter machinery on the AST
    path."""
    _BASE = (
        "import io\n"
        "from tpy import Readable\n"
        "def use(fp: Readable) -> str:\n"
        "    return fp.read(2)\n")

    def test_qual_ctor_arg_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    print(use(io.StringIO(\"ab\")))\n"
               + "    s = io.StringIO(\"qr\")\n"
               + "    print(use(s))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("argtemp.protocol", 0) >= 1

    def test_dyn_slot_stays_ast(self):
        # BOUNDARY: the @dynamic slot's adapter wrap is unmirrored for a
        # module-qual ctor rvalue -- the arg-shape gate defers the body.
        src = ("import io\n"
               "from typing import Protocol\n"
               "from tpy import dynamic, Int32\n"
               "@dynamic\n"
               "class Readable2(Protocol):\n"
               "    def read(self, n: Int32) -> str: ...\n"
               "def use(fp: Readable2) -> str:\n"
               "    return fp.read(2)\n"
               "def main() -> None:\n"
               "    print(use(io.StringIO(\"ab\")))\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the dyn-slot ctor arg to fall back"


class TestBytearrayAugConcat:
    """`got += chunk` on a bytearray local: the same resolved-binop
    concat-and-assign render as the owned-bytes target
    (`got = ::tpy::bytes_concat(got, chunk);`). A param target keeps the
    bytes row's local-only condition; a bytearray VALUE operand has no
    admission row."""

    def test_bytearray_local_routes(self):
        src = ("def main() -> None:\n"
               "    got = bytearray()\n"
               "    got += b\"ab\"\n"
               "    chunk = b\"cd\"\n"
               "    got += chunk\n"
               "    print(bytes(got).decode())\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "got = ::tpy::bytes_concat(got, chunk)" in cpp

    def test_param_target_stays_ast(self):
        # BOUNDARY: the local-only condition is mirrored from the bytes row.
        src = ("def use(got: bytearray) -> None:\n"
               "    got += b\"xy\"\n"
               "    print(bytes(got).decode())\n"
               "def main() -> None:\n"
               "    g = bytearray()\n"
               "    use(g)\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the param target to fall back"

    def test_bytearray_value_stays_ast(self):
        # BOUNDARY: a bytearray VALUE operand is outside
        # _bytes_concat_operand's slice.
        src = ("def use(a: bytearray) -> None:\n"
               "    got = bytearray()\n"
               "    got += a\n"
               "    print(bytes(got).decode())\n"
               "def main() -> None:\n"
               "    b2 = bytearray(b\"zz\")\n"
               "    use(b2)\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the bytearray value operand to fall back"


class TestUnionFieldWriteSources:
    """A value-variant union FIELD write from an assign-narrowed same-union
    ptr-variant NAME (`z.pet = new_pet`) or a ptr-variant-returning free
    CALL (`z.pet = identity(new_pet)`): both consumed whole through the
    sink's `to_value_variant` lift, so the divergent-read fence and the
    call result gate open exactly at this sink."""
    _BASE = (
        "class Dog:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "class Cat:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "class Zoo:\n"
        "    pet: Dog | Cat\n"
        "    def __init__(self, pet: Dog | Cat) -> None:\n"
        "        self.pet = pet\n"
        "def identity(p: Dog | Cat) -> Dog | Cat:\n"
        "    return p\n")

    def test_narrowed_name_source_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    z = Zoo(Dog(\"a\"))\n"
               + "    np: Dog | Cat = Cat(\"w\")\n"
               + "    z.pet = np\n"
               + "    print(np.name)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("field_write.union_name_lift", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "z.pet = ::tpy::to_value_variant<" in cpp

    def test_call_source_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    z = Zoo(Dog(\"a\"))\n"
               + "    np: Dog | Cat = Dog(\"d\")\n"
               + "    z.pet = identity(np)\n"
               + "    print(np.name)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("call.union_value_lift_ret", 0) >= 1

    def test_member_typed_sink_keeps_fence(self):
        # BOUNDARY: the divergent read into a MEMBER-typed sink is the AST
        # miscompile the fence guards -- it must keep deferring.
        src = (self._BASE
               + "def take_cat(c: Cat) -> None:\n"
               + "    print(c.name)\n"
               + "def main() -> None:\n"
               + "    np: Dog | Cat = Cat(\"w\")\n"
               + "    take_cat(np)\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the member-typed sink to keep the fence"


class TestStubContainerCallArg:
    """A container-returning call at a stub method's concrete container
    slot (`a.update(make_dict())` / `a.update(copy(b))`): the bare call
    under the cpp_template, STORAGE-threaded so the inner call's result
    gate admits the container. The match is element-Own-blind (the stub
    slot spells `dict[K, Own[V]]`)."""

    def test_update_call_args_route(self):
        src = ("from tpy import copy, Own\n"
               "def make_dict() -> Own[dict[str, int]]:\n"
               "    return {\"a\": 1}\n"
               "def main() -> None:\n"
               "    a: dict[str, int] = {}\n"
               "    a.update(make_dict())\n"
               "    b: dict[str, int] = {\"b\": 2}\n"
               "    a.update(copy(b))\n"
               "    print(len(a), len(b))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.container_call_rvalue", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "::tpy::dict_update(a, make_dict())" in cpp

    def test_mismatched_family_not_admitted(self):
        # SIBLING: a container call at the protocol Iterable slot
        # (`xs.extend(make_list())`) must route via the ITERABLE row, not
        # this one (a list call at the dict slot itself is a sema error).
        src = ("from tpy import Own\n"
               "def make_list() -> Own[list[int]]:\n"
               "    return [1, 2]\n"
               "def main() -> None:\n"
               "    xs: list[int] = []\n"
               "    xs.extend(make_list())\n"
               "    print(len(xs))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.container_call_rvalue", 0) == 0

    def test_set_update_call_routes(self):
        # The set leg of the same family guard.
        src = ("from tpy import Own\n"
               "def copy_of(x: set[int]) -> Own[set[int]]:\n"
               "    r: set[int] = set()\n"
               "    for v in x:\n"
               "        r.add(v)\n"
               "    return r\n"
               "def main() -> None:\n"
               "    s: set[int] = set()\n"
               "    s2: set[int] = {3, 4}\n"
               "    s.update(copy_of(s2))\n"
               "    print(len(s))\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.container_call_rvalue", 0) >= 1

    def test_own_elem_slot_stays_ast(self):
        # BOUNDARY: an Own[container] ELEMENT slot is outside the row's
        # concrete-container slot family -- the body falls back.
        src = ("from tpy import Own\n"
               "def make_list() -> Own[list[int]]:\n"
               "    return [7]\n"
               "def main() -> None:\n"
               "    rows: list[list[int]] = []\n"
               "    rows.append(make_list())\n"
               "    print(len(rows))\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the Own-elem slot call arg to fall back"


class TestPrintKwargsTail:
    """print's flush= literal (True -> `<< std::flush`, False -> no
    token; sema pins it to a literal), the record-NAME `file=` sink
    (bare lvalue under as_ostream), and the empty-args file-only form
    (just the end token). Empty-args with sep/end kwargs stays AST."""
    _SINK = (
        "from tpy import Int32\n"
        "class Sink:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = Int32(0)\n"
        "    def write(self, text: str) -> Int32:\n"
        "        self.n += Int32(1)\n"
        "        return Int32(len(text))\n"
        "    def flush(self) -> None:\n"
        "        pass\n")

    def test_flush_and_name_sink_route(self):
        src = (self._SINK
               + "def main() -> None:\n"
               + "    s = Sink()\n"
               + "    print(\"a\", file=s, flush=True)\n"
               + "    print(file=s)\n"
               + "    print(\"c\", flush=False)\n"
               + "    print(s.n)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("print.kw_flush", 0) >= 1
        assert w.get("print.file_name_sink", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert '<< "\\n" << std::flush;' in cpp
        assert '::tpy::as_ostream(s) << "\\n";' in cpp
        assert '"c" << "\\n";' in cpp

    def test_empty_print_with_end_stays_ast(self):
        # BOUNDARY: empty-args + a non-file kwarg needs gen_print's
        # emit-nothing arm -- keeps deferring.
        src = ("def main() -> None:\n"
               "    print(end=\"\")\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected empty print with end= to fall back"


class TestPtrOptSelfReturn:
    """`return self` at `-> Optional[Self]` renders the bare `this` (the
    receiver already IS the `T*` the ptr-opt return spells); both
    auto_readonly clones share the token. A generator's frame-captured
    self keeps rejecting."""
    _BASE = (
        "from typing import Optional, Self\n"
        "from tpy import Int32\n"
        "class Node:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n")

    def test_self_return_routes(self):
        src = (self._BASE
               + "    def pick(self, want: Int32) -> Optional[Self]:\n"
               + "        if self.v == want:\n"
               + "            return self\n"
               + "        return None\n"
               + "def main() -> None:\n"
               + "    n = Node(4)\n"
               + "    r = n.pick(4)\n"
               + "    if r is not None:\n"
               + "        print(r.v)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.ptr_opt_self", 0) >= 1
        c2, modules2 = _compile(src)
        h, _cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "return this;" in h

    def test_plain_self_return_routes(self):
        # SIBLING: `return self` at plain `-> Self` keeps its existing
        # `(*this)` row -- distinct render from the ptr-opt `this` token.
        # (A frame-captured generator self is not legally constructible
        # at a ptr-opt return, so the frame boundary has no pin.)
        src = (self._BASE
               + "    def bump(self) -> Self:\n"
               + "        self.v += 1\n"
               + "        return self\n"
               + "def main() -> None:\n"
               + "    n = Node(4)\n"
               + "    print(n.bump().v)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.ptr_opt_self", 0) == 0


class TestCopyPtrVariant:
    """`copy(pet)` of a ptr-variant union binding: the whole-variant deep
    copy via `to_value_variant` (the STORAGE FormConvert on the bare
    name), keyed on the BINDING type -- an assign-narrowed local reads as
    its member but still copies the variant; the plain-record copy row
    explicitly excludes such bindings. Isinstance-narrowed sources stay
    on the AST path."""
    _BASE = (
        "from tpy import copy\n"
        "class Dog:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "class Cat:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n")

    def test_param_and_narrowed_binding_route(self):
        src = (self._BASE
               + "def copy_param(pet: Dog | Cat) -> None:\n"
               + "    pet2 = copy(pet)\n"
               + "    if isinstance(pet2, Dog):\n"
               + "        print(pet2.name)\n"
               + "def copy_assigned() -> None:\n"
               + "    d = Dog(\"Rex\")\n"
               + "    pet: Dog | Cat = d\n"
               + "    pet2 = copy(pet)\n"
               + "    d.name = \"Changed\"\n"
               + "    if isinstance(pet2, Dog):\n"
               + "        print(pet2.name)\n"
               + "def main() -> None:\n"
               + "    copy_param(Dog(\"A\"))\n"
               + "    copy_assigned()\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("call.copy_ptr_variant", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "::tpy::to_value_variant<std::variant<Cat, Dog>>(pet)" in cpp

    def test_plain_record_copy_row_intact(self):
        # REGRESSION: the copy-record row's new ptr-variant exclusion must
        # not disturb a plain record copy decl.
        src = (self._BASE
               + "def main() -> None:\n"
               + "    d = Dog(\"Solo\")\n"
               + "    d2 = copy(d)\n"
               + "    d.name = \"x\"\n"
               + "    print(d2.name)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("decl.copy_record", 0) >= 1
        assert w.get("call.copy_ptr_variant", 0) == 0

    def test_narrowed_branch_copy_routes(self):
        # An isinstance-narrowed source's copy decl in a branch: the copy
        # reads the pre-narrow union binding, not the branch-retyped one.
        src = (self._BASE
               + "def use(pet: Dog | Cat) -> None:\n"
               + "    if isinstance(pet, Dog):\n"
               + "        p2 = copy(pet)\n"
               + "        if isinstance(p2, Dog):\n"
               + "            print(p2.name)\n"
               + "def main() -> None:\n"
               + "    use(Dog(\"B\"))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("call.copy_ptr_variant", 0) >= 1


class TestOwnStorageTupleNameReturn:
    """`return pair;` of a STORAGE-form Own-element tuple binding at the
    widened value-tuple return slot: the bare name through the generic
    tail (NRVO, no lift). The borrow-form sibling is sema-unreachable
    (returning a borrowed element into an Own slot is a SemanticError),
    so the storage_tuple_locals guard mirrors a proven fact."""

    def test_storage_name_returns_route(self):
        src = ("from tpy import Int32, Own, copy\n"
               "class Box:\n"
               "    val: Int32\n"
               "    def __init__(self, val: Int32) -> None:\n"
               "        self.val = val\n"
               "def f(b: Box) -> tuple[Own[Box], Int32]:\n"
               "    pair = (copy(b), 0)\n"
               "    return pair\n"
               "def main() -> None:\n"
               "    r = f(Box(3))\n"
               "    print(r[1])\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.own_storage_tuple_name", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "return pair;" in cpp

    def test_value_tuple_name_row_intact(self):
        # SIBLING: a plain value-tuple NAME return keeps its own witness.
        src = ("def f() -> tuple[int, int]:\n"
               "    pair = (1, 2)\n"
               "    return pair\n"
               "def main() -> None:\n"
               "    print(f()[0])\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.own_storage_tuple_name", 0) == 0


class TestOwnElemTupleLiteralArg:
    """A tuple LITERAL at an Own-ELEMENT tuple slot (`read_owned((A(1),
    2))` at `std::tuple<A, int32_t>&&`): the spelled brace-init with each
    element rendered against its Own-peeled by-value slot -- the
    Own-storage-tuple RETURN arm's arg twin (rvalue / movable-last-use /
    copy() members)."""
    _BASE = (
        "from tpy import Own, Int32, copy\n"
        "class A:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "def read_owned(p: tuple[Own[A], Int32]) -> Int32:\n"
        "    return p[1]\n")

    def test_literal_arg_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    print(read_owned((A(1), 2)))\n"
               + "    a = A(3)\n"
               + "    print(read_owned((copy(a), 4)))\n"
               + "    print(a.n)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.own_elem_tuple_literal", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "read_owned(std::tuple<A, int32_t>{A(1), 2})" in cpp

    def test_movable_name_elem_moves(self):
        # A movable last-use NAME element takes the member-wise move
        # (`{std::move(a), 4}` -- the AST's per-element auto-move).
        src = (self._BASE
               + "def main() -> None:\n"
               + "    a = A(3)\n"
               + "    print(read_owned((a, 4)))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("arg.own_elem_tuple_literal", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "{std::move(a), 4}" in cpp

    def test_ternary_elem_stays_ast(self):
        # BOUNDARY (same-site): a ternary member is neither an rvalue
        # source nor a movable name -- the arm's own reject branch fires.
        src = (self._BASE
               + "def use(f: bool) -> None:\n"
               + "    print(read_owned((A(1) if f else A(2), 3)))\n"
               + "def main() -> None:\n"
               + "    use(True)\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the ternary member to fall back"

    def test_readonly_borrow_tuple_return_stays_ast(self):
        # BOUNDARY: the sibling shape -- a borrow-tuple RETURN of
        # const-rooted field reads -- is a different family and must keep
        # deferring (the borrow builder's const-pointee elements).
        src = ("from tpy import Int32, readonly\n"
               "class Container:\n"
               "    value: Int32\n"
               "    def __init__(self, value: Int32) -> None:\n"
               "        self.value = value\n"
               "class Holder:\n"
               "    inner: Container\n"
               "    def __init__(self, inner: Container) -> None:\n"
               "        self.inner = inner\n"
               "    @readonly\n"
               "    def get_pair(self) -> tuple[Container, Int32]:\n"
               "        return (self.inner, self.inner.value)\n"
               "def main() -> None:\n"
               "    h = Holder(Container(7))\n"
               "    print(h.get_pair()[1])\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the readonly borrow-tuple return to defer"


class TestPtrRecvRecordGetitem:
    """A pointer-slot LOCAL receiver at a record-getitem field read
    (`acc[0].name` on a rebind-slot ArrayList local): the name read
    derefs (`(*acc)[0].name`), threaded via indirect_read at the
    prechecked record-getitem construction. Only the FIELD-over-getitem
    gate admits pointer receivers (ptr_recv_ok); the subscript arm's
    other positions keep excluding them."""
    _BASE = (
        "from tplib import ArrayList\n"
        "from tpy import Own\n"
        "class Item:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "def make() -> Own[ArrayList[Item, 4]]:\n"
        "    xs = ArrayList[Item, 4]()\n"
        "    xs.append(Item(\"alpha\"))\n"
        "    return xs\n")

    def test_ptr_receiver_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    acc = make()\n"
               + "    acc = make()\n"
               + "    print(acc[0].name)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("subscript.record_getitem", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "(*acc)[0].name" in cpp

    def test_bare_subscript_ptr_recv_stays_ast(self):
        # BOUNDARY: a bare subscript decl off the pointer-slot receiver
        # (no field consumer) keeps rejecting -- ptr_recv_ok is scoped to
        # the field-over-getitem gate.
        src = (self._BASE
               + "def main() -> None:\n"
               + "    acc = make()\n"
               + "    acc = make()\n"
               + "    it = acc[0]\n"
               + "    print(it.name)\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the bare ptr-recv subscript to fall back"

    def test_plain_receiver_row_intact(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    xs = make()\n"
               + "    print(xs[0].name)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "xs[0].name" in cpp
        assert "(*xs)" not in cpp


class TestAssignNarrowedUnionMethodRecv:
    """An ASSIGN-narrowed ptr-variant union NAME as a METHOD receiver
    (`c: Circle | Rect = Circle(5.0); c.area()`): the inline bare-get
    read (`(*std::get<Circle*>(c)).area()`) -- the field row's method
    twin; the gate dispatches on the member record. Isinstance-narrowed
    receivers keep their alias arms."""
    _BASE = (
        "from tpy import Int32, Float64\n"
        "class Circle:\n"
        "    r: Float64\n"
        "    def __init__(self, r: Float64) -> None:\n"
        "        self.r = r\n"
        "    def area(self) -> Float64:\n"
        "        return 3.14 * self.r * self.r\n"
        "    def scaled(self, k: Int32) -> Float64:\n"
        "        return self.area() * k\n"
        "class Rect:\n"
        "    w: Float64\n"
        "    h: Float64\n"
        "    def __init__(self, w: Float64, h: Float64) -> None:\n"
        "        self.w = w\n"
        "        self.h = h\n"
        "    def area(self) -> Float64:\n"
        "        return self.w * self.h\n")

    def test_assign_narrowed_receiver_routes(self):
        src = (self._BASE
               + "def main() -> None:\n"
               + "    c: Circle | Rect = Circle(5.0)\n"
               + "    print(c.area())\n"
               + "    print(c.scaled(2))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("method.assign_narrowed_union", 0) >= 2
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "(*std::get<Circle*>(c)).area()" in cpp

    def test_isinstance_narrowed_stays_on_alias_arms(self):
        # BOUNDARY: an isinstance-narrowed receiver reads via its alias
        # machinery -- this arm must not capture it (it defers today).
        src = (self._BASE
               + "def use(u: Circle | Rect) -> None:\n"
               + "    if isinstance(u, Circle):\n"
               + "        print(u.area())\n"
               + "def main() -> None:\n"
               + "    use(Circle(2.0))\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert w.get("method.assign_narrowed_union", 0) == 0


class TestF3StrElemTuple:
    """An owned-str element joins the F3 tuple family (`tuple[str,
    Point]` -> `std::tuple<std::string, Point*>` borrow form): str is a
    value type spelling `std::string` in BOTH forms, so it rides the
    per-element conversions untouched -- return literal, call decl, arg
    pass and unpack all route."""
    _BASE = (
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def to_pair(p: Point) -> tuple[str, Point]:\n"
        "    return (str(p.x), p)\n")

    def test_str_elem_positions_route(self):
        src = (self._BASE
               + "def consume(t: tuple[str, Point]) -> Int32:\n"
               + "    return t[1].x\n"
               + "def main() -> None:\n"
               + "    p = Point(7)\n"
               + "    t = to_pair(p)\n"
               + "    print(consume(t))\n"
               + "    s, q = to_pair(p)\n"
               + "    print(s, q.x)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.btuple_literal", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert ("std::tuple<std::string, Point*>{"
                "::tpy::fixed_to_str<int32_t>(p.x), &(p)}" in cpp)

    def test_view_elem_stays_out(self):
        # BOUNDARY: a StrView element is a VIEW (borrowed), outside the
        # owned-str widening -- the family keeps deferring.
        src = ("from tpy import Int32, StrView\n"
               "class Point:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def to_pair(s: StrView, p: Point) -> tuple[StrView, Point]:\n"
               "    return (s, p)\n"
               "def main() -> None:\n"
               "    p = Point(7)\n"
               "    t = to_pair(\"k\", p)\n"
               "    print(t[0], t[1].x)\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the view-element tuple to keep deferring"


class TestValueOptViewFieldReturn:
    """An owned-str FIELD read at the value-opt view return (`return
    sub.value` through the narrowed ptr local -> `return sub->value;`):
    the owned member lands bare in the `std::optional<std::string>`
    return. VIEW fields type as views and stay out."""
    _BASE = (
        "from typing import Optional\n"
        "from tpy import StrView\n"
        "class Inner:\n"
        "    value: str\n"
        "    view: StrView\n"
        "    def __init__(self, value: str) -> None:\n"
        "        self.value = value\n"
        "        self.view = \"v\"\n"
        "class Outer:\n"
        "    _inner: Optional[Inner]\n"
        "    def __init__(self) -> None:\n"
        "        self._inner = Inner(\"x\")\n")

    def test_owned_field_routes(self):
        src = (self._BASE
               + "    def get(self) -> Optional[str]:\n"
               + "        sub = self._inner\n"
               + "        if sub is None:\n"
               + "            return None\n"
               + "        return sub.value\n"
               + "def main() -> None:\n"
               + "    o = Outer()\n"
               + "    print(o.get())\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.value_opt_view_field", 0) >= 1
        c2, modules2 = _compile(src)
        h, _cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "return sub->value;" in h

    def test_view_field_stays_ast(self):
        # BOUNDARY: a StrView member types as a view -- the row's owned
        # requirement keeps it deferring.
        src = (self._BASE
               + "    def get_view(self) -> Optional[StrView]:\n"
               + "        sub = self._inner\n"
               + "        if sub is None:\n"
               + "            return None\n"
               + "        return sub.view\n"
               + "def main() -> None:\n"
               + "    o = Outer()\n"
               + "    print(o.get_view())\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the view field to keep deferring"


class TestMilTupleCallAndMoveElem:
    """The ctor MIL's F1-tuple field: a storage-form-tuple-returning CALL
    stores bare (`t(make_pair(5))`, no tuple_to_storage lift), and an
    Own-param element at its LAST USE moves inside the literal wrap
    (`pair(::tpy::tuple_to_storage<S>(S{1, std::move(b)}))`). A
    copy()-of-Own-param element stays out (the copy row is plain-param
    only)."""
    _BASE = (
        "from tpy import Int32, Own, copy\n"
        "class Box:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "def make_pair(n: Int32) -> tuple[Int32, Own[Box]]:\n"
        "    return (n, Box(n))\n")

    def test_call_and_move_elem_route(self):
        src = (self._BASE
               + "class H:\n"
               + "    t: tuple[Int32, Box]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.t = make_pair(5)\n"
               + "class L:\n"
               + "    pair: tuple[Int32, Box]\n"
               + "    def __init__(self, b: Own[Box]) -> None:\n"
               + "        self.pair = (1, b)\n"
               + "def main() -> None:\n"
               + "    h = H()\n"
               + "    l = L(Box(2))\n"
               + "    print(h.t[0], l.pair[0])\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("mil.tuple_storage_call", 0) >= 1
        assert w.get("mil.ptr_tuple_literal", 0) >= 1
        c2, modules2 = _compile(src)
        h, _cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert ": t(make_pair(5))" in h
        assert "{1, std::move(b)}" in h

    def test_copy_own_param_elem_stays_ast(self):
        # BOUNDARY: copy() of an OWN param element is outside the copy
        # row's plain-param slice -- the ctor keeps deferring.
        src = (self._BASE
               + "class M:\n"
               + "    pair: tuple[Int32, Box]\n"
               + "    other: Box\n"
               + "    def __init__(self, b: Own[Box]) -> None:\n"
               + "        self.pair = (1, copy(b))\n"
               + "        self.other = b\n"
               + "def main() -> None:\n"
               + "    m = M(Box(3))\n"
               + "    print(m.pair[0], m.other.v)\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the copy-of-Own-param elem to defer"


class TestBaseInitCtorDefaultArg:
    """A materialized-default CTOR rvalue at the base-init slot
    (`: Base(a, Fixed(5), 2)` -- sema fills omitted defaults/kwargs into
    positional args before codegen): the target-less render is the bare
    prvalue; only scalar-literal/name ctor args admit (the base-init
    cell has no flush point)."""
    _BASE = (
        "from tpy import Int64\n"
        "from typing import Optional\n"
        "class Fixed:\n"
        "    off: Int64\n"
        "    def __init__(self, off: Int64) -> None:\n"
        "        self.off = off\n"
        "class Base:\n"
        "    v: Int64\n"
        "    def __init__(self, a: Int64, tz: \"Fixed | None\" = None,\n"
        "                 *, tag: Int64) -> None:\n"
        "        self.v = a * 100 + (0 if tz is None else tz.off) * 10 + tag\n")

    def test_materialized_ctor_default_routes(self):
        src = (self._BASE
               + "class Passing(Base):\n"
               + "    def __init__(self, a: Int64) -> None:\n"
               + "        super().__init__(a, Fixed(5), tag=2)\n"
               + "def main() -> None:\n"
               + "    print(Passing(1).v)\n"
               + "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        c2, modules2 = _compile(src)
        h, _cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert ": Base(a, Fixed(5), 2)" in h

    def test_record_name_ctor_arg_stays_ast(self):
        # BOUNDARY: a ctor arg wrapping a RECORD name is outside the
        # scalar-literal/name slice -- the ctor keeps deferring.
        src = (self._BASE
               + "class Holder(Base):\n"
               + "    def __init__(self, a: Int64, f: Fixed) -> None:\n"
               + "        super().__init__(a, Fixed(f.off), tag=2)\n"
               + "def main() -> None:\n"
               + "    print(Holder(1, Fixed(3)).v)\n"
               + "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the field-read ctor arg to defer"


class TestBytesViewLitTernary:
    """A bytes ternary mixing a VIEW arm and a bytes-LITERAL arm
    (`return b if b is not None else b"none"`): the raw mixed render --
    the span converts from the literal's owned temporary (alive to the
    full expression's end), and the owned sink wraps the WHOLE ternary in
    bytes_copy. A literal + CALL arm mix keeps deferring."""

    def test_view_lit_mix_routes(self):
        src = ("from typing import Optional\n"
               "def opt_or_default(b: Optional[bytes]) -> bytes:\n"
               "    return b if b is not None else b\"none\"\n"
               "def main() -> None:\n"
               "    print(opt_or_default(b\"xy\").decode())\n"
               "    print(opt_or_default(None).decode())\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ifexpr.bytes_view_lit", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert ("::tpy::bytes_copy((((b.has_value())) ? ((*b)) : "
                "(::tpy::bytes_literal_owned(\"none\", 4))))" in cpp)

    def test_plain_param_mix_stays_ast(self):
        # BOUNDARY: the PLAIN bytes-param flavor keeps deferring -- its
        # AST emit is uncompilable (BUGS.md), so the arm keys on the
        # value-opt-param deref flavor only.
        src = ("def f(c: bool, b: bytes) -> bytes:\n"
               "    return b if c else b\"x\"\n"
               "def main() -> None:\n"
               "    print(f(True, b\"ab\").decode())\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the plain-param mix to defer"

    def test_lit_call_mix_stays_ast(self):
        # BOUNDARY: a literal + CALL arm pair is outside the view+literal
        # slice -- the arm keeps deferring.
        src = ("from typing import Optional\n"
               "def base(b: Optional[bytes]) -> bytes:\n"
               "    return b if b is not None else b\"n\"\n"
               "def pick(b: Optional[bytes], flag: bool) -> bytes:\n"
               "    return b\"yes\" if flag else base(b)\n"
               "def main() -> None:\n"
               "    print(pick(b\"a\", True).decode())\n"
               "main()\n")
        _w, fallback = _assert_identical(src)
        assert fallback, "expected the literal+call mix to defer"


class TestDiscardSubscriptStmt:
    """The `_` unpack half desugars to a bare subscript statement
    evaluated for its bounds check (`::tpy::__getitem__(items, 0);`):
    the expr-stmt arm admits non-slice subscripts and the element gate's
    DISCARD position takes the record element lvalue (dropped, no alias
    escapes)."""

    def test_discard_unpack_routes(self):
        src = ("from tpy import Int32\n"
               "class Counter:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def main() -> None:\n"
               "    items = [Counter(1), Counter(2), Counter(3)]\n"
               "    c, _ = (items[2], items[0])\n"
               "    c.n += 5\n"
               "    print(items[2].n)\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("expr_stmt.subscript_discard", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "    ::tpy::__getitem__(items, 0);" in cpp

    def test_slice_discard_stays_ast(self):
        # BOUNDARY: a discarded SLICE subscript is outside the row
        # (slice_function_info machinery) -- keeps deferring.
        src = ("def main() -> None:\n"
               "    xs = [1, 2, 3]\n"
               "    _ = xs[0:2]\n"
               "    print(len(xs))\n"
               "main()\n")
        w, _fallback = _assert_identical(src)
        assert w.get("expr_stmt.subscript_discard", 0) == 0


class TestOwnTupleParamReturn:
    """`return p;` of an Own-element tuple PARAM (`std::tuple<A, A>&& p`)
    at the widened value-tuple return: the rvalue-ref binding is already
    storage form, so the bare name rides the generic tail -- the
    storage-local row's param leg."""

    def test_param_return_routes(self):
        src = ("from tpy import Int32, Own\n"
               "class A:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def relay(p: tuple[Own[A], Own[A]]) -> tuple[Own[A], Own[A]]:\n"
               "    return p\n"
               "def main() -> None:\n"
               "    a, b = relay((A(3), A(4)))\n"
               "    print(a.n, b.n)\n"
               "main()\n")
        w, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert w.get("ret.own_tuple_param", 0) >= 1
        c2, modules2 = _compile(src)
        _h, cpp = c2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=True, thir_codegen=True))
        assert "    return p;" in cpp


class TestRecordGetitemGlobalSlotReceiver:
    """A pointer-slot GLOBAL receiver at a record __getitem__ derefs
    (`(*g)[1]` -- the name arm's pointer render) at top level and in
    function bodies alike; a branch-hoisted pointer LOCAL receiver keeps
    the exclusion."""

    _PRE = (
        "from tpy import Int32\n"
        "class Grid:\n"
        "    base: Int32\n"
        "    def __init__(self, base: Int32) -> None:\n"
        "        self.base = base\n"
        "    def __getitem__(self, i: Int32) -> Int32:\n"
        "        return self.base + i\n")

    def test_global_slot_receiver_routes(self):
        src = self._PRE + (
            "g = Grid(10)\n"
            "print(g[1])\n"
            "def fn_body() -> None:\n"
            "    print(g[2])\n"
            "fn_body()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*g)[1]" in cpp
        assert "(*g)[2]" in cpp

    def test_pointer_local_receiver_defers(self):
        from .testutil import _assert_byte_identical, _lower_ctx
        src = self._PRE + (
            "def ptr_local(flag: bool) -> None:\n"
            "    if flag:\n"
            "        h = Grid(1)\n"
            "    else:\n"
            "        h = Grid(2)\n"
            "    print(h[3])\n"
            "ptr_local(True)\n")
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "ptr_local") is None

"""Generic-call Own-slot arg rows: the substituted-slot twins of the
concrete free-call gate's Own family, plus the literal-type resolution the
BigInt slots need. Rows pinned here:

 * a literal-typed scalar NAME into a substituted `Own[scalar]` slot keeps
   the copy+move temp (`auto __tmp_N = v;` -- the IntLiteralType use-site
   resolves like `_resolved_scalar`);
 * an int literal into an `Own[T]`-resolved-BigInt slot renders BARE
   (`sink<::tpy::BigInt>(h, 42)` -- gen_call_arg threads the RAW Own
   wrapper, on which every literal target predicate is False), generic and
   concrete alike;
 * `None` into a substituted unit slot renders the bare `std::monostate{}`;
 * a tuple literal into a substituted `Own[value-tuple]` slot takes the
   spelled value render; a pointer-repr element takes the CONSUMING
   storage lift (tuple_to_storage_move over the borrow build);
 * a str literal into a substituted `Own[str]` slot binds bare; an owned
   str NAME takes the copy+move temp instead;
 * an owned-`bytes` CALL rvalue into a substituted `Own[bytes]` slot binds
   bare, while every other bytes source at that slot keeps rejecting;
 * the `Own[@dynamic P]` erasure rows fire on generic callees too -- the
   structural-conformer `::tpy::make_adapter<P>(...)` wrap and the
   async-factory wrap at an `Own[Cancellable[T]]` slot.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (
    _reject_tally, _lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _thir_ctx)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_SINK = (
    "from tpy import Own\n"
    "def sink[T](xs: list[T], x: Own[T]) -> None:\n"
    "    xs.append(x)\n"
)


class TestGenericOwnScalarSlot:
    def test_literal_typed_name_keeps_the_copy_temp(self):
        # The for-var over a literal list reads as an IntLiteral-typed NAME;
        # T resolves BigInt from the list arg, and the arg keeps the same
        # `auto` copy+move temp a concrete Own slot hoists.
        src = _SINK + (
            "def f() -> None:\n"
            "    h: list[int] = []\n"
            "    for v in [5, 3]:\n"
            "        sink(h, v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "auto __tmp_1 = v;" in body
        assert "sink<::tpy::BigInt>(h, std::move(__tmp_1))" in body
        assert faces["argtemp.own_copy"] >= 1
        _assert_byte_identical(src)

    def test_int_literal_renders_bare(self):
        # Sema leaves the literal unwrapped at a BigInt slot, and the Own
        # wrapper suppresses the ctor retype -- bare `42` on both paths.
        src = _SINK + (
            "def f() -> None:\n"
            "    h: list[int] = []\n"
            "    sink(h, 42)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "sink<::tpy::BigInt>(h, 42)" in body
        assert faces["own.scalar_rvalue"] >= 1
        _assert_byte_identical(src)

    def test_narrowed_scalar_name_routes_via_alias(self):
        # A NARROWED scalar name is NOT a boundary here: the TypeParamRef
        # scalar row reads the declared binding, the narrowed read renames
        # to its extraction alias on both paths, and the render matches --
        # pinned as routed rather than assumed rejecting.
        src = _SINK + (
            "from tpy import Int32\n"
            "def f(x: Int32 | None) -> None:\n"
            "    h: list[Int32] = []\n"
            "    if x is not None:\n"
            "        sink(h, x)\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_concrete_own_bigint_literal_renders_bare(self):
        # The concrete twin: the tail's Own-slot literal skip is
        # path-blind, so a plain free call renders the same bare literal.
        src = (
            "from tpy import Own\n"
            "def take_big(x: Own[int]) -> None:\n"
            "    print(x)\n"
            "def f() -> None:\n"
            "    take_big(42)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "take_big(42)" in body
        assert faces["own.scalar_rvalue"] >= 1
        _assert_byte_identical(src)


class TestGenericOwnUnitSlot:
    def test_none_renders_monostate(self):
        src = (
            "from tpy import Own\n"
            "def unit[T](x: Own[T]) -> None:\n"
            "    pass\n"
            "def f() -> None:\n"
            "    unit[None](None)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "unit<std::monostate>(std::monostate{})" in body
        assert faces["call.none_unit"] >= 1
        _assert_byte_identical(src)


class TestGenericOwnTupleSlot:
    def test_value_tuple_literal_spells_the_value_render(self):
        src = _SINK + (
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    pq: list[tuple[Int32, str]] = []\n"
            "    sink(pq, (3, \"third\"))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert ("sink<std::tuple<int32_t, std::string>>(pq, "
                "std::tuple<int32_t, std::string>{3, \"third\"})") in body
        assert faces["btuple.value_arg"] >= 1
        _assert_byte_identical(src)

    def test_pointer_repr_tuple_literal_routes_storage_lift(self):
        # A record element makes the tuple pointer-repr: the CONSUMING
        # storage lift (borrow build with per-element moves +
        # tuple_to_storage_move) now routes it -- the fence's
        # "different render" is the render the gate admission mirrors.
        src = (
            "from tpy import Own, Int32\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def gen_take[T](x: Own[T]) -> None:\n"
            "    print(\"g\")\n"
            "def f() -> None:\n"
            "    m = Node(3)\n"
            "    gen_take((m, 4))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("arg.own_btuple_literal", 0) >= 1
        _assert_byte_identical(src)


class TestGenericOwnStrSlot:
    def test_str_literal_binds_bare(self):
        src = _SINK + (
            "def f() -> None:\n"
            "    words: list[str] = []\n"
            "    sink(words, \"cherry\")\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "sink<std::string>(words, \"cherry\")" in body
        assert faces["arg.own_str_slot"] >= 1
        _assert_byte_identical(src)

    def test_owned_str_name_takes_the_copy_temp(self):
        # An owned STORAGE str local at the SUBSTITUTED Own[str] slot takes
        # the copy+move temp, not the literal row's bare bind -- the owned
        # form, told from a view by `declared` + `param_names`.
        src = _SINK + (
            "def f() -> None:\n"
            "    words: list[str] = []\n"
            "    w: str = \"apple\" + \"x\"\n"
            "    sink(words, w)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("argtemp.own_str", 0) >= 1
        body = _body(thir, "f")
        assert "std::string __tmp_1{w};" in body
        assert "sink<std::string>(words, std::move(__tmp_1))" in body
        _assert_byte_identical(src)


class TestTypeParamDefaultConstruct:
    def test_kwargs_gap_fill_renders_brace_init(self):
        src = (
            "from tpy import Int32\n"
            "def three[T](a: T, b: T = T(), c: T = T()) -> None:\n"
            "    print(a)\n"
            "def f() -> None:\n"
            "    three[Int32](10, c=5)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "int32_t{}" in body
        assert faces["call.tparam_default_construct"] >= 1
        _assert_byte_identical(src)

    def test_record_default_spells_the_record_brace(self):
        # The adjacent non-scalar shape: a RECORD-resolved T() spells the
        # record's brace-init through the same arm (no reject sibling
        # exists -- the arm is unconditional like the AST's).
        src = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.x = 0\n"
            "def three[T](a: T, b: T = T(), c: T = T()) -> None:\n"
            "    print(a)\n"
            "def f() -> None:\n"
            "    three[Point](Point(), c=Point())\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Point{}" in body
        assert faces["call.tparam_default_construct"] >= 1
        _assert_byte_identical(src)


class TestGenericOpenProtocolSlot:
    _AW = (
        "from tpy import Int32, Own\n"
        "from tpy.coro import Awaitable, Poll, Waker, poll_once, "
        "poll_pending\n"
        "class MyTask[T]:\n"
        "    def __poll__(self, w: Waker) -> Own[Poll[T]]:\n"
        "        return poll_pending()\n"
        "def drive[T](aw: Awaitable[T]) -> Own[Poll[T]]:\n"
        "    return poll_once(aw)\n"
    )

    def test_conformer_name_binds_bare(self):
        # Inside a generic body the slot protocol stays open; the conformer
        # NAME still binds the monomorphized template param bare.
        src = self._AW + (
            "def use[T](t: MyTask[T]) -> Own[Poll[T]]:\n"
            "    return drive(t)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "use")
        assert "drive<T>(t)" in body
        assert faces["call.generic_open_proto_name"] >= 1
        _assert_byte_identical(src)

    def test_dynamic_open_slot_stays_ast(self):
        # The row excludes @dynamic protocols (their adapter temps are a
        # different render) -- the boundary the arm's own guard names.
        src = (
            "from typing import Protocol\n"
            "from tpy import Int32, dynamic\n"
            "@dynamic\n"
            "class Holder[T](Protocol):\n"
            "    def get(self) -> T: ...\n"
            "class Bag[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: T) -> None:\n"
            "        self.v = v\n"
            "    def get(self) -> T:\n"
            "        return self.v\n"
            "def read[T](s: Holder[T]) -> T:\n"
            "    return s.get()\n"
            "def use[T](b: Bag[T]) -> T:\n"
            "    return read(b)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.generic_arg_slot")


class TestNestedGenericCallTempRide:
    def test_ctor_arg_nested_generic_flushes_at_statement(self):
        # The inner generic call's scalar ref-slot temp lands at the AST's
        # pre-statement flush even though the call sits nested inside the
        # ctor arg (`nested_temps` rides the statement flush in).
        src = (
            "from tpy import Int32, Own\n"
            "class Box[T]:\n"
            "    val: T\n"
            "    def __init__(self, val: Own[T]) -> None:\n"
            "        self.val = val\n"
            "class Holder:\n"
            "    box: Box[Int32]\n"
            "    def __init__(self, box: Own[Box[Int32]]) -> None:\n"
            "        self.box = box\n"
            "def wrap[T](v: T) -> Own[Box[T]]:\n"
            "    return Box[T](v)\n"
            "def f() -> None:\n"
            "    h = Holder(wrap(Int32(42)))\n"
            "    print(h.box.val)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "int32_t __tmp_1 = 42;" in body
        assert "Holder(wrap<int32_t>(__tmp_1))" in body
        # The inner temp is the generic ref-slot argtemp -- witnessing it
        # proves the ride reached the nested call's own temp row.
        assert faces["argtemp.generic_ref_slot"] >= 1
        _assert_byte_identical(src)

    def test_nested_temp_in_comp_filter_routes(self):
        # Converted fence: comp FILTERS are flush positions now (temps_ok on
        # the condition lowering; the emit's checkpointed loop-body-indent
        # flush -- see test_thir_wave_comp_cond_temps.py), so the nested
        # temp-needing call routes byte-identically.
        src = (
            "from tpy import Int32, Own\n"
            "class Box[T]:\n"
            "    val: T\n"
            "    def __init__(self, val: Own[T]) -> None:\n"
            "        self.val = val\n"
            "def wrap[T](v: T) -> Own[Box[T]]:\n"
            "    return Box[T](v)\n"
            "def ok(b: Box[Int32]) -> bool:\n"
            "    return b.val > 0\n"
            "def f(xs: list[Int32]) -> None:\n"
            "    ys = [x for x in xs if ok(wrap(Int32(1)))]\n"
            "    print(len(ys))\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


_PET = (
    "from typing import Protocol\n"
    "from tpy import dynamic, Own, Int32\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Dog:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n"
    "        return self.label\n"
)


class TestGenericDynOwnRows:
    def test_structural_conformer_wraps_on_a_generic_callee(self):
        # The concrete `Own[Pet]` slot sits in a GENERIC callee's param
        # list, so admission runs through the substituted-slot tail; the
        # render is the same verdict-keyed make_adapter wrap.
        src = _PET + (
            "def keep[T](p: Own[Pet], tag: T) -> None:\n"
            "    pass\n"
            "def f() -> None:\n"
            "    n: Int32 = 1\n"
            "    keep[Int32](Dog(label=\"R\"), n)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "::tpy::make_adapter<Pet>(Dog(" in body
        assert faces["dynown.adapter_conformer"] >= 1
        _assert_byte_identical(src)

    def test_list_param_lambda_routes_with_len_body(self):
        # The widened lambda param family: a list param spells
        # `std::vector<T>&` via to_cpp_param and a len() body routes.
        src = (
            "from tpy import Int32\n"
            "from functools import reduce\n"
            "def f() -> None:\n"
            "    grids: list[list[Int32]] = [[1], [2, 3]]\n"
            "    init: Int32 = 0\n"
            "    total = reduce(lambda acc, xs: acc + len(xs), grids, init)\n"
            "    print(total)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "std::vector<int32_t>& xs" in body
        assert faces["expr.lambda"] >= 1
        _assert_byte_identical(src)

    def test_list_concat_lambda_body_routes(self):
        # A list-concat lambda body (`acc + [x]`) routes via the
        # binop.list_concat leg (see TestListConcatBinop in
        # test_thir_containers.py for the family's own pins).
        src = (
            "from tpy import Int32\n"
            "from functools import reduce\n"
            "def f() -> None:\n"
            "    init: list[Int32] = [100]\n"
            "    nums: list[Int32] = [1, 2, 3]\n"
            "    built = reduce(lambda acc, x: acc + [x], nums, init)\n"
            "    print(built)\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_async_factory_wraps_at_a_cancellable_slot(self):
        src = (
            "from tpy import Own, Int32\n"
            "from tpy.coro import Cancellable\n"
            "def hold[T](c: Own[Cancellable[T]]) -> Int32:\n"
            "    return 0\n"
            "async def once() -> Int32:\n"
            "    return 1\n"
            "def f() -> None:\n"
            "    print(hold(once()))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "make_adapter<::tpystd::coro::Cancellable<int32_t>>(once())" \
            in body
        assert faces["call.coro_factory_adapter"] >= 1
        _assert_byte_identical(src)


class TestGenericOptOwnSlot:
    """A substituted `Own[T] | None` slot: a record NAME moves bare
    (`take_optional<Box>(std::move(b), 99)` -- the optional's converting
    ctor absorbs the move; the name may arrive under the Own-lift coerce
    on an inferred-targ call), and `None` renders `std::nullopt`. A
    REUSED (non-last-use) name keeps rejecting via the move verdict."""

    _PRE = ("from tpy import Int32, Own\n"
            "class Box:\n"
            "    value: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.value = 0\n"
            "def take_optional[T](item: Own[T] | None, fallback: Int32)"
            " -> Int32:\n"
            "    return fallback\n")

    def test_move_and_none_route(self):
        src = (self._PRE
               + "def main() -> None:\n"
               + "    b = Box()\n"
               + "    print(take_optional(b, 99))\n"
               + "    print(take_optional[Box](None, 77))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("move.opt_own_last_use", 0) >= 1
        _assert_byte_identical(src)

    def test_reused_name_stays_ast(self):
        # `b` is read after the call, so the move verdict fails -- the
        # copy shape is unwitnessed and the body falls back.
        src = (self._PRE
               + "def main() -> None:\n"
               + "    b = Box()\n"
               + "    print(take_optional(b, 99))\n"
               + "    print(b.value)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.opt_own_copy")


class TestGenericOwnBytesSlot:
    """An owned-`bytes` CALL rvalue at a substituted `Own[bytes]` slot binds
    BARE: a prvalue has nothing to move from and owes no view->owned copy, so
    the Own cascade emits neither a temp nor a convert. The row is slot-keyed
    and blind to the callee being generic, which is why the concrete ladder's
    cell carries over unchanged.

    Every neighbouring bytes source at the SAME slot keeps rejecting, each
    for its own render: an owned LOCAL rides the copy+move temp, a view-form
    source (param name, view local, view-returning call, slice) owes the
    `bytes_copy` materialize, and a literal renders owned in place."""

    _PRE = ("from tpy import Own, Int32, BytesView\n"
            "from tpy.coro import Poll, poll_ready\n"
            "class Src:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def recv(self, k: Int32) -> bytes:\n"
            "        return b\"ab\"\n"
            "    def peek(self, k: Int32) -> BytesView:\n"
            "        return b\"ab\"\n")

    _MAIN = ("def main() -> None:\n"
             "    s = Src(2)\n"
             "    q = run(s)\n"
             "main()\n")

    def test_owned_bytes_call_rvalue_routes(self):
        src = (self._PRE
               + "def run(s: Src) -> Own[Poll[bytes]]:\n"
               + "    return poll_ready(s.recv(s.n))\n"
               + self._MAIN)
        _assert_routes_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.bytes_owned_call", 0) >= 1

    def test_owned_bytes_call_rvalue_renders_bare(self):
        src = (self._PRE
               + "def run(s: Src) -> Own[Poll[bytes]]:\n"
               + "    return poll_ready(s.recv(s.n))\n"
               + self._MAIN)
        _hpp, cpp = _assert_byte_identical(src)
        assert ("::tpystd::coro::poll_ready<std::vector<uint8_t>>("
                "s.recv(s.n))" in cpp)

    def test_view_returning_call_stays_ast(self):
        # BOUNDARY: the slot is pinned to `bytes` by the explicit type arg,
        # so this really is the SAME slot -- and the view source still owes
        # the `bytes_copy` materialize the bare bind would drop.
        src = (self._PRE
               + "def run(s: Src) -> Own[Poll[bytes]]:\n"
               + "    return poll_ready[bytes](s.peek(s.n))\n"
               + self._MAIN)
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.generic_arg_shape")

    def test_owned_bytes_local_name_stays_ast(self):
        # BOUNDARY: an owned STORAGE local at the same slot rides the AST's
        # copy+move-temp cascade -- the row is the RVALUE face only.
        src = (self._PRE
               + "def run(s: Src) -> Own[Poll[bytes]]:\n"
               + "    b = s.recv(s.n)\n"
               + "    return poll_ready[bytes](b)\n"
               + self._MAIN)
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.generic_arg_shape")

    def test_str_sibling_call_rvalue_stays_ast(self):
        # BOUNDARY: the str family's owned-slot rows are not in this sink at
        # all (only its literal cell is), so the str twin of the admitted
        # shape keeps rejecting -- admitting bytes did not admit "the
        # owned-slot rvalue" generally.
        src = (self._PRE
               + "def name(s: Src) -> str:\n"
               + "    return \"ab\"\n"
               + "def run(s: Src) -> Own[Poll[str]]:\n"
               + "    return poll_ready[str](name(s))\n"
               + self._MAIN)
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.generic_arg_shape")


class TestGenericOwnCompositeSlot:
    """An owned NAME at an `Own[T]` slot whose SUBSTITUTED payload is still
    composite in T (`poll_ready(empty)` with `empty: list[T]`).

    The open slot's bare `T` never matches a composite binding, so the
    temp-free type-param row cannot reach the shape; the substituted payload
    does, and it is the one `_lower_call_arg` renders against."""

    _SINK = (
        "from tpy import Int32, Own\n"
        "def take_own[T](v: Own[T]) -> Own[T]:\n"
        "    return v\n"
    )

    def test_owned_local_at_its_last_use_moves(self):
        src = self._SINK + (
            "def from_local[T]() -> Own[list[T]]:\n"
            "    empty: list[T] = []\n"
            "    return take_own(empty)\n"
            "def main() -> None:\n"
            "    print(len(from_local[Int32]()))\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "take_own<std::vector<T>>(std::move(empty))" in hpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.generic_own_composite_slot", 0) >= 1

    def test_still_live_owned_local_keeps_the_copy_temp(self):
        # The other half of the same row: a source still live after the call
        # owes the AST's defensive `auto __tmp_N = ys;` copy, which lowering
        # picks from the same movability facts the gate does not consult.
        src = self._SINK + (
            "def from_local[T](seed: T) -> Own[list[T]]:\n"
            "    ys: list[T] = [seed]\n"
            "    r = take_own(ys)\n"
            "    ys.append(seed)\n"
            "    return r\n"
            "def main() -> None:\n"
            "    print(len(from_local(1)))\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "auto __tmp_1 = ys;" in hpp
        assert "take_own<std::vector<T>>(std::move(__tmp_1))" in hpp

    def test_dict_payload_rides_the_same_row(self):
        src = self._SINK + (
            "def from_local[K, V]() -> Own[dict[K, V]]:\n"
            "    m: dict[K, V] = {}\n"
            "    return take_own(m)\n"
            "def main() -> None:\n"
            "    print(len(from_local[Int32, Int32]()))\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "take_own<::tpy::ordered_map<K, V>>(std::move(m))" in hpp

    def test_generic_record_payload_rides_the_same_row(self):
        src = self._SINK + (
            "class Pair[T]:\n"
            "    a: T\n"
            "    b: T\n"
            "    def __init__(self, a: Own[T], b: Own[T]) -> None:\n"
            "        self.a = a\n"
            "        self.b = b\n"
            "def from_local[T](x: Own[T], y: Own[T]) -> Own[Pair[T]]:\n"
            "    p = Pair(x, y)\n"
            "    return take_own(p)\n"
            "def main() -> None:\n"
            "    q = from_local(1, 2)\n"
            "    print(q.a, q.b)\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "take_own<Pair<T>>(std::move(p))" in hpp

    def test_nocopy_payload_moves_rather_than_copies(self):
        # A payload whose copy would not compile: the row must reach the
        # temp-free move, not the copy temp, at a last use.
        src = self._SINK + (
            "from tpy import nocopy\n"
            "@nocopy\n"
            "class Res[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: Own[T]) -> None:\n"
            "        self.v = v\n"
            "def from_local[T](x: Own[T]) -> Own[Res[T]]:\n"
            "    r = Res(x)\n"
            "    return take_own(r)\n"
            "def main() -> None:\n"
            "    print(from_local(5).v)\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "take_own<Res<T>>(std::move(r))" in hpp

    def test_borrowed_param_at_the_same_slot_stays_ast(self):
        # BOUNDARY, and a wrong-code one: a param binds in BORROW form, so
        # the slot resolves through the ref wrapper and the AST hoists a
        # defensive copy before the move. Admitting the name here passes a
        # live borrow straight into a move-consuming slot.
        src = self._SINK + (
            "def from_param[T](xs: list[T]) -> Own[list[T]]:\n"
            "    r = take_own(xs)\n"
            "    return r\n"
            "def main() -> None:\n"
            "    print(len(from_param([1, 2])))\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call", "call.generic_arg_slot")

    def test_field_read_at_the_same_slot_stays_ast(self):
        # BOUNDARY: the field twin of the param row -- same borrow form,
        # same dropped copy if admitted.
        src = self._SINK + (
            "class Holder[T]:\n"
            "    items: list[T]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = []\n"
            "    def from_field(self) -> Own[list[T]]:\n"
            "        return take_own(self.items)\n"
            "def main() -> None:\n"
            "    h = Holder[Int32]()\n"
            "    print(len(h.from_field()))\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call", "call.generic_arg_slot")

    def test_flushless_position_stays_ast(self):
        # BOUNDARY: a condition is no flush point, so the copy temp has
        # nowhere to land -- without the flush guard THIR drops it and moves
        # a name the AST leaves live.
        src = (
            "from tpy import Int32, Own\n"
            "def take_len[T](v: Own[list[T]]) -> Int32:\n"
            "    return len(v)\n"
            "def in_cond[T](seed: T) -> Int32:\n"
            "    ys: list[T] = [seed]\n"
            "    if take_len(ys) > 0 and len(ys) > 0:\n"
            "        return 1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(in_cond(1))\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.if", "call.generic_arg_slot")


class TestGenericOwnCtorSlotCallRvalue:
    """A T-returning call rvalue at a bare `Own[T]` RECORD-CTOR slot inside a
    generic body (`Box(p.value())`): the prvalue binds the `T&&` slot with
    no temp and no move wrap, exactly as at the method ladder's same slot."""

    _PRE = (
        "from tpy import Int32, Own\n"
        "from tpy.coro import Poll\n"
        "class Box2[T]:\n"
        "    v: T\n"
        "    def __init__(self, value: Own[T]) -> None:\n"
        "        self.v = value\n"
    )
    _MAIN = (
        "def main() -> None:\n"
        "    b = wrap(Poll[Int32].ready(7))\n"
        "    print(b.v)\n"
        "main()\n"
    )

    def test_call_rvalue_binds_the_ctor_slot_bare(self):
        src = self._PRE + (
            "def wrap[T](p: Own[Poll[T]]) -> Own[Box2[T]]:\n"
            "    return Box2(p.value())\n"
        ) + self._MAIN
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "Box2<T>(std::move(p).value())" in hpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.own_tparam_call_rvalue", 0) >= 1

    def test_borrow_returning_callee_keeps_rejecting(self):
        # BOUNDARY: `is_rvalue_source` answers True for a borrowing callee
        # too, so the row keys on the DECLARED return instead. The AST binds
        # the borrowed `val_or_ref_t<T>` result straight into the `T&&`
        # slot, which is ill-formed at a reference-type instantiation --
        # nothing may route it, byte-identical or not.
        src = (
            "from tpy import Int32, Own\n"
            "class Box2[T]:\n"
            "    v: T\n"
            "    def __init__(self, value: Own[T]) -> None:\n"
            "        self.v = value\n"
            "class Holder[T]:\n"
            "    item: T\n"
            "    def __init__(self, it: Own[T]) -> None:\n"
            "        self.item = it\n"
            "    def get(self) -> T:\n"
            "        return self.item\n"
            "def wrap[T](h: Holder[T]) -> Own[Box2[T]]:\n"
            "    return Box2(h.get())\n"
            "def main() -> None:\n"
            "    h = Holder(3)\n"
            "    print(wrap(h).v)\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.ctor_arg.own_generic")

    def test_container_literal_element_position_routes_too(self):
        # The element of a container literal still carries the enclosing
        # statement's flush right, so it gates as a direct ctor position --
        # the bare bind is position-independent either way.
        src = self._PRE + (
            "def wrap[T](p: Own[Poll[T]]) -> Own[list[Box2[T]]]:\n"
            "    return [Box2(p.value())]\n"
            "def main() -> None:\n"
            "    bs = wrap(Poll[Int32].ready(7))\n"
            "    print(len(bs))\n"
            "main()\n"
        )
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "{Box2<T>(std::move(p).value())}" in hpp

    def test_the_caller_type_param_need_not_be_spelled_T(self):
        # The row pairs the slot's open T with the ARGUMENT's, and the
        # render is settled by the rvalue-ness rather than the spelling --
        # so a caller whose own param is named differently routes too.
        src = self._PRE + (
            "def wrap[U](p: Own[Poll[U]]) -> Own[Box2[U]]:\n"
            "    return Box2(p.value())\n"
        ) + self._MAIN
        hpp, _cpp = _assert_routes_byte_identical(src)
        assert "Box2<U>(std::move(p).value())" in hpp

"""The Own[ptr-Optional tuple] consuming rows: tuple_to_storage_move over
the borrow tuple with per-element moves (append), the non-move
tuple_to_storage call lift (append/setitem), the subscript whole-element
pass, the unpack's subscript-wrap source, and the matching borrow-param
bare bind."""

from __future__ import annotations

from .testutil import (
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn,
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile, _entry,
)
from ..codegen_cpp import CodeGenOptions

_P = (
    "from tpy import Int32, copy\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestOwnBtupleAppend:
    def test_literal_shapes_route(self):
        # The six per-element ownership paths: last-use lvalues (moved
        # pointers), mixed lvalue+rvalue (tuple_value_to_borrow), None
        # (nullptr), copy() (the copy-construct row inside the ladder).
        src = (_P
               + "def f() -> None:\n"
               + "    pairs: list[tuple[P | None, P | None]] = []\n"
               + "    a = P(1)\n"
               + "    b = P(2)\n"
               + "    pairs.append((a, b))\n"
               + "    c = P(3)\n"
               + "    pairs.append((c, P(4)))\n"
               + "    pairs.append((P(5), None))\n"
               + "    pairs.append((None, None))\n"
               + "    keep = P(6)\n"
               + "    pairs.append((copy(keep), None))\n"
               + "    print(keep.x, len(pairs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("arg.own_btuple_literal", 0) >= 5
        _assert_byte_identical(src)

    def test_call_source_lifts_nonmove(self):
        # `pairs.append(make_pair(a, b))` -> the NON-move tuple_to_storage
        # (moving from a returned pointer would alias caller storage), and
        # the dict-setitem twin.
        src = (_P
               + "def make_pair(left: P, right: P) "
               + "-> tuple[P | None, P | None]:\n"
               + "    return (left, right)\n"
               + "def f() -> None:\n"
               + "    a = P(1)\n"
               + "    b = P(2)\n"
               + "    pairs: list[tuple[P | None, P | None]] = []\n"
               + "    pairs.append(make_pair(a, b))\n"
               + "    d: dict[Int32, tuple[P | None, P | None]] = {}\n"
               + "    d[Int32(0)] = make_pair(a, b)\n"
               + "    print(len(pairs), len(d))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("arg.own_btuple_call", 0) >= 1
        assert w.get("setitem.btuple_call", 0) >= 1
        _assert_byte_identical(src)

    def test_subscript_sources_route(self):
        # The whole-element pass (`pairs2.append(pairs[0])`, bare
        # __getitem__) and the unpack's subscript-wrap source
        # (`a0, b0 = pairs[0]` -> tuple_to_pointer over the element read).
        src = (_P
               + "def f() -> None:\n"
               + "    pairs: list[tuple[P | None, P | None]] = []\n"
               + "    pairs.append((P(1), P(2)))\n"
               + "    pairs2: list[tuple[P | None, P | None]] = []\n"
               + "    pairs2.append(pairs[0])\n"
               + "    a0, b0 = pairs[0]\n"
               + "    if a0 is not None:\n"
               + "        print(a0.x)\n"
               + "    print(len(pairs2))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("stmt.tuple_unpack.subscript_wrap_source", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_param_call_binds_bare(self):
        # `show(f2(t1, t2))`: the borrow-tuple call result binds the
        # matching `const std::tuple<const T*, ..>&` param bare.
        src = (_P
               + "def make_pair(left: P, right: P) "
               + "-> tuple[P | None, P | None]:\n"
               + "    return (left, right)\n"
               + "def show(p: tuple[P | None, P | None]) -> None:\n"
               + "    a, b = p\n"
               + "    if a is not None:\n"
               + "        print(a.x)\n"
               + "def f() -> None:\n"
               + "    t1 = P(1)\n"
               + "    t2 = P(2)\n"
               + "    show(make_pair(t1, t2))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("call.btuple_pass", 0) >= 1
        _assert_byte_identical(src)

    def test_call_borrow_unpack_routes(self):
        # `a, b = both(t1, t2)`: the borrow-form call result captures via
        # the plain RVALUE bind (`auto __tup_N = both(t1, t2);`), opt_ptr
        # targets read the pointers directly (`P* a = std::get<0>(..)`).
        src = (_P
               + "def both(a: P, b: P) -> tuple[P | None, P | None]:\n"
               + "    return (a, b)\n"
               + "def f() -> None:\n"
               + "    t1 = P(1)\n"
               + "    t2 = P(2)\n"
               + "    a, b = both(t1, t2)\n"
               + "    if a is not None:\n"
               + "        print(a.x)\n"
               + "    if b is not None:\n"
               + "        print(b.x)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("stmt.tuple_unpack.call_borrow_source", 0) >= 1
        _assert_byte_identical(src)

    def test_owned_tuple_method_unpack_routes(self):
        # `conn, _ = srv.accept()` at `tuple[Own[socket], tuple[str,
        # Int32]]`: the owned-tuple move-out family admits a nested
        # VALUE-tuple element (the discarded address pair), and the
        # record-method result gate admits the owned-tuple result at the
        # tuple-source sink ONLY.
        src = ("import socket\n"
               "def f() -> None:\n"
               "    srv = socket.create_server((\"127.0.0.1\", 0))\n"
               "    conn, _ = srv.accept()\n"
               "    print(\"x\")\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_owned_tuple_method_decl_stays_ast(self):
        # The fence: an Own-tuple result at a DECL sink is an unrouted
        # slot -- the owned_tuple_ret_ok flag must not leak past the
        # tuple-source position.
        src = ("import socket\n"
               "def f() -> None:\n"
               "    srv = socket.create_server((\"127.0.0.1\", 0))\n"
               "    pair = srv.accept()\n"
               "    print(\"x\")\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_plain_ref_tuple_element_takes_the_storage_move(self):
        # A plain-record (non-Optional) element tuple: the united method-arg
        # sink reaches a stub slot with the record listing's `tuple_literal`
        # cell, and the same storage-move lift renders it. The borrow
        # checker owns whether the element may move -- a NON-last-use `a`
        # is refused there, not here.
        src = (_P
               + "def f() -> None:\n"
               + "    pairs: list[tuple[P, Int32]] = []\n"
               + "    a = P(1)\n"
               + "    pairs.append((a, 2))\n"
               + "    print(len(pairs))\n")
        assert _fn(_lower_ctx(src), "f") is not None

    def test_setitem_literal_value_routes_nonmove(self):
        # A tuple LITERAL setitem value takes the NON-move lift with plain
        # `&(a)` lifts (the dict store copies -- the AST's setitem path
        # never moves elements), and the same-tuple element read passes
        # bare (`d2[0] = d[0]`).
        src = (_P
               + "def f() -> None:\n"
               + "    a = P(1)\n"
               + "    b = P(2)\n"
               + "    d: dict[Int32, tuple[P | None, P | None]] = {}\n"
               + "    d[Int32(0)] = (a, b)\n"
               + "    d[Int32(1)] = (P(3), None)\n"
               + "    d2: dict[Int32, tuple[P | None, P | None]] = {}\n"
               + "    d2[Int32(0)] = d[Int32(0)]\n"
               + "    print(a.x, len(d), len(d2))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("setitem.btuple_literal", 0) >= 2
        assert w.get("setitem.btuple_elem_pass", 0) >= 1
        _assert_byte_identical(src)

    def test_readonly_param_subscript_unpack_stays_ast(self):
        # The Critical fence: a readonly-typed container PARAM is a const
        # binding -- the non-const tuple_to_pointer spelling would be a
        # hard C++ error, so the subscript-wrap source must defer
        # (const_locals covers locals, _param_is_const the params).
        src = ("from tpy import Int32, readonly\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def show(pairs: readonly[list[tuple[P | None, P | None]]]"
               ") -> None:\n"
               "    a0, b0 = pairs[0]\n"
               "    if a0 is not None:\n"
               "        print(a0.x)\n")
        assert _fn(_lower_ctx(src), "show") is None

    def test_setitem_name_value_stays_ast(self):
        # The boundary: a whole-tuple NAME value at the btuple setitem slot
        # keeps rejecting (only literal / call / same-tuple element sources
        # carry mirrored renders).
        src = (_P
               + "def f() -> None:\n"
               + "    d: dict[Int32, tuple[P | None, P | None]] = {}\n"
               + "    pair = (P(1), None)\n"
               + "    d[0] = pair\n"
               + "    print(len(d))\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestPlainRecordBtupleLiteral:
    """The plain-record sibling of the all-Optional literal row: an
    ALL-RVALUE tuple literal at `list[tuple[Item, Item]].append` builds its
    elements in VALUE slots and takes the consuming `tuple_to_storage_move`
    lift over them. The owning slot keeps the tuple past the call, so the
    borrow form -- whose element addresses die with the full-expression --
    must not appear. A last-use movable local element keeps rejecting (its
    per-element move render is its own rung); a plain borrowed lvalue
    element is sema-rejected before lowering. Corpus witness:
    async/coro_for_tuple_unpack_ref."""

    SRC = (_P
           + "def f() -> None:\n"
           + "    pairs: list[tuple[P, P]] = []\n"
           + "    pairs.append((P(1), P(2)))\n"
           + "    for a, b in pairs:\n"
           + "        print(a.x + b.x)\n"
           + "def main() -> None:\n"
           + "    f()\n"
           + "main()\n")

    def test_all_rvalue_literal_routes(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("arg.own_btuple_literal", 0) >= 1
        _assert_routes_byte_identical(self.SRC)
        compiler, modules = _compile(self.SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False))
        assert ("::tpy::tuple_to_storage_move<std::tuple<P, P>>("
                "std::tuple<P, P>{P(1), P(2)})" in cpp)
        # The owning sink must not reach for the borrow form at all.
        assert "tuple_value_to_borrow" not in cpp

    def test_lastuse_element_takes_the_storage_move(self):
        # A last-use movable local is not an rvalue source, so THIS row's
        # all-rvalue gate keeps it out -- but the united method-arg sink
        # reaches the same stub slot with the record listing's
        # `tuple_literal` cell, whose lift moves the last use
        # (`std::tuple<P*, P>{std::move(&(x)), P(2)}`), built and run.
        src = (_P
               + "def f() -> None:\n"
               + "    pairs: list[tuple[P, P]] = []\n"
               + "    x = P(5)\n"
               + "    pairs.append((x, P(2)))\n"
               + "    for a, b in pairs:\n"
               + "        print(a.x + b.x)\n")
        _, cpp = _assert_routes_byte_identical(src)
        assert "std::tuple<P*, P>{std::move(&(x)), P(2)}" in cpp


class TestOpenTOwningSinkLiteral:
    """A tuple literal with an OPEN-`T` element at an `Own[tuple[T, ..]]`
    element slot. The owning slot keeps the tuple, so the generic element is
    spelled with the bare `T` its destination already uses -- a generic
    element is never bare-pointer repr, so nothing lifts it afterwards and
    the borrow trait would otherwise become the stored slot. The BORROWING
    twin (a plain `tuple[T, ..]` param, read only through the call) keeps
    `val_or_ptr_t<T>` and its `to_val_or_ptr` wrap. Corpus witness:
    tuple/tuple_own_sink_generic_elem, tuple/tuple_borrow_sink_literal_alias.
    """

    _BAG = (
        "from tpy import Int32, Own, copy\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Bag[T]:\n"
        "    items: list[T]\n"
        "    def __init__(self) -> None:\n"
        "        self.items = []\n"
        "    def add(self, v: Own[T]) -> None:\n"
        "        self.items.append(v)\n"
    )

    SRC = (_BAG
           + "    def pairs(self) -> Own[list[tuple[T, Int32]]]:\n"
           + "        out: list[tuple[T, Int32]] = []\n"
           + "        i = 0\n"
           + "        for it in self.items:\n"
           + "            out.append((copy(it), i))\n"
           + "            i += 1\n"
           + "        return out\n"
           + "def main() -> None:\n"
           + "    b: Bag[P] = Bag()\n"
           + "    b.add(P(1))\n"
           + "    for it, i in b.pairs():\n"
           + "        print(i, it.x)\n"
           + "main()\n")

    def test_open_t_literal_routes(self):
        # The routing claim is `_assert_routes_byte_identical`: the subject
        # sits in a record METHOD, which the module-level lowering does not
        # carry, so its faces are witnessed by the emit below.
        _assert_routes_byte_identical(self.SRC)
        compiler, modules = _compile(self.SRC)
        hpp, _ = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False))
        w = compiler._thir_face_witnesses
        assert w.get("arg.own_open_t_tuple_literal", 0) >= 1
        assert w.get("containerlit.tparam_copy_elem", 0) >= 1
        assert "out.push_back(std::tuple<T, int32_t>{T(it), i});" in hpp
        # The owning slot must not pick up the borrow-form trait.
        assert "val_or_ptr" not in hpp

    def test_borrowing_slot_keeps_val_or_ptr(self):
        # BOUNDARY (dualgen-probed): the SAME literal at a plain
        # `tuple[T, Int32]` slot -- borrowed only for the call's duration --
        # keeps the generic borrow trait and its to_val_or_ptr wrap.
        src = (self._BAG
               + "    def wrap(self, e: T) -> tuple[T, Int32]:\n"
               + "        return (e, 1)\n")
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        hpp, _ = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False))
        assert compiler._thir_face_witnesses.get("gentuple.literal", 0) >= 1
        assert ("std::tuple<::tpy::val_or_ptr_t<T>, int32_t>{"
                "::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<T>>(e), 1}" in hpp)

    def test_container_element_sink_keeps_const_borrow(self):
        # BOUNDARY (dualgen-probed): the container-element sink reads a
        # read-only transient into `tuple_to_storage`, so its lvalue slots
        # stay CONST borrows. The owning sink moves out of the borrow slots
        # it keeps, which is why the two are separate decisions.
        src = (_P
               + "def build(a: P, b: P) -> Int32:\n"
               + "    rows: list[tuple[P, Int32]] = [(a, 1), (b, 2)]\n"
               + "    return len(rows)\n")
        _assert_routes_byte_identical(src)
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False))
        assert ("::tpy::tuple_to_storage<std::tuple<P, int32_t>>("
                "std::tuple<const P*, int32_t>{&(a), 1})" in cpp)

    def test_non_name_copy_source_stays_ast(self):
        # BOUNDARY: the open-`T` element admits a plain declared NAME source;
        # a subscript read under `copy()` carries a render this row does not
        # mirror, so the body keeps falling back.
        # `_fn` sees only module-level functions, so the reject has to be
        # read off the fallback tally for a body inside a record.
        src = (self._BAG
               + "    def pairs(self) -> Own[list[tuple[T, Int32]]]:\n"
               + "        out: list[tuple[T, Int32]] = []\n"
               + "        out.append((copy(self.items[0]), 1))\n"
               + "        return out\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.container_literal")


class TestOpenTOwningSinkStorageRead:
    """A whole open-`T` tuple ELEMENT READ at an `Own[tuple[T, ..]]` element
    slot (`out.append(ranked[i])`). The generic element has no pointer repr,
    so borrow and storage coincide there and the element is a self-contained
    value the read passes bare -- the pointer-repr sibling's form conversion
    has nothing to convert. Corpus witness: collections.Counter.most_common.
    """

    _BAG = (
        "from tpy import Int32, Own\n"
        "class Bag[T]:\n"
        "    rows: list[tuple[T, Int32]]\n"
        "    def __init__(self) -> None:\n"
        "        self.rows = []\n"
    )

    def test_local_and_field_reads_route(self):
        # `_assert_routes_byte_identical`: the subject sits in a record
        # METHOD, which the module-level lowering lens does not carry.
        src = (self._BAG
               + "    def take(self, n: Int32) -> Own[list[tuple[T, Int32]]]:\n"
               + "        out: list[tuple[T, Int32]] = []\n"
               + "        i = 0\n"
               + "        while i < n and i < len(self.rows):\n"
               + "            out.append(self.rows[i])\n"
               + "            i += 1\n"
               + "        return out\n")
        _assert_routes_byte_identical(src)
        compiler, modules = _compile(src)
        hpp, _ = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False))
        w = compiler._thir_face_witnesses
        assert w.get("arg.own_open_t_tuple_storage_source", 0) >= 1
        assert w.get("subscript.open_t_tuple_source", 0) >= 1
        assert ("out.push_back(::tpy::__getitem__(this->rows, i));" in hpp)
        # The owning slot must not pick up the borrow-form trait.
        assert "val_or_ptr" not in hpp

    def test_nested_tuple_element_stays_ast(self):
        # BOUNDARY (dualgen-probed): a TUPLE element beside the open one is
        # outside the value-element family this row reads through, so the
        # body keeps falling back at the arg gate.
        # `_fn` sees only module-level functions, so a record body's reject
        # has to be read off the fallback tally.
        src = (self._BAG
               + "    def pack(self, src: list[tuple[T, tuple[Int32, Int32]]]"
                 ") -> Own[list[tuple[T, tuple[Int32, Int32]]]]:\n"
               + "        out: list[tuple[T, tuple[Int32, Int32]]] = []\n"
               + "        out.append(src[0])\n"
               + "        return out\n")
        _assert_rejects_at(_reject_tally(src), 'body:expr.method_call', 'method.arg_shape')

    def test_call_source_stays_ast(self):
        # BOUNDARY: this row reads a SUBSCRIPT only. A CALL returning the
        # same open-`T` tuple never even reaches the arg gate -- the method
        # call's own return shape refuses it first -- so widening that gate
        # must re-decide the call source deliberately rather than inherit it.
        src = (self._BAG
               + "    def make(self, i: Int32) -> Own[tuple[T, Int32]]:\n"
               + "        return self.rows[i]\n"
               + "    def pack(self) -> Own[list[tuple[T, Int32]]]:\n"
               + "        out: list[tuple[T, Int32]] = []\n"
               + "        out.append(self.make(0))\n"
               + "        return out\n")
        _assert_rejects_at(_reject_tally(src), 'body:stmt.return',
                           'return.tuple_source')


class TestBtupleBranchHoist:
    """The if-head borrow-tuple hoist track (wave 9): an OWNING tuple-call
    branch bind emplaces into the name's pre-declared rebind slot
    (`std::optional<std::tuple<int32_t, Box>> __slot_1;` at the chain
    head, `t = ::tpy::tuple_to_pointer<...>(__slot_1.emplace(
    make_pair(9)));` per branch); a sibling borrow-tuple LOCAL source is
    the bare pointer-tuple copy (`u = t;`); a borrow-tuple PARAM source
    keeps the whole-body fallback (const-element spelling mismatch)."""

    _BOX = (
        "from tpy import Int32, Own\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32):\n"
        "        self.val = v\n"
        "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
        "    return (n, Box(n))\n"
    )

    def test_owning_call_emplace_routes(self):
        src = (self._BOX
               + "def use(c: bool) -> Int32:\n"
               + "    if c:\n"
               + "        t = make_pair(9)\n"
               + "    else:\n"
               + "        t = make_pair(5)\n"
               + "    return t[1].val\n"
               + "print(use(True), use(False))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("if.hoist_borrow_tuple", 0) >= 1
        assert faces.get("btuple.reseat_emplace", 0) >= 2
        cpp = _emit_cpp(src)
        assert "std::optional<std::tuple<int32_t, Box>> __slot_1;" in cpp
        assert ("t = ::tpy::tuple_to_pointer<std::tuple<int32_t, Box*>>"
                "(__slot_1.emplace(make_pair(9)));") in cpp
        _assert_byte_identical(src)

    def test_sibling_name_copy_routes(self):
        src = (self._BOX
               + "def f(b: Box, c: bool) -> tuple[Int32, Box]:\n"
               + "    t = (1, b)\n"
               + "    if c:\n"
               + "        u = t\n"
               + "    else:\n"
               + "        u = (2, b)\n"
               + "    return u\n"
               + "def main() -> None:\n"
               + "    b = Box(5)\n"
               + "    pair = f(b, True)\n"
               + "    pair[1].val = 99\n"
               + "    print(b.val)\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("btuple.reseat_name_copy", 0) >= 1
        assert faces.get("btuple.reseat_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_param_source_still_defers(self):
        src = (self._BOX
               + "def f(p: tuple[Int32, Box], c: bool) -> Int32:\n"
               + "    if c:\n"
               + "        u = p\n"
               + "    else:\n"
               + "        u = (3, Box(3))\n"
               + "    return u[0]\n"
               + "print(f((4, Box(4)), True))\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.if:if.hoist_type")


def _emit_cpp(src: str) -> str:
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return cpp


class TestBtupleHoistedWalrus:
    """Cell 2 of the branch-hoist track: a HOISTED borrow-tuple walrus
    with an owning-call value (`(t := make_pair(9))[0]`) renders the
    emplace + bare-name tail (`(t = ::tpy::tuple_to_pointer<...>(
    __slot_N.emplace(make_pair(9))), t)`) over the if-head slot; an
    owning-call walrus VALUE counts as an ordinary binding source in the
    hoist admission. A non-owning walrus bind keeps the fence."""

    _SRC = (
        "from tpy import Int32, Own\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32):\n"
        "        self.val = v\n"
        "class Holder:\n"
        "    pair: tuple[Int32, Box]\n"
        "    def __init__(self, p: Own[tuple[Int32, Box]]):\n"
        "        self.pair = p\n"
        "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
        "    return (n, Box(n))\n"
        "def use(h: Holder, c: bool) -> Int32:\n"
        "    if c:\n"
        "        if (t := make_pair(9))[0] > 0:\n"
        "            return t[1].val\n"
        "    else:\n"
        "        t = h.pair\n"
        "        t[1].val = 77\n"
        "    return h.pair[1].val\n"
        "def main() -> None:\n"
        "    h = Holder(make_pair(1))\n"
        "    print(use(h, True), use(h, False))\n"
        "main()\n"
    )

    def test_hoisted_walrus_emplace_routes(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use") is not None
        assert faces.get("expr.walrus_btuple_emplace", 0) >= 1
        # The module's own `main` rejects at an unrelated ctor-arg shape,
        # so the render this walrus emits cannot be read off a whole-module
        # emit; the face witness is what pins the arm.


class TestBtupleHoistBoundaries2:
    """Batch-3 review hardenings: the REVERSED branch order (a plain
    btuple reseat emitted before any emplace -- the emit arm's structural
    TupleType exclusion carries it); a NON-owning-call walrus value keeps
    the sources fence (borrow-returning callee)."""

    def test_reversed_branch_order_routes(self):
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32):\n"
            "        self.val = v\n"
            "class Holder:\n"
            "    pair: tuple[Int32, Box]\n"
            "    def __init__(self, p: Own[tuple[Int32, Box]]):\n"
            "        self.pair = p\n"
            "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
            "    return (n, Box(n))\n"
            "def use(h: Holder, c: bool) -> Int32:\n"
            "    if c:\n"
            "        t = h.pair\n"
            "        t[1].val = 77\n"
            "    else:\n"
            "        t = make_pair(9)\n"
            "    return t[1].val\n"
            "def main() -> None:\n"
            "    h = Holder(make_pair(1))\n"
            "    print(use(h, True), use(h, False))\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_tuple")

    def test_borrow_call_walrus_still_defers(self):
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32):\n"
            "        self.val = v\n"
            "class Holder:\n"
            "    pair: tuple[Int32, Box]\n"
            "    def __init__(self, p: Own[tuple[Int32, Box]]):\n"
            "        self.pair = p\n"
            "    def view(self) -> tuple[Int32, Box]:\n"
            "        return self.pair\n"
            "def use(h: Holder, c: bool) -> Int32:\n"
            "    if c:\n"
            "        if (t := h.view())[0] > 0:\n"
            "            return t[1].val\n"
            "    else:\n"
            "        t = h.pair\n"
            "        return t[0]\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    h = Holder((1, Box(2)))\n"
            "    print(use(h, True))\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.walrus")

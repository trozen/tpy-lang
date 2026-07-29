"""Ref-element tuple literals as container elements: the BORROW inner
(`std::tuple<T*, ..>{&(a), nullptr}`) under the `tuple_to_storage` convert."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _lower_ctor, _fn,
    _assert_byte_identical, _compile, _entry,
)

_T = (
    "from tpy import Int32\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestOptionalMemberTupleElements:
    def test_name_and_none_members_route(self):
        # `[(t1, t2), (t1, None), (None, None)]` at a
        # `list[tuple[T | None, T | None]]` slot: each element builds the
        # borrow tuple, the convert emits tuple_to_storage.
        src = (_T
               + "def use() -> None:\n"
               + "    t1 = T(1)\n"
               + "    t2 = T(2)\n"
               + "    items: list[tuple[T | None, T | None]] = "
               + "[(t1, t2), (t1, None), (None, None)]\n"
               + "    print(len(items))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("containerlit.tuple_borrow_storage", 0) >= 1
        assert w.get("btuple.elem_optptr", 0) >= 1
        _assert_byte_identical(src)

    def test_plain_record_member_stays_ast(self):
        # A non-Optional record member is a ref element too, but
        # `_tuple_literal_slot_info` sends a simple lvalue of that shape to
        # CONST_REF in a STORAGE context (`const T*`), a rule
        # `_lower_borrow_tuple_literal` does not carry -- so the arm is
        # restricted to the all-Optional shape both ladders agree on.
        src = (_T
               + "def use() -> None:\n"
               + "    t1 = T(1)\n"
               + "    items: list[tuple[T, Int32]] = [(t1, 7)]\n"
               + "    print(len(items))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestTupleLiteralFieldWrite:
    def test_value_tuple_field_assigns_bare(self):
        # Borrow and storage coincide for a value tuple -- the spelled
        # brace-init assigns directly, no tuple_to_storage.
        src = ("class S:\n"
               "    auth: tuple[str, str]\n"
               "    def __init__(self) -> None:\n"
               "        self.auth = (\"\", \"\")\n"
               "def use(s: S) -> None:\n"
               "    s.auth = (\"user\", \"pw\")\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field_write.tuple_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_record_element_tuple_field_lifts(self):
        # An F3 (non-value-element) tuple field adds the assign's
        # `tuple_to_storage` wrap over the same brace-init.
        src = (_T
               + "class P:\n"
               + "    points: tuple[T, T]\n"
               + "    def __init__(self) -> None:\n"
               + "        a = T(1)\n"
               + "        b = T(2)\n"
               + "        self.points = (a, b)\n")
        # The write lives in a CTOR body, which `_lower_ctx_witnessed` does
        # not lower -- assert the ctor routes, then pin the render.
        assert _lower_ctor(src, "P") is not None
        _assert_byte_identical(src)


class TestTupleElementBoundaries:
    def test_all_value_members_keep_storage_direct(self):
        # No ref element -> the storage-DIRECT inner, not the borrow ladder.
        src = ("from tpy import Int32\n"
               "def use() -> None:\n"
               "    items: list[tuple[Int32, Int32]] = [(1, 2), (3, 4)]\n"
               "    print(len(items))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("containerlit.tuple_borrow_storage", 0) == 0
        _assert_byte_identical(src)

    def test_bad_member_shape_falls_back(self):
        # The field-write row checks only the slot/arity pairing; a member
        # shape outside `_lower_tuple_literal` raises there and falls the
        # body back whole (a FIELD-read member has no element row).
        src = (_T
               + "class Holder:\n"
               + "    t: T\n"
               + "    def __init__(self, t: T) -> None:\n"
               + "        self.t = t\n"
               + "class P:\n"
               + "    pair: tuple[T, T]\n"
               + "    def __init__(self, t: T) -> None:\n"
               + "        self.pair = (t, t)\n"
               + "def use(p: P, h: Holder) -> None:\n"
               + "    p.pair = (h.t, h.t)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_nonname_lvalue_member_stays_ast(self):
        # The borrow ladder admits NAME / subscript lvalues and `None`; a
        # FIELD-read member is outside it and must keep falling back.
        src = (_T
               + "class Holder:\n"
               + "    t: T\n"
               + "    def __init__(self, t: T) -> None:\n"
               + "        self.t = t\n"
               + "def use(h: Holder) -> None:\n"
               + "    items: list[tuple[T | None, T | None]] = [(h.t, None)]\n"
               + "    print(len(items))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


_PTR_T = (
    "from tpy import Int32, Ptr\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def take(t: tuple[T, Int32]) -> Int32:\n"
    "    return t[0].x + t[1]\n"
)


def _gen_thir(source: str):
    """Emit through THIR and return (text, fallback tally) -- the fallback
    tally is what separates "routed and rendered right" from "fell back and
    borrowed the AST's answer", which the byte-diff alone cannot tell apart."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp, dict(compiler._thir_fallback)


class TestPtrElementPassesBare:
    """A `Ptr[T]` element already IS the `T*` the slot wants, so it renders
    bare -- the `&(...)` lift would spell `T**`. A `Ptr[T]` source never
    enters `lc.pointers`, so the name-membership test these rows used to
    spell answered "not a pointer" for exactly the type that is one."""

    def test_ptr_local_element_routes_bare(self):
        src = (_PTR_T
               + "def use(items: list[T]) -> Int32:\n"
               + "    p: Ptr[T] = items[0]\n"
               + "    return take((p, 10))\n")
        out, fallback = _gen_thir(src)
        assert not fallback
        assert "take(std::tuple<T*, int32_t>{p, 10})" in out
        _assert_byte_identical(src)

    def test_ptr_param_element_routes_bare(self):
        src = (_PTR_T
               + "def use(p: Ptr[T]) -> Int32:\n"
               + "    return take((p, 10))\n")
        out, fallback = _gen_thir(src)
        assert not fallback
        assert "take(std::tuple<T*, int32_t>{p, 10})" in out
        _assert_byte_identical(src)

    def test_ptr_subscript_element_routes_bare(self):
        # The subscript arm lifted UNCONDITIONALLY; only `use` need route
        # (building the list needs `.append`, which falls back).
        src = (_PTR_T
               + "def use(ps: list[Ptr[T]]) -> Int32:\n"
               + "    return take((ps[0], 10))\n")
        out, fallback = _gen_thir(src)
        assert not fallback
        assert ("take(std::tuple<T*, int32_t>"
                "{::tpy::__getitem__(ps, 0), 10})") in out
        _assert_byte_identical(src)

    def test_readonly_ptr_param_element_routes_bare(self):
        # `readonly` wraps the POINTER, not the pointee, so the slot stays
        # `Node*` -- the predicate's `unwrap_readonly` is what keeps this
        # source recognizable as already-a-pointer.
        src = (_PTR_T
               + "def use(p: readonly[Ptr[T]]) -> Int32:\n"
               + "    return take((p, 10))\n").replace(
                   "from tpy import Int32, Ptr\n",
                   "from tpy import Int32, Ptr, readonly\n")
        out, fallback = _gen_thir(src)
        assert not fallback
        assert "take(std::tuple<T*, int32_t>{p, 10})" in out
        _assert_byte_identical(src)

    def test_plain_record_element_still_lifts(self):
        # The inverse: a plain lvalue is NOT already a pointer and must keep
        # taking `&(...)`, or the fix would under-trigger into a T-into-T*.
        src = (_PTR_T
               + "def use() -> Int32:\n"
               + "    n = T(5)\n"
               + "    return take((n, 10))\n")
        out, fallback = _gen_thir(src)
        assert not fallback
        assert "take(std::tuple<T*, int32_t>{&(n), 10})" in out
        _assert_byte_identical(src)

    def test_ptr_element_at_optional_slot_defers(self):
        # The pointer-repr-Optional element row knows only the addr_of
        # render, so an already-pointer source must DEFER rather than lift
        # it wrongly. Routing it bare would be a new emit arm.
        src = (_PTR_T.replace("def take(t: tuple[T, Int32]) -> Int32:\n"
                              "    return t[0].x + t[1]\n",
                              "def take(t: tuple[T | None, Int32]) -> Int32:\n"
                              "    n = t[0]\n"
                              "    if n is None:\n"
                              "        return t[1]\n"
                              "    return n.x + t[1]\n")
               + "def use(p: Ptr[T]) -> Int32:\n"
               + "    return take((p, 10))\n")
        out, fallback = _gen_thir(src)
        assert "body:expr.tuple_literal" in fallback
        assert "{&(p), 10}" not in out
        _assert_byte_identical(src)

    def test_ptr_at_optional_ptr_call_arg_defers(self):
        # The adjacent SINK for the same predicate: the optional-ptr call arg
        # (kept with these pins because they share the predicate, not the
        # sink). It rejects before reaching any pointer test today -- pinned
        # so it stays a reject rather than becoming a silent `&(p)` if the
        # arm is widened without routing through `_already_pointer_source`.
        src = (_PTR_T
               + "def f(n: T | None) -> Int32:\n"
               + "    if n is None:\n"
               + "        return 0\n"
               + "    return n.x\n"
               + "def use(p: Ptr[T]) -> Int32:\n"
               + "    return f(p)\n")
        out, fallback = _gen_thir(src)
        assert "body:expr.call" in fallback
        assert "f(&(p))" not in out
        _assert_byte_identical(src)

"""The Own-slot `needs_copy` mirror: gen_call_arg's rendered-string identity
test for a coerce-wrapped simple lvalue, mirrored by coercion KIND
(`_coerce_disposition`, whose per-KIND verdict agrees with the string test
wherever the name renders plain). Rows pinned here:

 * an all-identity coerce chain over a NAME at an `Own[scalar]` slot keeps
   the copy+move temp (`auto __tmp_N = v;` -- needs_copy=True, the identity
   render leaves the lvalue);
 * a WRAPPING chain over a NAME renders an rvalue (needs_copy=False, no
   temp) -- unwitnessed, stays on the AST path (boundary);
 * an `Own[str]` slot's copy temp declares the owned type with brace init
   (`std::string __tmp_N{...};` -- the view->owned CONVERSION, never elided
   for a cpp_template callee either), fed by a bare str FIELD read or a
   coerce-wrapped view source (the enum `.name` face);
 * the subscript-aug-assign statement (`d[k] += m.take(b)`) flushes arg
   temps before its single setitem line, like the other flush positions;
 * a user record's own slice `__getitem__` overload renders the plain
   member call over the BasicSlice initializer.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_SINK = (
    "from tpy import Int32, Own\n"
    "class Sink:\n"
    "    total: Int32\n"
    "    def __init__(self) -> None:\n        self.total = 0\n"
    "    def push(self, v: Own[Int32]) -> None:\n        self.total += v\n"
)


class TestIdentityCoerceChainCopy:
    def test_int_literal_coerce_over_name_keeps_the_temp(self):
        # The for-var over a literal list reads as an IntLiteral-typed NAME,
        # so the arg arrives coerce-wrapped (int_literal_to_fixed_int -- an
        # identity render). needs_copy stays True: same temp as a bare name.
        src = _SINK + (
            "def f(s: Sink) -> None:\n"
            "    for v in [10, 20]:\n"
            "        s.push(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "auto __tmp_1 = v;" in body
        assert "s.push(std::move(__tmp_1))" in body
        assert faces["argtemp.own_copy"] >= 1
        _assert_byte_identical(src)

    def test_wrapping_coerce_over_name_stays_ast(self):
        # bigint_to_fixed_int renders `(b).to_fixed_check<int32_t>()` -- a
        # real conversion, so the AST binds the rvalue bare (needs_copy=
        # False). Unwitnessed as a routed face: the body must fall back.
        src = _SINK + (
            "def f(s: Sink, b: int) -> None:\n"
            "    s.push(b)\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestOwnStrTemp:
    def test_bare_str_field_hoists_the_typed_temp(self):
        src = (
            "class R:\n"
            "    label: str\n"
            "    def __init__(self, label: str) -> None:\n"
            "        self.label = label\n"
            "def f(r: R) -> None:\n"
            "    xs: list[str] = []\n"
            "    xs.append(r.label)\n"
            "    print(xs)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "std::string __tmp_1{r.label};" in body
        assert "xs.push_back(std::move(__tmp_1))" in body
        assert faces["argtemp.own_str"] >= 1
        _assert_byte_identical(src)

    def test_coerced_enum_name_hoists_temp_with_the_wrapped_init(self):
        # strview_to_str at the Own[str] ARG slot materializes (the
        # coercions.py lambda's OwnType branch); the FIELD inner never takes
        # the rendered-string test, so the temp still hoists -- with the
        # conversion inside its init.
        src = (
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "def f(c: Color) -> None:\n"
            "    xs: list[str] = []\n"
            "    xs.append(c.name)\n"
            "    print(xs)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert ("std::string __tmp_1{std::string("
                "::tpy::EnumUtil<Color>::name(c))};") in body
        assert "xs.push_back(std::move(__tmp_1))" in body
        assert faces["argtemp.own_str"] >= 1
        _assert_byte_identical(src)

    def test_own_str_arg_in_while_condition_hoists_into_the_loop_head(self):
        # The restructured loop head is a flush position too: the typed temp
        # lands inside `while (true) { ... }` ahead of the break test, same
        # as the AST's cond-checkpoint flush.
        src = (
            "from tpy import Own\n"
            "class S:\n"
            "    kept: list[str]\n"
            "    def __init__(self) -> None:\n        self.kept = []\n"
            "    def check(self, v: Own[str]) -> bool:\n"
            "        self.kept.append(v)\n"
            "        return len(self.kept) < 2\n"
            "class R:\n"
            "    label: str\n"
            "    def __init__(self, label: str) -> None:\n"
            "        self.label = label\n"
            "def f(s: S, r: R) -> None:\n"
            "    while s.check(r.label):\n"
            "        break\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "std::string __tmp_1{r.label};" in body
        assert "if (!(s.check(std::move(__tmp_1)))) break;" in body
        assert faces["argtemp.own_str"] >= 1
        _assert_byte_identical(src)


_OWN_STR_PARAM = (
    "from tpy import Own\n"
    "class S:\n"
    "    kept: list[str]\n"
    "    def __init__(self) -> None:\n        self.kept = []\n"
    "    def check(self, v: Own[str]) -> bool:\n"
    "        self.kept.append(v)\n"
    "        return len(self.kept) < 2\n"
)


class TestOwnViewfamParamReads:
    """The Own[str]/Own[bytes]-PARAM bare-read cell: the signature spells the
    OWNED type by value (`_own_viewfam_param`), so the name reads are STORAGE
    -- the owned-sink view->owned copy never fires, and an owning arg slot
    hoists the same typed copy temp + move the AST's needs_copy cascade
    renders. Not movable on either path (value payload)."""

    def test_own_str_param_at_owned_elem_slot_hoists_the_copy_temp(self):
        # The callee half of the while-condition fence above: the Own[str]
        # param at the `Own[str]` element slot takes the copy+move temp
        # (`std::string __tmp_1{v};` -- NOT the inline `std::string(v)`
        # view convert, which is what the naive param-implies-view verdict
        # rendered and the corpus fence caught byte-diverging).
        thir, faces = _lower_ctx_witnessed(_OWN_STR_PARAM)
        body = _body(thir, "check")
        assert "std::string __tmp_1{v};" in body
        assert "this->kept.push_back(std::move(__tmp_1))" in body
        assert faces["argtemp.own_str"] >= 1
        _assert_byte_identical(_OWN_STR_PARAM)

    def test_own_str_param_storage_reads_render_bare(self):
        # STORAGE reads: return / decl init / setitem key render the plain
        # name -- no view->owned wrap, no move.
        src = (
            "from tpy import Own\n"
            "def ret(v: Own[str]) -> str:\n    return v\n"
            "def decl(v: Own[str]) -> int:\n"
            "    s = v\n    return len(s)\n"
            "def key(v: Own[str]) -> None:\n"
            "    d: dict[str, int] = {}\n    d[v] = 1\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        assert "return v;" in _body(thir, "ret")
        assert "std::string s = v;" in _body(thir, "decl")
        assert "::tpy::__setitem__(d, v" in _body(thir, "key")
        _assert_byte_identical(src)

    def test_own_bytes_param_at_owned_elem_slot_lands_bare(self):
        # The bytes twin of the copy-temp leg is a DIFFERENT render, not a
        # missing one: an `Own[bytes]` param is owned storage the template
        # callee binds as an lvalue, so it passes bare where the str twin
        # above hoists the brace-init view->owned temp.
        src = (
            "from tpy import Own\n"
            "def f(v: Own[bytes]) -> None:\n"
            "    xs: list[bytes] = []\n"
            "    xs.append(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("arg.bytes_owned_name", 0) >= 1
        assert not faces.get("argtemp.own_str")
        assert "xs.push_back(v);" in _body(thir, "f")
        _assert_byte_identical(src)

    def test_own_strview_param_read_still_defers(self):
        # Boundary: Own[StrView] is the no-op Own spelling over a value VIEW
        # (`std::string_view` by value) -- outside `_own_viewfam_param`, and
        # its bare read keeps the name.own_read reject.
        src = (
            "from tpy import Own, StrView\n"
            "def f(v: Own[StrView]) -> None:\n"
            "    xs: list[str] = []\n"
            "    xs.append(v)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_reassigned_own_str_param_still_defers(self):
        # Boundary: a reassigned Own[str] param stays out -- the AST renders
        # the plain concat-assign (`v = str_concat(v, "x")`; its inplace
        # peephole checks the DECLARED type, which the Own wrapper fails),
        # so THIR's self-append fold must not fire on it. The reassign arm
        # rejects the shape whole today.
        src = (
            "from tpy import Own\n"
            'def f(v: Own[str]) -> str:\n    v = v + "x"\n    return v\n'
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestOwnCoerceFieldBoundary:
    def test_wrapping_coerce_field_at_scalar_slot_routes(self):
        # A widening coerce over an Int8 field at an Own[Int32] slot: the
        # cast rvalue binds the slot natively (the Own[value-scalar]
        # template disposition) -- inline, no temp, dualgen-verified.
        src = (
            "from tpy import Int8, Int32\n"
            "class R:\n"
            "    n: Int8\n"
            "    def __init__(self) -> None:\n        self.n = 5\n"
            "def f(r: R) -> None:\n"
            "    xs: list[Int32] = []\n"
            "    xs.append(r.n)\n"
            "    print(xs)\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


class TestAugSetitemFlush:
    def test_aug_subscript_value_flushes_the_own_copy_temp(self):
        # The subscript-target index reads b.items in the same full
        # expression as the consuming arg, so sema keeps `b` un-movable
        # (the copies warning) and the COPY half must hoist -- the
        # same-statement conflict the auto_move case pins end-to-end.
        src = (
            "from tpy import Int32, Own\n"
            "class Blob:\n"
            "    items: list[Int32]\n"
            "    def __init__(self) -> None:\n        self.items = [10]\n"
            "class K:\n"
            "    kept: list[Blob]\n"
            "    def __init__(self) -> None:\n        self.kept = []\n"
            "    def take(self, b: Own[Blob]) -> Int32:\n"
            "        self.kept.append(b)\n"
            "        return 9\n"
            "def f() -> None:\n"
            "    k = K()\n"
            "    b = Blob()\n"
            "    d: dict[Int32, Int32] = {3: 1}\n"
            "    d[len(b.items)] += k.take(b)  # tpyc: warning(/copies/)\n"
            "    print(d)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "auto __tmp_1 = b;" in body
        assert "k.take(std::move(__tmp_1))" in body
        assert "::tpy::__setitem__(d, " in body
        assert faces["argtemp.own_copy"] >= 1
        _assert_byte_identical(src)


class TestRecordSliceMethod:
    def test_array_list_slice_renders_the_member_call(self):
        src = (
            "from tpy import Int32\n"
            "from tplib import ArrayList\n"
            "def f() -> None:\n"
            "    a = ArrayList[Int32, 8]()\n"
            "    a.append(3)\n"
            "    sp = a[0:1]\n"
            "    sp2 = a[-1:]\n"
            "    print(len(sp), len(sp2))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "a.__getitem__(::tpy::BasicSlice{0, 1})" in body
        assert "a.__getitem__(::tpy::BasicSlice{-1, std::nullopt})" in body
        assert faces["subscript.record_slice_method"] >= 2
        _assert_byte_identical(src)

    def test_self_slice_receiver_stays_ast(self):
        # A `self[...]` slice inside the record's own method: `self` renders
        # `this`, so the bare `{self}.` member template would spell `.` on a
        # pointer (the AST pre-derefs `(*this)`) -- the gate rejects the
        # self receiver and the body falls back whole.
        src = (
            "from typing import overload\n"
            "from tpy import Int32, Span, basic_slice, span, readonly\n"
            "class Buf:\n"
            "    data: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.data = [1, 2, 3]\n"
            "    @overload\n"
            "    def __getitem__(self, index: Int32) -> Int32: ...\n"
            "    @overload\n"
            "    def __getitem__(self, index: basic_slice)"
            " -> Span[readonly[Int32]]: ...\n"
            "    def __getitem__(self, index: Int32 | basic_slice)"
            " -> Int32 | Span[readonly[Int32]]:\n"
            "        if isinstance(index, basic_slice):\n"
            "            return span(self.data)[index]\n"
            "        return self.data[index]\n"
            "    def head_count(self) -> Int32:\n"
            "        sp = self[0:2]\n"
            "        return len(sp)\n"
            "def f() -> None:\n"
            "    b = Buf()\n"
            "    print(b.head_count())\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "head_count") is None
        _assert_byte_identical(src)

    def test_stepped_record_slice_routes(self):
        # The stepped overload now dispatches on the 3-part
        # `::tpy::Slice{...}` initializer (the emit picks the spelling
        # off the stepped flag) -- routed, byte-identical.
        src = (
            "from tpy import Int32\n"
            "from tplib import ArrayList\n"
            "def f() -> None:\n"
            "    a = ArrayList[Int32, 8]()\n"
            "    a.append(3)\n"
            "    sp = a[::2]\n"
            "    print(len(sp))\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

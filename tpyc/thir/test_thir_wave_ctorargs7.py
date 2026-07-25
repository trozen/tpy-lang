"""Ctor-arg wave arms: the flush-less DIRECT own-move-source row, the
Own[list]-literal ctor row, the tuple-literal ctor slot row, the borrow-tuple
pointer-repr Optional element faces (None -> nullptr, plain name -> &(name)),
and the tuple-field print form."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_ITEM = (
    "from tpy import Int32, Own\n"
    "class Item:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "class BoxI:\n"
    "    item: Item\n"
    "    def __init__(self, item: Own[Item]) -> None:\n        self.item = item\n"
)


class TestCtorOwnMoveFlushless:
    # `[BoxI(a)]`: the ctor element of a container literal reaches the arg
    # gate DIRECT with allow_temps=False; the temp-free move-source slice
    # (`std::move(a)`) is position-independent and admits there.
    def test_last_use_routes(self):
        src = (_ITEM
               + "def use() -> Int32:\n"
               + "    a = Item(1)\n"
               + "    boxes: list[BoxI] = [BoxI(a)]\n"
               + "    return boxes[0].item.n\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.own_arg", 0) >= 1
        _assert_byte_identical(src)

    def test_not_last_use_stays_ast(self):
        # `a` read after the ctor: not a move source, and the flush-less
        # position has no copy-temp -- the body must keep falling back.
        src = (_ITEM
               + "def use() -> Int32:\n"
               + "    a = Item(2)\n"
               + "    boxes: list[BoxI] = [BoxI(a)]\n"
               + "    return a.n + len(boxes)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestCtorOwnListLiteral:
    _BAG = (
        "from tpy import Int32, Own\n"
        "class Bag:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self, xs: Own[list[Int32]]) -> None:\n"
        "        self.xs = xs\n"
    )

    def test_list_literal_routes_bare_brace(self):
        src = (self._BAG
               + "def use() -> Int32:\n"
               + "    b = Bag([1, 2, 3])\n"
               + "    return len(b.xs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.container_literal_arg", 0) >= 1
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "Bag b = Bag({1, 2, 3});" in cpp

    def test_own_dict_literal_routes(self):
        # The dict face renders off the literal's own resolved type -- the
        # same coincidence the list face rests on (the shape check pins the
        # literal's family to the peeled slot's, so the two agree).
        src = ("from tpy import Int32, Own\n"
               "class M:\n"
               "    d: dict[str, Int32]\n"
               "    def __init__(self, d: Own[dict[str, Int32]]) -> None:\n"
               "        self.d = d\n"
               "def use() -> Int32:\n"
               "    m = M({\"a\": 1})\n"
               "    return len(m.d)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


_PAIR = (
    "from typing import Optional\n"
    "from tpy import Int32\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
    "class Hold:\n"
    "    pair: tuple[Optional[Point], Int32]\n"
    "    def __init__(self, pair: tuple[Optional[Point], Int32]) -> None:\n"
    "        self.pair = pair\n"
)


class TestCtorTupleLiteralArg:
    def test_optional_elem_name_and_none_route(self):
        src = (_PAIR
               + "def use() -> Int32:\n"
               + "    p = Point(1)\n"
               + "    h1 = Hold((p, 4))\n"
               + "    h2 = Hold((None, 9))\n"
               + "    return h1.pair[1] + h2.pair[1]\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("btuple.elem_optptr", 0) >= 2
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "std::tuple<Point*, int32_t>{&(p), 4}" in cpp
        assert "std::tuple<Point*, int32_t>{nullptr, 9}" in cpp

    def test_narrowed_name_elem_stays_ast(self):
        # A NARROWED Optional name element needs the extraction-alias /
        # optional_to_ptr renders -- not mirrored; the body keeps falling
        # back.
        src = (_PAIR
               + "def use(op: Optional[Point]) -> Int32:\n"
               + "    if op is None:\n        return 0\n"
               + "    h = Hold((op, 4))\n"
               + "    return h.pair[1]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestPrintTupleField:
    def test_storage_tuple_field_prints_tuple_printer(self):
        src = (_PAIR
               + "def use(h: Hold) -> None:\n"
               + "    print(h.pair)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "::tpy::TuplePrinter(h.pair)" in cpp


class TestConsumingInstArg:
    # `dict(pairs)` at pairs' last use: the instantiation arg takes the
    # consuming-__iter__ wrap (`::tpy::own_iter(std::move(pairs))`); a
    # still-live name keeps the bare borrow render.
    _SRC = (
        "from tpy import Int32\n"
        "def consume_move() -> Int32:\n"
        "    pairs: list[tuple[str, Int32]] = [(\"a\", 1), (\"b\", 2)]\n"
        "    d = dict(pairs)\n"
        "    return len(d)\n"
    )

    def test_last_use_wraps_own_iter(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "consume_move") is not None
        assert w.get("call.own_iter_arg", 0) >= 1
        _assert_byte_identical(self._SRC)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "::tpy::own_iter(std::move(pairs))" in cpp

    def test_not_last_use_stays_bare(self):
        src = (self._SRC.replace("return len(d)",
                                 "return len(d) + len(pairs)"))
        thir = _lower_ctx(src)
        assert _fn(thir, "consume_move") is not None
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "own_iter" not in cpp


class TestDictViewInstArg:
    def test_view_args_route(self):
        src = ("from tpy import Int32\n"
               "def views() -> Int32:\n"
               "    m: dict[str, Int32] = {\"a\": 1, \"b\": 2}\n"
               "    ks = list(m.keys())\n"
               "    vs = list(m.values())\n"
               "    return len(ks) + len(vs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "views") is not None
        assert w.get("call.inst_view_arg", 0) >= 2
        _assert_byte_identical(src)

    def test_items_list_decl_stays_ast(self):
        # `list(d.items())` routes as an EXPR, but the list[tuple] DECL slot
        # is outside _storage_call_ret -- the body keeps falling back until
        # the container-elem axis widens.
        src = ("from tpy import Int32\n"
               "def items_decl() -> Int32:\n"
               "    m: dict[str, Int32] = {\"a\": 1}\n"
               "    items = list(m.items())\n"
               "    return len(items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "items_decl") is None
        _assert_byte_identical(src)


class TestValueOptPassCtorArg:
    def test_whole_optional_name_passes_bare(self):
        src = ("from typing import Optional\n"
               "from tpy import Int32\n"
               "class OptHold:\n"
               "    v: Optional[Int32]\n"
               "    def __init__(self, v: Optional[Int32]) -> None:\n"
               "        self.v = v\n"
               "def use(x: Optional[Int32]) -> Int32:\n"
               "    h = OptHold(x)\n"
               "    if h.v is None:\n        return 0\n"
               "    return h.v\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.value_opt_pass_arg", 0) >= 1
        _assert_byte_identical(src)


class TestProtocolOptionalContainerArg:
    # `Counter(words)`: a container NAME into an Optional[protocol] ctor
    # slot takes the address-of lift (`&(words)`), like the all-protocols
    # union slot.
    _SRC_LIB = (
        "from typing import Optional, Iterable\n"
        "from tpy import Int32\n"
        "class Sink:\n"
        "    n: Int32\n"
        "    def __init__(self, src: Optional[Iterable[str]] = None) -> None:\n"
        "        self.n = 0\n"
        "        if src is not None:\n"
        "            for _s in src:\n"
        "                self.n += 1\n"
        "def use() -> Int32:\n"
        "    words: list[str] = [\"a\", \"b\"]\n"
        "    s = Sink(words)\n"
        "    return s.n\n"
    )

    def test_container_name_addr_lift(self):
        thir, w = _lower_ctx_witnessed(self._SRC_LIB)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.protocol_union_arg", 0) >= 1
        _assert_byte_identical(self._SRC_LIB)


class TestTryParseSpecial:
    _SRC = ("from enum import Enum\n"
            "from tpy import Int32, try_parse\n"
            "class Hue(Enum):\n"
            "    R = 0\n"
            "    G = 1\n"
            "def use(s: str) -> Int32:\n"
            "    h = try_parse(Hue, s)\n"
            "    if h is None:\n        return -1\n"
            "    return 1\n")

    def test_routes_with_enumutil_render(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use") is not None
        assert w.get("call.try_parse", 0) >= 1
        _assert_byte_identical(self._SRC)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "::tpy::EnumUtil<Hue>::try_parse(s)" in cpp


class TestNativeRetCast:
    _SRC = ("from tpy import Int32, UInt64\n"
            "from tpy.extern import native\n"
            "@native(\"nx::wide_add\", cpp_return_type=UInt64)\n"
            "def wide_add(a: Int32, b: Int32) -> Int32: ...\n"
            "def use() -> Int32:\n"
            "    n = wide_add(1, 2)\n"
            "    return n\n")

    def test_cast_template_renders(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use") is not None
        assert w.get("call.native_ret_cast", 0) >= 1
        _assert_byte_identical(self._SRC)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "static_cast<int32_t>(::nx::wide_add(1, 2))" in cpp


class TestMethodUnionDcbpWrap:
    # A same-union NAME into a DEEP-CONST pointer-variant method slot takes
    # the ptr_variant_to_const wrap; a mutable slot stays bare.
    _SRC = (
        "from tpy import Int32, readonly\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self) -> None:\n        self.x = 1\n"
        "    @readonly\n"
        "    def get(self) -> Int32:\n        return self.x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self) -> None:\n        self.y = 2\n"
        "    @readonly\n"
        "    def get(self) -> Int32:\n        return self.y\n"
        "class Keeper:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n        self.n = 0\n"
        "    def peek(self, v: readonly[A | B]) -> Int32:\n"
        "        return self.n\n"
        "    def keep(self, v: A | B) -> None:\n"
        "        self.n = 1\n"
        "def use() -> Int32:\n"
        "    k = Keeper()\n"
        "    u: A | B = A()\n"
        "    k.keep(u)\n"
        "    return k.peek(u)\n"
    )

    def test_dcbp_wrap_and_bare_pass(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use") is not None
        assert w.get("unionlift.const_wrap", 0) >= 1
        assert w.get("method.union_pass_arg", 0) >= 1
        _assert_byte_identical(self._SRC)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "::tpy::ptr_variant_to_const<std::variant<const A*, const B*>>(u)" in cpp
        assert "k.keep(u);" in cpp


class TestMethodUnionDcbpGenericReceiver:
    # The dcbp verdict must come off the RAW method fi: the call-site
    # substituted copy drops deep_const_borrow_params, so a GENERIC
    # receiver's inferred deep-const union param silently lost the
    # ptr_variant_to_const wrap (review-caught divergence).
    _SRC = (
        "from tpy import Int32, readonly\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self) -> None:\n        self.x = 1\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self) -> None:\n        self.y = 2\n"
        "class Keeper[T]:\n"
        "    item: T\n"
        "    def __init__(self, item: T) -> None:\n        self.item = item\n"
        "    @readonly\n"
        "    def peek(self, v: A | B) -> Int32:\n"
        "        return 0\n"
        "def use() -> Int32:\n"
        "    k = Keeper(Int32(5))\n"
        "    u: A | B = A()\n"
        "    return k.peek(u)\n"
    )

    def test_generic_receiver_inferred_dcbp_wraps(self):
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(self._SRC)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert ("::tpy::ptr_variant_to_const<std::variant<const A*, "
                "const B*>>(u)") in cpp

    def test_narrowed_name_into_dcbp_slot_stays_ast(self):
        # A NARROWED union name into the deep-const slot is outside the
        # gate's dcbp admission -- the body keeps falling back.
        src = self._SRC.replace(
            "    u: A | B = A()\n    return k.peek(u)\n",
            "    u: A | B = A()\n"
            "    if isinstance(u, A):\n        return k.peek(u)\n"
            "    return 0\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestProtocolUnionContainerArg:
    def test_container_name_at_plain_union_slot(self):
        # A container NAME into an all-protocol (non-Optional-normalized)
        # UNION ctor slot -- ArrayList's src slot -- takes the same
        # &(name) lift.
        src = ("from tplib import ArrayList\n"
               "from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    xs: list[Int32] = [1, 2, 3]\n"
               "    al = ArrayList[Int32, 8](xs)\n"
               "    xs.append(4)\n"
               "    return len(al) + len(xs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.protocol_union_arg", 0) >= 1
        _assert_byte_identical(src)


class TestTryParseBoundaries:
    def test_kwarg_form_sema_unreachable(self):
        # `try_parse(Hue, name=s)` is rejected by SEMA (the builtin takes
        # positional args only), so the arm's `not e.kwargs` guard is
        # defensive -- pin the unreachability.
        import pytest
        from tpyc.diagnostics import SemanticError
        src = ("from enum import Enum\n"
               "from tpy import Int32, try_parse\n"
               "class Hue(Enum):\n"
               "    R = 0\n"
               "def use(s: str) -> Int32:\n"
               "    h = try_parse(Hue, name=s)\n"
               "    if h is None:\n        return -1\n"
               "    return 1\n")
        with pytest.raises(SemanticError):
            _lower_ctx(src)


class TestPrintNarrowedOptionalFieldDeclines:
    def test_narrowed_optional_container_field_stays_ast(self):
        # The AST prints print_optional_val over the WHOLE narrowed
        # Optional field; the kind-keyed field print form must decline
        # (the wave's corpus-caught divergence, pinned as a unit).
        src = ("from tpy import Int32\n"
               "class Bag:\n"
               "    items: list[Int32] | None\n"
               "    def __init__(self, items: list[Int32] | None) -> None:\n"
               "        self.items = items\n"
               "    def show(self) -> None:\n"
               "        if self.items is not None:\n"
               "            print(self.items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "show") is None
        _assert_byte_identical(src)


class TestDictViewItemsInstArg:
    def test_items_view_arg_routes(self):
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    m: dict[str, Int32] = {\"a\": 1, \"b\": 2}\n"
               "    d2 = dict(m.items())\n"
               "    return len(d2)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("call.inst_view_arg", 0) >= 1
        _assert_byte_identical(src)

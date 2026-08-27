"""Ctor-arg wave arms: the flush-less DIRECT own-move-source row, the
Own[list]-literal ctor row, the tuple-literal ctor slot row, the borrow-tuple
pointer-repr Optional element faces (None -> nullptr, plain name -> &(name)),
and the tuple-field print form."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_rejects_at, _assert_routes_byte_identical, _compile, _entry,
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

    def test_not_last_use_copy_temp_routes(self):
        # `a` read after the ctor: not a move source, so the Own slot takes
        # the copy temp -- and since wave 5 the decl-literal element
        # position IS a flush point (allow_temps threads through container
        # elements), so the hoist lands like the AST's
        # (`auto __tmp_1 = a;` + `{BoxI(std::move(__tmp_1))}`).
        src = (_ITEM
               + "def use() -> Int32:\n"
               + "    a = Item(2)\n"
               + "    boxes: list[BoxI] = [BoxI(a)]\n"
               + "    return a.n + len(boxes)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
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

    def test_narrowed_pointer_name_elem_passes_bare(self):
        # A post-if-narrowed Optional-ptr PARAM element is already the
        # pointer and passes bare (`{op, 4}`) -- the same-repr pointer-name
        # row. A narrowed STORAGE-form optional (a value-opt /
        # OPTIONAL_STORAGE binding) is still excluded: it is not an
        # already-pointer source.
        src = (_PAIR
               + "def use(op: Optional[Point]) -> Int32:\n"
               + "    if op is None:\n        return 0\n"
               + "    h = Hold((op, 4))\n"
               + "    return h.pair[1]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
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

    def test_items_list_decl_routes(self):
        # The blocker was the instantiation EXPR gate reusing the ELEMENT-keyed
        # `_storage_call_ret` verdict, not the decl slot: the ctor template
        # spells the whole `std::vector<std::tuple<..>>`, so a tuple element
        # renders what a scalar one does.
        src = ("from tpy import Int32\n"
               "def items_decl() -> Int32:\n"
               "    m: dict[str, Int32] = {\"a\": 1}\n"
               "    items = list(m.items())\n"
               "    return len(items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "items_decl") is not None
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


_ZONE = (
    "from tpy import Int32, Own\n"
    "class Zone:\n"
    "    off: Int32\n"
    "    name: str | None\n"
    "    def __init__(self, off: Int32, name: str | None = None) -> None:\n"
    "        self.off = off\n"
    "        self.name = name\n"
)


class TestValueOptMemberCtorArg:
    # The datetime `timezone(off, tz_intern.name_at(id))` shape: a
    # member-typed source at a value-repr Optional ctor slot. Both ctor
    # families reach the marker ladder's row now -- the optional's
    # converting ctor absorbs the bare member render, temp-free, which is
    # also what makes it admissible at the NESTED position.
    # One shape per case: a fixture carrying BOTH sources passes whole while
    # admission narrows to either one of them, since routing is asserted over
    # the module rather than per statement.
    def test_member_call_rvalue_source_routes(self):
        src = _ZONE + (
            "def label(i: Int32) -> str:\n"
            "    return \"UTC\"\n"
            "def show(z: Own[Zone]) -> Int32:\n"
            "    return z.off\n"
            "def main() -> None:\n"
            "    print(show(Zone(1, label(1))))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_member_name_source_routes(self):
        src = _ZONE + (
            "def mk(off: Int32, zname: str) -> Own[Zone]:\n"
            "    return Zone(off, zname)\n"
            "def main() -> None:\n"
            "    print(mk(2, \"CET\").off)\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_data_field_source_stays_ast(self):
        # A data FIELD read at the same slot keeps rejecting: only the
        # type-level enum-member read has a fixed spelling, while a data
        # field could be a narrowed optional whose AST render passes the
        # WHOLE optional.
        src = _ZONE + (
            "class Cfg:\n"
            "    label: str\n"
            "    def __init__(self, label: Own[str]) -> None:\n"
            "        self.label = label\n"
            "def mk(off: Int32, c: Cfg) -> Own[Zone]:\n"
            "    return Zone(off, c.label)\n"
            "def main() -> None:\n"
            "    print(mk(2, Cfg(\"CET\")).off)\n"
            "main()\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        _assert_rejects_at(dict(compiler._thir_fallback), "body:expr.call",
                           "call.ctor_arg.optional")
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


class TestPrintNarrowedOptionalField:
    def test_narrowed_optional_container_field_wraps_the_whole_optional(self):
        # The AST prints print_optional_val over the WHOLE narrowed Optional
        # field (field storage IS `std::optional<T>`), with the kind-keyed
        # Formatter its container inner needs -- NOT the plain container
        # printer the same read would take at a non-Optional field.
        src = ("from tpy import Int32\n"
               "class Bag:\n"
               "    items: list[Int32] | None\n"
               "    def __init__(self, items: list[Int32] | None) -> None:\n"
               "        self.items = items\n"
               "    def show(self) -> None:\n"
               "        if self.items is not None:\n"
               "            print(self.items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "show") is not None
        hpp, cpp = _assert_byte_identical(src)
        out = hpp + cpp
        assert ("::tpy::print_optional_val<::tpy::ListPrinter<std::vector"
                "<int32_t>>, std::vector<int32_t>>(this->items)") in out
        assert "::tpy::ListPrinter(this->items)" not in out


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


class TestFieldReadRefCtorArg:
    # A bare FIELD read into a ctor REF slot of the field's OWN declared type
    # binds the `const T&` slot directly, so the emit is the bare member read
    # (`Sink(this->xs)`). The rule is slot/field type EQUALITY, which is why
    # one row covers containers and open-T alike.
    _SRC = (
        "from tpy import Int32, Own\n"
        "class Sink:\n"
        "    items: list[Int32]\n"
        "    def __init__(self, items: list[Int32]) -> None:\n"
        "        self.items = items\n"
        "class Holder:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self, xs: Own[list[Int32]]) -> None:\n"
        "        self.xs = xs\n"
        "    def make(self) -> Own[Sink]:\n"
        "        return Sink(self.xs)\n")

    def test_container_field_ctor_arg_routes(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert w.get("ctor.field_read_ref_arg", 0) == 1
        _assert_byte_identical(
            self._SRC
            + "def main() -> None:\n    print(len(Holder([1, 2]).make().items))\n"
            + "main()\n")

    def test_open_t_field_ctor_arg_routes(self):
        # Same row, open-T slot: `Grid<T, N>(this->_value)`.
        src = ("from tpy import Int32, Own\n"
               "class Grid[T]:\n"
               "    _value: T\n"
               "    def __init__(self, value: T) -> None:\n"
               "        self._value = value\n"
               "    def clone(self) -> Own[Grid[T]]:\n"
               "        return Grid[T](self._value)\n")
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.field_read_ref_arg", 0) == 1
        _assert_byte_identical(
            src + "def main() -> None:\n"
            "    print(Grid[Int32](Int32(1)).clone()._value)\nmain()\n")

    def test_mutated_slot_still_defers(self):
        # BOUNDARY: type equality cannot see whether the callee writes through
        # the `T&`, so a MUTATED ctor slot keeps falling back.
        src = ("from tpy import Int32, Own\n"
               "class MutSink:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, items: list[Int32]) -> None:\n"
               "        items.append(99)\n        self.items = items\n"
               "class Holder:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self, xs: Own[list[Int32]]) -> None:\n"
               "        self.xs = xs\n"
               "    def make(self) -> Own[MutSink]:\n"
               "        return MutSink(self.xs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.field_read_ref_arg", 0) == 0
        assert _fn(thir, "make") is None

    def test_narrowed_optional_field_still_defers(self):
        # BOUNDARY: the row keys on the DECLARED field type, so a narrowed
        # `list | None` field (whose EXPR type is the bare container) stays
        # out -- its AST render takes the unwrap.
        src = ("from tpy import Int32, Own\n"
               "class Sink:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, items: list[Int32]) -> None:\n"
               "        self.items = items\n"
               "class Holder:\n"
               "    opt: list[Int32] | None\n"
               "    def __init__(self) -> None:\n        self.opt = None\n"
               "    def make(self) -> Own[Sink] | None:\n"
               "        if self.opt is not None:\n"
               "            return Sink(self.opt)\n"
               "        return None\n")
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.field_read_ref_arg", 0) == 0


class TestDictSetLiteralCtorArg:
    # A dict / set literal at a ctor container slot renders SPELLED and
    # INLINE (`Config(::tpy::ordered_map<std::string, Int32>({{..}}))`) --
    # the stub-method twin's render, not the list arm's bare brace, and
    # without the `__tmp_N` hoist a plain FREE call would take.
    _SRC = (
        "from tpy import Int32, Own, readonly\n"
        "class DictSink:\n"
        "    d: dict[str, Int32]\n"
        "    def __init__(self, d: dict[str, Int32]) -> None:\n"
        "        self.d = d\n"
        "class SetSink:\n"
        "    s: set[Int32]\n"
        "    def __init__(self, s: set[Int32]) -> None:\n"
        "        self.s = s\n")

    def test_dict_literal_ctor_arg_routes_spelled(self):
        src = (self._SRC
               + "def build() -> Int32:\n"
               + '    s = DictSink({"a": 1, "b": 2})\n'
               + "    return len(s.d)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "build") is not None
        assert w.get("ctor.container_literal_arg", 0) == 1
        _assert_byte_identical(
            src + "def main() -> None:\n    print(build())\nmain()\n")

    def test_set_literal_ctor_arg_routes(self):
        src = (self._SRC
               + "def build() -> Int32:\n"
               + "    s = SetSink({1, 2, 3})\n"
               + "    return len(s.s)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "build") is not None
        assert w.get("ctor.container_literal_arg", 0) == 1
        _assert_byte_identical(
            src + "def main() -> None:\n    print(build())\nmain()\n")

    def test_other_dict_slot_flavors_stay_identical(self):
        # BOUNDARY sweep: the row keeps its Own / Optional / readonly /
        # mutated exclusions, and a plain FREE call still takes the ref-param
        # temp hoist rather than this inline render. Each of these reaches a
        # DIFFERENT pre-existing row (or falls back); what must hold either
        # way is byte-identity -- this pin fails if the widened ctor arm ever
        # swallows one of them.
        src = (self._SRC
               + "class OwnSink:\n"
               + "    d: dict[str, Int32]\n"
               + "    def __init__(self, d: Own[dict[str, Int32]]) -> None:\n"
               + "        self.d = d\n"
               + "class RoSink:\n"
               + "    n: Int32\n"
               + "    def __init__(self, d: readonly[dict[str, Int32]]) -> None:\n"
               + "        self.n = len(d)\n"
               + "class MutSink:\n"
               + "    d: dict[str, Int32]\n"
               + "    def __init__(self, d: dict[str, Int32]) -> None:\n"
               + '        d["extra"] = 1\n        self.d = d\n'
               + "def free_takes_dict(d: dict[str, Int32]) -> Int32:\n"
               + "    return len(d)\n"
               + "def main() -> None:\n"
               + '    print(len(OwnSink({"c": 3}).d))\n'
               + '    print(RoSink({"d": 4}).n)\n'
               + '    print(len(MutSink({"e": 5}).d))\n'
               + '    print(free_takes_dict({"f": 6}))\n'
               + "main()\n")
        _assert_byte_identical(src)


class TestBytearrayTypeCtor:
    # `bytearray()` / `bytearray(n)` expand through the type-ctor template to
    # the plain `std::vector<uint8_t>(...)` construction -- the same kind as
    # the scalar ctors beside them; the result kind was simply missing.
    def test_zero_arg_bytearray_routes(self):
        src = ("from tpy import Int32\n"
               "def f() -> Int32:\n"
               "    ba = bytearray()\n"
               "    ba.append(65)\n"
               "    return len(ba)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("call.type_ctor.bytearray", 0) == 1
        _assert_byte_identical(
            src + "def main() -> None:\n    print(f())\nmain()\n")

    def test_argful_bytearray_takes_the_plain_call_path(self):
        # BOUNDARY, and the reason the row is arg-less: `bytearray(n)` and
        # `bytearray(b"..")` never reach the type-ctor arm -- they resolve as
        # plain calls, so the free-call arg ladder decides them (both forms
        # route there now). An arg rule on this row would be dead code: what
        # this pins is that the type-ctor face stays UNWITNESSED either way.
        sized = ("from tpy import Int32\n"
                 "def f() -> Int32:\n"
                 "    ba = bytearray(3)\n"
                 "    return len(ba)\n")
        thir, w = _lower_ctx_witnessed(sized)
        assert _fn(thir, "f") is not None
        assert w.get("call.type_ctor.bytearray", 0) == 0
        _assert_byte_identical(
            sized + "def main() -> None:\n    print(f())\nmain()\n")
        from_bytes = ("from tpy import Int32\n"
                      "def f() -> Int32:\n"
                      '    ba = bytearray(b"abc")\n'
                      "    return len(ba)\n")
        thir2, w2 = _lower_ctx_witnessed(from_bytes)
        assert _fn(thir2, "f") is not None
        assert w2.get("call.type_ctor.bytearray", 0) == 0
        _assert_byte_identical(
            from_bytes + "def main() -> None:\n    print(f())\nmain()\n")

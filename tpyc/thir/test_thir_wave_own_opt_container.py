"""Pins for the argparse Own[Optional[container]] chain, three arms:
the MIL move (`tag(std::move(tag))` -- an Own storage-optional param
into the Optional[container] field; its borrow sibling lifts a
pointer-repr Optional param through `ptr_to_optional`), the
OPT_PTR_SLOT reseat from a
container-returning by-value call (`p = &*(__slot_N = <rvalue>);`), and
the ctor-arg inline materialization
(`std::move(p ? std::optional<V>(std::move(*p)) : std::nullopt)` --
the AST's gen_expr_deref Own[Optional] arm, which moves the pointee
UNCONDITIONALLY and is inner-type-agnostic; the THIR row keeps the
witnessed container slice). The pointer-local container COPY row
(`V((*acc))` -- the macro accumulator's `.copy()`) is durably witnessed
by the FLIPPED argparse/nargs + optional_list_absent cases (the ratchet
enforces their routing; no synthetic fixture reaches that exact macro
shape)."""

from __future__ import annotations

from .nodes import Form, THIRFieldAccess, THIRFormConvert, THIRSetItem
from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctor,
    _lower_ctx_witnessed,
)

_HDR = "from tpy import Int32, Own\n"


class TestOwnOptContainerChain:
    def test_argparse_shaped_chain_routes(self):
        # The three arms in one flow: Own[Optional[list]] ctor param moves
        # into the field (MIL); the caller's OPT_PTR_SLOT local reseats
        # from a container COPY-instantiation rvalue (`list(acc)` -- the
        # argparse builder's accumulator copy); the ctor arg materializes
        # the storage optional inline.
        src = _HDR + (
            "class Holder:\n"
            "    tags: list[str] | None\n"
            "    def __init__(self, tags: Own[list[str] | None]) -> None:\n"
            "        self.tags = tags\n"
            "def build(flag: bool) -> Own[Holder]:\n"
            "    acc: list[str] = ['a', 'b']\n"
            "    tag: list[str] | None = None\n"
            "    if flag:\n"
            "        tag = list(acc)\n"
            "    return Holder(tag)\n"
            "def main() -> None:\n"
            "    h = build(True)\n"
            "    print(h.tags)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "build") is not None
        assert faces["arg.own_opt_container_move"] >= 1
        assert faces["reseat.opt_rvalue"] >= 1
        _assert_byte_identical(src)

    def test_narrowed_opt_field_subscript_read_routes(self):
        # `a.coord[0]` after the assert-narrow: the receiver types at the
        # narrowed INNER container (narrowed_ok, READ positions only) and
        # renders the `(*a.coord)` deref under __getitem__.
        src = _HDR + (
            "class Args:\n"
            "    coord: list[Int32] | None\n"
            "    def __init__(self, c: Own[list[Int32] | None]) -> None:\n"
            "        self.coord = c\n"
            "def read(a: Args) -> Int32:\n"
            "    assert a.coord is not None\n"
            "    return a.coord[0]\n"
            "def main() -> None:\n"
            "    print(read(Args([5, 6])))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "read") is not None
        _assert_byte_identical(src)

    def test_narrowed_opt_field_setitem_routes(self):
        # The WRITE flavor takes the same narrowed_ok receiver unwrap as the
        # read above -- `::tpy::__setitem__((*a.coord), 0, 9)`. The
        # un-narrowed write is sema-rejected outright ("Cannot assign to
        # elements of `list[Int32] | None` (read-only)"), so there is no
        # unproven shape this widening could mis-admit.
        src = _HDR + (
            "class Args:\n"
            "    coord: list[Int32] | None\n"
            "    def __init__(self, c: Own[list[Int32] | None]) -> None:\n"
            "        self.coord = c\n"
            "def write(a: Args) -> None:\n"
            "    assert a.coord is not None\n"
            "    a.coord[0] = 9\n"
            "def main() -> None:\n"
            "    a = Args([5, 6])\n"
            "    write(a)\n"
            "    print(a.coord)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        stmt = _fn(thir, "write").body[1]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.target.receiver, THIRFieldAccess)
        _assert_byte_identical(src)

    def test_narrowed_opt_field_record_elem_setitem_routes(self):
        # The record-element flavor the widened receiver newly reaches: the
        # element move renders `__setitem__((*this->items), 0, std::move(b))`.
        src = _HDR + (
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class RecElems:\n"
            "    items: list[Box] | None\n"
            "    def __init__(self, c: Own[list[Box] | None]) -> None:\n"
            "        self.items = c\n"
            "    def write(self, b: Own[Box]) -> None:\n"
            "        assert self.items is not None\n"
            "        self.items[0] = b\n"
            "def main() -> None:\n"
            "    r = RecElems([Box(1)])\n"
            "    r.write(Box(9))\n"
            "    print(r.items is None)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        stmt = _fn(thir, "write").body[1]
        assert isinstance(stmt, THIRSetItem)
        _assert_byte_identical(src)

    def test_narrowed_opt_field_delitem_still_defers(self):
        # The DEL gate keeps the DECLARED slice: `narrowed_ok` is threaded
        # from the read and setitem call sites only, so the same narrowed
        # Optional field rejects here.
        src = _HDR + (
            "class Args:\n"
            "    d: dict[str, Int32] | None\n"
            "    def __init__(self, d: dict[str, Int32] | None) -> None:\n"
            "        self.d = d\n"
            "    def drop(self) -> None:\n"
            "        assert self.d is not None\n"
            "        del self.d['a']\n"
            "def main() -> None:\n"
            "    a = Args({'a': 1})\n"
            "    a.drop()\n"
            "    print(a.d is None)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "drop") is None
        _assert_byte_identical(src)

    def test_opt_container_mil_borrow_ptr_lift_routes(self):
        # The MIL twin of the Optional[F1-record] borrow lift: a pointer-repr
        # Optional[container] PARAM renders `T*` and lifts into the storage
        # `std::optional<...>` field through `ptr_to_optional`.
        src = _HDR + (
            "class Buf:\n"
            "    items: list[Int32] | None\n"
            "    def __init__(self, items: list[Int32] | None) -> None:\n"
            "        self.items = items\n"
            "def main() -> None:\n"
            "    b = Buf([1, 2])\n"
            "    print(b.items is None)\n"
        )
        ctor = _lower_ctor(src, "Buf")
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["items"]
        init = ctor.mil_inits[0].value
        assert isinstance(init, THIRFormConvert)
        assert init.form is Form.STORAGE and not init.move
        _assert_byte_identical(src)

    def test_opt_container_mil_literal_takes_its_own_row(self):
        # A container LITERAL at the same slot is neither a move source nor a
        # borrow pointer: it has a row of its own that threads the Optional's
        # INNER and prefixes the container spelling (an Optional target cannot
        # deduce a bare brace-init), so it must NOT arrive as a FormConvert.
        src = _HDR + (
            "class Buf:\n"
            "    items: list[Int32] | None\n"
            "    def __init__(self) -> None:\n"
            "        self.items = [1, 2, 3]\n"
            "def main() -> None:\n"
            "    b = Buf()\n"
            "    print(b.items is None)\n"
        )
        ctor = _lower_ctor(src, "Buf")
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["items"]
        init = ctor.mil_inits[0].value
        assert not isinstance(init, THIRFormConvert)
        assert init.typed_brace_cpp == "std::vector<int32_t>"
        _assert_byte_identical(src)

    def test_still_live_ctor_arg_still_defers(self):
        # THE REVIEW-CAUGHT boundary: a STILL-LIVE pointer local at the
        # Own[Optional[container]] slot -- the AST hoists the ternary into
        # a temp (`auto __tmp_N = ...; Holder(std::move(__tmp_N))`), an
        # unmirrored render; the inline row is last-use-gated
        # (_is_move_source, which also feeds the move-verdict join).
        src = _HDR + (
            "class Holder:\n"
            "    tags: list[str] | None\n"
            "    def __init__(self, tags: Own[list[str] | None]) -> None:\n"
            "        self.tags = tags\n"
            "def build_live(flag: bool) -> Int32:\n"
            "    acc: list[str] = ['a']\n"
            "    tag: list[str] | None = None\n"
            "    if flag:\n"
            "        tag = list(acc)\n"
            "    h = Holder(tag)\n"
            "    if tag is not None:\n"
            "        return len(tag)\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(build_live(True))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "build_live") is None
        assert not faces.get("arg.own_opt_container_move")
        _assert_byte_identical(src)

    def test_record_pointee_ctor_arg_still_defers(self):
        # The RECORD-pointee flavor of the Own[Optional] ctor slot stays
        # on the pre-existing M3b machinery / AST path -- the container
        # row must not take it.
        src = _HDR + (
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: P | None\n"
            "    def __init__(self, p: Own[P | None]) -> None:\n"
            "        self.p = p\n"
            "def build(flag: bool) -> Own[Holder]:\n"
            "    q: P | None = None\n"
            "    if flag:\n"
            "        q = P(1)\n"
            "    return Holder(q)\n"
            "def main() -> None:\n"
            "    h = build(True)\n"
            "    print(h.p is None)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("arg.own_opt_container_move")
        _assert_byte_identical(src)

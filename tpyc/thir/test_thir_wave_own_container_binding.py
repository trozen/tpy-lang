"""The `Own[container]` LOOP-VAR binding read: the `auto&& row` element
reads bare and takes the shared movable-last-use move at an owning sink, and
its method calls dispatch on the payload (the receiver peel the container
family membership core withholds because it also answers ELEMENT-slot
questions, where the move-in ABI matters).

Boundaries: the PARAM of the same type is movable-seeded too but keeps its
reject (a deliberate slice, fenced in seven other units); an Own payload
outside the container families (bytearray) or outside the seeded slice
(a value type) keeps the `name.own_read` reject.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)


class TestOwnContainerLoopVar:
    _SRC = (
        "from tpy import own_iter\n"
        "def use() -> None:\n"
        "    src: list[list[str]] = [[\"a\", \"b\"], [\"c\"]]\n"
        "    kept: list[list[str]] = []\n"
        "    for row in own_iter(src):\n"
        "        print(len(row), \"|\".join(row))\n"
        "        row.append(\"EXTRA\")\n"
        "        kept.append(row)\n"
        "    print(kept[0])\n"
        "def main() -> None:\n"
        "    use()\n"
        "main()\n"
    )

    def test_reads_receiver_and_move_route(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert "auto&& row = *__beg_0;" in out
        # Bare reads, payload-dispatched method call, moved last use.
        assert "::tpy::__len__(row)" in out
        assert 'row.push_back("EXTRA");' in out
        assert "kept.push_back(std::move(row));" in out


class TestOwnContainerBindingBoundaries:
    def test_own_container_param_read_stays_ast(self):
        # BOUNDARY: the PARAM of the loop var's own type is movable-seeded
        # too, so the row must NOT key on the working set alone.
        src = ("from tpy import Own, Int32\n"
               "def take(v: Own[list[str]]) -> Int32:\n"
               "    return Int32(len(v))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "take") is None
        _assert_byte_identical(src)

    def test_own_bytearray_param_read_stays_ast(self):
        # BOUNDARY: bytearray is movable-seeded (non-value) but outside the
        # list/dict/set families the row names -- its bare read keeps the
        # reject, so the row is keyed on the container family, not on
        # "movable Own".
        src = ("from tpy import Own, Int32\n"
               "def take(b: Own[bytearray]) -> Int32:\n"
               "    return Int32(len(b))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "take") is None
        _assert_byte_identical(src)

    def test_own_strview_param_read_stays_ast(self):
        # BOUNDARY: a VALUE payload is never movable-seeded, so the
        # movable_local key never opens for it.
        src = ("from tpy import Own, StrView, Int32\n"
               "def take(v: Own[StrView]) -> Int32:\n"
               "    return Int32(len(v))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "take") is None
        _assert_byte_identical(src)


class TestNarrowedOptContainerRecv:
    """The narrowed pointer-repr `Optional[container]` NAME receiver: the
    proven `T*` binding dispatches on the payload (`xs->push_back(..)`), the
    container twin of the record arm's `_optional_ptr_borrow` unwrap. Sema
    forbids the un-narrowed call, so reaching lowering implies the proof."""

    _SRC = (
        "from tpy import Int32\n"
        "def add_lit(xs: list[str] | None) -> None:\n"
        "    if xs is not None:\n"
        "        xs.append(\"lit\")\n"
        "def add_num(ns: list[Int32] | None) -> None:\n"
        "    if ns is not None:\n"
        "        ns.append(3)\n"
        "def put(d: dict[str, Int32] | None) -> None:\n"
        "    if d is not None:\n"
        "        d[\"k\"] = 1\n"
        "def main() -> None:\n"
        "    a: list[str] = []\n"
        "    add_lit(a)\n"
        "    print(a)\n"
        "    b: list[Int32] = []\n"
        "    add_num(b)\n"
        "    print(b)\n"
        "    c: dict[str, Int32] = {}\n"
        "    put(c)\n"
        "    print(len(c))\n"
        "main()\n"
    )

    def test_narrowed_param_recv_routes(self):
        _t, w = _lower_ctx_witnessed(self._SRC)
        assert w.get("method.opt_ptr_container_recv", 0) >= 2
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out = hpp + cpp
        assert 'xs->push_back("lit");' in out
        assert "ns->push_back(3);" in out
        assert '::tpy::__setitem__((*d), "k", 1);' in out

    def test_narrowed_field_recv_keeps_its_own_row(self):
        # BOUNDARY: the one-level FIELD receiver of the same declared type
        # already had a route (`method.recv.container_field`, deref not
        # arrow) -- this row must not capture it.
        src = ("class Holder:\n"
               "    xs: list[str] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.xs = None\n"
               "    def add(self) -> None:\n"
               "        if self.xs is not None:\n"
               "            self.xs.append(\"b\")\n")
        _t, w = _lower_ctx_witnessed(src)
        assert w.get("method.opt_ptr_container_recv", 0) == 0
        assert w.get("method.recv.container_field", 0) >= 1
        cpp = _assert_byte_identical(src)
        assert '(*this->xs).push_back("b");' in (cpp[0] + cpp[1])

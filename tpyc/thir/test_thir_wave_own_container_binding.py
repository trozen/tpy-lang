"""The `Own[container]` LOOP-VAR binding read: the `auto&& row` element
reads bare and takes the shared movable-last-use move at an owning sink, and
its method calls dispatch on the payload (the receiver peel the container
family membership core withholds because it also answers ELEMENT-slot
questions, where the move-in ABI matters).

The PARAM of the same type is movable-seeded on both paths for the same
reason, so it reads bare and moves at its last use alongside the loop var --
one position excepted: as the SIMPLE-GENERATOR for-head iterable, where the
skeleton classifies the iteration strategy off the un-unwrapped binding and
so captures the universal iterator where the leaf would spell begin/end.
That seam declines the name itself.

Boundaries: an Own payload outside the container families (bytearray) or
outside the seeded slice (a value type) keeps the `name.own_read` reject.
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
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

    def test_own_bytearray_param_read_stays_ast(self):
        # BOUNDARY: bytearray is movable-seeded (non-value) but outside the
        # list/dict/set families the row names -- its bare read keeps the
        # reject, so the row is keyed on the container family, not on
        # "movable Own".
        src = ("from tpy import Own, Int32\n"
               "def take(b: Own[bytearray]) -> Int32:\n"
               "    return Int32(len(b))\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:name.own_read")

    def test_own_strview_param_read_stays_ast(self):
        # BOUNDARY: a VALUE payload is never movable-seeded, so the
        # movable_local key never opens for it.
        src = ("from tpy import Own, StrView, Int32\n"
               "def take(v: Own[StrView]) -> Int32:\n"
               "    return Int32(len(v))\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:name.own_read")


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


_HISTORY = (
    "from tpy import Own, Int32\n"
    "from typing import Iterator\n"
    "class R:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
)


class TestOwnContainerParamBinding:
    """The `Own[container]` PARAM read, the loop var's twin: the signature
    spells the container by value and `seed_param_locals` marks it movable,
    so reads render bare and the shared move-source rows move the last one
    at an owning sink. Corpus witness: tplib/requests' `Session._hop`, whose
    `history` param is read bare by `len`, moved into `resp.history` and
    into the recursive `Own` arg slot, and receives `push_back`."""

    _HOP = (_HISTORY
            + "class S:\n"
            + "    n: Int32\n"
            + "    def __init__(self) -> None:\n        self.n = 0\n"
            + "    def hop(self, history: Own[list[R]], k: Int32) -> Int32:\n"
            + "        if k <= 0:\n"
            + "            return len(history)\n"
            + "        history.append(R(k))\n"
            + "        return self.hop(history, k - 1)\n"
            + "def main() -> None:\n"
            + "    s = S()\n"
            + "    xs: list[R] = []\n"
            + "    print(s.hop(xs, 2))\n"
            + "main()\n")

    def test_hop_shape_routes(self):
        _assert_routes_byte_identical(self._HOP)

    def test_hop_shape_renders_bare_reads_and_a_moved_last_use(self):
        hpp, cpp = _assert_byte_identical(self._HOP)
        out = hpp + cpp
        assert "::tpy::__len__(history)" in out
        assert "history.push_back(" in out
        assert "this->hop(std::move(history), " in out

    def test_field_sink_moves_the_param(self):
        src = (_HISTORY
               + "class H:\n"
               + "    xs: list[R]\n"
               + "    def __init__(self) -> None:\n        self.xs = []\n"
               + "    def put(self, ys: Own[list[R]]) -> Int32:\n"
               + "        self.xs = ys\n"
               + "        return len(self.xs)\n"
               + "def main() -> None:\n"
               + "    h = H()\n"
               + "    zs: list[R] = []\n"
               + "    print(h.put(zs))\n"
               + "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "this->xs = std::move(ys);" in hpp + cpp

    def test_dict_and_set_payloads_route(self):
        src = (_HISTORY
               + "def d(m: Own[dict[Int32, Int32]]) -> Int32:\n"
               + "    return len(m)\n"
               + "def s(t: Own[set[Int32]]) -> Int32:\n"
               + "    return len(t)\n"
               + "def main() -> None:\n"
               + "    print(d({1: 2}) + s({3}))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_sgen_for_head_over_the_param_stays_ast(self):
        # BOUNDARY: the one position where the two paths disagree. The
        # peephole skeleton reads the DECLARED binding to pick its iteration
        # strategy and does not unwrap Own, so it captures the universal
        # iterator object; the leaf would spell begin/end off the payload.
        src = (_HISTORY
               + "def drain(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    for x in xs:\n"
               + "        yield x + len(xs)\n"
               + "def main() -> None:\n"
               + "    for u in drain([1, 2, 3]):\n"
               + "        print(u)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:sgen.iterable_own_binding")

    def test_sgen_holding_the_param_off_the_for_head_routes(self):
        # ... and the complement: the same param in every OTHER position of
        # a peephole body routes, so the seam's decline is keyed on the
        # iterable name and not on the body being a generator.
        src = (_HISTORY
               + "def g(xs: Own[list[Int32]], n: Int32) -> Iterator[Int32]:\n"
               + "    base = len(xs)\n"
               + "    for i in range(n):\n"
               + "        xs.append(i)\n"
               + "        yield i + base + xs[0]\n"
               + "def main() -> None:\n"
               + "    for u in g([1, 2], 2):\n"
               + "        print(u)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_sync_for_head_over_the_param_stays_ast(self):
        # BOUNDARY: the sync twin declines the same binding at its own
        # for-head for the same reason, so the two seams stay in step.
        src = (_HISTORY
               + "def f(xs: Own[list[Int32]]) -> Int32:\n"
               + "    t: Int32 = 0\n"
               + "    for x in xs:\n"
               + "        t += x\n"
               + "    return t\n"
               + "def main() -> None:\n"
               + "    print(f([1, 2]))\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:iter.name_shape")

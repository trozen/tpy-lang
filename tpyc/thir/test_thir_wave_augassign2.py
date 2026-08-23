"""Aug-assign long-tail rows: the borrow-call field receiver
(`o.get().v += 1`), the raw-Ptr receiver field (`p.n += 1`), the list
FIELD extend (`self.items += [..]`), and the user-record aug-setitem
(`items[0] += 5` -- operator[] read + the fixed ::tpy::__setitem__
write)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestAugAssignLongTail:
    def test_call_receiver_field_aug_routes(self):
        src = ("from tpy import Int32\n"
               "class Cell:\n"
               "    v: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.v = 1\n"
               "class Holder:\n"
               "    c: Cell\n"
               "    def __init__(self) -> None:\n"
               "        self.c = Cell()\n"
               "    def get(self) -> Cell:\n"
               "        return self.c\n"
               "def main() -> None:\n"
               "    h = Holder()\n"
               "    h.get().v += 1\n"
               "    print(h.c.v)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert ("h.get().v = ::tpy::add_check<int32_t>(h.get().v, 1);"
                in cpp[1])

    def test_ptr_receiver_field_aug_routes(self):
        # An lvalue coerces to the Ptr param (record_to_ptr).
        src = ("from tpy import Int32, Ptr\n"
               "class Tag:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n"
               "def bump(p: Ptr[Tag]) -> None:\n"
               "    p.n += 1\n"
               "def main() -> None:\n"
               "    t = Tag()\n"
               "    bump(t)\n"
               "    print(t.n)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "bump") is not None
        _assert_byte_identical(src)

    def test_field_list_extend_routes(self):
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.xs = [1]\n"
               "    def add(self) -> None:\n"
               "        self.xs += [2, 3]\n"
               "def main() -> None:\n"
               "    h = H()\n"
               "    h.add()\n"
               "    print(len(h.xs))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "add") is not None
        assert faces.get("aug.container_inplace", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::list_extend(this->xs, std::vector<int32_t>{2, 3});"
                in cpp[0])

    def test_record_aug_setitem_routes(self):
        src = ("from tpy import Int32\n"
               "from tplib import ArrayList\n"
               "def main() -> None:\n"
               "    items = ArrayList[Int32, 4]()\n"
               "    items.append(100)\n"
               "    items[0] += 5\n"
               "    print(items[0])\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("setitem.record_aug", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::__setitem__(items, 0, "
                "::tpy::add_check<int32_t>(items[0], 5));" in cpp[1])

    def test_record_without_setitem_aug_read_only(self):
        # The inverse guard: a getitem-only record never admits the aug
        # pair (sema rejects the write; the gate's registry check is the
        # mirror) -- exercised here as the read staying routable while no
        # aug target exists in the corpus for such a record.
        src = ("from tpy import Int32\n"
               "class RO:\n"
               "    v: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.v = 7\n"
               "    def __getitem__(self, i: Int32) -> Int32:\n"
               "        return self.v\n"
               "def main() -> None:\n"
               "    r = RO()\n"
               "    print(r[0])\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_record_aug_setitem_narrowed_opt_recv_routes(self):
        # A None-narrowed ptr-repr Optional record receiver: both halves of
        # the aug pair spell the `(*items)` deref. (Was the stand-in for the
        # slice exclusion, which sema rejects and so cannot be written here;
        # that exclusion now lives only in the gate.)
        src = ("from tpy import Int32\n"
               "from tplib import ArrayList\n"
               "def use(items: ArrayList[Int32, 4] | None) -> None:\n"
               "    if items is not None:\n"
               "        items[0] += 5\n"
               "def main() -> None:\n"
               "    a = ArrayList[Int32, 4]()\n"
               "    a.append(1)\n"
               "    use(a)\n"
               "    print(a[0])\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("setitem.record_aug", 0) == 1
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) >= 1
        cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::__setitem__((*items), 0, "
                "::tpy::add_check<int32_t>((*items)[0], 5));" in cpp[1])

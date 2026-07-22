"""Owned-optional record slot wave arms: the `std::optional<Rc<T>>` decl
from an owned-optional-returning call, its narrowed `(*name)` receiver
reads and has_value None-test, the storage-sink owned-record method-call
rvalue, and the optional-field write off an accessor-call receiver."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_CELL = (
    "from tpy import Int32\n"
    "from tplib.rc import Rc, Weak\n"
    "class Cell:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.val = v\n"
)


class TestOwnOptRecordSlot:
    def test_upgrade_slot_and_narrowed_reads_route(self):
        src = (_CELL
               + "def use(w: Weak[Cell]) -> None:\n"
               + "    u = w.upgrade()\n"
               + "    if u is None:\n"
               + "        print(-1)\n"
               + "        return\n"
               + "    print(u.get().val)\n"
               + "    u.get().val = 5\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("decl.opt_record_call", 0) >= 1
        assert w.get("name.opt_record_deref", 0) >= 1
        assert w.get("name.opt_record_whole", 0) >= 1
        _assert_byte_identical(src)

    def test_reassigned_slot_stays_ast(self):
        # A reassigned optional-record local keeps the AST pointer
        # machinery; the decl gate's prescan guard must hold.
        src = (_CELL
               + "def use(w: Weak[Cell]) -> None:\n"
               + "    u = w.upgrade()\n"
               + "    u = w.upgrade()\n"
               + "    if u is not None:\n"
               + "        print(u.get().val)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_own_optional_param_stays_ast(self):
        # An `Own[Rc[T]] | None` PARAM has no registered binding -- its read
        # keeps the unrouted-binding reject (the registration is decl-only).
        src = (_CELL
               + "from tpy import Own\n"
               + "def use(u: Own[Rc[Cell]] | None) -> None:\n"
               + "    if u is not None:\n"
               + "        print(u.get().val)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestAccessorReceiverOptFieldWrite:
    _NODE = (
        "from tpy import Int32\n"
        "from tplib.rc import Rc\n"
        "class Node:\n"
        "    value: Int32\n"
        "    next: Rc[Node] | None\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.value = v\n"
        "        self.next = None\n"
    )

    def test_clone_rvalue_into_accessor_field_routes(self):
        src = (self._NODE
               + "def use(a: Rc[Node], b: Rc[Node]) -> None:\n"
               + "    a.get().next = b.clone()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_borrow_accessor_value_stays_ast(self):
        # A `T&`-returning accessor VALUE (`n.byval = h.get()` on a copyable
        # record) is a borrow, not the Own-rvalue slice: is_rvalue_source
        # gates it out, so the body keeps falling back (byte-identical via
        # AST).
        src = ("from tpy import Int32, copy\n"
               "class Val:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Holder:\n"
               "    v: Val\n"
               "    byval: Val | None\n"
               "    def __init__(self, v: Val) -> None:\n"
               "        self.v = copy(v)\n"
               "        self.byval = None\n"
               "    def get(self) -> Val:\n"
               "        return self.v\n"
               "def use(h: Holder, n: Holder) -> None:\n"
               "    n.byval = h.get()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

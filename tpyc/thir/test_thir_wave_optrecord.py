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

    def test_own_optional_param_routes_record_kind(self):
        # An `Own[Rc[T]] | None` PARAM rides the RECORD-kind param seed:
        # the has_value None-test and the `(*u)` narrowed receiver deref.
        src = (_CELL
               + "from tpy import Own\n"
               + "def use(u: Own[Rc[Cell]] | None) -> None:\n"
               + "    if u is not None:\n"
               + "        print(u.get().val)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        cpp = _assert_byte_identical(src)
        assert "if ((u.has_value()))" in cpp[1]
        assert "(*u).get().val" in cpp[1]


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


class TestValueOptRecordSlot:
    """The VALUE-record twin (`decl.opt_value_record`): an
    `Optional[Coord]` slot off a property/call binds the plain spelled
    `std::optional<Coord>` copy; the None-test reads has_value off the
    registered binding. Reassigned targets stay AST; a narrowed FIELD
    read off the local is a separate unported read row (atomic
    fallback, byte-identical)."""

    _SRC = (
        "from typing import Optional\n"
        "from tpy import Int32, ValueType\n"
        "class Coord(ValueType):\n"
        "    x: Int32\n"
        "    y: Int32\n"
        "    def __init__(self, x: Int32, y: Int32) -> None:\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "class Track:\n"
        "    _has: bool\n"
        "    def __init__(self) -> None:\n"
        "        self._has = True\n"
        "    @property\n"
        "    def goal(self) -> Optional[Coord]:\n"
        "        if not self._has:\n"
        "            return None\n"
        "        return Coord(1, 2)\n"
    )

    def test_property_slot_and_none_test_route(self):
        src = (self._SRC
               + "def use(t: Track) -> None:\n"
               + "    g = t.goal\n"
               + "    print(g is None)\n"
               + "use(Track())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.opt_value_record")
        _assert_byte_identical(src)

    def test_reassigned_target_stays_out(self):
        src = (self._SRC
               + "def use(t: Track) -> None:\n"
               + "    g = t.goal\n"
               + "    print(g is None)\n"
               + "    g = t.goal\n"
               + "    print(g is None)\n"
               + "use(Track())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is None
        assert not faces.get("decl.opt_value_record")
        _assert_byte_identical(src)

    def test_narrowed_field_read_routes(self):
        # The `(*g).x` receiver read ported in grind wave 7
        # (field.opt_record_recv): the narrowed owned-optional record name
        # derefs and the field appends -- byte-identical.
        src = (self._SRC
               + "def use(t: Track) -> Int32:\n"
               + "    g = t.goal\n"
               + "    if g is not None:\n"
               + "        return g.x\n"
               + "    return -1\n"
               + "print(use(Track()))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("field.opt_record_recv", 0) >= 1
        _assert_byte_identical(src)


class TestPropertyIsNoneSubject:
    """A @property read as the DIRECT `is [not] None` subject (`w.node is
    None`): the AST sees an Optional-typed FieldAccess (storage-form
    source), so the render is has_value over the getter call -- any repr
    (the property getter skips the plain-method optional_to_ptr lift).
    The OPTIONAL_TO_PTR decl off a property (`n = w.node`) wraps the same
    bare call in the lift. A plain METHOD-call subject keeps falling back
    (its AST render is the borrow-form `T*` + nullptr compare)."""

    _SRC = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "class Wrapper:\n"
        "    _node: Optional[Node]\n"
        "    _port: Optional[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._node = None\n"
        "        self._port = None\n"
        "    @property\n"
        "    def node(self) -> Optional[Node]:\n"
        "        return self._node\n"
        "    @node.setter\n"
        "    def node(self, n: Optional[Node]) -> None:\n"
        "        self._node = n\n"
        "    @property\n"
        "    def port(self) -> Optional[Int32]:\n"
        "        return self._port\n"
        "    def find(self) -> Optional[Node]:\n"
        "        return self._node\n"
    )

    def test_property_subjects_route(self):
        src = (self._SRC
               + "def probe(w: Wrapper) -> None:\n"
               + "    print(w.node is None)\n"
               + "    print(w.node is not None)\n"
               + "    print(None is w.node)\n"
               + "    print(w.port is None)\n"
               + "probe(Wrapper())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert faces.get("isnone.property_subject", 0) >= 4
        _assert_byte_identical(src)

    def test_property_decl_lift_routes(self):
        # `n = w.node` -> `Node* n = ::tpy::optional_to_ptr(w.node());`
        src = (self._SRC
               + "def probe(w: Wrapper) -> Int32:\n"
               + "    n = w.node\n"
               + "    if n is not None:\n"
               + "        return n.val\n"
               + "    return -1\n"
               + "print(probe(Wrapper()))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert faces.get("decl.opt_property_lift", 0) >= 1
        _assert_byte_identical(src)

    def test_plain_method_subject_still_defers(self):
        # The method-call sibling keeps the whole-body fallback: the AST
        # renders the borrow-form nullptr compare there, unmirrored.
        src = (self._SRC
               + "def probe(w: Wrapper) -> None:\n"
               + "    print(w.find() is None)\n"
               + "probe(Wrapper())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is None
        assert not faces.get("isnone.property_subject")
        _assert_byte_identical(src)

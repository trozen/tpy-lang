"""Three FIELD-shaped twins of rows that were already carried for the bare
NAME shape: a container field at a container ref slot, a bytearray field as a
method receiver, and a field off an explicit `Ptr[record]` binding as a method
receiver.

Each is the same render one indirection down -- a member read binding by
reference, or a pointer deref the receiver node already spells -- so the twin
is the SAME cell, not a parallel one. The boundaries pinned beside them are
the shapes where the indirection changes the render: a slot that lifts rather
than binds, a getter CALL standing in for a member read, an Optional field
that still needs its unwrap.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _thir_ctx,
)


class TestContainerFieldArg:
    """A container FIELD read at a plain container ref slot binds the member
    by reference, bare on both paths -- at a free callee and at a record
    method alike."""

    FREE = ("from tpy import Int32\n"
            "def wake(ws: list[Int32]) -> None:\n"
            "    if len(ws) > 0:\n        ws.pop(0)\n"
            "class Q:\n    ws: list[Int32]\n"
            "    def __init__(self) -> None:\n        self.ws = []\n"
            "    def go(self) -> None:\n        wake(self.ws)\n"
            "def main() -> None:\n    q = Q()\n    q.go()\n"
            "    print(len(q.ws))\nmain()\n")

    METHOD = ("from tpy import Int32, Own\n"
              "class W:\n    n: Int32\n"
              "    def __init__(self) -> None:\n        self.n = 0\n"
              "    def take(self, row: list[str]) -> None:\n"
              "        self.n = len(row)\n"
              "class D:\n    w: W\n    names: list[str]\n"
              "    def __init__(self, w: Own[W]) -> None:\n"
              "        self.w = w\n        self.names = [\"a\"]\n"
              "    def go(self) -> None:\n        self.w.take(self.names)\n"
              "def main() -> None:\n    d = D(W())\n    d.go()\n"
              "    print(d.w.n)\nmain()\n")

    def test_free_call_routes_bare(self):
        _thir, faces = _lower_ctx_witnessed(self.FREE)
        assert faces.get("arg.container_field", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.FREE)
        assert "wake(this->ws);" in hpp + cpp

    def test_record_method_routes_bare(self):
        _thir, faces = _lower_ctx_witnessed(self.METHOD)
        assert faces.get("arg.container_field", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.METHOD)
        assert "this->w.take(this->names);" in hpp + cpp

    def test_optional_slot_stays_ast(self):
        # BOUNDARY: an `Optional[container]` slot LIFTS the argument (`&(xs)`)
        # instead of binding it -- the row is explicitly the non-Own,
        # non-Optional slot, so this must keep rejecting.
        src = ("from tpy import Int32\n"
               "def eat(ws: list[Int32] | None) -> Int32:\n"
               "    if ws is None:\n        return 0\n    return len(ws)\n"
               "class Q:\n    n: Int32\n    ws: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n        self.ws = []\n"
               "    def go(self) -> None:\n        self.n = eat(self.ws)\n"
               "def main() -> None:\n    q = Q()\n    q.go()\n"
               "    print(q.n)\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get("body:expr.call:call.arg_shape.optional") == 1, fell


class TestBytearrayFieldMethodReceiver:
    """A `bytearray` FIELD method receiver: every bytearray stub method is an
    @native rename over the bare receiver, so the member read composes exactly
    as a bytearray NAME receiver does."""

    SRC = ("from tpy import Int32\n"
           "class H:\n    n: Int32\n    buffer: bytearray\n"
           "    def __init__(self, k: Int32) -> None:\n        self.n = k\n"
           "        if k < 0:\n            raise ValueError(\"neg\")\n"
           "        self.buffer = bytearray()\n"
           "    def update(self, data: bytes) -> None:\n        k: Int32 = 0\n"
           "        while k < len(data):\n"
           "            self.buffer.append(data[k])\n            k += 1\n"
           "def main() -> None:\n    h = H(1)\n    h.update(b\"abc\")\n"
           "    print(len(h.buffer))\nmain()\n")

    def test_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("method.recv.bytearray_field", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "this->buffer.push_back(" in hpp + cpp

    def test_bytearray_property_receiver_stays_ast(self):
        # BOUNDARY: the arm reads a stored MEMBER. The property leg above it
        # admits view- and container-valued getters only, so a bytearray-
        # valued property -- whose receiver is the getter CALL, not a member
        # read -- must keep rejecting.
        src = ("from tpy import Int32\n"
               "class H:\n    _b: bytearray\n"
               "    def __init__(self, k: Int32) -> None:\n"
               "        if k < 0:\n            raise ValueError(\"neg\")\n"
               "        self._b = bytearray()\n"
               "    @property\n"
               "    def buf(self) -> bytearray:\n        return self._b\n"
               "    def go(self) -> None:\n        self.buf.append(65)\n"
               "def main() -> None:\n    h = H(1)\n    h.go()\n"
               "    print(len(h._b))\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get(
            "body:expr.method_call:method.recv.field_parent") == 1, fell


class TestPtrFieldMethodReceiver:
    """A field off an explicit `Ptr[record]` binding as a method receiver: the
    pointer arm renders the receiver itself -- `p->field` once sema proved the
    pointer, `::tpy::deref_check(p).field` before that -- and the outer method
    access is `.` either way."""

    SRC = ("from tpy import Int32, Ptr\n"
           "from tpy.unsafe import unsafe_take, unsafe_release\n"
           "class Inner:\n    v: Int32\n"
           "    def __init__(self) -> None:\n        self.v = 0\n"
           "    def bump(self, k: Int32) -> None:\n        self.v += k\n"
           "    def get(self) -> Int32:\n        return self.v\n"
           "class Cell:\n    st: Inner\n"
           "    def __init__(self) -> None:\n        self.st = Inner()\n"
           "def g(c: Ptr[Cell]) -> Int32:\n"
           "    c.st.bump(3)\n"
           "    return c.st.get()\n"
           "def main() -> None:\n    c = unsafe_take(Cell())\n"
           "    print(g(c))\n    unsafe_release(c)\nmain()\n")

    def test_routes_with_the_checked_then_proven_derefs(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("method.recv.record_field", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        out = hpp + cpp
        assert "::tpy::deref_check(c).st.bump(3);" in out
        assert "return c->st.get();" in out

    def test_generic_pointee_routes(self):
        # The pointee's type args ride the same F1 spelling slice every other
        # record receiver does, so a generic cell composes identically.
        src = ("from tpy import Int32, Ptr, Own\n"
               "from tpy.unsafe import unsafe_take, unsafe_release\n"
               "class Inner:\n    v: Int32\n"
               "    def __init__(self) -> None:\n        self.v = 0\n"
               "    def bump(self, k: Int32) -> None:\n        self.v += k\n"
               "class Cell[T]:\n    st: Inner\n    v: T\n"
               "    def __init__(self, v: Own[T]) -> None:\n"
               "        self.st = Inner()\n        self.v = v\n"
               "def main() -> None:\n"
               "    c = unsafe_take(Cell[Int32](5))\n    c.st.bump(3)\n"
               "    print(c.st.v)\n    unsafe_release(c)\nmain()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::deref_check(c).st.bump(3);" in hpp + cpp

    def test_optional_pointee_field_stays_ast(self):
        # BOUNDARY: the extra deref does not change the FIELD classification.
        # An Optional field still needs the outer unwrap no arm here renders,
        # so it keeps rejecting under the pointer exactly as off a plain
        # receiver.
        src = ("from tpy import Int32, Ptr\n"
               "from tpy.unsafe import unsafe_take, unsafe_release\n"
               "class Inner:\n    v: Int32\n"
               "    def __init__(self) -> None:\n        self.v = 1\n"
               "    def bump(self, k: Int32) -> None:\n        self.v += k\n"
               "class Cell:\n    st: Inner | None\n"
               "    def __init__(self) -> None:\n        self.st = Inner()\n"
               "def g(c: Ptr[Cell], s: Inner | None) -> Int32:\n"
               "    if s is not None:\n        return s.v\n"
               "    c.st.bump(3)\n    return 0\n"
               "def main() -> None:\n    c = unsafe_take(Cell())\n"
               "    print(g(c, None))\n    unsafe_release(c)\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get(
            "body:expr.method_call:method.recv.field_parent") == 1, fell

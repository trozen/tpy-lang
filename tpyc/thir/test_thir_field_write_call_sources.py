"""Field-write value rows whose source is a CALL or a BINOP, plus the
same-type default construction that serves the slots with no source rows of
their own.

The verdict for the str and bytes families is read off the LOWERED source's
form, not off the source node's shape: an owned rvalue assigns bare, a view
takes the family's convert. These units pin that the call/binop sources reach
that rule, and that the shapes whose render is decided elsewhere (a str INDEX
subscript, a view-only field with no owned sink, a construction carrying a
conversion) keep rejecting.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _thir_ctx,
)


class TestBytesFieldWriteBinop:
    """`self.buf = self.buf + chunk` -- the owned concat rvalue lands bare
    (`this->_buf = (::tpy::bytes_concat(this->_buf, chunk));`), no convert."""

    SRC = ("class B:\n    buf: bytes\n"
           "    def __init__(self) -> None:\n        self.buf = b\"\"\n"
           "    def add(self, c: bytes) -> None:\n"
           "        self.buf = self.buf + c\n"
           "def main() -> None:\n    b = B()\n    b.add(b\"xy\")\n"
           "    print(len(b.buf))\nmain()\n")

    def test_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("field_write.bytes_binop", 0) >= 1
        _assert_routes_byte_identical(self.SRC)

    def test_emit_is_the_bare_concat(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "this->buf = (::tpy::bytes_concat(this->buf, c));" in hpp + cpp


class TestBytesFieldWriteCall:
    """`self.buf = bytes(self.buf[n:])` -- the call's result is a view, so the
    family's `bytes_copy` STORAGE convert applies exactly as for a bare
    slice."""

    SRC = ("from tpy import Int32\nclass B:\n    buf: bytes\n"
           "    def __init__(self) -> None:\n        self.buf = b\"\"\n"
           "    def cut(self, n: Int32) -> None:\n"
           "        self.buf = bytes(self.buf[n:])\n"
           "def main() -> None:\n    b = B()\n    b.cut(1)\n"
           "    print(len(b.buf))\nmain()\n")

    def test_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("field_write.bytes_call", 0) >= 1
        _assert_routes_byte_identical(self.SRC)

    def test_owned_returning_call_lands_bare(self):
        src = ("def mk() -> bytes:\n    return b\"ab\"\n"
               "class B:\n    buf: bytes\n"
               "    def __init__(self) -> None:\n        self.buf = b\"\"\n"
               "    def go(self) -> None:\n        self.buf = mk()\n"
               "def main() -> None:\n    b = B()\n    b.go()\n"
               "    print(len(b.buf))\nmain()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "this->buf = mk();" in hpp + cpp

    def test_view_field_stays_ast(self):
        # BOUNDARY: a `BytesView` FIELD is the view side of the family -- no
        # owned sink, so no copy row claims it whatever the source is.
        src = ("from tpy import BytesView\n"
               "def mk() -> bytes:\n    return b\"ab\"\n"
               "class H:\n    v: BytesView\n"
               "    def __init__(self) -> None:\n        self.v = b\"\"\n"
               "    def go(self) -> None:\n        self.v = mk()\n"
               "def main() -> None:\n    h = H()\n    h.go()\n"
               "    print(len(h.v))\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get("body:stmt.assign:assign.field_write_shape") == 1, fell


class TestStrFieldWriteCall:
    """A str-typed CALL rvalue at an owned `str` field: an owned-str return
    assigns bare, a view-returning one arrives under the sema coerce that
    carries its own `std::string(...)` materialization."""

    OWNED = ("def mk() -> str:\n    return \"abc\"\n"
             "class T:\n    name: str\n"
             "    def __init__(self) -> None:\n        self.name = \"\"\n"
             "    def go(self) -> None:\n        self.name = mk()\n"
             "def main() -> None:\n    t = T()\n    t.go()\n"
             "    print(t.name)\nmain()\n")

    VIEW = ("class T:\n    name: str\n"
            "    def __init__(self) -> None:\n        self.name = \"\"\n"
            "    def go(self, s: str) -> None:\n"
            "        self.name = s.strip()\n"
            "def main() -> None:\n    t = T()\n    t.go(\" x \")\n"
            "    print(t.name)\nmain()\n")

    def test_owned_call_routes_bare(self):
        _thir, faces = _lower_ctx_witnessed(self.OWNED)
        assert faces.get("field_write.str_call", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.OWNED)
        assert "this->name = mk();" in hpp + cpp

    def test_view_returning_method_call_materializes(self):
        _thir, faces = _lower_ctx_witnessed(self.VIEW)
        assert faces.get("field_write.str_call", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.VIEW)
        assert "this->name = std::string(::tpy::str_strip(s));" in hpp + cpp

    def test_index_subscript_stays_ast(self):
        # BOUNDARY: an INDEX subscript is not a call -- it is a one-char str
        # with its own `str_getitem` render, and no row of this family
        # claims it.
        src = ("class T:\n    name: str\n"
               "    def __init__(self) -> None:\n        self.name = \"t\"\n"
               "    def pick(self, s: str) -> None:\n"
               "        self.name = s[0]\n"
               "def main() -> None:\n    t = T()\n    t.pick(\"ab\")\n"
               "    print(t.name)\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get("body:stmt.assign:assign.field_write_shape") == 1, fell


class TestDefaultCtorFieldWrite:
    """`recv.field = T()` at a field of that same T -- the construction
    prvalue assigned bare. The slots that reach it are the ones with no
    source rows of their own (`Array[T, N]`, `bytearray`); the families with
    rows claim their statements earlier."""

    # The ctor writes both fields from its TAIL (the leading guard keeps them
    # out of the member-init list), so the same row decides them there and in
    # `reset`; the free `main` is what makes the face visible to the lens.
    _HEAD = ("from tpy import Int32, Array\nclass R:\n    n: Int32\n"
             "    a: Array[Int32, 4]\n    ba: bytearray\n"
             "    def __init__(self, k: Int32) -> None:\n        self.n = k\n"
             "        if k < 0:\n            raise ValueError(\"neg\")\n"
             "        self.a = Array[Int32, 4]()\n"
             "        self.ba = bytearray()\n")

    ARRAY = (_HEAD + "    def reset(self) -> None:\n"
             "        self.a = Array[Int32, 4]()\n"
             "def main() -> None:\n    r = R(1)\n    r.reset()\n"
             "    print(r.a[0])\nmain()\n")

    BYTEARRAY = (_HEAD + "    def reset(self) -> None:\n"
                 "        self.ba = bytearray()\n"
                 "def main() -> None:\n    h = R(1)\n    h.reset()\n"
                 "    print(len(h.ba))\nmain()\n")

    def test_array_slot_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.ARRAY)
        assert faces.get("field_write.default_ctor", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.ARRAY)
        assert "this->a = std::array<int32_t, 4>();" in hpp + cpp

    def test_bytearray_slot_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.BYTEARRAY)
        assert faces.get("field_write.default_ctor", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.BYTEARRAY)
        assert "this->ba = std::vector<uint8_t>();" in hpp + cpp

    def test_different_type_construction_is_not_this_row(self):
        # BOUNDARY: the row is SAME-TYPE. A construction of a different type
        # carries a conversion whose render this row does not answer for, so
        # it must reach whichever family owns that conversion instead -- here
        # none does, and the statement keeps rejecting.
        src = ("from tpy import Int32\nclass H:\n    n: Int32\n"
               "    ba: bytearray\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n        self.ba = bytearray()\n"
               "    def go(self, x: bytes) -> None:\n"
               "        self.ba = bytearray(x)\n"
               "def main() -> None:\n    h = H()\n    h.go(b\"ab\")\n"
               "    print(len(h.ba))\nmain()\n")
        _assert_byte_identical(src)
        _ctx, fell = _thir_ctx(src)
        assert fell.get("body:stmt.assign:assign.field_write_shape") == 1, fell

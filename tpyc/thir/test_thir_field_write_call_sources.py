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
    _assert_rejects_at,
    _reject_tally,
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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")


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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")


class TestContainerFieldWritePrvalue:
    """A container field written from a value that materializes its own owned
    container -- `[e] * n` and a container-returning method-call RVALUE. Both
    assign bare: `gen_expr_deref` adds nothing over `gen_expr` for a prvalue
    and neither shape is a movable name, so there is no move verdict."""

    _HEAD = ("from tpy import Int32\nclass R:\n    pixels: list[Int32]\n"
             "    lines: list[str]\n"
             "    def __init__(self) -> None:\n"
             "        self.pixels = []\n        self.lines = []\n")

    REPEAT = (_HEAD + "    def fill(self, n: Int32, v: Int32) -> None:\n"
              "        self.pixels = [v] * n\n"
              "def main() -> None:\n    r = R()\n    r.fill(3, 7)\n"
              "    print(len(r.pixels), r.pixels[0])\nmain()\n")

    METHOD_CALL = (_HEAD + "    def slurp(self, data: str) -> None:\n"
                   "        self.lines = data.splitlines()\n"
                   "def main() -> None:\n    r = R()\n"
                   "    r.slurp(\"a\\nb\\nc\")\n"
                   "    print(len(r.lines), r.lines[1])\nmain()\n")

    def test_repeat_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.REPEAT)
        assert faces.get("field_write.container_repeat", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.REPEAT)
        # The repeat threads the FIELD type; an untargeted resolve would
        # demote the build to the Array flavor.
        assert ("this->pixels = ::tpy::from_range<std::vector<int32_t>>("
                "::tpy::repeat_range<int32_t>(n, {v}));") in hpp + cpp

    def test_method_call_routes(self):
        _thir, faces = _lower_ctx_witnessed(self.METHOD_CALL)
        assert faces.get("field_write.container_method_call", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.METHOD_CALL)
        assert "this->lines = ::tpy::str_splitlines(data);" in hpp + cpp

    SET_METHOD_CALL = (
        "from tpy import Int32\nclass R:\n    tags: set[Int32]\n"
        "    def __init__(self) -> None:\n        self.tags = set()\n"
        "    def merge(self, a: set[Int32], b: set[Int32]) -> None:\n"
        "        self.tags = a.union(b)\n"
        "def main() -> None:\n    r = R()\n"
        "    x: set[Int32] = {1, 2}\n    y: set[Int32] = {2, 3}\n"
        "    r.merge(x, y)\n    r.tags.add(9)\n"
        "    print(len(r.tags), len(x))\nmain()\n")

    def test_set_field_from_a_method_call_routes(self):
        # The row is keyed on the container FAMILY, not on list: a set field
        # takes the same bare assign off an owned-set rvalue.
        _thir, faces = _lower_ctx_witnessed(self.SET_METHOD_CALL)
        assert faces.get("field_write.container_method_call", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(self.SET_METHOD_CALL)
        assert "this->tags = ::tpy::set_union(a, b);" in hpp + cpp

    OPT_HEAD = ("from tpy import Int32\nfrom typing import Optional\n"
                "class R:\n    pixels: Optional[list[Int32]]\n"
                "    def __init__(self) -> None:\n        self.pixels = None\n")

    def test_optional_container_field_is_not_this_row(self):
        # BOUNDARY: a storage-form `Optional[container]` field is a
        # `std::optional<C>`, so the repeat build must be threaded with the
        # OPTIONAL (`from_range<std::optional<std::vector<T>>>`). This row
        # does not make that unwrap, so the slot must stay out.
        src = (self.OPT_HEAD
               + "    def fill(self, n: Int32, v: Int32) -> None:\n"
               + "        self.pixels = [v] * n\n"
               + "def main() -> None:\n    r = R()\n    r.fill(2, 7)\n"
               + "    print(1)\nmain()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_narrowed_optional_container_field_is_not_this_row(self):
        # ... and the slot is read from the DECLARED field type, not the
        # flow-narrowed one: after the None test the field READS as a bare
        # list while its C++ storage is still the optional, so classifying
        # off the read type would pick a render one unwrap too shallow.
        src = (self.OPT_HEAD
               + "    def fill(self, n: Int32, v: Int32) -> None:\n"
               + "        if self.pixels is None:\n            return\n"
               + "        self.pixels = [v] * n\n"
               + "def main() -> None:\n    r = R()\n    r.fill(2, 7)\n"
               + "    print(1)\nmain()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_narrowed_optional_container_field_literal_keeps_the_typed_brace(
            self):
        # The neighbouring literal row reads the same declared slot: the
        # optional's converting ctor has no type to deduce from `{1, 2}`, so
        # the container must be spelled. A narrowed read type would drop it.
        src = (self.OPT_HEAD
               + "    def reset(self) -> None:\n"
               + "        if self.pixels is None:\n            return\n"
               + "        self.pixels = [1, 2]\n"
               + "def main() -> None:\n    r = R()\n    r.reset()\n"
               + "    print(1)\nmain()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("field_write.opt_container_lit", 0) >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "this->pixels = std::vector<int32_t>{1, 2};" in hpp + cpp

    def test_borrow_returning_method_is_not_this_row(self):
        # BOUNDARY: the row is RVALUE-only. A borrow-returning method aliases
        # its receiver, and the by-value field copy off that alias is not what
        # the bare passthrough spells, so the statement keeps rejecting.
        src = ("from tpy import Int32\nclass Src:\n    xs: list[Int32]\n"
               "    def __init__(self) -> None:\n        self.xs = [1, 2]\n"
               "    def rows(self) -> list[Int32]:\n        return self.xs\n"
               "class R:\n    pixels: list[Int32]\n"
               "    def __init__(self) -> None:\n        self.pixels = []\n"
               "    def take(self, s: Src) -> None:\n"
               "        self.pixels = s.rows()\n"
               "def main() -> None:\n    r = R()\n    r.take(Src())\n"
               "    print(len(r.pixels))\nmain()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.arg_shape")

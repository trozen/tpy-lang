"""A VALUE-tuple FIELD read consumed whole: at a qualified callee's matching
tuple slot (`self._sock.connect(self._addr)`), at a @native callee's, and
under an f-string's `tuple_to_str` wrap. Borrow and storage forms coincide
for a value tuple, so the member read binds by reference with no lift --
while a POINTER-REPR tuple field, whose two forms genuinely differ, keeps
its own `tuple_to_pointer` lift and must stay out of both positions.

The row sits in the shared marker ladder, so the native family carries it
too -- and that family renders through a different arg loop (it sets
`inline_template`), so the qualified pins below cannot stand in for it."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _lower_ctx_witnessed, _thir_ctx,
)

_PRELUDE = (
    "from tpy import Int32, Ptr, take_ptr, nocopy\n"
    "class Rec:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "@nocopy\n"
    "class Sink:\n"
    "    total: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.total = 0\n"
    "    def feed(self, pair: tuple[str, Int32]) -> None:\n"
    "        name, n = pair\n"
    "        self.total += n\n"
    "    def feed_ref(self, pair: tuple[Rec, Int32]) -> None:\n"
    "        self.total += pair[1]\n"
    "    def feed_opt(self, pair: tuple[str, Int32] | None) -> None:\n"
    "        if pair is not None:\n"
    "            self.total += pair[1]\n"
    "def free_feed(pair: tuple[str, Int32]) -> Int32:\n"
    "    return pair[1]\n")

_ADDR_HOLDER = (
    "@nocopy\n"
    "class H:\n"
    "    _sink: Ptr[Sink]\n"
    "    _addr: tuple[str, Int32]\n"
    "    def __init__(self, sink: Ptr[Sink], addr: tuple[str, Int32])"
    " -> None:\n"
    "        self._sink = sink\n"
    "        self._addr = addr\n")

_ADDR_MAIN = ("def main() -> None:\n"
              "    s = Sink()\n"
              "    h = H(take_ptr(s), ('host', 80))\n"
              "    h.go()\n"
              "    print(s.total)\n"
              "main()\n")


class TestValueTupleFieldAtDerefSlot:

    def test_deref_call_binds_the_field_bare(self):
        src = _PRELUDE + _ADDR_HOLDER + (
            "    def go(self) -> None:\n"
            "        self._sink.feed(self._addr)\n") + _ADDR_MAIN
        hpp = _assert_routes_byte_identical(src)[0]
        assert "::tpy::deref_check(this->_sink).feed(this->_addr)" in hpp

    def test_the_arg_row_is_what_admits_it(self):
        # The row prechecks the field gates itself, so the read never
        # re-asks the result ladder -- the arg face is the whole witness
        # here (the f-string position below is where the result leg fires).
        src = _PRELUDE + _ADDR_HOLDER + (
            "    def go(self) -> None:\n"
            "        self._sink.feed(self._addr)\n") + _ADDR_MAIN
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.value_tuple_field", 0) >= 1
        assert not faces.get("field.value_tuple")

    def test_pointer_repr_tuple_field_stays_ast(self):
        # The two forms differ, so this read owes `tuple_to_pointer` -- the
        # key is the value-tuple predicate, never "a tuple field".
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _sink: Ptr[Sink]\n"
            "    _pair: tuple[Rec, Int32]\n"
            "    def __init__(self, sink: Ptr[Sink], pair: tuple[Rec, Int32])"
            " -> None:\n"
            "        self._sink = sink\n"
            "        self._pair = pair\n"
            "    def go(self) -> None:\n"
            "        self._sink.feed_ref(self._pair)\n"
            "def main() -> None:\n"
            "    s = Sink()\n"
            "    h = H(take_ptr(s), (Rec(2), 7))\n"
            "    h.go()\n"
            "    print(s.total)\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.qualcall.arg.other.expr.field_access")

    def test_optional_tuple_slot_stays_ast(self):
        # An `Optional[tuple]` slot is not a tuple, so the same field read
        # keeps whatever lift the optional slot owns.
        src = _PRELUDE + _ADDR_HOLDER + (
            "    def go(self) -> None:\n"
            "        self._sink.feed_opt(self._addr)\n") + _ADDR_MAIN
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.qualcall.arg.optional")

    def test_narrowed_optional_tuple_field_stays_ast(self):
        # DECLARED-type keyed: a narrowed `tuple | None` field types its
        # occurrence at the member but still owes the AST's unwrap.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _sink: Ptr[Sink]\n"
            "    _addr: tuple[str, Int32] | None\n"
            "    def __init__(self, sink: Ptr[Sink]) -> None:\n"
            "        self._sink = sink\n"
            "        self._addr = None\n"
            "    def go(self) -> None:\n"
            "        if self._addr is not None:\n"
            "            self._sink.feed(self._addr)\n"
            "def main() -> None:\n"
            "    s = Sink()\n"
            "    h = H(take_ptr(s))\n"
            "    h.go()\n"
            "    print(s.total)\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.qualcall.arg.other.expr.field_access")

    def test_plain_free_call_slot_stays_ast(self):
        # The cell belongs to the marker families only: the plain free-call
        # ladder carries no tuple-field row, and giving it one is its own
        # change.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _addr: tuple[str, Int32]\n"
            "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
            "        self._addr = addr\n"
            "    def go(self) -> Int32:\n"
            "        return free_feed(self._addr)\n"
            "def main() -> None:\n"
            "    print(H(('host', 80)).go())\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.call", "call.arg_shape.tuple")

    def test_user_record_method_slot_stays_ast(self):
        # ... and the user-record method ladder likewise.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _sink: Sink\n"
            "    _addr: tuple[str, Int32]\n"
            "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
            "        self._sink = Sink()\n"
            "        self._addr = addr\n"
            "    def go(self) -> None:\n"
            "        self._sink.feed(self._addr)\n"
            "def main() -> None:\n"
            "    h = H(('host', 80))\n"
            "    h.go()\n"
            "    print(h._sink.total)\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.arg_shape")


class TestValueTupleFieldInFString:

    def test_interpolated_field_renders_the_bare_member(self):
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _addr: tuple[str, Int32]\n"
            "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
            "        self._addr = addr\n"
            "    def describe(self) -> str:\n"
            "        return f'addr {self._addr}'\n"
            "def main() -> None:\n"
            "    print(H(('host', 80)).describe())\n"
            "main()\n")
        hpp = _assert_routes_byte_identical(src)[0]
        assert "::tpy::tuple_to_str(this->_addr)" in hpp
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("field.value_tuple", 0) >= 1

    def test_conversion_and_spec_forms_route_too(self):
        # `!r` and a format spec are the same wrap over the same bare
        # member read, so the admission is conversion-blind.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _addr: tuple[str, Int32]\n"
            "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
            "        self._addr = addr\n"
            "    def conv(self) -> str:\n"
            "        return f'a {self._addr!r}'\n"
            "    def spec(self) -> str:\n"
            "        return f'a {self._addr:>24}'\n"
            "def main() -> None:\n"
            "    h = H(('host', 80))\n"
            "    print(h.conv())\n"
            "    print(h.spec())\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_a_decl_sink_off_the_same_field_stays_ast(self):
        # The admission is POSITION-gated, not blind: binding the same read
        # into a local is a copy the decl sink owns, and it keeps rejecting.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _addr: tuple[str, Int32]\n"
            "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
            "        self._addr = addr\n"
            "    def describe(self) -> str:\n"
            "        p = self._addr\n"
            "        return f'a {p}'\n"
            "def main() -> None:\n"
            "    print(H(('host', 80)).describe())\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.var_decl", "field.result_type")

    def test_pointer_repr_tuple_field_stays_ast(self):
        # The interpolation half of the boundary: this read owes
        # `tuple_to_pointer` under the same `tuple_to_str` wrap.
        src = _PRELUDE + (
            "@nocopy\n"
            "class H:\n"
            "    _pair: tuple[Rec, Int32]\n"
            "    def __init__(self, pair: tuple[Rec, Int32]) -> None:\n"
            "        self._pair = pair\n"
            "    def describe(self) -> str:\n"
            "        return f'pair {self._pair}'\n"
            "def main() -> None:\n"
            "    print(H((Rec(2), 7)).describe())\n"
            "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.fstring", "field.result_type")


_NATIVE_HELPER = (
    '# tpy: include("<x/feed.hpp>")\n'
    "from tpy.extern import native\n"
    "from tpy import Int32\n"
    "class Node:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    '@native("ns::feed")\n'
    "def feed(pair: tuple[str, Int32]) -> Int32: ...\n"
    '@native("ns::feed_ref")\n'
    "def feed_ref(pair: tuple[Node, Int32]) -> Int32: ...\n")


def _write_native_helper(tmp_path):
    (tmp_path / "vtf_native.py").write_text(_NATIVE_HELPER)


class TestValueTupleFieldAtNativeSlot:
    """The same row under the @native marker family. Its nativeness comes
    from the CALLEE, not from a module directive, so an ordinary module
    holding both the stubs and the record they name is enough."""

    _SRC = (
        "import vtf_native\n"
        "from tpy import Int32, nocopy\n"
        "@nocopy\n"
        "class H:\n"
        "    _addr: tuple[str, Int32]\n"
        "    def __init__(self, addr: tuple[str, Int32]) -> None:\n"
        "        self._addr = addr\n"
        "def go(h: H) -> Int32:\n"
        "    return vtf_native.feed(h._addr)\n"
        "def main() -> None:\n"
        "    print(go(H(('host', 80))))\n"
        "main()\n")

    def test_native_call_binds_the_field_bare(self, tmp_path):
        _write_native_helper(tmp_path)
        hpp, cpp = _assert_routes_byte_identical(
            self._SRC, extra_lib_dirs=[tmp_path])
        # The native spelling is what distinguishes the family: a qualified
        # callee would render `::tpyapp::vtf_native::feed` instead.
        assert "::ns::feed(h._addr)" in hpp + cpp

    def test_the_native_family_witnesses_the_arg_row(self, tmp_path):
        _write_native_helper(tmp_path)
        _, faces = _lower_ctx_witnessed(self._SRC, extra_lib_dirs=[tmp_path])
        assert faces.get("arg.value_tuple_field", 0) >= 1

    def test_pointer_repr_tuple_field_stays_ast(self, tmp_path):
        # The native half of the boundary: the two forms differ here, so the
        # read owes `tuple_to_pointer` and must keep rejecting.
        _write_native_helper(tmp_path)
        src = (
            "import vtf_native\n"
            "from vtf_native import Node\n"
            "from tpy import Int32, nocopy\n"
            "@nocopy\n"
            "class H:\n"
            "    _pair: tuple[Node, Int32]\n"
            "    def __init__(self, pair: tuple[Node, Int32]) -> None:\n"
            "        self._pair = pair\n"
            "def go(h: H) -> Int32:\n"
            "    return vtf_native.feed_ref(h._pair)\n"
            "def main() -> None:\n"
            "    print(go(H((Node(2), 7))))\n"
            "main()\n")
        _, fallback = _thir_ctx(src, extra_lib_dirs=[tmp_path])
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.qualcall.arg.other.expr.field_access")

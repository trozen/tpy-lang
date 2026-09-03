"""Call-lane render arms: the container-literal typed temp at a
nullable-protocol ctor slot, the bytes-literal temp at a
beyond-the-slice pointer-variant union slot, and the bytes half of the
view->owned chokepoint at Own[bytes] container-element args (plus the
chained bytes element read it unmasked)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestProtocolUnionLiteralTemp:
    def test_list_literal_hoists_typed_temp(self):
        src = ("from collections import Counter\n"
               "def main() -> None:\n"
               "    more = Counter([\"a\", \"x\", \"x\"])\n"
               "    print(more[\"x\"])\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("argtemp.protocol_union_literal", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "std::array<std::string, 3> __tmp_1 = {\"a\", \"x\", \"x\"};" \
            in cpp[1]
        assert "(&(__tmp_1))" in cpp[1]

    def test_container_name_keeps_addr_face(self):
        # The sibling NAME face is untouched: `&(words)` via the 'addr' row,
        # never the literal temp.
        src = ("from collections import Counter\n"
               "def main() -> None:\n"
               "    words: list[str] = [\"a\", \"b\", \"a\"]\n"
               "    c = Counter(words)\n"
               "    print(c[\"a\"])\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("ctor.protocol_union_arg", 0) == 1
        assert faces.get("argtemp.protocol_union_literal", 0) == 0
        cpp = _assert_routes_byte_identical(src)
        assert "(&(words))" in cpp[1]


class TestUnionBytesLiteralTemp:
    _HDR = (
        "from tpy import Int32\n"
        "class Sink:\n"
        "    n: Int32\n"
        "    def put(self, body: bytes | dict[str, str] | None) -> None:\n"
        "        self.n = 1\n"
    )

    def test_bytes_literal_hoists_owned_temp(self):
        src = (self._HDR +
               "def main() -> None:\n"
               "    s = Sink()\n"
               "    s.put(b\"payload\")\n"
               "    print(s.n)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("unionlift.bytes_literal_temp", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("std::vector<uint8_t> __tmp_1 = "
                "::tpy::bytes_literal_owned(\"payload\", 7);") in cpp[1]
        assert "{&__tmp_1}" in cpp[1]

    def test_bytes_name_stays_out(self):
        # A bytes NAME at the same slot is unwitnessed (the AST's copy/lift
        # question is open) -- the face must not fire; the body falls back.
        src = (self._HDR +
               "def send(s: Sink, b: bytes) -> None:\n"
               "    s.put(b)\n"
               "def main() -> None:\n"
               "    send(Sink(), b\"xy\")\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.arg_shape")


class TestBytesOwnedSlotArg:
    def test_param_and_slice_sources_copy(self):
        src = ("def sinks(b: bytes) -> None:\n"
               "    app: list[bytes] = []\n"
               "    app.append(b)\n"
               "    app.append(b[1:3])\n"
               "    print(len(app), len(app[0]))\n"
               "def main() -> None:\n"
               "    sinks(b\"hello\")\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "sinks") is not None
        assert faces.get("arg.own_bytes_slot", 0) == 2
        cpp = _assert_routes_byte_identical(src)
        assert "app.push_back(::tpy::bytes_copy(b));" in cpp[1]
        assert ("app.push_back(::tpy::bytes_copy(::tpy::bytes_slice(b, "
                "::tpy::BasicSlice{1, 3})));") in cpp[1]

    def test_narrowed_optional_deref_copies(self):
        src = ("def sink(b: bytes | None) -> None:\n"
               "    out: list[bytes] = []\n"
               "    if b is not None:\n"
               "        out.append(b)\n"
               "    print(len(out))\n"
               "def main() -> None:\n"
               "    sink(b\"world\")\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "sink") is not None
        assert faces.get("arg.own_bytes_slot", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "out.push_back(::tpy::bytes_copy((*b)));" in cpp[1]

    def test_owned_local_stays_ast(self):
        # An owned bytes LOCAL is STORAGE -- the AST rides the copy+move
        # temp cascade, which this arm does not render; the face must not
        # fire and the body falls back whole.
        src = ("def sink() -> None:\n"
               "    owned = b\"abc\" + b\"d\"\n"
               "    app: list[bytes] = []\n"
               "    app.append(owned)\n"
               "    print(len(app))\n"
               "def main() -> None:\n"
               "    sink()\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.own_bytes_slot", 0) == 0
        _assert_byte_identical(src)


class TestChainedBytesElemRead:
    def test_bytes_elem_of_container_elem_routes(self):
        src = ("def peek(app: list[bytes]) -> None:\n"
               "    print(app[0][0])\n"
               "def main() -> None:\n"
               "    xs: list[bytes] = [b\"hi\"]\n"
               "    peek(xs)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "peek") is not None
        cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::bytes_getitem(::tpy::__getitem__(app, 0), 0)"
                in cpp[1])

    def test_outer_slice_index_takes_the_slice_arm(self):
        # The chained face admits scalar BYTE reads only, so a SLICE of the
        # bytes element is not its shape -- it is the slice arm's, over the
        # same element read as receiver.
        src = ("def peek(app: list[bytes]) -> None:\n"
               "    print(len(app[0][1:]))\n"
               "def main() -> None:\n"
               "    xs: list[bytes] = [b\"hi\"]\n"
               "    peek(xs)\n"
               "main()\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert ("::tpy::bytes_slice(::tpy::__getitem__(app, 0), "
                "::tpy::BasicSlice{1, std::nullopt})") in cpp

    def test_two_level_nested_receiver_stays_out(self):
        # One nested step only: `m[0][0][0]` (a bytes element two container
        # levels down) exceeds the one-level receiver resolver -- the body
        # falls back whole.
        src = ("def peek(m: list[list[bytes]]) -> None:\n"
               "    print(m[0][0][0])\n"
               "def main() -> None:\n"
               "    xs: list[list[bytes]] = [[b\"hi\"]]\n"
               "    peek(xs)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:subscript.recv.subscript")

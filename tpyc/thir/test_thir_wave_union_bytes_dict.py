"""Pins for the bytes-member ptr-variant union slice + the dict-literal
union ArgTemp row: `_ptr_union_view_member_ok` gains the owned-bytes
member (uniform `std::vector<uint8_t>` / `...*` spelling), so
`bytes | dict[str, str] | None` slots take the member machinery -- the
None monostate lift, the member-name address lift, the bytes-literal
temp, and the NEW dict-literal typed temp (`unionlift.dict_literal_temp`).
Boundaries: a LIST literal at a list-membered union stays unwitnessed and
keeps rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = "from tpy import Int32\n"

_SEND = _HDR + (
    "def send(data: bytes | dict[str, str] | None) -> None:\n"
    "    pass\n"
)


class TestBytesUnionArgRows:
    def test_none_and_literals_route(self):
        src = _SEND + (
            "def main() -> None:\n"
            "    send(None)\n"
            "    send(b'raw')\n"
            "    send({'user': 'ann'})\n"
        )
        _assert_routes_byte_identical(src)

    def test_member_name_lift_routes(self):
        src = _SEND + (
            "def main() -> None:\n"
            "    d = {'a': '1'}\n"
            "    send(d)\n"
        )
        _assert_routes_byte_identical(src)


_RECV = _HDR + (
    "class S:\n"
    "    n: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.n = 0\n"
    "    def go(self, data: bytes | dict[str, str] | None = None) -> Int32:\n"
    "        return self.n\n"
)


class TestRecordMethodUnionNoneArg:
    """The record-method twin of the free ladder's `unionlift.none` row: the
    method arg loop shares `_lower_call_arg`'s lift, so a `None` at a
    pointer-variant union method slot renders the fully spelled
    `pv{std::monostate{}}` -- NOT `std::nullopt`."""

    def test_none_at_ptr_union_method_slot_routes(self):
        src = _RECV + (
            "def main() -> None:\n"
            "    s = S()\n"
            "    print(s.go(None))\n"
        )
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("unionlift.none", 0) > 0
        hpp, cpp = _assert_routes_byte_identical(src)
        # The callee's deep-const verdict makes this slot's pointees const;
        # what the row pins is the fully spelled variant + monostate.
        assert ("s.go(std::variant<std::monostate, const std::vector<uint8_t>*"
                ", const ::tpy::ordered_map<std::string, std::string>*>"
                "{std::monostate{}})" in hpp + cpp)
        assert "go(std::nullopt)" not in hpp + cpp

    def test_member_name_at_ptr_union_method_slot_still_defers(self):
        # Only the None leg has a method-position witness; the member-NAME
        # leg of the free ladder's row stays out.
        src = _RECV + (
            "def main() -> None:\n"
            "    s = S()\n"
            "    d = {'a': 'b'}\n"
            "    print(s.go(d))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:method.arg_shape")


class TestBytesUnionBoundaries:
    def test_list_literal_at_union_slot_still_defers(self):
        # The unwitnessed literal sibling: only the DICT literal has an
        # oracle-verified temp render; a list literal at a list-membered
        # union must keep rejecting.
        src = _HDR + (
            "def send(data: bytes | list[str] | None) -> None:\n"
            "    pass\n"
            "def main() -> None:\n"
            "    send(['a', 'b'])\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.union")

    def test_readonly_union_slot_dict_literal_still_defers(self):
        # A readonly slot's spelling is unwitnessed for the literal temp --
        # the row is const-blind only for the member machinery, not the
        # literal hoist.
        src = _HDR + (
            "from tpy import readonly\n"
            "def send(data: readonly[bytes | dict[str, str] | None])"
            " -> None:\n"
            "    pass\n"
            "def main() -> None:\n"
            "    send({'user': 'ann'})\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.union")

    def test_bytes_union_isinstance_body_routes(self):
        # The narrowing bodies over the same subject now ride the widened
        # member class too -- every spelling here is `render_type(m)`.
        src = _SEND + (
            "def consume(data: bytes | dict[str, str] | None) -> Int32:\n"
            "    if isinstance(data, bytes):\n"
            "        return len(data)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(consume(b'abc'))\n"
        )
        _assert_routes_byte_identical(src)

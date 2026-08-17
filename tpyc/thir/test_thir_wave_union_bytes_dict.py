"""Pins for the bytes-member ptr-variant union slice + the dict-literal
union ArgTemp row: `_ptr_union_view_member_ok` gains the owned-bytes
member (uniform `std::vector<uint8_t>` / `...*` spelling), so
`bytes | dict[str, str] | None` slots take the member machinery -- the
None monostate lift, the member-name address lift, the bytes-literal
temp, and the NEW dict-literal typed temp (`unionlift.dict_literal_temp`).
Boundaries: a LIST literal at a list-membered union stays unwitnessed and
keeps rejecting; the bytes-union isinstance narrowing bodies keep their
own gates (identity via fallback)."""

from __future__ import annotations

from .testutil import (
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
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert not faces.get("unionlift.dict_literal_temp")
        _assert_byte_identical(src)

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
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert not faces.get("unionlift.dict_literal_temp")
        _assert_byte_identical(src)

    def test_bytes_union_isinstance_body_keeps_own_gate(self):
        # The widened member class must not smuggle the narrowing bodies
        # through -- they keep their own condition gates; identity holds
        # via fallback.
        src = _SEND + (
            "def consume(data: bytes | dict[str, str] | None) -> Int32:\n"
            "    if isinstance(data, bytes):\n"
            "        return len(data)\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(consume(b'abc'))\n"
        )
        _assert_byte_identical(src)

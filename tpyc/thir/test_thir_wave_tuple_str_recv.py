"""A STR tuple element as a method-call receiver (`kv[0].lower()`).

`std::get<N>` yields the element and feeds the view family's receiver slot
positionally (`::tpy::str_lower(std::get<0>(kv))`) -- the str sibling of the
tuple-element record receiver, asking the same element question the
container-element str row asks. A bytes element has no witness and keeps
rejecting.
"""

from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)
from ..codegen_cpp.context import CodeGenOptions


class TestStrTupleElemReceiver:
    SRC = (
        "from tpy import Own\n"
        "def drop(headers: dict[str, str]) -> Own[list[str]]:\n"
        "    out: list[str] = []\n"
        "    for kv in headers.items():\n"
        "        if kv[0].lower() == \"a\":\n"
        "            out.append(kv[1])\n"
        "    return out\n"
        "def main() -> None:\n"
        "    d: dict[str, str] = {\"A\": \"x\"}\n"
        "    print(drop(d))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(self.SRC))
        assert "::tpy::str_lower(std::get<0>(kv))" in out

    def test_witnesses_the_tuple_str_elem_face(self):
        _thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert witnesses.get("method.recv.tuple_str_elem", 0) >= 1, witnesses


class TestBytesTupleElemReceiverKeepsRejecting:
    SRC = (
        "from tpy import Own\n"
        "def take(pairs: list[tuple[str, bytes]]) -> Own[list[str]]:\n"
        "    out: list[str] = []\n"
        "    for kv in pairs:\n"
        "        out.append(kv[1].decode())\n"
        "    return out\n"
        "def main() -> None:\n"
        "    ps: list[tuple[str, bytes]] = [(\"a\", b\"x\")]\n"
        "    print(take(ps))\n"
        "main()\n"
    )

    def test_rejects_at_the_receiver_shape(self):
        _assert_rejects_at(_reject_tally(self.SRC), 'body:expr.method_call', shape='method.recv.subscript')

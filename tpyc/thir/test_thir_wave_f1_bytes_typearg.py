"""The `bytes` type-arg of a generic user record: both paths spell the OWNED
storage form (`Pair<std::vector<uint8_t>>`), so `_f1_record_type_arg_ok`
admits it exactly as it admits the str family -- the view/owned param split
never applies inside a type-arg list.

The two siblings that share the shape stay fenced: `BytesView` spells the
view form and no admitted shape witnesses it, and `bytearray` spells the same
C++ as `bytes` but is a reference type whose borrow form is a separate
question.
"""

from .testutil import (
    _assert_byte_identical,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _thir_ctx,
)

_PAIR = (
    "class Pair[T]:\n"
    "    first: T\n"
    "    def __init__(self, first: T):\n"
    "        self.first = first\n"
)


class TestBytesTypeArg:
    _SRC = (
        "from tpy import Int32\n"
        + _PAIR +
        "def probe(p: Pair[bytes]) -> Int32:\n"
        "    return Int32(len(p.first))\n"
        "def main() -> None:\n"
        "    p = Pair(b\"ab\")\n"
        "    print(probe(p))\n"
        "main()\n"
    )

    def test_bytes_arg_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "Pair<std::vector<uint8_t>>" in hpp + cpp

    def test_bytes_element_arg_routes_byte_identical(self):
        # The container arm recurses its elements through the same slice, so
        # a `list[bytes]` arg admits only once `bytes` itself does.
        src = (
            "from tpy import Int32\n"
            + _PAIR +
            "def probe(p: Pair[list[bytes]]) -> Int32:\n"
            "    return Int32(len(p.first))\n"
            "def main() -> None:\n"
            "    p = Pair([b\"ab\"])\n"
            "    print(probe(p))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Pair<std::vector<std::vector<uint8_t>>>" in hpp + cpp


class TestBytesTypeArgBoundaries:
    def test_bytesview_arg_stays_ast(self):
        # BOUNDARY: the view spelling (`Pair<std::span<const uint8_t>>`) is
        # a different render with no admitted shape behind it, so the slot
        # keeps rejecting -- the arm is keyed on the owned spelling, not on
        # "the bytes family".
        # The `Pair(v)` bind is the fixture's ONLY declaration, so the
        # reject cannot come from an unrelated slot.
        src = (
            "from tpy import Int32, BytesView\n"
            + _PAIR +
            "def probe(p: Pair[BytesView]) -> Int32:\n"
            "    return Int32(len(p.first))\n"
            "def make(v: BytesView) -> Int32:\n"
            "    p = Pair(v)\n"
            "    return probe(p)\n"
            "def main() -> None:\n"
            "    print(make(b\"ab\"))\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.var_decl", "decl.slot_type")

    def test_bytearray_arg_stays_ast(self):
        # BOUNDARY: `bytearray` spells the SAME `std::vector<uint8_t>` the
        # admitted `bytes` arg does, so a pin keyed on the spelling alone
        # would let it through -- it is a reference type, whose borrow form
        # inside a type-arg list is unanswered.
        src = (
            "from tpy import Int32\n"
            + _PAIR +
            "def probe(p: Pair[bytearray]) -> Int32:\n"
            "    return Int32(len(p.first))\n"
            "def main() -> None:\n"
            "    p = Pair(bytearray(b\"ab\"))\n"
            "    print(probe(p))\n"
            "main()\n"
        )
        _ctx, fallback = _thir_ctx(src)
        # `probe`'s field read is the reject the type-arg gate DECIDES: the
        # receiver is not an F1 record while its arg stays out of the slice.
        _assert_rejects_at(fallback, "body:expr.call",
                           "call.native_arg.record_nonf1")

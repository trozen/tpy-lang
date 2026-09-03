"""Pins for the S1 view->owned str convert at an `Own[str]` slot reached
through the QUALCALL and RECORD-CTOR arg ladders.

A `str` param is BORROW-form (`std::string_view` in the signature), so an
`Own[str]` slot has to materialize the owned copy inline: `std::string(x)`.
The free-call and method ladders already carried that row; the marker
(qualified / static-method) family and the record-ctor family did not, so
`Rc.new(s)` and `Box(s)` on a str param rejected while `take(s)` did not.

Their committed oracles are `tests/cases/protocols/overload_nested_generic_call`
(`Rc<std::string>::new_<std::string>(std::string(inner))`) and
`.../overload_nested_generic_ctor` (`Box<std::string>(std::string(x))`).
"""

from __future__ import annotations

from .testutil import (_assert_rejects_at, _assert_routes_byte_identical,
                       _thir_ctx)

_SRC = (
    "from tpy import Own\n"
    "from tplib import Box, Rc\n"
    "def wrap_s(s: str) -> Own[Rc[str]]:\n"
    "    return Rc.new(s)\n"
    "def box_s(s: str) -> Own[Box[str]]:\n"
    "    return Box(s)\n"
    "def main() -> None:\n"
    "    print(wrap_s('ab').get(), box_s('cd').get())\n"
    "main()\n"
)


class TestOwnStrSlotAtMarkerAndCtorLadders:
    def test_str_param_at_qualcall_own_slot_routes(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC, comments=False)
        assert ("Rc<std::string>::new_<std::string>(std::string(s))"
                in hpp + cpp)

    def test_str_param_at_record_ctor_own_slot_routes(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC, comments=False)
        assert ("::tpystd::tplib::box::Box<std::string>(std::string(s))"
                in hpp + cpp)

    def test_bytes_param_at_qualcall_own_slot_keeps_rejecting(self):
        # BOUNDARY: the S6 bytes twin (`::tpy::bytes_copy(b)`) is a separate
        # row and no committed render witnesses it at this ladder.
        src = (
            "from tpy import Own\n"
            "from tplib import Rc\n"
            "def wrap_b(b: bytes) -> Own[Rc[bytes]]:\n"
            "    return Rc.new(b)\n"
            "def main() -> None:\n"
            "    print(len(wrap_b(b'xy').get()))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:expr.method_call",
                           "method.qualcall.arg.own")

    def test_bytes_param_at_record_ctor_own_slot_keeps_rejecting(self):
        # BOUNDARY: the ctor-ladder half of the same bytes twin.
        src = (
            "from tpy import Own\n"
            "from tplib import Box\n"
            "def wrap_b(b: bytes) -> Own[Box[bytes]]:\n"
            "    return Box(b)\n"
            "def main() -> None:\n"
            "    print(len(wrap_b(b'xy').get()))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:expr.call", "call.ctor_arg.own_bytes")

    def test_str_literal_stays_bare_at_both_ladders(self):
        # The row's literal face: a `const char[N]` binds the owned slot
        # through `std::string`'s own converting ctor, so no convert is
        # spelled. The ctor ladder already had a dedicated literal row
        # ahead of this one and must keep rendering the same way.
        src = (
            "from tpy import Own\n"
            "from tplib import Box, Rc\n"
            "def lit_box() -> Own[Box[str]]:\n"
            "    return Box('ab')\n"
            "def lit_rc() -> Own[Rc[str]]:\n"
            "    return Rc.new('cd')\n"
            "def main() -> None:\n"
            "    print(lit_box().get(), lit_rc().get())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert '::tpystd::tplib::box::Box<std::string>("ab")' in out
        assert 'Rc<std::string>::new_<std::string>("cd")' in out
        assert "std::string(" not in out

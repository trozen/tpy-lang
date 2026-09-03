"""The inline-template Own-slot lvalue skip: a simple non-str NAME into an
`Own[..]` slot of a native/template callee renders BARE (gen_call_arg's
inline_template Own arm -- the template binds the lvalue natively, no
copy+move temp). The move half (`_own_move_source_slice`) still outranks
the skip: a movable record name moves; str payloads keep their conversion
temp (excluded from the skip and covered by the argtemp.own_str pins)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_HDR = (
    "from tpy import Int32, Own, Ptr\n"
    "from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init,"
    " unsafe_drop\n"
)


class TestInlineTemplateOwnLvalue:
    def test_scalar_name_renders_bare(self):
        # `unsafe_init(p, value)` at `Own[T]`: the scalar name goes bare
        # into the placement-new expansion -- no `auto __tmp_N` copy.
        src = (_HDR +
               "def main() -> None:\n"
               "    p: Ptr[Int32] = unsafe_alloc()\n"
               "    value = 41\n"
               "    unsafe_init(p, value)\n"
               "    unsafe_drop(p)\n"
               "    unsafe_free(p)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("call.native_own_scalar_lvalue", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "::new(static_cast<void*>(p)) int32_t(value);" in cpp[1]
        assert "__tmp" not in cpp[1]

    def test_movable_record_name_still_moves(self):
        # The move half outranks the bare skip: a record name at its last
        # use moves into the placement-new expansion.
        src = (_HDR +
               "class Rec:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def main() -> None:\n"
               "    p: Ptr[Rec] = unsafe_alloc()\n"
               "    r = Rec(5)\n"
               "    unsafe_init(p, r)\n"
               "    unsafe_drop(p)\n"
               "    unsafe_free(p)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "::new(static_cast<void*>(p)) Rec(std::move(r));" in cpp[1]

    def test_field_arg_keeps_copy_temp(self):
        # A FIELD read (non-NAME) into the Own slot keeps the copy+move
        # temp -- the bare skip admits simple names only.
        src = (_HDR +
               "class W:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 3\n"
               "def main() -> None:\n"
               "    p: Ptr[Int32] = unsafe_alloc()\n"
               "    w = W()\n"
               "    unsafe_init(p, w.n)\n"
               "    unsafe_drop(p)\n"
               "    unsafe_free(p)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.native_arg.own")

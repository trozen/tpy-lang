"""A plain `@native` record's ctor rvalue at an `Own[T]` call slot.

Its AST emit IS the record branch (`::tpy::MovableConditionVariable()`), and
an rvalue binds the `T&&` slot bare -- no temp, so the face is
position-blind. Only the decl/value slot and the ctor member-init list
carried it. Corpus witness: tpy.sync's `Condvar.__init__`
(`unsafe_take(_RawCondvar())`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


_HEAD = ("from tpy import Int32, Ptr, Own, nocopy\n"
         "from tpy.extern import native\n"
         "from tpy.unsafe import unsafe_take\n")

_RAW = ("@native('tpy::MovableConditionVariable')\n"
        "@nocopy\n"
        "class RawCv:\n"
        "    def __init__(self) -> None: ...\n")


def _emit(src: str):
    """Fallback tally + face witnesses from a FULL emit: a ctor body is
    lowered by the record driver, not by `lower_module`, so the module-level
    witness lens cannot see it."""
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback), dict(compiler._thir_face_witnesses)


def _fallback(src: str):
    return _emit(src)[0]


class TestNativeCtorAtOwnSlot:
    NATIVE_SLOT = (_HEAD + _RAW
                   + "class H:\n"
                   + "    q: Ptr[RawCv]\n"
                   + "    def __init__(self) -> None:\n"
                   + "        self.q = unsafe_take(RawCv())\n"
                   + "def main() -> None:\n"
                   + "    h = H()\n"
                   + "    print(1)\n"
                   + "main()\n")

    NESTED_CTOR = (_HEAD + _RAW
                   + "@nocopy\n"
                   + "class Wrap:\n"
                   + "    p: Ptr[RawCv]\n"
                   + "    def __init__(self, p: Ptr[RawCv]) -> None:\n"
                   + "        self.p = p\n"
                   + "def build() -> Own[Wrap]:\n"
                   + "    return Wrap(unsafe_take(RawCv()))\n"
                   + "def main() -> None:\n"
                   + "    w = build()\n"
                   + "    print(1)\n"
                   + "main()\n")

    def test_native_callee_own_slot_routes_witnessed(self):
        _, witnesses = _emit(self.NATIVE_SLOT)
        assert witnesses.get("ctor.native_plain", 0) >= 1
        assert witnesses.get("own.record_rvalue", 0) >= 1
        _assert_routes_byte_identical(self.NATIVE_SLOT)

    def test_nested_user_ctor_arg_routes(self):
        _, witnesses = _emit(self.NESTED_CTOR)
        assert witnesses.get("ctor.native_plain", 0) >= 1
        _assert_routes_byte_identical(self.NESTED_CTOR)

    def test_native_ctor_with_args_routes(self):
        src = (_HEAD
               + "@native('tpy::BasicSlice')\n"
               + "class RawSlice:\n"
               + "    def __init__(self, a: Int32, b: Int32) -> None: ...\n"
               + "class H:\n"
               + "    q: Ptr[RawSlice]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.q = unsafe_take(RawSlice(1, 2))\n"
               + "def main() -> None:\n"
               + "    h = H()\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)


class TestNativeCtorAtOwnSlotBoundary:
    def test_native_c_aggregate_init_keeps_rejecting(self):
        # A C-binding record constructs through the `{args}` aggregate --
        # a different render the record branch does not spell.
        src = (_HEAD
               + "@native('tpy::MovableConditionVariable', binding='C')\n"
               + "class RawC:\n"
               + "    def __init__(self) -> None: ...\n"
               + "class H:\n"
               + "    q: Ptr[RawC]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.q = unsafe_take(RawC())\n"
               + "def main() -> None:\n"
               + "    h = H()\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "ctor:expr.call",
                           "call.native_arg.own")
        _assert_byte_identical(src)

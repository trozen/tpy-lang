"""User-Deref chain over a NARROWED value-repr `Own[wrapper] | None` NAME.

The binding is a by-value `std::optional<Box<T>>`, so the narrowed read
derefs in place (`(*b)`) and the chain composes off that lvalue --
`(*b).__deref__().m()`. Corpus witness: urllib.request's `_urlopen`
(`injected: Own[Box[_Connection]] | None`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_PET = ("from tpy import Int32, Own\n"
        "from tplib import Box\n"
        "class Pet:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def speak(self) -> Int32:\n"
        "        return self.n\n"
        "    def bump(self, d: Int32) -> None:\n"
        "        self.n = self.n + d\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestValueOptDerefReceiver:
    METHOD = (_PET
              + "def use(b: Own[Box[Pet]] | None) -> Int32:\n"
              + "    if b is not None:\n"
              + "        b.bump(4)\n"
              + "        return b.speak()\n"
              + "    return 0\n"
              + "def main() -> None:\n"
              + "    print(use(Box(Pet(3))))\n"
              + "main()\n")

    FIELD = (_PET
             + "def use(b: Own[Box[Pet]] | None) -> Int32:\n"
             + "    if b is not None:\n"
             + "        b.n = 7\n"
             + "        return b.n\n"
             + "    return 0\n"
             + "def main() -> None:\n"
             + "    print(use(Box(Pet(3))))\n"
             + "main()\n")

    def test_method_call_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.METHOD)
        assert witnesses.get("recv.deref_value_opt_name", 0) >= 1
        assert witnesses.get("method.user_deref_chain", 0) >= 1
        _assert_routes_byte_identical(self.METHOD)

    def test_field_read_and_write_route_witnessed(self):
        # The same receiver resolution feeds the field twin, so widening it
        # for the method arm has to keep the field chain byte-identical.
        _, witnesses = _lower_ctx_witnessed(self.FIELD)
        assert witnesses.get("recv.deref_value_opt_name", 0) >= 1
        assert witnesses.get("field.user_deref_chain", 0) >= 1
        _assert_routes_byte_identical(self.FIELD)

    def test_nested_wrapper_depth_routes(self):
        src = (_PET
               + "def use(b: Own[Box[Box[Pet]]] | None) -> Int32:\n"
               + "    if b is not None:\n"
               + "        return b.speak()\n"
               + "    return 0\n"
               + "def main() -> None:\n"
               + "    print(use(Box(Box(Pet(3)))))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_none_first_narrow_routes(self):
        src = (_PET
               + "def use(b: Own[Box[Pet]] | None) -> Int32:\n"
               + "    if b is None:\n"
               + "        return -1\n"
               + "    return b.speak()\n"
               + "def main() -> None:\n"
               + "    print(use(None))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_subscript_element_receiver_routes(self):
        # The third leg of the shared receiver resolution, kept here so a
        # change to the value-opt legs has to keep it routing too; its own
        # rows and boundary live in the subscript-receiver unit.
        src = (_PET
               + "def use(bs: list[Box[Pet]]) -> Int32:\n"
               + "    return bs[0].speak()\n"
               + "def main() -> None:\n"
               + "    bs = [Box(Pet(3))]\n"
               + "    print(use(bs))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_pointer_repr_optional_still_routes(self):
        # The pre-existing pointer-repr leg beside the new one.
        src = (_PET
               + "def use(b: Box[Pet] | None) -> Int32:\n"
               + "    if b is not None:\n"
               + "        return b.speak()\n"
               + "    return 0\n"
               + "def main() -> None:\n"
               + "    p = Box(Pet(3))\n"
               + "    print(use(p))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)


class TestValueOptDerefReceiverBoundary:
    def test_call_rvalue_receiver_keeps_rejecting(self):
        src = (_PET
               + "def mk() -> Own[Box[Pet]]:\n"
               + "    return Box(Pet(3))\n"
               + "def use() -> Int32:\n"
               + "    return mk().speak()\n"
               + "def main() -> None:\n"
               + "    print(use())\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.method_call",
                           "method.marker.deref.recv_shape")
        _assert_byte_identical(src)

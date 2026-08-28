"""User-Deref chain over a container ELEMENT receiver (`xs[0].m()`).

The element read renders itself and the `.__deref__()` hops compose postfix
off that lvalue -- `::tpy::__getitem__(this->_pool, k).__deref__().close()`.
No pointer first hop can arise, so the `->` join the NAME receiver picks off
the pointer set is unreachable here. Corpus witness: tplib.requests'
`Session.__exit__` (`_pool: dict[str, Box[_Connection]]`).

The element read's own subscript arm gates its shape, so a receiver whose
render is NOT this bare composition (a record's own `__getitem__`, a slice
`__getitem__`, a subscript-over-subscript) falls the body back there instead
of being pre-screened by the receiver predicate -- the boundary class pins
each one at the landmark that actually rejects it.
"""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


_PET = ("from tpy import Int32, Own\n"
        "from tplib import Box\n"
        "from tplib.rc import Rc\n"
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


class TestSubscriptDerefReceiver:
    # The corpus witness's shape: a dict FIELD element, both at a discarded
    # statement position and inside a print.
    DICT_FIELD = (_PET
                  + "class Holder:\n"
                  + "    pool: dict[str, Box[Pet]]\n"
                  + "    def __init__(self) -> None:\n"
                  + "        self.pool = {}\n"
                  + "    def add(self, k: str, p: Own[Box[Pet]]) -> None:\n"
                  + "        self.pool[k] = p\n"
                  + "    def hit(self, k: str) -> None:\n"
                  + "        self.pool[k].bump(2)\n"
                  + "def main() -> None:\n"
                  + "    h = Holder()\n"
                  + "    h.add('a', Box(Pet(3)))\n"
                  + "    h.hit('a')\n"
                  + "    for k in h.pool:\n"
                  + "        print(h.pool[k].speak())\n"
                  + "main()\n")

    def test_dict_field_element_routes(self):
        out = _assert_routes_byte_identical(self.DICT_FIELD)
        body = "".join(out)
        assert "::tpy::__getitem__(this->pool, k).__deref__().bump(2);" in body
        assert "::tpy::__getitem__(h.pool, k).__deref__().speak()" in body

    def test_list_field_element_routes(self):
        src = (_PET
               + "class Holder:\n"
               + "    xs: list[Box[Pet]]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.xs = []\n"
               + "    def hit(self) -> Int32:\n"
               + "        self.xs[0].bump(2)\n"
               + "        return self.xs[0].speak()\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    h.xs.append(Box(Pet(3)))\n"
               + "    print(h.hit())\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_rc_wrapper_element_routes(self):
        # A second wrapper record over the same leg: the verdict must key on
        # the element being a `__deref__`-carrying record, not on Box.
        src = (_PET
               + "class Holder:\n"
               + "    xs: list[Rc[Pet]]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.xs = []\n"
               + "    def hit(self) -> Int32:\n"
               + "        self.xs[0].bump(2)\n"
               + "        return self.xs[0].speak()\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    h.xs.append(Rc.new(Pet(3)))\n"
               + "    print(h.hit())\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_list_local_element_routes(self):
        src = (_PET
               + "def use(xs: list[Box[Pet]]) -> Int32:\n"
               + "    xs[0].bump(2)\n"
               + "    return xs[0].speak()\n"
               + "def main() -> None:\n"
               + "    xs = [Box(Pet(3))]\n"
               + "    print(use(xs))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_dict_local_computed_key_routes(self):
        src = (_PET
               + "def use(d: dict[str, Box[Pet]], k: str) -> Int32:\n"
               + "    return d[k].speak()\n"
               + "def main() -> None:\n"
               + "    d = {'a': Box(Pet(3))}\n"
               + "    print(use(d, 'a'))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_tuple_element_routes(self):
        src = (_PET
               + "def use(t: tuple[Box[Pet], Int32]) -> Int32:\n"
               + "    return t[0].speak()\n"
               + "def main() -> None:\n"
               + "    t = (Box(Pet(3)), 1)\n"
               + "    print(use(t))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_nested_wrapper_depth_routes(self):
        src = (_PET
               + "def use(xs: list[Box[Box[Pet]]]) -> Int32:\n"
               + "    return xs[0].speak()\n"
               + "def main() -> None:\n"
               + "    xs = [Box(Box(Pet(3)))]\n"
               + "    print(use(xs))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_field_read_and_write_over_element_route(self):
        # The receiver resolution is shared with the field twin, so the same
        # element receiver has to spell the field chain identically.
        src = (_PET
               + "def use(xs: list[Box[Pet]]) -> Int32:\n"
               + "    xs[0].n = 9\n"
               + "    return xs[0].n\n"
               + "def main() -> None:\n"
               + "    xs = [Box(Pet(3))]\n"
               + "    print(use(xs))\n"
               + "main()\n")
        out = _assert_routes_byte_identical(src)
        assert "::tpy::__getitem__(xs, 0).__deref__().n = 9;" in "".join(out)


class TestSubscriptDerefReceiverBoundary:
    """Element receivers whose render is NOT the bare composition. Each is
    rejected by the element read's own arm, downstream of the receiver
    predicate -- the landmark names which arm, so a future widening there
    cannot silently pull the deref chain along with it."""

    _BAG = (_PET
            + "class Bag:\n"
            + "    xs: list[Box[Pet]]\n"
            + "    def __init__(self) -> None:\n"
            + "        self.xs = []\n")

    def test_self_record_getitem_keeps_rejecting(self):
        # `self[0].m()` reaches the wrapper through the record's OWN
        # `__getitem__`, whose result follows the call convention rather
        # than being an element lvalue. It must not route: the bare-`self`
        # value position this would construct is the shape the hand-applied
        # "is self a pointer here" fact gets wrong.
        src = (self._BAG
               + "    def __getitem__(self, i: Int32) -> Box[Pet]:\n"
               + "        return self.xs[i]\n"
               + "    def hit(self) -> Int32:\n"
               + "        self[0].bump(1)\n"
               + "        return self[0].speak()\n"
               + "def main() -> None:\n"
               + "    b = Bag()\n"
               + "    b.xs.append(Box(Pet(3)))\n"
               + "    print(b.hit())\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.expr_stmt",
                           "subscript.record_getitem")
        _assert_byte_identical(src)

    def test_local_record_getitem_keeps_rejecting(self):
        src = (self._BAG
               + "    def __getitem__(self, i: Int32) -> Box[Pet]:\n"
               + "        return self.xs[i]\n"
               + "def use(b: Bag) -> Int32:\n"
               + "    return b[0].speak()\n"
               + "def main() -> None:\n"
               + "    b = Bag()\n"
               + "    b.xs.append(Box(Pet(3)))\n"
               + "    print(use(b))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.return",
                           "subscript.record_getitem")
        _assert_byte_identical(src)

    def test_slice_getitem_keeps_rejecting(self):
        # A user `__getitem__(slice)` returning a wrapper is the one shape
        # where a SLICE subscript types as a Deref record; the slice arm
        # rejects it.
        src = (self._BAG
               + "    def __getitem__(self, s: slice) -> Box[Pet]:\n"
               + "        return self.xs[0]\n"
               + "def use(b: Bag) -> Int32:\n"
               + "    return b[0:2].speak()\n"
               + "def main() -> None:\n"
               + "    b = Bag()\n"
               + "    b.xs.append(Box(Pet(3)))\n"
               + "    print(use(b))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.return",
                           "subscript.slice_shape")
        _assert_byte_identical(src)

    def test_subscript_over_subscript_keeps_rejecting(self):
        src = (_PET
               + "def use(pp: list[list[Box[Pet]]]) -> Int32:\n"
               + "    return pp[0][0].speak()\n"
               + "def main() -> None:\n"
               + "    inner: list[Box[Pet]] = []\n"
               + "    inner.append(Box(Pet(3)))\n"
               + "    pp: list[list[Box[Pet]]] = []\n"
               + "    pp.append(inner)\n"
               + "    print(use(pp))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.return",
                           "subscript.recv.subscript")
        _assert_byte_identical(src)

    def test_unproven_optional_element_keeps_rejecting(self):
        # An Optional element read carries the call's null-check marker, so
        # the chain would have to compose off `deref_optional_check(...)`
        # rather than the bare element read.
        src = (_PET
               + "def use(xs: list[Box[Pet] | None]) -> Int32:\n"
               + "    return xs[0].speak()\n"
               + "def main() -> None:\n"
               + "    xs: list[Box[Pet] | None] = [Box(Pet(3))]\n"
               + "    print(use(xs))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.method_call",
                           "method.marker.deref.recv_shape")
        _assert_byte_identical(src)

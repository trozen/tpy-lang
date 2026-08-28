"""A call already producing the slot's wrapper union at an `Own[V]` return.

The wrapper-union return admitted sources the wrapper's converting ctor
had to absorb (a monostate `None`, a container literal, a scalar, a
member-container name, a member-record ctor). A callee declared
`-> Own[V]` hands back a prvalue of the return type itself, so it
forwards bare. The wrapper identity is the guard -- a sibling alias with
the same members is a different C++ struct.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _thir_ctx,
)

_PRE = (
    "from tpy import Own, Int32\n"
    "type J = None | bool | int | str | list[J]\n"
    "def parse(s: str) -> Own[J]:\n"
    "    return 5\n"
)


class TestFreeCallAtWrapperSlotRoutes:
    # The stdlib witness shape: `json.load` is `return loads(fp.read())`.
    SRC = (
        _PRE +
        "def load(s: str) -> Own[J]:\n"
        "    return parse(s)\n"
        "def main() -> None:\n"
        "    v: J = load('x')\n"
        "    print(isinstance(v, int))\n"
        "main()\n"
    )

    def test_routes_with_face(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("ret.own_wrapper_call", 0) >= 1

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return parse(s);" in cpp


class TestMethodCallOnTemporaryAtWrapperSlotRoutes:
    # The method-call twin over a constructor receiver, the shape
    # `tplib.requests.Response.json` reaches through its own receiver.
    SRC = (
        _PRE +
        "class H:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def parse(self, s: str) -> Own[J]:\n"
        "        return 7\n"
        "def load(s: str) -> Own[J]:\n"
        "    return H(1).parse(s)\n"
        "def main() -> None:\n"
        "    v: J = load('x')\n"
        "    print(isinstance(v, int))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return H(1).parse(s);" in cpp


class TestWrapperNameReturnDefers:
    # BOUNDARY: a wrapper-typed LOCAL at the same slot is not a call, and
    # its render depends on the binding's own storage (a try-hoisted local
    # returns `std::move((*v))`, not the bare name), so it keeps the
    # named reject.
    SRC = (
        _PRE +
        "def echo(s: str) -> Own[J]:\n"
        "    v: J = parse(s)\n"
        "    return v\n"
        "def main() -> None:\n"
        "    v: J = echo('x')\n"
        "    print(isinstance(v, int))\n"
        "main()\n"
    )

    def test_defers_at_named_shape(self):
        _ctx, fb = _thir_ctx(self.SRC)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.own_wrapper_source")


class TestTryHoistedWrapperLocalAtWrapperSlot:
    """The hoisted-optional sibling of the deferred plain local above: a
    wrapper-union local bound inside a `try` is hoisted as
    `std::optional<J> v;`, so the AST's indirect-name return arm derefs and
    moves it. It shares the record flavors' deref+move render, which is what
    keeps the `std::move` from being dropped by an admission-only widening.

    The stdlib witness is `json.loads`."""

    _HEAD = (
        "from tpy import Own, Int32, error_return, ReturnException\n"
        "type J = None | bool | int | str | list[J]\n"
        "class ReadErr(Exception, ReturnException):\n"
        "    def __init__(self, message: str = '') -> None:\n"
        "        self.message = message\n"
        "@error_return(ReadErr)\n"
        "def read(n: Int32) -> Own[J]:\n"
        "    if n == 0:\n"
        "        raise ReadErr('zero')\n"
        "    return 42\n"
    )

    SRC = _HEAD + (
        "def loads(n: Int32) -> Own[J]:\n"
        "    try:\n"
        "        value = read(n)\n"
        "    except ReadErr as e:\n"
        "        raise ValueError(e.message)\n"
        "    if n < 0:\n"
        "        raise ValueError('neg')\n"
        "    return value\n"
        "def main() -> None:\n"
        "    v: J = loads(1)\n"
        "    print(isinstance(v, int))\n"
        "main()\n"
    )

    def test_routes_with_face(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("ret.own_wrapper_ptr_opt_local", 0) >= 1

    def test_routes_byte_identical_and_keeps_the_move(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::optional<J> value;" in cpp
        assert "return std::move((*value));" in cpp

    def test_plain_local_at_the_same_slot_defers(self):
        # BOUNDARY: without the try the local is a plain `J value;`, whose
        # bare name IS the AST render -- deref+move there would read through
        # a non-optional.
        src = self._HEAD + (
            "def loads(n: Int32) -> Own[J]:\n"
            "    value: J = 5\n"
            "    if n < 0:\n"
            "        raise ValueError('neg')\n"
            "    return value\n"
            "def main() -> None:\n"
            "    v: J = loads(1)\n"
            "    print(isinstance(v, int))\n"
            "main()\n"
        )
        _ctx, fb = _thir_ctx(src)
        _assert_rejects_at(fb, "body:stmt.return",
                           shape="return.own_wrapper_source")

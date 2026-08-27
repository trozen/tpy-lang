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

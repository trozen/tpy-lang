# An Own[T] parameter is the same owning slot a list element is, so a
# borrow-returning call arriving there copies and is warned -- at a free
# function, a method and a constructor alike. Codegen then rejects the shape,
# which is what keeps the copy from being observable; the reject is also what
# makes this an error_ case. Compilation stops at the FIRST reject, so only
# the `via_free` leg on line 40 is asserted here -- `via_method` and
# `via_ctor` are the same slot at the other two callee kinds and are pinned
# by the unit-level warning check in `tpyc/test_operator_borrow_binding.py`
# (the harness drops warnings from a failing compile, see TODO.md).
from tpy import Int32, Own


class Payload:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Holder:
    p: Payload

    def __init__(self) -> None:
        self.p = Payload(42)

    def borrow(self) -> Payload:
        return self.p


class Sink:
    p: Payload

    def __init__(self, p: Own[Payload]) -> None:
        self.p = p

    def replace(self, q: Own[Payload]) -> None:
        self.p = q


def keep(p: Own[Payload]) -> Own[Payload]:
    return p


def via_free(h: Holder) -> None:
    d = keep(h.borrow())  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print(d.v)


def via_method(h: Holder, s: Sink) -> None:
    s.replace(h.borrow())


def via_ctor(h: Holder) -> None:
    s = Sink(h.borrow())
    print(s.p.v)


def main() -> None:
    h = Holder()
    s = Sink(Payload(0))
    via_free(h)
    via_method(h, s)
    via_ctor(h)


main()

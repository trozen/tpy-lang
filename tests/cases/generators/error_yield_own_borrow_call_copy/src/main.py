# A yield at Iterator[Own[T]] coerces into the same owning slot an element
# insert or an Own[T] parameter does, so a borrow-returning call yielded
# there copies and is warned. Codegen then rejects the yield type, so the
# reject -- not the warning -- is what keeps the copy unobservable, and this
# case pins the reject at the shape the warning covers, at both call
# spellings.
from typing import Iterator

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


def borrow_free(h: Holder) -> Payload:
    return h.p


# Two yields, so the body takes the resumable frame. The FREE-call twin is
# the shape a value-category admission (`is_rvalue_source`) would let into
# the owning slot; only the callee's DECLARED `Own` return keeps it out.
def each(h: Holder) -> Iterator[Own[Payload]]:  # tpyc: error(/not yet supported by C\+\+ code generation/)
    yield borrow_free(h)  # tpyc: warning(/copies Payload into owned storage/)
    yield h.borrow()  # tpyc: warning(/copies Payload into owned storage/)


def main() -> None:
    h = Holder()
    for p in each(h):
        print(p.v)


main()

# A yield at Iterator[Own[T]] coerces into the same owning slot an element
# insert or an Own[T] parameter does, so a borrow-returning call yielded
# there copies and is warned. Codegen then rejects the yield type, so the
# reject -- not the warning -- is what keeps the copy unobservable, and this
# case pins the reject at the shape the warning covers. The warning cannot be
# annotated here: the harness drops every warning from a failing compile's
# diagnostics (TODO.md "a warning alongside a compile error never reaches
# diag.txt"), so it is pinned at unit level in
# `tpyc/test_operator_borrow_binding.py`.
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


def each(h: Holder) -> Iterator[Own[Payload]]:  # tpyc: error(/not yet supported by C\+\+ code generation/)
    yield h.borrow()


def main() -> None:
    h = Holder()
    for p in each(h):
        print(p.v)


main()

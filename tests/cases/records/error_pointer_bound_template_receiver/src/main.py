# A user dunder called explicitly on a POINTER-bound record: `{self}` would
# have to render the deref, which the template-expansion arm does not spell, so
# it is rejected.
from typing import Optional
from tpy import Int32


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: Int32) -> bool:
        return self.n == other


class Holder:
    cur: Optional[Box]

    def __init__(self) -> None:
        self.cur = None


def main() -> None:
    h = Holder()
    h.cur = Box(3)
    c = h.cur
    if c is not None:
        print(c.__eq__(3))  # tpyc: error(/method.fi_kind/)


main()

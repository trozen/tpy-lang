# A Ptr FIELD receiver whose pointee is outside the eligible value family
# (StrView) does not take the ptr-field receiver row. Concretely,
# `c.q.__deref__()` where `c.q: Ptr[StrView]`; TPy rejects that call today.
from tpy import Ptr, StrView, take_ptr


class Cell:
    q: Ptr[StrView]

    def __init__(self, q: Ptr[StrView]) -> None:
        self.q = q


def use(c: Cell) -> None:
    print(c.q.__deref__())  # tpyc: error(/method.recv.field_nonrecord/)


def main() -> None:
    s = StrView("v")
    use(Cell(take_ptr(s)))


main()

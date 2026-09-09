# A BORROW-returning getter at a generic FREE function's `T` slot is refused
# although the same argument at a generic METHOD's `T` slot binds bare:
# BUGS.md#generic-free-call-borrow-return-arg-rejects (the happy method form is
# the twin case `generics/generic_method_borrow_ret_arg`).
from __future__ import annotations
from tpy import Int32


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    c: Cell

    def __init__(self) -> None:
        self.c = Cell(1)

    def borrow(self) -> Cell:
        return self.c


def show[T](v: T) -> None:
    print("show")


def main() -> None:
    h = Holder()
    # The free seam's generic prologue admits a CALL at a substituted
    # non-value `T` slot only for an rvalue source, so the `T&` getter result
    # matches no row.
    show(h.borrow())  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()

# `nonlocal self` in a nested def is rejected: the method receiver has no
# rebindable storage behind `this`, so a rebind would silently keep the
# original object where CPython switches to the new one.
from tpy import Int32


class C:
    n: Int32

    def __init__(self) -> None:
        self.n = 1

    def swap(self) -> None:
        def replace() -> None:  # tpyc: error(/cannot be declared nonlocal/)
            nonlocal self
            self = C()

        replace()


def main() -> None:
    c = C()
    c.swap()


main()

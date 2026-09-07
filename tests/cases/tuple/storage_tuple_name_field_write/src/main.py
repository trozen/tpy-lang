# A STORAGE-form tuple name (the alias local off another record's field) copies
# bare into a tuple field -- no borrow-to-storage lift, unlike a borrow-tuple
# parameter.
from tpy import Int32


class Elem:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    pair: tuple[Elem | None, Elem | None]

    def __init__(self) -> None:
        self.pair = (None, None)


def copy_alias(h: Holder, other: Holder) -> None:
    t = other.pair
    # A bare copy of the storage-form tuple -- one warning per element.
    h.pair = t  # tpyc: warning(/tuple element 0/) warning(/tuple element 1/)


def main() -> None:
    a = Holder()
    b = Holder()
    copy_alias(a, b)
    print(a.pair[0] is None)


main()

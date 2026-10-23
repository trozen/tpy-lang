# A subscript on a protocol receiver whose protocol declares no __getitem__
# is refused as not subscriptable.
from typing import Protocol
from tpy import int32


class Sized2(Protocol):
    def size(self) -> int32: ...


class Bag:
    def __init__(self) -> None:
        pass

    def size(self) -> int32:
        return 1


def first(p: Sized2) -> None:
    print(p[0])  # tpyc: error(/Type 'Sized2' is not subscriptable/)


def main() -> None:
    first(Bag())


main()

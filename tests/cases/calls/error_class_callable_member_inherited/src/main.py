# A lambda building a fresh list at an INHERITED generic `Callable[[], V]`
# field is refused, naming the declaring class's field and the
# `Callable[[], Own[V]]` it writes (LANGUAGE_FEATURES.md, the Restriction on a
# generic class's `Callable[[], V]` member).
from typing import Callable
from tpy import int32


SHARED: list[int32] = []


def shared() -> list[int32]:
    return SHARED


class Base[V]:
    factory: Callable[[], V]

    def __init__(self, factory: Callable[[], V]) -> None:
        self.factory = factory


class Ints(Base[list[int32]]):
    def __init__(self) -> None:
        super().__init__(shared)


def main() -> None:
    sub = Ints()
    # the subject: a lambda building a new list where the inherited field lends
    sub.factory = lambda: []  # tpyc: error(/this lambda builds a new list\[int32\], but 'Base.factory' \(Callable\[\[\], V\]\) returns a borrow; declare 'Base.factory' as Callable\[\[\], Own\[V\]\]/)


main()

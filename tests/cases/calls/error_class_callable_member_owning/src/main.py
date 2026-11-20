# A lambda building a fresh list at a generic class's `Callable[[], V]` field
# is refused, naming the field and the `Callable[[], Own[V]]` its class writes
# (LANGUAGE_FEATURES.md, the Restriction on a generic class's `Callable[[], V]`
# member).
from typing import Callable
from tpy import int32


SHARED: list[int32] = []


def shared() -> list[int32]:
    return SHARED


class DefaultMap[V]:
    factory: Callable[[], V]

    def __init__(self, factory: Callable[[], V]) -> None:
        self.factory = factory


def main() -> None:
    d = DefaultMap[list[int32]](shared)
    # the subject: a lambda building a new list where the field lends
    d.factory = lambda: []  # tpyc: error(/this lambda builds a new list\[int32\], but 'DefaultMap.factory' \(Callable\[\[\], V\]\) returns a borrow; declare 'DefaultMap.factory' as Callable\[\[\], Own\[V\]\]/)
    print(len(d.factory()))


main()

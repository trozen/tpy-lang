# A for over a field reached through a Ptr held in a record FIELD stays
# refused: no loan key spells it (BUGS.md#iter-borrow-place-needs-hops).
from typing import Iterator
from tpy import Ptr, int32


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def __iter__(self) -> Iterator[int32]:
        for x in self.xs:
            yield x


class K:
    c: Bag

    def __init__(self) -> None:
        self.c = Bag()


class L:
    pk: Ptr[K]

    def __init__(self, k: K) -> None:
        self.pk = k


def walk(l: L) -> None:
    for x in l.pk.c:  # tpyc: error(/iter_borrow_unplaceable/)
        print(x)


def main() -> None:
    k = K()
    walk(L(k))


main()

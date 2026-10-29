# A generator expression over a field reached through a Ptr held in a record
# FIELD is refused, as a for or a comprehension over it is.
from tpy import Ptr, int32


class K:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]


class L:
    pk: Ptr[K]

    def __init__(self, k: K) -> None:
        self.pk = k


def total(l: L) -> int32:
    return sum(x for x in l.pk.items)  # tpyc: error(/genexpr/)


def main() -> None:
    k = K()
    print(total(L(k)))


main()

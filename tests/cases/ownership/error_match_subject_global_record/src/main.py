# A record field of a module global is no subject the record tier binds by
# reference, and copying it would lose the capture's write: refused
# (BUGS.md#match-subject-unbound-root-refused).
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    item: Item

    def __init__(self) -> None:
        self.item = Item(1)


G = Holder()


def main() -> None:
    match G.item:  # tpyc: error(/stmt\.match/)
        case Item() as a:
            a.n = 9
    print(G.item.n)


main()

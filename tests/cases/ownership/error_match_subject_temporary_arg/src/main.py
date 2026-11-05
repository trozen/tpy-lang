# A match subject binds by reference only over durable storage: a reference
# result over a temporary argument is refused
# (BUGS.md#match-subject-temporary-root-refused).
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Item]

    def __init__(self) -> None:
        self.items = [Item(0), Item(0)]


def first(c: Bag) -> Item:
    return c.items[0]


def main() -> None:
    match first(Bag()):  # tpyc: warning(/temporary argument/) error(/stmt\.match/)
        case Item() as it:
            it.n = 5


main()

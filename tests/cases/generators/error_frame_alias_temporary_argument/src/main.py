# A generator local holding a method result that points at a TEMPORARY
# argument is refused: the temporary dies with the statement, before the
# yield the frame holds the alias across (warned as the borrow it is).
from typing import Iterator
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    def __init__(self) -> None:
        pass

    def pick(self, o: Item) -> Item:
        return o

    def walk(self) -> Iterator[int32]:
        x = self.pick(Item(3))  # tpyc: warning(/Result borrows from temporary argument 'o'/) error(/res.alias_bind/)
        yield 1
        yield x.n


def main() -> None:
    for v in Bag().walk():
        print(v)


main()
